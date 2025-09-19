from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bot.domain.enums import Exchange, Side, Timeframe
from bot.domain.models.entities import Candle, ThresholdMetric, Thresholds
from bot.domain.services.metrics_service import MetricsService
from bot.domain.services.selector_service import SignalSelectorService


def _build_candle_sequence(
    *,
    count: int,
    final_open: float,
    final_close: float,
    final_high: float,
    final_low: float,
    base_volume: float,
    final_volume: float,
    timeframe: Timeframe = Timeframe.M15,
) -> list[Candle]:
    base_time = datetime(2024, 1, 1)
    minutes_map = {
        Timeframe.M1: 1,
        Timeframe.M3: 3,
        Timeframe.M5: 5,
        Timeframe.M15: 15,
    }
    step_minutes = minutes_map[timeframe]
    candles: list[Candle] = []
    for index in range(count - 1):
        candles.append(
            Candle(
                symbol="TESTUSDT",
                exchange=Exchange.BINANCE,
                timeframe=timeframe,
                open=100.0,
                high=102.0,
                low=98.0,
                close=100.0,
                volume=base_volume,
                started_at=base_time + timedelta(minutes=step_minutes * index),
                closed_at=base_time + timedelta(minutes=step_minutes * (index + 1)),
            )
        )
    candles.append(
        Candle(
            symbol="TESTUSDT",
            exchange=Exchange.BINANCE,
            timeframe=timeframe,
            open=final_open,
            high=final_high,
            low=final_low,
            close=final_close,
            volume=final_volume,
            started_at=base_time + timedelta(minutes=step_minutes * (count - 1)),
            closed_at=base_time + timedelta(minutes=step_minutes * count),
        )
    )
    return candles


def _default_thresholds() -> Thresholds:
    return Thresholds(
        min_relative_volume=40.0,
        max_relative_volume=200.0,
        min_atr_mult=5.0,
        min_pct_move=5.0,
        max_pct_move=10.0,
        max_upper_wick_pct=75.0,
        max_lower_wick_pct=50.0,
        short_pct_move_ranges=[(3.0, 5.0), (10.0, None)],
        short_relative_volume_ranges=[(None, 40.0), (200.0, None)],
        allow_long=True,
        allow_short=True,
        metrics=[ThresholdMetric(name="atr", min_value=5.0)],
    )


@pytest.fixture()
def selector() -> SignalSelectorService:
    service = MetricsService(atr_period=14, volume_period=20, momentum_period=5)
    return SignalSelectorService(service)


def test_select_allows_long_when_thresholds_met(selector: SignalSelectorService) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=106.0,
        final_high=118.0,
        final_low=88.0,
        base_volume=2_000_000.0,
        final_volume=120_000_000.0,
    )
    thresholds = _default_thresholds()

    result = selector.select("TESTUSDT", candles, thresholds)

    assert not result.rejected
    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.side is Side.LONG
    assert signal.metrics_snapshot is not None
    assert signal.metrics_snapshot.pct_move == pytest.approx(6.0)
    assert "metrics" not in signal.metadata


def test_select_redirects_to_short_when_long_body_growth_too_small(
    selector: SignalSelectorService,
) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=104.0,
        final_high=118.0,
        final_low=88.0,
        base_volume=2_000_000.0,
        final_volume=440_000_000.0,
    )
    thresholds = _default_thresholds()

    result = selector.select("TESTUSDT", candles, thresholds)

    assert len(result.signals) == 1
    assert result.signals[0].side is Side.SHORT


def test_select_allows_short_when_thresholds_met(selector: SignalSelectorService) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=104.0,
        final_high=140.0,
        final_low=86.0,
        base_volume=2_000_000.0,
        final_volume=440_000_000.0,
    )
    thresholds = _default_thresholds()

    result = selector.select("TESTUSDT", candles, thresholds)

    assert len(result.signals) == 1
    assert result.signals[0].side is Side.SHORT


def test_select_allows_short_when_pct_move_in_high_range(
    selector: SignalSelectorService,
) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=112.0,
        final_high=130.0,
        final_low=99.0,
        base_volume=2_000_000.0,
        final_volume=440_000_000.0,
    )
    thresholds = _default_thresholds()

    result = selector.select("TESTUSDT", candles, thresholds)

    assert not result.rejected
    assert len(result.signals) == 1
    assert result.signals[0].side is Side.SHORT


def test_select_rejects_short_outside_pct_move_ranges(
    selector: SignalSelectorService,
) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=106.0,
        final_high=112.0,
        final_low=82.0,
        base_volume=2_000_000.0,
        final_volume=120_000_000.0,
    )
    thresholds = _default_thresholds()
    thresholds.allow_long = False

    result = selector.select("TESTUSDT", candles, thresholds)

    assert not result.signals
    assert "short_pct_move" in result.rejected


def test_select_rejects_short_with_non_positive_body(
    selector: SignalSelectorService,
) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=94.0,
        final_high=112.0,
        final_low=82.0,
        base_volume=2_000_000.0,
        final_volume=440_000_000.0,
    )
    thresholds = _default_thresholds()

    result = selector.select("TESTUSDT", candles, thresholds)

    assert not result.signals
    assert "short_non_positive_body" in result.rejected


def test_select_short_uses_low_volume_range(selector: SignalSelectorService) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=112.0,
        final_high=130.0,
        final_low=99.0,
        base_volume=2_000_000.0,
        final_volume=60_000_000.0,
    )
    thresholds = _default_thresholds()

    result = selector.select("TESTUSDT", candles, thresholds)

    assert len(result.signals) == 1
    assert result.signals[0].side is Side.SHORT


def test_select_short_rejects_mid_volume_band(selector: SignalSelectorService) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=112.0,
        final_high=130.0,
        final_low=99.0,
        base_volume=2_000_000.0,
        final_volume=200_000_000.0,
    )
    thresholds = _default_thresholds()
    thresholds.allow_long = False

    result = selector.select("TESTUSDT", candles, thresholds)

    assert not result.signals
    assert "short_relative_volume" in result.rejected


def test_select_short_uses_high_volume_range(selector: SignalSelectorService) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=112.0,
        final_high=130.0,
        final_low=99.0,
        base_volume=2_000_000.0,
        final_volume=440_000_000.0,
    )
    thresholds = _default_thresholds()
    thresholds.allow_long = False

    result = selector.select("TESTUSDT", candles, thresholds)

    assert len(result.signals) == 1
    assert result.signals[0].side is Side.SHORT


def test_select_includes_timeframe_metadata(selector: SignalSelectorService) -> None:
    candles = _build_candle_sequence(
        count=20,
        final_open=100.0,
        final_close=112.0,
        final_high=130.0,
        final_low=95.0,
        base_volume=2_000_000.0,
        final_volume=440_000_000.0,
        timeframe=Timeframe.M5,
    )
    thresholds = _default_thresholds()

    result = selector.select("TESTUSDT", candles, thresholds)

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.timeframe is Timeframe.M5
    assert signal.metadata["timeframe"] == Timeframe.M5.value
