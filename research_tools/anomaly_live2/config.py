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
