from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

