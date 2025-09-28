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
from bot.utils.logger import get_logger
from analyzer import SignalAnalyzer
from execution_service import ExecutionService
from models.bar import Bar, BreakDirection
from models.close_reason import CloseReason
from models.signal import Signal
from models.trade import Trade
from models.trade_status import TradeStatus
from swing_detector import SwingDetector


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
class SimulatedTrade:
    """Representation of a simulated trade during backfill."""

    trade_id: str
    signal: Signal

    @property
    def levels(self):  # pragma: no cover - simple delegation
        return self.signal.levels

    @property
    def timestamp_open(self) -> datetime:  # pragma: no cover - simple delegation
        return self.signal.timestamp

    @property
    def side(self):  # pragma: no cover - simple delegation
        return self.signal.direction


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
        self._logger.info("Запускаем оркестратор: подписываемся на поток баров")
        self._deps.live_stream.subscribe(self._on_bar)

    def stop(self) -> None:
        self._logger.info("Останавливаем оркестратор и поток данных")
        self._deps.live_stream.close()

    def backfill(self, request) -> None:  # type: ignore[no-untyped-def]
        self._logger.info(
            "Старт бэктеста: %s %s %s с %s по %s",
            request.exchange.value,
            request.symbol,
            request.timeframe.value,
            request.start,
            request.end,
        )
        simulated_trades: dict[str, SimulatedTrade] = {}
        last_bar: Bar | None = None
        bars_processed = 0
        signals_found = 0
        trades_closed = 0
        anomalies_found = 0
        for bar in self._deps.market_loader.load(request):
            last_bar = bar
            bars_processed += 1
            closed = self._update_simulated_trades(simulated_trades, bar)
            if closed:
                self._deps.diary.append_trades(closed)
                trades_closed += len(closed)

            signal, anomaly = self._deps.analyzer.analyze_bar(bar)

            if anomaly is not None:
                self._deps.diary.append_anomalies([anomaly])
                anomalies_found += 1

            if signal is None:
                continue

            self._deps.diary.append_signals([signal])
            signals_found += 1
            simulated_trade = SimulatedTrade(
                trade_id=f"backtest-{signal.signal_id}",
                signal=signal,
            )
            simulated_trades[simulated_trade.trade_id] = simulated_trade

        if simulated_trades:
            closing_timestamp = (
                last_bar.close_time if last_bar is not None else datetime.now(tz=config.TIMEZONE)
            )
            expired = [
                self._expire_simulated_trade(state, closing_timestamp)
                for state in simulated_trades.values()
            ]
            self._deps.diary.append_trades(expired)
            trades_closed += len(expired)

        self._logger.info(
            "Бэктест завершён: баров %s, сигналов %s, закрытых сделок %s, аномалий %s",
            bars_processed,
            signals_found,
            trades_closed,
            anomalies_found,
        )

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

        self._logger.info(
            "Получен сигнал %s по %s %s",
            signal.signal_id,
            signal.exchange.value,
            signal.symbol,
        )
        try:
            self._deps.notifier.send_signal(signal)
        except Exception:
            self._logger.exception("Ошибка отправки уведомления по сигналу %s", signal.signal_id)

        self._deps.diary.append_signals([signal])

        deposit = self._deps.balance_provider.current_deposit()
        order_size = self._deps.execution.calc_order_size_usdt(deposit_usdt=deposit)
        quantity = order_size / signal.levels.entry_price if signal.levels.entry_price else 0.0
        self._logger.info(
            "Депозит %.2f USDT, размер заявки %.2f USDT, количество %.4f",
            deposit,
            order_size,
            quantity,
        )
        trade_id = f"trade-{signal.signal_id}"
        self._logger.info(
            "Открываем сделку %s: %s %s %s",
            trade_id,
            signal.exchange.value,
            signal.symbol,
            "лонг" if signal.direction.is_long else "шорт",
        )
        trade = self._deps.execution.open_trade(signal, quantity=quantity)
        self._logger.info(
            "Сделка %s отправлена, статус %s, исполнено %.4f",
            trade.trade_id,
            trade.status.value,
            trade.executed_qty,
        )
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
        self._logger.info(
            "Кулдаун для %s включён на %s секунд",
            self._cooldown_key(bar),
            config.SYMBOL_COOLDOWN_SEC,
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
        self._logger.info("Сделка %s взята на сопровождение", trade.trade_id)

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
                self._logger.info(
                    "Сделка %s исполнена, объём %.4f", trade.trade_id, trade.executed_qty
                )
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

    def _update_simulated_trades(
        self,
        registry: dict[str, SimulatedTrade],
        bar: Bar,
    ) -> list[Trade]:
        closed_trades: list[Trade] = []
        for trade_id, trade_state in list(registry.items()):
            outcome = self._resolve_simulated_outcome(trade_state, bar)
            if outcome is None:
                continue
            closed_trades.append(self._close_simulated_trade(trade_state, bar, outcome))
            registry.pop(trade_id, None)
        return closed_trades

    @staticmethod
    def _resolve_simulated_outcome(
            trade_state: SimulatedTrade,
        bar: Bar,
    ) -> CloseReason | None:
        levels = trade_state.levels
        if trade_state.side.is_long:
            tp_hit = bar.high >= levels.take_profit_price
            sl_hit = bar.low <= levels.stop_loss_price
        else:
            tp_hit = bar.low <= levels.take_profit_price
            sl_hit = bar.high >= levels.stop_loss_price

        if not tp_hit and not sl_hit:
            return None
        if tp_hit and not sl_hit:
            return CloseReason.TAKE_PROFIT
        if sl_hit and not tp_hit:
            return CloseReason.STOP_LOSS

        direction = bar.metrics.break_direction
        if trade_state.side.is_long:
            if direction is BreakDirection.HIGH_FIRST:
                return CloseReason.TAKE_PROFIT
            return CloseReason.STOP_LOSS

        if direction is BreakDirection.LOW_FIRST:
            return CloseReason.TAKE_PROFIT
        return CloseReason.STOP_LOSS

    def _close_simulated_trade(
        self,
        trade_state: SimulatedTrade,
        bar: Bar,
        reason: CloseReason,
    ) -> Trade:
        levels = trade_state.levels
        if reason is CloseReason.TAKE_PROFIT:
            exit_price = levels.take_profit_price
            status = TradeStatus.CLOSED_TP
        else:
            exit_price = levels.stop_loss_price
            status = TradeStatus.CLOSED_SL

        result_pct = self._calc_simulated_result_pct(
            entry_price=levels.entry_price,
            exit_price=exit_price,
            is_long=trade_state.side.is_long,
        )
        self._logger.info(
            "Симуляция сделки %s закрыта по %s: результат %.2f%%",
            trade_state.trade_id,
            "тейк-профиту" if reason is CloseReason.TAKE_PROFIT else "стоп-лоссу",
            result_pct,
        )

        return Trade(
            trade_id=trade_state.trade_id,
            source_signal_id=trade_state.signal.signal_id,
            exchange=trade_state.signal.exchange,
            symbol=trade_state.signal.symbol,
            timeframe=trade_state.signal.timeframe,
            side=trade_state.side,
            timestamp_open=trade_state.timestamp_open,
            timestamp_close=bar.close_time,
            entry_price=levels.entry_price,
            take_profit_price=levels.take_profit_price,
            stop_loss_price=levels.stop_loss_price,
            requested_qty=1.0,
            executed_qty=1.0,
            status=status,
            avg_fill_price=exit_price,
            reason_close=reason,
        )

    @staticmethod
    def _calc_simulated_result_pct(
        *, entry_price: float, exit_price: float, is_long: bool
    ) -> float:
        if entry_price == 0.0:
            return 0.0
        if is_long:
            return (exit_price - entry_price) / entry_price * 100.0
        return (entry_price - exit_price) / entry_price * 100.0

    def _expire_simulated_trade(
        self,
        trade_state: SimulatedTrade,
        timestamp_close: datetime,
    ) -> Trade:
        levels = trade_state.levels
        self._logger.info(
            "Симуляция сделки %s завершена без достижения уровней — помечена как отменённая",
            trade_state.trade_id,
        )
        return Trade(
            trade_id=trade_state.trade_id,
            source_signal_id=trade_state.signal.signal_id,
            exchange=trade_state.signal.exchange,
            symbol=trade_state.signal.symbol,
            timeframe=trade_state.signal.timeframe,
            side=trade_state.side,
            timestamp_open=trade_state.timestamp_open,
            timestamp_close=timestamp_close,
            entry_price=levels.entry_price,
            take_profit_price=levels.take_profit_price,
            stop_loss_price=levels.stop_loss_price,
            requested_qty=1.0,
            executed_qty=0.0,
            status=TradeStatus.CANCELLED,
        )

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
