"""Failed-break RECLAIM short: poke above the prior session high, fail, reclaim
back into the prior range, short toward the range mid.

Setup (per user):
  1. current session's price prints a high above the PRIOR session's high
     (prior = immediately preceding block, or same block-type a day earlier and
     only if price held inside the prior range through the sessions in between);
  2. after the poke, price closes back INSIDE the prior range for `n_confirm`
     candles, with seller aggression (low taker-buy share) and falling volume;
  3. short at the reclaim (fill next candle open), stop above the poke high,
     structural trail, book half at the prior range MID.

Tested across candle timeframes (TF minutes). Sessions are the fixed macro blocks
(Asia/EU/Overlap/US/Late). Shorts use the SIGN-CLEAN engine (gross R with a -1
stop floor, per-trade risk fraction; real cost charged in R-space downstream) --
NOT the buggy negation-with-slippage that once faked an edge.

Rich features stored for the "was the prior high a REAL distribution high or a
drift high" question and the rest of the user's list.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import (
    ATR_WINDOW, BASELINE_BARS,
)
from anomaly_science.strategy.session_break.research.fade_exec_v2 import _short
from anomaly_science.strategy.session_break.research.sessions import (
    N_BLOCKS, block_seq_for_ms, utc_day_for_ms,
)

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END_MS = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6
FWD_SESSIONS_CAP = 3     # allow the reclaim to complete within this many sessions after the poke


def _resample(df: pd.DataFrame, tf_min: int) -> pd.DataFrame:
    if tf_min == 1:
        return df.reset_index(drop=True)
    bucket = (df["timestamp"] // (tf_min * 60_000)) * (tf_min * 60_000)
    g = df.groupby(bucket)
    out = pd.DataFrame({
        "timestamp": g["timestamp"].first().values,
        "open": g["open"].first().values,
        "high": g["high"].max().values,
        "low": g["low"].min().values,
        "close": g["close"].last().values,
        "volume": g["volume"].sum().values if "volume" in df else np.nan,
        "quote_volume": g["quote_volume"].sum().values,
        "trade_count": g["trade_count"].sum().values,
        "taker_buy_quote_volume": g["taker_buy_quote_volume"].sum().values if "taker_buy_quote_volume" in df else np.nan,
        "open_interest": g["open_interest"].last().values if "open_interest" in df else np.nan,
    })
    return out.reset_index(drop=True)


def _block_runs(day, seq):
    key = day * 10 + seq
    change = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(key)]))
    return [(int(day[s]), int(seq[s]), int(s), int(e)) for s, e in zip(starts, ends)]


def build_symbol(path: Path, tf_min: int, variant: str, n_confirm: int) -> pd.DataFrame:
    raw = pd.read_parquet(path, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "quote_volume", "trade_count", "taker_buy_quote_volume", "open_interest"])
    raw = raw.sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, tf_min)
    n = len(df)
    if n < 200:
        return pd.DataFrame()
    ts = df["timestamp"].to_numpy(np.int64)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    vol = df["volume"].to_numpy(float); qv = df["quote_volume"].to_numpy(float)
    tc = df["trade_count"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    oi = df["open_interest"].to_numpy(float)
    atr = causal_atr(high=h, low=lo, close=c, window=ATR_WINDOW)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, np.nan)
    base_bars = max(30, BASELINE_BARS // tf_min)
    base_qv = pd.Series(qv).rolling(base_bars, min_periods=20).median().to_numpy()
    base_tc = pd.Series(tc).rolling(base_bars, min_periods=20).median().to_numpy()

    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    runs = _block_runs(day, seq)
    idx_by_key = {(d, s): (st, en) for d, s, st, en in runs}
    order = {(d, s): i for i, (d, s, st, en) in enumerate(runs)}

    def ref_key(d, s):
        if variant == "sequential":
            return (d - 1, N_BLOCKS - 1) if s == 0 else (d, s - 1)
        return (d - 1, s)  # same_type

    rows = []
    for ri, (d, s, st, en) in enumerate(runs):
        rk = ref_key(d, s)
        if rk not in idx_by_key:
            continue
        rst, ren = idx_by_key[rk]
        if ren - rst < 3 or en - st < 2:
            continue
        ref_high = float(h[rst:ren].max()); ref_low = float(lo[rst:ren].min())
        ref_rng = ref_high - ref_low
        if ref_rng <= 0:
            continue
        ref_mid = 0.5 * (ref_high + ref_low)
        a0 = atr[st] if st < n else np.nan
        if not (np.isfinite(a0) and a0 > 0):
            continue
        # the block must open below the level (genuine takeout, not already above)
        if not (o[st] < ref_high):
            continue
        # same_type: price must have HELD inside the prior range through the gap
        if variant == "same_type":
            if ren >= st:
                continue
            gap_hi = h[ren:st].max() if st > ren else -np.inf
            gap_lo = lo[ren:st].min() if st > ren else np.inf
            tol = 0.05 * ref_rng
            if gap_hi > ref_high + tol or gap_lo < ref_low - tol:
                continue

        # ---- prior-high quality (was it a real distribution high?) ----
        hi_rel = int(np.argmax(h[rst:ren])); hi_idx = rst + hi_rel
        up_imp = (ref_high - float(lo[rst:hi_idx + 1].min())) / a0
        down_after = (ref_high - float(lo[hi_idx:ren].min())) / a0 if ren - hi_idx > 1 else 0.0
        prior_high_pos = hi_rel / max(1, (ren - rst - 1))
        rng_hi = h[hi_idx] - lo[hi_idx]
        prior_high_wick = (h[hi_idx] - max(o[hi_idx], c[hi_idx])) / rng_hi if rng_hi > 0 else 0.0
        vol_ref = qv[rst:ren].mean()
        prior_vol_at_high = qv[hi_idx] / vol_ref if vol_ref > 0 else np.nan

        # ---- the poke inside the current session ----
        blk_h = h[st:en]
        crossed = np.flatnonzero(blk_h > ref_high)
        if len(crossed) == 0:
            continue
        poke = st + int(crossed[0])
        # pre-poke activity (current session up to poke)
        if poke > st:
            pre_qv = qv[st:poke].mean(); pre_tc = tc[st:poke].mean()
            pre_atr = (h[st:poke].max() - lo[st:poke].min()) / a0
        else:
            pre_qv, pre_tc, pre_atr = qv[st], tc[st], (h[st] - lo[st]) / a0
        base_qv0 = base_qv[st - 1] if st > 0 else np.nan
        base_tc0 = base_tc[st - 1] if st > 0 else np.nan

        # ---- track excursion + find the reclaim (n_confirm closes back inside) ----
        fwd_cap = min(n, en + FWD_SESSIONS_CAP * (en - st) + 5)
        peak = h[poke]; peak_idx = poke; below = 0; reclaim = None
        for j in range(poke, fwd_cap):
            if h[j] > peak:
                peak = h[j]; peak_idx = j
            below = below + 1 if c[j] < ref_high else 0
            if below >= n_confirm and j > poke:
                reclaim = j; break
        if reclaim is None or reclaim + 2 >= n:
            continue
        poke_high = float(peak)
        poke_depth = (poke_high - ref_high) / a0
        rng_peak = h[peak_idx] - lo[peak_idx]
        poke_wick = (h[peak_idx] - max(o[peak_idx], c[peak_idx])) / rng_peak if rng_peak > 0 else 0.0
        bars_above = reclaim - poke
        # reclaim confirmation quality
        reclaim_taker = np.nanmean(taker[max(poke, reclaim - n_confirm + 1):reclaim + 1])
        reclaim_voldrop = qv[reclaim] / qv[peak_idx] if qv[peak_idx] > 0 else np.nan
        oi_change = (oi[reclaim] / oi[poke] - 1.0) if (np.isfinite(oi[poke]) and oi[poke] > 0) else np.nan
        trail_turnover = float(qv[max(0, st - base_bars):st].sum())

        # ---- SHORT outcome (sign-clean engine): stop above poke high, TP0.5 at mid ----
        d0 = reclaim; ei = d0 + 1
        fill = o[ei]
        if fill <= ref_mid:
            continue
        stop = poke_high + 0.25 * a0
        if stop <= fill:
            continue
        we = min(ei + 6 * (en - st) + 20, n)
        gr = _short(o[d0:we], h[d0:we], lo[d0:we], c[d0:we], atr[d0:we], stop, ref_mid, "partial")
        if not np.isfinite(gr):
            continue
        rows.append({
            "symbol": path.stem, "tf": tf_min, "variant": variant, "n_confirm": n_confirm,
            "session": int(s), "break_ts": int(ts[d0]),
            "week": pd.Timestamp(ts[d0], unit="ms", tz="UTC").strftime("%G-W%V"),
            "gross_r": gr, "risk_frac": (stop - fill) / fill,
            # prior-high quality
            "prior_up_imp": up_imp, "prior_down_after": down_after,
            "prior_high_pos": prior_high_pos, "prior_high_wick": prior_high_wick,
            "prior_vol_at_high": prior_vol_at_high, "prior_range_atr": ref_rng / a0,
            # poke / reclaim
            "poke_depth_atr": poke_depth, "poke_wick": poke_wick, "bars_above": bars_above,
            "reclaim_taker": reclaim_taker, "reclaim_voldrop": reclaim_voldrop,
            "oi_change": oi_change,
            # pre-poke activity
            "pre_rvol": pre_qv / base_qv0 if (np.isfinite(base_qv0) and base_qv0 > 0) else np.nan,
            "pre_rtrades": pre_tc / base_tc0 if (np.isfinite(base_tc0) and base_tc0 > 0) else np.nan,
            "pre_atr": pre_atr,
            "trail_turnover": trail_turnover,
        })
    return pd.DataFrame(rows)


def _one(args):
    ps, tf, var, nc = args
    try:
        return build_symbol(Path(ps), tf, var, nc)
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, required=True, help="candle timeframe minutes")
    ap.add_argument("--variant", default="sequential", choices=["sequential", "same_type"])
    ap.add_argument("--nconfirm", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[: args.limit]
    print(f"tf={args.tf}m variant={args.variant} nconfirm={args.nconfirm} symbols={len(files)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, (f, args.tf, args.variant, args.nconfirm)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 150 == 0:
                print(f"  {done}/{len(files)} trades={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(args.out)
    print(f"wrote {len(t):,} -> {args.out}")


if __name__ == "__main__":
    main()
