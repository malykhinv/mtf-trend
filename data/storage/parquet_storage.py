"""Модуль проекта."""

from __future__ import annotations

from logging import INFO
from pathlib import Path
from urllib.parse import quote, unquote

import pandas as pd

from domain.enums.timeframe import Timeframe
from utils.logger import get_logger


class ParquetCacheValidationError(ValueError):
    """Ошибка валидации записанного parquet-кэша."""


class ParquetStorage:
    """Класс."""

    def __init__(
        self,
        base_dir: str | Path = "cache",
        log_level: int | str = INFO,
        logs_dir: str | Path = "logs",
    ) -> None:
        self._base_dir = Path(base_dir)
        self._logger = get_logger(name="parquet-storage", level=log_level, logs_dir=logs_dir)

    @staticmethod
    def encode_symbol_for_path(symbol: str) -> str:
        return quote(str(symbol), safe="")

    @staticmethod
    def decode_symbol_from_path(value: str) -> str:
        return unquote(str(value))

    def _data_path(self, symbol: str, timeframe: Timeframe) -> Path:
        return self._base_dir / self.encode_symbol_for_path(symbol) / timeframe.value / "data.parquet"

    @staticmethod
    def _ensure_columns(data: pd.DataFrame) -> pd.DataFrame:
        if "timestamp" not in data.columns:
            raise ValueError("data must contain 'timestamp' column")

        prepared = data.copy()
        prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
        prepared = prepared.loc[prepared["timestamp"].notna()].copy()
        prepared["timestamp"] = prepared["timestamp"].astype("int64")
        return prepared

    def _validate_written_cache(
        self,
        symbol: str,
        timeframe: Timeframe,
        previous_count: int,
        incoming: pd.DataFrame,
        merged: pd.DataFrame,
        path_override: Path | None = None,
    ) -> None:
        path = path_override or self._data_path(symbol, timeframe)
        written = pd.read_parquet(path)
        added_rows = max(len(merged) - previous_count, 0)

        if len(written) < previous_count or len(written) != previous_count + added_rows:
            raise ParquetCacheValidationError(
                "parquet cache validation failed: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, "
                f"expected_rows={previous_count}+{added_rows}, actual_rows={len(written)}"
            )

        if "timestamp" not in written.columns:
            raise ParquetCacheValidationError(
                "parquet cache validation failed: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, missing_column=timestamp"
            )

        incoming_ts = set(pd.to_numeric(incoming["timestamp"], errors="coerce").dropna().astype("int64").tolist())
        written_ts = set(pd.to_numeric(written["timestamp"], errors="coerce").dropna().astype("int64").tolist())
        missing = incoming_ts - written_ts
        if missing:
            raise ParquetCacheValidationError(
                "parquet cache validation failed: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, "
                f"missing_written_batch_rows={sorted(list(missing))[:10]}"
            )

    def load(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_parquet(path)
        if frame.empty:
            return frame
        return self._ensure_columns(frame)

    def get_last_timestamp(self, symbol: str, timeframe: Timeframe) -> int | None:
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        timestamps = pd.to_numeric(data["timestamp"], errors="coerce").dropna()
        if timestamps.empty:
            return None
        return int(timestamps.max())

    def get_last_timestamp_for_column(self, symbol: str, timeframe: Timeframe, column_name: str) -> int | None:
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        if column_name == "timestamp":
            return self.get_last_timestamp(symbol, timeframe)

        if column_name not in data.columns:
            return None

        valid_rows = data[column_name].notna()
        if not valid_rows.any():
            return None

        timestamps = pd.to_numeric(data.loc[valid_rows, "timestamp"], errors="coerce").dropna()
        if timestamps.empty:
            return None
        return int(timestamps.max())

    def save_incremental(self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame) -> int:
        if new_data.empty:
            return 0

        path = self._data_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        incoming = self._ensure_columns(new_data)
        existing = self.load(symbol, timeframe)
        previous_count = len(existing)

        if existing.empty:
            merged = incoming
        else:
            merged = pd.merge(existing, incoming, on="timestamp", how="outer", suffixes=("", "__new"))
            for col in list(merged.columns):
                if not col.endswith("__new"):
                    continue
                base_col = col[:-5]
                if base_col in merged.columns:
                    merged[base_col] = merged[col].combine_first(merged[base_col])
                    merged = merged.drop(columns=[col])
                else:
                    merged = merged.rename(columns={col: base_col})

        merged = self._ensure_columns(merged)
        merged = merged.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        merged.to_parquet(tmp_path, index=False)

        self._validate_written_cache(
            symbol=symbol,
            timeframe=timeframe,
            previous_count=previous_count,
            incoming=incoming,
            merged=merged,
            path_override=tmp_path,
        )

        tmp_path.replace(path)
        return max(len(merged) - previous_count, 0)
