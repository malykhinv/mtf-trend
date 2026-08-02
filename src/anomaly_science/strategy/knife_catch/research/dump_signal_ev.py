"""EV/R + placebo + portfolio for the surviving knife-catch signals (break_long, aggression).

Converts the trader WIN/LOSS label into R: stop = the bottom (a NEW LOW = loss), target =
50%-retrace of the drop (the WIN). entry = signal next-open (slippage-aware). R = reward/risk
on win, ~-1 on a new low. Matched drift placebo = random long with the SAME risk% and reward%
distances. Reports meanR, medR, win%, EDGE-over-placebo, top%toNeg, posWk -- incl and excl the
crash week -- then a portfolio sim (chronological concurrency + adversarial worst-first). DEV only.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol
from anomaly_science.strategy.knife_catch.research.dump_signals import _first_entry, RETRACE
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TFS = (10, 15)
REASONS = ("break_long", "aggression")
MIN_DROP = 0.20
STOP_BUF = 0.004
SCAN_H = 8.0
HORIZON_H = 72.0
SLIP = 0.003


def _race(eb, entry, stop, target, hi, lo, cl, n, horizon, slip):
    entry = entry * (1 + slip); risk = entry - stop
    if risk <= 0:
        return None, None
    we = min(eb + horizon, n - 1)
    for k in range(eb, we + 1):
        if lo[k] <= stop:
            return (stop * (1 - slip) - entry) / risk, k
        if hi[k] >= target:
            return (target * (1 - slip) - entry) / risk, k
    return (cl[we] * (1 - slip) - entry) / risk, we


def build_symbol(path, tf, min_drop, exclude_week):
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
    base_qv = pd.Series(qv).rolling(240, min_periods=30).median().to_numpy()
    n = len(ts); sym = path.stem
    scan = max(2, round(SCAN_H * 60 / tf)); horizon = max(8, round(HORIZON_H * 60 / tf))
    oi_lb = max(1, round(5 / tf))
    rng = np.random.default_rng(abs(hash((sym, tf))) % (2**32))
    dev_hi = int(np.searchsorted(ts, DEV_END)) - horizon - 2
    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or t >= b or b >= n - 2:
            continue
        T = float(d.pump_start_price)
        for reason in REASONS:
            eb, bottom = _first_entry(reason, t, b, hi, lo, cl, op, taker, oi, qv, base_qv,
                                      min_drop, scan, oi_lb, tf, n)
            if eb is None or eb >= n - 2:
                continue
            entry0 = float(op[eb]); stop = bottom * (1 - STOP_BUF)
            target = bottom + RETRACE * (T - bottom)
            if entry0 <= stop or target <= entry0:
                continue
            R, xb = _race(eb, entry0, stop, target, hi, lo, cl, n, horizon, SLIP)
            if R is None:
                continue
            wk = pd.Timestamp(int(ts[eb]), unit="ms", tz="UTC").strftime("%G-W%V")
            row = {"symbol": sym, "tf": tf, "reason": reason, "week": wk,
                   "is_crash": int(wk == exclude_week), "R": float(R),
                   "entry_ts": int(ts[eb]), "exit_ts": int(ts[xb]), "kind": "real"}
            out.append(row)
            # matched drift placebo: random long, same risk% and reward% distances
            rf = (entry0 * (1 + SLIP) - stop) / (entry0 * (1 + SLIP))
            rw = (target - entry0 * (1 + SLIP)) / (entry0 * (1 + SLIP))
            if dev_hi > 250 and 0 < rf < 0.95 and rw > 0:
                cb = int(rng.integers(200, dev_hi)); pe = float(op[cb + 1])
                pR, _ = _race(cb + 1, pe, pe * (1 - rf), pe * (1 + rw), hi, lo, cl, n, horizon, SLIP)
                if pR is not None:
                    out.append({"symbol": sym, "tf": tf, "reason": reason,
                                "week": pd.Timestamp(int(ts[cb + 1]), unit="ms", tz="UTC").strftime("%G-W%V"),
                                "is_crash": 0, "R": float(pR), "entry_ts": int(ts[cb + 1]),
                                "exit_ts": int(ts[cb + 1]), "kind": "placebo"})
    return out


def _one(a):
    f, tf, md, xw = a
    try:
        return build_symbol(Path(f), tf, md, xw)
    except Exception:  # noqa: BLE001
        return []


def _top_pct_to_neg(R):
    R = np.asarray(R, float)
    if R.sum() <= 0:
        return 0.0
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    return k / len(R) * 100


def simulate(trades, risk_frac, start=1000.0):
    ev = []
    for i, r in trades.iterrows():
        ev.append((int(r.entry_ts), 0, i)); ev.append((int(r.exit_ts), 1, i))
    ev.sort()
    eq = start; openr = {}; peak = start; mdd = 0.0; mc = 0; msl = 0.0; liq = False
    for _t, kind, i in ev:
        if kind == 0:
            rk = risk_frac * eq; openr[i] = rk; mc = max(mc, len(openr))
            msl = max(msl, sum(openr.values()) / max(eq, 1e-9))
            if sum(openr.values()) >= eq:
                liq = True
        else:
            rk = openr.pop(i, 0.0); eq += float(trades.loc[i, "R"]) * rk
            peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
            if eq <= 0:
                liq = True; eq = 0.0; break
    return eq, mdd, mc, msl, liq


def simulate_adverse(trades, risk_frac, start=1000.0):
    eq = start; peak = start; mdd = 0.0
    for R in np.sort(trades.R.to_numpy()):
        eq += R * (risk_frac * eq)
        if eq <= 0:
            return 0.0, 1.0
        peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    return eq, mdd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-drop", type=float, default=MIN_DROP)
    ap.add_argument("--exclude-week", default="2025-W41")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"signal EV tfs={TFS} reasons={REASONS} drop>{args.min_drop:.0%} slip={SLIP:.1%} "
          f"crash={args.exclude_week} symbols={len(files)}")
    tasks = [(f, tf, args.min_drop, args.exclude_week) for tf in TFS for f in files]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(tasks)}")
    t = pd.DataFrame(rows)
    t.to_parquet(".output/results/knife_catch/dump_signal_ev.parquet")
    real = t[t.kind == "real"]; plc = t[t.kind == "placebo"]
    print(f"\nEV per cell (excl crash week {args.exclude_week}):")
    print(f"  {'TF':>3} {'reason':<11} {'n':>5} {'meanR':>7} {'medR':>7} {'win%':>6} {'placeboR':>9} {'EDGE':>7} {'top%toNeg':>10} {'posWk%':>7}")
    for tf in TFS:
        for reason in REASONS:
            g = real[(real.tf == tf) & (real.reason == reason) & (real.is_crash == 0)]
            p = plc[(plc.tf == tf) & (plc.reason == reason)]
            if len(g) < 30:
                continue
            wk = g.groupby("week").R.mean(); pm = p.R.mean() if len(p) >= 30 else float("nan")
            print(f"  {tf:>3} {reason:<11} {len(g):>5} {g.R.mean():>+7.3f} {g.R.median():>+7.3f} "
                  f"{(g.R>0).mean()*100:>5.1f}% {pm:>+9.3f} {g.R.mean()-pm:>+7.3f} "
                  f"{_top_pct_to_neg(g.R.to_numpy()):>9.1f}% {(wk>0).mean()*100:>6.0f}%")
    # portfolio on the pooled best reasons, excl crash, per-symbol dedup not needed (diff reasons rare-overlap)
    best = real[(real.is_crash == 0)].drop_duplicates(subset=["symbol", "entry_ts"]).sort_values("entry_ts")
    print(f"\nPORTFOLIO (pooled {REASONS} on TFs {TFS}, excl crash, deduped): trades={len(best)}")
    span = (best.entry_ts.max() - best.entry_ts.min()) / 86_400_000
    print(f"  meanR={best.R.mean():+.3f} win={ (best.R>0).mean()*100:.0f}% trades/day={len(best)/max(span,1):.2f} "
          f"top%toNeg={_top_pct_to_neg(best.R.to_numpy()):.0f}%")
    print(f"  {'risk':>5} {'chron$':>8} {'DD':>5} {'maxConc':>8} {'simLoss':>8} {'liq?':>5} | {'adv$':>7} {'advDD':>6}")
    for rf in (0.02, 0.03, 0.05):
        fq, dd, mc, msl, liq = simulate(best, rf)
        aq, add = simulate_adverse(best, rf)
        print(f"  {rf*100:>4.0f}% {fq:>8.0f} {dd*100:>4.0f}% {mc:>8} {msl:>7.1f}x {('YES' if liq else 'no'):>5} | "
              f"{aq:>7.0f} {add*100:>5.0f}%")
    print("\n(EDGE = real-placebo. Want EDGE>0, top%toNeg>=40, posWk>50, and a portfolio that survives "
          "adversarial ordering. Universe still survivor-only.)")


if __name__ == "__main__":
    main()
