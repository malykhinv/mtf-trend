"""Market-wide session context -- the regime backdrop that likely modulates whether
a session-streak continues. Built ONCE over all symbols and cached; joined causally
into any per-symbol study by the most recent COMPLETED session before an event.

Per session (utc_day, seq) we aggregate across all alts, plus BTC separately:
  alt_ret_med    : median alt session return  (risk-on/off)
  alt_breadth    : fraction of alts closing up (breadth)
  alt_rvol_med   : median alt relative volume
  alt_oi_med     : median alt OI change        (alt leverage build/unwind)
  alt_ema_dist   : median alt (price-EMA200)/ATR (alt-market trend)
  btc_ret / btc_ema_dist / btc_oichg / btc_atr_pct : BTC regime
Rolling alt-index return over recent sessions gives the market-momentum context.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.reclaim import _block_runs
from anomaly_science.strategy.session_break.research.sessions import block_seq_for_ms, utc_day_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")


def _sym_rows(path: Path, tf_min: int) -> pd.DataFrame:
    try:
        base = load_ohlcv_parquet(path)
        df = base if tf_min == 1 else resample_ohlcv_np(base, tf_min)
    except Exception:
        return pd.DataFrame()
    ts = df["timestamp"]; o = df["open"]; h = df["high"]; lo = df["low"]; c = df["close"]
    qv = df["quote_volume"]; oi = df.get("open_interest")
    if len(ts) < 200:
        return pd.DataFrame()
    atr = causal_atr(high=h, low=lo, close=c, window=ATR_WINDOW)
    ema = pd.Series(c).ewm(span=200, adjust=False).mean().to_numpy()
    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    rows = []
    for d, s, st, en in _block_runs(day, seq):
        if en - st < 3:
            continue
        op = float(o[st]); cl = float(c[en - 1])
        base_qv = np.median(qv[max(0, st - 96):st]) + 1e-9
        oichg = (float(oi[en - 1]) / float(oi[st]) - 1.0) if (oi is not None and np.isfinite(oi[st]) and oi[st] > 0) else np.nan
        rows.append({"day": int(d), "seq": int(s), "start_ts": int(ts[st]), "end_ts": int(ts[en - 1]),
                     "ret": cl / op - 1.0 if op > 0 else 0.0, "up": int(cl > op),
                     "rvol": float(qv[st:en].sum()) / (base_qv * (en - st)),
                     "oichg": oichg,
                     "ema_dist": (op - ema[st]) / atr[st] if atr[st] > 0 else 0.0})
    return pd.DataFrame(rows)


def _one(a):
    return _sym_rows(Path(a[0]), a[1])


def build_market_index(tf_min: int, workers: int, out: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    print(f"market index: tf={tf_min}m symbols={len(files)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, (f, tf_min)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(files)}")
    allrows = pd.concat(frames, ignore_index=True)
    # BTC rows
    btc = _sym_rows(CACHE / "BTCUSDT.parquet", tf_min).set_index(["day", "seq"])
    agg = allrows.groupby(["day", "seq"]).agg(
        start_ts=("start_ts", "median"), end_ts=("end_ts", "median"),
        alt_ret_med=("ret", "median"), alt_breadth=("up", "mean"),
        alt_rvol_med=("rvol", "median"), alt_oi_med=("oichg", "median"),
        alt_ema_dist=("ema_dist", "median"), n_symbols=("ret", "size"),
    ).reset_index()
    agg["btc_ret"] = agg.apply(lambda r: btc.ret.get((r.day, r.seq), np.nan) if (r.day, r.seq) in btc.index else np.nan, axis=1)
    agg["btc_ema_dist"] = agg.apply(lambda r: btc.ema_dist.get((r.day, r.seq), np.nan) if (r.day, r.seq) in btc.index else np.nan, axis=1)
    agg["btc_oichg"] = agg.apply(lambda r: btc.oichg.get((r.day, r.seq), np.nan) if (r.day, r.seq) in btc.index else np.nan, axis=1)
    agg = agg.sort_values("start_ts").reset_index(drop=True)
    # market-momentum over a GRID of session lookbacks (not a single static 5)
    for w in MKT_WINDOWS:
        agg[f"alt_ret_{w}"] = agg.alt_ret_med.rolling(w, min_periods=1).sum()
        agg[f"alt_breadth_{w}"] = agg.alt_breadth.rolling(w, min_periods=1).mean()
        agg[f"btc_ret_{w}"] = agg.btc_ret.rolling(w, min_periods=1).sum()
    out.parent.mkdir(parents=True, exist_ok=True)
    agg.to_parquet(out)
    print(f"wrote market index: {len(agg)} sessions -> {out}")
    return agg


MKT_WINDOWS = [3, 5, 10, 20]
MKT_COLS = (["alt_ret_med", "alt_breadth", "alt_rvol_med", "alt_oi_med", "alt_ema_dist",
             "btc_ret", "btc_ema_dist", "btc_oichg"]
            + [f"alt_ret_{w}" for w in MKT_WINDOWS]
            + [f"alt_breadth_{w}" for w in MKT_WINDOWS]
            + [f"btc_ret_{w}" for w in MKT_WINDOWS])


def load_market_arrays(path: Path):
    """Return (sorted end_ts array, {col: values array}) for CAUSAL as-of joins:
    a session only contributes context once it has fully CLOSED."""
    m = pd.read_parquet(path).sort_values("end_ts").reset_index(drop=True)
    end = m.end_ts.to_numpy(np.int64)
    return end, {c: m[c].to_numpy(float) for c in MKT_COLS}


def market_asof(mkt_end, mkt_vals, event_ts: int) -> dict:
    """Market context from the most recent session that fully CLOSED before event_ts
    (uses end_ts, so the event's own concurrent session can never leak in)."""
    i = int(np.searchsorted(mkt_end, event_ts, side="right")) - 1
    if i < 0:
        return {("mkt_" + c): np.nan for c in MKT_COLS}
    return {("mkt_" + c): float(mkt_vals[c][i]) for c in MKT_COLS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or Path(f".output/results/session_break/market_index_tf{args.tf}.parquet")
    build_market_index(args.tf, args.workers, out)


if __name__ == "__main__":
    main()
