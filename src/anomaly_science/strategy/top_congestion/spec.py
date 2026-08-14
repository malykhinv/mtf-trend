"""Frozen contracts for the top-of-pump congestion breakout study.

Setup narrative (the geometry the whole study is built around):

    pump >= MIN_PUMP_PCT from base ──► price STALLS near the high for
    >= MIN_STALL_MINUTES (the "stuck at the top" sideways range) ──►
    inside that range, LOCAL congestions form; the detector emits EVERY
    congestion that sits in the LOWER part of the range (below range mid);
    a human then marks the subset out of which price broke UP to a new
    pump high. Target = new high above the pump peak, stop = under the
    congestion.

Design rules this file encodes (see [[strategy-research-architecture]],
[[archetype-discovery-protocol]]):
  * The detector is OUTCOME-INDEPENDENT. It never uses whether a congestion
    later broke to a new high. That verdict is a human label + a separately
    computed causal outcome, never a detection filter (avoids the
    selection-on-outcome trap that killed [[bee-bite-spring]] and the
    structure_break pilot).
  * Anomaly gate is the user's FINAL trader definition (see
    [[anomaly-definition]]): >=20% from base, activity (volume OR trades)
    >= 10x the 24h baseline, ATR >= 3x, >= 300k USDT turnover.
  * Detection runs on 1m and 5m signal bars off the same enriched_1m cache.

Every threshold here is deliberately a named, tunable field: this is the
one place to adjust the hypothesis before the heavy detector is built on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


PROTOCOL_FREEZE_ID = "top_congestion_break_long_is_20260811_v1"
UNIVERSE_SCHEMA_VERSION = "top_congestion_universe_v1"
MECHANICS_SCHEMA_VERSION = "top_congestion_mechanics_v1"

# IS window mirrors the rest of the desk (enriched_1m cache is IS 2025).
IS_START_MS = int(pd.Timestamp("2025-06-01T00:00:00Z").timestamp() * 1_000)
IS_END_EXCLUSIVE_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000)
MINUTE_MS = 60_000
HOUR_MS = 3_600_000

# Detection timeframes (user request: 1m and 5m). Signal bars are resampled
# from the 1m cache; congestion geometry is measured on the selected TF.
DETECTION_TIMEFRAMES_MINUTES: tuple[int, ...] = (1, 5)


@dataclass(frozen=True, slots=True)
class AnomalyGateConfig:
    """The FINAL trader anomaly definition (pump PERIOD, not per-minute)."""

    # Rise from base to pump peak. Base = low of the last red candle before
    # ignition; kept even if later broken (per [[anomaly-definition]]).
    min_pump_pct: float = 0.20
    # Activity ratio vs the trailing 24h baseline; volume OR trades qualifies.
    baseline_hours: int = 24
    min_activity_ratio: float = 10.0
    # ATR of the ignition window vs the trailing baseline ATR.
    min_atr_ratio: float = 3.0
    atr_window_minutes: int = 60
    # Quote-volume turnover of the pump period, in USDT.
    min_turnover_usdt: float = 300_000.0
    # A pump PERIOD may span 1m..1h; cap how long we let ignition run.
    max_pump_period_minutes: int = 60

    def __post_init__(self) -> None:
        if not 0.0 < self.min_pump_pct < 5.0:
            raise ValueError("min_pump_pct out of range")
        if self.baseline_hours < 1:
            raise ValueError("baseline_hours must be >= 1")
        if self.min_activity_ratio <= 1.0:
            raise ValueError("min_activity_ratio must exceed 1")
        if self.min_atr_ratio <= 1.0:
            raise ValueError("min_atr_ratio must exceed 1")
        if self.min_turnover_usdt <= 0.0:
            raise ValueError("min_turnover_usdt must be positive")


@dataclass(frozen=True, slots=True)
class TopStallConfig:
    """'Stuck at the top for 30+ minutes' — the overall sideways range."""

    # Minimum minutes price must range near the high to qualify as stalled.
    min_stall_minutes: int = 30
    # How close to the pump peak the stall must sit: the stall high must be
    # within this fraction below the peak (0.20 => within 20% of the peak).
    # Loosened from 0.10 on user request (2026-08-11).
    top_band_pct: float = 0.20
    # If price falls more than this below the peak, it has "left the top" and
    # the stall (and any congestion study) ends there.
    # Loosened from 0.25 on user request (2026-08-11).
    max_leave_top_pct: float = 0.40
    # The stall is measured over a rolling window after the peak, up to this
    # many minutes (keeps the range definition bounded for memory/economy).
    max_stall_lookahead_minutes: int = 12 * 60

    def __post_init__(self) -> None:
        if self.min_stall_minutes < 5:
            raise ValueError("min_stall_minutes too small to be a range")
        if not 0.0 < self.top_band_pct < self.max_leave_top_pct:
            raise ValueError("top_band_pct must be positive and below max_leave_top_pct")


@dataclass(frozen=True, slots=True)
class CongestionConfig:
    """Local congestions inside the LOWER part of the overall range."""

    # A congestion is a run of >= this many bars held inside a tight band.
    min_congestion_bars: int = 5
    # Tightness of that band, as a multiple of causal ATR on the signal TF.
    # Loosened from 1.0 on user request (2026-08-11).
    max_band_atr: float = 1.5
    # "Lower part of the range": the congestion's high must sit at or below
    # this fraction of the way from range_low to range_high (0.5 => bottom
    # half; lower values => stricter "floor of the range").
    lower_zone_fraction: float = 0.5
    # ATR window (in signal-TF bars) for the tightness measure.
    atr_window_bars: int = 20

    def __post_init__(self) -> None:
        if self.min_congestion_bars < 3:
            raise ValueError("min_congestion_bars must be >= 3")
        if self.max_band_atr <= 0.0:
            raise ValueError("max_band_atr must be positive")
        if not 0.0 < self.lower_zone_fraction <= 1.0:
            raise ValueError("lower_zone_fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class OutcomeConfig:
    """Causal, outcome-independent labeling of each emitted congestion.

    Computed AFTER detection for study/pool-splitting only; never a filter.
    Target = a new high above the pump peak (перехай). Stop = under the
    congestion low. Both are measured forward from the congestion end with
    no look-ahead into the detection itself.
    """

    # New-high target sits this fraction above the pump peak (0.0 => the peak
    # itself counts as the перехай; small positive values demand a clear break).
    new_high_buffer_pct: float = 0.0
    # Stop sits this fraction below the congestion low.
    stop_buffer_pct: float = 0.0
    # How long we let the outcome resolve before censoring it.
    resolution_horizon_minutes: int = 12 * 60

    def __post_init__(self) -> None:
        if self.new_high_buffer_pct < 0.0 or self.stop_buffer_pct < 0.0:
            raise ValueError("buffers must be non-negative")
        if self.resolution_horizon_minutes <= 0:
            raise ValueError("resolution_horizon_minutes must be positive")


@dataclass(frozen=True, slots=True)
class TopCongestionConfig:
    """Full frozen detection contract for one signal timeframe."""

    timeframe_minutes: int = 1
    anomaly: AnomalyGateConfig = field(default_factory=AnomalyGateConfig)
    stall: TopStallConfig = field(default_factory=TopStallConfig)
    congestion: CongestionConfig = field(default_factory=CongestionConfig)
    outcome: OutcomeConfig = field(default_factory=OutcomeConfig)

    def __post_init__(self) -> None:
        if self.timeframe_minutes not in DETECTION_TIMEFRAMES_MINUTES:
            raise ValueError(
                f"timeframe_minutes must be one of {DETECTION_TIMEFRAMES_MINUTES}"
            )
