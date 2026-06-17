from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

import pandas as pd

from anomaly_science.contracts.market import Candle1m, Candle5m, LiquidationEvent, OpenInterest5m


@dataclass(frozen=True, slots=True)
class NormalizedMarketData:
    candles_1m: tuple[Candle1m, ...]
    candles_5m: tuple[Candle5m, ...]
    open_interest_5m: tuple[OpenInterest5m, ...]
    liquidations: tuple[LiquidationEvent, ...]


T = TypeVar("T")


def _optional_float(row: pd.Series, name: str) -> float | None:
    if name not in row or pd.isna(row[name]):
        return None
    return float(row[name])


def _records(frame: pd.DataFrame) -> list[pd.Series]:
    return [row for _, row in frame.iterrows()]


def normalize_candles_1m(frame: pd.DataFrame) -> tuple[Candle1m, ...]:
    return tuple(
        Candle1m(
            symbol=str(row["symbol"]),
            open_time_ms=int(row["open_time_ms"]),
            available_time_ms=int(row["available_time_ms"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            quote_volume=float(row["quote_volume"]),
            number_of_trades=_optional_float(row, "number_of_trades"),
            taker_buy_quote_volume=_optional_float(row, "taker_buy_quote_volume"),
        )
        for row in _records(frame)
    )


def normalize_candles_5m(frame: pd.DataFrame) -> tuple[Candle5m, ...]:
    return tuple(
        Candle5m(
            symbol=str(row["symbol"]),
            open_time_ms=int(row["open_time_ms"]),
            available_time_ms=int(row["available_time_ms"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            quote_volume=float(row["quote_volume"]),
            number_of_trades=_optional_float(row, "number_of_trades"),
            taker_buy_quote_volume=_optional_float(row, "taker_buy_quote_volume"),
        )
        for row in _records(frame)
    )


def normalize_open_interest_5m(frame: pd.DataFrame | None) -> tuple[OpenInterest5m, ...]:
    if frame is None:
        return ()
    return tuple(
        OpenInterest5m(
            symbol=str(row["symbol"]),
            timestamp_ms=int(row["timestamp_ms"]),
            available_time_ms=int(row["available_time_ms"]),
            open_interest=float(row["open_interest"]),
            source=str(row["source"]),
        )
        for row in _records(frame)
    )


def normalize_liquidations(frame: pd.DataFrame | None) -> tuple[LiquidationEvent, ...]:
    if frame is None:
        return ()
    return tuple(
        LiquidationEvent(
            symbol=str(row["symbol"]),
            event_time_ms=int(row["event_time_ms"]),
            available_time_ms=int(row["available_time_ms"]),
            side=str(row["side"]),
            price=float(row["price"]),
            quantity=float(row["quantity"]),
            quote_quantity=float(row["quote_quantity"]),
            source=str(row["source"]),
        )
        for row in _records(frame)
    )


def normalize_market_data(
    *,
    candles_1m: pd.DataFrame,
    candles_5m: pd.DataFrame,
    open_interest_5m: pd.DataFrame | None,
    liquidations: pd.DataFrame | None,
) -> NormalizedMarketData:
    return NormalizedMarketData(
        candles_1m=normalize_candles_1m(candles_1m),
        candles_5m=normalize_candles_5m(candles_5m),
        open_interest_5m=normalize_open_interest_5m(open_interest_5m),
        liquidations=normalize_liquidations(liquidations),
    )
