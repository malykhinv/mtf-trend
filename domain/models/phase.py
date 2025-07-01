from enum import Enum


class Phase(str, Enum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    FLAT = "flat"
    UNDEFINED = "undefined"

    @property
    def is_uptrend(self) -> bool:
        return self == Phase.UPTREND

    @property
    def is_downtrend(self) -> bool:
        return self == Phase.DOWNTREND

    @property
    def is_trend(self) -> bool:
        return self.is_uptrend or self.is_downtrend

    @property
    def is_flat(self) -> bool:
        return self == Phase.FLAT