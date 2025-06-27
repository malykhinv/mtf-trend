from enum import Enum


class Confidence(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"

    @property
    def is_weak(self) -> bool:
        return self == Confidence.WEAK

    @property
    def is_moderate(self) -> bool:
        return self == Confidence.MODERATE

    @property
    def is_strong(self) -> bool:
        return self == Confidence.STRONG