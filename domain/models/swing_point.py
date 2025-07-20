from dataclasses import dataclass
from datetime import datetime

from config.constants import FLOAT_UNDEFINED
from domain.models.swing_type import SwingType


@dataclass
class SwingPoint:
    type: SwingType
    timestamp: datetime
    index: int
    price: float

    @classmethod
    def undefined(cls):
        return cls(
            type=SwingType.UNDEFINED,
            timestamp=datetime.now(),
            index=-1,
            price=FLOAT_UNDEFINED,
        )

    @property
    def is_undefined(self) -> bool:
        return self.type == SwingType.UNDEFINED