"""Decompose the knife-catch by TF x ENTRY-REASON, with a trader's WIN/LOSS label.

For each timeframe and each distinct causal reversal REASON (why we'd go long at the low),
we fire the first entry after price has fallen >= MIN_DROP from the sleep top, then race a
trader-meaningful outcome:
  WIN  = price retraces >= RETRACE of the drop (buys back to the midpoint) ...
  LOSS = ... BEFORE it makes a NEW LOW below the bottom (перелой).
Unresolved within the horizon is dropped. We report win-rate per (TF, reason), the week
distribution and the single biggest-week share, and the numbers EXCLUDING the market-crash
week (so a real repeatable signal is not just one crash). DEV only.

Reasons (all causal, evaluated at the signal bar j; OI on its native ~5-min cadence):
  flow        -- green bar, taker-buy>0.5, OI not falling (the original trigger).
  aggression  -- seller aggression EXHAUSTS: taker-buy share jumps well above the dump's mean.
  absorption  -- volume climax with a long lower wick that holds (buyers absorb the sells).
  break_long  -- price closes back above the last ~30-min high (structure break up / reclaim).
  higher_low  -- a confirmed swing low ABOVE the running low (causal pivot).
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
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TFS = (1, 5, 10, 15)
MIN_DROP = 0.25
SCAN_H = 8.0
HORIZON_H = 72.0
RETRACE = 0.50
REASONS = ("flow", "aggression", "absorption", "break_long", "higher_low")


def _first_entry(reason, t, b, hi, lo, cl, op, taker, oi, qv, base_qv, min_drop, scan, oi_lb, tf, n):
    """Return (entry_bar, bottom_price) for the first bar where REASON fires after the drop
    has reached min_drop, else (None, None). Strictly causal (data <= j)."""
    T = float(hi[t]); run_min = float(lo[t])
    kbreak = max(2, round(30 / tf)); taker_sum = 0.0; nb = 0
    for j in range(t + 1, min(b + scan, n - 2)):
        run_min = min(run_min, lo[j]); taker_sum += taker[j]; nb += 1
        if (T - run_min) / T < min_drop:
            continue
        green = cl[j] > op[j]; rng = hi[j] - lo[j] + 1e-12
        lower_wick = (min(op[j], cl[j]) - lo[j]) / rng
        fire = False
        if reason == "flow":
            fire = green and taker[j] > 0.5 and oi[j] >= oi[max(0, j - oi_lb)]
        elif reason == "aggression":
            fire = green and (taker[j] - taker_sum / max(nb, 1)) > 0.15
        elif reason == "absorption":
            base = np.nanmedian(base_qv[max(0, j - 50):j + 1])
            fire = qv[j] > 3 * (base + 1e-9) and lower_wick > 0.5 and lo[j] >= run_min
        elif reason == "break_long":
            fire = green and cl[j] > float(hi[max(0, j - kbreak):j].max())
        elif reason == "higher_low":
            seg = min(kbreak + 6, n - j)
            if seg >= 6:
                pl, _ = causal_pivots(hi[j - seg:j + 1], lo[j - seg:j + 1], 2)
                fire = np.isfinite(pl[-1]) and pl[-1] > run_min
        if fire:
            return j + 1, run_min
    return None, None


def _winloss(eb, bottom, T, hi, lo, n, horizon):
    tgt = bottom + RETRACE * (T - bottom)          # 50%-retrace target
    we = min(eb + horizon, n - 1)
    for k in range(eb, we + 1):
        if lo[k] < bottom:                         # new low first -> LOSS (pereloy); pessimistic tie
            return 0
        if hi[k] >= tgt:                           # 50% buy-back first -> WIN
            return 1
    return -1                                       # unresolved


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
            wk = pd.Timestamp(int(ts[eb]), unit="ms", tz="UTC").strftime("%G-W%V")
            y = _winloss(eb, bottom, T, hi, lo, n, horizon)
            if y < 0:
                continue
            out.append({"symbol": sym, "tf": tf, "reason": reason, "week": wk,
                        "is_crash": int(wk == exclude_week), "y": y})
    return out


def _one(a):
    f, tf, min_drop, exclude_week = a
    try:
        return build_symbol(Path(f), tf, min_drop, exclude_week)
    except Exception:  # noqa: BLE001
        return []


def _report(t, exclude_week):
    print(f"\n{'TF':>3} {'reason':<12} {'n':>6} {'win%':>6} {'wks':>4} {'topWk%':>7} || "
          f"{'n(noCrash)':>10} {'win%':>7} {'wks':>4} {'posWk%':>7}")
    for tf in sorted(t.tf.unique()):
        for reason in REASONS:
            g = t[(t.tf == tf) & (t.reason == reason)]
            if len(g) < 30:
                continue
            tw = g.groupby("week").size(); topwk = tw.max() / len(g) * 100
            nc = g[g.is_crash == 0]
            if len(nc) >= 10:
                wkwin = nc.groupby("week").y.mean()
                posWk = (wkwin > 0.5).mean() * 100
                print(f"{tf:>3} {reason:<12} {len(g):>6} {g.y.mean()*100:>5.1f}% {tw.size:>4} {topwk:>6.0f}% || "
                      f"{len(nc):>10} {nc.y.mean()*100:>6.1f}% {nc.week.nunique():>4} {posWk:>6.0f}%")
            else:
                print(f"{tf:>3} {reason:<12} {len(g):>6} {g.y.mean()*100:>5.1f}% {tw.size:>4} {topwk:>6.0f}% || (noCrash too few)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, action="append", default=None)
    ap.add_argument("--min-drop", type=float, default=MIN_DROP)
    ap.add_argument("--exclude-week", default="2025-W41")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    tfs = tuple(args.tf) if args.tf else TFS
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    print(f"signal decomposition tfs={tfs} drop>{args.min_drop:.0%} WIN=>{RETRACE:.0%}retrace LOSS=new-low "
          f"crash_week={args.exclude_week} symbols={len(files)}")
    tasks = [(f, tf, args.min_drop, args.exclude_week) for tf in tfs for f in files]
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, tk): tk for tk in tasks}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(tasks)} rows={len(rows):,}")
    t = pd.DataFrame(rows)
    Path(".output/results/knife_catch").mkdir(parents=True, exist_ok=True)
    t.to_parquet(".output/results/knife_catch/dump_signals.parquet")
    print(f"\nwrote {len(t):,} entries")
    if len(t):
        _report(t, args.exclude_week)
        print("\n(WIN=buy-back reaches 50% of the drop before a new low. Want: win%>50 that HOLDS "
              "excluding the crash week, distributed across many weeks (low topWk%), posWk>50.)")


if __name__ == "__main__":
    main()
