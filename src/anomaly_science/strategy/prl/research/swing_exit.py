"""Does a 1h swing-trailing exit beat a fixed hold? (user lever)

Entry = the daily book's long/short candidates at each rebalance. For each name we
simulate two exits over a max hold:
  * FIXED  : hold the full horizon, exit at the close.
  * TRAILED: ratcheting 1h Donchian stop (long: stop = cummax of rolling-min low;
             exit on close < stop; short mirrored) -> let winners run, cut losers.
Outcomes are market-relative (minus BTC over each trade's actual window, so an early
trailed exit is compared fairly). We report win-rate and mean for both, long & short.

1h data (klines_1h) covers the full IS. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.swing_exit --n 60 --hold 15 --N 12
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.anatomy import _load
from anomaly_science.strategy.prl.research.run_coarse import _warmup

H1_DIR = ".output/market/binance_vision/um_futures/klines_1h"
OUT = Path(".output/results/prl_coarse")


def _load_1h(sym, start, end):
    f = f"{H1_DIR}/{sym}.parquet"
    if not os.path.exists(f):
        return None
    df = pd.read_parquet(f, columns=["timestamp", "open", "high", "low", "close"])
    df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df[(df.index >= start) & (df.index < end)].sort_index()
    return df if len(df) > 5 else None


def _trail_exit(bars, side, N, max_bars):
    """Return market-unadjusted trade return under a ratcheting 1h swing stop and
    the fixed full-hold return; both entered at the first bar's open."""
    b = bars.iloc[:max_bars]
    if len(b) < 3:
        return None
    o = float(b["open"].iloc[0])
    if not (o > 0):
        return None
    if side > 0:
        stop = b["low"].rolling(N, min_periods=1).min().cummax()
        hit = b["close"] < stop.shift(1)
    else:
        stop = b["high"].rolling(N, min_periods=1).max().cummin()
        hit = b["close"] > stop.shift(1)
    idx = np.where(hit.to_numpy())[0]
    xi = int(idx[0]) if len(idx) else len(b) - 1
    px_trail = float(b["close"].iloc[xi]); px_fixed = float(b["close"].iloc[-1])
    sgn = 1.0 if side > 0 else -1.0
    return {"trail": sgn * (px_trail / o - 1.0), "fixed": sgn * (px_fixed / o - 1.0),
            "t_enter": b.index[0], "t_exit": b.index[xi], "t_end": b.index[-1]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--hold", type=int, default=15)
    ap.add_argument("--N", type=int, default=12, help="1h swing window (bars)")
    args = ap.parse_args()

    p, close, ret, um, score = _load(args.n)
    start, end = close.index.min(), close.index.max() + pd.Timedelta(days=args.hold + 2)
    syms = [c for c in close.columns if bool(um[c].any())]  # only ever-in-universe symbols
    print(f"loading 1h for {len(syms)} symbols ...")
    h1 = {s: _load_1h(s, start, end) for s in syms}
    h1 = {s: v for s, v in h1.items() if v is not None}
    btc = _load_1h("BTCUSDT", start, end)
    max_bars = args.hold * 24

    rows = []
    idx = close.index
    for ri in range(_warmup(p), len(idx) - args.hold - 1, args.hold):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < 30:
            continue
        order = s.sort_values(ascending=False)
        k = max(1, int(len(order) * 0.1))
        t0 = idx[ri] + pd.Timedelta(days=1)
        for side, names in ((+1, order.index[:k]), (-1, order.index[-k:])):
            for sym in names:
                d = h1.get(sym)
                if d is None:
                    continue
                bars = d[d.index >= t0]
                r = _trail_exit(bars, side, args.N, max_bars)
                if r is None:
                    continue
                # market-relative: subtract BTC over each trade's actual window
                def mkt(a, b_):
                    seg = btc[(btc.index >= a) & (btc.index <= b_)]
                    return (seg["close"].iloc[-1] / seg["open"].iloc[0] - 1.0) if len(seg) > 1 else 0.0
                m_trail = mkt(r["t_enter"], r["t_exit"]); m_fix = mkt(r["t_enter"], r["t_end"])
                sgn = 1.0 if side > 0 else -1.0
                rows.append({"date": idx[ri], "symbol": sym, "side": side,
                             "trail_rel": r["trail"] - sgn * m_trail,
                             "fixed_rel": r["fixed"] - sgn * m_fix})
    tr = pd.DataFrame(rows)
    print(f"trades: {len(tr)}  (hold={args.hold}d, swing N={args.N} 1h-bars)\n")
    for side, nm in ((+1, "LONG"), (-1, "SHORT")):
        g = tr[tr.side == side]
        if not len(g):
            continue
        print(f"  {nm}:  FIXED  win={ (g.fixed_rel>0).mean():.3f}  mean={g.fixed_rel.mean()*100:+.2f}%  med={g.fixed_rel.median()*100:+.2f}%")
        print(f"        TRAIL  win={ (g.trail_rel>0).mean():.3f}  mean={g.trail_rel.mean()*100:+.2f}%  med={g.trail_rel.median()*100:+.2f}%")
    both = tr.groupby("date")[["fixed_rel", "trail_rel"]].mean()
    print(f"\n  per-rebalance mean rel: fixed {both.fixed_rel.mean()*100:+.2f}%  trail {both.trail_rel.mean()*100:+.2f}%")
    tr.to_parquet(OUT / "swing_exit_trades.parquet")
    print(f"  wrote {OUT}/swing_exit_trades.parquet")


if __name__ == "__main__":
    main()
