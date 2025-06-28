from dataclasses import dataclass
from domain.models.swing_type import SwingType


@dataclass
class SwingPoint:
    type: SwingType
    index: int
    price: float
    confirmed: bool

    @classmethod
    def undefined(cls):
        return cls(
            type=SwingType.UNDEFINED,
            index=-1,
            price=0.0,
            confirmed=False
        )