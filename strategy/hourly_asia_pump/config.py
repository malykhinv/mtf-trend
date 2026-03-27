"""Configuration for hourly Asia-session pump research."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Literal, cast

from domain.enums.timeframe import Timeframe

HourlyAsiaPumpProfileId = Literal["loose", "balanced", "strict"]

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
GRID_MIN_BREAKOUT_PCT_VALUES: tuple[float, ...] = (0.001, 0.002, 0.003)

_PROFILE_THRESHOLDS: dict[HourlyAsiaPumpProfileId, tuple[float, float, float, float, float]] = {
    "loose": (2.0, 1.25, 2.0, 0.25, 0.001),
    "balanced": (2.5, 1.5, 2.5, 0.25, 0.002),
    "strict": (3.0, 2.0, 3.0, 0.20, 0.003),
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
