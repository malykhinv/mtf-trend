"""Parquet storage with symbol/timeframe partition layout and incremental updates."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe


class ParquetStorage:
    """Store and incrementally update time-series in `{symbol}/{timeframe}/data.parquet`."""

    def __init__(self, base_dir: str | Path = "cache") -> None:
        self._base_dir = Path(base_dir)

    def _data_path(self, symbol: str, timeframe: Timeframe) -> Path:
        return self._base_dir / symbol / timeframe.value / "data.parquet"

    def load(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def get_last_timestamp(self, symbol: str, timeframe: Timeframe) -> pd.Timestamp | None:
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None
        return pd.to_datetime(data["timestamp"], unit="ms", utc=True).max()

    def save_incremental(self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame) -> int:
        if new_data.empty:
            return 0

        path = self._data_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        incoming = new_data.copy()
        if "timestamp" not in incoming.columns:
            raise ValueError("new_data must contain 'timestamp' column")

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

        merged = merged.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
        merged.to_parquet(path, index=False)

        return max(len(merged) - previous_count, 0)
