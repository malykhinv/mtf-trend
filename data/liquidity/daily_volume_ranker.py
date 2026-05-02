"""Расчёт среднедневного объёма торгов в USD по кэшу OHLCV."""

from __future__ import annotations

from numbers import Integral
from pathlib import Path

from domain.enums.timeframe import Timeframe
from vectorbt_runner.data_preparer import DataPreparer


class DailyVolumeRanker:
    """Ранжирует символы по среднему дневному USD-объёму из кэша."""

    _UNIX_MS_MIN = 1_000_000_000_000
    _UNIX_MS_MAX = 9_999_999_999_999

    @classmethod
    def _is_unix_ms(cls, value: object) -> bool:
        return (
            isinstance(value, Integral)
            and not isinstance(value, bool)
            and cls._UNIX_MS_MIN <= int(value) <= cls._UNIX_MS_MAX
        )

    def __init__(self, cache_dir: Path) -> None:
        self._preparer = DataPreparer(cache_dir)

    def calculate_avg_daily_volume_usd(
            self,
            symbols: list[str],
            timeframe: Timeframe,
    ) -> dict[str, float]:
        """Возвращает средний дневной USD-объём по каждому символу."""
        result: dict[str, float] = {}

        for symbol in symbols:
            frame = self._preparer.load_symbol_data(symbol, timeframe)
            if frame.empty:
                result[symbol] = 0.0
                continue

            if "timestamp" not in frame.columns or "close" not in frame.columns or "volume" not in frame.columns:
                result[symbol] = 0.0
                continue

            prepared = frame.copy()
            prepared = prepared.dropna(subset=["timestamp", "close", "volume"])
            if prepared.empty:
                result[symbol] = 0.0
                continue

            invalid_timestamp_mask = ~prepared["timestamp"].map(self._is_unix_ms)
            invalid_timestamp_count = int(invalid_timestamp_mask.sum())
            if invalid_timestamp_count > 0:
                result[symbol] = 0.0
                continue

            prepared["daily_volume_usd"] = prepared["close"] * prepared["volume"]

            daily_volume = prepared.sort_values("timestamp").dropna(subset=["daily_volume_usd"])["daily_volume_usd"]
            if daily_volume.empty:
                result[symbol] = 0.0
                continue

            avg_daily_volume_usd = float(daily_volume.mean())
            if avg_daily_volume_usd <= 0:
                result[symbol] = 0.0
                continue

            result[symbol] = avg_daily_volume_usd

        return result
