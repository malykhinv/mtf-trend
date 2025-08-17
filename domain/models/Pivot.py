from dataclasses import dataclass

from domain.models.ExtremumType import ExtremumType


@dataclass
class Pivot:
    idx: int
    price: float
    type: ExtremumType