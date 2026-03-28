"""Configuration for hourly Asia-session pump research."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Literal, cast

from domain.enums.timeframe import Timeframe

HourlyAsiaPumpProfileId = Literal["loose", "balanced", "strict"]
HourlyAsiaPumpTradeEntryStyle = Literal[
    "break_trigger_high",
    "pullback_reclaim",
    "pressure_reclaim",
    "flag_break",
]
HourlyAsiaPumpTradeTrailStyle = Literal["prev_bar_low", "last_red_low"]
HourlyAsiaPumpTradeInitialStopStyle = Literal["trigger_body_mid", "trigger_low", "pattern_low"]

HOURLY_ASIA_PUMP_SUPPORTED_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M3,
    Timeframe.M5,
)
DEFAULT_ASIA_START_HOUR_UTC = 0
DEFAULT_ASIA_END_HOUR_UTC = 9
DEFAULT_TRIGGER_MINUTE = 0
DEFAULT_ATR_WINDOW_MINUTES = 180
DEFAULT_VOLUME_WINDOW_MINUTES = 720
DEFAULT_BREAKOUT_LOOKBACK_MINUTES = 60
DEFAULT_MAX_FOLLOW_MINUTES = 720

GRID_MIN_RANGE_ATR_VALUES: tuple[float, ...] = (2.0, 2.5, 3.0)
GRID_MIN_BODY_ATR_VALUES: tuple[float, ...] = (1.25, 1.5, 2.0)
GRID_MIN_VOLUME_MULT_VALUES: tuple[float, ...] = (2.0, 2.5, 3.0)
GRID_MAX_CLOSE_TO_HIGH_FRAC_VALUES: tuple[float, ...] = (0.25, 0.20)
GRID_MIN_BREAKOUT_PCT_VALUES: tuple[float, ...] = (0.0,)

_PROFILE_THRESHOLDS: dict[HourlyAsiaPumpProfileId, tuple[float, float, float, float, float]] = {
    "loose": (2.0, 1.25, 2.0, 0.25, 0.0),
    "balanced": (2.5, 1.5, 2.5, 0.25, 0.0),
    "strict": (3.0, 2.0, 3.0, 0.20, 0.0),
}


@dataclass(frozen=True, slots=True)
class HourlyAsiaPumpParams:
    grid_id: str
    profile_id: HourlyAsiaPumpProfileId | None
    timeframe: Timeframe
    asia_start_hour_utc: int
    asia_end_hour_utc: int
    trigger_minute: int
    atr_window_minutes: int
    volume_window_minutes: int
    breakout_lookback_minutes: int
    max_follow_minutes: int
    min_range_atr: float
    min_body_atr: float
    min_volume_mult: float
    max_close_to_high_frac: float
    min_breakout_pct: float


@dataclass(frozen=True, slots=True)
class HourlyAsiaPumpTradeModel:
    model_id: str
    label: str
    entry_style: HourlyAsiaPumpTradeEntryStyle
    trail_style: HourlyAsiaPumpTradeTrailStyle
    initial_stop_style: HourlyAsiaPumpTradeInitialStopStyle
    min_trigger_return_pct: float
    min_range_atr: float
    min_body_atr: float
    min_volume_mult: float
    max_close_to_high_frac: float
    max_entry_bars: int
    max_pullback_frac: float
    pullback_volume_frac: float
    flag_bars: int
    flag_max_range_frac: float
    partial_take_pct: float
    partial_take_r: float
    partial_fraction: float
    move_stop_to_be_after_partial: bool
    trail_activation_pct: float
    fast_fail_bars: int
    fast_fail_min_return_pct: float
    max_hold_minutes: int


def parse_hourly_asia_pump_profile_id(
    raw_value: str | None,
    *,
    default: HourlyAsiaPumpProfileId = "balanced",
) -> HourlyAsiaPumpProfileId:
    if raw_value is None:
        return default
    normalized = str(raw_value).strip().lower()
    if normalized not in _PROFILE_THRESHOLDS:
        supported = ", ".join(sorted(_PROFILE_THRESHOLDS))
        raise ValueError(f"Unsupported hourly Asia pump profile: {raw_value}. Supported: {supported}")
    return cast(HourlyAsiaPumpProfileId, normalized)


def build_hourly_asia_pump_profile(
    *,
    timeframe: Timeframe,
    profile_id: HourlyAsiaPumpProfileId,
    asia_start_hour_utc: int = DEFAULT_ASIA_START_HOUR_UTC,
    asia_end_hour_utc: int = DEFAULT_ASIA_END_HOUR_UTC,
    trigger_minute: int = DEFAULT_TRIGGER_MINUTE,
    max_follow_minutes: int = DEFAULT_MAX_FOLLOW_MINUTES,
) -> HourlyAsiaPumpParams:
    min_range_atr, min_body_atr, min_volume_mult, max_close_to_high_frac, min_breakout_pct = _PROFILE_THRESHOLDS[profile_id]
    return HourlyAsiaPumpParams(
        grid_id=profile_id,
        profile_id=profile_id,
        timeframe=timeframe,
        asia_start_hour_utc=asia_start_hour_utc,
        asia_end_hour_utc=asia_end_hour_utc,
        trigger_minute=trigger_minute,
        atr_window_minutes=DEFAULT_ATR_WINDOW_MINUTES,
        volume_window_minutes=DEFAULT_VOLUME_WINDOW_MINUTES,
        breakout_lookback_minutes=DEFAULT_BREAKOUT_LOOKBACK_MINUTES,
        max_follow_minutes=max_follow_minutes,
        min_range_atr=min_range_atr,
        min_body_atr=min_body_atr,
        min_volume_mult=min_volume_mult,
        max_close_to_high_frac=max_close_to_high_frac,
        min_breakout_pct=min_breakout_pct,
    )


def build_hourly_asia_pump_grid(
    *,
    timeframe: Timeframe,
    asia_start_hour_utc: int = DEFAULT_ASIA_START_HOUR_UTC,
    asia_end_hour_utc: int = DEFAULT_ASIA_END_HOUR_UTC,
    trigger_minute: int = DEFAULT_TRIGGER_MINUTE,
    max_follow_minutes: int = DEFAULT_MAX_FOLLOW_MINUTES,
) -> list[HourlyAsiaPumpParams]:
    profile_by_thresholds = {
        thresholds: profile_id
        for profile_id, thresholds in _PROFILE_THRESHOLDS.items()
    }
    params: list[HourlyAsiaPumpParams] = []
    for min_range_atr, min_body_atr, min_volume_mult, max_close_to_high_frac, min_breakout_pct in product(
        GRID_MIN_RANGE_ATR_VALUES,
        GRID_MIN_BODY_ATR_VALUES,
        GRID_MIN_VOLUME_MULT_VALUES,
        GRID_MAX_CLOSE_TO_HIGH_FRAC_VALUES,
        GRID_MIN_BREAKOUT_PCT_VALUES,
    ):
        thresholds = (
            float(min_range_atr),
            float(min_body_atr),
            float(min_volume_mult),
            float(max_close_to_high_frac),
            float(min_breakout_pct),
        )
        profile_id = profile_by_thresholds.get(thresholds)
        grid_id = (
            f"ra{min_range_atr:.2f}_"
            f"ba{min_body_atr:.2f}_"
            f"vm{min_volume_mult:.2f}_"
            f"ch{max_close_to_high_frac:.2f}_"
            f"bp{min_breakout_pct:.3f}"
        ).replace(".", "p")
        params.append(
            HourlyAsiaPumpParams(
                grid_id=grid_id,
                profile_id=profile_id,
                timeframe=timeframe,
                asia_start_hour_utc=asia_start_hour_utc,
                asia_end_hour_utc=asia_end_hour_utc,
                trigger_minute=trigger_minute,
                atr_window_minutes=DEFAULT_ATR_WINDOW_MINUTES,
                volume_window_minutes=DEFAULT_VOLUME_WINDOW_MINUTES,
                breakout_lookback_minutes=DEFAULT_BREAKOUT_LOOKBACK_MINUTES,
                max_follow_minutes=max_follow_minutes,
                min_range_atr=float(min_range_atr),
                min_body_atr=float(min_body_atr),
                min_volume_mult=float(min_volume_mult),
                max_close_to_high_frac=float(max_close_to_high_frac),
                min_breakout_pct=float(min_breakout_pct),
            )
        )
    return sorted(params, key=lambda item: item.grid_id)


def build_hourly_asia_pump_trade_models() -> list[HourlyAsiaPumpTradeModel]:
    return [
        HourlyAsiaPumpTradeModel(
            model_id="aggr_break_fast",
            label="Aggressive Break Fast",
            entry_style="break_trigger_high",
            trail_style="prev_bar_low",
            initial_stop_style="trigger_body_mid",
            min_trigger_return_pct=0.02,
            min_range_atr=2.5,
            min_body_atr=1.5,
            min_volume_mult=2.5,
            max_close_to_high_frac=0.20,
            max_entry_bars=2,
            max_pullback_frac=0.0,
            pullback_volume_frac=1.0,
            flag_bars=0,
            flag_max_range_frac=0.0,
            partial_take_pct=0.02,
            partial_take_r=1.5,
            partial_fraction=0.35,
            move_stop_to_be_after_partial=True,
            trail_activation_pct=0.0,
            fast_fail_bars=2,
            fast_fail_min_return_pct=0.005,
            max_hold_minutes=180,
        ),
        HourlyAsiaPumpTradeModel(
            model_id="pullback_reclaim",
            label="Pullback Reclaim",
            entry_style="pullback_reclaim",
            trail_style="prev_bar_low",
            initial_stop_style="pattern_low",
            min_trigger_return_pct=0.015,
            min_range_atr=2.5,
            min_body_atr=1.5,
            min_volume_mult=2.5,
            max_close_to_high_frac=0.25,
            max_entry_bars=4,
            max_pullback_frac=0.45,
            pullback_volume_frac=1.0,
            flag_bars=0,
            flag_max_range_frac=0.0,
            partial_take_pct=0.025,
            partial_take_r=2.0,
            partial_fraction=0.5,
            move_stop_to_be_after_partial=True,
            trail_activation_pct=0.0,
            fast_fail_bars=3,
            fast_fail_min_return_pct=0.003,
            max_hold_minutes=240,
        ),
        HourlyAsiaPumpTradeModel(
            model_id="pressure_reclaim",
            label="Pressure Reclaim",
            entry_style="pressure_reclaim",
            trail_style="last_red_low",
            initial_stop_style="pattern_low",
            min_trigger_return_pct=0.02,
            min_range_atr=2.75,
            min_body_atr=1.75,
            min_volume_mult=3.0,
            max_close_to_high_frac=0.20,
            max_entry_bars=4,
            max_pullback_frac=0.40,
            pullback_volume_frac=0.70,
            flag_bars=0,
            flag_max_range_frac=0.0,
            partial_take_pct=0.03,
            partial_take_r=2.0,
            partial_fraction=0.4,
            move_stop_to_be_after_partial=True,
            trail_activation_pct=0.0,
            fast_fail_bars=3,
            fast_fail_min_return_pct=0.004,
            max_hold_minutes=240,
        ),
        HourlyAsiaPumpTradeModel(
            model_id="tight_flag_runner",
            label="Tight Flag Runner",
            entry_style="flag_break",
            trail_style="prev_bar_low",
            initial_stop_style="pattern_low",
            min_trigger_return_pct=0.015,
            min_range_atr=2.5,
            min_body_atr=1.5,
            min_volume_mult=2.5,
            max_close_to_high_frac=0.20,
            max_entry_bars=2,
            max_pullback_frac=0.35,
            pullback_volume_frac=1.0,
            flag_bars=2,
            flag_max_range_frac=0.60,
            partial_take_pct=0.03,
            partial_take_r=2.0,
            partial_fraction=0.35,
            move_stop_to_be_after_partial=True,
            trail_activation_pct=0.0,
            fast_fail_bars=2,
            fast_fail_min_return_pct=0.004,
            max_hold_minutes=240,
        ),
        HourlyAsiaPumpTradeModel(
            model_id="monster_break_3pct",
            label="Monster Break 3pct",
            entry_style="break_trigger_high",
            trail_style="last_red_low",
            initial_stop_style="trigger_low",
            min_trigger_return_pct=0.03,
            min_range_atr=3.0,
            min_body_atr=2.0,
            min_volume_mult=3.0,
            max_close_to_high_frac=0.20,
            max_entry_bars=2,
            max_pullback_frac=0.0,
            pullback_volume_frac=1.0,
            flag_bars=0,
            flag_max_range_frac=0.0,
            partial_take_pct=0.03,
            partial_take_r=1.5,
            partial_fraction=0.30,
            move_stop_to_be_after_partial=True,
            trail_activation_pct=0.03,
            fast_fail_bars=2,
            fast_fail_min_return_pct=0.0075,
            max_hold_minutes=360,
        ),
        HourlyAsiaPumpTradeModel(
            model_id="monster_break_5pct",
            label="Monster Break 5pct",
            entry_style="break_trigger_high",
            trail_style="last_red_low",
            initial_stop_style="trigger_low",
            min_trigger_return_pct=0.05,
            min_range_atr=3.0,
            min_body_atr=2.0,
            min_volume_mult=3.0,
            max_close_to_high_frac=0.20,
            max_entry_bars=2,
            max_pullback_frac=0.0,
            pullback_volume_frac=1.0,
            flag_bars=0,
            flag_max_range_frac=0.0,
            partial_take_pct=0.05,
            partial_take_r=2.0,
            partial_fraction=0.25,
            move_stop_to_be_after_partial=True,
            trail_activation_pct=0.05,
            fast_fail_bars=2,
            fast_fail_min_return_pct=0.01,
            max_hold_minutes=480,
        ),
        HourlyAsiaPumpTradeModel(
            model_id="super_monster_7p5pct",
            label="Super Monster 7.5pct",
            entry_style="break_trigger_high",
            trail_style="last_red_low",
            initial_stop_style="trigger_low",
            min_trigger_return_pct=0.075,
            min_range_atr=4.0,
            min_body_atr=3.0,
            min_volume_mult=5.0,
            max_close_to_high_frac=0.15,
            max_entry_bars=1,
            max_pullback_frac=0.0,
            pullback_volume_frac=1.0,
            flag_bars=0,
            flag_max_range_frac=0.0,
            partial_take_pct=0.0,
            partial_take_r=0.0,
            partial_fraction=0.0,
            move_stop_to_be_after_partial=False,
            trail_activation_pct=0.05,
            fast_fail_bars=2,
            fast_fail_min_return_pct=0.015,
            max_hold_minutes=720,
        ),
        HourlyAsiaPumpTradeModel(
            model_id="super_monster_10pct",
            label="Super Monster 10pct",
            entry_style="break_trigger_high",
            trail_style="last_red_low",
            initial_stop_style="trigger_low",
            min_trigger_return_pct=0.10,
            min_range_atr=4.0,
            min_body_atr=3.0,
            min_volume_mult=5.0,
            max_close_to_high_frac=0.15,
            max_entry_bars=1,
            max_pullback_frac=0.0,
            pullback_volume_frac=1.0,
            flag_bars=0,
            flag_max_range_frac=0.0,
            partial_take_pct=0.0,
            partial_take_r=0.0,
            partial_fraction=0.0,
            move_stop_to_be_after_partial=False,
            trail_activation_pct=0.075,
            fast_fail_bars=2,
            fast_fail_min_return_pct=0.02,
            max_hold_minutes=720,
        ),
    ]
