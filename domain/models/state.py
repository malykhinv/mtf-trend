from dataclasses import dataclass, field
from typing import Optional

from .enums import BotState, Profile, Side
from .metrics import SymbolMetrics
from .trading import PositionPlan


@dataclass
class Position:
    side: Side
    plan: PositionPlan


@dataclass
class SymbolState:
    symbol: str
    state: BotState
    metrics: SymbolMetrics = field(default_factory=SymbolMetrics)
    position: Optional[Position] = None
    last_signal_ts: Optional[float] = None


@dataclass
class GlobalState:
    profile: Profile
    btc_pause_until_ms: Optional[int]
