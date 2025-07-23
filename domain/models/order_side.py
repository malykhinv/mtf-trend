from enum import Enum


class OrderSide(str, Enum):
    """
    Направление заявки (buy/sell) на бирже.
    """
    BUY = "buy"
    SELL = "sell"
