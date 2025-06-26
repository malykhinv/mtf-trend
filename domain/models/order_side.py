from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
