"""BLIND / market-beta control for the knife-catch base rate.

The dump base rate is positive (flow trigger ~+0.18R, week-stable). But the DEV period
could simply be broadly up, making ANY long positive. This control answers that: for
every real flow entry we race MATCHED placebo longs -- same symbol, same risk fraction,
same RR, but entered at RANDOM bars unrelated to a dump. If the placebo meanR ~ the real
meanR, the "edge" is just market drift; if real >> placebo, the dump buy-back is real.

Reads the dump_reversal parquet (no re-detection) + the price cache. DEV only.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.knife_catch.research.dump_reversal import (
    HORIZON, RR, TF, resolve_rr,
)
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
N_PLACEBO = 3            # placebo longs per real trade
RNG_SEED = 0


def _race_summary(e, entry, stop, hi, lo, cl, n):
    we = min(e + HORIZON, n - 1)
    rmax = -np.inf; stop_hit = False
    for j in range(e + 1, we + 1):
        if lo[j] <= stop:
            stop_hit = True; break
        if hi[j] > rmax:
            rmax = hi[j]
    risk = entry - stop
    mfe = (rmax - entry) / risk if np.isfinite(rmax) and risk > 0 else 0.0
    close_R = (cl[we] - entry) / risk if risk > 0 else 0.0
    return mfe, stop_hit, close_R


def _one(args):
    sym, risks = args                         # risks = matched risk fractions of the real trades
    p = CACHE / f"{sym}.parquet"
    if not p.exists():
        return []
    try:
        raw = pd.read_parquet(p, columns=["timestamp", "open", "high", "low", "close",
                                          "quote_volume", "trade_count"])
    except (OSError, ValueError, KeyError):
        return []
    df = _resample(raw.sort_values("timestamp").reset_index(drop=True), TF)
    ts = df["timestamp"].to_numpy(np.int64)
    hi = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
    n = len(ts)
    if n < HORIZON + 300:
        return []
    rng = np.random.default_rng(abs(hash(sym)) % (2**32) ^ RNG_SEED)
    dev_end = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6
    hi_bar = int(np.searchsorted(ts, dev_end)) - HORIZON - 2
    if hi_bar < 250:
        return []
    out = []
    for rp in risks:
        for _ in range(N_PLACEBO):
            e = int(rng.integers(200, hi_bar))
            entry = cl[e]; stop = entry * (1 - rp)
            mfe, sh, cr = _race_summary(e, entry, stop, hi, lo, cl, n)
            y, r = resolve_rr(mfe, sh, cr, RR)
            out.append({"symbol": sym, "week": pd.Timestamp(int(ts[e]), unit="ms", tz="UTC").strftime("%G-W%V"),
                        "R": r, "y": y})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reversal", type=Path, default=Path(".output/results/knife_catch/dump_reversal.parquet"))
    ap.add_argument("--trigger", default="flow")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    t = pd.read_parquet(args.reversal)
    real = t[(t.trigger == args.trigger) & (t.entered == 1)].dropna(subset=["risk_pct"]).copy()
    print(f"real {args.trigger} trades={len(real)}  meanR={real.R.mean():+.3f}  win={real.y.mean()*100:.1f}%  "
          f"posWk={ (real.groupby('week').R.mean()>0).mean()*100:.0f}%")

    tasks = [(sym, g.risk_pct.to_numpy()) for sym, g in real.groupby("symbol")]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(tasks)} placebo rows={len(rows):,}")
    pb = pd.DataFrame(rows)
    print(f"\nPLACEBO (matched random longs) n={len(pb):,}  meanR={pb.R.mean():+.3f}  win={pb.y.mean()*100:.1f}%  "
          f"posWk={ (pb.groupby('week').R.mean()>0).mean()*100:.0f}%")
    # symbol-concentration of the REAL edge: is meanR from a few coins?
    sym_r = real.groupby("symbol").R.mean()
    top = real.groupby("symbol").R.sum().sort_values(ascending=False)
    share = top.head(10).sum() / real.R.sum() if real.R.sum() != 0 else np.nan
    print(f"\nreal meanR={real.R.mean():+.3f} vs placebo {pb.R.mean():+.3f}  ->  EDGE = {real.R.mean()-pb.R.mean():+.3f} R/trade")
    print(f"symbols with meanR>0: {(sym_r>0).mean()*100:.0f}%  | top-10 symbols carry {share*100:.0f}% of total R "
          f"(of {real.symbol.nunique()} symbols)")


if __name__ == "__main__":
    main()
