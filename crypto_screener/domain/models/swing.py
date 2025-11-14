from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SwingType(Enum):
    LOW = "LOW"
    HIGH = "HIGH"


@dataclass(frozen=True)
class Swing:
    ts: datetime
    price: float
    type: SwingType
    is_open: bool
