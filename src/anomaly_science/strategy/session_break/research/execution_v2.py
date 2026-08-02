"""Long-continuation execution v2: N*ATR stops, structural trail, partial TP.

Reuses the frozen core ``simulate_long_path`` (no new sim loop). Improvements the
user asked for:
  * initial stop = **N * ATR** (ATR-weighted), swept over N -- not a structural low;
  * a structural ZigZag swing trail rides the move in every mode;
  * exit modes: trail-only (no TP), full TP, and **partial** -- half the position
    scales out at the TP, the other half keeps trailing with the stop armed to
    breakeven once the TP is reached. The partial is assembled from TWO core calls
    (no bespoke partial-exit loop): half A = sim with take_profit; half B = sim
    with breakeven_arm_return at the TP level and no take_profit; R = mean of the two
    (identical entry & initial stop, so R is on the same risk denominator).

ATR unit = the 30-min range (a session-scale ATR; the per-minute ATR is microscopic
and would make N*ATR stops smaller than costs). Entry = next-1m open after the break
(the exit study fixes entry; the entry grid lives in execution.py). Look-ahead safe:
forward arrays are sliced to the horizon; the core checks stop before TP intrabar.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr, simulate_long_path
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW

SPEC = PumpLongExecutionSpec()
TRAIL_ATR = 1.0
HORIZON = 480
N_STOPS = (1.5, 2.5, 4.0)      # initial stop = N * 30-min-range ATR
TP_MULTS = (2.0, 4.0)          # take-profit = M * 30-min-range ATR above entry
CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")


def _atr30(high, low):
    rh = pd.Series(high).rolling(30, min_periods=30).max()
    rl = pd.Series(low).rolling(30, min_periods=30).min()
    return (rh - rl).to_numpy()


def _run_one(open_, high, low, close, atr, stop_price, tp_price, mode):
    """One position's net_r for a given exit mode (entry_index=1 => fill next open)."""
    if mode == "trail":
        r = simulate_long_path(open_=open_, high=high, low=low, close=close,
                               entry_index=1, initial_stop_price=stop_price, spec=SPEC,
                               swing_reversal_atr=TRAIL_ATR, atr=atr)
        return r.net_r if r.status == "filled" else np.nan
    if mode == "full_tp":
        r = simulate_long_path(open_=open_, high=high, low=low, close=close,
                               entry_index=1, initial_stop_price=stop_price, spec=SPEC,
                               take_profit_price=tp_price, swing_reversal_atr=TRAIL_ATR, atr=atr)
        return r.net_r if r.status == "filled" else np.nan
    # partial: half scales out at TP, half trails with breakeven arm once TP reached
    entry_open = float(open_[1])
    arm = tp_price / entry_open - 1.0 if entry_open > 0 else None
    a = simulate_long_path(open_=open_, high=high, low=low, close=close, entry_index=1,
                           initial_stop_price=stop_price, spec=SPEC,
                           take_profit_price=tp_price, swing_reversal_atr=TRAIL_ATR, atr=atr)
    b = simulate_long_path(open_=open_, high=high, low=low, close=close, entry_index=1,
                           initial_stop_price=stop_price, spec=SPEC,
                           breakeven_arm_return=arm, swing_reversal_atr=TRAIL_ATR, atr=atr)
    if a.status != "filled" or b.status != "filled":
        return np.nan
    return 0.5 * a.net_r + 0.5 * b.net_r


def run_symbol(path: Path, ev: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"].to_numpy(np.int64)
    opn = df["open"].to_numpy(float); high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float); close = df["close"].to_numpy(float)
    atr30 = _atr30(high, low)
    n = len(df); idx = {int(t): i for i, t in enumerate(ts)}
    rows = []
    for r in ev.itertuples():
        b = idx.get(int(r.break_ts))
        if b is None or b + 2 >= n:
            continue
        at = atr30[b]
        if not (np.isfinite(at) and at > 0):
            continue
        d = b; ei = d + 1; we = min(ei + HORIZON, n)
        o, h, l, cl, atrw = opn[d:we], high[d:we], low[d:we], close[d:we], atr30[d:we]
        entry_ref = close[d]
        for N in N_STOPS:
            stop_price = entry_ref - N * at
            for mode in ("trail", "full_tp", "partial"):
                tps = TP_MULTS if mode != "trail" else (None,)
                for M in tps:
                    tp_price = entry_ref + M * at if M is not None else None
                    nr = _run_one(o, h, l, cl, atrw, stop_price, tp_price, mode)
                    if not np.isfinite(nr):
                        continue
                    rows.append({
                        "symbol": r.symbol, "variant": r.variant, "tier": r.tier,
                        "week": r.week, "score_pct": r.score_pct,
                        "stop_n": N, "mode": mode, "tp_m": (M if M is not None else 0.0),
                        "net_r": nr,
                    })
    return pd.DataFrame(rows)


def _one(args):
    sym, p, shard = args
    try:
        return run_symbol(Path(p), pd.read_parquet(shard))
    except Exception:
        return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/events_scored.parquet"))
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/trades_v2.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--top-decile-only", action="store_true")
    args = ap.parse_args()

    sc = pd.read_parquet(args.scored)
    if "break_ts" in sc.columns:
        from anomaly_science.strategy.session_break.research.portrait import DEV_END_MS
        sc = sc[sc["break_ts"] < DEV_END_MS]
    if "score_pct" not in sc.columns:
        sc["score_pct"] = np.nan
    if args.top_decile_only:
        sc = sc[sc["score_pct"] >= 0.90]
    tmp = args.out.parent / "_v2_shards"; tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in sc.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"; g.to_parquet(shard)
        tasks.append((sym, str(p), str(shard)))
    print(f"symbols: {len(tasks)}  top_decile_only={args.top_decile_only}")
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
    print(f"wrote {len(trades):,} trades -> {args.out}")


if __name__ == "__main__":
    main()
