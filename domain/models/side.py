from enum import Enum


class Side(str, Enum):
    """
    Сторона сделки (long/short/undefined).
    """
    LONG = "long"
    SHORT = "short"
    UNDEFINED = "undefined"

    @property
    def is_long(self) -> bool:
        """True если long/лонг."""
        return self == Side.LONG

    @property
    def is_short(self) -> bool:
        """True если short/шорт."""
        return self == Side.SHORT
