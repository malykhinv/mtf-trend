"""Parquet storage with UTC timestamp persistence for symbol/timeframe partitions."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe


class ParquetStorage:
    """Store and incrementally update time-series in `{symbol}/{timeframe}/data.parquet`."""

    def __init__(self, base_dir: str | Path = "cache") -> None:
        self._base_dir = Path(base_dir)

    # область Приватные

    def _data_path(self, symbol: str, timeframe: Timeframe) -> Path:
        return self._base_dir / symbol / timeframe.value / "data.parquet"

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

    # конец области Приватные

    def load(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_parquet(path)
        if frame.empty:
            return frame
        return self._ensure_utc_columns(frame)

    def get_last_timestamp(self, symbol: str, timeframe: Timeframe) -> pd.Timestamp | None:
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
