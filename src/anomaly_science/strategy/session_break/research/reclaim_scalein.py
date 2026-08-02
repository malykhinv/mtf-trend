"""Scale-in + close-based-stop variant of the reclaim short (user design):

  * enter 0.5 unit at the reclaim (return bar next open);
  * resting LIMIT short 0.5 unit at the breakout high (ref_high) -> average up to
    1.0 unit if price rallies back to the level (a better blended entry);
  * STOP = a candle CLOSE above ref_high -> cover the whole position at market
    (close). This is close-based, so it survives the intrabar wick re-test that a
    touch-stop above the poke got shaken out by;
  * TARGET = range mid (a resting limit; fills intrabar when low<=mid);
  * variant B: require TWO consecutive closes above ref_high before stopping.

R is measured against the structural risk = ref_high - entry (1R = entry-to-level).
Costs charged per leg per side (maker limits for entry/add/target, market for the
stop). GROSS + net at maker/taker, win rate, week-stability -- does the close-stop
+ scale-in finally capture the real structural edge?
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.session_break.research.reclaim import _resample

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
HORIZON = 72  # ~6 sessions of 60m


def run_symbol(path: Path, ev: pd.DataFrame, tf_min: int, n_close: int) -> pd.DataFrame:
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "volume", "quote_volume", "trade_count",
                                         "taker_buy_quote_volume", "open_interest"])
    df = _resample(raw.sort_values("timestamp").reset_index(drop=True), tf_min)
    ts = df["timestamp"].to_numpy(np.int64); o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    n = len(df); idx = {int(t): i for i, t in enumerate(ts)}
    rows = []
    for r in ev.itertuples():
        d0 = idx.get(int(r.break_ts))
        if d0 is None or d0 + 2 >= n:
            continue
        ei = d0 + 1
        entry = o[ei]; hi = float(r.ref_high); mid = float(r.ref_mid)
        risk = hi - entry
        if not (entry > mid and risk > 0):
            continue
        we = min(ei + HORIZON, n)
        leg2 = False; above = 0; exit_px = None; reason = None
        for j in range(ei, we):
            # resting limit add at the breakout high (maker) once price rallies to it
            if not leg2 and h[j] >= hi:
                leg2 = True
            # target: limit buy at mid fills intrabar (favourable, realistic)
            if lo[j] <= mid:
                exit_px = mid; reason = "mid"; break
            # close-based stop: n_close consecutive closes above the high
            above = above + 1 if c[j] > hi else 0
            if above >= n_close:
                exit_px = c[j]; reason = "stop"; break
        if exit_px is None:
            exit_px = c[we - 1]; reason = "time"
        # position-weighted P&L (short): leg1 always 0.5 @ entry, leg2 0.5 @ hi if filled
        size = 0.5 + (0.5 if leg2 else 0.0)
        pnl = 0.5 * (entry - exit_px) + (0.5 * (hi - exit_px) if leg2 else 0.0)
        rows.append({
            "symbol": r.symbol, "week": r.week, "score": float(r.score), "label": int(r.label),
            "reason": reason, "leg2": int(leg2), "size": size,
            "r": pnl / risk,                       # R vs entry-to-level structural risk
            "notional": 0.5 * entry + (0.5 * hi if leg2 else 0.0),
            "gross_pnl": pnl,
        })
    return pd.DataFrame(rows)


def _one(a):
    sym, p, shard, tf, nc = a
    try:
        return run_symbol(Path(p), pd.read_parquet(shard), tf, nc)
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/rlabel_tf60_scored.parquet"))
    ap.add_argument("--tf", type=int, default=60)
    ap.add_argument("--n-close", type=int, default=1, help="closes above high to stop (1 or 2)")
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/scalein_trades.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    sc = pd.read_parquet(args.scored)
    sc = sc[(sc.break_ts < DEV_END) & sc.score.notna()].drop_duplicates(["symbol", "break_ts"])
    tmp = args.out.parent / "_si_shards"; tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in sc.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"; g.to_parquet(shard)
        tasks.append((sym, str(p), str(shard), args.tf, args.n_close))
    print(f"symbols={len(tasks)} n_close={args.n_close}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            f = fut.result()
            if len(f):
                frames.append(f)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(tasks)} trades={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)

    def rep(sub, lbl):
        if len(sub) < 50:
            return f"{lbl:20s} few"
        wk = sub.groupby("week")["r"].sum()
        return (f"{lbl:20s} n={len(sub):6d} win={(sub.r>0).mean():.2f} leg2_fill={sub.leg2.mean():.2f} "
                f"gross_R={sub.r.mean():+.3f} med={sub.r.median():+.3f} pos_wk={(wk>0).mean():.2f} "
                f"mid%={(sub.reason=='mid').mean():.2f} stop%={(sub.reason=='stop').mean():.2f} "
                f"time%={(sub.reason=='time').mean():.2f}")

    print(f"\nwrote {len(t):,} trades  (R vs entry->level risk; GROSS, no cost)")
    print(rep(t, "ALL"))
    print("--- by MID-score tier ---")
    for qs, lbl in [(0.5, "score>50%"), (0.8, "score>80%"), (0.9, "score>90%")]:
        print(rep(t[t.score >= t.score.quantile(qs)], lbl))


if __name__ == "__main__":
    main()
