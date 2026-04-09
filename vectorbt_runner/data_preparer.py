"""Модуль проекта."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from pyarrow import parquet as pq

from constants import (
    DATA_PREPARER_EMPTY_BOOL_DTYPE,
    DATA_PREPARER_EMPTY_FLOAT_DTYPE,
    DATA_PREPARER_NUMERIC_COLUMNS,
    DATA_PREPARER_TRADE_COLUMNS,
    SIMULATION_PARQUET_FILE_NAME,
    SIMULATION_PNL_PERCENT_DIVISOR,
    SIMULATION_PRICE_INIT,
    SIMULATION_UNIT_INCREMENT,
    SIMULATION_ZERO_VALUE,
    STRATEGY_REQUIRED_COLUMNS,
)
from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe
from domain.models.trade_result import TradeResult
from utils.symbols import normalize_symbol
from vectorbt_runner.vectorbt_inputs import VectorbtInputs


class DataPreparer:
    """Класс."""
    REQUIRED_COLUMNS = STRATEGY_REQUIRED_COLUMNS
    # Набор колонок, читаемых из parquet для подготовки входов стратегии.
    INPUT_COLUMNS = tuple(dict.fromkeys((*STRATEGY_REQUIRED_COLUMNS, *DATA_PREPARER_NUMERIC_COLUMNS)))

    # region Приватные
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._symbol_dir_cache: dict[str, str] | None = None
        self._parquet_columns_cache: dict[Path, tuple[str, ...]] = {}

    def _ensure_symbol_dir_cache(self) -> dict[str, str]:
        if self._symbol_dir_cache is not None:
            return self._symbol_dir_cache

        mapping: dict[str, str] = {}
        if self._cache_dir.exists():
            for symbol_dir in self._cache_dir.iterdir():
                if not symbol_dir.is_dir():
                    continue
                decoded_symbol = ParquetStorage.decode_symbol_from_path(symbol_dir.name)
                mapping[normalize_symbol(decoded_symbol)] = symbol_dir.name
        self._symbol_dir_cache = mapping
        return mapping

    def _get_available_columns(self, path: Path) -> tuple[str, ...]:
        cached = self._parquet_columns_cache.get(path)
        if cached is not None:
            return cached
        columns = tuple(pq.ParquetFile(path).schema.names)
        self._parquet_columns_cache[path] = columns
        return columns

    def _resolve_symbol_dir_name(self, symbol: str) -> str | None:
        exact_dir_name = ParquetStorage.encode_symbol_for_path(symbol)
        exact_path = self._cache_dir / exact_dir_name
        if exact_path.exists():
            return exact_dir_name

        normalized_requested = normalize_symbol(symbol)
        return self._ensure_symbol_dir_cache().get(normalized_requested)

    # endregion Приватные
    def list_symbols(self, timeframe: Timeframe) -> list[str]:
        """Возвращает список символов, доступных для расчёта."""
        symbols: list[str] = []
        if not self._cache_dir.exists():
            return symbols

        for symbol_dir in self._cache_dir.iterdir():
            path = symbol_dir / timeframe.value / SIMULATION_PARQUET_FILE_NAME
            if symbol_dir.is_dir() and path.exists():
                symbols.append(ParquetStorage.decode_symbol_from_path(symbol_dir.name))
        return sorted(symbols)

    def load_symbol_data(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        """Загружает данные по одному символу для бэктеста.

        Читает из parquet только колонки, используемые в downstream-подготовке:
        обязательные поля стратегии + числовые поля для нормализации типов.
        """
        symbol_dir_name = self._resolve_symbol_dir_name(symbol)
        if symbol_dir_name is None:
            return pd.DataFrame()

        path = self._cache_dir / symbol_dir_name / timeframe.value / SIMULATION_PARQUET_FILE_NAME
        if not path.exists():
            return pd.DataFrame()

        available_columns = set(self._get_available_columns(path))
        required_columns_subset = [column for column in self.INPUT_COLUMNS if column in available_columns]
        frame = pd.read_parquet(path, columns=required_columns_subset)
        missing = [col for col in self.REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            return pd.DataFrame()

        prepared = frame
        if not is_numeric_dtype(prepared["timestamp"]):
            prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")

        numeric_cols = [col for col in DATA_PREPARER_NUMERIC_COLUMNS if col in prepared.columns]
        for col in numeric_cols:
            if not is_numeric_dtype(prepared[col]):
                prepared[col] = pd.to_numeric(prepared[col], errors="coerce")

        required_mask = np.ones(len(prepared), dtype=bool)
        for column in ("timestamp", "open", "high", "low", "close", "volume"):
            required_mask &= prepared[column].notna().to_numpy(dtype=bool, copy=False)
        if not bool(required_mask.all()):
            prepared = prepared.loc[required_mask]
        if prepared.empty:
            return pd.DataFrame()

        timestamps = prepared["timestamp"].to_numpy(dtype=np.int64, copy=False)
        needs_sort = bool(timestamps.size > 1 and np.any(timestamps[1:] < timestamps[:-1]))
        if needs_sort:
            prepared = prepared.iloc[np.argsort(timestamps, kind="stable")]
            timestamps = prepared["timestamp"].to_numpy(dtype=np.int64, copy=False)
        if timestamps.size > 1:
            unique_mask = np.ones(len(prepared), dtype=bool)
            unique_mask[:-1] = timestamps[:-1] != timestamps[1:]
            if not bool(unique_mask.all()):
                prepared = prepared.loc[unique_mask]

        prepared["symbol"] = pd.Categorical.from_codes(
            np.zeros(len(prepared), dtype=np.int8),
            categories=[symbol],
        )
        return prepared


    def load_symbol_data_multi(self, symbol: str, timeframes: Iterable[Timeframe]) -> dict[Timeframe, pd.DataFrame]:
        """Загружает данные по нескольким символам сразу."""
        return {
            timeframe: self.load_symbol_data(symbol, timeframe)
            for timeframe in timeframes
        }

    @staticmethod
    def prepare_vectorbt_inputs(trades: list[TradeResult], initial_price: float = SIMULATION_PRICE_INIT) -> VectorbtInputs:
        """Метод."""
        if not trades:
            index = pd.Index([], dtype="int64", name="timestamp_ms")
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

        trades_sorted = sorted(trades, key=lambda trade: (trade.entry_timestamp_ms, trade.exit_timestamp_ms))
        trade_rows = []
        for trade in trades_sorted:
            row = {
                "entry_timestamp_ms": int(trade.entry_timestamp_ms),
                "exit_timestamp_ms": int(trade.exit_timestamp_ms),
                "pnl": float(trade.pnl),
                "pnl_percent": float(trade.pnl_percent.value),
                "result_type": trade.result_type.value,
            }
            if trade.metadata:
                for key, value in trade.metadata.items():
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        row[key] = value
            trade_rows.append(row)
        trades_frame = pd.DataFrame(trade_rows)

        timeline = pd.Index(
            sorted(set(trades_frame["entry_timestamp_ms"].tolist() + trades_frame["exit_timestamp_ms"].tolist())),
            dtype="int64",
            name="timestamp_ms",
        )
        close = pd.Series(initial_price, index=timeline, dtype=DATA_PREPARER_EMPTY_FLOAT_DTYPE)
        entries = pd.Series(False, index=timeline, dtype=DATA_PREPARER_EMPTY_BOOL_DTYPE)
        exits = pd.Series(False, index=timeline, dtype=DATA_PREPARER_EMPTY_BOOL_DTYPE)
        equity_curve = pd.Series(SIMULATION_ZERO_VALUE, index=timeline, dtype=DATA_PREPARER_EMPTY_FLOAT_DTYPE)

        running_price = float(initial_price)
        cumulative_pnl = SIMULATION_ZERO_VALUE
        for trade in trade_rows:
            entry_time = int(trade["entry_timestamp_ms"])
            exit_time = int(trade["exit_timestamp_ms"])
            pnl_percent = trade["pnl_percent"]
            pnl = trade["pnl"]

            entries.at[entry_time] = True
            exits.at[exit_time] = True
            close.at[entry_time] = running_price

            running_price *= SIMULATION_UNIT_INCREMENT + (pnl_percent / SIMULATION_PNL_PERCENT_DIVISOR)
            close.at[exit_time] = running_price

            cumulative_pnl += pnl
            equity_curve.at[exit_time] = cumulative_pnl

        close = close.ffill()
        equity_curve = equity_curve.replace(SIMULATION_ZERO_VALUE, pd.NA).ffill().fillna(SIMULATION_ZERO_VALUE)

        return VectorbtInputs(
            close=close,
            entries=entries,
            exits=exits,
            equity_curve=equity_curve,
            trades=trades_frame,
        )
