"""Clean first-passage confirmation of the deep-revert short: TARGET = range LOW,
STOP = 2 closes above the prior-session high (close-based, matched to the deep
label's ABOVE branch -- no stop/frequency mismatch). Real price-space P&L (% of
price), cost sweep incl. slippage, symbol concentration + week-stability, and the
sweet-spot filter from the analysis (moving coin, weak high, downtrend, normal vol).

Answers: does bottom-targeting actually pay after realistic costs, or was the EV
table an artifact? Reads rlabel_tf60_deep.parquet (geometry + features) + 1m cache.
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
HORIZON = 48  # ~2 sessions of 60m


def run_symbol(path: Path, ev: pd.DataFrame, tf_min: int) -> pd.DataFrame:
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
        entry = o[ei]; hi = float(r.ref_high); low = float(r.ref_low)
        if not (entry > low and hi > entry):
            continue
        we = min(ei + HORIZON, n)
        above = 0; exit_px = None; reason = "time"
        for j in range(ei, we):
            if lo[j] <= low:                     # target (favourable) checked first
                exit_px = low; reason = "low"; break
            above = above + 1 if c[j] > hi else 0
            if above >= 2:                       # close-based stop = 2 closes above the high
                exit_px = c[j]; reason = "stop"; break
        if exit_px is None:
            exit_px = c[we - 1]
        pnl_pct = (entry - exit_px) / entry      # short P&L, fraction of price
        rows.append({
            "symbol": r.symbol, "week": r.week, "session": int(r.session),
            "pnl_bp": pnl_pct * 1e4, "reason": reason,
            "atr_pct": float(r.atr_pct), "sess_ampl_pct": float(r.sess_ampl_pct),
            "prior_up_imp": float(r.prior_up_imp), "ema_dist": float(r.ema_dist),
            "poke_rvol": float(r.poke_rvol), "prior_high_prom": float(r.prior_high_prom),
            "reward_low_bp": (entry - low) / entry * 1e4,
        })
    return pd.DataFrame(rows)


def _one(a):
    sym, p, shard, tf = a
    try:
        return run_symbol(Path(p), pd.read_parquet(shard), tf)
    except Exception:
        return pd.DataFrame()


def report(t: pd.DataFrame, name: str, costs=(4, 8, 12, 20)) -> None:
    if len(t) < 200:
        print(f"{name:26s} n={len(t)} (too few)"); return
    sp = t.groupby("symbol").pnl_bp.sum().sort_values(ascending=False)
    top5 = sp.head(5).sum() / sp.sum() if sp.sum() != 0 else np.nan
    parts = []
    for cb in costs:
        net = t.pnl_bp - cb
        wk = net.groupby(t.week).mean()
        parts.append(f"@{cb}bp={net.mean():+6.1f}({(wk>0).mean():.2f}w)")
    print(f"{name:26s} n={len(t):6d} ~{len(t)/t.week.nunique():4.0f}/wk  gross={t.pnl_bp.mean():+6.1f}bp "
          f"win={(t.pnl_bp>0).mean():.2f} low%={(t.reason=='low').mean():.2f} stop%={(t.reason=='stop').mean():.2f} "
          f"top5={top5:.2f} | " + " ".join(parts))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/rlabel_tf60_deep.parquet"))
    ap.add_argument("--tf", type=int, default=60)
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/deeptrade.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    ev = pd.read_parquet(args.events)
    ev = ev[ev.break_ts < DEV_END].drop_duplicates(["symbol", "break_ts"])
    tmp = args.out.parent / "_dt_shards"; tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in ev.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"; g.to_parquet(shard)
        tasks.append((sym, str(p), str(shard), args.tf))
    print(f"symbols={len(tasks)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            f = fut.result()
            if len(f):
                frames.append(f)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(tasks)} {sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)
    print(f"\nwrote {len(t):,} target-LOW trades | price-space net (mean bp, +week frac) | close-stop=2closes>hi\n")
    report(t, "ALL")
    print("\n-- single filters --")
    report(t[t.atr_pct >= 0.007], "coin moves (atr>=0.7%)")
    report(t[(t.sess_ampl_pct >= 0.02) & (t.sess_ampl_pct < 0.04)], "session 2-4%")
    report(t[t.prior_up_imp < 2], "weak high (up_imp<2)")
    report(t[t.ema_dist < 0], "downtrend (ema_dist<0)")
    report(t[t.poke_rvol < 4], "normal vol (rvol<4)")
    print("\n-- combined sweet spot --")
    ss = t[(t.atr_pct >= 0.007) & (t.sess_ampl_pct >= 0.02) & (t.sess_ampl_pct < 0.05) &
           (t.prior_up_imp < 2) & (t.ema_dist < 0) & (t.poke_rvol < 4)]
    report(ss, "moving+weak+down+normvol")
    ss2 = t[(t.atr_pct >= 0.007) & (t.prior_up_imp < 2) & (t.ema_dist < 0)]
    report(ss2, "moving+weak+down")
    print("\n-- sweet spot by session --")
    from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ
    for s, g in ss2.groupby("session"):
        report(g, f"  {BLOCK_BY_SEQ[int(s)].name}")


if __name__ == "__main__":
    main()
