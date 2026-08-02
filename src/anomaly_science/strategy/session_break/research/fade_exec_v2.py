"""Fade-short v2: find WHERE the mean-reversion fade is actually profitable.

Not "poke once and shrug" -- a real search over the levers that matter for a fade:
  * exit is not only "take profit at VWAP": also a structural TRAIL that rides the
    full collapse of a failed pump (the fat right tail a fixed VWAP target throws
    away), and a PARTIAL (half booked at VWAP, half trailed);
  * initial stop = N * ATR above the running high (N swept);
  * every trade tagged by session / tier / stretch bucket / climax so the report
    can locate the profitable regime instead of pooling everything to a mush.

Shorts reuse the frozen core ``simulate_long_path`` via price negation (a short is
a long on -price; highs<->lows swap), so no bespoke short-path loop is written.
Look-ahead safe: next-open fill, causal decision bars, horizon-sliced forward
window, stop checked before target intrabar (pessimistic for the short).

Cost note: under negation the 5 bps/side slippage sign is favourable; fees (10
bps/side, symmetric) dominate. Winners are stress-tested with extra cost in the
report, so the search is not fooled by ~10 bps of slippage optimism.
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
from anomaly_science.strategy.session_break.research.portrait import DEV_END_MS, add_features

import os

# IMPORTANT: costs are ZEROED here on purpose. This engine simulates SHORTS by
# negating prices and reusing the long-only core; the core applies slippage in the
# LONG direction, which under negation becomes FAVOURABLE for the short (a real
# bug that once faked a +0.29R "edge"). So we take GROSS R from the sim (correct
# -1R stop floor) and apply the true round-trip cost in R-space afterwards, using
# the per-trade risk fraction -- sign-correct and cost-swept in the report.
SPEC = PumpLongExecutionSpec(
    stop_trigger_close_beyond=(os.environ.get("SB_FADE_INTRABAR") != "1"),
    entry_slippage_bps=0.0, exit_slippage_bps=0.0, fee_per_side_bps=0.0)
TRAIL_ATR = 1.0
HORIZON = int(os.environ.get("SB_FADE_HORIZON", "480"))
N_STOPS = tuple(float(x) for x in os.environ.get("SB_FADE_NSTOPS", "1.0,2.0,3.0").split(","))
ENTRIES = tuple(os.environ.get("SB_FADE_ENTRIES", "break,stall").split(","))
MODES = tuple(os.environ.get("SB_FADE_MODES", "vwap,trail,partial").split(","))
CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")


def _atr30(high, low):
    rh = pd.Series(high).rolling(30, min_periods=30).max()
    rl = pd.Series(low).rolling(30, min_periods=30).min()
    return (rh - rl).to_numpy()


def _short(o, h, l, c, atr, stop_abs, tp_abs, mode):
    """One short's net_r via a long on negated prices (highs<->lows swap)."""
    no, nh, nl, nc = -o, -l, -h, -c
    nstop = -stop_abs
    if mode == "bracket":  # fixed stop + VWAP TP, NO trail (isolates the trail's effect)
        r = simulate_long_path(open_=no, high=nh, low=nl, close=nc, entry_index=1,
                               initial_stop_price=nstop, spec=SPEC,
                               take_profit_price=(-tp_abs if tp_abs else None), atr=atr)
        return r.net_r if r.status == "filled" else np.nan
    if mode == "vwap":
        r = simulate_long_path(open_=no, high=nh, low=nl, close=nc, entry_index=1,
                               initial_stop_price=nstop, spec=SPEC,
                               take_profit_price=(-tp_abs if tp_abs else None),
                               swing_reversal_atr=TRAIL_ATR, atr=atr)
        return r.net_r if r.status == "filled" else np.nan
    if mode == "trail":
        r = simulate_long_path(open_=no, high=nh, low=nl, close=nc, entry_index=1,
                               initial_stop_price=nstop, spec=SPEC,
                               swing_reversal_atr=TRAIL_ATR, atr=atr)
        return r.net_r if r.status == "filled" else np.nan
    # partial: half booked at VWAP, half trailed to ride the collapse
    a = simulate_long_path(open_=no, high=nh, low=nl, close=nc, entry_index=1,
                           initial_stop_price=nstop, spec=SPEC,
                           take_profit_price=(-tp_abs if tp_abs else None),
                           swing_reversal_atr=TRAIL_ATR, atr=atr)
    b = simulate_long_path(open_=no, high=nh, low=nl, close=nc, entry_index=1,
                           initial_stop_price=nstop, spec=SPEC,
                           swing_reversal_atr=TRAIL_ATR, atr=atr)
    if a.status != "filled" or b.status != "filled":
        return np.nan
    return 0.5 * a.net_r + 0.5 * b.net_r


def _decision_bar(entry, *, b, start, break_high, high, low, close, opn, a, n):
    if entry == "break":
        return b
    if entry == "stall":  # first red bar off a fresh high = exhaustion
        peak = break_high; made = False
        for j in range(b, min(b + 120, n)):
            if high[j] >= peak:
                peak = high[j]; made = True
            elif made and close[j] < opn[j]:
                return j
        return None
    return None


def run_symbol(path: Path, ev: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    ts = df["timestamp"].to_numpy(np.int64)
    opn = df["open"].to_numpy(float); high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float); close = df["close"].to_numpy(float)
    atrpm = causal_atr(high=high, low=low, close=close, window=ATR_WINDOW)
    atr30 = _atr30(high, low)
    n = len(df); idx = {int(t): i for i, t in enumerate(ts)}
    rows = []
    for r in ev.itertuples():
        b = idx.get(int(r.break_ts))
        if b is None or not np.isfinite(r.stretch_vwap_atr):
            continue
        at = atr30[b]; apm = atrpm[b]
        if not (np.isfinite(at) and at > 0 and apm > 0):
            continue
        start = b - int(r.bars_to_break)
        vwap = float(r.close_break) - float(r.stretch_vwap_atr) * apm
        break_high = float(high[start:b + 1].max())
        for entry in ENTRIES:
            d = _decision_bar(entry, b=b, start=start, break_high=break_high,
                              high=high, low=low, close=close, opn=opn, a=at, n=n)
            if d is None or d + 2 >= n:
                continue
            ei = d + 1
            fill = opn[ei]
            if fill <= vwap:
                continue
            run_high = float(high[start:d + 1].max())
            we = min(ei + HORIZON, n)
            o, h, l, c, aw = opn[d:we], high[d:we], low[d:we], close[d:we], atr30[d:we]
            for N in N_STOPS:
                stop = run_high + N * at
                if stop <= fill:
                    continue
                for mode in MODES:
                    tp = vwap if mode != "trail" else None
                    nr = _short(o, h, l, c, aw, stop, tp, mode)
                    if not np.isfinite(nr):
                        continue
                    rows.append({
                        "symbol": r.symbol, "variant": r.variant, "tier": r.tier,
                        "session": r.session, "week": r.week, "score_pct": r.score_pct,
                        "stretch": float(r.stretch_vwap_atr), "climax": float(r.climax),
                        "block_vol_share": float(r.block_vol_share), "oi_change": float(r.oi_change),
                        "entry": entry, "stop_n": N, "mode": mode,
                        "gross_r": nr,                       # sign-correct GROSS R (-1 stop floor)
                        "risk_frac": (stop - fill) / fill,   # to charge round-trip cost in R-space
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
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/events_scored_rev.parquet"))
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/fade_v2.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--min-stretch", type=float, default=0.0)
    args = ap.parse_args()
    sc = pd.read_parquet(args.scored)
    sc = sc[sc["break_ts"] < DEV_END_MS]
    if "tier" not in sc.columns:
        sc = add_features(sc)
    if "score_pct" not in sc.columns:
        sc["score_pct"] = np.nan
    if args.min_stretch > 0:
        sc = sc[sc["stretch_vwap_atr"] >= args.min_stretch]
    tmp = args.out.parent / "_fv2_shards"; tmp.mkdir(parents=True, exist_ok=True)
    tasks = []
    for sym, g in sc.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        shard = tmp / f"{sym}.parquet"; g.to_parquet(shard)
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
    print(f"wrote {len(trades):,} trades -> {args.out}")


if __name__ == "__main__":
    main()
