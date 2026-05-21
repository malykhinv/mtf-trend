"""Shared aggTrade-to-OHLCV helpers for anomaly cache/backtest jobs."""

from __future__ import annotations

import pandas as pd


def resolve_aggtrade_timestamp(row: dict[str, object]) -> int | None:
    for column in ("transact_time", "T"):
        if column not in row:
            continue
        try:
            return int(row[column])
        except (TypeError, ValueError):
            return None
    return None


def resolve_aggtrade_id(row: dict[str, object]) -> int | None:
    for column in ("aggregate_trade_id", "a"):
        if column not in row:
            continue
        try:
            return int(row[column])
        except (TypeError, ValueError):
            return None
    return None


def aggregate_aggtrades_to_ohlcv_frame(
    trades: pd.DataFrame,
    *,
    timeframe_ms: int,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_quote_volume",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    timestamp_column = "transact_time" if "transact_time" in trades.columns else "T"
    price_column = "price" if "price" in trades.columns else "p"
    quantity_column = "quantity" if "quantity" in trades.columns else "q"
    maker_column = "is_buyer_maker" if "is_buyer_maker" in trades.columns else "m"
    required = (timestamp_column, price_column, quantity_column, maker_column)
    missing = [column for column in required if column not in trades.columns]
    if missing:
        return pd.DataFrame(columns=columns)
    work = trades.copy()
    work["timestamp"] = pd.to_numeric(work[timestamp_column], errors="coerce")
    work["price"] = pd.to_numeric(work[price_column], errors="coerce")
    work["quantity"] = pd.to_numeric(work[quantity_column], errors="coerce")
    work = work.loc[
        work["timestamp"].notna()
        & work["price"].notna()
        & work["quantity"].notna()
        & (work["timestamp"] >= int(start_timestamp_ms))
        & (work["timestamp"] <= int(end_timestamp_ms))
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)
    work["timestamp"] = work["timestamp"].astype("int64")
    work["bucket"] = (work["timestamp"] // int(timeframe_ms)) * int(timeframe_ms)
    work["quote_volume"] = work["price"].astype("float64") * work["quantity"].astype("float64")
    buyer_is_maker = work[maker_column].astype(str).str.lower().isin(("true", "1"))
    work["taker_buy_quote_volume"] = work["quote_volume"].where(~buyer_is_maker, 0.0)
    aggregated = (
        work.groupby("bucket", sort=True)
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
            quote_volume=("quote_volume", "sum"),
            number_of_trades=("quantity", "size"),
            taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
        )
        .reset_index()
        .rename(columns={"bucket": "timestamp"})
    )
    aggregated = aggregated.loc[
        (aggregated["timestamp"] >= int(start_timestamp_ms))
        & (aggregated["timestamp"] <= int(end_timestamp_ms))
    ].copy()
    return aggregated.loc[:, columns].reset_index(drop=True)


_resolve_aggtrade_timestamp = resolve_aggtrade_timestamp
_resolve_aggtrade_id = resolve_aggtrade_id
_aggregate_aggtrades_to_ohlcv_frame = aggregate_aggtrades_to_ohlcv_frame
