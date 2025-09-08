from __future__ import annotations

import logging
from dataclasses import dataclass

from domain.models.trading import PositionPlan
from domain.models.enums import Side
from infrastructure.telegram import send_message

logger = logging.getLogger(__name__)


@dataclass
class TelegramClient:
    """Simple Telegram client wrapper."""

    token: str
    chat_id: str

    def send(self, text: str) -> bool:
        ok, desc = send_message(self.token, self.chat_id, text)
        if not ok:
            logger.error("Failed to send Telegram message: %s", desc)
            return False
        return True


class NotificationService:
    """Utility class for sending trading notifications."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    def notify_order_open(
        self, plan: PositionPlan, side: Side, actual_price: float | None = None
    ) -> None:
        price = plan.entry_price if actual_price is None else actual_price
        msg = f"Open {side} {plan.symbol} @ {price:.4f}"
        if actual_price is not None:
            pnl = self._pnl_pct(plan.entry_price, actual_price, side)
            msg += f" ({pnl:.2f}%)\n"
        else:
            msg += "\n"
        msg += (
            f"SL: {plan.stop_loss:.4f} | TP1: {plan.take_profit1:.4f} | TP2: {plan.take_profit2:.4f}\n"
            f"Qty left: {plan.quantity:.4f}"
        )
        self._send(msg)

    def notify_tp_hit(
        self, plan: PositionPlan, side: Side, price: float, level: int, remaining: float
    ) -> None:
        pnl = self._pnl_pct(plan.entry_price, price, side)
        msg = f"TP{level} hit {plan.symbol} {side} @ {price:.4f} ({pnl:.2f}%)"
        if level == 1:
            msg += (
                f"\nSL: {plan.stop_loss:.4f} | TP2: {plan.take_profit2:.4f} | Qty left: {remaining:.4f}"
            )
        else:
            if remaining > 0.0:
                msg += f"\nQty left: {remaining:.4f}"
            else:
                msg += "\nPosition closed"
        self._send(msg)

    def notify_stop(
        self, plan: PositionPlan, side: Side, price: float, remaining: float
    ) -> None:
        pnl = self._pnl_pct(plan.entry_price, price, side)
        msg = (
            f"Stop hit {plan.symbol} {side} @ {price:.4f} ({pnl:.2f}%)\n"
            f"Qty left: {remaining:.4f}"
        )
        self._send(msg)

    def notify_trail_update(
        self, plan: PositionPlan, side: Side, price: float, remaining: float
    ) -> None:
        pnl = self._pnl_pct(plan.entry_price, price, side)
        msg = (
            f"Trail update {plan.symbol} {side} @ {price:.4f} ({pnl:.2f}%)\n"
            f"New SL: {plan.stop_loss:.4f} | Qty left: {remaining:.4f}"
        )
        self._send(msg)

    def _send(self, msg: str) -> None:
        if not self._client.send(msg):
            logger.error("Failed to send notification")

    @staticmethod
    def _pnl_pct(entry: float, price: float, side: Side) -> float:
        diff = price - entry
        if side is Side.SHORT:
            diff = entry - price
        return diff / entry * 100.0
