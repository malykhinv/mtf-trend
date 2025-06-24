from dataclasses import dataclass
from typing import List, Literal
from domain.models.swing_point import SwingPoint

@dataclass
class MTFState:
    timeframe: str
    trend: Literal['up', 'down', 'flat']
    structure: List[SwingPoint]
    is_in_correction: bool
    correction_direction: Literal['up', 'down', 'none']
    rr_potential: float
    is_range: bool
    range_high: float
    range_low: float
