"""Модуль проекта."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

import pandas as pd

from domain.enums.timeframe import Timeframe


class ParquetStorage:
    """Класс."""
    def __init__(self, base_dir: str | Path = "cache") -> None:
        self._base_dir = Path(base_dir)

    # region Приватные

    @staticmethod
    def encode_symbol_for_path(symbol: str) -> str:
        """Преобразует биржевой символ в безопасное имя каталога."""
        return quote(str(symbol), safe="")

    @staticmethod
    def decode_symbol_from_path(value: str) -> str:
        """Восстанавливает исходный биржевой символ из имени каталога."""
        return unquote(str(value))

    def _data_path(self, symbol: str, timeframe: Timeframe) -> Path:
        symbol_path = self.encode_symbol_for_path(symbol)
        return self._base_dir / symbol_path / timeframe.value / "data.parquet"

    @staticmethod
    def _ensure_utc_columns(data: pd.DataFrame) -> pd.DataFrame:
        normalized = data.copy()
        if "timestamp" not in normalized.columns:
            raise ValueError("data must contain 'timestamp' column")

        ts = pd.to_datetime(normalized["timestamp"], unit="ms", utc=True, errors="coerce")
        normalized = normalized.loc[ts.notna()].copy()
        ts = ts.loc[ts.notna()]

        normalized["timestamp"] = (ts.astype("int64") // 1_000_000).astype("int64")
        normalized["datetime"] = ts
        return normalized

    # endregion Приватные

    def load(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        """Загружает данные из parquet-файла."""
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_parquet(path)
        if frame.empty:
            return frame
        return self._ensure_utc_columns(frame)

    def get_last_timestamp(self, symbol: str, timeframe: Timeframe) -> pd.Timestamp | None:
        """Возвращает последнюю временную метку в хранилище."""
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None
        return pd.to_datetime(data["timestamp"], unit="ms", utc=True).max()

    def get_last_timestamp_for_column(
        self,
        symbol: str,
        timeframe: Timeframe,
        column_name: str,
    ) -> pd.Timestamp | None:
        """Возвращает последнюю метку времени для указанной колонки."""
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        if column_name == "timestamp":
            return pd.to_datetime(data["timestamp"], unit="ms", utc=True).max()

        if column_name not in data.columns:
            return None

        valid_rows = data[column_name].notna()
        if not valid_rows.any():
            return None

        return pd.to_datetime(data.loc[valid_rows, "timestamp"], unit="ms", utc=True).max()

    def save_incremental(self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame) -> int:
        """Дозаписывает новые данные без перезаписи старых."""
        if new_data.empty:
            return 0

        path = self._data_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        incoming = self._ensure_utc_columns(new_data)

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

        merged = self._ensure_utc_columns(merged)
        merged = merged.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
        merged.to_parquet(path, index=False)

        return max(len(merged) - previous_count, 0)
