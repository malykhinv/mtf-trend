from __future__ import annotations

from dataclasses import dataclass

from domain.models.trading import PositionPlan
from domain.models.enums import Side
from infrastructure.telegram import send_message


@dataclass
class TelegramClient:
    """Simple Telegram client wrapper."""

    token: str
    chat_id: str

    def send(self, text: str) -> None:
        send_message(self.token, self.chat_id, text)


class NotificationService:
    """Utility class for sending trading notifications."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    def notify_order_open(self, plan: PositionPlan) -> None:
        msg = (
            f"Open {plan.symbol} \n"
            f"Entry: {plan.entry_price:.4f} | SL: {plan.stop_loss:.4f} \n"
            f"TP1: {plan.take_profit1:.4f} | TP2: {plan.take_profit2:.4f} \n"
            f"Qty: {plan.quantity:.4f}"
        )
        self._client.send(msg)

    def notify_tp_hit(self, plan: PositionPlan, side: Side, price: float, level: int) -> None:
        pnl = self._pnl_pct(plan.entry_price, price, side)
        msg = (
            f"TP{level} hit {plan.symbol} @ {price:.4f} ({pnl:.2f}%)\n"
            f"SL: {plan.stop_loss:.4f} | TP2: {plan.take_profit2:.4f}"
        )
        self._client.send(msg)

    def notify_stop(self, plan: PositionPlan, side: Side, price: float) -> None:
        pnl = self._pnl_pct(plan.entry_price, price, side)
        msg = (
            f"Stop hit {plan.symbol} @ {price:.4f} ({pnl:.2f}%)\n"
            f"SL: {plan.stop_loss:.4f}"
        )
        self._client.send(msg)

    def notify_trail_update(self, plan: PositionPlan, side: Side, price: float) -> None:
        pnl = self._pnl_pct(plan.entry_price, price, side)
        msg = (
            f"Trail update {plan.symbol} @ {price:.4f} ({pnl:.2f}%)\n"
            f"New SL: {plan.stop_loss:.4f}"
        )
        self._client.send(msg)

    @staticmethod
    def _pnl_pct(entry: float, price: float, side: Side) -> float:
        diff = price - entry
        if side is Side.SHORT:
            diff = entry - price
        return diff / entry * 100.0
