from __future__ import annotations

import numpy as np

from anomaly_science.visualization import (
    CandleSeries,
    ChartSpec,
    HistogramSeries,
    HorizontalLevel,
    LineSeries,
    PointMarker,
    PriceZone,
    SwingPoint,
    render_chart,
)


def test_renderer_accepts_only_generic_market_primitives(tmp_path) -> None:
    timestamps = np.arange(8, dtype=np.int64) * 60_000 + 1_700_000_000_000
    open_ = np.array([10.0, 10.2, 10.1, 10.4, 10.3, 10.6, 10.5, 10.8])
    close = np.array([10.2, 10.1, 10.4, 10.3, 10.6, 10.5, 10.8, 10.7])
    high = np.maximum(open_, close) + 0.1
    low = np.minimum(open_, close) - 0.1
    output = tmp_path / "generic.png"
    spec = ChartSpec(
        candles=CandleSeries(timestamps, open_, high, low, close),
        title="generic",
        lines=(LineSeries("EMA", close, "#f59e0b"),),
        swings=(SwingPoint(int(timestamps[2]), float(high[2]), "high"),),
        levels=(HorizontalLevel(10.5, "#22c55e", "level"),),
        zones=(PriceZone(10.1, 10.4, int(timestamps[1]), int(timestamps[5]), "#34d399", "#064e3b"),),
        markers=(PointMarker(int(timestamps[4]), float(close[4]), "#38bdf8"),),
        histograms=(HistogramSeries("activity", np.arange(8), "#38bdf8", normalize=True),),
    )

    result = render_chart(spec, output)

    assert result == output
    assert output.stat().st_size > 1_000


def test_candle_series_rejects_misaligned_inputs() -> None:
    timestamps = np.array([1, 2], dtype=np.int64)

    try:
        CandleSeries(timestamps, np.ones(2), np.ones(2), np.ones(2), np.ones(1))
    except ValueError as exc:
        assert "equally sized" in str(exc)
    else:
        raise AssertionError("misaligned candles must be rejected")
