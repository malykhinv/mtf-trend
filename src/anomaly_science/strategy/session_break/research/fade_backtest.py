"""Mean-reversion FADE-SHORT backtest: sell the pop, target the session mean.

After a break above the previous-session high, short expecting a reversion to the
current session's VWAP. A fade is a bracket (fixed target + fixed stop, no trail),
so the outcome is a clean causal first-passage -- not the frozen long path sim.

The enemy is the *flyer* (a real up-leg runs the short over), so we test where to
enter (sell at the break vs sell into further strength / after exhaustion) and how
far to place the stop above the breakout high. Costs charged both sides.

Look-ahead discipline: entry is the OPEN after the decision bar; the session VWAP
target and the swing-high stop anchor use only bars <= decision; the forward scan
is bounded to the horizon; on an intrabar tie the STOP is assumed hit first
(pessimistic for the short).

ENTRY timings (short decision bar d; fill at d+1 open):
  break       d = break bar b (sell immediately).
  ext1        first bar >= b with high >= break_high + 1*ATR  (sell into strength).
  ext2        first bar >= b with high >= break_high + 2*ATR  (sell deeper stretch).
  stall       first bar > b whose high < previous high AND close < open after a new
              high was made (first lower/red bar off the extreme = exhaustion).
STOP buffers above the running breakout high: 0.3 / 1.0 / 2.0 ATR.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.portrait import DEV_END_MS, add_features

FEE_BPS = 5.0          # per side
SLIP_BPS = 5.0         # per side
HORIZON = 240
ENTRIES = ("break", "ext1", "ext2", "stall")
STOP_BUFS = {"s03": 0.3, "s10": 1.0, "s20": 2.0}
CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
COST = (FEE_BPS + SLIP_BPS) / 10_000.0  # one side; charged on entry and exit notionals


def _decision_bar(entry, *, b, start, break_high, high, low, close, opn, a, n):
    if entry == "break":
        return b
    if entry == "ext1":
        for j in range(b, min(b + 120, n)):
            if high[j] >= break_high + 1.0 * a:
                return j
        return None
    if entry == "ext2":
        for j in range(b, min(b + 120, n)):
            if high[j] >= break_high + 2.0 * a:
                return j
        return None
    if entry == "stall":
        peak = break_high
        made_high = False
        for j in range(b, min(b + 120, n)):
            if high[j] >= peak:
                peak = high[j]
                made_high = True
            elif made_high and close[j] < opn[j]:
                return j  # first red bar off a fresh high
        return None
    return None


def run_symbol(path: Path, ev: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"].to_numpy(np.int64)
    opn = df["open"].to_numpy(float); high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float); close = df["close"].to_numpy(float)
    atr = causal_atr(high=high, low=low, close=close, window=ATR_WINDOW)  # per-min, for VWAP reconstruction
    # 30-min range = the meaningful stop/extension unit (per-min ATR is microscopic
    # and would make every stop smaller than costs -> distorted R).
    roll_hi = pd.Series(high).rolling(30, min_periods=30).max()
    roll_lo = pd.Series(low).rolling(30, min_periods=30).min()
    atr_tr = (roll_hi - roll_lo).to_numpy()
    n = len(df)
    idx = {int(t): i for i, t in enumerate(ts)}

    rows = []
    for r in ev.itertuples():
        b = idx.get(int(r.break_ts))
        if b is None or not np.isfinite(r.stretch_vwap_atr):
            continue
        start = b - int(r.bars_to_break)
        a = atr[b]
        at = atr_tr[b]  # 30-min range: the stop / extension unit
        if not (a > 0 and np.isfinite(at) and at > 0):
            continue
        vwap = float(r.close_break) - float(r.stretch_vwap_atr) * a  # session VWAP (causal)
        break_high = float(high[start:b + 1].max())
        for entry in ENTRIES:
            d = _decision_bar(entry, b=b, start=start, break_high=break_high,
                              high=high, low=low, close=close, opn=opn, a=at, n=n)
            if d is None or d + 1 >= n:
                continue
            ei = d + 1
            fill = opn[ei] * (1.0 - SLIP_BPS / 10_000.0)  # short fill, pessimistic
            if fill <= vwap:
                continue  # already at/below the mean -> no fade left
            run_high = float(high[start:d + 1].max())
            jh = min(ei + HORIZON, n)
            fh = high[ei:jh]; fl = low[ei:jh]
            for sname, buf in STOP_BUFS.items():
                stop = run_high + buf * at
                risk = stop - fill
                if risk <= 0:
                    continue
                up = np.flatnonzero(fh >= stop)
                tg = np.flatnonzero(fl <= vwap)
                ui = up[0] if len(up) else np.iinfo(np.int64).max
                ti = tg[0] if len(tg) else np.iinfo(np.int64).max
                if ui == ti == np.iinfo(np.int64).max:
                    exit_px = close[jh - 1]; reason = "time"
                elif ui <= ti:
                    exit_px = stop; reason = "stop"
                else:
                    exit_px = vwap; reason = "target"
                exit_px *= (1.0 + SLIP_BPS / 10_000.0)  # buy-to-cover, pessimistic
                gross = fill - exit_px
                cost = (fill + exit_px) * (FEE_BPS / 10_000.0)
                net_r = (gross - cost) / risk
                rows.append({
                    "symbol": r.symbol, "variant": r.variant, "tier": r.tier,
                    "session": r.session, "week": r.week, "score_pct": r.score_pct,
                    "stretch": r.stretch_vwap_atr, "entry": entry, "stop": sname,
                    "net_r": net_r, "reason": reason,
                })
    return pd.DataFrame(rows)


def _one(args):
    sym, path_str, shard = args
    try:
        return run_symbol(Path(path_str), pd.read_parquet(shard))
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/events_scored.parquet"))
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/fade_trades.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--min-stretch", type=float, default=0.0, help="only fade breaks stretched >= this ATR over VWAP")
    args = ap.parse_args()

    scored = pd.read_parquet(args.scored)
    scored = scored[scored["break_ts"] < DEV_END_MS]  # DEV only; OOS frozen
    if "tier" not in scored.columns:            # raw events -> derive tier/week
        scored = add_features(scored)
    if "score_pct" not in scored.columns:
        scored["score_pct"] = np.nan
    if args.min_stretch > 0:
        scored = scored[scored["stretch_vwap_atr"] >= args.min_stretch]
    tmp = args.out.parent / "_fade_shards"
    tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in scored.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"
        g.to_parquet(shard)
        tasks.append((sym, str(p), str(shard)))
    print(f"symbols: {len(tasks)}")
    frames = []; done = 0
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
    print(f"wrote {len(trades):,} fade trades -> {args.out}")


if __name__ == "__main__":
    main()
