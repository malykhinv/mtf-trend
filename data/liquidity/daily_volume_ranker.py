"""Расчёт среднедневного объёма торгов в USD по кэшу OHLCV."""

from __future__ import annotations

from logging import Logger
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from vectorbt_runner.data_preparer import DataPreparer


class DailyVolumeRanker:
    """Ранжирует символы по среднему дневному USD-объёму из кэша."""

    def __init__(self, cache_dir: Path) -> None:
        self._preparer = DataPreparer(cache_dir)

    def calculate_avg_daily_volume_usd(
            self,
            symbols: list[str],
            timeframe: Timeframe,
            logger: Logger,
    ) -> dict[str, float]:
        """Возвращает средний дневной USD-объём по каждому символу."""
        result: dict[str, float] = {}

        for symbol in symbols:
            frame = self._preparer.load_symbol_data(symbol, timeframe)
            if frame.empty:
                logger.info(
                    "ликвидность-кэш: символ=%s исключён: отсутствуют данные в кэше (%s)",
                    symbol,
                    timeframe.value,
                )
                continue

            if "timestamp" not in frame.columns or "close" not in frame.columns or "volume" not in frame.columns:
                logger.info(
                    "ликвидность-кэш: символ=%s исключён: отсутствуют обязательные колонки для расчёта",
                    symbol,
                )
                continue

            prepared = frame.copy()
            prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
            prepared = prepared.dropna(subset=["timestamp", "close", "volume"])
            if prepared.empty:
                logger.info(
                    "ликвидность-кэш: символ=%s исключён: после очистки не осталось валидных строк",
                    symbol,
                )
                continue

            prepared["timestamp"] = prepared["timestamp"].astype("int64")
            prepared["daily_volume_usd"] = prepared["close"] * prepared["volume"]

            daily_volume = prepared.sort_values("timestamp").dropna(subset=["daily_volume_usd"])["daily_volume_usd"]
            if daily_volume.empty:
                logger.info(
                    "ликвидность-кэш: символ=%s исключён: не удалось посчитать дневной USD-объём",
                    symbol,
                )
                continue

            avg_daily_volume_usd = float(daily_volume.mean())
            if avg_daily_volume_usd <= 0:
                logger.info(
                    "ликвидность-кэш: символ=%s исключён: среднедневной объём <= 0 (%.6f)",
                    symbol,
                    avg_daily_volume_usd,
                )
                continue

            result[symbol] = avg_daily_volume_usd

        return result
