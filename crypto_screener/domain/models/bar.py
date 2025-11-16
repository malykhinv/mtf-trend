from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from crypto_screener.domain.models.swing import Swing


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    swing: Optional[Swing]
