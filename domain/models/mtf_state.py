from dataclasses import dataclass
from typing import Literal, List

from domain.models.swing_point import SwingPoint


@dataclass
class MTFState:
    timeframe: str
    trend: Literal['up', 'down', 'flat']
    structure: List[SwingPoint]
    is_in_correction: bool
    rr_potential: float
