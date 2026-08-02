"""Labelled events: an UNCONFIDENT poke above the prior-session high that returns
into the range -> does it reach the range MID (revert) or CONSOLIDATE back ABOVE
the high (the break was real after all)?

Event: current session prints a high above the prior session high, then closes back
inside the prior range (return). Decision point t0 = the return bar; all features
causal (<= t0). Outcome is a first-passage race over the current + next session:
  label = 1 (MID)   : low reaches the prior-range mid first;
  label = 0 (ABOVE) : price closes above the prior high for 2 bars first;
  dropped (-1)      : neither within the 2-session horizon.

MANY market-logical features stored for a CatBoost discrimination of the two
classes and the search for week-STABLE patterns. No trade sim here -- pure
predictability, per validity-before-EV.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW, BASELINE_BARS
from anomaly_science.strategy.session_break.research.fade_exec_v2 import _short  # sign-clean, zero-cost
from anomaly_science.strategy.session_break.research.reclaim import _resample, _block_runs
from anomaly_science.strategy.session_break.research.sessions import (
    N_BLOCKS, block_seq_for_ms, utc_day_for_ms,
)

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")

# the reference high must be an OBVIOUS SWING HIGH: an interior pivot (not a session
# edge), the highest point within +/- a session window, with a big rise INTO it AND a
# big fall OUT of it (both >= LEVEL_SWING_MIN ATR). Filters clock-window "limp" highs.
LEVEL_SWING_MIN = 2.0
PIVOT_K = 3               # bars of clearance the swing high needs from each session edge
RANGE_EXPAND_TOL = 0.15   # wick tolerance (fraction of range) before the range counts as expanded


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
    ema = pd.Series(c).ewm(span=max(30, BASELINE_BARS // tf_min), adjust=False).mean().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, np.nan)
        ats = np.where(tc > 0, qv / tc, np.nan)
    base_bars = max(30, BASELINE_BARS // tf_min)
    base_qv = pd.Series(qv).rolling(base_bars, min_periods=20).median().to_numpy()
    base_tc = pd.Series(tc).rolling(base_bars, min_periods=20).median().to_numpy()
    base_ats = pd.Series(ats).rolling(base_bars, min_periods=20).median().to_numpy()

    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    runs = _block_runs(day, seq)
    idx_by_key = {(d, s): (st, en) for d, s, st, en in runs}
    end_by_ord = [en for (d, s, st, en) in runs]

    def ref_key(d, s):
        if variant == "sequential":
            return (d - 1, N_BLOCKS - 1) if s == 0 else (d, s - 1)
        return (d - 1, s)

    rows = []
    for ri, (d, s, st, en) in enumerate(runs):
        rk = ref_key(d, s)
        if rk not in idx_by_key:
            continue
        rst, ren = idx_by_key[rk]
        if ren - rst < 3 or en - st < 2 or st < base_bars:
            continue
        ref_high = float(h[rst:ren].max()); ref_low = float(lo[rst:ren].min())
        ref_rng = ref_high - ref_low
        if ref_rng <= 0:
            continue
        ref_mid = 0.5 * (ref_high + ref_low)
        a0 = atr[st]
        if not (np.isfinite(a0) and a0 > 0):
            continue
        if not (o[st] < ref_high):
            continue
        # held-level tracking. TOLERANT rule: a wick-poke above the level between the
        # reference and now is fine (the level still held) as long as no CLOSE sustained
        # above it AND the range was not materially expanded. Whether price dipped below
        # the reference LOW is tracked, not filtered.
        interv_below_low = 0; interv_min_frac = 0.0; interv_hold_bars = 0; interv_high_frac = 0.0
        interv_poke_count = 0; interv_poke_depth = 0.0; interv_touch_count = 0; interv_close_top = 0.0
        if variant == "same_type":
            if not (st > ren):
                continue
            seg_h = h[ren:st]; seg_l = lo[ren:st]; seg_c = c[ren:st]
            if seg_h.size == 0:
                continue
            interv_hi = float(seg_h.max()); interv_low = float(seg_l.min())
            tol = RANGE_EXPAND_TOL * ref_rng
            # UPSIDE = the level (high). It must HOLD: a small wick-poke is tolerated, but
            # if price CLOSED above it (walked above) or wicked far above -> broken, skip.
            if bool((seg_c > ref_high).any()) or interv_hi > ref_high + tol:
                continue
            # DOWNSIDE is NOT a level to defend: if price traded below the reference low
            # in between, EXPAND the range down to that low (the mid/take shifts with it).
            interv_below_low = int(interv_low < ref_low - 0.05 * ref_rng)
            interv_min_frac = (interv_low - ref_low) / ref_rng       # how far below (<0) before expansion
            interv_poke_count = int((seg_h > ref_high).sum())        # tolerated wick pokes above the level
            interv_poke_depth = max(0.0, (interv_hi - ref_high) / ref_rng)       # deepest tolerated poke
            interv_touch_count = int((seg_h >= ref_high - 0.10 * ref_rng).sum()) # approaches within 10% band
            interv_hold_bars = st - ren                              # bars the level held
            if interv_low < ref_low:                                 # expand the range down
                ref_low = interv_low
                ref_rng = ref_high - ref_low
                ref_mid = 0.5 * (ref_high + ref_low)
            interv_high_frac = (interv_hi - ref_low) / ref_rng       # ~1 => intervening high tested the level
            interv_close_top = (float(seg_c.max()) - ref_low) / ref_rng          # closest a CLOSE came

        # ---- the level must be an OBVIOUS SWING HIGH, not the session-clock max ----
        hi_rel = int(np.argmax(h[rst:ren])); hi_idx = rst + hi_rel
        # (a) interior: room on both sides of the session edge (not a boundary artifact)
        if (hi_idx - rst) < PIVOT_K or (ren - 1 - hi_idx) < PIVOT_K:
            continue
        # (b) highest point within +/- a session window (a genuine local peak, no nearby
        #     higher high), with the rise/fall measured over that same window (the real move)
        wa = max(0, hi_idx - (ren - rst)); wb = min(n, hi_idx + (ren - rst) + 1)
        if h[hi_idx] < float(h[wa:wb].max()):
            continue
        up_imp = (ref_high - float(lo[wa:hi_idx].min())) / a0
        down_after = (ref_high - float(lo[hi_idx + 1:wb].min())) / a0
        # (c) big move INTO it AND big move OUT of it
        if min(up_imp, down_after) < LEVEL_SWING_MIN:
            continue
        prior_high_pos = hi_rel / max(1, ren - rst - 1)
        rng_hi = h[hi_idx] - lo[hi_idx]
        prior_high_wick = (h[hi_idx] - max(o[hi_idx], c[hi_idx])) / rng_hi if rng_hi > 0 else 0.0
        vref = qv[rst:ren].mean()
        prior_vol_at_high = qv[hi_idx] / vref if vref > 0 else np.nan
        prior_close_pos = (c[ren - 1] - ref_low) / ref_rng          # where prior session closed in its range
        prior_taker = np.nanmean(taker[rst:ren])
        prior_oi_trend = (oi[ren - 1] / oi[rst] - 1.0) if (np.isfinite(oi[rst]) and oi[rst] > 0) else np.nan
        prior_vol_slope = (qv[ren - 1] - qv[rst]) / (vref + 1.0)

        # ---- the poke inside the current session ----
        crossed = np.flatnonzero(h[st:en] > ref_high)
        if len(crossed) == 0:
            continue
        poke = st + int(crossed[0])
        # find the RETURN: first close back below ref_high after the poke (n_confirm)
        peak = h[poke]; peak_idx = poke; below = 0; ret = None
        cap = min(n, en + 2 * (en - st) + 5)
        for j in range(poke, cap):
            if h[j] > peak:
                peak = h[j]; peak_idx = j
            below = below + 1 if c[j] < ref_high else 0
            if below >= n_confirm and j > poke:
                ret = j; break
        if ret is None or ret + 2 >= n:
            continue
        poke_high = float(peak)
        # entry = next bar open. It must sit ABOVE the mid target (in the upper part of
        # the range, near the rejected level) -- never below the target.
        if o[ret + 1] <= ref_mid:
            continue

        # ---- OUTCOME: first-passage MID vs consolidate-ABOVE over current+next session ----
        hz_end = end_by_ord[min(len(end_by_ord) - 1, ri + 2)]   # end of the session after current
        hz_end = min(hz_end, n)
        if hz_end - ret < 2:
            continue
        label = -1; up_run = 0; t_mid = None; t_low = None; t_above = None
        for j in range(ret + 1, hz_end):
            if lo[j] <= ref_mid and t_mid is None:
                t_mid = j
            if lo[j] <= ref_low and t_low is None:
                t_low = j
            up_run = up_run + 1 if c[j] > ref_high else 0
            if up_run >= 2 and t_above is None:
                t_above = j
            if t_above is not None and t_low is not None:
                break  # both the MID and DEEP races are decided
        if t_mid is not None and (t_above is None or t_mid <= t_above):
            label = 1
        elif t_above is not None:
            label = 0
        else:
            continue  # neither within horizon -> drop
        # DEEP outcome: did price fall all the way to the range LOW before 2 closes above?
        if t_low is not None and (t_above is None or t_low <= t_above):
            label_deep = 1
        elif t_above is not None:
            label_deep = 0
        else:
            label_deep = -1

        # ---- rich causal features at the return bar ----
        base_qv0 = base_qv[st - 1]; base_tc0 = base_tc[st - 1]; base_ats0 = base_ats[st - 1]
        # pre-poke (current session up to poke)
        if poke > st:
            pre_qv = qv[st:poke].mean(); pre_tc = tc[st:poke].mean()
            pre_atr = (h[st:poke].max() - lo[st:poke].min()) / a0
            pre_taker = np.nanmean(taker[st:poke])
            pre_ret = c[poke] / c[st] - 1.0
        else:
            pre_qv, pre_tc, pre_atr, pre_taker, pre_ret = qv[st], tc[st], (h[st] - lo[st]) / a0, taker[st], 0.0
        # how price APPROACHED the level in the current session before poking: from deep
        # below (spike) or hugging it (coil), and how long it coiled under it.
        approach_from = (ref_high - float(lo[st:poke].min())) / ref_rng if poke > st else (ref_high - lo[st]) / ref_rng
        approach_bars = poke - st
        rng_peak = h[peak_idx] - lo[peak_idx]
        # ---- sign-clean SHORT trade: entry next candle open, target=mid, stop above poke ----
        ei = ret + 1
        fill = o[ei]
        stop = poke_high + 0.25 * a0
        gross_r = np.nan; risk_frac = np.nan
        if fill > ref_mid and stop > fill:
            we = min(ei + 6 * (en - st) + 20, n)
            gross_r = _short(o[ret:we], h[ret:we], lo[ret:we], c[ret:we],
                             atr[ret:we], stop, ref_mid, "partial")
            risk_frac = (stop - fill) / fill
        rows.append({
            "symbol": path.stem, "tf": tf_min, "variant": variant,
            "session": int(s), "break_ts": int(ts[ret]),
            "week": pd.Timestamp(ts[ret], unit="ms", tz="UTC").strftime("%G-W%V"),
            "label": label,   # 1=MID (revert), 0=ABOVE (real break)
            "label_deep": label_deep,  # 1=reached range LOW (big revert), 0=ABOVE, -1=stuck mid
            # held-level context (same_type): how the reference level held between then and now
            "interv_below_low": interv_below_low, "interv_min_frac": interv_min_frac,
            "interv_high_frac": interv_high_frac, "interv_hold_bars": interv_hold_bars,
            # did price APPROACH / poke the level during the hold (tolerant), and how
            "interv_poke_count": interv_poke_count, "interv_poke_depth": interv_poke_depth,
            "interv_touch_count": interv_touch_count, "interv_close_top": interv_close_top,
            # is the level NOISE or an obvious sweeping rise-then-fall (real swing high)?
            "level_swing": float(min(up_imp, down_after)),
            # how the current session approached the level before poking
            "approach_from": approach_from, "approach_bars": approach_bars,
            # movement / "does the coin actually move?" (#1) and "real high?" (#3)
            "sess_ampl_pct": ref_rng / ref_mid if ref_mid > 0 else np.nan,   # prior range as % of price
            "atr_pct": a0 / c[st] if c[st] > 0 else np.nan,                  # per-bar move as % of price
            "reward_low_pct": (o[ei] - ref_low) / o[ei] if o[ei] > 0 else np.nan,  # target=LOW reward %
            "prior_high_prom": (ref_high - float(np.median(h[rst:ren]))) / a0,     # high above session's typical
            # trade + desk geometry
            "gross_r": gross_r, "risk_frac": risk_frac,
            "ref_high": ref_high, "ref_low": ref_low, "ref_mid": ref_mid,
            "poke_high": poke_high, "entry_ts": int(ts[ei]) if ei < n else int(ts[ret]),
            "entry_px": float(fill), "poke_ts": int(ts[poke]), "ref_start_ts": int(ts[rst]),
            # prior-high quality
            "prior_up_imp": up_imp, "prior_down_after": down_after, "prior_high_pos": prior_high_pos,
            "prior_high_wick": prior_high_wick, "prior_vol_at_high": prior_vol_at_high,
            "prior_range_atr": ref_rng / a0, "prior_close_pos": prior_close_pos,
            "prior_taker": prior_taker, "prior_oi_trend": prior_oi_trend, "prior_vol_slope": prior_vol_slope,
            # poke
            "poke_depth_atr": (poke_high - ref_high) / a0,                  # poke height in ATR
            "poke_depth_rng": (poke_high - ref_high) / ref_rng,             # poke height vs range height
            "level_swing_up": up_imp, "level_swing_dn": down_after,          # the swing that formed the level
            "bars_above": ret - poke,
            "poke_wick": (h[peak_idx] - max(o[peak_idx], c[peak_idx])) / rng_peak if rng_peak > 0 else 0.0,
            "poke_rvol": qv[peak_idx] / base_qv0 if base_qv0 > 0 else np.nan,
            "poke_rtrades": tc[peak_idx] / base_tc0 if base_tc0 > 0 else np.nan,
            "poke_ats_ratio": ats[peak_idx] / base_ats0 if (np.isfinite(base_ats0) and base_ats0 > 0) else np.nan,
            "poke_taker": taker[peak_idx], "poke_speed": (poke_high - ref_high) / (a0 * max(1, peak_idx - poke + 1)),
            # return / reclaim
            "ret_taker": taker[ret], "ret_voldrop": qv[ret] / qv[peak_idx] if qv[peak_idx] > 0 else np.nan,
            "ret_body": (o[ret] - c[ret]) / (h[ret] - lo[ret]) if h[ret] > lo[ret] else 0.0,
            "ret_rvol": qv[ret] / base_qv0 if base_qv0 > 0 else np.nan,
            # pre-poke current session
            "pre_rvol": pre_qv / base_qv0 if base_qv0 > 0 else np.nan,
            "pre_rtrades": pre_tc / base_tc0 if base_tc0 > 0 else np.nan,
            "pre_atr": pre_atr, "pre_taker": pre_taker, "pre_ret": pre_ret,
            # trend / structure context
            "ema_dist": (c[ret] - ema[ret]) / a0 if np.isfinite(ema[ret]) else np.nan,
            "oi_change_poke": (oi[ret] / oi[poke] - 1.0) if (np.isfinite(oi[poke]) and oi[poke] > 0) else np.nan,
            "trail_turnover": float(qv[st - base_bars:st].sum()),
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
    ap.add_argument("--tf", type=int, required=True)
    ap.add_argument("--variant", default="sequential", choices=["sequential", "same_type"])
    ap.add_argument("--nconfirm", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    print(f"tf={args.tf}m variant={args.variant} nconfirm={args.nconfirm} symbols={len(files)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, (f, args.tf, args.variant, args.nconfirm)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(files)} events={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(args.out)
    if len(t):
        print(f"wrote {len(t):,} -> {args.out}  | MID={int((t.label==1).sum()):,} ABOVE={int((t.label==0).sum()):,}")


if __name__ == "__main__":
    main()
