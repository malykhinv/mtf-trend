from __future__ import annotations

from dataclasses import replace
from typing import Dict, List

from domain.models import signals as S
from domain.models.enums import OrderType, Side
from domain.models.trading import OrderSpec, PositionPlan

from .risk_manager import RiskManager
from domain.ports.trader import Trader


class TradeManager:
    """High level wrapper around :class:`Trader` handling position state."""

    def __init__(self, trader: Trader, risk_manager: RiskManager) -> None:
        self._trader = trader
        self._risk_manager = risk_manager
        self._positions: Dict[str, tuple[Side, PositionPlan]] = {}

    @staticmethod
    def _plan(plan: PositionPlan, **changes: float) -> PositionPlan:
        return replace(plan, **changes)

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

        exits, plan = self._handle_tp1(plan, side, current_price)
        if exits:
            return exits, plan

        exits, plan = self._handle_tp2(plan, side, current_price)
        if exits:
            return exits, plan

        _, plan = self._apply_trailing_stop(plan, side, current_price)

        exits, plan = self._check_stop(plan, side, current_price)
        if exits:
            return exits, plan

        return exits, plan

    # ------------------------------------------------------------------
    def _handle_tp1(
        self, plan: PositionPlan, side: Side, price: float
    ) -> tuple[list[S.ExitSignal], PositionPlan | None]:
        exits: List[S.ExitSignal] = []
        tp1_hit = plan.tp1_qty > 0.0 and (
            (side is Side.SHORT and price <= plan.take_profit1)
            or (side is Side.LONG and price >= plan.take_profit1)
        )
        if not tp1_hit:
            return exits, plan

        exits.append(S.ExitSignal(symbol=plan.symbol, reason="TP1"))
        exit_side = Side.LONG if side is Side.SHORT else Side.SHORT
        order = OrderSpec(
            symbol=plan.symbol,
            side=exit_side,
            type=OrderType.MARKET,
            quantity=plan.tp1_qty,
        )
        self._trader.place(order)
        self._risk_manager.release(replace(plan, quantity=plan.tp1_qty))
        new_plan = self._plan(
            plan,
            take_profit1=0.0,
            quantity=plan.quantity - plan.tp1_qty,
            tp1_qty=0.0,
        )
        self._positions[plan.symbol] = (side, new_plan)
        return exits, new_plan

    def _handle_tp2(
        self, plan: PositionPlan, side: Side, price: float
    ) -> tuple[list[S.ExitSignal], PositionPlan | None]:
        exits: List[S.ExitSignal] = []
        tp2_hit = plan.tp2_qty > 0.0 and (
            (side is Side.SHORT and price <= plan.take_profit2)
            or (side is Side.LONG and price >= plan.take_profit2)
        )
        if not tp2_hit:
            return exits, plan

        exits.append(S.ExitSignal(symbol=plan.symbol, reason="TP2"))
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
            del self._positions[plan.symbol]
            return exits, None
        new_plan = self._plan(
            plan,
            take_profit2=0.0,
            quantity=remaining_qty,
            tp2_qty=0.0,
        )
        self._positions[plan.symbol] = (side, new_plan)
        return exits, new_plan

    def _apply_trailing_stop(
        self, plan: PositionPlan, side: Side, price: float
    ) -> tuple[list[S.ExitSignal], PositionPlan]:
        exits: List[S.ExitSignal] = []
        trailing_active = plan.take_profit1 <= 0.0 and plan.take_profit2 <= 0.0
        trail_cond = (
            (side is Side.SHORT and price <= plan.trail_start)
            or (side is Side.LONG and price >= plan.trail_start)
        )
        if trailing_active and trail_cond:
            new_stop = (
                min(plan.stop_loss, price + plan.trail_distance)
                if side is Side.SHORT
                else max(plan.stop_loss, price - plan.trail_distance)
            )
            plan = self._plan(
                plan,
                stop_loss=new_stop,
                trail_start=price,
            )
            self._positions[plan.symbol] = (side, plan)
        return exits, plan

    def _check_stop(
        self, plan: PositionPlan, side: Side, price: float
    ) -> tuple[list[S.ExitSignal], PositionPlan | None]:
        exits: List[S.ExitSignal] = []
        trailing_active = plan.take_profit1 <= 0.0 and plan.take_profit2 <= 0.0
        stop_hit = (
            (side is Side.SHORT and price >= plan.stop_loss)
            or (side is Side.LONG and price <= plan.stop_loss)
        )
        if stop_hit:
            reason = "TRAIL" if trailing_active else "STOP"
            exits.append(S.ExitSignal(symbol=plan.symbol, reason=reason))
            self._close_position(plan.symbol, side, plan)
            return exits, None
        return exits, plan
