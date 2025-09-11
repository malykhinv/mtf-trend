from enum import Enum, StrEnum, auto


class Profile(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    ACTIVE = "ACTIVE"


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class CandleInterval(StrEnum):
    M1 = "1m"
    M5 = "5m"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class BotState(Enum):
    IDLE = auto()
    WATCHING = auto()
    CONFIRMING = auto()
    ENTERED = auto()
    EXITING = auto()
    COOLDOWN = auto()
