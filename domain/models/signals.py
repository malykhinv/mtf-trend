from dataclasses import dataclass

from .enums import Side
from . import metrics as M


@dataclass(frozen=True)
class PumpSignal:
    symbol: str
    window: M.PumpWindow


@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    side: Side
    price: float


@dataclass(frozen=True)
class ExitSignal:
    symbol: str
    reason: str
