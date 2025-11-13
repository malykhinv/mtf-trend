from dataclasses import dataclass
from typing import Optional


@dataclass
class CaptureState:
    def __init__(self) -> None:
        self.symbol: Optional[str] = None
