from enum import Enum


class Scenario(str, Enum):
    FLAT_BOUNCE = "flat_bounce"                 # Отбой от границы
    FLAT_FAKE_BREAKOUT = "flat_fake_breakout"   # Ложный пробой
    TREND_CONTINUATION = "trend_continuation"   # Продолжение тренда (momentum)
    TREND_FAKE_BREAK = "trend_fake_break"       # Ложный слом тренда