from enum import Enum


class Phase(str, Enum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    FLAT = "flat"