"""Strategy-neutral immutable chart input contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np


@dataclass(frozen=True, slots=True)
class CandleSeries:
    timestamps_ms: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def __post_init__(self) -> None:
        lengths = {len(self.timestamps_ms), len(self.open), len(self.high), len(self.low), len(self.close)}
        if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
            raise ValueError("candle arrays must be non-empty and equally sized")
        if np.any(np.diff(self.timestamps_ms.astype(np.int64)) <= 0):
            raise ValueError("candle timestamps must be strictly increasing")


@dataclass(frozen=True, slots=True)
class LineSeries:
    label: str
    values: np.ndarray
    color: str
    linewidth: float = 1.0
    alpha: float = 0.9


@dataclass(frozen=True, slots=True)
class SwingPoint:
    timestamp_ms: int
    price: float
    kind: Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class HorizontalLevel:
    price: float
    color: str
    label: str = ""
    start_timestamp_ms: int | None = None
    end_timestamp_ms: int | None = None
    linestyle: str = "--"
    linewidth: float = 1.0
    alpha: float = 0.9


@dataclass(frozen=True, slots=True)
class PriceZone:
    low: float
    high: float
    start_timestamp_ms: int
    end_timestamp_ms: int
    edge_color: str
    face_color: str
    label: str = ""
    alpha: float = 0.16


@dataclass(frozen=True, slots=True)
class PointMarker:
    timestamp_ms: int
    price: float
    color: str
    label: str = ""
    marker: str = "o"
    size: float = 38.0


@dataclass(frozen=True, slots=True)
class HistogramSeries:
    label: str
    values: np.ndarray
    color: str
    alpha: float = 0.75
    normalize: bool = False


@dataclass(frozen=True, slots=True)
class ChartSpec:
    candles: CandleSeries
    title: str = ""
    subtitle_lines: tuple[str, ...] = ()
    lines: tuple[LineSeries, ...] = ()
    swings: tuple[SwingPoint, ...] = ()
    levels: tuple[HorizontalLevel, ...] = ()
    zones: tuple[PriceZone, ...] = ()
    markers: tuple[PointMarker, ...] = ()
    histograms: tuple[HistogramSeries, ...] = ()
    histogram_hsegments: tuple[tuple[float, float, float, str], ...] = ()  # (raw value, x0, x1, color) on the histogram panel
    metadata: dict[str, object] = field(default_factory=dict)


__all__ = [
    "CandleSeries",
    "ChartSpec",
    "HistogramSeries",
    "HorizontalLevel",
    "LineSeries",
    "PointMarker",
    "PriceZone",
    "SwingPoint",
]
