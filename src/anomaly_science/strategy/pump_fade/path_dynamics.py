from __future__ import annotations

import math

import numpy as np


PUMP_FADE_PATH_DYNAMICS_SCHEMA_VERSION = "pump_fade_path_dynamics_v1"

PUMP_FADE_PATH_DYNAMICS_FEATURES: tuple[str, ...] = (
    "event_return_volatility",
    "event_downside_path_share",
    "event_return_sign_change_rate",
    "event_return_sign_entropy",
    "event_return_autocorrelation_1",
    "recent_return_3m",
    "recent_return_10m",
    "recent_red_fraction_3m",
    "recent_red_fraction_5m",
    "recent_down_body_share_5m",
    "recent_range_change_vs_prior_5m",
    "recent_quote_activity_change_vs_prior_5m",
    "recent_trade_activity_change_vs_prior_5m",
    "recent_taker_imbalance_5m",
    "prior_taker_imbalance_5m",
    "taker_imbalance_change_5m",
    "price_impact_asymmetry",
    "pre_realized_volatility_60m",
    "pre_volatility_ratio_60m_240m",
    "pre_range_ratio_60m_240m",
    "pre_path_efficiency_60m",
)


def build_path_dynamics_features(
    *,
    event_open: np.ndarray,
    event_high: np.ndarray,
    event_low: np.ndarray,
    event_close: np.ndarray,
    event_quote_volume: np.ndarray,
    event_trade_count: np.ndarray,
    event_taker_buy_quote: np.ndarray,
    pre_open: np.ndarray,
    pre_high: np.ndarray,
    pre_low: np.ndarray,
    pre_close: np.ndarray,
) -> dict[str, float]:
    """Build bounded causal path coordinates from closed candles only."""

    closes = np.asarray(event_close, dtype=float)
    opens = np.asarray(event_open, dtype=float)
    highs = np.asarray(event_high, dtype=float)
    lows = np.asarray(event_low, dtype=float)
    quote = np.asarray(event_quote_volume, dtype=float)
    trades = np.asarray(event_trade_count, dtype=float)
    taker_buy = np.asarray(event_taker_buy_quote, dtype=float)
    if not (
        len(closes)
        == len(opens)
        == len(highs)
        == len(lows)
        == len(quote)
        == len(trades)
        == len(taker_buy)
    ) or len(closes) == 0:
        raise ValueError("path-dynamics event arrays must be non-empty and aligned")

    previous = np.concatenate((opens[:1], closes[:-1]))
    returns = closes / np.maximum(previous, 1e-12) - 1.0
    positive_path = float(np.sum(np.maximum(returns, 0.0)))
    downside_path = float(np.sum(np.maximum(-returns, 0.0)))
    total_path = positive_path + downside_path
    signs = np.sign(returns)
    nonzero_signs = signs[signs != 0.0]
    sign_change_rate = (
        float(np.mean(nonzero_signs[1:] != nonzero_signs[:-1]))
        if len(nonzero_signs) > 1
        else 0.0
    )
    positive_fraction = (
        float(np.mean(nonzero_signs > 0.0)) if len(nonzero_signs) else 0.5
    )
    sign_entropy = _binary_entropy(positive_fraction)
    return_autocorrelation = _lag_one_correlation(returns)

    recent_quote = _tail_sum(quote, 5)
    prior_quote = _prior_sum(quote, 5)
    recent_trades = _tail_sum(trades, 5)
    prior_trades = _prior_sum(trades, 5)
    recent_imbalance = _taker_imbalance(quote[-5:], taker_buy[-5:])
    prior_imbalance = _taker_imbalance(quote[-10:-5], taker_buy[-10:-5])
    candle_ranges = (highs - lows) / np.maximum(opens, 1e-12)
    recent_range = _tail_mean(candle_ranges, 5)
    prior_range = _prior_mean(candle_ranges, 5)
    bodies = np.abs(closes - opens)
    down_bodies = np.maximum(opens - closes, 0.0)

    aggressive_buy = float(np.nansum(taker_buy))
    aggressive_sell = max(float(np.nansum(quote)) - aggressive_buy, 0.0)
    upside_impact = positive_path / max(aggressive_buy, 1e-12)
    downside_impact = downside_path / max(aggressive_sell, 1e-12)

    pre = _preconditioning_features(
        open_=np.asarray(pre_open, dtype=float),
        high=np.asarray(pre_high, dtype=float),
        low=np.asarray(pre_low, dtype=float),
        close=np.asarray(pre_close, dtype=float),
    )
    return {
        "event_return_volatility": float(np.std(returns)),
        "event_downside_path_share": downside_path / max(total_path, 1e-12),
        "event_return_sign_change_rate": sign_change_rate,
        "event_return_sign_entropy": sign_entropy,
        "event_return_autocorrelation_1": return_autocorrelation,
        "recent_return_3m": _window_return(closes, opens, 3),
        "recent_return_10m": _window_return(closes, opens, 10),
        "recent_red_fraction_3m": float(np.mean(closes[-3:] < opens[-3:])),
        "recent_red_fraction_5m": float(np.mean(closes[-5:] < opens[-5:])),
        "recent_down_body_share_5m": float(np.sum(down_bodies[-5:]))
        / max(float(np.sum(bodies[-5:])), 1e-12),
        "recent_range_change_vs_prior_5m": _relative_change(recent_range, prior_range),
        "recent_quote_activity_change_vs_prior_5m": _relative_change(
            recent_quote, prior_quote
        ),
        "recent_trade_activity_change_vs_prior_5m": _relative_change(
            recent_trades, prior_trades
        ),
        "recent_taker_imbalance_5m": recent_imbalance,
        "prior_taker_imbalance_5m": prior_imbalance,
        "taker_imbalance_change_5m": recent_imbalance - prior_imbalance,
        "price_impact_asymmetry": (downside_impact - upside_impact)
        / max(downside_impact + upside_impact, 1e-18),
        **pre,
    }


def _preconditioning_features(
    *, open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> dict[str, float]:
    if not (len(open_) == len(high) == len(low) == len(close)):
        raise ValueError("path-dynamics pre-event arrays must be aligned")
    if len(close) < 2:
        return {
            "pre_realized_volatility_60m": float("nan"),
            "pre_volatility_ratio_60m_240m": float("nan"),
            "pre_range_ratio_60m_240m": float("nan"),
            "pre_path_efficiency_60m": float("nan"),
        }
    previous = np.concatenate((open_[:1], close[:-1]))
    returns = close / np.maximum(previous, 1e-12) - 1.0
    returns_60 = returns[-60:]
    returns_240 = returns[-240:]
    volatility_60 = float(np.std(returns_60))
    volatility_240 = float(np.std(returns_240))
    ranges = (high - low) / np.maximum(open_, 1e-12)
    range_60 = float(np.mean(ranges[-60:]))
    range_240 = float(np.mean(ranges[-240:]))
    close_60 = close[-60:]
    open_60 = open_[-60:]
    net = abs(float(close_60[-1] - open_60[0]))
    path = float(
        np.sum(
            np.abs(
                np.diff(np.concatenate((open_60[:1], close_60)))
            )
        )
    )
    return {
        "pre_realized_volatility_60m": volatility_60,
        "pre_volatility_ratio_60m_240m": volatility_60
        / max(volatility_240, 1e-12),
        "pre_range_ratio_60m_240m": range_60 / max(range_240, 1e-12),
        "pre_path_efficiency_60m": net / max(path, 1e-12),
    }


def _window_return(close: np.ndarray, open_: np.ndarray, length: int) -> float:
    start = max(len(close) - length, 0)
    return float(close[-1] / max(float(open_[start]), 1e-12) - 1.0)


def _tail_sum(values: np.ndarray, length: int) -> float:
    return float(np.nansum(values[-length:]))


def _prior_sum(values: np.ndarray, length: int) -> float:
    prior = values[-2 * length : -length]
    return float(np.nansum(prior)) if len(prior) else _tail_sum(values, length)


def _tail_mean(values: np.ndarray, length: int) -> float:
    return float(np.nanmean(values[-length:]))


def _prior_mean(values: np.ndarray, length: int) -> float:
    prior = values[-2 * length : -length]
    return float(np.nanmean(prior)) if len(prior) else _tail_mean(values, length)


def _taker_imbalance(quote: np.ndarray, taker_buy: np.ndarray) -> float:
    total = float(np.nansum(quote))
    if total <= 0.0:
        return 0.0
    return 2.0 * float(np.nansum(taker_buy)) / total - 1.0


def _relative_change(current: float, previous: float) -> float:
    if abs(previous) <= 1e-12:
        return 0.0
    return current / previous - 1.0


def _binary_entropy(positive_fraction: float) -> float:
    probability = min(max(positive_fraction, 0.0), 1.0)
    if probability in {0.0, 1.0}:
        return 0.0
    return -(
        probability * math.log2(probability)
        + (1.0 - probability) * math.log2(1.0 - probability)
    )


def _lag_one_correlation(values: np.ndarray) -> float:
    if len(values) < 3 or float(np.std(values[:-1])) <= 1e-12 or float(np.std(values[1:])) <= 1e-12:
        return 0.0
    return float(np.corrcoef(values[:-1], values[1:])[0, 1])


__all__ = [
    "PUMP_FADE_PATH_DYNAMICS_FEATURES",
    "PUMP_FADE_PATH_DYNAMICS_SCHEMA_VERSION",
    "build_path_dynamics_features",
]
