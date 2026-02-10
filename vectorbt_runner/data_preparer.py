"""Модуль проекта."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from constants import (
    DATA_PREPARER_EMPTY_BOOL_DTYPE,
    DATA_PREPARER_EMPTY_FLOAT_DTYPE,
    DATA_PREPARER_NUMERIC_COLUMNS,
    DATA_PREPARER_TRADE_COLUMNS,
    SIMULATION_DATETIME_UNIT_MS,
    SIMULATION_PARQUET_FILE_NAME,
    SIMULATION_PNL_PERCENT_DIVISOR,
    SIMULATION_PRICE_INIT,
    SIMULATION_TIMEZONE_UTC,
    SIMULATION_UNIT_INCREMENT,
    SIMULATION_ZERO_VALUE,
    STRATEGY_REQUIRED_COLUMNS,
)
from domain.enums.timeframe import Timeframe
from domain.models.trade_result import TradeResult
from vectorbt_runner.vectorbt_inputs import VectorbtInputs


# область Приватные
def _to_utc_timestamp(value: object) -> pd.Timestamp:
    """Метод."""
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


# конец области Приватные
class DataPreparer:
    """Класс."""
    REQUIRED_COLUMNS = STRATEGY_REQUIRED_COLUMNS

    # область Приватные
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)

    # конец области Приватные
    def list_symbols(self, timeframe: Timeframe) -> list[str]:
        symbols: list[str] = []
        if not self._cache_dir.exists():
            return symbols

        for symbol_dir in self._cache_dir.iterdir():
            path = symbol_dir / timeframe.value / SIMULATION_PARQUET_FILE_NAME
            if symbol_dir.is_dir() and path.exists():
                symbols.append(symbol_dir.name)
        return sorted(symbols)

    def load_symbol_data(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        path = self._cache_dir / symbol / timeframe.value / SIMULATION_PARQUET_FILE_NAME
        if not path.exists():
            return pd.DataFrame()

        frame = pd.read_parquet(path)
        missing = [col for col in self.REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            return pd.DataFrame()

        normalized = frame.copy()
        normalized["symbol"] = symbol
        normalized["datetime"] = pd.to_datetime(normalized["timestamp"], unit=SIMULATION_DATETIME_UNIT_MS, utc=True, errors="coerce")
        normalized = normalized.dropna(subset=["datetime"])

        numeric_cols = [col for col in DATA_PREPARER_NUMERIC_COLUMNS if col in normalized.columns]
        for col in numeric_cols:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

        normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"])
        normalized = normalized.sort_values("datetime").drop_duplicates(subset=["timestamp"], keep="last")
        return normalized.reset_index(drop=True)


    def load_symbol_data_multi(self, symbol: str, timeframes: Iterable[Timeframe]) -> dict[Timeframe, pd.DataFrame]:
        return {
            timeframe: self.load_symbol_data(symbol, timeframe)
            for timeframe in timeframes
        }

    @staticmethod
    def prepare_vectorbt_inputs(trades: list[TradeResult], initial_price: float = SIMULATION_PRICE_INIT) -> VectorbtInputs:
        """Метод."""
        if not trades:
            index = pd.DatetimeIndex([], tz=SIMULATION_TIMEZONE_UTC)
            empty_float = pd.Series([], index=index, dtype=DATA_PREPARER_EMPTY_FLOAT_DTYPE)
            empty_bool = pd.Series([], index=index, dtype=DATA_PREPARER_EMPTY_BOOL_DTYPE)
            trades_frame = pd.DataFrame(columns=DATA_PREPARER_TRADE_COLUMNS)
            return VectorbtInputs(
                close=empty_float,
                entries=empty_bool,
                exits=empty_bool,
                equity_curve=empty_float,
                trades=trades_frame,
            )

        trades_sorted = sorted(trades, key=lambda trade: (trade.entry_time, trade.exit_time))
        trade_rows = [
            {
                "entry_time": _to_utc_timestamp(trade.entry_time),
                "exit_time": _to_utc_timestamp(trade.exit_time),
                "pnl": float(trade.pnl),
                "pnl_percent": float(trade.pnl_percent.value),
                "result_type": trade.result_type.value,
            }
            for trade in trades_sorted
        ]
        trades_frame = pd.DataFrame(trade_rows)

        timeline = pd.DatetimeIndex(
            sorted(set(trades_frame["entry_time"].tolist() + trades_frame["exit_time"].tolist())),
            tz=SIMULATION_TIMEZONE_UTC,
        )
        close = pd.Series(initial_price, index=timeline, dtype=DATA_PREPARER_EMPTY_FLOAT_DTYPE)
        entries = pd.Series(False, index=timeline, dtype=DATA_PREPARER_EMPTY_BOOL_DTYPE)
        exits = pd.Series(False, index=timeline, dtype=DATA_PREPARER_EMPTY_BOOL_DTYPE)
        equity_curve = pd.Series(SIMULATION_ZERO_VALUE, index=timeline, dtype=DATA_PREPARER_EMPTY_FLOAT_DTYPE)

        running_price = float(initial_price)
        cumulative_pnl = SIMULATION_ZERO_VALUE
        for trade in trade_rows:
            entry_time = trade["entry_time"]
            exit_time = trade["exit_time"]
            pnl_percent = trade["pnl_percent"]
            pnl = trade["pnl"]

            entries.loc[entry_time] = True
            exits.loc[exit_time] = True
            close.loc[entry_time] = running_price

            running_price *= SIMULATION_UNIT_INCREMENT + (pnl_percent / SIMULATION_PNL_PERCENT_DIVISOR)
            close.loc[exit_time] = running_price

            cumulative_pnl += pnl
            equity_curve.loc[exit_time] = cumulative_pnl

        close = close.ffill()
        equity_curve = equity_curve.replace(SIMULATION_ZERO_VALUE, pd.NA).ffill().fillna(SIMULATION_ZERO_VALUE)

        return VectorbtInputs(
            close=close,
            entries=entries,
            exits=exits,
            equity_curve=equity_curve,
            trades=trades_frame,
        )
