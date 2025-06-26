from dataclasses import dataclass
from domain.models.swing_type import SwingType


@dataclass
class SwingPoint:
    type: SwingType
    index: int
    price: float
    confirmed: bool