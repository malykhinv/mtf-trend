"""Strategy-neutral scientific chart rendering."""

from anomaly_science.visualization.contracts import (
    CandleSeries,
    ChartSpec,
    HistogramSeries,
    HorizontalLevel,
    LineSeries,
    PointMarker,
    PriceZone,
    SwingPoint,
)
from anomaly_science.visualization.renderer import render_chart
from anomaly_science.visualization.theme import ChartTheme, DEFAULT_THEME

__all__ = [
    "CandleSeries",
    "ChartSpec",
    "ChartTheme",
    "DEFAULT_THEME",
    "HistogramSeries",
    "HorizontalLevel",
    "LineSeries",
    "PointMarker",
    "PriceZone",
    "SwingPoint",
    "render_chart",
]
