"""Compare ENTRY models for the knife-catch on DEEP dumps (drop > MIN_DROP), PER TIMEFRAME
(1m/5m/10m/15m), all strictly causal, with robust metrics (meanR, medianR, posWk, top%toNeg
= % of best trades to remove before total R<=0; target ~40%) and a matched drift placebo.

Models:
  M1_flow  -- hardened flow trigger (>= b+confirm, next-open fill, running-min stop).
  M3_early -- causal EARLY entry: from the sleep top, once price dropped >= MIN_DROP, enter at
              the first flow signal (no forward bottom-confirm), next-open fill.
  M4_grid  -- DCA BASKET: rest limits at T*(1-p); each level price reaches ADDS a tranche to ONE
              position (average down). One catastrophic stop below the deepest tranche, one
              target above the AVERAGE entry. One trade per dump.

Outcome: fixed RR race (target-first vs stop, honest horizon time-stop). DEV only.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol, confirm_bars
from anomaly_science.strategy.knife_catch.research import dump_reversal as DR
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TFS = (1, 5, 10, 15)
MIN_DROP = 0.50
RR = 2.0
STOP_BUF = 0.004
SCAN_H = 8.0              # hours after the top to look for the causal entry
HORIZON_H = 24.0         # hours to resolve the trade
# M4 DCA basket
ARM = 0.45
GRID = (0.5, 0.6, 0.7, 0.8, 0.9)
CAT_BUF = 0.15           # catastrophic stop below the DEEPEST filled tranche
FILL_H = 24.0            # hours to let a level fill after arming


def _race(entry_bar, entry_raw, stop_level, hi, lo, cl, n, horizon, slip=0.0):
    """Slippage-aware: BUY fills at entry_raw*(1+slip); every SELL (stop/target/timeout)
    fills at level*(1-slip). Tight stops are punished proportionally more by a fixed slip%."""
    entry = entry_raw * (1 + slip)
    risk = entry - stop_level
    if risk <= 0:
        return None
    target = entry + RR * risk
    we = min(entry_bar + horizon, n - 1)
    for j in range(entry_bar, we + 1):
        if lo[j] <= stop_level:
            return float((stop_level * (1 - slip) - entry) / risk)       # ~ -1 minus slip
        if hi[j] >= target:
            return float((target * (1 - slip) - entry) / risk)           # ~ +RR minus slip
    return float((cl[we] * (1 - slip) - entry) / risk)


def build_symbol(path: Path, tf: int, slip: float = 0.0) -> list[dict]:
    try:
        dumps = [d for d in _scan_symbol(path, tf) if d.culmination_ms < DEV_END and d.drop_pct > MIN_DROP]
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
    confirm = confirm_bars(tf); scan = max(2, round(SCAN_H * 60 / tf))
    horizon = max(4, round(HORIZON_H * 60 / tf)); fill_win = max(4, round(FILL_H * 60 / tf))
    oi_lb = max(1, round(5 / tf))                 # OI is native 5-min: compare across ~5 minutes
    rng = np.random.default_rng(abs(hash((sym, tf))) % (2**32))
    dev_hi = int(np.searchsorted(ts, DEV_END)) - horizon - 2
    out = []

    def rec(model, ts_ms, R, risk):
        if R is None:
            return
        out.append({"symbol": sym, "tf": tf, "model": model, "R": float(R),
                    "week": pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").strftime("%G-W%V")})
        if dev_hi > 250 and 0 < risk < 0.95:
            cb = int(rng.integers(200, dev_hi)); pstop = float(op[cb + 1]) * (1 - risk)
            pr = _race(cb + 1, float(op[cb + 1]), pstop, hi, lo, cl, n, horizon, slip)
            if pr is not None:
                out.append({"symbol": sym, "tf": tf, "model": "P_" + model, "R": float(pr),
                            "week": pd.Timestamp(int(ts[cb + 1]), unit="ms", tz="UTC").strftime("%G-W%V")})

    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or b >= n - horizon - 3 or t >= b:
            continue
        T = float(d.pump_start_price)

        # M1 flow
        e = DR._find_trigger("flow", b, hi, lo, cl, op, taker, oi, n)
        if e is not None and e >= b + confirm and e + 1 < n - 1:
            eb = e + 1; stop = float(lo[b:e + 1].min()) * (1 - STOP_BUF)
            if op[eb] > stop:
                rec("M1_flow", ts[eb], _race(eb, float(op[eb]), stop, hi, lo, cl, n, horizon, slip),
                    (float(op[eb]) - stop) / float(op[eb]))

        # M3 early causal (OI compared across ~5 min, its native cadence)
        run_min = float(lo[t]); ecb = None
        for j in range(t + 1, min(b + scan, n - 2)):
            run_min = min(run_min, lo[j])
            if (T - run_min) / T < MIN_DROP:
                continue
            if taker[j] > 0.5 and cl[j] > op[j] and oi[j] >= oi[max(0, j - oi_lb)]:
                ecb = j + 1; break
        if ecb is not None and ecb < n - 1:
            stop = run_min * (1 - STOP_BUF)
            if op[ecb] > stop:
                rec("M3_early", ts[ecb], _race(ecb, float(op[ecb]), stop, hi, lo, cl, n, horizon, slip),
                    (float(op[ecb]) - stop) / float(op[ecb]))

        # M4 DCA basket
        arm = None
        for j in range(t + 1, min(b + scan, n - 2)):
            if (T - lo[j]) / T >= ARM:
                arm = j; break
        if arm is not None:
            fills = []
            for p in GRID:
                level = T * (1 - p)
                for j in range(arm, min(arm + fill_win, n - 2)):
                    if lo[j] <= level:
                        fills.append((j, level)); break
            if fills:
                fills.sort()
                last_bar = fills[-1][0]; levels = np.array([lv for _, lv in fills])
                avg_entry = float(levels.mean()); stop = float(levels.min()) * (1 - CAT_BUF)
                if last_bar < n - 1 and avg_entry > stop:
                    rec("M4_grid", ts[last_bar], _race(last_bar, avg_entry, stop, hi, lo, cl, n, horizon, slip),
                        (avg_entry - stop) / avg_entry)
    return out


def _one(args):
    f, tf, slip = args
    try:
        return build_symbol(Path(f), tf, slip)
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


def _row(m, g):
    wk = g.groupby("week").R.mean(); symp = (g.groupby("symbol").R.mean() > 0).mean()
    win = (g.R >= RR - 1e-9).mean()
    print(f"  {m:<12} {len(g):>6} {g.R.mean():>+7.3f} {g.R.median():>+7.3f} {win*100:>6.1f} "
          f"{(wk>0).mean()*100:>7.0f} {_top_pct_to_neg(g.R.to_numpy()):>9.1f}% {symp*100:>8.0f}%")


def _report(t):
    for tf in sorted(t.tf.unique()):
        tt = t[t.tf == tf]
        print(f"\n=== TF={tf}m  (deep dumps>{MIN_DROP:.0%}, RR={RR}) ===")
        print(f"  {'model':<12} {'n':>6} {'meanR':>7} {'medR':>7} {'win%':>6} {'posWk%':>7} {'top%toNeg':>10} {'symbols+':>9}")
        for m in ("M1_flow", "M3_early", "M4_grid"):
            g = tt[tt.model == m]; p = tt[tt.model == "P_" + m]
            if len(g) < 30:
                print(f"  {m:<12} {len(g):>6} (too few)"); continue
            _row(m, g)
            if len(p) >= 30:
                _row("placebo", p)
                print(f"    -> EDGE over drift: meanR {g.R.mean() - p.R.mean():+.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(".output/results/knife_catch/dump_variants.parquet"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tf", type=int, action="append", default=None)
    ap.add_argument("--slip", type=float, default=0.0, help="per-side slippage fraction (e.g. 0.003 = 0.3%)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    tfs = tuple(args.tf) if args.tf else TFS
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"deep dumps(drop>{MIN_DROP:.0%}) RR={RR} tfs={tfs} slip={args.slip:.1%} grid={GRID} cat_buf={CAT_BUF} symbols={len(files)}")
    tasks = [(f, tf, args.slip) for tf in tfs for f in files]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 400 == 0:
                print(f"  {done}/{len(tasks)} rows={len(rows):,}")
    t = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(args.out)
    print(f"\nwrote {len(t):,} entries -> {args.out}")
    if len(t):
        _report(t)
        print(f"\n(RR={RR} breakeven win%=33.3. top%toNeg target ~40%. medR<0 => typical trade loses. costs not applied.)")


if __name__ == "__main__":
    main()
