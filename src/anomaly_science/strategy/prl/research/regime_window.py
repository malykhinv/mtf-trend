"""Does the best ROLLING WINDOW depend on the circumstance? (adaptive H, user)

Compute rolling-PRL books at H in {5,10,15,20,30}; define causal regime variables
(market vol, cross-sectional dispersion, BTC trend, breadth); bucket days by each
regime tercile and report each H-book's annualized return within -> if a different H
wins in different regimes (heterogeneity), an adaptive window can help. Then build a
causal regime-adaptive-H book and compare to the best fixed H, per-year + shuffle.
IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.regime_window
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.overlap import overlapping
from anomaly_science.strategy.prl.research.policy import PRIMARY

HS = [5, 10, 15, 20, 30]


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

    print("building rolling-PRL books ...")
    books = {H: overlapping(close, ret, um, score, p, H=H, hyst=0.5) for H in HS}
    R = pd.DataFrame(books).dropna()

    # causal regime variables (trailing, known before the day)
    mret = ret.where(um).mean(axis=1)
    btc = close["BTCUSDT"] if "BTCUSDT" in close else close.median(axis=1)
    reg = pd.DataFrame({
        "mkt_vol20": mret.rolling(20).std().shift(1),
        "dispersion": ret.where(um).std(axis=1).rolling(5).mean().shift(1),
        "btc_trend20": (btc / btc.shift(20) - 1).shift(1),
        "breadth": (close >= close.rolling(20, min_periods=10).max().shift(1)).mean(axis=1).rolling(5).mean().shift(1),
    }).reindex(R.index)

    def sharpe(s):
        return s.mean() / s.std() * np.sqrt(252) if len(s) > 5 and s.std() > 0 else np.nan

    print("\n=== best rolling window by regime tercile (annualized Sharpe of each H-book) ===")
    for rv in reg.columns:
        q = pd.qcut(reg[rv], 3, labels=["low", "mid", "high"], duplicates="drop")
        print(f"  {rv}:")
        print(f"    {'tercile':8s} " + " ".join(f"H{H:>2}" for H in HS) + "   argmax")
        for t in ["low", "mid", "high"]:
            mask = (q == t)
            shs = [sharpe(R.loc[mask, H]) for H in HS]
            best = HS[int(np.nanargmax(shs))] if np.isfinite(shs).any() else None
            print(f"    {t:8s} " + " ".join(f"{v:>+4.1f}" for v in shs) + f"   -> H={best}")
        print()

    # adaptive-H book: pick H per day by trailing regime, compare vs best fixed
    print("=== adaptive vs fixed (does regime-adaptive H beat the best fixed H?) ===")
    # simple causal rule: high market vol -> short window, low vol -> long window
    v = reg["mkt_vol20"]
    lo, hi = v.quantile(1 / 3), v.quantile(2 / 3)
    pick = pd.Series(15, index=R.index)
    pick[v <= lo] = 30
    pick[v >= hi] = 5
    adaptive = pd.Series([R.loc[d, pick[d]] for d in R.index], index=R.index)
    for name, s in [("fixed H=5", R[5]), ("fixed H=15", R[15]), ("fixed H=20", R[20]),
                    ("ADAPTIVE(vol)", adaptive)]:
        wk = s.resample("W").sum()
        print(f"  {name:16s} CAGR={((1+s).prod()**(252/len(s))-1)*100:+.0f}% Sh={sharpe(s):+.2f} "
              f"+wk={float((wk>0).mean())*100:.0f}%")
    rng = np.random.default_rng(0)
    pick_sh = pd.Series(rng.permutation(pick.values), index=R.index)
    adash = pd.Series([R.loc[d, pick_sh[d]] for d in R.index], index=R.index)
    print(f"  {'ADAPTIVE-shuffled':16s} Sh={sharpe(adash):+.2f}  (adaptive must beat this)")


if __name__ == "__main__":
    main()
