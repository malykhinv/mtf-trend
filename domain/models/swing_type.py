from enum import Enum


class SwingType(str, Enum):
    """
    Тип экстремума: high (вершина), low (дно), undefined (служебное).
    """
    HIGH = "high"
    LOW = "low"
    UNDEFINED = "undefined"

    @property
    def is_high(self) -> bool:
        return self == SwingType.HIGH

    @property
    def is_low(self) -> bool:
        return self == SwingType.LOW