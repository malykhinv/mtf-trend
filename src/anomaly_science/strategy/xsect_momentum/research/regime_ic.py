"""Per-year and per-regime forward-rank IC across the multi-regime IS (2023-2025).

The 7-month study could only see a bear. Here we ask the questions that decide the
long side and the adaptive logic:
  * Does the quality / anti-pump signal hold sign in BULL years, or is it a purely
    bearish mean-reversion effect?
  * Does raw momentum revive in trending/bull regimes (vs ~0 on the bear)?
  * Which features are regime-robust (same sign everywhere) vs regime-flipping?

Regime per date = sign of BTC trend over regime_lb with a neutral band.
IS only (env XSM_IS_END). Never touches OOS.

Run (multi-regime panel):
  XSM_PANEL_GLOB=.../daily_klines_v1/panel.parquet XSM_IS_END=2026-01-01 \
    python -m anomaly_science.strategy.xsect_momentum.research.regime_ic
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import features as ft
from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.catboost_rank import rank_ic
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY as P

OUT = Path(".output/results/xsect_momentum")
HORIZON = 7
KEY_FEATURES = ["mom_long", "mom_short", "daily_vol", "avg_trade_size_last",
                "verticality", "stretch", "vol_accel", "rvar_mean", "maxabsret_max",
                "hhi_mean", "posmin_mean", "takerimb_mean"]


def btc_regime(panel: pd.DataFrame, lb: int = 50, band: float = 0.05) -> pd.Series:
    close = pn.pivot(panel, "close")
    base = close["BTCUSDT"] if "BTCUSDT" in close.columns else close.median(axis=1)
    lp = np.log(base.where(base > 0))
    trend = lp.shift(1) - lp.shift(1 + lb)
    st = pd.Series("side", index=close.index)
    st[trend > band] = "bull"
    st[trend < -band] = "bear"
    return st


def ic_of(df: pd.DataFrame, col: str) -> float:
    ic, _, _ = rank_ic(df.rename(columns={col: "pred"})[["date", "pred", "fwd_ret"]], "pred")
    return ic


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = pn.load_panel(is_only=True)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, P.universe_n, P.liquidity_lb, P.min_age_days)
    reg = btc_regime(panel)

    df = ft.build_features(panel, P, HORIZON)
    um = umask.stack().rename("u").reset_index(); um.columns = ["date", "symbol", "u"]
    df = df.merge(um, on=["date", "symbol"], how="left")
    df = df[df["u"].fillna(False) & df["fwd_ret"].notna()].copy()
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None)
    df["year"] = df["date"].dt.year
    reg.index = pd.to_datetime(reg.index, utc=True).tz_convert(None)
    df["regime"] = df["date"].map(reg.to_dict())
    print(f"rows={len(df):,}  dates={df['date'].nunique()}  "
          f"{df['date'].min().date()}->{df['date'].max().date()}")
    print("regime day counts:", df.drop_duplicates('date')['regime'].value_counts().to_dict())
    print("year day counts:  ", df.drop_duplicates('date')['year'].value_counts().sort_index().to_dict())

    feats = [c for c in KEY_FEATURES if c in df.columns]
    years = sorted(df["year"].unique())
    regimes = ["bull", "side", "bear"]

    print("\n=== forward-rank IC by YEAR ===")
    print(f"{'feature':20s} {'ALL':>7} " + " ".join(f"{y:>7}" for y in years))
    rows = []
    for c in feats:
        allic = ic_of(df, c)
        yv = {y: ic_of(df[df['year'] == y], c) for y in years}
        rows.append({"feature": c, "IC_all": allic, **{f"y{y}": yv[y] for y in years}})
        print(f"{c:20s} {allic:+7.3f} " + " ".join(f"{yv[y]:+7.3f}" for y in years))

    print("\n=== forward-rank IC by REGIME ===")
    print(f"{'feature':20s} {'ALL':>7} " + " ".join(f"{r:>7}" for r in regimes))
    for c in feats:
        allic = ic_of(df, c)
        rv = {r: ic_of(df[df['regime'] == r], c) for r in regimes}
        print(f"{c:20s} {allic:+7.3f} " + " ".join(f"{rv[r]:+7.3f}" for r in regimes))

    pd.DataFrame(rows).to_parquet(OUT / "regime_ic.parquet")
    print(f"\nwrote {OUT / 'regime_ic.parquet'}")


if __name__ == "__main__":
    main()
