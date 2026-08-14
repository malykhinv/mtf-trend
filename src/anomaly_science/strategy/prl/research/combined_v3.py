"""Combined v3: fully ROLLING (no calendar statics), sweep the rolling window H.

PRL sleeve = overlapping daily tranches (rebalance 1/H of the book every day) for a
grid of holding windows H; breakout sleeve = Donchian daily (already rolling). Report
each rolling-PRL window's Sharpe/weekly/year and the vol-parity combined book, to pick
the best rolling window. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.combined_v3
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve, _stats
from anomaly_science.strategy.prl.research.overlap import overlapping
from anomaly_science.strategy.prl.research.policy import PRIMARY


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    bo = breakout_sleeve(close, ret, um)

    def vp(a, b):
        a, b = a.align(b, join="inner")
        sa, sb = a.std(), b.std(); w = (1 / sa) / (1 / sa + 1 / sb)
        return w * a + (1 - w) * b

    print("=== ROLLING-PRL sleeve by holding window H (overlapping daily tranches) ===")
    rolls = {}
    for H in (5, 7, 10, 15, 20, 30):
        pr = overlapping(close, ret, um, score, p, H=H, hyst=0.5)
        rolls[H] = pr
        _stats(pr.dropna(), f"roll H={H:<2d}")
    print("\n=== combined (rolling-PRL H + breakout, vol-parity) ===")
    for H, pr in rolls.items():
        _stats(vp(pr, bo).dropna(), f"H={H:<2d} + BO")


if __name__ == "__main__":
    main()
