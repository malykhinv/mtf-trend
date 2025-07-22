from enum import Enum

_MINUTES_MAP = {
    "m": 1,
    "h": 60,
    "d": 1440,
}

class Timeframe(str, Enum):
    D1 = "1d"
    H4 = "4h"
    H1 = "1h"
    M30 = "30m"
    M15 = "15m"
    M5 = "5m"
    M3 = "3m"
    M1 = "1m"

    @property
    def minutes(self) -> int:
        """Возвращает количество минут для данного таймфрейма."""
        num, unit = int(self.value[:-1]), self.value[-1]
        return num * _MINUTES_MAP.get(unit, 1)
