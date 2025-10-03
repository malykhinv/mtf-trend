from typing import Union

from domain.models import OrderBookSnapshot, OrderBookUpdate

DepthStreamData = Union[OrderBookSnapshot, OrderBookUpdate]

__all__ = ["DepthStreamData"]
