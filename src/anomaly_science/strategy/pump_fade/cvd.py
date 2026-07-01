from __future__ import annotations

import math

import numpy as np


PUMP_FADE_CVD_SCHEMA_VERSION = "pump_fade_cvd_path_v1"
PUMP_FADE_CVD_MODEL_FEATURES: tuple[str, ...] = (
    "cvd_imbalance_5m",
    "cvd_imbalance_15m",
    "cvd_imbalance_5m_minus_event",
    "cvd_acceleration_5m",
    "cvd_drawdown_from_peak",
    "cvd_new_high_confirmation_margin",
    "cvd_path_efficiency",
    "cvd_positive_minute_fraction",
    "price_cvd_divergence_5m",
    "price_up_cvd_down_5m",
    "cvd_failed_to_confirm_high",
)


def build_pump_fade_cvd_features(
    *,
    quote_volume: np.ndarray,
    taker_buy_quote_volume: np.ndarray,
    close: np.ndarray,
) -> dict[str, float | bool]:
    """Build causal CVD path features from closed event minutes only."""

    quote = np.asarray(quote_volume, dtype=float)
    taker = np.asarray(taker_buy_quote_volume, dtype=float)
    prices = np.asarray(close, dtype=float)
    if not (len(quote) == len(taker) == len(prices)) or len(quote) == 0:
        raise ValueError("CVD inputs must have equal positive length")
    valid = (
        np.isfinite(quote).all()
        and np.isfinite(taker).all()
        and np.isfinite(prices).all()
        and np.all(quote >= 0.0)
        and np.all(taker >= 0.0)
        and np.all(taker <= quote + 1e-9)
        and np.all(prices > 0.0)
        and float(np.sum(quote)) > 0.0
    )
    if not valid:
        return {
            **{name: math.nan for name in PUMP_FADE_CVD_MODEL_FEATURES},
            "cvd_available": False,
        }
    signed = 2.0 * taker - quote
    cumulative = np.cumsum(signed)
    event_turnover = float(np.sum(quote))
    event_imbalance = float(cumulative[-1] / event_turnover)
    imbalance_5m = _window_imbalance(signed, quote, 5)
    imbalance_15m = _window_imbalance(signed, quote, 15)
    previous_5m = _previous_window_imbalance(signed, quote, 5)
    return_5m = float(prices[-1] / prices[max(0, len(prices) - 6)] - 1.0)
    previous_peak = float(np.max(cumulative[:-1])) if len(cumulative) > 1 else 0.0
    confirmation_margin = float((cumulative[-1] - previous_peak) / event_turnover)
    absolute_flow = float(np.sum(np.abs(signed)))
    return {
        "cvd_imbalance_5m": imbalance_5m,
        "cvd_imbalance_15m": imbalance_15m,
        "cvd_imbalance_5m_minus_event": imbalance_5m - event_imbalance,
        "cvd_acceleration_5m": (
            imbalance_5m - previous_5m if math.isfinite(previous_5m) else math.nan
        ),
        "cvd_drawdown_from_peak": float(
            (cumulative[-1] - np.max(cumulative)) / event_turnover
        ),
        "cvd_new_high_confirmation_margin": confirmation_margin,
        "cvd_path_efficiency": (
            abs(float(cumulative[-1])) / absolute_flow if absolute_flow > 0.0 else 0.0
        ),
        "cvd_positive_minute_fraction": float(np.mean(signed > 0.0)),
        "price_cvd_divergence_5m": return_5m - imbalance_5m,
        "price_up_cvd_down_5m": bool(return_5m > 0.0 and imbalance_5m < 0.0),
        "cvd_failed_to_confirm_high": bool(confirmation_margin < 0.0),
        "cvd_available": True,
    }


def _window_imbalance(
    signed: np.ndarray,
    quote: np.ndarray,
    window: int,
) -> float:
    start = max(0, len(signed) - window)
    denominator = float(np.sum(quote[start:]))
    return float(np.sum(signed[start:]) / denominator) if denominator > 0.0 else math.nan


def _previous_window_imbalance(
    signed: np.ndarray,
    quote: np.ndarray,
    window: int,
) -> float:
    stop = max(0, len(signed) - window)
    start = max(0, stop - window)
    if stop <= start:
        return math.nan
    denominator = float(np.sum(quote[start:stop]))
    return float(np.sum(signed[start:stop]) / denominator) if denominator > 0.0 else math.nan


__all__ = [
    "PUMP_FADE_CVD_MODEL_FEATURES",
    "PUMP_FADE_CVD_SCHEMA_VERSION",
    "build_pump_fade_cvd_features",
]
