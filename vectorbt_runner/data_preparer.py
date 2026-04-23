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

    @staticmethod
    def _resolve_window_start_ms(*, days: int, end_timestamp_ms: int) -> int:
        if days <= 0:
            raise ValueError("days must be > 0")
        return int(end_timestamp_ms) - int(days) * 86_400_000

    @staticmethod
    def _read_parquet_min_max_timestamp_ms(path: Path) -> tuple[int | None, int | None]:
        """Best-effort min/max timestamp from Parquet metadata statistics (no full scan).

        Returns (None, None) when statistics are missing or unreadable.
        """
        try:
            parquet_file = pq.ParquetFile(path)
            try:
                ts_idx = parquet_file.schema.names.index("timestamp")
            except ValueError:
                return None, None

            meta = parquet_file.metadata
            if meta is None:
                return None, None

            min_ts: int | None = None
            max_ts: int | None = None
            for rg_idx in range(meta.num_row_groups):
                col = meta.row_group(rg_idx).column(ts_idx)
                stats = col.statistics
                if stats is None or not getattr(stats, "has_min_max", False):
                    continue
                rg_min = getattr(stats, "min", None)
                rg_max = getattr(stats, "max", None)
                if rg_min is not None:
                    rg_min_i = int(rg_min)
                    min_ts = rg_min_i if min_ts is None else min(min_ts, rg_min_i)
                if rg_max is not None:
                    rg_max_i = int(rg_max)
                    max_ts = rg_max_i if max_ts is None else max(max_ts, rg_max_i)
            return min_ts, max_ts
        except Exception:
            return None, None

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

    def get_symbol_last_timestamp_ms(self, symbol: str, timeframe: Timeframe) -> int | None:
        symbol_dir_name = self._resolve_symbol_dir_name(symbol)
        if symbol_dir_name is None:
            return None
        path = self._cache_dir / symbol_dir_name / timeframe.value / SIMULATION_PARQUET_FILE_NAME
        if not path.exists():
            return None
        _min_ts, max_ts = self._read_parquet_min_max_timestamp_ms(path)
        return max_ts

    def load_symbol_data(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        days: int | None = None,
        end_timestamp_ms: int | None = None,
    ) -> pd.DataFrame:
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

        filters = None
        if days is not None:
            resolved_end_ms = int(end_timestamp_ms) if end_timestamp_ms is not None else None
            if resolved_end_ms is None:
                _min_ts, max_ts = self._read_parquet_min_max_timestamp_ms(path)
                resolved_end_ms = max_ts
            if resolved_end_ms is not None:
                start_ms = self._resolve_window_start_ms(days=int(days), end_timestamp_ms=int(resolved_end_ms))
                filters = [("timestamp", ">=", int(start_ms)), ("timestamp", "<=", int(resolved_end_ms))]

        frame = pd.read_parquet(path, columns=required_columns_subset, filters=filters)
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
            prepared = prepared.loc[required_mask].copy()
        if prepared.empty:
            return pd.DataFrame()

        timestamps = prepared["timestamp"].to_numpy(dtype=np.int64, copy=False)
        needs_sort = bool(timestamps.size > 1 and np.any(timestamps[1:] < timestamps[:-1]))
        if needs_sort:
            prepared = prepared.iloc[np.argsort(timestamps, kind="stable")].copy()
            timestamps = prepared["timestamp"].to_numpy(dtype=np.int64, copy=False)
        if timestamps.size > 1:
            unique_mask = np.ones(len(prepared), dtype=bool)
            unique_mask[:-1] = timestamps[:-1] != timestamps[1:]
            if not bool(unique_mask.all()):
                prepared = prepared.loc[unique_mask].copy()

        prepared["symbol"] = pd.Categorical.from_codes(
            np.zeros(len(prepared), dtype=np.int8),
            categories=[symbol],
        )
        return prepared

    def load_symbol_data_range(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Loads an exact timestamp window for one symbol from parquet cache."""
        symbol_dir_name = self._resolve_symbol_dir_name(symbol)
        if symbol_dir_name is None:
            return pd.DataFrame()

        path = self._cache_dir / symbol_dir_name / timeframe.value / SIMULATION_PARQUET_FILE_NAME
        if not path.exists():
            return pd.DataFrame()

        available_columns = set(self._get_available_columns(path))
        required_columns_subset = [column for column in self.INPUT_COLUMNS if column in available_columns]
        filters = [
            ("timestamp", ">=", int(start_timestamp_ms)),
            ("timestamp", "<=", int(end_timestamp_ms)),
        ]
        frame = pd.read_parquet(path, columns=required_columns_subset, filters=filters)
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
            prepared = prepared.loc[required_mask].copy()
        if prepared.empty:
            return pd.DataFrame()

        timestamps = prepared["timestamp"].to_numpy(dtype=np.int64, copy=False)
        needs_sort = bool(timestamps.size > 1 and np.any(timestamps[1:] < timestamps[:-1]))
        if needs_sort:
            prepared = prepared.iloc[np.argsort(timestamps, kind="stable")].copy()
            timestamps = prepared["timestamp"].to_numpy(dtype=np.int64, copy=False)
        if timestamps.size > 1:
            unique_mask = np.ones(len(prepared), dtype=bool)
            unique_mask[:-1] = timestamps[:-1] != timestamps[1:]
            if not bool(unique_mask.all()):
                prepared = prepared.loc[unique_mask].copy()

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
