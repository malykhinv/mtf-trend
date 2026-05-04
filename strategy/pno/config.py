"""Configuration for the PNO strategy."""

from __future__ import annotations

from dataclasses import dataclass, replace

from constants import DEFAULT_BEE_BITE_DEPOSIT, DEFAULT_COMMISSION_RATE
from domain.enums.timeframe import Timeframe

PnoTimeframePair = tuple[Timeframe, Timeframe]

PNO_BACKTEST_TIMEFRAME_PAIRS: tuple[PnoTimeframePair, ...] = (
    (Timeframe.M5, Timeframe.S30),
    (Timeframe.M5, Timeframe.S15),
    (Timeframe.M1, Timeframe.S5),
)
PNO_LIVE_TIMEFRAME_PAIRS: tuple[PnoTimeframePair, ...] = (
    (Timeframe.M5, Timeframe.S30),
    (Timeframe.M1, Timeframe.S5),
)
PNO_SUPPORTED_TIMEFRAME_PAIRS: tuple[PnoTimeframePair, ...] = tuple(
    dict.fromkeys((*PNO_BACKTEST_TIMEFRAME_PAIRS, *PNO_LIVE_TIMEFRAME_PAIRS))
)
PNO_DEFAULT_BACKTEST_TIMEFRAME_PAIR: PnoTimeframePair = PNO_BACKTEST_TIMEFRAME_PAIRS[0]
PNO_DEFAULT_LIVE_TIMEFRAME_PAIR: PnoTimeframePair = PNO_LIVE_TIMEFRAME_PAIRS[0]
PNO_DEFAULT_LEVELS_TIMEFRAME = PNO_DEFAULT_BACKTEST_TIMEFRAME_PAIR[0]
PNO_DEFAULT_ENTRY_TIMEFRAME = PNO_DEFAULT_BACKTEST_TIMEFRAME_PAIR[1]
PNO_SUPPORTED_LEVELS_TIMEFRAMES: tuple[Timeframe, ...] = tuple(
    dict.fromkeys(levels_timeframe for levels_timeframe, _entry_timeframe in PNO_SUPPORTED_TIMEFRAME_PAIRS)
)
PNO_SUPPORTED_ENTRY_TIMEFRAMES: tuple[Timeframe, ...] = tuple(
    dict.fromkeys(entry_timeframe for _levels_timeframe, entry_timeframe in PNO_SUPPORTED_TIMEFRAME_PAIRS)
)
PNO_SUPPORTED_ENTRY_CONFIRMATION_MODES: tuple[str, ...] = ("close_above",)
PNO_SUPPORTED_CATEGORY_MODES: tuple[str, ...] = ("all", "core", "discovery")
PNO_DEFAULT_RISK_PCT = 0.05


@dataclass(frozen=True, slots=True)
class PnoParams:
    symbol: str
    pno_variant_id: str = "baseline"
    entry_confirmation_mode: str = "close_above"
    levels_timeframe: Timeframe = PNO_DEFAULT_LEVELS_TIMEFRAME
    entry_timeframe: Timeframe = PNO_DEFAULT_ENTRY_TIMEFRAME
    pno_deposit: float = DEFAULT_BEE_BITE_DEPOSIT
    pno_risk_pct: float = PNO_DEFAULT_RISK_PCT
    pno_r_trade: float | None = None
    fee_rate: float = DEFAULT_COMMISSION_RATE
    min_data_5m: int = 300
    min_data_1m: int = 60
    stage1_pump_min_bars: int = 1
    stage1_pump_max_bars: int = 4
    stage1_pullback_min_bars: int = 1
    stage1_pullback_max_bars: int = 9
    stage1_pullback_low_min_pump_fraction: float = 0.50
    stage1_level_max_pullback_reclaim_fraction: float = 0.60
    stage1_fetch_post_bars: int = 9
    min_stage1_leg_v1: float = 1.0
    min_stage1_leg_v5_fraction: float = 0.5
    stage1_hold_fraction: float = 0.55
    pullback_min_v1: float = 1.0
    pullback_min_pump_fraction_5m: float = 0.15
    pullback_valid_min_leg_fraction: float = 0.20
    pullback_valid_max_leg_fraction: float = 1.20
    pullback_invalid_max_leg_fraction: float = 1.25
    pullback_valid_max_v5: float = 4.5
    pullback_invalid_max_v5: float = 6.0
    pullback_max_age_bars: int = 12
    structure_reversal_min_v1_fraction: float = 0.35
    structure_reversal_min_body_fraction: float = 0.20
    structure_pivot_merge_v1_fraction: float = 0.0
    structure_min_swing_vs_previous_avg: float = 0.50
    structure_min_leg_v1_fraction: float = 0.50
    structure_min_leg_bars: int = 1
    structure_terminal_retrace_fraction: float = 0.50
    structure_break_min_close_v1_fraction: float = 0.05
    structure_break_min_close_position: float = 0.55
    structure_max_breakout_extension_fraction: float = 0.92
    structure_min_descending_pivots: int = 4
    pullback_min_trade_activity_vs_sleep: float = 3.0
    pullback_min_quote_volume_vs_sleep: float = 3.0
    stage3_max_post_high_wick_share: float = 0.72
    stage3_max_post_high_body_overlap_rate: float = 0.65
    stage3_max_post_high_chop_alternation_rate: float = 0.55
    stage3_max_post_high_chop_path_efficiency: float = 0.18
    stage3_min_post_high_5m_volume_support_fraction: float = 0.50
    stage3_fast_reclaim_min_post_high_5m_volume_support_fraction: float = 0.0
    stage3_fast_reclaim_max_pullback_age_bars: int = 0
    ideal_like_impulse_enabled: bool = False
    ideal_like_min_impulse_atr_pre: float = 0.0
    ideal_like_min_peak_bar_tr_atr_pre: float = 0.0
    ideal_like_min_volume_ratio_start: float = 0.0
    ideal_like_min_path_efficiency: float = 0.0
    ideal_like_max_wick_share: float = 1.0
    ideal_like_min_body_share_mean: float = 0.0
    ideal_like_min_body_wick_edge: float = -1.0
    ideal_like_max_micro_flat_bar_share: float = 1.0
    ideal_like_max_active_high_upper_wick_share: float = 1.0
    ideal_like_max_counterflow_ratio_5m: float = 1.0
    ideal_like_relaxed_level_maturity_fraction: float = 0.0
    ideal_like_level_latest_high_max_age_bars: int = 0
    ideal_like_ignore_decay_invalidation: bool = False
    stage1_min_cumulative_quote_volume: float = 255_000.0
    stage1_pre_pump_ema_crosses_min: int = 1
    stage1_barcode_max_fraction_1h: float = 1.00
    stage1_barcode_tr_atr_fraction: float = 0.25
    stage1_barcode_tr_price_fraction: float = 0.0010
    stage1_min_impulse_atr_pre: float = 1.5
    stage1_min_peak_bar_tr_atr_pre: float = 1.1
    stage1_min_volume_ratio_start: float = 10.0
    stage1_min_trade_ratio_start: float = 10.0
    stage1_min_volume_ratio_continue: float = 3.0
    stage1_min_trade_ratio_continue: float = 3.0
    stage1_flow_hold_bars: int = 2
    stage1_flow_hold_window_bars: int = 4
    stage1_flow_hold_min_start_fraction: float = 0.35
    stage1_active_context_min_start_fraction: float = 0.35
    stage1_active_context_min_baseline_ratio: float = 3.0
    stage1_min_path_efficiency: float = 0.18
    stage1_max_wick_share: float = 0.68
    stage1_min_body_share_mean: float = 0.18
    stage1_max_flat_body_share: float = 1.0
    stage1_min_body_wick_edge: float = -0.028
    stage1_max_micro_flat_bar_share: float = 0.65
    stage1_max_active_high_upper_wick_share: float = 0.79
    stage1_max_red_body_share_5m: float = 0.45
    stage1_max_counterflow_ratio_5m: float = 0.04
    stage1_max_red_body_share_1m: float = 0.78
    stage1_max_counterflow_ratio_1m: float = 0.92
    stage1_min_pump_pct: float = 0.015
    stage1_min_pretrend_range_ratio_2h: float = 2.0
    stage1_pre_pump_high_max_fraction_of_leg: float = 0.50
    level_cluster_spread_v1: float = 0.45
    level_cluster_relaxed_spread_v1: float = 1.00
    level_latest_high_max_age_bars: int = 30
    level_max_age_bars_upper_tf: int = 24
    level_touch_tolerance_v1: float = 0.35
    level_low_minor_break_v1: float = 0.35
    level_low_major_break_v1: float = 0.75
    level_min_maturity_fraction: float = 0.20
    level_rearm_min_distance_v1: float = 0.50
    max_level_touches: int = 5
    min_score: float = 70.0
    strong_score: float = 80.0
    slip_plan_v1_fraction: float = 0.10
    min_tick_fraction: float = 0.0001
    max_entry_pullback_fraction: float = 0.58
    min_entry_rr: float = 1.0
    max_htf_bars_since_active_high: int = 10
    close_above_max_entry_pos: float = 0.70
    close_above_min_entry_pos: float = 0.24
    close_above_max_pullback_fraction_of_leg: float = 1.0
    close_above_max_post_high_wick_share: float = 0.85
    close_above_max_active_high_upper_wick_share: float = 0.50
    close_above_min_active_high_close_position: float = 0.0
    close_above_min_post_high_alternation_rate: float = 0.0
    close_above_max_level_cross_bars: int = 4
    close_above_min_level_life_quote_volume_median: float = 10_000.0
    close_above_min_signal_volume_vs_recent: float = 0.0
    close_above_min_signal_ema9_slope_3: float = 0.10
    close_above_min_signal_ema20_slope_3: float = 0.08
    close_above_min_signal_ema_spread_pct: float = 0.90
    close_above_min_signal_close_position_in_chop: float = 0.25
    close_above_choppy_overlap_threshold: float = 0.95
    tp1_share: float = 0.50
    be_arm_to_active_high_fraction: float = 0.60
    close_above_be_start_fraction: float = 0.60
    close_above_be_step_fraction: float = 0.05
    close_above_be_step_bars: int = 1
    close_above_be_min_fraction: float = 0.25
    be_buffer_r_fraction: float = 0.05


@dataclass(frozen=True, slots=True)
class PnoCategoryProfile:
    category_id: str
    label: str
    priority: int
    params: PnoParams


def _format_pno_timeframe_pairs(timeframe_pairs: tuple[PnoTimeframePair, ...]) -> str:
    return ", ".join(f"{levels.value}-{entry.value}" for levels, entry in timeframe_pairs)


def resolve_pno_default_timeframe_pair(*, mode: str = "backtest") -> PnoTimeframePair:
    if mode == "backtest":
        return PNO_DEFAULT_BACKTEST_TIMEFRAME_PAIR
    if mode == "live":
        return PNO_DEFAULT_LIVE_TIMEFRAME_PAIR
    raise ValueError(f"unsupported PNO timeframe mode: {mode}")


def validate_pno_timeframe_pair(
    *,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    supported_pairs: tuple[PnoTimeframePair, ...] = PNO_SUPPORTED_TIMEFRAME_PAIRS,
    context: str = "pno",
) -> None:
    timeframe_pair = (levels_timeframe, entry_timeframe)
    if timeframe_pair in supported_pairs:
        return
    supported = _format_pno_timeframe_pairs(supported_pairs)
    raise ValueError(
        f"{context} supports only timeframe pairs {{{supported}}}, "
        f"got {levels_timeframe.value}-{entry_timeframe.value}"
    )


def validate_pno_params(params: PnoParams) -> None:
    validate_pno_timeframe_pair(
        levels_timeframe=params.levels_timeframe,
        entry_timeframe=params.entry_timeframe,
    )
    if params.entry_confirmation_mode not in PNO_SUPPORTED_ENTRY_CONFIRMATION_MODES:
        supported = ", ".join(PNO_SUPPORTED_ENTRY_CONFIRMATION_MODES)
        raise ValueError(
            f"pno supports only entry_confirmation_mode in {{{supported}}}, got {params.entry_confirmation_mode}"
        )
    if params.min_data_5m < 300:
        raise ValueError("min_data_5m must be >= 300")
    if params.min_data_1m < 60:
        raise ValueError("min_data_1m must be >= 60")
    if params.stage1_pump_min_bars < 1 or params.stage1_pump_max_bars < params.stage1_pump_min_bars:
        raise ValueError("stage1 pump bars bounds are invalid")
    if params.stage1_pullback_min_bars < 1 or params.stage1_pullback_max_bars < params.stage1_pullback_min_bars:
        raise ValueError("stage1 pullback bars bounds are invalid")
    if not 0.0 < params.stage1_pullback_low_min_pump_fraction < 1.0:
        raise ValueError("stage1_pullback_low_min_pump_fraction must be in range (0, 1)")
    if not 0.0 < params.stage1_level_max_pullback_reclaim_fraction <= 1.0:
        raise ValueError("stage1_level_max_pullback_reclaim_fraction must be in range (0, 1]")
    if params.stage1_fetch_post_bars < 1:
        raise ValueError("stage1_fetch_post_bars must be >= 1")
    if params.fee_rate < 0.0 or params.fee_rate > 0.01:
        raise ValueError("fee_rate must be in range [0, 0.01]")
    if params.pno_deposit <= 0.0:
        raise ValueError("pno_deposit must be > 0")
    if params.pno_risk_pct <= 0.0 or params.pno_risk_pct > 1.0:
        raise ValueError("pno_risk_pct must be in range (0, 1]")
    if params.pno_r_trade is not None and params.pno_r_trade <= 0.0:
        raise ValueError("pno_r_trade must be > 0 when provided")
    if params.min_stage1_leg_v1 <= 0.0:
        raise ValueError("min_stage1_leg_v1 must be > 0")
    if params.min_stage1_leg_v5_fraction <= 0.0:
        raise ValueError("min_stage1_leg_v5_fraction must be > 0")
    if not 0.0 < params.stage1_hold_fraction < 1.0:
        raise ValueError("stage1_hold_fraction must be in range (0, 1)")
    if params.pullback_min_v1 <= 0.0:
        raise ValueError("pullback_min_v1 must be > 0")
    if not 0.0 < params.pullback_min_pump_fraction_5m < 1.0:
        raise ValueError("pullback_min_pump_fraction_5m must be in range (0, 1)")
    if not 0.0 < params.pullback_valid_min_leg_fraction < params.pullback_valid_max_leg_fraction:
        raise ValueError(
            "pullback_valid_min_leg_fraction must be > 0 and smaller than pullback_valid_max_leg_fraction"
        )
    if not 0.0 < params.pullback_valid_max_leg_fraction < params.pullback_invalid_max_leg_fraction:
        raise ValueError(
            "pullback_valid_max_leg_fraction must be > 0 and smaller than pullback_invalid_max_leg_fraction"
        )
    if params.pullback_valid_max_v5 <= 0.0 or params.pullback_invalid_max_v5 <= params.pullback_valid_max_v5:
        raise ValueError("pullback_invalid_max_v5 must be > pullback_valid_max_v5 > 0")
    if params.pullback_max_age_bars < 2:
        raise ValueError("pullback_max_age_bars must be >= 2")
    if params.structure_reversal_min_v1_fraction <= 0.0:
        raise ValueError("structure_reversal_min_v1_fraction must be > 0")
    if not 0.0 <= params.structure_reversal_min_body_fraction <= 1.0:
        raise ValueError("structure_reversal_min_body_fraction must be in range [0, 1]")
    if params.structure_pivot_merge_v1_fraction < 0.0:
        raise ValueError("structure_pivot_merge_v1_fraction must be >= 0")
    if params.structure_min_swing_vs_previous_avg < 0.0:
        raise ValueError("structure_min_swing_vs_previous_avg must be >= 0")
    if params.structure_min_leg_v1_fraction < 0.0:
        raise ValueError("structure_min_leg_v1_fraction must be >= 0")
    if params.structure_min_leg_bars < 1:
        raise ValueError("structure_min_leg_bars must be >= 1")
    if not 0.0 < params.structure_terminal_retrace_fraction <= 1.0:
        raise ValueError("structure_terminal_retrace_fraction must be in range (0, 1]")
    if params.structure_break_min_close_v1_fraction < 0.0:
        raise ValueError("structure_break_min_close_v1_fraction must be >= 0")
    if not 0.0 <= params.structure_break_min_close_position <= 1.0:
        raise ValueError("structure_break_min_close_position must be in range [0, 1]")
    if not 0.0 < params.structure_max_breakout_extension_fraction <= 1.0:
        raise ValueError("structure_max_breakout_extension_fraction must be in range (0, 1]")
    if params.structure_min_descending_pivots < 4:
        raise ValueError("structure_min_descending_pivots must be >= 4")
    if params.pullback_min_trade_activity_vs_sleep <= 0.0:
        raise ValueError("pullback_min_trade_activity_vs_sleep must be > 0")
    if params.pullback_min_quote_volume_vs_sleep <= 0.0:
        raise ValueError("pullback_min_quote_volume_vs_sleep must be > 0")
    if not 0.0 <= params.stage3_max_post_high_wick_share <= 1.0:
        raise ValueError("stage3_max_post_high_wick_share must be in range [0, 1]")
    if not 0.0 <= params.stage3_max_post_high_body_overlap_rate <= 1.0:
        raise ValueError("stage3_max_post_high_body_overlap_rate must be in range [0, 1]")
    if not 0.0 <= params.stage3_max_post_high_chop_alternation_rate <= 1.0:
        raise ValueError("stage3_max_post_high_chop_alternation_rate must be in range [0, 1]")
    if not 0.0 <= params.stage3_max_post_high_chop_path_efficiency <= 1.0:
        raise ValueError("stage3_max_post_high_chop_path_efficiency must be in range [0, 1]")
    if params.stage3_min_post_high_5m_volume_support_fraction < 0.0:
        raise ValueError("stage3_min_post_high_5m_volume_support_fraction must be >= 0")
    if params.stage3_fast_reclaim_min_post_high_5m_volume_support_fraction < 0.0:
        raise ValueError("stage3_fast_reclaim_min_post_high_5m_volume_support_fraction must be >= 0")
    if params.stage3_fast_reclaim_max_pullback_age_bars < 0:
        raise ValueError("stage3_fast_reclaim_max_pullback_age_bars must be >= 0")
    if not 0.0 <= params.ideal_like_max_wick_share <= 1.0:
        raise ValueError("ideal_like_max_wick_share must be in range [0, 1]")
    if not 0.0 <= params.ideal_like_min_body_share_mean <= 1.0:
        raise ValueError("ideal_like_min_body_share_mean must be in range [0, 1]")
    if not -1.0 <= params.ideal_like_min_body_wick_edge <= 1.0:
        raise ValueError("ideal_like_min_body_wick_edge must be in range [-1, 1]")
    if not 0.0 <= params.ideal_like_max_micro_flat_bar_share <= 1.0:
        raise ValueError("ideal_like_max_micro_flat_bar_share must be in range [0, 1]")
    if not 0.0 <= params.ideal_like_max_active_high_upper_wick_share <= 1.0:
        raise ValueError("ideal_like_max_active_high_upper_wick_share must be in range [0, 1]")
    if params.ideal_like_max_counterflow_ratio_5m < 0.0:
        raise ValueError("ideal_like_max_counterflow_ratio_5m must be >= 0")
    if not 0.0 <= params.ideal_like_relaxed_level_maturity_fraction <= 1.0:
        raise ValueError("ideal_like_relaxed_level_maturity_fraction must be in range [0, 1]")
    if params.ideal_like_level_latest_high_max_age_bars < 0:
        raise ValueError("ideal_like_level_latest_high_max_age_bars must be >= 0")
    if params.stage1_min_cumulative_quote_volume <= 0.0:
        raise ValueError("stage1_min_cumulative_quote_volume must be > 0")
    if params.stage1_pre_pump_ema_crosses_min < 0:
        raise ValueError("stage1_pre_pump_ema_crosses_min must be >= 0")
    if not 0.0 <= params.stage1_barcode_max_fraction_1h <= 1.0:
        raise ValueError("stage1_barcode_max_fraction_1h must be in range [0, 1]")
    if params.stage1_barcode_tr_atr_fraction <= 0.0:
        raise ValueError("stage1_barcode_tr_atr_fraction must be > 0")
    if params.stage1_barcode_tr_price_fraction <= 0.0:
        raise ValueError("stage1_barcode_tr_price_fraction must be > 0")
    if params.stage1_min_impulse_atr_pre <= 0.0:
        raise ValueError("stage1_min_impulse_atr_pre must be > 0")
    if params.stage1_min_peak_bar_tr_atr_pre <= 0.0:
        raise ValueError("stage1_min_peak_bar_tr_atr_pre must be > 0")
    if params.stage1_min_volume_ratio_start <= 0.0:
        raise ValueError("stage1_min_volume_ratio_start must be > 0")
    if params.stage1_min_trade_ratio_start <= 0.0:
        raise ValueError("stage1_min_trade_ratio_start must be > 0")
    if params.stage1_min_volume_ratio_continue <= 0.0:
        raise ValueError("stage1_min_volume_ratio_continue must be > 0")
    if params.stage1_min_trade_ratio_continue <= 0.0:
        raise ValueError("stage1_min_trade_ratio_continue must be > 0")
    if params.stage1_flow_hold_bars < 1:
        raise ValueError("stage1_flow_hold_bars must be >= 1")
    if params.stage1_flow_hold_window_bars < params.stage1_flow_hold_bars:
        raise ValueError("stage1_flow_hold_window_bars must be >= stage1_flow_hold_bars")
    if not 0.0 < params.stage1_flow_hold_min_start_fraction <= 1.0:
        raise ValueError("stage1_flow_hold_min_start_fraction must be in range (0, 1]")
    if not 0.0 < params.stage1_active_context_min_start_fraction <= 1.0:
        raise ValueError("stage1_active_context_min_start_fraction must be in range (0, 1]")
    if params.stage1_active_context_min_baseline_ratio <= 0.0:
        raise ValueError("stage1_active_context_min_baseline_ratio must be > 0")
    if not 0.0 < params.stage1_min_path_efficiency <= 1.0:
        raise ValueError("stage1_min_path_efficiency must be in range (0, 1]")
    if not 0.0 <= params.stage1_max_wick_share < 1.0:
        raise ValueError("stage1_max_wick_share must be in range [0, 1)")
    if not 0.0 < params.stage1_min_body_share_mean <= 1.0:
        raise ValueError("stage1_min_body_share_mean must be in range (0, 1]")
    if not 0.0 <= params.stage1_max_flat_body_share <= 1.0:
        raise ValueError("stage1_max_flat_body_share must be in range [0, 1]")
    if params.stage1_min_body_wick_edge < -1.0 or params.stage1_min_body_wick_edge > 1.0:
        raise ValueError("stage1_min_body_wick_edge must be in range [-1, 1]")
    if not 0.0 <= params.stage1_max_micro_flat_bar_share <= 1.0:
        raise ValueError("stage1_max_micro_flat_bar_share must be in range [0, 1]")
    if params.close_above_min_signal_ema20_slope_3 < 0.0:
        raise ValueError("close_above_min_signal_ema20_slope_3 must be >= 0")
    if params.close_above_min_signal_ema_spread_pct < 0.0:
        raise ValueError("close_above_min_signal_ema_spread_pct must be >= 0")
    if not 0.0 <= params.close_above_min_signal_close_position_in_chop <= 1.0:
        raise ValueError("close_above_min_signal_close_position_in_chop must be in range [0, 1]")
    if not 0.0 <= params.close_above_choppy_overlap_threshold <= 1.0:
        raise ValueError("close_above_choppy_overlap_threshold must be in range [0, 1]")
    if not 0.0 <= params.stage1_max_active_high_upper_wick_share < 1.0:
        raise ValueError("stage1_max_active_high_upper_wick_share must be in range [0, 1)")
    if not 0.0 <= params.stage1_max_red_body_share_5m <= 1.0:
        raise ValueError("stage1_max_red_body_share_5m must be in range [0, 1]")
    if params.stage1_max_counterflow_ratio_5m < 0.0:
        raise ValueError("stage1_max_counterflow_ratio_5m must be >= 0")
    if not 0.0 <= params.stage1_max_red_body_share_1m <= 1.0:
        raise ValueError("stage1_max_red_body_share_1m must be in range [0, 1]")
    if params.stage1_max_counterflow_ratio_1m < 0.0:
        raise ValueError("stage1_max_counterflow_ratio_1m must be >= 0")
    if params.stage1_min_pump_pct <= 0.0:
        raise ValueError("stage1_min_pump_pct must be > 0")
    if params.stage1_min_pretrend_range_ratio_2h <= 1.0:
        raise ValueError("stage1_min_pretrend_range_ratio_2h must be > 1")
    if not 0.0 < params.stage1_pre_pump_high_max_fraction_of_leg < 1.0:
        raise ValueError("stage1_pre_pump_high_max_fraction_of_leg must be in range (0, 1)")
    if params.level_cluster_spread_v1 <= 0.0 or params.level_cluster_relaxed_spread_v1 < params.level_cluster_spread_v1:
        raise ValueError("level cluster spread bounds are invalid")
    if params.level_latest_high_max_age_bars < 1:
        raise ValueError("level_latest_high_max_age_bars must be >= 1")
    if params.level_max_age_bars_upper_tf < 1:
        raise ValueError("level_max_age_bars_upper_tf must be >= 1")
    if params.level_touch_tolerance_v1 <= 0.0:
        raise ValueError("level_touch_tolerance_v1 must be > 0")
    if not 0.0 < params.level_min_maturity_fraction <= 1.0:
        raise ValueError("level_min_maturity_fraction must be in range (0, 1]")
    if params.level_rearm_min_distance_v1 <= 0.0:
        raise ValueError("level_rearm_min_distance_v1 must be > 0")
    if params.max_level_touches < 1:
        raise ValueError("max_level_touches must be >= 1")
    if params.min_score <= 0.0 or params.strong_score < params.min_score:
        raise ValueError("score thresholds are invalid")
    if not 0.0 < params.tp1_share < 1.0:
        raise ValueError("tp1_share must be in range (0, 1)")
    if not 0.0 < params.be_arm_to_active_high_fraction <= 1.0:
        raise ValueError("be_arm_to_active_high_fraction must be in range (0, 1]")
    if not 0.0 < params.close_above_be_start_fraction <= 1.0:
        raise ValueError("close_above_be_start_fraction must be in range (0, 1]")
    if params.close_above_be_step_fraction < 0.0 or params.close_above_be_step_fraction > 1.0:
        raise ValueError("close_above_be_step_fraction must be in range [0, 1]")
    if params.close_above_be_step_bars < 1:
        raise ValueError("close_above_be_step_bars must be >= 1")
    if not 0.0 < params.close_above_be_min_fraction <= params.close_above_be_start_fraction:
        raise ValueError("close_above_be_min_fraction must be in range (0, close_above_be_start_fraction]")
    if params.be_buffer_r_fraction < 0.0 or params.be_buffer_r_fraction > 1.0:
        raise ValueError("be_buffer_r_fraction must be in range [0, 1]")
    if params.slip_plan_v1_fraction < 0.0 or params.slip_plan_v1_fraction > 1.0:
        raise ValueError("slip_plan_v1_fraction must be in range [0, 1]")
    if params.min_tick_fraction <= 0.0:
        raise ValueError("min_tick_fraction must be > 0")
    if not 0.0 < params.max_entry_pullback_fraction <= 1.0:
        raise ValueError("max_entry_pullback_fraction must be in range (0, 1]")
    if params.min_entry_rr < 0.0:
        raise ValueError("min_entry_rr must be >= 0")
    if params.max_htf_bars_since_active_high < 0:
        raise ValueError("max_htf_bars_since_active_high must be >= 0")
    if not 0.0 < params.close_above_max_entry_pos <= 1.0:
        raise ValueError("close_above_max_entry_pos must be in range (0, 1]")
    if not 0.0 <= params.close_above_min_entry_pos < params.close_above_max_entry_pos:
        raise ValueError("close_above_min_entry_pos must be in range [0, close_above_max_entry_pos)")
    if not 0.0 < params.close_above_max_pullback_fraction_of_leg <= 1.0:
        raise ValueError("close_above_max_pullback_fraction_of_leg must be in range (0, 1]")
    if not 0.0 <= params.close_above_max_post_high_wick_share <= 1.0:
        raise ValueError("close_above_max_post_high_wick_share must be in range [0, 1]")
    if not 0.0 <= params.close_above_max_active_high_upper_wick_share <= 1.0:
        raise ValueError("close_above_max_active_high_upper_wick_share must be in range [0, 1]")
    if not 0.0 <= params.close_above_min_active_high_close_position <= 1.0:
        raise ValueError("close_above_min_active_high_close_position must be in range [0, 1]")
    if not 0.0 <= params.close_above_min_post_high_alternation_rate <= 1.0:
        raise ValueError("close_above_min_post_high_alternation_rate must be in range [0, 1]")
    if params.close_above_max_level_cross_bars < 1:
        raise ValueError("close_above_max_level_cross_bars must be >= 1")
    if params.close_above_min_level_life_quote_volume_median < 0.0:
        raise ValueError("close_above_min_level_life_quote_volume_median must be >= 0")
    if params.close_above_min_signal_volume_vs_recent < 0.0:
        raise ValueError("close_above_min_signal_volume_vs_recent must be >= 0")
    if params.close_above_min_signal_ema9_slope_3 < 0.0:
        raise ValueError("close_above_min_signal_ema9_slope_3 must be >= 0")


def build_pno_grid() -> list[PnoParams]:
    return [
        PnoParams(symbol="", pno_variant_id="baseline_close", entry_confirmation_mode="close_above"),
    ]


def resolve_pno_category_profiles(
    params: PnoParams,
    *,
    category_mode: str = "all",
) -> tuple[PnoCategoryProfile, ...]:
    if category_mode not in PNO_SUPPORTED_CATEGORY_MODES:
        supported = ", ".join(PNO_SUPPORTED_CATEGORY_MODES)
        raise ValueError(f"pno category_mode must be one of {{{supported}}}, got {category_mode}")
    core_params = replace(
        params,
        pno_variant_id=f"{params.pno_variant_id}__cat_a_core",
        stage1_min_impulse_atr_pre=1.8,
        stage1_min_body_wick_edge=-0.022,
        stage1_max_active_high_upper_wick_share=0.73,
        stage1_max_counterflow_ratio_5m=0.035,
        stage1_max_red_body_share_1m=0.72,
    )
    core_profile = PnoCategoryProfile(
        category_id="cat_a_core",
        label="core",
        priority=1,
        params=core_params,
    )
    if category_mode == "core":
        return (core_profile,)

    # Fixed category 2 profile (current close_above / discovery baseline).
    category2_common = dict(
        pno_variant_id=f"{params.pno_variant_id}__cat_b_category_2",
        stage1_min_cumulative_quote_volume=min(float(params.stage1_min_cumulative_quote_volume), 140_000.0),
        stage1_min_impulse_atr_pre=min(float(params.stage1_min_impulse_atr_pre), 0.95),
        stage1_min_body_wick_edge=min(float(params.stage1_min_body_wick_edge), -0.09),
        stage1_max_active_high_upper_wick_share=max(float(params.stage1_max_active_high_upper_wick_share), 0.84),
        stage1_max_counterflow_ratio_5m=max(float(params.stage1_max_counterflow_ratio_5m), 0.09),
        stage1_max_red_body_share_1m=max(float(params.stage1_max_red_body_share_1m), 0.93),
        stage1_max_micro_flat_bar_share=max(float(params.stage1_max_micro_flat_bar_share), 0.70),
        stage3_max_post_high_wick_share=max(float(params.stage3_max_post_high_wick_share), 0.88),
        stage3_max_post_high_body_overlap_rate=max(float(params.stage3_max_post_high_body_overlap_rate), 0.90),
        stage3_min_post_high_5m_volume_support_fraction=min(
            float(params.stage3_min_post_high_5m_volume_support_fraction),
            0.35,
        ),
        max_entry_pullback_fraction=max(float(params.max_entry_pullback_fraction), 0.75),
    )

    if params.entry_confirmation_mode == "close_above":
        category2_params = replace(
            params,
            **category2_common,
            close_above_max_entry_pos=max(float(params.close_above_max_entry_pos), 0.90),
            close_above_max_post_high_wick_share=max(float(params.close_above_max_post_high_wick_share), 0.95),
            close_above_min_signal_close_position_in_chop=min(
                float(params.close_above_min_signal_close_position_in_chop),
                0.15,
            ),
        )
    else:
        category2_params = replace(params, **category2_common)

    category2_profile = PnoCategoryProfile(
        category_id="cat_b_category_2",
        label="category_2",
        priority=2,
        params=category2_params,
    )

    category3_common = dict(
        pno_variant_id=f"{params.pno_variant_id}__cat_c_category_3",
        stage3_min_post_high_5m_volume_support_fraction=category2_params.stage3_min_post_high_5m_volume_support_fraction,
        stage3_fast_reclaim_min_post_high_5m_volume_support_fraction=max(
            float(params.stage3_fast_reclaim_min_post_high_5m_volume_support_fraction),
            0.30,
        ),
        stage3_fast_reclaim_max_pullback_age_bars=max(int(params.stage3_fast_reclaim_max_pullback_age_bars), 2),
        ideal_like_impulse_enabled=True,
        ideal_like_min_impulse_atr_pre=max(float(params.ideal_like_min_impulse_atr_pre), 14.0),
        ideal_like_min_peak_bar_tr_atr_pre=max(float(params.ideal_like_min_peak_bar_tr_atr_pre), 6.0),
        ideal_like_min_volume_ratio_start=max(float(params.ideal_like_min_volume_ratio_start), 7.0),
        ideal_like_min_path_efficiency=max(float(params.ideal_like_min_path_efficiency), 0.52),
        ideal_like_max_wick_share=min(float(params.ideal_like_max_wick_share), 0.46),
        ideal_like_min_body_share_mean=max(float(params.ideal_like_min_body_share_mean), 0.50),
        ideal_like_min_body_wick_edge=max(float(params.ideal_like_min_body_wick_edge), 0.05),
        ideal_like_max_micro_flat_bar_share=min(float(params.ideal_like_max_micro_flat_bar_share), 0.05),
        ideal_like_max_active_high_upper_wick_share=min(
            float(params.ideal_like_max_active_high_upper_wick_share),
            0.40,
        ),
        ideal_like_max_counterflow_ratio_5m=min(float(params.ideal_like_max_counterflow_ratio_5m), 0.02),
        ideal_like_relaxed_level_maturity_fraction=max(
            float(params.ideal_like_relaxed_level_maturity_fraction),
            0.05,
        ),
        ideal_like_level_latest_high_max_age_bars=max(int(params.ideal_like_level_latest_high_max_age_bars), 60),
        ideal_like_ignore_decay_invalidation=True,
        min_entry_rr=max(float(params.min_entry_rr), 3.0),
    )
    category3_params = replace(category2_params, **category3_common)
    category3_profile = PnoCategoryProfile(
        category_id="cat_c_category_3",
        label="category_3",
        priority=3,
        params=category3_params,
    )

    discovery_profile = PnoCategoryProfile(
        category_id="discovery",
        label="discovery",
        priority=1,
        params=params,
    )
    if category_mode == "discovery":
        return (discovery_profile,)
    return core_profile, category2_profile, category3_profile


def describe_pno_category_profile_set(params: PnoParams, *, category_mode: str = "all") -> str:
    profiles = resolve_pno_category_profiles(params, category_mode=category_mode)
    return "+".join(profile.category_id for profile in profiles)


def with_pno_risk(params: PnoParams, *, deposit: float, risk_pct: float) -> PnoParams:
    effective_risk_pct = min(float(risk_pct), PNO_DEFAULT_RISK_PCT)
    return replace(
        params,
        pno_deposit=deposit,
        pno_risk_pct=effective_risk_pct,
        pno_r_trade=deposit * effective_risk_pct,
    )
