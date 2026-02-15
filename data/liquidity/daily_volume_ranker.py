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

            if "datetime" not in frame.columns or "close" not in frame.columns or "volume" not in frame.columns:
                logger.info(
                    "ликвидность-кэш: символ=%s исключён: отсутствуют обязательные колонки для расчёта",
                    symbol,
                )
                continue

            normalized = frame.copy()
            normalized["datetime"] = pd.to_datetime(normalized["datetime"], errors="coerce")
            normalized = normalized.dropna(subset=["datetime", "close", "volume"])
            if normalized.empty:
                logger.info(
                    "ликвидность-кэш: символ=%s исключён: после очистки не осталось валидных строк",
                    symbol,
                )
                continue

            normalized["date"] = normalized["datetime"].dt.floor("D")
            normalized["daily_volume_usd"] = normalized["close"] * normalized["volume"]

            daily_volume = normalized.groupby("date", as_index=True)["daily_volume_usd"].sum(min_count=1).dropna()
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

