from __future__ import annotations

from dataclasses import dataclass

from ..enums import Side
from ..models.entities import Signal, Trade


@dataclass(slots=True)
class TpSlResult:
    tp_price: float
    sl_price: float


class TpSlService:
    def assign(self, signal: Signal, trade: Trade) -> TpSlResult:
        candle = signal.candle
        if signal.side == Side.LONG:
            tp = candle.high
            sl = candle.low
        else:
            tp = candle.low
            sl = candle.high
        trade.tp_price = tp
        trade.sl_price = sl
        return TpSlResult(tp_price=tp, sl_price=sl)
