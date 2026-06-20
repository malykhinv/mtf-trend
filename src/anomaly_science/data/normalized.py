from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

import pandas as pd

from anomaly_science.contracts.market import Candle1m, Candle5m, FundingRate, LiquidationEvent, OpenInterest5m, SymbolDayUniverseRow
from anomaly_science.contracts.market import FIVE_MINUTES_MS, ONE_MINUTE_MS


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


def _optional_int(row: pd.Series, name: str) -> int | None:
    if name not in row or pd.isna(row[name]):
        return None
    return int(row[name])


def _optional_str(row: pd.Series, name: str, default: str) -> str:
    if name not in row or pd.isna(row[name]):
        return default
    return str(row[name])


def _records(frame: pd.DataFrame) -> list[pd.Series]:
    return [row for _, row in frame.iterrows()]


def _tuple_optional_float(row: object, name: str, *, present: bool) -> float | None:
    if not present:
        return None
    value = getattr(row, name)
    if pd.isna(value):
        return None
    return float(value)


def normalize_candles_1m(frame: pd.DataFrame) -> tuple[Candle1m, ...]:
    has_number_of_trades = "number_of_trades" in frame.columns
    has_taker_buy_quote_volume = "taker_buy_quote_volume" in frame.columns
    return tuple(
        Candle1m(
            symbol=str(row.symbol),
            open_time_ms=int(row.open_time_ms),
            available_time_ms=int(row.available_time_ms),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            quote_volume=float(row.quote_volume),
            number_of_trades=_tuple_optional_float(row, "number_of_trades", present=has_number_of_trades),
            taker_buy_quote_volume=_tuple_optional_float(
                row,
                "taker_buy_quote_volume",
                present=has_taker_buy_quote_volume,
            ),
        )
        for row in frame.itertuples(index=False)
    )


def normalize_candles_5m(frame: pd.DataFrame) -> tuple[Candle5m, ...]:
    has_number_of_trades = "number_of_trades" in frame.columns
    has_taker_buy_quote_volume = "taker_buy_quote_volume" in frame.columns
    return tuple(
        Candle5m(
            symbol=str(row.symbol),
            open_time_ms=int(row.open_time_ms),
            available_time_ms=int(row.available_time_ms),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            quote_volume=float(row.quote_volume),
            number_of_trades=_tuple_optional_float(row, "number_of_trades", present=has_number_of_trades),
            taker_buy_quote_volume=_tuple_optional_float(
                row,
                "taker_buy_quote_volume",
                present=has_taker_buy_quote_volume,
            ),
        )
        for row in frame.itertuples(index=False)
    )


def normalize_open_interest_5m(frame: pd.DataFrame | None) -> tuple[OpenInterest5m, ...]:
    if frame is None:
        return ()
    return tuple(
        OpenInterest5m(
            symbol=str(row.symbol),
            timestamp_ms=int(row.timestamp_ms),
            available_time_ms=int(row.available_time_ms),
            open_interest=float(row.open_interest),
            source=str(row.source),
        )
        for row in frame.itertuples(index=False)
    )


def normalize_liquidations(frame: pd.DataFrame | None) -> tuple[LiquidationEvent, ...]:
    if frame is None:
        return ()
    return tuple(
        LiquidationEvent(
            symbol=str(row.symbol),
            event_time_ms=int(row.event_time_ms),
            available_time_ms=int(row.available_time_ms),
            side=str(row.side),
            price=float(row.price),
            quantity=float(row.quantity),
            quote_quantity=float(row.quote_quantity),
            source=str(row.source),
        )
        for row in frame.itertuples(index=False)
    )


def normalize_funding_rates(frame: pd.DataFrame | None) -> tuple[FundingRate, ...]:
    if frame is None:
        return ()
    return tuple(
        FundingRate(
            symbol=str(row.symbol),
            timestamp_ms=int(row.timestamp_ms),
            funding_rate=float(row.funding_rate),
        )
        for row in frame.itertuples(index=False)
    )


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n", ""}:
        return False
    raise ValueError(f"cannot parse boolean value: {value!r}")


def normalize_symbol_universe_by_day(frame: pd.DataFrame | None) -> tuple[SymbolDayUniverseRow, ...]:
    if frame is None:
        return ()
    return tuple(
        SymbolDayUniverseRow(
            trade_date=str(row["trade_date"]),
            symbol=str(row["symbol"]),
            listed_asof_day=_bool_value(row["listed_asof_day"]),
            delisted_asof_day=_bool_value(row["delisted_asof_day"]),
            tradable_on_day=_bool_value(row["tradable_on_day"]),
            has_1m_data=_bool_value(row["has_1m_data"]),
            has_5m_data=_bool_value(row["has_5m_data"]),
            has_oi_data=_bool_value(row["has_oi_data"]),
            has_liquidation_data=_bool_value(row["has_liquidation_data"]),
            liquidity_eligible_on_day=_bool_value(row["liquidity_eligible_on_day"]),
            eligible_for_cross_section=(
                _bool_value(row["eligible_for_cross_section"])
                if "eligible_for_cross_section" in row and not pd.isna(row["eligible_for_cross_section"])
                else None
            ),
            first_seen_data_time_ms=_optional_int(row, "first_seen_data_time_ms"),
            last_seen_data_time_ms=_optional_int(row, "last_seen_data_time_ms"),
            data_source_symbol_status=_optional_str(row, "data_source_symbol_status", "observed_on_day"),
            listing_confidence=_optional_str(row, "listing_confidence", "data_observed"),
            delisting_confidence=_optional_str(row, "delisting_confidence", "unknown_without_external_metadata"),
            reason_if_excluded="" if "reason_if_excluded" not in row or pd.isna(row["reason_if_excluded"]) else str(row["reason_if_excluded"]),
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


def validate_market_data_boundary(
    *,
    candles_1m: pd.DataFrame,
    candles_5m: pd.DataFrame,
    open_interest_5m: pd.DataFrame | None,
    liquidations: pd.DataFrame | None,
) -> None:
    """Validate normalized market-data contracts without materializing row objects."""
    _validate_candle_frame(candles_1m, dataset_name="candles_1m", timeframe_ms=ONE_MINUTE_MS)
    _validate_candle_frame(candles_5m, dataset_name="candles_5m", timeframe_ms=FIVE_MINUTES_MS)
    if open_interest_5m is not None:
        _validate_open_interest_frame(open_interest_5m)
    if liquidations is not None:
        _validate_liquidation_frame(liquidations)


def _validate_candle_frame(frame: pd.DataFrame, *, dataset_name: str, timeframe_ms: int) -> None:
    _require_non_empty_symbol(frame, dataset_name=dataset_name)
    numeric = _numeric_columns(
        frame,
        dataset_name=dataset_name,
        columns=(
            "open_time_ms",
            "available_time_ms",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
        ),
    )
    if bool((numeric["available_time_ms"] < numeric["open_time_ms"] + timeframe_ms).any()):
        raise ValueError(f"{dataset_name}.available_time_ms must be at or after candle close")
    if bool((numeric[["open", "high", "low", "close"]] <= 0).any(axis=1).any()):
        raise ValueError(f"{dataset_name} OHLC values must be positive")
    if bool(
        (
            (numeric["high"] < numeric[["open", "close"]].max(axis=1))
            | (numeric["low"] > numeric[["open", "close"]].min(axis=1))
            | (numeric["low"] > numeric["high"])
        ).any()
    ):
        raise ValueError(f"{dataset_name} OHLC ordering is invalid")
    if bool((numeric[["volume", "quote_volume"]] < 0).any(axis=1).any()):
        raise ValueError(f"{dataset_name} volume fields must be non-negative")

    optional_numeric = _existing_numeric_columns(frame, dataset_name=dataset_name, columns=("number_of_trades", "taker_buy_quote_volume"))
    for column in optional_numeric.columns:
        if bool((optional_numeric[column] < 0).any()):
            raise ValueError(f"{dataset_name}.{column} must be non-negative")
    if "taker_buy_quote_volume" in optional_numeric.columns:
        if bool((optional_numeric["taker_buy_quote_volume"] > numeric["quote_volume"]).any()):
            raise ValueError(f"{dataset_name}.taker_buy_quote_volume must be <= quote_volume")


def _validate_open_interest_frame(frame: pd.DataFrame) -> None:
    dataset_name = "open_interest_5m"
    _require_non_empty_symbol(frame, dataset_name=dataset_name)
    _require_non_empty_text(frame, dataset_name=dataset_name, column="source")
    numeric = _numeric_columns(frame, dataset_name=dataset_name, columns=("timestamp_ms", "available_time_ms", "open_interest"))
    if bool((numeric["available_time_ms"] < numeric["timestamp_ms"] + FIVE_MINUTES_MS).any()):
        raise ValueError("open_interest_5m.available_time_ms must be at or after closed 5m bucket")
    if bool((numeric["open_interest"] < 0).any()):
        raise ValueError("open_interest_5m.open_interest must be non-negative")


def _validate_liquidation_frame(frame: pd.DataFrame) -> None:
    dataset_name = "liquidations"
    _require_non_empty_symbol(frame, dataset_name=dataset_name)
    _require_non_empty_text(frame, dataset_name=dataset_name, column="source")
    sides = frame["side"].astype("string").str.strip()
    if bool((~sides.isin(("long", "short"))).any()):
        raise ValueError("liquidations.side must be 'long' or 'short'")
    numeric = _numeric_columns(
        frame,
        dataset_name=dataset_name,
        columns=("event_time_ms", "available_time_ms", "price", "quantity", "quote_quantity"),
    )
    if bool((numeric["available_time_ms"] < numeric["event_time_ms"]).any()):
        raise ValueError("liquidations.available_time_ms must be >= event_time_ms")
    if bool((numeric["price"] <= 0).any()):
        raise ValueError("liquidations.price must be positive")
    if bool((numeric[["quantity", "quote_quantity"]] < 0).any(axis=1).any()):
        raise ValueError("liquidations quantity fields must be non-negative")


def _require_non_empty_symbol(frame: pd.DataFrame, *, dataset_name: str) -> None:
    _require_non_empty_text(frame, dataset_name=dataset_name, column="symbol")


def _require_non_empty_text(frame: pd.DataFrame, *, dataset_name: str, column: str) -> None:
    if column not in frame.columns:
        raise ValueError(f"{dataset_name} is missing required column {column!r}")
    values = frame[column].astype("string").str.strip()
    if bool(values.isna().any()) or bool((values == "").any()):
        raise ValueError(f"{dataset_name}.{column} must be non-empty")


def _numeric_columns(frame: pd.DataFrame, *, dataset_name: str, columns: tuple[str, ...]) -> pd.DataFrame:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{dataset_name} is missing required numeric columns: {missing}")
    numeric = frame.loc[:, list(columns)].apply(pd.to_numeric, errors="coerce")
    if bool(numeric.isna().any(axis=1).any()):
        raise ValueError(f"{dataset_name} has non-numeric or missing values in {columns}")
    return numeric


def _existing_numeric_columns(frame: pd.DataFrame, *, dataset_name: str, columns: tuple[str, ...]) -> pd.DataFrame:
    existing = [column for column in columns if column in frame.columns]
    if not existing:
        return pd.DataFrame(index=frame.index)
    numeric = frame.loc[:, existing].apply(pd.to_numeric, errors="coerce")
    present = frame.loc[:, existing].notna()
    invalid = numeric.isna() & present
    if bool(invalid.any(axis=1).any()):
        raise ValueError(f"{dataset_name} has non-numeric values in optional columns {tuple(existing)}")
    return numeric
