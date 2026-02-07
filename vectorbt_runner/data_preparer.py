"""Data preparation helpers for strategy/backtest pipeline."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe


class DataPreparer:
    """Loads cached parquet data and normalizes it for backtest processing."""

    REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)

    def list_symbols(self, timeframe: Timeframe) -> list[str]:
        symbols: list[str] = []
        if not self._cache_dir.exists():
            return symbols

        for symbol_dir in self._cache_dir.iterdir():
            path = symbol_dir / timeframe.value / "data.parquet"
            if symbol_dir.is_dir() and path.exists():
                symbols.append(symbol_dir.name)
        return sorted(symbols)

    def load_symbol_data(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        path = self._cache_dir / symbol / timeframe.value / "data.parquet"
        if not path.exists():
            return pd.DataFrame()

        frame = pd.read_parquet(path)
        missing = [col for col in self.REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            return pd.DataFrame()

        normalized = frame.copy()
        normalized["symbol"] = symbol
        normalized["datetime"] = pd.to_datetime(normalized["timestamp"], unit="ms", utc=True, errors="coerce")
        normalized = normalized.dropna(subset=["datetime"])

        numeric_cols = [col for col in ("open", "high", "low", "close", "volume", "open_interest") if col in normalized.columns]
        for col in numeric_cols:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

        normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"])
        normalized = normalized.sort_values("datetime").drop_duplicates(subset=["timestamp"], keep="last")
        return normalized.reset_index(drop=True)
