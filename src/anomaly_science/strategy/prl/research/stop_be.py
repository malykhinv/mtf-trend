"""Initial stop + move-to-breakeven on the MAIN (daily) book's positions (user Q).

The daily book currently holds to a fixed horizon with no per-name stops. Here we
test adding, per position, an initial ATR stop and optionally moving it to breakeven
once the trade is in profit -- separately for the long and short legs -- using 1h
bars for stop detection, market-relative to BTC over each trade's actual window.

We compare: FIXED (no stop) / STOP (initial ATR stop) / STOP+BE (move to entry after
+be_atr*ATR). Full trailing was already shown to kill the short leg (swing_exit.py);
this asks whether the gentler breakeven helps. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.stop_be --n 60 --hold 15
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.anatomy import _load
from anomaly_science.strategy.prl.research.run_coarse import _warmup
from anomaly_science.strategy.prl.research.swing_exit import _load_1h


def _exits(bars, side, sl_mult, be_atr, max_bars, atr_win=24):
    b = bars.iloc[:max_bars]
    if len(b) < atr_win + 2:
        return None
    o = float(b["open"].iloc[0])
    if not (o > 0):
        return None
    pc = b["close"].shift(1)
    tr = pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1)
    a = float(tr.iloc[:atr_win].mean())
    if not (a > 0):
        return None
    risk = sl_mult * a
    hi = b["high"].to_numpy(); lo = b["low"].to_numpy(); cl = b["close"].to_numpy()
    px_fixed = float(cl[-1])

    def run_stop(move_be):
        stop = o - side * risk
        be_done = False
        for k in range(1, len(b)):
            if not be_done and move_be and side * (cl[k] - o) >= be_atr * a:
                stop = o; be_done = True
            if (side < 0 and hi[k] >= stop) or (side > 0 and lo[k] <= stop):
                return float(stop)
        return px_fixed

    sgn = 1.0 if side > 0 else -1.0
    return {"fixed": sgn * (px_fixed / o - 1.0),
            "stop": sgn * (run_stop(False) / o - 1.0),
            "stop_be": sgn * (run_stop(True) / o - 1.0),
            "t0": b.index[0], "t1": b.index[-1]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--hold", type=int, default=15)
    ap.add_argument("--sl", type=float, default=1.5, help="initial stop in ATR")
    ap.add_argument("--be", type=float, default=0.75, help="breakeven trigger in ATR")
    args = ap.parse_args()

    p, close, ret, um, score = _load(args.n)
    start, end = close.index.min(), close.index.max() + pd.Timedelta(days=args.hold + 2)
    syms = [c for c in close.columns if bool(um[c].any())]
    print(f"loading 1h for {len(syms)} symbols ...")
    h1 = {s: _load_1h(s, start, end) for s in syms}
    h1 = {s: v for s, v in h1.items() if v is not None}
    btc = _load_1h("BTCUSDT", start, end)
    max_bars = args.hold * 24
    idx = close.index
    rows = []
    for ri in range(_warmup(p), len(idx) - args.hold - 1, args.hold):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < 30:
            continue
        order = s.sort_values(ascending=False); k = max(1, int(len(order) * 0.1))
        t0 = idx[ri] + pd.Timedelta(days=1)
        for side, names in ((+1, order.index[:k]), (-1, order.index[-k:])):
            for sym in names:
                d = h1.get(sym)
                if d is None:
                    continue
                r = _exits(d[d.index >= t0], side, args.sl, args.be, max_bars)
                if r is None:
                    continue
                seg = btc[(btc.index >= r["t0"]) & (btc.index <= r["t1"])]
                m = (seg["close"].iloc[-1] / seg["open"].iloc[0] - 1.0) if len(seg) > 1 else 0.0
                sgn = 1.0 if side > 0 else -1.0
                rows.append({"side": side, "fixed": r["fixed"] - sgn * m,
                             "stop": r["stop"] - sgn * m, "stop_be": r["stop_be"] - sgn * m})
    tr = pd.DataFrame(rows)
    print(f"trades: {len(tr)}  (hold={args.hold}d, init stop={args.sl}ATR, BE trigger={args.be}ATR)\n")
    for side, nm in ((+1, "LONG"), (-1, "SHORT"), (0, "BOTH (per-trade avg)")):
        g = tr if side == 0 else tr[tr.side == side]
        if not len(g):
            continue
        print(f"  {nm}")
        for col in ("fixed", "stop", "stop_be"):
            print(f"    {col:8s} win={ (g[col]>0).mean():.3f}  mean={g[col].mean()*100:+.3f}%  med={g[col].median()*100:+.3f}%")


if __name__ == "__main__":
    main()
