"""Shared pump-category contract for live and historical anomaly execution.

The live runner and the backtest must rank and parameterize runner categories
from this module. Discovery remains a backtest-only fallback and is intentionally
not part of the live category set.
"""

from __future__ import annotations

from dataclasses import dataclass


CATEGORY_CONTRACT_ID = "shared_pump_category_contract_v1_live_overlay_v8"
PUMP_CATEGORY_DISCOVERY = "discovery"
PUMP_CATEGORY_FAMILY_LIVE = "live_priority"
PUMP_CATEGORY_FAMILY_DISCOVERY = "discovery"


@dataclass(frozen=True, slots=True)
class PumpCategoryContract:
    category_id: str
    label: str
    priority: int
    min_oi_change_pct_3x5m: float | None = None
    min_mark_close_vs_decision_close_basis: float | None = None
    max_start_quote_ratio: float | None = None
    max_start_trade_ratio: float | None = None
    min_baseline_quote_daily_proxy: float | None = None
    max_start_avg_trade_quote_size_ratio: float | None = None
    max_start_quote_ratio_per_abs_return: float | None = None
    max_start_trade_ratio_per_abs_return: float | None = None
    min_start_range_pct_ratio_to_baseline: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = None
    min_initial_risk_pct: float | None = None
    max_initial_risk_pct: float | None = None
    max_prior_up_down_whipsaw_to_impulse_range: float | None = None
    min_next_taker_buy_quote_share: float | None = None
    max_start_taker_buy_quote_share_delta: float | None = None
    max_price_retention: float | None = None
    min_flow_hold_count: int | None = None
    max_prior_spike_count_72h: int | None = None
    min_start_lower_wick_to_range: float | None = None
    max_start_upper_wick_to_range: float | None = None
    max_prior_fast_fade_count_72h: int | None = None


DEFAULT_PUMP_CATEGORY_IDS: tuple[str, ...] = (
    "runner_oi_confirmed",
    "runner_flow",
    "runner_balanced",
)

TIMEFRAME_CATEGORY_PRIORITY: dict[tuple[str, str], tuple[str, ...]] = {
    ("5m", "30s"): ("runner_flow", "runner_oi_confirmed", "runner_balanced"),
    ("1m", "15s"): ("runner_oi_confirmed", "runner_flow", "runner_balanced"),
    ("1m", "5s"): ("runner_oi_confirmed", "runner_flow", "runner_balanced"),
}

SUPPORTED_PUMP_CATEGORIES: dict[str, PumpCategoryContract] = {
    "runner_oi_confirmed": PumpCategoryContract(
        category_id="runner_oi_confirmed",
        label="runner OI confirmed",
        priority=10,
        min_oi_change_pct_3x5m=0.002,
        min_mark_close_vs_decision_close_basis=0.002,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=250.0,
        min_baseline_quote_daily_proxy=300_000.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=3_000.0,
        min_start_range_pct_ratio_to_baseline=6.0,
        min_initial_risk_pct=0.010,
        max_prior_up_down_whipsaw_to_impulse_range=0.60,
        max_prior_spike_count_72h=30,
        max_start_taker_buy_quote_share_delta=0.35,
        max_prior_fast_fade_count_72h=2,
    ),
    "runner_flow": PumpCategoryContract(
        category_id="runner_flow",
        label="runner flow",
        priority=20,
        min_mark_close_vs_decision_close_basis=0.0005,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=250.0,
        min_baseline_quote_daily_proxy=300_000.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=1_800.0,
        min_start_range_pct_ratio_to_baseline=8.0,
        min_initial_risk_pct=0.010,
        max_prior_up_down_whipsaw_to_impulse_range=0.50,
        max_prior_spike_count_72h=30,
        max_start_taker_buy_quote_share_delta=0.35,
        min_flow_hold_count=1,
        max_prior_fast_fade_count_72h=2,
    ),
    "runner_reclaim": PumpCategoryContract(
        category_id="runner_reclaim",
        label="runner reclaim",
        priority=30,
        min_mark_close_vs_decision_close_basis=0.0005,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=10.0,
        min_baseline_quote_daily_proxy=300_000.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=3_000.0,
        max_start_range_pct_ratio_to_baseline=10.5,
        max_initial_risk_pct=0.032,
        max_prior_spike_count_72h=5,
        max_start_taker_buy_quote_share_delta=0.35,
        min_start_lower_wick_to_range=0.0,
        max_start_upper_wick_to_range=0.20,
        max_prior_fast_fade_count_72h=2,
    ),
    "runner_balanced": PumpCategoryContract(
        category_id="runner_balanced",
        label="runner balanced",
        priority=40,
        min_mark_close_vs_decision_close_basis=0.002,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=250.0,
        min_baseline_quote_daily_proxy=300_000.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=1_500.0,
        min_start_range_pct_ratio_to_baseline=5.0,
        min_initial_risk_pct=0.008,
        max_prior_up_down_whipsaw_to_impulse_range=0.60,
        max_prior_spike_count_72h=20,
        max_start_taker_buy_quote_share_delta=0.35,
        max_prior_fast_fade_count_72h=2,
    ),
    "balanced_market": PumpCategoryContract(
        category_id="balanced_market",
        label="balanced market",
        priority=90,
    ),
    "mild_market": PumpCategoryContract(
        category_id="mild_market",
        label="mild market",
        priority=100,
        max_start_quote_ratio=120.0,
        max_start_trade_ratio=60.0,
        max_start_avg_trade_quote_size_ratio=10.0,
        max_start_quote_ratio_per_abs_return=30_000.0,
        max_start_range_pct_ratio_to_baseline=35.0,
        min_next_taker_buy_quote_share=0.46,
        max_price_retention=0.98,
    ),
}


def priority_for_timeframe(setup_timeframe: object, entry_timeframe: object) -> tuple[str, ...]:
    priority = TIMEFRAME_CATEGORY_PRIORITY.get((str(setup_timeframe), str(entry_timeframe)))
    if priority is not None:
        return priority
    return DEFAULT_PUMP_CATEGORY_IDS


def backtest_profile_overrides(category_id: str) -> dict[str, object]:
    """Return AnomalyBacktestConfig override kwargs for a shared category."""
    category = SUPPORTED_PUMP_CATEGORIES.get(str(category_id))
    if category is None:
        raise ValueError(f"unsupported pump category: {category_id}")
    values: dict[str, object] = {
        "min_oi_change_pct_3x5m": category.min_oi_change_pct_3x5m,
        "min_mark_close_vs_decision_close_basis": category.min_mark_close_vs_decision_close_basis,
        "max_start_quote_ratio": category.max_start_quote_ratio,
        "max_start_trade_ratio": category.max_start_trade_ratio,
        "min_baseline_quote_daily_proxy": category.min_baseline_quote_daily_proxy,
        "max_start_avg_trade_quote_size_ratio": category.max_start_avg_trade_quote_size_ratio,
        "max_start_quote_ratio_per_abs_return": category.max_start_quote_ratio_per_abs_return,
        "max_start_trade_ratio_per_abs_return": category.max_start_trade_ratio_per_abs_return,
        "min_start_range_pct_ratio_to_baseline": category.min_start_range_pct_ratio_to_baseline,
        "max_start_range_pct_ratio_to_baseline": category.max_start_range_pct_ratio_to_baseline,
        "min_initial_risk_pct": category.min_initial_risk_pct,
        "max_initial_risk_pct": category.max_initial_risk_pct,
        "max_prior_up_down_whipsaw_to_impulse_range": category.max_prior_up_down_whipsaw_to_impulse_range,
        "min_next_taker_buy_quote_share": category.min_next_taker_buy_quote_share,
        "max_start_taker_buy_quote_share_delta": category.max_start_taker_buy_quote_share_delta,
        "max_price_retention": category.max_price_retention,
        "min_flow_hold_count": category.min_flow_hold_count,
        "max_prior_spike_count_72h": category.max_prior_spike_count_72h,
        "min_start_lower_wick_to_range": category.min_start_lower_wick_to_range,
        "max_start_upper_wick_to_range": category.max_start_upper_wick_to_range,
        "max_prior_fast_fade_count_72h": category.max_prior_fast_fade_count_72h,
    }
    if category.min_oi_change_pct_3x5m is not None:
        values["require_oi_status_ok"] = True
    return {key: value for key, value in values.items() if value is not None}
