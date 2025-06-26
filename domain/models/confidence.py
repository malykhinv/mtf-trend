from enum import Enum


class Confidence(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"