from dataclasses import dataclass


@dataclass
class Trendline:
    k: float
    b: float
    point1_index: int
    point2_index: int
    valid: bool = True

    def get_value_at(self, x: int) -> float:
        return self.k * x + self.b
