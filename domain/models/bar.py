from dataclasses import dataclass
from datetime import datetime

from config.constants import FLOAT_UNDEFINED


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float = FLOAT_UNDEFINED
