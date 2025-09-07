from dataclasses import dataclass
from typing import Optional

from .enums import BotState, Profile, Side


@dataclass
class Position:
    side: Side
    entry_price: float
    quantity: float


@dataclass
class SymbolState:
    symbol: str
    state: BotState
    position: Optional[Position] = None
    last_signal_ts: Optional[float] = None


@dataclass
class GlobalState:
    profile: Profile
    btc_pause_until_ms: Optional[int]
