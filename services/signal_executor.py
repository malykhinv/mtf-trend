from __future__ import annotations

import logging
from dataclasses import dataclass, field

import ccxt

from config.config import AppConfig, MinTakeProfitConfig, RiskRewardConfig
from domain.models import OrderParams, ScenarioStatus, SymbolState
from execution import ExecutionEventHandler, OrderManager
from strategies.ltf_pipeline import BreakoutSignal


@dataclass
class SignalExecutor:
    """Bridge between LTF signals and the order management layer."""

    order_manager: OrderManager
    event_handler: ExecutionEventHandler
    risk_reward_config: RiskRewardConfig
    min_tp_config: MinTakeProfitConfig
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    @classmethod
    def from_config(
        cls,
        exchange: ccxt.Exchange,
        config: AppConfig,
        *,
        quote_currency: str = "USDT",
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
        )
        return cls(
            order_manager=order_manager,
            event_handler=event_handler,
            risk_reward_config=config.risk_reward,
            min_tp_config=config.min_take_profit,
        )

    def execute_breakout_signal(self, symbol: str, state: SymbolState, signal: BreakoutSignal) -> SymbolState:
        if state.has_open_position:
            self.logger.debug("Пропуск %s: уже есть открытая позиция", symbol)
            return state
        if state.active_orders:
            self.logger.debug("Пропуск %s: уже есть активные ордера", symbol)
            return state
        if self._is_in_cooldown(state):
            self.logger.info("Пропуск %s: действует кулдаун", symbol)
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
        state.open_quantity = 0.0
        state.has_open_position = False
        state.status = ScenarioStatus.ACTIVE
        self.logger.info(
            "Размещён сетап по %s: entry=%.4f, stop=%.4f, tp1=%.4f, tp2=%.4f",
            symbol,
            order_params.entry_price,
            order_params.stop_loss,
            order_params.take_profit_1,
            order_params.take_profit_2,
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
