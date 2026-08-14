"""Combined book v2: ROLLING PRL (overlapping daily tranches) + breakout-trend sleeve.

The v1 PRL sleeve rebalanced every 15 CALENDAR days (single phase -> lumpy, few
bets/week -> the ~58% weekly ceiling). Here PRL is made ROLLING (average of 15
daily-offset tranches = rebalance 1/15 of the book daily), added to the daily-rolling
breakout sleeve. Question: does rolling + the second sleeve lift WEEKLY consistency
while keeping the year-uniformity? IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.combined_v2
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book, _stats as _st  # noqa
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

    prl_static, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    prl_roll = overlapping(close, ret, um, score, p, H=15, hyst=0.5)   # rolling daily tranches
    bo = breakout_sleeve(close, ret, um)

    j = pd.concat([prl_static.rename("ps"), prl_roll.rename("pr"), bo.rename("bo")], axis=1).dropna()
    ps, pr, bo = j["ps"], j["pr"], j["bo"]

    print("=== sleeves ===")
    _stats(ps, "PRL static (15d cal)")
    _stats(pr, "PRL ROLLING (daily)")
    _stats(bo, "Breakout trend")
    print(f"\n  corr(PRL_roll, BO) = {pr.corr(bo):+.2f}")

    print("\n=== combined (vol-parity) ===")
    def vp(a, b):
        sa, sb = a.std(), b.std(); w = (1 / sa) / (1 / sa + 1 / sb)
        return w * a + (1 - w) * b
    _stats(vp(ps, bo), "STATIC-PRL + BO")
    _stats(vp(pr, bo), "ROLLING-PRL + BO")


if __name__ == "__main__":
    main()
