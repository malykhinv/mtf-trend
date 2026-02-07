"""Trade result categories."""

from enum import Enum


class TradeResultType(str, Enum):
    SL = "SL"
    BE = "BE"
    TP1_BE = "TP1_BE"
    TP2 = "TP2"
