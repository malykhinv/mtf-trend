"""Canonical minimal dark theme for research charts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChartTheme:
    figure_face: str = "#08111f"
    axis_face: str = "#0f172a"
    grid: str = "#334155"
    text: str = "#dbe4f0"
    muted: str = "#94a3b8"
    panel_edge: str = "#1e293b"
    up: str = "#22c55e"
    down: str = "#f97316"
    ema_fast: str = "#f59e0b"
    ema_slow: str = "#38bdf8"
    swing_high: str = "#fb7185"
    swing_low: str = "#c084fc"
    entry: str = "#38bdf8"
    exit: str = "#fde047"
    stop: str = "#ef4444"
    target: str = "#22c55e"
    zone_edge: str = "#34d399"
    zone_face: str = "#064e3b"


DEFAULT_THEME = ChartTheme()


__all__ = ["ChartTheme", "DEFAULT_THEME"]
