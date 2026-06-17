from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from anomaly_science.contracts.market import Candle1m, MarketDataContractError

ATR_1D_WINDOW_MINUTES = 1440


class AtrComputationError(ValueError):
    """Raised when ATR cannot be computed under the strict as-of contract."""


@dataclass(frozen=True, slots=True)
class AtrAsOfResult:
    """Point-in-time ATR result computed from closed 1m candles only.

    `atr_1d_asof_t` is the mean true range of the last `atr_window_minutes`
    closed candles available at or before `snapshot_time_ms`. Computing the
    first true range also requires the previous closed candle, so the engine
    requires at least `atr_window_minutes + 1` as-of candles and does not invent
    a fallback value when history is insufficient.
    """

    symbol: str
    snapshot_time_ms: int
    atr_window_minutes: int
    atr_1d_asof_t: float
    atr_1d_pct_asof_t: float
    source_candle_count: int
    first_candle_available_time_ms: int
    last_candle_available_time_ms: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        if self.atr_window_minutes <= 0:
            raise MarketDataContractError("atr_window_minutes must be positive")
        if self.source_candle_count != self.atr_window_minutes:
            raise MarketDataContractError("source_candle_count must equal atr_window_minutes")
        for field_name in ("atr_1d_asof_t", "atr_1d_pct_asof_t"):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value <= 0:
                raise MarketDataContractError(f"{field_name} must be positive and finite")
        if self.first_candle_available_time_ms > self.last_candle_available_time_ms:
            raise MarketDataContractError("first candle timestamp must be <= last candle timestamp")
        if self.last_candle_available_time_ms > self.snapshot_time_ms:
            raise MarketDataContractError("ATR source candles must be available <= snapshot_time_ms")


def compute_atr_1d_asof(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    symbol: str,
    snapshot_time_ms: int,
    atr_window_minutes: int = ATR_1D_WINDOW_MINUTES,
) -> AtrAsOfResult:
    """Compute strict point-in-time 1d ATR from closed 1m candles.

    Rules enforced by this boundary:
    - only candles for `symbol` are used;
    - every source candle must have `available_time_ms <= snapshot_time_ms`;
    - no future candle is read, even if present in the input sequence;
    - no partial/fallback ATR is returned when history is insufficient;
    - ATR uses true range with the previous closed candle's close.
    """
    if not symbol:
        raise AtrComputationError("symbol is required")
    if atr_window_minutes <= 0:
        raise AtrComputationError("atr_window_minutes must be positive")

    asof_candles = sorted(
        (
            candle
            for candle in candles_1m
            if candle.symbol == symbol and candle.available_time_ms <= snapshot_time_ms
        ),
        key=lambda item: (item.available_time_ms, item.open_time_ms),
    )
    required_candle_count = atr_window_minutes + 1
    if len(asof_candles) < required_candle_count:
        raise AtrComputationError(
            "insufficient as-of 1m candle history for ATR: "
            f"need {required_candle_count} closed candles for {atr_window_minutes} true ranges, "
            f"got {len(asof_candles)} for {symbol} at {snapshot_time_ms}"
        )

    source_slice = asof_candles[-required_candle_count:]
    true_ranges: list[float] = []
    for previous_candle, candle in zip(source_slice, source_slice[1:]):
        true_ranges.append(_true_range(candle=candle, previous_close=previous_candle.close))

    if len(true_ranges) != atr_window_minutes:
        raise AtrComputationError("internal ATR window size mismatch")

    atr = sum(true_ranges) / atr_window_minutes
    last_close = source_slice[-1].close
    if last_close <= 0:
        raise AtrComputationError("latest as-of close must be positive")
    if not math.isfinite(atr) or atr <= 0:
        raise AtrComputationError("computed ATR must be positive and finite")

    first_source_candle = source_slice[1]
    last_source_candle = source_slice[-1]
    return AtrAsOfResult(
        symbol=symbol,
        snapshot_time_ms=snapshot_time_ms,
        atr_window_minutes=atr_window_minutes,
        atr_1d_asof_t=atr,
        atr_1d_pct_asof_t=atr / last_close,
        source_candle_count=len(true_ranges),
        first_candle_available_time_ms=first_source_candle.available_time_ms,
        last_candle_available_time_ms=last_source_candle.available_time_ms,
    )


def _true_range(*, candle: Candle1m, previous_close: float) -> float:
    if previous_close <= 0:
        raise AtrComputationError("previous close must be positive")
    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )
