from __future__ import annotations

from dataclasses import replace
from typing import Dict, List

from domain.models import signals as S
from domain.models.enums import OrderType, Side
from domain.models.trading import OrderSpec, PositionPlan

from .risk_manager import RiskManager
from .trader import Trader


class TradeManager:
    """High level wrapper around :class:`Trader` handling position state."""

    def __init__(self, trader: Trader, risk_manager: RiskManager) -> None:
        self._trader = trader
        self._risk_manager = risk_manager
        self._positions: Dict[str, tuple[Side, PositionPlan]] = {}

    def open_position(self, plan: PositionPlan, side: Side) -> None:
        order = OrderSpec(
            symbol=plan.symbol,
            side=side,
            type=OrderType.MARKET,
            quantity=plan.quantity,
        )
        self._trader.place(order)
        self._positions[plan.symbol] = (side, plan)

    def _close_position(self, symbol: str, side: Side, plan: PositionPlan) -> None:
        exit_side = Side.LONG if side is Side.SHORT else Side.SHORT
        order = OrderSpec(
            symbol=plan.symbol,
            side=exit_side,
            type=OrderType.MARKET,
            quantity=plan.quantity,
        )
        self._trader.place(order)
        self._risk_manager.release(plan)
        del self._positions[symbol]

    def on_tick_manage(
        self, symbol: str, price: float | None = None
    ) -> tuple[list[S.ExitSignal], PositionPlan | None]:
        """Manage an existing position on each price tick."""

        record = self._positions.get(symbol)
        exits: List[S.ExitSignal] = []
        if record is None:
            return exits, None

        side, plan = record
        current_price = plan.entry_price if price is None else price

        # ------------------------------------------------------------------
        # Take profit levels
        tp1_hit = plan.tp1_qty > 0.0 and (
            (side is Side.SHORT and current_price <= plan.take_profit1)
            or (side is Side.LONG and current_price >= plan.take_profit1)
        )
        if tp1_hit:
            exits.append(S.ExitSignal(symbol=symbol, reason="TP1"))
            exit_side = Side.LONG if side is Side.SHORT else Side.SHORT
            order = OrderSpec(
                symbol=plan.symbol,
                side=exit_side,
                type=OrderType.MARKET,
                quantity=plan.tp1_qty,
            )
            self._trader.place(order)
            self._risk_manager.release(replace(plan, quantity=plan.tp1_qty))
            plan = PositionPlan(
                symbol=plan.symbol,
                entry_price=plan.entry_price,
                stop_loss=plan.stop_loss,
                take_profit1=0.0,
                take_profit2=plan.take_profit2,
                trail_start=plan.trail_start,
                trail_distance=plan.trail_distance,
                quantity=plan.quantity - plan.tp1_qty,
                tp1_qty=0.0,
                tp2_qty=plan.tp2_qty,
                tail_qty=plan.tail_qty,
            )
            self._positions[symbol] = (side, plan)
            return exits, plan

        tp2_hit = plan.tp2_qty > 0.0 and (
            (side is Side.SHORT and current_price <= plan.take_profit2)
            or (side is Side.LONG and current_price >= plan.take_profit2)
        )
        if tp2_hit:
            exits.append(S.ExitSignal(symbol=symbol, reason="TP2"))
            exit_side = Side.LONG if side is Side.SHORT else Side.SHORT
            order = OrderSpec(
                symbol=plan.symbol,
                side=exit_side,
                type=OrderType.MARKET,
                quantity=plan.tp2_qty,
            )
            self._trader.place(order)
            self._risk_manager.release(replace(plan, quantity=plan.tp2_qty))
            remaining_qty = plan.quantity - plan.tp2_qty
            if remaining_qty <= 0.0:
                del self._positions[symbol]
                return exits, None
            plan = PositionPlan(
                symbol=plan.symbol,
                entry_price=plan.entry_price,
                stop_loss=plan.stop_loss,
                take_profit1=plan.take_profit1,
                take_profit2=0.0,
                trail_start=plan.trail_start,
                trail_distance=plan.trail_distance,
                quantity=remaining_qty,
                tp1_qty=plan.tp1_qty,
                tp2_qty=0.0,
                tail_qty=plan.tail_qty,
            )
            self._positions[symbol] = (side, plan)
            return exits, plan

        # ------------------------------------------------------------------
        # Trailing stop management after take profits
        trailing_active = plan.take_profit1 <= 0.0 and plan.take_profit2 <= 0.0
        trail_cond = (
            (side is Side.SHORT and current_price <= plan.trail_start)
            or (side is Side.LONG and current_price >= plan.trail_start)
        )
        if trailing_active and trail_cond:
            new_stop = (
                min(plan.stop_loss, current_price + plan.trail_distance)
                if side is Side.SHORT
                else max(plan.stop_loss, current_price - plan.trail_distance)
            )
            plan = PositionPlan(
                symbol=plan.symbol,
                entry_price=plan.entry_price,
                stop_loss=new_stop,
                take_profit1=plan.take_profit1,
                take_profit2=plan.take_profit2,
                trail_start=current_price,
                trail_distance=plan.trail_distance,
                quantity=plan.quantity,
                tp1_qty=plan.tp1_qty,
                tp2_qty=plan.tp2_qty,
                tail_qty=plan.tail_qty,
            )
            self._positions[symbol] = (side, plan)

        # ------------------------------------------------------------------
        # Stop loss or trailing stop hit
        stop_hit = (
            (side is Side.SHORT and current_price >= plan.stop_loss)
            or (side is Side.LONG and current_price <= plan.stop_loss)
        )
        if stop_hit:
            reason = "TRAIL" if trailing_active else "STOP"
            exits.append(S.ExitSignal(symbol=symbol, reason=reason))
            self._close_position(symbol, side, plan)
            return exits, None

        return exits, plan
