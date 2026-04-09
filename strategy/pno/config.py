"""Configuration for the PNO strategy."""

from __future__ import annotations

from dataclasses import dataclass, replace

from constants import DEFAULT_BEE_BITE_DEPOSIT, DEFAULT_BEE_BITE_RISK_PCT, DEFAULT_COMMISSION_RATE
from domain.enums.timeframe import Timeframe

PnoTimeframePair = tuple[Timeframe, Timeframe]

PNO_BACKTEST_TIMEFRAME_PAIRS: tuple[PnoTimeframePair, ...] = (
    (Timeframe.M5, Timeframe.M1),
)
PNO_LIVE_TIMEFRAME_PAIRS: tuple[PnoTimeframePair, ...] = (
    (Timeframe.M5, Timeframe.M1),
    (Timeframe.M1, Timeframe.S10),
    (Timeframe.M3, Timeframe.S30),
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
PNO_SUPPORTED_ENTRY_CONFIRMATION_MODES: tuple[str, ...] = ("cross", "close_above")


@dataclass(frozen=True, slots=True)
class PnoParams:
    symbol: str
    pno_variant_id: str = "baseline"
    entry_confirmation_mode: str = "cross"
    levels_timeframe: Timeframe = PNO_DEFAULT_LEVELS_TIMEFRAME
    entry_timeframe: Timeframe = PNO_DEFAULT_ENTRY_TIMEFRAME
    pno_deposit: float = DEFAULT_BEE_BITE_DEPOSIT
    pno_risk_pct: float = DEFAULT_BEE_BITE_RISK_PCT
    pno_r_trade: float | None = None
    fee_rate: float = DEFAULT_COMMISSION_RATE
    min_data_5m: int = 200
    min_data_1m: int = 60
    min_stage1_leg_v1: float = 1.0
    min_stage1_leg_v5_fraction: float = 0.5
    stage1_hold_fraction: float = 0.5
    pullback_min_v1: float = 1.0
    pullback_min_pump_fraction_5m: float = 0.20
    pullback_valid_max_leg_fraction: float = 0.62
    pullback_invalid_max_leg_fraction: float = 0.90
    pullback_valid_max_v5: float = 4.5
    pullback_invalid_max_v5: float = 6.0
    pullback_max_age_bars: int = 12
    stage1_min_cumulative_quote_volume: float = 500_000.0
    stage1_pre_pump_ema_crosses_min: int = 2
    stage1_barcode_max_fraction_1h: float = 0.60
    stage1_barcode_tr_atr_fraction: float = 0.25
    stage1_barcode_tr_price_fraction: float = 0.0010
    stage1_min_impulse_atr_pre: float = 2.5
    stage1_min_peak_bar_tr_atr_pre: float = 1.5
    stage1_min_volume_ratio_start: float = 5.0
    stage1_min_volume_ratio_continue: float = 1.25
    stage1_min_pump_pct: float = 0.015
    stage1_min_pretrend_range_ratio_2h: float = 2.0
    stage1_pre_pump_high_max_fraction_of_leg: float = 0.50
    level_cluster_spread_v1: float = 0.45
    level_cluster_relaxed_spread_v1: float = 0.75
    level_latest_high_max_age_bars: int = 10
    level_touch_tolerance_v1: float = 0.35
    level_low_minor_break_v1: float = 0.35
    level_low_major_break_v1: float = 0.75
    level_min_maturity_fraction: float = 0.25
    level_rearm_min_distance_v1: float = 0.50
    max_level_touches: int = 4
    min_score: float = 70.0
    strong_score: float = 80.0
    slip_plan_v1_fraction: float = 0.10
    min_tick_fraction: float = 0.0001
    max_entry_pullback_fraction: float = 0.50


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
    if params.min_data_5m < 200:
        raise ValueError("min_data_5m must be >= 200")
    if params.min_data_1m < 60:
        raise ValueError("min_data_1m must be >= 60")
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
    if not 0.0 < params.pullback_valid_max_leg_fraction < params.pullback_invalid_max_leg_fraction:
        raise ValueError(
            "pullback_valid_max_leg_fraction must be > 0 and smaller than pullback_invalid_max_leg_fraction"
        )
    if params.pullback_valid_max_v5 <= 0.0 or params.pullback_invalid_max_v5 <= params.pullback_valid_max_v5:
        raise ValueError("pullback_invalid_max_v5 must be > pullback_valid_max_v5 > 0")
    if params.pullback_max_age_bars < 2:
        raise ValueError("pullback_max_age_bars must be >= 2")
    if params.stage1_min_cumulative_quote_volume <= 0.0:
        raise ValueError("stage1_min_cumulative_quote_volume must be > 0")
    if params.stage1_pre_pump_ema_crosses_min < 1:
        raise ValueError("stage1_pre_pump_ema_crosses_min must be >= 1")
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
    if params.stage1_min_volume_ratio_continue <= 0.0:
        raise ValueError("stage1_min_volume_ratio_continue must be > 0")
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
    if params.slip_plan_v1_fraction < 0.0 or params.slip_plan_v1_fraction > 1.0:
        raise ValueError("slip_plan_v1_fraction must be in range [0, 1]")
    if params.min_tick_fraction <= 0.0:
        raise ValueError("min_tick_fraction must be > 0")
    if not 0.0 < params.max_entry_pullback_fraction <= 1.0:
        raise ValueError("max_entry_pullback_fraction must be in range (0, 1]")


def build_pno_grid() -> list[PnoParams]:
    return [
        PnoParams(symbol="", pno_variant_id="baseline_cross", entry_confirmation_mode="cross"),
        PnoParams(symbol="", pno_variant_id="baseline_close", entry_confirmation_mode="close_above"),
    ]


def with_pno_risk(params: PnoParams, *, deposit: float, risk_pct: float) -> PnoParams:
    return replace(
        params,
        pno_deposit=deposit,
        pno_risk_pct=risk_pct,
        pno_r_trade=deposit * risk_pct,
    )
