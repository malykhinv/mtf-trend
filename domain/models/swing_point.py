from dataclasses import dataclass

from config.constants import FLOAT_UNDEFINED
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
            price=FLOAT_UNDEFINED,
            confirmed=False
        )

    @property
    def is_undefined(self) -> bool:
        return self.type == SwingType.UNDEFINED