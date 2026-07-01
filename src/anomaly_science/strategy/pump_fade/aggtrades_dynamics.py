from __future__ import annotations

import numpy as np

PUMP_FADE_AGGTRADES_DYNAMICS_SCHEMA_VERSION = "pump_fade_aggtrades_dynamics_v1"

PUMP_FADE_AGGTRADES_DYNAMICS_FEATURES: tuple[str, ...] = (
    "aggtrades_available",
    "event_mean_trade_notional_p90",
    "event_mean_notional_gini",
    "event_mean_same_side_run",
    "event_mean_side_sign_entropy",
    "recent_trade_notional_p90_3m",
    "recent_notional_gini_5m",
    "prior_notional_gini_5m",
    "notional_gini_change_5m",
    "recent_flip_rate_5m",
    "prior_flip_rate_5m",
    "flip_rate_change_5m",
    "recent_same_side_run_5m",
    "prior_same_side_run_5m",
    "same_side_run_change_5m",
    "recent_side_sign_entropy_5m",
    "prior_side_sign_entropy_5m",
    "recent_buy_impact_5m",
    "prior_buy_impact_5m",
    "buy_impact_decay_5m",
    "recent_sell_impact_5m",
    "prior_sell_impact_5m",
    "sell_impact_change_5m",
    "recent_inter_arrival_ms_5m",
    "prior_inter_arrival_ms_5m",
    "inter_arrival_ms_change_5m",
    "pre_trade_notional_p90_60m",
    "pre_side_sign_entropy_60m",
    "pre_inter_arrival_ms_mean_60m",
    "pre_buy_impact_per_notional_60m",
    "pre_sell_impact_per_notional_60m",
)


def build_aggtrades_dynamics_features(
    *,
    event_trade_notional_p90: np.ndarray,
    event_notional_gini: np.ndarray,
    event_same_side_run_mean: np.ndarray,
    event_side_sign_entropy: np.ndarray,
    event_side_flip_rate: np.ndarray,
    event_inter_arrival_ms_mean: np.ndarray,
    event_buy_impact_per_notional: np.ndarray,
    event_sell_impact_per_notional: np.ndarray,
    event_aggtrades_trade_count: np.ndarray,
    pre_trade_notional_p90: np.ndarray,
    pre_side_sign_entropy: np.ndarray,
    pre_inter_arrival_ms_mean: np.ndarray,
    pre_buy_impact_per_notional: np.ndarray,
    pre_sell_impact_per_notional: np.ndarray,
) -> dict[str, float]:
    """Build bounded causal aggTrades coordinates from per-minute sidecar features.

    All inputs are already-aligned per-minute arrays sliced the same way as
    `path_dynamics.build_path_dynamics_features` (event window, pre-ignition
    lookback). Missing sidecar data (symbol never backfilled, or a zero-trade
    minute) arrives as NaN and must stay NaN in the output, not be coerced to 0.
    """

    trade_count = np.asarray(event_aggtrades_trade_count, dtype=float)
    if trade_count.size == 0 or not np.any(np.isfinite(trade_count)):
        return _empty_result()

    notional_p90 = np.asarray(event_trade_notional_p90, dtype=float)
    gini = np.asarray(event_notional_gini, dtype=float)
    run_mean = np.asarray(event_same_side_run_mean, dtype=float)
    sign_entropy = np.asarray(event_side_sign_entropy, dtype=float)
    flip_rate = np.asarray(event_side_flip_rate, dtype=float)
    inter_arrival = np.asarray(event_inter_arrival_ms_mean, dtype=float)
    buy_impact = np.asarray(event_buy_impact_per_notional, dtype=float)
    sell_impact = np.asarray(event_sell_impact_per_notional, dtype=float)

    recent_gini = _tail_nanmean(gini, 5)
    prior_gini = _prior_nanmean(gini, 5)
    recent_flip = _tail_nanmean(flip_rate, 5)
    prior_flip = _prior_nanmean(flip_rate, 5)
    recent_run = _tail_nanmean(run_mean, 5)
    prior_run = _prior_nanmean(run_mean, 5)
    recent_entropy = _tail_nanmean(sign_entropy, 5)
    prior_entropy = _prior_nanmean(sign_entropy, 5)
    recent_buy_impact = _tail_nanmean(buy_impact, 5)
    prior_buy_impact = _prior_nanmean(buy_impact, 5)
    recent_sell_impact = _tail_nanmean(sell_impact, 5)
    prior_sell_impact = _prior_nanmean(sell_impact, 5)
    recent_inter_arrival = _tail_nanmean(inter_arrival, 5)
    prior_inter_arrival = _prior_nanmean(inter_arrival, 5)

    return {
        "aggtrades_available": 1.0,
        "event_mean_trade_notional_p90": _safe_nanmean(notional_p90),
        "event_mean_notional_gini": _safe_nanmean(gini),
        "event_mean_same_side_run": _safe_nanmean(run_mean),
        "event_mean_side_sign_entropy": _safe_nanmean(sign_entropy),
        "recent_trade_notional_p90_3m": _tail_nanmean(notional_p90, 3),
        "recent_notional_gini_5m": recent_gini,
        "prior_notional_gini_5m": prior_gini,
        "notional_gini_change_5m": recent_gini - prior_gini,
        "recent_flip_rate_5m": recent_flip,
        "prior_flip_rate_5m": prior_flip,
        "flip_rate_change_5m": recent_flip - prior_flip,
        "recent_same_side_run_5m": recent_run,
        "prior_same_side_run_5m": prior_run,
        "same_side_run_change_5m": recent_run - prior_run,
        "recent_side_sign_entropy_5m": recent_entropy,
        "prior_side_sign_entropy_5m": prior_entropy,
        "recent_buy_impact_5m": recent_buy_impact,
        "prior_buy_impact_5m": prior_buy_impact,
        "buy_impact_decay_5m": recent_buy_impact - prior_buy_impact,
        "recent_sell_impact_5m": recent_sell_impact,
        "prior_sell_impact_5m": prior_sell_impact,
        "sell_impact_change_5m": recent_sell_impact - prior_sell_impact,
        "recent_inter_arrival_ms_5m": recent_inter_arrival,
        "prior_inter_arrival_ms_5m": prior_inter_arrival,
        "inter_arrival_ms_change_5m": recent_inter_arrival - prior_inter_arrival,
        "pre_trade_notional_p90_60m": _tail_nanmean(np.asarray(pre_trade_notional_p90, dtype=float), 60),
        "pre_side_sign_entropy_60m": _tail_nanmean(np.asarray(pre_side_sign_entropy, dtype=float), 60),
        "pre_inter_arrival_ms_mean_60m": _tail_nanmean(np.asarray(pre_inter_arrival_ms_mean, dtype=float), 60),
        "pre_buy_impact_per_notional_60m": _tail_nanmean(np.asarray(pre_buy_impact_per_notional, dtype=float), 60),
        "pre_sell_impact_per_notional_60m": _tail_nanmean(np.asarray(pre_sell_impact_per_notional, dtype=float), 60),
    }


def _empty_result() -> dict[str, float]:
    return {name: (0.0 if name == "aggtrades_available" else float("nan")) for name in PUMP_FADE_AGGTRADES_DYNAMICS_FEATURES}


def _safe_nanmean(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.mean(finite)) if len(finite) else float("nan")


def _tail_nanmean(values: np.ndarray, length: int) -> float:
    return _safe_nanmean(values[-length:])


def _prior_nanmean(values: np.ndarray, length: int) -> float:
    prior = values[-2 * length : -length]
    return _safe_nanmean(prior) if len(prior) else _tail_nanmean(values, length)


__all__ = [
    "PUMP_FADE_AGGTRADES_DYNAMICS_FEATURES",
    "PUMP_FADE_AGGTRADES_DYNAMICS_SCHEMA_VERSION",
    "build_aggtrades_dynamics_features",
]
