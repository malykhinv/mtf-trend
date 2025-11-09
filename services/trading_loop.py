from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Sequence

import ccxt

from config.config import AppConfig
from data_providers import TradingStats
from data_providers.ccxt_client import OHLCV
from domain.models import Candle, Pump, ScenarioStatus
from infrastructure import Notifier, get_logger
from integrations import SwingsAdapter
from services.market_scan import (
    MarketScanState,
    MarketScanner,
    SymbolMarketSnapshot,
    build_market_scanner,
)
from services.order_watcher import OrderWatcher
from services.signal_executor import SignalExecutor
from state import SymbolState
from strategies import HtfPipeline, LtfPipeline


@dataclass
class TradingLoop:
    """Coordinated trading loop combining scanning, analysis and execution."""

    scanner: MarketScanner
    htf_pipeline: HtfPipeline
    ltf_pipeline: LtfPipeline
    signal_executor: SignalExecutor
    order_watcher: OrderWatcher
    scan_interval_hours: int
    notifier: Notifier | None = None
    now_factory: Callable[[], datetime] = field(
        default=lambda: datetime.now(tz=timezone.utc)
    )
    logger: logging.Logger = field(default_factory=lambda: get_logger(__name__))
    states: Dict[str, SymbolState] = field(default_factory=dict)
    pumps: Dict[str, Pump] = field(default_factory=dict)

    def run_forever(self) -> None:
        """Continuously perform market scans and react to resulting signals."""

        interval_seconds = max(self.scan_interval_hours, 1) * 3600
        next_scan_at = self.now_factory()
        while True:
            now = self.now_factory()
            if now >= next_scan_at:
                self.logger.info("Запуск торгового цикла")
                start = now
                try:
                    self.run_once()
                except Exception as exc:  # pragma: no cover - safeguard for runtime
                    self.logger.exception("Ошибка торгового цикла: %s", exc)
                next_scan_at = start + timedelta(seconds=interval_seconds)
            self.order_watcher.poll()
            sleep_for = self._compute_sleep_duration(now, next_scan_at)
            if sleep_for > 0:
                time.sleep(sleep_for)

    def run_once(self) -> None:
        """Execute a single trading cycle iteration."""

        focus_symbols = self._determine_focus_symbols()
        market_state = self.scanner.scan_once(focus_symbols=focus_symbols)
        self._cleanup_stale_symbols(market_state)
        for snapshot in market_state.symbols.values():
            try:
                self._process_snapshot(snapshot)
            except Exception as exc:  # pragma: no cover - safeguard for runtime
                self.logger.exception(
                    "Ошибка обработки символа %s: %s", snapshot.symbol, exc
                )
        self.order_watcher.poll()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _process_snapshot(self, snapshot: SymbolMarketSnapshot) -> None:
        state = self.states.get(snapshot.symbol)
        if state is None:
            state = SymbolState(symbol=snapshot.symbol)
            self.states[snapshot.symbol] = state

        if not snapshot.htf_candles:
            self.logger.debug("%s: нет свечей HTF для анализа", snapshot.symbol)
            return

        candles = tuple(_convert_ohlcv_to_candle(candle) for candle in snapshot.htf_candles)
        existing_pump = self.pumps.get(snapshot.symbol)
        htf_result = self.htf_pipeline.run(candles, existing_pump, symbol=snapshot.symbol)

        cancel_reason = htf_result.pullback.cancel_reason
        if (
            cancel_reason is not None
            and not state.has_open_position
            and (state.active_orders or state.status in {ScenarioStatus.MONITORING, ScenarioStatus.ACTIVE})
        ):
            state = self.signal_executor.event_handler.handle_scenario_cancelled(
                state,
                snapshot.symbol,
                reason=cancel_reason,
            )
            state.update_pump(h_main=None, l_pullback=None)
            state.update_levels(level_top=None, level_low=None)
            self.pumps.pop(snapshot.symbol, None)
            self.states[snapshot.symbol] = state
            return

        pump = htf_result.pump
        if pump is None:
            self.pumps.pop(snapshot.symbol, None)
            state.update_pump(h_main=None, l_pullback=None)
            if not state.has_open_position and state.status != ScenarioStatus.COOLDOWN:
                state.mark_idle()
            state.update_levels(level_top=None, level_low=None)
            return

        self.pumps[snapshot.symbol] = pump

        l_pullback = htf_result.pullback.l_pullback
        state.update_pump(h_main=pump.h_main, l_pullback=l_pullback)

        if not htf_result.ready_for_l_analysis:
            if not state.has_open_position and state.status != ScenarioStatus.COOLDOWN:
                state.mark_idle()
            return

        if l_pullback is None:
            self.logger.debug("%s: нет pullback для запуска L-анализа", snapshot.symbol)
            return

        atr_value = _select_atr_value(htf_result.atr_values, htf_result.analysis_start_index)
        if atr_value is None:
            self.logger.debug("%s: не удалось определить ATR для L-анализа", snapshot.symbol)
            return

        if not state.has_open_position:
            state.mark_monitoring()

        ltf_candles = snapshot.ltf_candles
        if not ltf_candles:
            self.logger.debug("%s: LTF недоступен, ожидание следующего цикла", snapshot.symbol)
            return

        ltf_window = _filter_ltf_since(ltf_candles, pump.candle.timestamp)
        if not ltf_window:
            ltf_window = tuple(ltf_candles)

        last_price = _determine_last_price(ltf_window, snapshot.stats)

        ltf_result = self.ltf_pipeline.run(
            state,
            ltf_window,
            atr_h=atr_value,
            h_main=pump.h_main,
            l_pullback=l_pullback,
            last_price=last_price,
        )

        state = ltf_result.state
        if ltf_result.signal is not None:
            state = self.signal_executor.execute_breakout_signal(
                snapshot.symbol,
                state,
                ltf_result.signal,
            )

        self.states[snapshot.symbol] = state

    def _determine_focus_symbols(self) -> set[str]:
        focus: set[str] = set()
        for symbol, state in self.states.items():
            if state.has_open_position:
                focus.add(symbol)
                continue
            if state.status in {ScenarioStatus.MONITORING, ScenarioStatus.ACTIVE}:
                focus.add(symbol)
        return focus

    def _compute_sleep_duration(self, now: datetime, next_scan_at: datetime) -> float:
        candidates: list[float] = []
        if next_scan_at > now:
            candidates.append((next_scan_at - now).total_seconds())
        watcher_sleep = self.order_watcher.time_until_next_poll(now)
        if watcher_sleep is not None:
            candidates.append(watcher_sleep)
        if not candidates:
            return 0.0
        positive = [value for value in candidates if value > 0.0]
        if positive:
            return min(positive)
        return 0.0

    def _cleanup_stale_symbols(self, market_state: MarketScanState) -> None:
        observed = set(market_state.symbols)
        for symbol in list(self.states.keys()):
            if symbol not in observed:
                self.states.pop(symbol, None)
                self.pumps.pop(symbol, None)


def build_trading_loop(
    config: AppConfig,
    *,
    exchange: ccxt.Exchange,
    swings_adapter: SwingsAdapter,
    notifier: Notifier | None = None,
    logger: logging.Logger | None = None,
    now_factory: Callable[[], datetime] | None = None,
) -> TradingLoop:
    """Factory assembling a :class:`TradingLoop` with configured dependencies."""

    loop_logger = logger or get_logger(__name__)
    scanner = build_market_scanner(config, exchange=exchange)
    htf_pipeline = HtfPipeline.from_config(config, notifier=notifier, logger=loop_logger)
    ltf_pipeline = LtfPipeline.from_config(
        config,
        swings_adapter=swings_adapter,
        notifier=notifier,
        logger=loop_logger,
    )
    signal_executor = SignalExecutor.from_config(
        exchange,
        config,
        notifier=notifier,
        logger=loop_logger,
    )
    states: Dict[str, SymbolState] = {}
    pumps: Dict[str, Pump] = {}
    order_watcher = OrderWatcher(
        exchange=exchange,
        event_handler=signal_executor.event_handler,
        states=states,
        order_poll_interval=config.execution_polling.orders_interval_s,
        trade_poll_interval=config.execution_polling.trades_interval_s,
        logger=loop_logger,
    )
    return TradingLoop(
        scanner=scanner,
        htf_pipeline=htf_pipeline,
        ltf_pipeline=ltf_pipeline,
        signal_executor=signal_executor,
        order_watcher=order_watcher,
        scan_interval_hours=config.scan.market_scan_interval_h,
        notifier=notifier,
        now_factory=now_factory or (lambda: datetime.now(tz=timezone.utc)),
        logger=loop_logger,
        states=states,
        pumps=pumps,
    )


def _convert_ohlcv_to_candle(ohlcv: OHLCV) -> Candle:
    timestamp = _normalise_timestamp(ohlcv["timestamp"])
    return Candle(
        open=ohlcv["open"],
        high=ohlcv["high"],
        low=ohlcv["low"],
        close=ohlcv["close"],
        volume=ohlcv["volume"],
        timestamp=timestamp,
    )


def _filter_ltf_since(candles: Sequence[OHLCV], start_time: datetime) -> tuple[OHLCV, ...]:
    start_ts = start_time.astimezone(timezone.utc)
    filtered = [
        candle
        for candle in candles
        if _normalise_timestamp(candle["timestamp"]) >= start_ts
    ]
    return tuple(filtered)


def _determine_last_price(candles: Sequence[OHLCV], stats: TradingStats) -> float:
    if candles:
        last = candles[-1]
        return float(last["high"])
    return float(stats.get("last_price", 0.0))


def _select_atr_value(atr_values: Sequence[float], start_index: int | None) -> float | None:
    if not atr_values:
        return None
    if start_index is None:
        return atr_values[-1]
    pump_index = start_index - 1
    if 0 <= pump_index < len(atr_values):
        return atr_values[pump_index]
    return atr_values[-1]


def _normalise_timestamp(value: int | float) -> datetime:
    timestamp = float(value)
    if timestamp > 1_000_000_000_000:
        timestamp /= 1_000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


__all__ = ["TradingLoop", "build_trading_loop"]

