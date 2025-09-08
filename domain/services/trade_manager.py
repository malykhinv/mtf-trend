from __future__ import annotations

import time
from dataclasses import replace
from typing import List

import constants
from domain.models import signals as S
from domain.models.enums import OrderType, Side
from domain.models.trading import OrderSpec, PositionPlan
from domain.models.state import Position

from .risk_manager import RiskManager
from domain.services.symbol_registry import SymbolRegistry
from domain.ports.trader import Trader
from .notification import NotificationService


class TradeManager:
    """High level wrapper around :class:`Trader` handling position state."""

    def __init__(
        self,
        trader: Trader,
        risk_manager: RiskManager,
        registry: SymbolRegistry,
        notifier: NotificationService | None = None,
    ) -> None:
        self._trader = trader
        self._risk_manager = risk_manager
        self._registry = registry
        self._notifier = notifier

    @staticmethod
    def _plan(plan: PositionPlan, **changes: float) -> PositionPlan:
        return replace(plan, **changes)

    async def open_position(self, plan: PositionPlan, side: Side) -> None:
        order = OrderSpec(
            symbol=plan.symbol,
            side=side,
            type=OrderType.MARKET,
            quantity=plan.quantity,
        )
        actual_price: float | None = None
        try:
            result = await self._trader.place(order)
        except Exception:
            self._risk_manager.release(plan)
            raise
        if result is not None:
            if isinstance(result, dict):
                actual_price = result.get("price")
            elif hasattr(result, "price"):
                actual_price = result.price
        if actual_price is None and hasattr(self._trader, "get_price"):
            try:  # type: ignore[attr-defined]
                actual_price = await self._trader.get_price(plan.symbol)
            except Exception:
                actual_price = None
        state = self._registry.get(plan.symbol)
        stored_plan = plan if actual_price is None else self._plan(plan, entry_price=actual_price)
        state.position = Position(side=side, plan=stored_plan)
        state.last_signal_ts = time.time()
        self._registry.update(plan.symbol, state)
        if self._notifier:
            await self._notifier.notify_order_open(plan, side, actual_price)

    async def _close_position(self, symbol: str, side: Side, plan: PositionPlan) -> None:
        exit_side = Side.LONG if side is Side.SHORT else Side.SHORT
        order = OrderSpec(
            symbol=plan.symbol,
            side=exit_side,
            type=OrderType.MARKET,
            quantity=plan.quantity,
        )
        await self._trader.place(order)
        self._risk_manager.release(plan)
        state = self._registry.get(symbol)
        state.position = None
        self._registry.update(symbol, state)

    async def on_tick_manage(
        self, symbol: str, price: float | None = None
    ) -> tuple[list[S.ExitSignal], PositionPlan | None]:
        """Manage an existing position on each price tick."""

        state = self._registry.get(symbol)
        record = state.position
        exits: List[S.ExitSignal] = []
        if record is None:
            return exits, None

        side, plan = record.side, record.plan
        metrics = state.metrics
        current_price = plan.entry_price if price is None else price

        exits, plan = await self._handle_tp1(plan, side, current_price)
        if exits:
            return exits, plan

        exits, plan = await self._handle_tp2(plan, side, current_price)
        if exits:
            return exits, plan

        _, plan = await self._apply_trailing_stop(plan, side, current_price)

        taker_buy = metrics.taker_buy_volume
        taker_sell = metrics.taker_sell_volume
        taker_ratio = (
            taker_buy / taker_sell
            if taker_sell > 0.0
            else float("inf") if taker_buy > 0.0 else 0.0
        )
        if metrics.premium_pct > 0.0 and (
            metrics.delta_oi_pct > 0.0 or taker_ratio > 1.0
        ):
            exits.append(S.ExitSignal(symbol=plan.symbol, reason="LONGS_RETURNED"))
            await self._close_position(plan.symbol, side, plan)
            return exits, None

        exits, plan = await self._check_stop(plan, side, current_price)
        if exits:
            return exits, plan

        timed_out = (
            state.last_signal_ts is not None
            and time.time() - state.last_signal_ts > constants.TRADE_INVALIDATION_SEC
        )
        price_invalid = side is Side.SHORT and (
            current_price > plan.entry_price or current_price > plan.window_high
        )
        if timed_out or price_invalid:
            exits.append(S.ExitSignal(symbol=plan.symbol, reason="INVALIDATED"))
            await self._close_position(plan.symbol, side, plan)
            return exits, None

        return exits, plan

    # ------------------------------------------------------------------
    async def _handle_tp1(
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
        await self._trader.place(order)
        self._risk_manager.release(replace(plan, quantity=plan.tp1_qty))
        new_plan = self._plan(
            plan,
            take_profit1=0.0,
            quantity=plan.quantity - plan.tp1_qty,
            tp1_qty=0.0,
        )
        state = self._registry.get(plan.symbol)
        state.position = Position(side=side, plan=new_plan)
        self._registry.update(plan.symbol, state)
        if self._notifier:
            await self._notifier.notify_tp_hit(new_plan, side, price, 1, new_plan.quantity)
        return exits, new_plan

    async def _handle_tp2(
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
        await self._trader.place(order)
        self._risk_manager.release(replace(plan, quantity=plan.tp2_qty))
        remaining_qty = plan.quantity - plan.tp2_qty
        if remaining_qty <= 0.0:
            state = self._registry.get(plan.symbol)
            state.position = None
            self._registry.update(plan.symbol, state)
            if self._notifier:
                await self._notifier.notify_tp_hit(plan, side, price, 2, 0.0)
            return exits, None
        new_plan = self._plan(
            plan,
            take_profit2=0.0,
            quantity=remaining_qty,
            tp2_qty=0.0,
        )
        state = self._registry.get(plan.symbol)
        state.position = Position(side=side, plan=new_plan)
        self._registry.update(plan.symbol, state)
        if self._notifier:
            await self._notifier.notify_tp_hit(new_plan, side, price, 2, new_plan.quantity)
        return exits, new_plan

    async def _apply_trailing_stop(
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
            if new_stop != plan.stop_loss:
                plan = self._plan(
                    plan,
                    stop_loss=new_stop,
                    trail_start=price,
                )
                state = self._registry.get(plan.symbol)
                state.position = Position(side=side, plan=plan)
                self._registry.update(plan.symbol, state)
                if self._notifier:
                    await self._notifier.notify_trail_update(plan, side, price, plan.quantity)
        return exits, plan

    async def _check_stop(
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
            await self._close_position(plan.symbol, side, plan)
            if self._notifier:
                await self._notifier.notify_stop(plan, side, price, 0.0)
            return exits, None
        return exits, plan
