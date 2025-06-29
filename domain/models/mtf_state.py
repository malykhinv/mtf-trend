from dataclasses import dataclass
from typing import List

from domain.models.phase import Phase
from domain.models.price_direction import PriceDirection
from domain.models.swing_point import SwingPoint
from domain.models.timeframe import Timeframe


@dataclass
class MTFState:
    timeframe: Timeframe
    phase: Phase
    structure: List[SwingPoint]
    is_in_correction: bool
    correction_direction: PriceDirection
    is_range: bool
    range_high: float
    range_low: float
