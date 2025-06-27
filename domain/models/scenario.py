from enum import Enum


class Scenario(str, Enum):
    FLAT_BOUNCE = "flat_bounce"
    FLAT_FAKE_BREAKOUT = "flat_fake_breakout"
    MOMENTUM_INITIATION = "momentum_initiation"
    MOMENTUM_PULLBACK = "momentum_pullback"