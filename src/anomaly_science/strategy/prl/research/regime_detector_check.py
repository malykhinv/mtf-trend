"""Validate the ONLINE (causal) BTC regime detector: is it correct and TIMELY vs hindsight?

We label BTC daily regime causally (trailing trend + vol, known day-before) and compare to a
CENTERED hindsight label (uses future -> reference ONLY, to measure lag). Report: agreement %,
median flip-lag at major turns, whipsaw count, and a chart of BTC log-price shaded by the causal
regime with hindsight turn markers -- so the timeline can be eyeballed against world events
(2020 COVID crash, 2021 bull + Nov top, 2022 bear + FTX bottom, 2023 recovery, 2024 bull).
Also a faster detector variant to show the speed/stability trade-off. IS only.
Run: python -m anomaly_science.strategy.prl.research.regime_detector_check
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research.policy import PRIMARY


def trend_label(btc, rlb, malb, thr, shift_causal):
    r = btc / btc.shift(rlb) - 1 if shift_causal else btc.shift(-rlb // 2) / btc.shift(rlb // 2) - 1
    ma = btc.rolling(malb, min_periods=malb // 2, center=not shift_causal).mean()
    lab = pd.Series("side", index=btc.index)
    lab[(r > thr) & (btc > ma)] = "bull"
    lab[(r < -thr) & (btc < ma)] = "bear"
    return (lab.shift(1) if shift_causal else lab)


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    btc = pn.pivot(panel, "close")["BTCUSDT"].dropna()
    btc.index = pd.to_datetime(btc.index).tz_localize(None)

    causal = trend_label(btc, 60, 50, 0.12, True)          # the online detector
    fast = trend_label(btc, 30, 30, 0.10, True)            # faster variant
    hind = trend_label(btc, 60, 60, 0.12, False)           # centered hindsight reference

    both = pd.concat([causal.rename("c"), fast.rename("f"), hind.rename("h")], axis=1).dropna()
    print(f"=== agreement with hindsight reference ({len(both)} days) ===")
    print(f"  causal(r60/ma50): {(both.c==both.h).mean()*100:.0f}% agree   fast(r30/ma30): {(both.f==both.h).mean()*100:.0f}% agree")

    # flip-lag: for each hindsight regime CHANGE, days until causal matches
    hchg = both.index[both.h.ne(both.h.shift(1)).fillna(False)]
    lags = []
    for t in hchg:
        newr = both.h.loc[t]
        after = both.loc[t:]
        match = after.index[after.c == newr]
        if len(match):
            lags.append((match[0] - t).days)
    print(f"  median flip-lag at hindsight turns: {int(np.median(lags)) if lags else 'na'} days  (n turns={len(hchg)})")
    cwhip = int(both.c.ne(both.c.shift(1)).sum()); hwhip = int(both.h.ne(both.h.shift(1)).sum())
    print(f"  regime flips: causal {cwhip}  vs hindsight {hwhip}  (excess = whipsaw)")

    # causal regime periods table
    print("\n=== causal regime periods (BTC return during) ===")
    seg = (causal != causal.shift(1)).cumsum()
    for _, g in causal.dropna().groupby(seg):
        if len(g) < 5:
            continue
        a, b = g.index[0], g.index[-1]
        r = btc.loc[b] / btc.loc[a] - 1
        print(f"  {a.date()} -> {b.date()}  {g.iloc[0]:5s}  {len(g):4d}d  BTC {r*100:+.0f}%")

    # chart
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(13, 5.5))
        ax.semilogy(btc.index, btc.values, color="black", lw=1.0, zorder=5)
        col = {"bull": "#c8e6c9", "bear": "#ffcdd2", "side": "#eeeeee"}
        c = causal.reindex(btc.index).ffill()
        seg2 = (c != c.shift(1)).cumsum()
        for _, g in c.dropna().groupby(seg2):
            ax.axvspan(g.index[0], g.index[-1], color=col.get(g.iloc[0], "#fff"), alpha=0.9, zorder=0)
        for t in hchg:
            ax.axvline(t, color="#1565c0", lw=0.7, ls=":", zorder=3)
        ax.set_title("BTC (log) shaded by CAUSAL regime  (green=bull red=bear grey=side; blue dots=hindsight turns)")
        ax.set_ylabel("BTC $ (log)")
        out = ".output/results/prl_coarse/regime_timeline.png"
        fig.tight_layout(); fig.savefig(out, dpi=110); print(f"\nchart -> {out}")
    except Exception as e:
        print("chart skipped:", e)


if __name__ == "__main__":
    main()
