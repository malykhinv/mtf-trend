from __future__ import annotations

from dataclasses import dataclass

from ..enums import BreakDirection, Side, TradeStatus
from ..models.entities import Candle, Signal, Trade


@dataclass(slots=True)
class TpSlResult:
    tp_price: float
    sl_price: float
    tp_pct: float
    sl_pct: float


class TpSlService:
    def assign(self, signal: Signal, trade: Trade) -> TpSlResult:
        candle = signal.candle
        if signal.side == Side.LONG:
            tp = candle.high
            sl = candle.low
            tp_pct = self._calculate_long_pct(trade.entry_price, tp)
            sl_pct = self._calculate_long_pct(trade.entry_price, sl)
        else:
            tp = candle.low
            sl = candle.high
            tp_pct = self._calculate_short_pct(trade.entry_price, tp)
            sl_pct = self._calculate_short_pct(trade.entry_price, sl)
        trade.tp_price = tp
        trade.sl_price = sl
        trade.tp_pct = tp_pct
        trade.sl_pct = sl_pct
        return TpSlResult(tp_price=tp, sl_price=sl, tp_pct=tp_pct, sl_pct=sl_pct)

    def check_levels(self, trade: Trade, candle: Candle) -> tuple[bool, bool]:
        tp_price = trade.tp_price
        sl_price = trade.sl_price
        if tp_price is None or sl_price is None:
            return False, False
        if trade.side == Side.LONG:
            hit_tp = candle.high >= tp_price
            hit_sl = candle.low <= sl_price
        else:
            hit_tp = candle.low <= tp_price
            hit_sl = candle.high >= sl_price
        return hit_tp, hit_sl

    def resolve_status(
        self,
        signal: Signal,
        trade: Trade,
        candle: Candle,
        hit_tp: bool,
        hit_sl: bool,
    ) -> TradeStatus | None:
        if not hit_tp and not hit_sl:
            return None
        if hit_tp and hit_sl:
            direction = signal.direction
            if direction == BreakDirection.HIGH_FIRST:
                hit_sl = False
            elif direction == BreakDirection.LOW_FIRST:
                hit_tp = False
            else:
                tp_price = trade.tp_price
                sl_price = trade.sl_price
                if trade.side == Side.LONG:
                    if tp_price is not None and candle.open >= tp_price:
                        hit_sl = False
                    elif sl_price is not None and candle.open <= sl_price:
                        hit_tp = False
                else:
                    if tp_price is not None and candle.open <= tp_price:
                        hit_sl = False
                    elif sl_price is not None and candle.open >= sl_price:
                        hit_tp = False
        if hit_tp:
            return TradeStatus.CLOSED_TP
        if hit_sl:
            return TradeStatus.CLOSED_SL
        return None

    @staticmethod
    def _calculate_long_pct(entry_price: float, target_price: float) -> float:
        return (target_price - entry_price) / entry_price * 100 if entry_price else 0.0

    @staticmethod
    def _calculate_short_pct(entry_price: float, target_price: float) -> float:
        return (entry_price - target_price) / entry_price * 100 if entry_price else 0.0
