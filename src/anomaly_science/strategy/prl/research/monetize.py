"""Turn the real-but-uneven residual signal into a YEAR-UNIFORM edge (§32.3, §44, §45).

Diagnosis so far: the OOF rank-IC is positive every year (2023/24/25), but the naive
decile long-short monetizes it only in 2025 — a low-vol tilt that wins in chop/bear and
loses in the momentum bull. This module tests the two direct fixes:

  1. rank-WEIGHTED dollar-neutral factor return (whole cross-section) instead of decile
     tails — monetizes the IC faithfully, so if IC is positive every year the factor
     return should be too;
  2. cross-sectional NEUTRALIZATION of the score vs volatility / beta / size — removes the
     low-vol bet so the edge is idiosyncratic leadership, not a vol factor.

Plus a leg autopsy (long vs short, per year) so we see WHICH side breaks when.

Run: python -m anomaly_science.strategy.prl.research.monetize
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import feature_pool as fpm
from anomaly_science.strategy.prl.research.policy import PRIMARY as P
from anomaly_science.strategy.prl.research.run_coarse import _warmup

OUT = Path(".output/results/prl_coarse")


def _xs_rank(mat: pd.DataFrame, umask: pd.DataFrame) -> pd.DataFrame:
    return mat.where(umask).rank(axis=1, pct=True)


def neutralize(score: pd.DataFrame, controls: list[pd.DataFrame], umask: pd.DataFrame) -> pd.DataFrame:
    """Per-date residual of `score` regressed on `controls` (+intercept), re-ranked.
    Removes the part of the ranking explained by the control factors."""
    idx = score.index
    cols = score.columns
    out = pd.DataFrame(np.nan, index=idx, columns=cols)
    ctrl_arr = [c.reindex(index=idx, columns=cols).to_numpy() for c in controls]
    S = score.where(umask).to_numpy()
    M = umask.to_numpy()
    for i in range(len(idx)):
        y = S[i]
        rows = M[i] & np.isfinite(y)
        for ca in ctrl_arr:
            rows &= np.isfinite(ca[i])
        if rows.sum() < 20:
            continue
        X = np.column_stack([np.ones(rows.sum())] + [ca[i][rows] for ca in ctrl_arr])
        yy = y[rows]
        beta, *_ = np.linalg.lstsq(X, yy, rcond=None)
        out.iloc[i, np.where(rows)[0]] = yy - X @ beta
    return _xs_rank(out, umask)


def _reb_dates(idx, warmup, step):
    return list(range(warmup, len(idx) - step - 1, step))


def rank_weighted_factor(score: pd.DataFrame, label: pd.DataFrame, umask: pd.DataFrame,
                         p, warmup: int) -> pd.Series:
    """Dollar-neutral rank-weighted factor return per non-overlapping rebalance:
    w_i = (pct_rank_i - 0.5), normalized to gross 1; ret = sum w_i * residual_label_i."""
    idx = score.index
    out = {}
    for ri in _reb_dates(idx, warmup, p.rebalance_days):
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        l = label.iloc[ri].reindex(s.index).dropna()
        s = s.reindex(l.index)
        if len(l) < max(p.min_xs, 15):
            continue
        r = s.rank(pct=True)
        w = r - r.mean()               # exact dollar-neutral (sum w = 0)
        w = w / w.abs().sum()
        out[idx[ri]] = float((w * l).sum())
    return pd.Series(out).sort_index()


def decile_ls(score, label, umask, p, warmup, q=0.1):
    idx = score.index
    out = {}
    for ri in _reb_dates(idx, warmup, p.rebalance_days):
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        l = label.iloc[ri].reindex(s.index).dropna()
        s = s.reindex(l.index)
        if len(l) < max(p.min_xs, 15):
            continue
        k = max(1, int(len(s) * q))
        order = s.sort_values(ascending=False)
        out[idx[ri]] = float(l.reindex(order.index[:k]).mean() - l.reindex(order.index[-k:]).mean())
    return pd.Series(out).sort_index()


def leg_means(score, label, umask, p, warmup, q=0.1):
    """Per-rebalance long-leg and short-leg residual (relative to the cross-section mean)."""
    idx = score.index
    longs, shorts = {}, {}
    for ri in _reb_dates(idx, warmup, p.rebalance_days):
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        l = label.iloc[ri].reindex(s.index).dropna()
        s = s.reindex(l.index)
        if len(l) < max(p.min_xs, 15):
            continue
        k = max(1, int(len(s) * q))
        order = s.sort_values(ascending=False)
        mkt = l.mean()
        longs[idx[ri]] = float(l.reindex(order.index[:k]).mean() - mkt)
        shorts[idx[ri]] = float(mkt - l.reindex(order.index[-k:]).mean())  # short profits if laggards < mkt
    return pd.Series(longs).sort_index(), pd.Series(shorts).sort_index()


def realized_rank_weighted(score, close, open_, umask, p, warmup, cost_mult=1.0) -> pd.Series:
    """Tradeable dollar-neutral rank-weighted book on RAW open-to-open returns, net of
    realistic turnover cost (Σ|Δw| between rebalances × per-side bps). Dollar-neutral
    weights approximately hedge the market without an explicit hedge leg."""
    idx = score.index
    side_bps = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * p.cost_multiplier * cost_mult
    prev_w = pd.Series(dtype=float)
    out = {}
    for ri in _reb_dates(idx, warmup, p.rebalance_days):
        ei, xi = ri + 1, 1 + ri + p.rebalance_days
        if xi >= len(idx):
            break
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        o_in = open_.iloc[ei].reindex(s.index)
        o_out = open_.iloc[xi].reindex(s.index)
        ok = (o_in > 0) & (o_out > 0) & o_in.notna() & o_out.notna()
        s = s[ok]
        if len(s) < max(p.min_xs, 15):
            continue
        r = s.rank(pct=True)
        w = r - r.mean()               # exact dollar-neutral (sum w = 0)
        w = w / w.abs().sum()
        fwd = (o_out[ok] / o_in[ok] - 1.0)
        gross = float((w * fwd).sum())
        turn = float((w.subtract(prev_w, fill_value=0.0)).abs().sum())
        out[idx[ri]] = gross - turn * side_bps / 1e4
        prev_w = w
    return pd.Series(out).sort_index()


def per_year(ret: pd.Series, hold: int) -> str:
    ret = ret.copy(); ret.index = pd.to_datetime(ret.index)
    cells = []
    for yr, g in ret.groupby(ret.index.year):
        tot = g.sum() * 100
        sh = g.mean() / g.std() * np.sqrt(252 / hold) if g.std() > 0 else np.nan
        mo = g.resample("ME").sum()
        cells.append(f"{yr}:{tot:+.1f}%(Sh{sh:+.1f},mo>0 {float((mo>0).mean()):.2f})")
    allsh = ret.mean() / ret.std() * np.sqrt(252 / hold) if ret.std() > 0 else np.nan
    return f"ALL Sh{allsh:+.2f}  " + "  ".join(cells)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    oos = pd.Timestamp(P.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, P.universe_n, P.liquidity_lb, P.min_age_days)
    b = fpm.build_descriptors(panel, umask, P)
    d, label, umask, close = b["descriptors"], b["label"], b["umask"], b["close"]
    m = fx.build_coarse(panel, umask, P)
    warmup = _warmup(P)

    # OOF linear score -> matrix
    oof = pd.read_parquet(OUT / "oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex(
        index=close.index, columns=close.columns)

    # controls (as cross-sectional ranks)
    c_vol = _xs_rank(d["rvol_28"], umask)
    c_beta = _xs_rank(m["beta"], umask)
    c_size = _xs_rank(np.log(qv.reindex_like(close).rolling(30, min_periods=10).median() + 1.0), umask)

    hold = P.rebalance_days
    print(f"=== MONETIZE (hold={hold}d, non-overlap) — signal = OOF linear ===\n")

    print("[leg autopsy: long-leg and short-leg residual vs cross-section mean]")
    lg, sh = leg_means(score, label, umask, P, warmup)
    print(f"  LONG  {per_year(lg, hold)}")
    print(f"  SHORT {per_year(sh, hold)}")

    print("\n[construction comparison, per year]")
    print(f"  decile LS (raw)      {per_year(decile_ls(score, label, umask, P, warmup), hold)}")
    print(f"  rank-weighted (raw)  {per_year(rank_weighted_factor(score, label, umask, P, warmup), hold)}")

    sc_v = neutralize(score, [c_vol], umask)
    sc_vbs = neutralize(score, [c_vol, c_beta, c_size], umask)
    print(f"  rank-w NEUTRAL vol   {per_year(rank_weighted_factor(sc_v, label, umask, P, warmup), hold)}")
    print(f"  rank-w NEUTRAL v+b+s {per_year(rank_weighted_factor(sc_vbs, label, umask, P, warmup), hold)}")
    print(f"  decile NEUTRAL v+b+s {per_year(decile_ls(sc_vbs, label, umask, P, warmup), hold)}")

    open_ = pn.pivot(panel, "open").reindex_like(close)
    print("\n[REALIZED tradeable rank-weighted book (raw open-to-open, net of turnover cost)]")
    for tag, sc in [("raw", score), ("neutral vol", sc_v), ("neutral v+b+s", sc_vbs)]:
        for cm in (1.0, 2.0, 3.0):
            r = realized_rank_weighted(sc, close, open_, umask, P, warmup, cost_mult=cm)
            print(f"  {tag:14s} cost x{cm:.0f}  {per_year(r, hold)}")

    pd.DataFrame({
        "raw": rank_weighted_factor(score, label, umask, P, warmup),
        "neutral_vol": rank_weighted_factor(sc_v, label, umask, P, warmup),
        "realized_raw_x1": realized_rank_weighted(score, close, open_, umask, P, warmup, 1.0),
        "realized_neutralvol_x2": realized_rank_weighted(sc_v, close, open_, umask, P, warmup, 2.0),
    }).to_parquet(OUT / "monetize_factor_returns.parquet")
    print(f"\nwrote {OUT}/monetize_factor_returns.parquet")


if __name__ == "__main__":
    main()
