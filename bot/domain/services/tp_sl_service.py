from __future__ import annotations

from dataclasses import dataclass

from ..enums import Side
from ..models.entities import Signal, Thresholds, Trade


@dataclass(slots=True)
class TpSlResult:
    tp_price: float
    sl_price: float


class TpSlService:
    def __init__(self, risk_reward_ratio: float) -> None:
        self._risk_reward_ratio = risk_reward_ratio

    def assign(self, signal: Signal, trade: Trade) -> TpSlResult:
        thresholds: Thresholds = signal.thresholds
        atr = signal.metadata.get("metrics", {}).get("atr", 0.0)
        risk = atr * thresholds.sl_multiplier
        reward = risk * self._risk_reward_ratio
        entry_price = trade.entry_price
        if signal.side == Side.LONG:
            tp = entry_price + reward
            sl = entry_price - risk
        else:
            tp = entry_price - reward
            sl = entry_price + risk
        trade.tp_price = tp
        trade.sl_price = sl
        return TpSlResult(tp_price=tp, sl_price=sl)
