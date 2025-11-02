from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BinanceTradeSettings:
    new_client_order_id_prefix: Optional[str]
    reduce_only: bool
    close_position: bool


__all__ = ["BinanceTradeSettings"]
