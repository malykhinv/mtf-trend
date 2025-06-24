from dataclasses import dataclass
from typing import Literal

@dataclass
class SwingPoint:
    index: int
    price: float
    kind: Literal['high', 'low']
    confirmed: bool