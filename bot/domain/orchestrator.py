"""Orchestrator ties together data, analysis and execution layers."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Deque

from bot import config
from bot.data.accounting import BalanceProvider
from bot.data.diary import WorkbookDiary
from bot.data.loader import LiveBarEvent, LiveDataStream, MarketDataLoader
from bot.data.notifier import Notifier
from bot.utils.logging import get_logger
from .analyzer import SignalAnalyzer
from .execution_service import ExecutionService
from .models.bar import Bar
from .models.close_reason import CloseReason
from .models.trade import Trade
from .models.trade_status import TradeStatus
from .swing_detector import SwingDetector


@dataclass(slots=True)
class ActiveTrade:
    """Track an opened trade together with history required for trailing."""

    trade: Trade
    highs: Deque[float]
    lows: Deque[float]
    awaiting_close_confirmation: bool = False

    def record_bar(self, bar: Bar) -> None:
        self.highs.append(bar.high)
        self.lows.append(bar.low)


@dataclass(slots=True)
class OrchestratorDependencies:
    market_loader: MarketDataLoader
    live_stream: LiveDataStream
    diary: WorkbookDiary
    notifier: Notifier
    analyzer: SignalAnalyzer
    execution: ExecutionService
    balance_provider: BalanceProvider


class Orchestrator:
    """Coordinates incoming data and delegates actions to other services."""

    def __init__(self, dependencies: OrchestratorDependencies) -> None:
        self._deps = dependencies
        self._symbol_cooldown: dict[str, datetime] = {}
        self._latest_imbalance: dict[str, float] = {}
        self._symbols_above_threshold: set[str] = set()
        self._active_trades: dict[str, ActiveTrade] = {}
        self._swing_detector = SwingDetector()
        self._trail_history = config.TRAIL_SWING_WINDOW + config.TRAIL_SWING_CONFIRM + 5
        self._logger = get_logger(__name__)

    def start(self) -> None:
        self._deps.live_stream.subscribe(self._on_bar)

    def stop(self) -> None:
        self._deps.live_stream.close()

    def backfill(self, request) -> None:  # type: ignore[no-untyped-def]
        for bar in self._deps.market_loader.load(request):
            self._handle_bar(bar, ignore_imbalance_checks=True)

    def _on_bar(self, event: LiveBarEvent) -> None:
        bar = event.bar
        symbol_key = self._cooldown_key(bar)
        self._latest_imbalance[symbol_key] = event.imbalance
        if event.imbalance >= config.AGGR_IMBALANCE_THRESHOLD:
            self._symbols_above_threshold.add(symbol_key)
        else:
            self._symbols_above_threshold.discard(symbol_key)
        self._deps.execution.record_imbalance(symbol_key, event.imbalance)
        if self._is_on_cooldown(bar):
            return
        self._handle_bar(bar)

    def _handle_bar(self, bar: Bar, *, ignore_imbalance_checks: bool = False) -> None:
        symbol_key = self._cooldown_key(bar)
        trade_key = self._trade_key(bar.exchange.value, bar.symbol, bar.timeframe.value)

        self._manage_active_trade(trade_key, bar)

        if trade_key in self._active_trades:
            return

        if not ignore_imbalance_checks:
            last_imbalance = self._latest_imbalance.get(symbol_key)
            if last_imbalance is None:
                last_imbalance = self._deps.execution.last_recorded_imbalance(symbol_key)

            if last_imbalance is None:
                self._symbols_above_threshold.discard(symbol_key)
            elif last_imbalance >= config.AGGR_IMBALANCE_THRESHOLD:
                self._symbols_above_threshold.add(symbol_key)
            else:
                self._symbols_above_threshold.discard(symbol_key)

            if (
                symbol_key not in self._symbols_above_threshold
                or last_imbalance is None
                or last_imbalance < config.AGGR_IMBALANCE_THRESHOLD
            ):
                return

        signal, anomaly = self._deps.analyzer.analyze_bar(bar)

        if anomaly is not None:
            self._deps.diary.append_anomalies([anomaly])

        if signal is None:
            return

        try:
            self._deps.notifier.send_signal(signal)
        except Exception:
            self._logger.exception("Ошибка отправки уведомления по сигналу %s", signal.signal_id)

        self._deps.diary.append_signals([signal])

        deposit = self._deps.balance_provider.current_deposit()
        order_size = self._deps.execution.calc_order_size_usdt(deposit_usdt=deposit)
        quantity = order_size / signal.levels.entry_price if signal.levels.entry_price else 0.0
        trade = self._deps.execution.open_trade(signal, quantity=quantity)
        self._deps.diary.append_trades([trade])
        if trade.status is not TradeStatus.CANCELLED:
            self._register_active_trade(trade, bar)

        try:
            self._deps.notifier.send_trade(trade)
        except Exception:
            self._logger.exception("Ошибка отправки уведомления по сделке %s", trade.trade_id)

        if anomaly is not None:
            self._activate_cooldown(bar)

    def _is_on_cooldown(self, bar: Bar) -> bool:
        key = self._cooldown_key(bar)
        until = self._symbol_cooldown.get(key)
        if until is None:
            return False
        now = datetime.now(tz=config.TIMEZONE)
        if now >= until:
            self._symbol_cooldown.pop(key, None)
            return False
        return True

    def _activate_cooldown(self, bar: Bar) -> None:
        self._symbol_cooldown[self._cooldown_key(bar)] = datetime.now(tz=config.TIMEZONE) + timedelta(
            seconds=config.SYMBOL_COOLDOWN_SEC
        )

    @staticmethod
    def _cooldown_key(bar: Bar) -> str:
        return f"{bar.exchange.value}:{bar.symbol}"

    @staticmethod
    def _trade_key(exchange: str, symbol: str, timeframe: str) -> str:
        return f"{exchange}:{symbol}:{timeframe}"

    def _register_active_trade(self, trade: Trade, bar: Bar) -> None:
        key = self._trade_key(trade.exchange.value, trade.symbol, trade.timeframe.value)
        history = ActiveTrade(
            trade=trade,
            highs=deque(maxlen=self._trail_history),
            lows=deque(maxlen=self._trail_history),
        )
        history.record_bar(bar)
        self._active_trades[key] = history

    def _manage_active_trade(self, trade_key: str, bar: Bar) -> None:
        state = self._active_trades.get(trade_key)
        if state is None:
            return

        state.record_bar(bar)
        trade = state.trade

        if trade.status is TradeStatus.CANCELLED:
            self._active_trades.pop(trade_key, None)
            return

        poll_result = self._deps.execution.poll_trade_state(
            trade,
            awaiting_close=state.awaiting_close_confirmation,
        )
        if poll_result is not None:
            state.trade = poll_result.trade
            trade = state.trade
            if poll_result.outcome == "filled":
                self._deps.diary.append_trades([trade])
            elif poll_result.outcome in {"closed", "cancelled"}:
                symbol_key = self._cooldown_key(bar)
                last_imbalance = self._latest_imbalance.get(symbol_key)
                if last_imbalance is None:
                    last_imbalance = self._deps.execution.last_recorded_imbalance(symbol_key)
                self._finalize_trade(trade_key, trade, poll_result.outcome, last_imbalance)
                return

        if state.awaiting_close_confirmation:
            return

        symbol_key = self._cooldown_key(bar)
        last_imbalance = self._latest_imbalance.get(symbol_key)
        if last_imbalance is None:
            last_imbalance = self._deps.execution.last_recorded_imbalance(symbol_key)

        aggression_detected = False
        if last_imbalance is not None:
            if trade.side.is_long and last_imbalance <= -config.AGGR_IMBALANCE_THRESHOLD:
                aggression_detected = True
            elif trade.side.is_short and last_imbalance >= config.AGGR_IMBALANCE_THRESHOLD:
                aggression_detected = True

        if aggression_detected:
            close_price = bar.close
            closed_trade = self._deps.execution.close_trade(
                trade,
                reason=CloseReason.AGGRESSION,
                price=close_price,
                timestamp=bar.close_time,
            )
            state.trade = closed_trade
            state.awaiting_close_confirmation = True
            self._logger.info(
                "Инициировано закрытие сделки %s из-за агрессии против позиции (дисбаланс %.2f)",
                trade.trade_id,
                last_imbalance,
            )
            return

        if trade.executed_qty <= 0:
            return

        updated_trade = trade
        trade_updated = False

        trigger_price = bar.high if trade.side.is_long else bar.low
        if trade.sl_be_at is None and self._deps.execution.should_move_to_breakeven(
            trade.entry_price,
            trigger_price,
            trade.side,
        ):
            new_stop = self._deps.execution.breakeven_stop(trade.entry_price, trade.side)
            if (trade.side.is_long and new_stop > trade.stop_loss_price) or (
                trade.side.is_short and new_stop < trade.stop_loss_price
            ):
                updated_trade = replace(
                    updated_trade,
                    stop_loss_price=new_stop,
                    sl_be_at=bar.close_time,
                )
                trade_updated = True
                self._logger.info(
                    "Сделка %s переведена в безубыток, новый стоп %.4f",
                    trade.trade_id,
                    new_stop,
                )

        swing_stop = self._calculate_trailing_stop(state)
        if swing_stop is not None:
            if trade.side.is_long and swing_stop > updated_trade.stop_loss_price:
                updated_trade = replace(updated_trade, stop_loss_price=swing_stop)
                trade_updated = True
                self._logger.info(
                    "Сделка %s: трейлинг-стоп обновлён до %.4f",
                    trade.trade_id,
                    swing_stop,
                )
            elif trade.side.is_short and swing_stop < updated_trade.stop_loss_price:
                updated_trade = replace(updated_trade, stop_loss_price=swing_stop)
                trade_updated = True
                self._logger.info(
                    "Сделка %s: трейлинг-стоп обновлён до %.4f",
                    trade.trade_id,
                    swing_stop,
                )

        if trade_updated and updated_trade != trade:
            state.trade = updated_trade
            self._deps.diary.append_trades([updated_trade])

    def _calculate_trailing_stop(self, state: ActiveTrade) -> float | None:
        trade = state.trade
        if trade.side.is_long:
            return self._swing_detector.detect_swing_low(tuple(state.lows))
        return self._swing_detector.detect_swing_high(tuple(state.highs))

    def _finalize_trade(
        self,
        trade_key: str,
        trade: Trade,
        outcome: str,
        last_imbalance: float | None,
    ) -> None:
        self._active_trades.pop(trade_key, None)
        self._deps.diary.append_trades([trade])

        if outcome == "closed":
            self._log_trade_closure(trade, last_imbalance)
        else:
            self._logger.info("Сделка %s отменена биржей", trade.trade_id)

        try:
            self._deps.notifier.send_trade(trade)
        except Exception:
            self._logger.exception("Ошибка отправки уведомления о сделке %s", trade.trade_id)

    def _log_trade_closure(self, trade: Trade, last_imbalance: float | None) -> None:
        reason = trade.reason_close
        if reason is CloseReason.TAKE_PROFIT:
            price = trade.avg_fill_price or trade.take_profit_price
            self._logger.info(
                "Сделка %s закрыта по тейк-профиту на уровне %.4f",
                trade.trade_id,
                price,
            )
        elif reason is CloseReason.STOP_LOSS:
            price = trade.avg_fill_price or trade.stop_loss_price
            self._logger.info(
                "Сделка %s закрыта по стоп-лоссу на уровне %.4f",
                trade.trade_id,
                price,
            )
        elif reason is CloseReason.AGGRESSION:
            if last_imbalance is not None:
                self._logger.info(
                    "Сделка %s закрыта из-за агрессии против позиции (дисбаланс %.2f)",
                    trade.trade_id,
                    last_imbalance,
                )
            else:
                self._logger.info(
                    "Сделка %s закрыта из-за агрессии против позиции",
                    trade.trade_id,
                )
        else:
            self._logger.info("Сделка %s закрыта вручную", trade.trade_id)
