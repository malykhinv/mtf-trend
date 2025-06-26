from dataclasses import dataclass
from typing import List, Optional

from domain.models.price_direction import PriceDirection
from domain.models.phase import Phase
from domain.models.swing_point import SwingPoint
from domain.models.timeframe import Timeframe


@dataclass
class MTFState:
    timeframe: Timeframe
    phase: Optional[Phase]
    structure: List[SwingPoint]
    is_in_correction: bool
    correction_direction: Optional[PriceDirection]
    rr_potential: float
    is_range: bool
    range_high: Optional[float]
    range_low: Optional[float]
