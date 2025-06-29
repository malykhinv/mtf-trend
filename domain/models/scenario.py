from enum import Enum


class Scenario(str, Enum):
    FLAT_BOUNCE = "Flat bounce"
    FLAT_FAKE_BREAKOUT = "Flat fake breakout"
    MOMENTUM_INITIATION = "Momentum initiation"
    MOMENTUM_PULLBACK = "Momentum pullback"