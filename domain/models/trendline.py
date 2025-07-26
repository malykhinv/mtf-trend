from dataclasses import dataclass
from datetime import datetime

@dataclass
class Trendline:
    """Простая наклонная линия, построенная по двум точкам."""

    k: float
    b: float
    point1_time: datetime
    point2_time: datetime
    valid: bool = True

    def get_value_at_time(self, ts: datetime) -> float:
        """Возвращает значение линии в указанный момент времени."""
        return self.k * ts.timestamp() + self.b
