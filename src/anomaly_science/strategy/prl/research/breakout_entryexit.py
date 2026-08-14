"""Wide ENTRY x EXIT grid for the 1h breakout trade, state-conditioned (user).

We do not run every trade on one template. For each 20d-high breakout we simulate a
grid of entry rules (immediate / hold-confirm / retest / reclaim / volume-confirmed /
two-green) x exit rules (time / stop+time / ATR-target / trail / volume-death /
aggression-against / level-break / EMA-break), report per-trade R expectancy & win,
per year, + a BLIND (random-entry) control. Then split by setup STATE (reclaim speed,
volume surge, breadth, coin vol) to see which exit suits which setup.

Long thesis, risk = 1.5 ATR, costs included. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.breakout_entryexit --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, MAXH, RISK_ATR = 20, 72, 1.5
COST = (4 + 2 + 1) / 1e4


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def entry_bar(rule, c, lv, vs0, ema):
    """Return entry offset (>=1) into the window or -1 to skip."""
    if rule == "IMM":
        return 1
    if rule == "HOLD4":
        return 4 if len(c) > 4 and c[4] >= lv else -1
    if rule == "VOLCONF":
        return 1 if vs0 >= 1.5 else -1
    if rule == "2GREEN":
        for j in range(1, min(len(c) - 1, 12)):
            if c[j] >= lv and c[j] > c[j - 1] and c[j - 1] > c[max(j - 2, 0)]:
                return j
        return -1
    if rule == "RETEST":                       # pull back to level then hold
        for j in range(1, min(len(c), 13)):
            if c[j] <= lv * 1.003 and c[j] >= lv * 0.99:
                return j
        return -1
    if rule == "RECLAIM":                        # dip under level then reclaim
        under = False
        for j in range(1, min(len(c), 25)):
            if c[j] < lv:
                under = True
            elif under and c[j] >= lv:
                return j
        return -1
    return 1


def exit_px(rule, c, h, l, v, sf, ema, e, atr_i, lv):
    """Return exit price given entry offset e."""
    entry = c[e]; stop = entry - RISK_ATR * atr_i
    end = min(len(c), e + 48)
    if rule == "TIME48":
        return c[end - 1]
    if rule == "STOPTIME":
        for j in range(e + 1, end):
            if l[j] <= stop:
                return stop
        return c[end - 1]
    if rule == "TARGET":
        tgt = entry + 2 * RISK_ATR * atr_i
        for j in range(e + 1, end):
            if l[j] <= stop:
                return stop
            if h[j] >= tgt:
                return tgt
        return c[end - 1]
    if rule == "TRAIL":
        rmax = h[e]
        for j in range(e + 1, end):
            rmax = max(rmax, h[j]); tr = rmax - 3 * atr_i
            if l[j] <= max(stop, tr):
                return max(stop, tr)
        return c[end - 1]
    if rule == "VOLDEATH":
        base = v[max(e - 3, 0):e + 1].mean()
        for j in range(e + 1, end):
            if l[j] <= stop:
                return stop
            if v[max(j - 5, e):j + 1].mean() < 0.5 * base:
                return c[j]
        return c[end - 1]
    if rule == "AGGR":                           # taker flow turns negative
        for j in range(e + 1, end):
            if l[j] <= stop:
                return stop
            if sf[j] < 0 and sf[j - 1] < 0:
                return c[j]
        return c[end - 1]
    if rule == "LEVELBREAK":
        for j in range(e + 1, end):
            if c[j] < lv:
                return c[j]
        return c[end - 1]
    if rule == "EMABREAK":
        for j in range(e + 1, end):
            if c[j] < ema[j]:
                return c[j]
        return c[end - 1]
    return c[end - 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda col: panel.pivot(index="date", columns="symbol", values=col)
    close, high, low, qv, tbq = (px(c) for c in ("close", "high", "low", "quote_volume", "taker_buy_quote_volume"))
    idx, cols = close.index, close.columns
    dhigh = high.resample("1D").max()
    level = dhigh.rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    ema = close.ewm(span=24, min_periods=12).mean()
    vs = (qv / (_roll(qv, 168, "mean") + 1e-9)).to_numpy()
    sf = (2 * tbq - qv).to_numpy()
    breadth = above.mean(axis=1).to_numpy()
    C, Hh, Ll, V, EMA, A, LVn = (x.to_numpy() for x in (close, high, low, qv, ema, atr, level))
    bmask = brk.to_numpy()

    ENTRIES = ["IMM", "HOLD4", "VOLCONF", "2GREEN", "RETEST", "RECLAIM"]
    EXITS = ["TIME48", "STOPTIME", "TARGET", "TRAIL", "VOLDEATH", "AGGR", "LEVELBREAK", "EMABREAK"]
    rng = np.random.default_rng(0)

    # collect per-event windows once
    events = []
    for si in range(len(cols)):
        for i in np.where(bmask[:, si])[0]:
            if i + MAXH >= len(idx) or not (A[i, si] > 0):
                continue
            sl = slice(i, i + MAXH)
            events.append((idx[i], si, C[sl, si], Hh[sl, si], Ll[sl, si], V[sl, si], sf[sl, si],
                           EMA[sl, si], A[i, si], LVn[i, si], vs[i, si], breadth[i]))
    print(f"events={len(events)}\n")

    def trade_R(en, ex, blind=False):
        rs, yrs = [], []
        for (dt, si, c, h, l, v, s, em, atr_i, lv, vs0, br) in events:
            e = rng.integers(1, 5) if blind else entry_bar(en, c, lv, vs0, em)
            if e < 1 or e >= len(c) - 1 or not (c[e] > 0):
                continue
            xp = exit_px(ex, c, h, l, v, s, em, e, atr_i, lv)
            R = (xp / c[e] - 1 - 2 * COST) / (RISK_ATR * atr_i / c[e])
            rs.append(R); yrs.append(dt.year)
        rs = np.array(rs); yrs = np.array(yrs)
        return rs, yrs

    print("=== per-trade mean R by ENTRY x EXIT (long, risk 1.5ATR, net) ===")
    print("  entry    " + " ".join(f"{x[:7]:>7}" for x in EXITS))
    best = []
    for en in ENTRIES:
        line = []
        for ex in EXITS:
            rs, _ = trade_R(en, ex)
            mR = rs.mean() if len(rs) else np.nan
            line.append(mR); best.append((en, ex, mR, len(rs)))
        print(f"  {en:8s} " + " ".join(f"{v:+7.2f}" for v in line))
    b = sorted([x for x in best if np.isfinite(x[2])], key=lambda z: -z[2])[:5]
    print("\n  top cells (meanR):")
    for en, ex, mR, n in b:
        rs, yrs = trade_R(en, ex)
        yr = " ".join(f"{y}:{rs[yrs==y].mean():+.2f}" for y in sorted(set(yrs)))
        rb, _ = trade_R(en, ex, blind=True)
        print(f"    {en:8s} {ex:10s} meanR={mR:+.2f} win={(rs>0).mean():.2f} n={n}  blind={rb.mean():+.2f}  by-year[{yr}]")


if __name__ == "__main__":
    main()
