from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass(frozen=True, slots=True)
class RadarEvent:
    symbol: str
    t0: Optional[datetime] = None
