from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SetupType(Enum):
    NONE = "NONE"
    CAPTURE = "CAPTURE"
    ORDER = "ORDER"


@dataclass(frozen=True)
class Setup:
    type: SetupType
    symbol: str
    timeframe: str
    ts: datetime
