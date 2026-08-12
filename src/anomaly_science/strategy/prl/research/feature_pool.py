"""Wide causal cross-sectional feature pool for the PRL residual ranker (§11, §54).

Every descriptor is built from data <= decision-time t (shift skip), then converted
to a per-date cross-sectional percentile within the point-in-time universe (§54 puts
cross-sectional percentile first). Percentiles are stationary across regimes, so a
tree cannot leak the calendar through a drifting feature level, and per-date-constant
market-context columns are deliberately excluded (they carry no within-date rank info).

Target = per-date percentile rank of the frozen-beta future residual return (§29, §30).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import quality as ql
from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy

_TINY = 1e-9


def _roll(m: pd.DataFrame, w: int, fn: str) -> pd.DataFrame:
    return getattr(m.rolling(w, min_periods=max(3, w // 2)), fn)()


def _selfz(m: pd.DataFrame, w: int) -> pd.DataFrame:
    return (m - _roll(m, w, "mean")) / (_roll(m, w, "std") + _TINY)


def build_descriptors(panel: pd.DataFrame, umask: pd.DataFrame, p: PRLCoarsePolicy) -> dict:
    """Raw causal descriptors (date x symbol), each already shifted by skip."""
    m = fx.build_coarse(panel, umask, p)
    close, ret, eps, label, umask = m["close"], m["ret"], m["eps"], m["label"], m["umask"]
    qv = pn.pivot(panel, "quote_volume").reindex_like(close)
    ntr = pn.pivot(panel, "number_of_trades").reindex_like(close)
    tbq = pn.pivot(panel, "taker_buy_quote_volume").reindex_like(close)
    lp = np.log(close.where(close > 0))
    sk = p.skip
    d: dict[str, pd.DataFrame] = {}

    # --- residual momentum / risk (multi-horizon) ---
    for H in (7, 14, 28, 60):
        d[f"rmom_{H}"] = eps.rolling(H, min_periods=max(3, H // 2)).sum().shift(sk)
    for H in (14, 28):
        rv = eps.rolling(H, min_periods=max(3, H // 2)).std().shift(sk)
        d[f"rvol_{H}"] = rv
        d[f"rsharpe_{H}"] = d[f"rmom_{H}"] / (rv + _TINY)
    # distance of cumulative residual from its own trailing extremes
    ceps = eps.cumsum()
    d["resid_from_high_28"] = (ceps - _roll(ceps, 28, "max")).shift(sk)
    d["resid_from_low_28"] = (ceps - _roll(ceps, 28, "min")).shift(sk)

    # --- quality / persistence (multi-window) ---
    for q_lb in (14, 28, 56):
        qf = ql.quality_features(eps, umask, p, q_lb)
        for name in ("path_eff", "frac_pos", "burst"):
            d[f"{name}_{q_lb}"] = qf[name]
    d["recent_shr_28"] = ql.quality_features(eps, umask, p, 28)["recent_shr"]

    # --- raw momentum / stretch (controls) ---
    for H in (7, 14, 28):
        d[f"rawmom_{H}"] = (lp.shift(sk) - lp.shift(sk + H))
    ema = close.ewm(span=28, min_periods=14).mean()
    d["stretch_28"] = (close / ema - 1.0).shift(sk)
    d["dist_high_28"] = (close / _roll(close, 28, "max") - 1.0).shift(sk)
    d["dist_low_28"] = (close / _roll(close, 28, "min") - 1.0).shift(sk)
    d["dvol_20"] = _roll(ret, 20, "std").shift(sk)

    # --- activity / participation ---
    qv_share = qv.div(qv.where(umask).sum(axis=1) + _TINY, axis=0)
    tr_share = ntr.div(ntr.where(umask).sum(axis=1) + _TINY, axis=0)
    d["qv_share"] = qv_share.shift(sk)
    d["tr_share"] = tr_share.shift(sk)
    d["qv_share_accel"] = (_roll(qv_share, 3, "mean") / (_roll(qv_share, 28, "mean") + _TINY)).shift(sk)
    d["tr_share_accel"] = (_roll(tr_share, 3, "mean") / (_roll(tr_share, 28, "mean") + _TINY)).shift(sk)
    d["vol_accel"] = (_roll(qv, 3, "mean") / (_roll(qv, 28, "mean") + _TINY)).shift(sk)
    d["qv_selfz_30"] = _selfz(qv, 30).shift(sk)
    avg_trade = qv / ntr.replace(0, np.nan)
    d["avg_trade_size"] = avg_trade.shift(sk)
    d["avg_trade_accel"] = (_roll(avg_trade, 3, "mean") / (_roll(avg_trade, 28, "mean") + _TINY)).shift(sk)

    # --- taker flow (aggressor-side proxy, §19) ---
    signed = (2.0 * tbq - qv)  # taker buy minus taker sell
    d["taker_buy_ratio"] = (tbq / (qv + _TINY)).shift(sk)
    d["signed_flow_selfz_20"] = _selfz(signed, 20).shift(sk)
    d["flow_persist_28"] = _roll((signed > 0).astype(float), 28, "mean").shift(sk)
    d["cum_signed_flow_28"] = (_roll(signed, 28, "sum") / (_roll(qv, 28, "sum") + _TINY)).shift(sk)

    return {"descriptors": d, "label": label, "umask": umask, "close": close}


def build_feature_pool(panel: pd.DataFrame, umask: pd.DataFrame, p: PRLCoarsePolicy):
    """Long-form (date, symbol) frame of per-date percentile features + residual
    label + per-date rank target, restricted to in-universe rows with a valid label."""
    b = build_descriptors(panel, umask, p)
    d, label, umask = b["descriptors"], b["label"], b["umask"]

    def rank(mat):
        return mat.where(umask).rank(axis=1, pct=True)

    frames = [rank(mat).stack(future_stack=True).rename(name) for name, mat in d.items()]
    frames.append(label.where(umask).stack(future_stack=True).rename("resid_label"))
    out = pd.concat(frames, axis=1)
    out.index.set_names(["date", "symbol"], inplace=True)
    out = out.reset_index()
    out = out[out["resid_label"].notna()].copy()
    out["y_rank"] = out.groupby("date")["resid_label"].rank(pct=True)
    feats = [c for c in d.keys()]
    return out, feats
