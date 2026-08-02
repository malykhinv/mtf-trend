"""EXIT-family plateau study for the knife-catch (does letting it run beat early fixed-RR?),
with slippage AND a matched drift placebo per exit family.

Entry (M3_early): from the sleep top, once price dropped >= MIN_DROP, enter at the first
flow signal (green + taker>0.5 + OI-not-falling on its NATIVE ~5-min cadence), next-open
fill, running-min stop. Then simulate exit families over a long horizon and sweep each
parameter. A real edge shows a PLATEAU and beats a matched random-entry placebo net of
slippage. Metrics: meanR, medR, posWk, top%toNeg (target ~40%). DEV only.

Exit families: FIX m (target=entry+m*risk), TRAIL p (run_max*(1-p) floor init_stop), HOLD.
Slippage: BUY fills at entry*(1+slip); every SELL at level*(1-slip).
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
FIX_MS = (1.0, 2.0, 3.0, 5.0, 8.0)
TRAIL_PS = (0.12, 0.20, 0.30, 0.50)
FAMILIES = [f"FIX_{m}" for m in FIX_MS] + [f"TRAIL_{int(p*100)}" for p in TRAIL_PS] + ["HOLD"]


def _exits(e0, entry_raw, stop0, hi, lo, cl, n, we, slip):
    """Return {family: R} for one entry, slippage-aware (buy*(1+slip), sell*(1-slip))."""
    entry = entry_raw * (1 + slip); risk = entry - stop0
    if risk <= 0:
        return None
    r = {}
    for m in FIX_MS:
        target = entry + m * risk; out = None
        for j in range(e0, we + 1):
            if lo[j] <= stop0:
                out = (stop0 * (1 - slip) - entry) / risk; break
            if hi[j] >= target:
                out = (target * (1 - slip) - entry) / risk; break
        r[f"FIX_{m}"] = float(out if out is not None else (cl[we] * (1 - slip) - entry) / risk)
    for p in TRAIL_PS:
        run_max = entry; out = None
        for j in range(e0, we + 1):
            run_max = max(run_max, hi[j]); eff = max(stop0, run_max * (1 - p))
            if lo[j] <= eff:
                out = (eff * (1 - slip) - entry) / risk; break
        r[f"TRAIL_{int(p*100)}"] = float(out if out is not None else (cl[we] * (1 - slip) - entry) / risk)
    r["HOLD"] = float((cl[we] * (1 - slip) - entry) / risk)
    return r


def build_symbol(path: Path, tf: int, slip: float, min_drop: float = MIN_DROP,
                 exclude_week: str = "") -> list[dict]:
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
    rng = np.random.default_rng(abs(hash((sym, tf))) % (2**32))
    dev_hi = int(np.searchsorted(ts, DEV_END)) - horizon - 2
    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or t >= b or b >= n - 3:
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
        entry = float(op[ecb]); stop = run_min * (1 - STOP_BUF)
        if entry <= stop:
            continue
        we = min(ecb + horizon, n - 1)
        wk = pd.Timestamp(int(ts[ecb]), unit="ms", tz="UTC").strftime("%G-W%V")
        if exclude_week and wk == exclude_week:
            continue
        r = _exits(ecb, entry, stop, hi, lo, cl, n, we, slip)
        if r is None:
            continue
        for fam, R in r.items():
            out.append({"symbol": sym, "week": wk, "family": fam, "R": R})
        # matched drift placebo: random-bar long, same risk fraction, same families
        risk_frac = (entry * (1 + slip) - stop) / (entry * (1 + slip))
        if dev_hi > 250 and 0 < risk_frac < 0.95:
            cb = int(rng.integers(200, dev_hi)); pe = float(op[cb + 1]); pstop = pe * (1 - risk_frac)
            pwe = min(cb + 1 + horizon, n - 1)
            pr = _exits(cb + 1, pe, pstop, hi, lo, cl, n, pwe, slip)
            if pr is not None:
                pwk = pd.Timestamp(int(ts[cb + 1]), unit="ms", tz="UTC").strftime("%G-W%V")
                for fam, R in pr.items():
                    out.append({"symbol": sym, "week": pwk, "family": "P_" + fam, "R": R})
    return out


def _one(args):
    f, tf, slip, min_drop, exclude_week = args
    try:
        return build_symbol(Path(f), tf, slip, min_drop, exclude_week)
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


def _report(t, tf, slip):
    print(f"\n=== EXIT plateau  TF={tf}m  entry=M3_early  slip={slip:.1%}  (deep dumps>{MIN_DROP:.0%}, horizon={HORIZON_H:.0f}h) ===")
    print(f"  {'family':<10} {'n':>6} {'meanR':>7} {'medR':>7} {'win%':>6} {'posWk%':>7} {'top%toNeg':>10} {'placeboR':>9} {'EDGE':>7}")
    for fam in FAMILIES:
        g = t[t.family == fam]; p = t[t.family == "P_" + fam]
        if len(g) < 30:
            continue
        wk = g.groupby("week").R.mean(); win = (g.R > 0).mean()
        pm = p.R.mean() if len(p) >= 30 else float("nan")
        print(f"  {fam:<10} {len(g):>6} {g.R.mean():>+7.3f} {g.R.median():>+7.3f} {win*100:>6.1f} "
              f"{(wk>0).mean()*100:>7.0f} {_top_pct_to_neg(g.R.to_numpy()):>9.1f}% {pm:>+9.3f} {g.R.mean()-pm:>+7.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--slip", type=float, default=0.003)
    ap.add_argument("--min-drop", type=float, default=MIN_DROP)
    ap.add_argument("--exclude-week", default="", help="e.g. 2025-W41 to drop the market-crash week")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    out = args.out or Path(f".output/results/knife_catch/dump_exits_tf{args.tf}.parquet")
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"EXIT study tf={args.tf}m entry=M3_early slip={args.slip:.1%} drop>{args.min_drop:.0%} "
          f"exclude_week={args.exclude_week or 'none'} horizon={HORIZON_H:.0f}h symbols={len(files)}")
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, (f, args.tf, args.slip, args.min_drop, args.exclude_week)): f for f in files}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(files)} rows={len(rows):,}")
    t = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(out)
    print(f"\nwrote {len(t):,} rows -> {out}")
    if len(t):
        _report(t, args.tf, args.slip)
        print("\n(EDGE = real meanR - matched random-long placebo. A tradeable exit: EDGE>0, top%toNeg>=40, "
              "across a PLATEAU of adjacent params, net of slippage.)")


if __name__ == "__main__":
    main()
