"""Portfolio simulation for the knife-catch (entry M3_early, exit FIX_M with running-min stop).

Answers: how many trades, trades/day, % of days with trades, max concurrent positions, max
drawdown, and LIQUIDATION risk under per-trade risk sizing -- especially the correlated
crash-week cluster (the user's "worst-first" concern). Event-driven equity: at each entry
allocate risk_frac * equity; realize R*risk at the exit bar; a futures account is liquidated
if the SIMULTANEOUS open loss (all open positions at -1R at once) exceeds equity.

Honest caveat baked into the print: the universe is survivor-only, so the crash-week deaths
are ABSENT -> this sim OVERSTATES the crash-week outcome.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6
MIN_DROP = 0.50
STOP_BUF = 0.004
SCAN_H = 8.0
HORIZON_H = 72.0
FIX_M = 8.0
SLIP = 0.003


def build_symbol(path, tf, min_drop, exclude_week=""):
    try:
        dumps = [d for d in _scan_symbol(path, tf) if d.culmination_ms < DEV_END and d.drop_pct > min_drop]
    except (OSError, ValueError, KeyError):
        return []
    if not dumps:
        return []
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "quote_volume", "trade_count", "taker_buy_quote_volume",
                                         "open_interest"]).sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, tf)
    ts = df["timestamp"].to_numpy(np.int64); op = df["open"].to_numpy(float)
    hi = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    n = len(ts); sym = path.stem
    scan = max(2, round(SCAN_H * 60 / tf)); horizon = max(8, round(HORIZON_H * 60 / tf))
    oi_lb = max(1, round(5 / tf))
    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or t >= b or b >= n - 2:
            continue
        T = float(d.pump_start_price); run_min = float(lo[t]); ecb = None
        for j in range(t + 1, min(b + scan, n - 2)):
            run_min = min(run_min, lo[j])
            if (T - run_min) / T < min_drop:
                continue
            if taker[j] > 0.5 and cl[j] > op[j] and oi[j] >= oi[max(0, j - oi_lb)]:
                ecb = j + 1; break
        if ecb is None or ecb >= n - 2:
            continue
        if exclude_week and pd.Timestamp(int(ts[ecb]), unit="ms", tz="UTC").strftime("%G-W%V") == exclude_week:
            continue
        entry = float(op[ecb]) * (1 + SLIP); stop = run_min * (1 - STOP_BUF)
        risk = entry - stop
        if risk <= 0:
            continue
        target = entry + FIX_M * risk; we = min(ecb + horizon, n - 1); R = None; xb = we
        for j in range(ecb, we + 1):
            if lo[j] <= stop:
                R = (stop * (1 - SLIP) - entry) / risk; xb = j; break
            if hi[j] >= target:
                R = (target * (1 - SLIP) - entry) / risk; xb = j; break
        if R is None:
            R = (cl[we] * (1 - SLIP) - entry) / risk
        out.append({"symbol": sym, "entry_ts": int(ts[ecb]), "exit_ts": int(ts[xb]), "R": float(R)})
    return out


def _one(a):
    f, tf, min_drop, exclude_week = a
    try:
        return build_symbol(Path(f), tf, min_drop, exclude_week)
    except Exception:  # noqa: BLE001
        return []


def simulate_adverse(trades, risk_frac, start=1000.0):
    """User's stress: sort trades WORST-first, run sequentially (max early damage). With
    fractional risk sizing a single trade can't liquidate, so this bounds the DRAWDOWN of
    the unluckiest ordering; ruin only if some trade R < -1/risk_frac."""
    Rs = np.sort(trades.R.to_numpy())            # ascending = worst first
    eq = start; peak = start; max_dd = 0.0; ruin = False
    for R in Rs:
        eq += R * (risk_frac * eq)
        if eq <= 0:
            ruin = True; eq = 0.0; break
        peak = max(peak, eq); max_dd = max(max_dd, (peak - eq) / peak)
    return {"final": eq, "max_dd": max_dd, "ruin": ruin}


def simulate(trades: pd.DataFrame, risk_frac: float, start=1000.0):
    """Event-driven: allocate risk_frac*equity at entry; realize R*risk at exit. Track the
    worst SIMULTANEOUS open loss (all-open at -1R) vs equity = liquidation stress."""
    ev = []
    for i, r in trades.iterrows():
        ev.append((int(r.entry_ts), 0, i)); ev.append((int(r.exit_ts), 1, i))
    ev.sort()
    equity = start; open_risk = {}; peak = start; max_dd = 0.0
    max_conc = 0; max_sim_loss_ratio = 0.0; liquidated = False
    for _ts, kind, i in ev:
        if kind == 0:                                   # open: size off current equity
            rk = risk_frac * equity
            open_risk[i] = rk
            max_conc = max(max_conc, len(open_risk))
            sim_loss = sum(open_risk.values())          # if every open position hit -1R now
            max_sim_loss_ratio = max(max_sim_loss_ratio, sim_loss / max(equity, 1e-9))
            if sim_loss >= equity:                      # correlated wipeout would liquidate
                liquidated = True
        else:                                           # close: realize R * its risk
            rk = open_risk.pop(i, 0.0)
            equity += float(trades.loc[i, "R"]) * rk
            peak = max(peak, equity); max_dd = max(max_dd, (peak - equity) / peak)
            if equity <= 0:
                liquidated = True; equity = 0.0; break
    return {"final": equity, "max_dd": max_dd, "max_conc": max_conc,
            "max_sim_loss_ratio": max_sim_loss_ratio, "liquidated": liquidated}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--min-drop", type=float, default=MIN_DROP)
    ap.add_argument("--exclude-week", default="", help="e.g. 2025-W41 to drop the market-crash week")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"portfolio sim tf={args.tf}m entry=M3 exit=FIX_{FIX_M}+stop slip={SLIP:.1%} drop>{args.min_drop:.0%} "
          f"exclude_week={args.exclude_week or 'none'} symbols={len(files)}")
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, (f, args.tf, args.min_drop, args.exclude_week)): f for f in files}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
    t = pd.DataFrame(rows).sort_values("entry_ts").reset_index(drop=True)
    if not len(t):
        print("no trades"); return
    t["day"] = pd.to_datetime(t.entry_ts, unit="ms").dt.date
    t["week"] = pd.to_datetime(t.entry_ts, unit="ms").dt.strftime("%G-W%V")
    span_days = (t.entry_ts.max() - t.entry_ts.min()) / 86_400_000
    print(f"\ntrades={len(t)}  span={span_days:.0f}d  meanR={t.R.mean():+.3f}  winrate={ (t.R>0).mean()*100:.1f}%")
    print(f"trades/day (over span)={len(t)/max(span_days,1):.2f}  distinct trade-days={t.day.nunique()} "
          f"(={t.day.nunique()/max(span_days,1)*100:.1f}% of days)")
    tw = t.groupby('week').size().sort_values(ascending=False)
    print(f"trades in the single biggest week ({tw.index[0]}): {tw.iloc[0]} of {len(t)} ({tw.iloc[0]/len(t)*100:.0f}%)")
    print(f"max trades that OVERLAP in time (concurrent): see sim below")
    print(f"\nchronological (real timing, concurrent positions):")
    print(f"  {'risk/trade':>10} {'final$':>10} {'maxDD':>7} {'maxConc':>8} {'simLoss/eq':>11} {'liquidated?':>12}")
    for rf in (0.02, 0.03, 0.04, 0.05):
        s = simulate(t, rf)
        print(f"  {rf*100:>9.0f}% {s['final']:>10.0f} {s['max_dd']*100:>6.0f}% {s['max_conc']:>8} "
              f"{s['max_sim_loss_ratio']:>10.1f}x {('YES' if s['liquidated'] else 'no'):>12}")
    print(f"\nADVERSARIAL ordering (worst trades first, sequential -- your stress test):")
    print(f"  {'risk/trade':>10} {'final$':>10} {'maxDD':>7} {'ruin?':>7}")
    for rf in (0.02, 0.03, 0.04, 0.05):
        a = simulate_adverse(t, rf)
        print(f"  {rf*100:>9.0f}% {a['final']:>10.0f} {a['max_dd']*100:>6.0f}% {('YES' if a['ruin'] else 'no'):>7}")
    print("\n(maxConc = most positions open at once; simLoss/eq = if ALL open positions hit -1R together, "
          "loss as a multiple of equity (>1x => wiped out). Universe is SURVIVOR-ONLY: crash-week deaths "
          "absent, so real crash outcome is WORSE than shown.)")


if __name__ == "__main__":
    main()
