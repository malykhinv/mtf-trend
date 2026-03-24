"""Configuration for the post-pump absorption strategy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil
from typing import Literal

from constants import DEFAULT_BEE_BITE_DEPOSIT, DEFAULT_BEE_BITE_RISK_PCT
from domain.enums.timeframe import Timeframe

PostPumpAbsorptionProfileId = Literal["loose", "balanced", "strict"]

POST_PUMP_ABSORPTION_PROFILE_IDS: tuple[PostPumpAbsorptionProfileId, ...] = (
    "loose",
    "balanced",
    "strict",
)
POST_PUMP_ABSORPTION_DEFAULT_TIMEFRAME = Timeframe.M3
POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M3,
    Timeframe.M5,
)


@dataclass(frozen=True, slots=True)
class PostPumpAbsorptionParams:
    profile_id: PostPumpAbsorptionProfileId
    symbol: str
    levels_timeframe: Timeframe = POST_PUMP_ABSORPTION_DEFAULT_TIMEFRAME
    entry_timeframe: Timeframe = POST_PUMP_ABSORPTION_DEFAULT_TIMEFRAME
    ppa_deposit: float = DEFAULT_BEE_BITE_DEPOSIT
    ppa_risk_pct: float = DEFAULT_BEE_BITE_RISK_PCT
    ppa_r_trade: float | None = None
    atr_window_minutes: int = 42
    pump_window_minutes: int = 18
    pump_baseline_window_minutes: int = 144
    pump_min_move_atr: float = 1.8
    pump_volume_mult: float = 1.15
    range_min_minutes: int = 18
    range_max_minutes: int = 84
    lower_zone_fraction: float = 0.35
    max_range_width_atr: float = 4.5
    max_range_width_pump_fraction: float = 0.75
    taker_ratio_threshold: float = 0.56
    taker_volume_mult: float = 1.20
    flow_baseline_window_minutes: int = 60
    structure_break_minutes: int = 15
    micro_base_minutes: int = 12
    micro_base_max_width_atr: float = 0.75
    entry_break_buffer_atr: float = 0.03
    stop_buffer_atr: float = 0.05
    min_stop_atr: float = 0.12
    max_stop_atr: float = 0.90
    max_stop_range_fraction: float = 0.45
    max_entry_range_fraction: float = 0.50
    tp1_share: float = 0.60
    be_buffer_pct: float = 0.001
    time_exit_minutes: int = 60


@dataclass(frozen=True, slots=True)
class PostPumpAbsorptionRuntime:
    atr_window_bars: int
    pump_window_bars: int
    pump_baseline_window_bars: int
    range_min_bars: int
    range_max_bars: int
    flow_baseline_window_bars: int
    structure_break_lookback_bars: int
    micro_base_bars: int
    time_exit_bars: int


POST_PUMP_ABSORPTION_BASELINES: dict[PostPumpAbsorptionProfileId, PostPumpAbsorptionParams] = {
    "loose": PostPumpAbsorptionParams(
        profile_id="loose",
        symbol="",
        pump_min_move_atr=1.4,
        pump_volume_mult=1.05,
        range_min_minutes=12,
        range_max_minutes=96,
        lower_zone_fraction=0.38,
        max_range_width_atr=5.5,
        max_range_width_pump_fraction=0.85,
        taker_ratio_threshold=0.54,
        taker_volume_mult=1.05,
        structure_break_minutes=12,
        micro_base_minutes=9,
        micro_base_max_width_atr=0.85,
        entry_break_buffer_atr=0.02,
        stop_buffer_atr=0.04,
        min_stop_atr=0.10,
        max_stop_atr=1.00,
        max_stop_range_fraction=0.50,
        max_entry_range_fraction=0.55,
        tp1_share=0.55,
        time_exit_minutes=72,
    ),
    "balanced": PostPumpAbsorptionParams(
        profile_id="balanced",
        symbol="",
    ),
    "strict": PostPumpAbsorptionParams(
        profile_id="strict",
        symbol="",
        pump_min_move_atr=2.0,
        pump_volume_mult=1.25,
        range_min_minutes=24,
        range_max_minutes=72,
        lower_zone_fraction=0.30,
        max_range_width_atr=4.0,
        max_range_width_pump_fraction=0.65,
        taker_ratio_threshold=0.58,
        taker_volume_mult=1.30,
        structure_break_minutes=18,
        micro_base_minutes=15,
        micro_base_max_width_atr=0.60,
        entry_break_buffer_atr=0.04,
        stop_buffer_atr=0.06,
        min_stop_atr=0.14,
        max_stop_atr=0.80,
        max_stop_range_fraction=0.40,
        max_entry_range_fraction=0.45,
        tp1_share=0.65,
        time_exit_minutes=48,
    ),
}


def parse_post_pump_absorption_profile_id(
    raw_value: str | None,
    *,
    default: PostPumpAbsorptionProfileId = "balanced",
) -> PostPumpAbsorptionProfileId:
    profile_id = (raw_value or default).strip().lower()
    if profile_id not in POST_PUMP_ABSORPTION_PROFILE_IDS:
        supported = ", ".join(POST_PUMP_ABSORPTION_PROFILE_IDS)
        raise ValueError(
            f"Invalid POST_PUMP_ABSORPTION_PROFILE: {profile_id}. Supported values: {supported}"
        )
    return profile_id  # type: ignore[return-value]


def validate_post_pump_absorption_params(params: PostPumpAbsorptionParams) -> None:
    if params.entry_timeframe not in POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES:
        supported = ", ".join(timeframe.value for timeframe in POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES)
        raise ValueError(
            "post_pump_absorption supports only entry_timeframe "
            f"in {{{supported}}}, got {params.entry_timeframe.value}"
        )
    if params.levels_timeframe != params.entry_timeframe:
        raise ValueError(
            "post_pump_absorption currently runs in single-timeframe mode: "
            "levels_timeframe must equal entry_timeframe"
        )
    if params.atr_window_minutes < 5 or params.atr_window_minutes > 240:
        raise ValueError("atr_window_minutes must be in range [5, 240]")
    if params.pump_window_minutes < 3 or params.pump_window_minutes > 240:
        raise ValueError("pump_window_minutes must be in range [3, 240]")
    if params.pump_baseline_window_minutes < 20 or params.pump_baseline_window_minutes > 1440:
        raise ValueError("pump_baseline_window_minutes must be in range [20, 1440]")
    if params.pump_min_move_atr <= 0.0 or params.pump_min_move_atr > 10.0:
        raise ValueError("pump_min_move_atr must be in range (0, 10]")
    if params.pump_volume_mult <= 0.0 or params.pump_volume_mult > 5.0:
        raise ValueError("pump_volume_mult must be in range (0, 5]")
    if params.range_min_minutes < 3 or params.range_min_minutes > params.range_max_minutes:
        raise ValueError("range_min_minutes must be >= 3 and <= range_max_minutes")
    if params.range_max_minutes < 4 or params.range_max_minutes > 1440:
        raise ValueError("range_max_minutes must be in range [4, 1440]")
    if not 0.10 <= params.lower_zone_fraction <= 0.60:
        raise ValueError("lower_zone_fraction must be in range [0.10, 0.60]")
    if params.max_range_width_atr <= 0.0 or params.max_range_width_atr > 10.0:
        raise ValueError("max_range_width_atr must be in range (0, 10]")
    if not 0.10 <= params.max_range_width_pump_fraction <= 1.0:
        raise ValueError("max_range_width_pump_fraction must be in range [0.10, 1.0]")
    if not 0.0 <= params.taker_ratio_threshold <= 1.0:
        raise ValueError("taker_ratio_threshold must be in range [0, 1]")
    if params.taker_volume_mult <= 0.0 or params.taker_volume_mult > 5.0:
        raise ValueError("taker_volume_mult must be in range (0, 5]")
    if params.flow_baseline_window_minutes < 5 or params.flow_baseline_window_minutes > 720:
        raise ValueError("flow_baseline_window_minutes must be in range [5, 720]")
    if params.structure_break_minutes < 3 or params.structure_break_minutes > 240:
        raise ValueError("structure_break_minutes must be in range [3, 240]")
    if params.micro_base_minutes < 3 or params.micro_base_minutes > 240:
        raise ValueError("micro_base_minutes must be in range [3, 240]")
    if params.micro_base_max_width_atr <= 0.0 or params.micro_base_max_width_atr > 3.0:
        raise ValueError("micro_base_max_width_atr must be in range (0, 3]")
    if params.entry_break_buffer_atr < 0.0 or params.entry_break_buffer_atr > 1.0:
        raise ValueError("entry_break_buffer_atr must be in range [0, 1]")
    if params.stop_buffer_atr < 0.0 or params.stop_buffer_atr > 1.0:
        raise ValueError("stop_buffer_atr must be in range [0, 1]")
    if params.min_stop_atr <= 0.0 or params.min_stop_atr > 2.0:
        raise ValueError("min_stop_atr must be in range (0, 2]")
    if params.max_stop_atr <= 0.0 or params.max_stop_atr > 5.0:
        raise ValueError("max_stop_atr must be in range (0, 5]")
    if params.max_stop_atr < params.min_stop_atr:
        raise ValueError("max_stop_atr must be >= min_stop_atr")
    if not 0.10 <= params.max_stop_range_fraction <= 1.0:
        raise ValueError("max_stop_range_fraction must be in range [0.10, 1.0]")
    if not 0.10 <= params.max_entry_range_fraction <= 1.0:
        raise ValueError("max_entry_range_fraction must be in range [0.10, 1.0]")
    if not 0.05 <= params.tp1_share <= 0.95:
        raise ValueError("tp1_share must be in range [0.05, 0.95]")
    if params.be_buffer_pct < 0.0 or params.be_buffer_pct > 0.02:
        raise ValueError("be_buffer_pct must be in range [0, 0.02]")
    if params.time_exit_minutes < 2 or params.time_exit_minutes > 1440:
        raise ValueError("time_exit_minutes must be in range [2, 1440]")


def _minutes_to_bars(*, minutes: int, timeframe: Timeframe, minimum: int) -> int:
    timeframe_minutes = max(1, timeframe.to_milliseconds() // 60_000)
    return max(minimum, int(ceil(minutes / timeframe_minutes)))


def build_post_pump_absorption_runtime(params: PostPumpAbsorptionParams) -> PostPumpAbsorptionRuntime:
    timeframe = params.entry_timeframe
    return PostPumpAbsorptionRuntime(
        atr_window_bars=_minutes_to_bars(minutes=params.atr_window_minutes, timeframe=timeframe, minimum=5),
        pump_window_bars=_minutes_to_bars(minutes=params.pump_window_minutes, timeframe=timeframe, minimum=3),
        pump_baseline_window_bars=_minutes_to_bars(
            minutes=params.pump_baseline_window_minutes,
            timeframe=timeframe,
            minimum=20,
        ),
        range_min_bars=_minutes_to_bars(minutes=params.range_min_minutes, timeframe=timeframe, minimum=3),
        range_max_bars=_minutes_to_bars(minutes=params.range_max_minutes, timeframe=timeframe, minimum=4),
        flow_baseline_window_bars=_minutes_to_bars(
            minutes=params.flow_baseline_window_minutes,
            timeframe=timeframe,
            minimum=5,
        ),
        structure_break_lookback_bars=_minutes_to_bars(
            minutes=params.structure_break_minutes,
            timeframe=timeframe,
            minimum=3,
        ),
        micro_base_bars=_minutes_to_bars(minutes=params.micro_base_minutes, timeframe=timeframe, minimum=3),
        time_exit_bars=_minutes_to_bars(minutes=params.time_exit_minutes, timeframe=timeframe, minimum=2),
    )


def build_post_pump_absorption_grid(
    *,
    profile_id: PostPumpAbsorptionProfileId,
) -> list[PostPumpAbsorptionParams]:
    return [POST_PUMP_ABSORPTION_BASELINES[profile_id]]


def with_post_pump_absorption_risk(
    params: PostPumpAbsorptionParams,
    *,
    deposit: float,
    risk_pct: float,
) -> PostPumpAbsorptionParams:
    return replace(
        params,
        ppa_deposit=deposit,
        ppa_risk_pct=risk_pct,
        ppa_r_trade=deposit * risk_pct,
    )
