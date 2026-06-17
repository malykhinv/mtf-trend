from __future__ import annotations

import pytest

from anomaly_science.contracts.market import Candle1m
from anomaly_science.future.atr import AtrComputationError, compute_atr_1d_asof

BASE_TS = 1_704_067_200_000


def _candle(index: int, *, close: float = 100.0, high: float | None = None, low: float | None = None) -> Candle1m:
    open_time_ms = BASE_TS + index * 60_000
    candle_high = high if high is not None else close + 1.0
    candle_low = low if low is not None else close - 1.0
    return Candle1m(
        symbol="AAA/USDT:USDT",
        open_time_ms=open_time_ms,
        available_time_ms=open_time_ms + 60_000,
        open=close,
        high=candle_high,
        low=candle_low,
        close=close,
        volume=1.0,
        quote_volume=100.0,
        number_of_trades=10.0,
        taker_buy_quote_volume=50.0,
    )


def test_atr_asof_uses_last_closed_candles_only() -> None:
    candles = [
        _candle(0, close=100.0, high=100.5, low=99.5),
        _candle(1, close=102.0, high=105.0, low=101.0),
        _candle(2, close=101.0, high=103.0, low=99.0),
        _candle(3, close=103.0, high=104.0, low=100.0),
        _candle(4, close=500.0, high=700.0, low=1.0),  # future relative to snapshot
    ]
    snapshot_time_ms = candles[3].available_time_ms

    result = compute_atr_1d_asof(
        candles_1m=candles,
        symbol="AAA/USDT:USDT",
        snapshot_time_ms=snapshot_time_ms,
        atr_window_minutes=3,
    )

    expected_true_ranges = [5.0, 4.0, 4.0]
    expected_atr = sum(expected_true_ranges) / 3.0
    assert result.atr_1d_asof_t == pytest.approx(expected_atr)
    assert result.atr_1d_pct_asof_t == pytest.approx(expected_atr / 103.0)
    assert result.source_candle_count == 3
    assert result.first_candle_available_time_ms == candles[1].available_time_ms
    assert result.last_candle_available_time_ms == candles[3].available_time_ms
    assert result.last_candle_available_time_ms <= snapshot_time_ms


def test_mutating_future_candle_does_not_change_atr() -> None:
    candles = [
        _candle(0, close=100.0),
        _candle(1, close=101.0),
        _candle(2, close=102.0),
        _candle(3, close=103.0),
    ]
    snapshot_time_ms = candles[2].available_time_ms
    mutated = list(candles)
    mutated[3] = _candle(3, close=400.0, high=800.0, low=1.0)

    result = compute_atr_1d_asof(
        candles_1m=candles,
        symbol="AAA/USDT:USDT",
        snapshot_time_ms=snapshot_time_ms,
        atr_window_minutes=2,
    )
    mutated_result = compute_atr_1d_asof(
        candles_1m=mutated,
        symbol="AAA/USDT:USDT",
        snapshot_time_ms=snapshot_time_ms,
        atr_window_minutes=2,
    )

    assert result == mutated_result


def test_atr_asof_rejects_insufficient_history_without_fallback() -> None:
    candles = [_candle(0, close=100.0), _candle(1, close=101.0)]

    with pytest.raises(AtrComputationError, match="insufficient as-of 1m candle history"):
        compute_atr_1d_asof(
            candles_1m=candles,
            symbol="AAA/USDT:USDT",
            snapshot_time_ms=candles[-1].available_time_ms,
            atr_window_minutes=2,
        )


def test_atr_asof_filters_by_symbol() -> None:
    candles = [_candle(0), _candle(1), _candle(2)]

    with pytest.raises(AtrComputationError, match="got 0"):
        compute_atr_1d_asof(
            candles_1m=candles,
            symbol="BBB/USDT:USDT",
            snapshot_time_ms=candles[-1].available_time_ms,
            atr_window_minutes=2,
        )
