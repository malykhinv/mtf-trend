from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ExtremumType(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(slots=True)
class Extremum:
    time: datetime
    price: float
    type: ExtremumType

