from dataclasses import dataclass, field
from typing import List


@dataclass
class CascadeLevel:
    price: float
    is_crossed: bool
    distance: int
    touches_raw: List[int] = field(default_factory=list)
