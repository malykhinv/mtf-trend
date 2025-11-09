from __future__ import annotations

import logging
from dataclasses import dataclass, field

import ccxt

from config.config import AppConfig, MinTakeProfitConfig, RiskRewardConfig
from domain.models import OrderParams
from execution import ExecutionEventHandler, OrderManager
from infrastructure import (
    Notifier,
    OrderPlacementContext,
    get_logger,
    log_cooldown_active,
    log_orders_placed,
)
from state import SymbolState
from strategies.ltf_pipeline import BreakoutSignal


@dataclass
class SignalExecutor:
    """Bridge between LTF signals and the order management layer."""

    order_manager: OrderManager
    event_handler: ExecutionEventHandler
    risk_reward_config: RiskRewardConfig
    min_tp_config: MinTakeProfitConfig
    timeframe_pair: str = ""
    notifier: Notifier | None = None
    logger: logging.Logger = field(default_factory=lambda: get_logger(__name__))

    @classmethod
    def from_config(
        cls,
        exchange: ccxt.Exchange,
        config: AppConfig,
        *,
        quote_currency: str = "USDT",
        notifier: Notifier | None = None,
        logger: logging.Logger | None = None,
    ) -> SignalExecutor:
        order_manager = OrderManager(
            exchange=exchange,
            order_config=config.order,
            positioning_config=config.positioning,
            quote_currency=quote_currency,
        )
        event_handler = ExecutionEventHandler(
            order_manager=order_manager,
            cooldown_minutes=config.cooldown.minutes,
            notifier=notifier,
        )
        return cls(
            order_manager=order_manager,
            event_handler=event_handler,
            risk_reward_config=config.risk_reward,
            min_tp_config=config.min_take_profit,
            timeframe_pair=f"{config.timeframes.htf}/{config.timeframes.ltf}",
            notifier=notifier,
            logger=logger or get_logger(__name__),
        )

    def execute_breakout_signal(self, symbol: str, state: SymbolState, signal: BreakoutSignal) -> SymbolState:
        if state.has_open_position:
            self.logger.debug("Пропуск %s: уже есть открытая позиция", symbol)
            return state
        if state.active_orders:
            self.logger.debug("Пропуск %s: уже есть активные ордера", symbol)
            return state
        if self._is_in_cooldown(state):
            if state.cooldown_until is not None:
                log_cooldown_active(
                    logger=self.logger,
                    notifier=self.notifier,
                    symbol=symbol,
                    until=state.cooldown_until,
                )
            return state
        if not self._meets_thresholds(signal):
            self.logger.debug("Пропуск %s: сигнал не удовлетворяет RR/MIN_TP", symbol)
            return state

        raw_params = OrderParams(
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            position_size=0.0,
        )

        try:
            order_params = self.order_manager.create_order_params(symbol, raw_params)
            active_orders = self.order_manager.place_bracket_orders(symbol, order_params)
        except (ccxt.BaseError, ValueError) as exc:
            return self.event_handler.handle_order_error(state, symbol, exc)

        state.active_orders = dict(active_orders)
        state.last_order_params = order_params
        state.reset_position()
        state.mark_active()
        log_orders_placed(
            logger=self.logger,
            notifier=self.notifier,
            context=OrderPlacementContext(
                symbol=symbol,
                timeframe_pair=self.timeframe_pair,
                entry_price=order_params.entry_price,
                stop_loss=order_params.stop_loss,
                take_profit_1=order_params.take_profit_1,
                take_profit_2=order_params.take_profit_2,
                position_size=order_params.position_size,
                rr=signal.rr,
            ),
        )
        return state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _meets_thresholds(self, signal: BreakoutSignal) -> bool:
        if signal.rr < self.risk_reward_config.rr_min:
            return False
        min_tp_value = (signal.take_profit_1 - signal.entry_price) / signal.entry_price
        return min_tp_value >= self.min_tp_config.min_tp_pct

    def _is_in_cooldown(self, state: SymbolState) -> bool:
        if state.cooldown_until is None:
            return False
        return state.cooldown_until > self.event_handler.now_factory()


__all__ = ["SignalExecutor"]
