"""Stream rate limit configuration for Binance streaming components."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class CommandWindow:
    """Represents a single rate window for command dispatch."""

    window_seconds: float
    command_limit: int


@dataclass(frozen=True)
class StreamLoadProfile:
    """Describes how a stream should be scheduled across websocket workers."""

    max_streams_per_connection: int
    steady: CommandWindow
    burst: CommandWindow
    minimum_command_reserve: int


DEFAULT_BINANCE_STREAM_PROFILES: Mapping[str, StreamLoadProfile] = {
    "depth": StreamLoadProfile(
        max_streams_per_connection=180,
        steady=CommandWindow(window_seconds=1.0, command_limit=5),
        burst=CommandWindow(window_seconds=0.25, command_limit=2),
        minimum_command_reserve=1,
    ),
    "trades": StreamLoadProfile(
        max_streams_per_connection=220,
        steady=CommandWindow(window_seconds=1.0, command_limit=5),
        burst=CommandWindow(window_seconds=0.5, command_limit=3),
        minimum_command_reserve=1,
    ),
    "book": StreamLoadProfile(
        max_streams_per_connection=240,
        steady=CommandWindow(window_seconds=1.0, command_limit=5),
        burst=CommandWindow(window_seconds=0.5, command_limit=3),
        minimum_command_reserve=1,
    ),
}


__all__ = [
    "CommandWindow",
    "StreamLoadProfile",
    "DEFAULT_BINANCE_STREAM_PROFILES",
]
