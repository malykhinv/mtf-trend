"""Модуль проекта."""

from __future__ import annotations

import pandas as pd


class Deduplicator:
    """Класс."""
    @staticmethod
    def deduplicate(data: pd.DataFrame) -> pd.DataFrame:
        """Удаляет дублирующиеся строки по ключевым полям."""
        if data.empty:
            return data.copy()

        if "timestamp" not in data.columns:
            msg = "DataFrame must contain 'timestamp' column"
            raise ValueError(msg)

        return (
            data.sort_values("timestamp")
            .drop_duplicates(subset=["timestamp"], keep="last")
            .reset_index(drop=True)
        )
