from enum import Enum

class Timeframe(str, Enum):
    D1 = "1d"
    H4 = "4h"
    H1 = "1h"
    M15 = "15m"
    M5 = "5m"
    M1 = "1m"