"""Configuration for anomaly live2 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class AnomalyLive2Config:
    """Minimal v0 configuration for the real-live v2 runtime skeleton."""

    output_dir: Path
    symbols: tuple[str, ...] = ()
    heartbeat_interval_seconds: float = 5.0
    ticker_stale_ms: int = 5_000
    ticker_startup_wait_seconds: float = 10.0
    aggtrade_stale_ms: int = 5_000
    aggtrade_startup_wait_seconds: float = 10.0
    aggtrade_max_streams_per_connection: int = 150
    universe_max_symbols: int = 240
    universe_min_quote_volume_24h: float = 300_000.0
    universe_min_trade_count_24h: int = 1
    decision_timeframe_ms: int = 5_000
    decision_deadline_ms: int = 750
    actionable_min_quote_volume: float = 2_500.0
    actionable_min_trade_count: int = 20
    actionable_min_abs_return_pct: float = 0.003
    runtime_generation: str = "live2_v0"

    def __post_init__(self) -> None:
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be > 0")
        if self.ticker_stale_ms <= 0:
            raise ValueError("ticker_stale_ms must be > 0")
        if self.ticker_startup_wait_seconds < 0:
            raise ValueError("ticker_startup_wait_seconds must be >= 0")
        if self.aggtrade_stale_ms <= 0:
            raise ValueError("aggtrade_stale_ms must be > 0")
        if self.aggtrade_startup_wait_seconds < 0:
            raise ValueError("aggtrade_startup_wait_seconds must be >= 0")
        if self.aggtrade_max_streams_per_connection <= 0:
            raise ValueError("aggtrade_max_streams_per_connection must be > 0")
        if self.universe_max_symbols <= 0:
            raise ValueError("universe_max_symbols must be > 0")
        if self.universe_min_quote_volume_24h < 0:
            raise ValueError("universe_min_quote_volume_24h must be >= 0")
        if self.universe_min_trade_count_24h < 0:
            raise ValueError("universe_min_trade_count_24h must be >= 0")
        if self.decision_timeframe_ms <= 0:
            raise ValueError("decision_timeframe_ms must be > 0")
        if self.decision_deadline_ms <= 0:
            raise ValueError("decision_deadline_ms must be > 0")
        if self.actionable_min_quote_volume < 0:
            raise ValueError("actionable_min_quote_volume must be >= 0")
        if self.actionable_min_trade_count < 0:
            raise ValueError("actionable_min_trade_count must be >= 0")
        if self.actionable_min_abs_return_pct < 0:
            raise ValueError("actionable_min_abs_return_pct must be >= 0")
