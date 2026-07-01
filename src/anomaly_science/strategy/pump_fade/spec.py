from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from anomaly_science.strategy.base import BaseResearchStrategy, StrategyCustomFeatureSpec
from anomaly_science.strategy.execution import PUMP_FADE_EXECUTION_POLICIES, StrategyExecutionPolicies
from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig
from anomaly_science.strategy.pump_fade.cvd import PUMP_FADE_CVD_MODEL_FEATURES
from anomaly_science.strategy.pump_fade.event_memory import (
    PUMP_FADE_EVENT_MEMORY_FEATURES,
    PUMP_FADE_EVENT_MEMORY_FLAGS,
)
from anomaly_science.strategy.pump_fade.path_dynamics import (
    PUMP_FADE_PATH_DYNAMICS_FEATURES,
)


PUMP_FADE_FEATURE_SCHEMA_VERSION = "pump_fade_market_mechanics_v5"
PUMP_FADE_STRATEGY_CONTRACT_VERSION = "horizon_free_event_strategy_v1"


PUMP_MARKET_MECHANICS_FEATURES: tuple[StrategyCustomFeatureSpec, ...] = (
    StrategyCustomFeatureSpec("pump_verticality", "pump_geometry", "float", "Event rise divided by elapsed closed minutes."),
    StrategyCustomFeatureSpec("max_1m_high_return", "pump_geometry", "float", "Largest closed-candle high/open expansion in the event."),
    StrategyCustomFeatureSpec("max_1m_close_return", "pump_geometry", "float", "Largest closed-candle close/open return in the event."),
    StrategyCustomFeatureSpec("path_efficiency", "pump_geometry", "float", "Net event rise divided by total absolute close path."),
    StrategyCustomFeatureSpec("mean_pullback_between_highs", "auction_structure", "float", "Mean closed-bar retracement between successive running highs."),
    StrategyCustomFeatureSpec("max_pullback_between_highs", "auction_structure", "float", "Largest closed-bar retracement between successive running highs."),
    StrategyCustomFeatureSpec("rehigh_count", "auction_structure", "int", "Number of running-high renewals after the first event high."),
    StrategyCustomFeatureSpec("current_upper_wick_fraction", "rejection", "float", "Current closed candle upper wick as a fraction of range."),
    StrategyCustomFeatureSpec("current_lower_wick_fraction", "rejection", "float", "Current closed candle lower wick as a fraction of range."),
    StrategyCustomFeatureSpec("current_body_fraction", "rejection", "float", "Current candle body as a fraction of range."),
    StrategyCustomFeatureSpec("current_close_location", "rejection", "float", "Current close location inside the candle range."),
    StrategyCustomFeatureSpec("mean_upper_wick_fraction", "rejection", "float", "Mean event upper-wick fraction."),
    StrategyCustomFeatureSpec("max_upper_wick_fraction", "rejection", "float", "Maximum event upper-wick fraction."),
    StrategyCustomFeatureSpec("event_turnover", "participation", "float", "Absolute event quote turnover; audit/context only.", is_model_feature=False),
    StrategyCustomFeatureSpec("turnover_share_24h", "participation", "float", "Event turnover divided by trailing 24h quote turnover."),
    StrategyCustomFeatureSpec("event_trade_count", "participation", "float", "Absolute event trade count; audit/context only.", is_model_feature=False),
    StrategyCustomFeatureSpec("average_trade_notional_vs_24h", "participation", "float", "Event quote turnover per trade relative to trailing 24h."),
    StrategyCustomFeatureSpec("turnover_top_candle_share", "participation", "float", "Largest minute share of event turnover."),
    StrategyCustomFeatureSpec("trade_count_top_candle_share", "participation", "float", "Largest minute share of event trades."),
    StrategyCustomFeatureSpec("retail_frenzy_proxy", "participant_proxy", "float", "Trade-count acceleration relative to quote-volume acceleration; not participant identity.", identifiability="proxy"),
    StrategyCustomFeatureSpec("large_print_proxy", "participant_proxy", "float", "Quote-volume acceleration relative to trade-count acceleration; not whale identity.", identifiability="proxy"),
    StrategyCustomFeatureSpec("algorithmic_persistence_proxy", "participant_proxy", "float", "Regularity of minute participation; not proof of algorithmic execution.", identifiability="proxy"),
    StrategyCustomFeatureSpec("taker_buy_share_event", "order_flow", "float", "Share of event quote volume initiated by taker buys.", required_streams=("taker_buy_quote_volume",)),
    StrategyCustomFeatureSpec("taker_imbalance_event", "order_flow", "float", "Signed taker-buy minus taker-sell quote-volume share.", required_streams=("taker_buy_quote_volume",)),
    StrategyCustomFeatureSpec("pre_return_15m", "preconditioning", "float", "Return over the 15 closed minutes preceding the event snapshot."),
    StrategyCustomFeatureSpec("pre_return_60m", "preconditioning", "float", "Return over the 60 closed minutes preceding the event snapshot."),
    StrategyCustomFeatureSpec("pre_return_240m", "preconditioning", "float", "Return over the 240 closed minutes preceding the event snapshot."),
    StrategyCustomFeatureSpec("pre_dump_depth_60m", "preconditioning", "float", "Current close drawdown from the prior 60m high."),
    StrategyCustomFeatureSpec("price_vs_ema_240", "market_regime", "float", "Price displacement from a causal 240-minute exponential mean."),
    StrategyCustomFeatureSpec("ema_240_slope_60m", "market_regime", "float", "Causal 60-minute change in the 240-minute exponential mean."),
    StrategyCustomFeatureSpec("oi_change_15m", "positioning", "float", "Closed-sample OI change over 15 minutes.", required_streams=("open_interest",)),
    StrategyCustomFeatureSpec("oi_change_60m", "positioning", "float", "Closed-sample OI change over 60 minutes.", required_streams=("open_interest",)),
    StrategyCustomFeatureSpec("price_up_oi_up", "positioning_state", "bool", "Price and OI both rising; compatible with new risk entering, not directional identity.", required_streams=("open_interest",), identifiability="latent_hypothesis"),
    StrategyCustomFeatureSpec("price_up_oi_down", "positioning_state", "bool", "Price rising while OI falls; compatible with short covering/deleveraging.", required_streams=("open_interest",), identifiability="latent_hypothesis"),
    StrategyCustomFeatureSpec("price_down_oi_up", "positioning_state", "bool", "Price falling while OI rises; compatible with new short risk entering.", required_streams=("open_interest",), identifiability="latent_hypothesis"),
    StrategyCustomFeatureSpec("price_down_oi_down", "positioning_state", "bool", "Price and OI falling; compatible with long closing/liquidation.", required_streams=("open_interest",), identifiability="latent_hypothesis"),
)


_PUMP_FADE_DATASET_FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "pump_geometry": (
        "pump_size", "pump_elapsed_min", "verticality", "max_candle_share",
        "max_1m_high_return", "max_1m_close_return", "event_path_efficiency",
        "mean_pullback_between_highs", "max_pullback_between_highs",
        "mean_minutes_between_highs", "rehigh_count", "close_drawdown_from_high",
        "remaining_to_base", "ext_vwap",
    ),
    "candle_rejection": (
        "current_upper_wick_fraction", "current_lower_wick_fraction",
        "current_body_fraction", "current_close_location", "ignition_upper_wick_fraction",
        "mean_upper_wick_fraction", "max_upper_wick_fraction", "green_candle_fraction",
        "upper_wick5", "range_contraction", "candles_since_red",
    ),
    "participation_and_flow": (
        "turnover_top_candle_share", "trade_count_top_candle_share", "turnover",
        "quote_volume_24h", "event_trade_count", "event_average_trade_notional",
        "average_trade_notional_vs_24h", "taker_buy_share_event", "taker_imbalance_event",
        "retail_frenzy_proxy", "large_print_proxy", "algorithmic_persistence_proxy",
        "activity_above_10x_fraction", "taker_decline",
    ),
    "activity_state": (
        "atr_mult", "act_now", "act_now_over_peak", "rel_vol_phase",
        "trade_activity_1h_vs_4h", "quote_activity_1h_vs_4h", "atr_activity_1h_vs_4h",
    ),
    "event_recurrence": (
        "n_prior_24h", "n_prior_48h", "min_since_last_prior", "frac_prior_faded_48h",
        "last_prior_faded", "last_prior_size", "cluster_idx_day", "base_broken_before",
    ),
    "resolved_event_memory": PUMP_FADE_EVENT_MEMORY_FEATURES,
    "cvd_path": PUMP_FADE_CVD_MODEL_FEATURES,
    "path_dynamics": (
        *PUMP_FADE_PATH_DYNAMICS_FEATURES,
        "latest_high_extension",
        "high_extension_decay_ratio",
        "high_interval_change_ratio",
    ),
    "preconditioning_and_regime": (
        "pre_return_15m", "pre_return_60m", "pre_return_240m", "pre_return_1440m",
        "pre_dump_depth_60m", "pre_dump_depth_240m", "price_vs_ema_60",
        "price_vs_ema_240", "price_vs_ema_1440", "ema_240_slope_60m",
        "minutes_since_prior_higher_price", "price_decel",
    ),
    "calendar_context": (
        "ignition_hour_utc", "ignition_minute_of_hour", "ignition_minutes_from_round_hour",
        "ignition_day_of_week_utc",
    ),
    "decision_timing": ("decision_index",),
}

PUMP_FADE_OI_MODEL_FEATURES: tuple[str, ...] = (
    "oi_change_5m", "oi_change_15m", "oi_change_60m", "oi_change_240m",
    "oi_change_since_ignition", "price_up_oi_up_60m", "price_up_oi_down_60m",
    "price_down_oi_up_60m", "price_down_oi_down_60m",
)

_PUMP_FADE_MISSINGNESS_FLAGS: tuple[str, ...] = (
    "has_atr_activity_1h_vs_4h",
    "has_average_trade_notional_baseline",
    "has_last_resolved_prior",
    "has_prior_event",
    "has_prior_higher_price",
    "has_quote_activity_1h_vs_4h",
    "has_quote_volume_24h",
    "has_rel_vol_phase",
    "has_resolved_prior_48h",
    "has_taker_buy_quote_data",
    "has_trade_activity_1h_vs_4h",
    "cvd_available",
    *PUMP_FADE_EVENT_MEMORY_FLAGS,
)


def _dataset_feature_catalog() -> tuple[StrategyCustomFeatureSpec, ...]:
    specs: list[StrategyCustomFeatureSpec] = []
    for family, names in _PUMP_FADE_DATASET_FEATURE_FAMILIES.items():
        for name in names:
            identifiability = "proxy" if name in {
                "retail_frenzy_proxy", "large_print_proxy", "algorithmic_persistence_proxy"
            } else "observable"
            specs.append(
                StrategyCustomFeatureSpec(
                    name=name,
                    family=family,
                    dtype="float",
                    description=f"Causal pump-fade {family} coordinate `{name}`; formula is frozen in the canonical builder.",
                    identifiability=identifiability,
                )
            )
    specs.append(
        StrategyCustomFeatureSpec(
            name="session",
            family="calendar_context",
            dtype="str",
            description="UTC liquidity-session category known at ignition.",
        )
    )
    specs.extend(
        StrategyCustomFeatureSpec(
            name=name,
            family="positioning",
            dtype="float",
            description=f"Causal point-in-time open-interest state `{name}`; compatible explanation, not actor identity.",
            required_streams=("open_interest",),
            identifiability="latent_hypothesis" if name.startswith("price_") else "observable",
        )
        for name in PUMP_FADE_OI_MODEL_FEATURES
    )
    specs.append(
        StrategyCustomFeatureSpec(
            name="oi_available",
            family="data_quality",
            dtype="bool",
            description="Audit-only proof that bounded-age causal OI exists at the snapshot.",
            is_model_feature=False,
            required_streams=("open_interest",),
        )
    )
    specs.extend(
        StrategyCustomFeatureSpec(
            name=name,
            family="missingness",
            dtype="bool",
            description=(
                f"Explicit point-in-time availability flag `{name}`; paired with "
                "a NaN-valued causal feature rather than a numeric sentinel."
            ),
        )
        for name in _PUMP_FADE_MISSINGNESS_FLAGS
    )
    return tuple(specs)


PUMP_FADE_DATASET_FEATURES = _dataset_feature_catalog()


@dataclass(frozen=True, slots=True)
class PumpFadeStrategyDefinition(BaseResearchStrategy):
    """Typed definition consumed by the optimized horizon-free pump-fade pipeline."""

    detector_config: PumpFadeDecisionConfig = field(default_factory=PumpFadeDecisionConfig)
    strategy_name: str = "pump_fade_close_race_v1"
    strategy_version: str = "1.0.0"
    strategy_contract_version: str = PUMP_FADE_STRATEGY_CONTRACT_VERSION
    feature_schema_version: str = PUMP_FADE_FEATURE_SCHEMA_VERSION
    label_schema_version: str = "pump_fade_close_race_horizon_free_v1"
    outcome_protocol: str = "close_to_base_vs_close_above_running_high_horizon_free"

    @property
    def required_data_streams(self) -> Mapping[str, bool]:
        return {"open_interest": False, "liquidations": False}

    @property
    def execution_policies(self) -> StrategyExecutionPolicies:
        return PUMP_FADE_EXECUTION_POLICIES

    @property
    def custom_feature_catalog(self) -> tuple[StrategyCustomFeatureSpec, ...]:
        return PUMP_FADE_DATASET_FEATURES


PUMP_FADE_STRATEGY = PumpFadeStrategyDefinition()


__all__ = [
    "PUMP_FADE_FEATURE_SCHEMA_VERSION",
    "PUMP_FADE_STRATEGY",
    "PUMP_FADE_STRATEGY_CONTRACT_VERSION",
    "PUMP_FADE_DATASET_FEATURES",
    "PUMP_FADE_CVD_MODEL_FEATURES",
    "PUMP_FADE_OI_MODEL_FEATURES",
    "PUMP_MARKET_MECHANICS_FEATURES",
    "PumpFadeStrategyDefinition",
]
