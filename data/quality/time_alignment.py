"""Модуль проекта."""

from __future__ import annotations

import pandas as pd


class TimeAlignment:
    """Класс."""

    @staticmethod
    def align_to_utc(data: pd.DataFrame) -> pd.DataFrame:
        """Возвращает данные без нормализации временных меток."""
        if data.empty:
            return data.copy()

        if "timestamp" not in data.columns and "datetime" not in data.columns:
            msg = "DataFrame must contain 'timestamp' or 'datetime' column"
            raise ValueError(msg)

        return data.copy().sort_values("timestamp" if "timestamp" in data.columns else "datetime").reset_index(drop=True)
