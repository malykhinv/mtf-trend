"""IS execution test: does the session-break MFE edge convert to positive R?

Reuses the frozen core ``simulate_long_path`` (no new sim loop). For every scored
break event we test a grid of ENTRY timings x initial STOP styles, all long, all
causal. The structural swing-low ZigZag trail (``swing_reversal_atr``) rides the
move in every cell -- per the fp whipsaw finding a tight fixed exit would be self-
defeating; only the INITIAL stop and the entry timing vary.

Look-ahead discipline (audited):
  * fill is always the OPEN of the bar AFTER the decision bar (sim contract);
  * every decision bar uses only information at//before it;
  * initial stop uses only bars <= decision bar;
  * the forward window is sliced [decision : decision+H] so the sim can never
    see beyond the horizon;
  * ranker scores are out-of-fold (see score_events.py);
  * intrabar, the sim checks the stop before the target (pessimistic).

ENTRY timings (decision bar d; fill at d+1 open):
  break       d = break bar b.
  confirm15   first 15m-boundary 1m bar >= b that CLOSES above ref_high.
  retest      first bar > b whose low <= ref_high (pullback to the level).
  prebreak    earliest bar in the run-up (< b) that is near the level from below
              AND shows an activity surge (rvol & vol_accel above thresholds).
              NOTE: conditioned on a break later occurring -> optimistic; treat as
              an upper bound, not a clean rule (flagged in the report).

STOP styles (initial stop; long):
  atr1        entry_ref - 1.0 * ATR      (tight)
  atr25       entry_ref - 2.5 * ATR      (wide)
  struct30    below min-low of last 30 bars, ATR-buffered   (structural, narrow)
  struct60    below min-low of last 60 bars, ATR-buffered   (structural, wide)
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr, simulate_long_path
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW, BASELINE_BARS

SPEC = PumpLongExecutionSpec()
SWING_TRAIL_ATR = 1.0          # ZigZag confirmation in units of the 30-min range ATR
HORIZON = 480                  # cap the forward window (8h) so the sim can't peek
TRADE_ATR_WIN = 30             # stop/trail unit = typical 30-min price range (NOT
                               # the per-minute ATR, which is microscopic and would
                               # make every stop a whipsaw)
PREBAND_ATR = 0.5              # "near the level" for the pre-break entry
PRE_RVOL = 2.0                 # activity-surge thresholds for the pre-break entry
PRE_VACCEL = 1.5
ENTRIES = ("break", "confirm15", "retest", "prebreak")
STOPS = ("atr_narrow", "atr_wide", "struct30", "struct60")
CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")


def _decision_bar(entry: str, *, b: int, start: int, ref_high: float,
                  ts, high, low, close, opn, atr, rvol_bar, vaccel_bar, n) -> int | None:
    if entry == "break":
        return b
    if entry == "confirm15":
        for j in range(b, min(b + 120, n)):
            # 15m boundary = minute-of-hour multiple of 15; close confirms break
            if ((ts[j] // 60000) % 15 == 14) and close[j] > ref_high:
                return j
        return None
    if entry == "retest":
        for j in range(b + 1, min(b + 120, n)):
            if low[j] <= ref_high:
                return j
        return None
    if entry == "prebreak":
        # scan the run-up BEFORE the break for the first activity surge near level
        for j in range(start, b):
            if (high[j] >= ref_high - PREBAND_ATR * atr[b]
                    and rvol_bar[j] >= PRE_RVOL and vaccel_bar[j] >= PRE_VACCEL):
                return j
        return None
    return None


def _initial_stop(stop: str, *, d: int, low, atr_trade) -> float:
    ref = float(low[d])  # anchor the stop off the decision bar's low / structure
    a = float(atr_trade[d])
    if not (np.isfinite(a) and a > 0):
        return np.nan
    if stop == "atr_narrow":
        return ref - 0.5 * a
    if stop == "atr_wide":
        return ref - 1.5 * a
    if stop == "struct30":
        lo = float(np.min(low[max(0, d - 29):d + 1]))
        return lo - 0.1 * a
    if stop == "struct60":
        lo = float(np.min(low[max(0, d - 59):d + 1]))
        return lo - 0.1 * a
    return np.nan


def run_symbol(path: Path, ev: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_parquet(path, columns=[
        "timestamp", "open", "high", "low", "close", "volume", "quote_volume", "trade_count"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"].to_numpy(np.int64)
    opn = df["open"].to_numpy(float); high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float); close = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); tc = df["trade_count"].to_numpy(float)
    atr = causal_atr(high=high, low=low, close=close, window=ATR_WINDOW)
    # session-scale stop/trail unit: the typical 30-min price range, causal.
    roll_hi = pd.Series(high).rolling(TRADE_ATR_WIN, min_periods=TRADE_ATR_WIN).max()
    roll_lo = pd.Series(low).rolling(TRADE_ATR_WIN, min_periods=TRADE_ATR_WIN).min()
    atr_trade = (roll_hi - roll_lo).to_numpy()
    n = len(df)
    # per-bar causal activity for the pre-break trigger
    base_qv = pd.Series(qv).rolling(BASELINE_BARS, min_periods=240).median().to_numpy()
    rvol_bar = qv / np.where(base_qv > 0, base_qv, np.nan)
    qv5 = pd.Series(qv).rolling(5, min_periods=1).mean().to_numpy()
    qv_prior = pd.Series(qv).shift(5).rolling(15, min_periods=1).mean().to_numpy()
    vaccel_bar = qv5 / np.where(qv_prior > 0, qv_prior, np.nan)

    # map event break_ts -> bar index
    ts_to_idx = {int(t): i for i, t in enumerate(ts)}
    rows = []
    for r in ev.itertuples():
        b = ts_to_idx.get(int(r.break_ts))
        if b is None:
            continue
        start = b - int(r.bars_to_break)
        for entry in ENTRIES:
            d = _decision_bar(entry, b=b, start=start, ref_high=float(r.ref_high),
                              ts=ts, high=high, low=low, close=close, opn=opn, atr=atr,
                              rvol_bar=rvol_bar, vaccel_bar=vaccel_bar, n=n)
            if d is None or d + 1 >= n:
                continue
            ei = d + 1  # fill at next open
            win_end = min(d + 1 + HORIZON, n)
            for stop in STOPS:
                sp = _initial_stop(stop, d=d, low=low, atr_trade=atr_trade)
                if not np.isfinite(sp):
                    continue
                res = simulate_long_path(
                    open_=opn[d:win_end], high=high[d:win_end], low=low[d:win_end],
                    close=close[d:win_end], entry_index=1, initial_stop_price=sp,
                    spec=SPEC, swing_reversal_atr=SWING_TRAIL_ATR, atr=atr_trade[d:win_end],
                )
                if res.status != "filled":
                    continue
                rows.append({
                    "symbol": r.symbol, "variant": r.variant, "tier": r.tier,
                    "session": r.session, "week": r.week, "score_pct": r.score_pct,
                    "entry": entry, "stop": stop, "net_r": res.net_r,
                    "net_return": res.net_return, "exit_reason": res.exit_reason,
                    "hold": res.holding_minutes,
                })
    return pd.DataFrame(rows)


def _one(args):
    sym, path_str, ev_bytes = args
    ev = pd.read_parquet(ev_bytes)
    try:
        return run_symbol(Path(path_str), ev)
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/events_scored.parquet"))
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/trades.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--top-decile-only", action="store_true",
                    help="only trade events with score_pct>=0.9 (much faster)")
    args = ap.parse_args()

    scored = pd.read_parquet(args.scored)
    if args.top_decile_only:
        scored = scored[scored["score_pct"] >= 0.90]
    tmp = args.out.parent / "_ev_shards"
    tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in scored.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"
        g.to_parquet(shard)
        tasks.append((sym, str(p), str(shard)))
    print(f"symbols: {len(tasks)}  top_decile_only={args.top_decile_only}")

    frames = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            f = fut.result()
            if len(f):
                frames.append(f)
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(tasks)}  trades={sum(len(x) for x in frames):,}")
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    trades.to_parquet(args.out)
    print(f"wrote {len(trades):,} trades -> {args.out}")


if __name__ == "__main__":
    main()
