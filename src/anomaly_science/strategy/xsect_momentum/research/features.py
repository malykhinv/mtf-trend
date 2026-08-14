"""Leak-free coin-day feature pool for the forward-return-rank study (§8).

Design driver (user, 2026-08-04): a momentum "leader" may just be a manipulative
pump — a sleeper that suddenly rips to pull retail into FOMO, then dumps. That is
junk. Raw momentum cannot tell a clean trend from a pump-and-dump, which is likely
*why* raw momentum failed. So the pool deliberately includes an **anti-pump /
quality** family (volume-spike concentration, verticality, stretch, trade-size mix,
OI support) alongside momentum, all computed strictly from data <= day t, with the
target measured on the t+1 entry forward window.

All features use only completed-bar data available at the decision (day t). The
target is the forward return over the hold horizon, entered at t+1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.policy import XSectMomentumPolicy


def _roll(mat: pd.DataFrame, win: int, fn: str) -> pd.DataFrame:
    r = mat.rolling(win, min_periods=max(3, win // 2))
    return getattr(r, fn)()


def build_features(panel: pd.DataFrame, p: XSectMomentumPolicy, horizon: int) -> pd.DataFrame:
    """Return a long-form (date, symbol) frame of features + forward target.

    horizon: forward hold length in days (entered at t+1).
    """
    close = pn.pivot(panel, "close")
    open_ = pn.pivot(panel, "open")
    qv = pn.pivot(panel, "quote_volume")
    ntr = pn.pivot(panel, "number_of_trades")

    lp = np.log(close.where(close > 0))
    ret = close.pct_change(fill_method=None)

    L = p.short_lb
    feats: dict[str, pd.DataFrame] = {}

    # --- momentum baseline (the thing we're testing to beat) ---
    feats["mom_short"] = lp.shift(p.skip) - lp.shift(p.skip + p.short_lb)
    feats["mom_long"] = lp.shift(p.skip) - lp.shift(p.skip + p.long_lb)
    feats["mom_1w"] = lp.shift(p.skip) - lp.shift(p.skip + 7)

    # --- anti-pump / quality family ---
    # verticality: share of the short-lb gain delivered by the single best day
    up = ret.clip(lower=0)
    feats["verticality"] = _roll(up, L, "max") / (_roll(up, L, "sum") + 1e-9)
    feats["updays_share"] = _roll((ret > 0).astype(float), L, "mean")
    # stretch: how far price is extended above its own trailing mean (FOMO extension)
    ema = close.ewm(span=L, min_periods=L // 2).mean()
    feats["stretch"] = (close / ema - 1.0).shift(p.skip)
    # volume-spike concentration inside the day (manipulation signature)
    for col, name in [
        ("intraday_quote_volume_hhi", "hhi"),
        ("intraday_max_minute_volume_share", "maxminshare"),
        ("intraday_max_absolute_return", "maxabsret"),
        ("intraday_return_skew", "iskew"),
        ("intraday_return_kurtosis", "ikurt"),
        ("intraday_positive_minute_share", "posmin"),
        ("intraday_taker_imbalance_mean", "takerimb"),
        ("intraday_taker_imbalance_std", "takerimb_std"),
        ("intraday_realized_variance", "rvar"),
        ("intraday_upside_semivariance", "usemi"),
        ("intraday_downside_semivariance", "dsemi"),
        ("oi_price_change_correlation_1m", "oi_pxcorr"),
        ("oi_intraday_change_volatility", "oi_chgvol"),
        ("last_hour_volume_share", "lasthr_vshare"),
        ("first_hour_volume_share", "firsthr_vshare"),
    ]:
        if col in panel.columns:
            m = pn.pivot(panel, col)
            feats[f"{name}_mean"] = _roll(m, L, "mean").shift(p.skip)
            feats[f"{name}_max"] = _roll(m, L, "max").shift(p.skip)

    # trade-size mix: avg notional per trade — whales vs retail FOMO frenzy
    avg_trade = (qv / ntr.replace(0, np.nan))
    feats["avg_trade_size_mean"] = _roll(avg_trade, L, "mean").shift(p.skip)
    feats["avg_trade_size_last"] = avg_trade.shift(p.skip)
    # volume acceleration: recent vs baseline (pump ignition)
    feats["vol_accel"] = (_roll(qv, 3, "mean") / (_roll(qv, L, "mean") + 1e-9)).shift(p.skip)
    # OI support over the lookback: is positioning backing the move?
    if "open_interest_mean" in panel.columns:
        oi = pn.pivot(panel, "open_interest_mean")
        feats["oi_chg_lb"] = (oi / oi.shift(L) - 1.0).shift(p.skip)

    # --- context / normalization ---
    feats["log_dollar_vol"] = np.log(_roll(qv, p.liquidity_lb, "median") + 1.0).shift(p.skip)
    feats["daily_vol"] = _roll(ret, p.vol_lb, "std").shift(p.skip)

    # --- forward target: enter t+1, hold `horizon` days (leak-free) ---
    fwd = (close.shift(-(1 + horizon)) / close.shift(-1) - 1.0)

    # stack to long form
    frames = []
    for name, mat in feats.items():
        s = mat.stack(dropna=False).rename(name)
        frames.append(s)
    target = fwd.stack(dropna=False).rename("fwd_ret")
    frames.append(target)
    out = pd.concat(frames, axis=1)
    out.index.set_names(["date", "symbol"], inplace=True)
    out = out.reset_index()
    return out


FEATURE_COLS_EXCLUDE = {"date", "symbol", "fwd_ret", "in_universe"}


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in FEATURE_COLS_EXCLUDE]
