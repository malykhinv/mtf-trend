from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"
    UNDEFINED = "undefined"

    @property
    def is_long(self) -> bool:
        return self == Side.LONG

    @property
    def is_short(self) -> bool:
        return self == Side.SHORT
