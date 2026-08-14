"""Combined v4: regime-ADAPTIVE rolling window (causal thresholds) + breakout sleeve.

PRL sleeve picks its rolling window per day from trailing market vol -- short window
(H=5) in high vol, long (H=30) in low vol, H=15 in between -- with CAUSAL expanding-
quantile thresholds (no full-sample look-ahead). Combined vol-parity with the breakout
sleeve. Reports the adaptive sleeve, the combined book, per-year/weekly/Sharpe, and a
shuffle control (permuted window picks). IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.combined_v4
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

    print("building rolling-PRL books (5/15/30) ...")
    R = pd.DataFrame({H: overlapping(close, ret, um, score, p, H=H, hyst=0.5) for H in (5, 15, 30)}).dropna()
    bo = breakout_sleeve(close, ret, um)

    # causal market-vol regime + expanding-quantile thresholds (no look-ahead)
    mvol = ret.where(um).mean(axis=1).rolling(20).std().shift(1).reindex(R.index)
    lo = mvol.expanding(min_periods=90).quantile(1 / 3).shift(1)
    hi = mvol.expanding(min_periods=90).quantile(2 / 3).shift(1)
    Hpick = pd.Series(15, index=R.index)
    Hpick[mvol <= lo] = 30
    Hpick[mvol >= hi] = 5
    Hpick[mvol.isna() | lo.isna()] = 15
    adaptive = pd.Series([R.loc[d, int(Hpick[d])] for d in R.index], index=R.index)

    def vp(a, b):
        a, b = a.align(b, join="inner")
        sa, sb = a.std(), b.std(); w = (1 / sa) / (1 / sa + 1 / sb)
        return w * a + (1 - w) * b

    print("\n=== sleeves ===")
    _stats(R[15].dropna(), "PRL roll fixed H=15")
    _stats(adaptive.dropna(), "PRL ADAPTIVE (causal)")
    _stats(bo.dropna(), "Breakout trend")
    rng = np.random.default_rng(0)
    Hsh = pd.Series(rng.permutation(Hpick.values), index=R.index)
    adash = pd.Series([R.loc[d, int(Hsh[d])] for d in R.index], index=R.index)
    _stats(adash.dropna(), "PRL adaptive-SHUFFLED")

    print("\n=== combined vol-parity (adaptive-PRL + breakout) ===")
    _stats(vp(R[15], bo).dropna(), "fixedH15 + BO")
    _stats(vp(adaptive, bo).dropna(), "ADAPTIVE + BO")
    print(f"\n  corr(adaptive-PRL, BO) = {adaptive.align(bo, join='inner')[0].corr(adaptive.align(bo, join='inner')[1]):+.2f}"
          if False else "")


if __name__ == "__main__":
    main()
