from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from typing import Deque, Dict, Iterable, Optional

from ...data.providers.base import BaseExchangeProvider
from ...data.repositories.signal_repository import SignalRepository
from ...data.repositories.state_repository import StateRepository
from ...data.repositories.trade_repository import TradeRepository
from ...data.models import DepositSnapshot
from ...utils.logging import get_logger
from ...utils.clock import utcnow
from ..enums import Timeframe, TradeStatus
from ..models.entities import Candle, Signal, Thresholds, Trade
from ..models.metadata import DepositSnapshotMetadata, LiveMetadata, TradeMetadata
from .dedup_policy import DeduplicationPolicy
from .selector_service import SignalSelectorService
from .tp_sl_service import TpSlService


class LiveTradingRunner:
    def __init__(
        self,
        provider: BaseExchangeProvider,
        selector: SignalSelectorService,
        tp_sl_service: TpSlService,
        signal_repository: SignalRepository,
        state_repository: StateRepository,
        trade_repository: TradeRepository,
        dedup_policy: DeduplicationPolicy,
        window: int,
    ) -> None:
        self._provider = provider
        self._selector = selector
        self._tp_sl_service = tp_sl_service
        self._signals = signal_repository
        self._state = state_repository
        self._trades = trade_repository
        self._dedup = dedup_policy
        self._window = window
        self._logger = get_logger(self.__class__.__name__)
        initial_asset = state_repository.get_deposit_asset()
        initial_balance = state_repository.get_deposit()
        self._deposit = DepositSnapshot(asset=initial_asset, balance=initial_balance)
        self._last_deposit_update = state_repository.get_last_deposit_update()
        self._trade_watchers: Dict[str, asyncio.Task[None]] = {}
        self._timeframes: tuple[Timeframe, ...] = (
            Timeframe.M1,
            Timeframe.M3,
            Timeframe.M5,
            Timeframe.M15,
        )

    async def _handle_signal(self, signal: Signal) -> None:
        accepted, key = self._dedup.should_accept(signal)
        if not accepted:
            self._logger.debug("Duplicate live signal %s skipped", key)
            return
        snapshot = await self.refresh_deposit()
        trade_size = self._calculate_trade_size()
        timestamp = utcnow()
        source_signal_id = (
            signal.metadata.source_signal_id if signal.metadata else None
        ) or signal.id
        snapshot_metadata = DepositSnapshotMetadata(
            asset=snapshot.asset,
            balance=snapshot.balance,
            updated_at=self._last_deposit_update,
        )
        trade = Trade(
            id=signal.id,
            signal_id=signal.id,
            source_signal_id=source_signal_id,
            exchange=signal.candle.exchange,
            symbol=signal.candle.symbol,
            timeframe=signal.timeframe or signal.candle.timeframe,
            side=signal.side,
            status=TradeStatus.OPENED,
            entry_price=signal.candle.close,
            size=trade_size,
            used_margin=trade_size,
            created_at=timestamp,
            updated_at=timestamp,
            allow_long=signal.allow_long,
            allow_short=signal.allow_short,
            thresholds_snapshot=signal.thresholds,
            metadata=TradeMetadata(
                mode="live", deposit_snapshot=snapshot_metadata, live=LiveMetadata()
            ),
        )
        self._tp_sl_service.assign(signal, trade)
        self._signals.save(signal)
        self._trades.save(trade)
        self._update_used_amount()
        self._logger.info("Signal accepted %s", signal.id)
        self._start_trade_watch(signal, trade)

    def _start_trade_watch(self, signal: Signal, trade: Trade) -> None:
        existing = self._trade_watchers.get(trade.id)
        if existing and not existing.done():
            existing.cancel()
        task = asyncio.create_task(self._watch_trade(signal, trade))
        self._trade_watchers[trade.id] = task

        def _cleanup(task: asyncio.Task[None]) -> None:
            self._trade_watchers.pop(trade.id, None)
            if task.cancelled():
                return
            exception = task.exception()
            if exception is not None:
                self._logger.error(
                    "Trade watcher for %s failed: %s", trade.id, exception, exc_info=True
                )

        task.add_done_callback(_cleanup)

    async def _watch_trade(self, signal: Signal, trade: Trade) -> None:
        timeframe = trade.timeframe or signal.candle.timeframe
        last_closed_at = signal.candle.closed_at
        while trade.status == TradeStatus.OPENED:
            try:
                candles = await self._provider.fetch_ohlcv(trade.symbol, timeframe, 3)
                if not candles:
                    await asyncio.sleep(self._get_poll_delay(timeframe))
                    continue
                for candle in candles:
                    if last_closed_at and candle.closed_at <= last_closed_at:
                        continue
                    last_closed_at = candle.closed_at
                    hit_tp, hit_sl = self._tp_sl_service.check_levels(trade, candle)
                    status = self._tp_sl_service.resolve_status(
                        signal, trade, candle, hit_tp, hit_sl
                    )
                    if status is None:
                        continue
                    trade.status = status
                    trade.closed_at = candle.closed_at
                    if status == TradeStatus.CLOSED_TP:
                        exit_price = trade.tp_price
                        result_pct = trade.tp_pct
                    else:
                        exit_price = trade.sl_price
                        result_pct = trade.sl_pct
                    trade.exit_price = exit_price
                    trade.pnl_pct = result_pct
                    trade.pnl = (
                        trade.size * (result_pct / 100)
                        if result_pct is not None and trade.size is not None
                        else None
                    )
                    trade.updated_at = utcnow()
                    if trade.metadata is None:
                        trade.metadata = TradeMetadata(mode="live", live=LiveMetadata())
                    if trade.metadata.live is None:
                        trade.metadata.live = LiveMetadata()
                    live_meta = trade.metadata.live
                    if result_pct is not None:
                        live_meta.result_pct = result_pct
                    live_meta.closed_status = status
                    self._trades.save(trade)
                    self._update_used_amount()
                    return
                await asyncio.sleep(self._get_poll_delay(timeframe))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.error(
                    "Error watching trade %s: %s", trade.id, exc, exc_info=True
                )
                await asyncio.sleep(self._get_poll_delay(timeframe))

    async def _process_symbol(self, symbol: str, thresholds: Thresholds) -> None:
        if self._selector is None:
            self._logger.warning("Selector is not configured; skipping symbol %s", symbol)
            return
        windows: Dict[Timeframe, Deque[Candle]] = {
            timeframe: deque(maxlen=self._window) for timeframe in self._timeframes
        }
        last_closed: Dict[Timeframe, Optional[datetime]] = {
            timeframe: None for timeframe in self._timeframes
        }
        next_poll: Dict[Timeframe, datetime] = {
            timeframe: utcnow() for timeframe in self._timeframes
        }
        last_error: Optional[str] = None
        repeat_count = 0
        while True:
            try:
                processed = False
                now = utcnow()
                for timeframe in self._timeframes:
                    if now < next_poll[timeframe]:
                        continue
                    processed = True
                    delay = self._get_poll_delay(timeframe)
                    try:
                        candles = await self._provider.fetch_ohlcv(
                            symbol, timeframe, self._window
                        )
                    finally:
                        next_poll[timeframe] = utcnow() + timedelta(seconds=delay)
                    if not candles:
                        continue
                    window = windows[timeframe]
                    latest_closed = last_closed[timeframe]
                    new_candles = [
                        candle
                        for candle in candles
                        if candle.closed_at
                        and (latest_closed is None or candle.closed_at > latest_closed)
                    ]
                    if not new_candles:
                        continue
                    window.extend(candles[-self._window :])
                    last_closed[timeframe] = max(
                        (candle.closed_at for candle in new_candles if candle.closed_at),
                        default=latest_closed,
                    )
                    if len(window) < self._window:
                        continue
                    result = self._selector.select(symbol, list(window), thresholds)
                    for signal in result.signals:
                        await self._handle_signal(signal)
                last_error = None
                repeat_count = 0
                now = utcnow()
                wait_until = min(next_poll.values())
                wait_seconds = max(0.0, (wait_until - now).total_seconds())
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                elif not processed:
                    await asyncio.sleep(0.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_signature = f"{type(exc).__name__}: {exc}"
                if error_signature == last_error:
                    repeat_count += 1
                    self._logger.error(
                        "Error processing %s persists (occurrence #%d): %s",
                        symbol,
                        repeat_count + 1,
                        error_signature,
                        exc_info=True,
                    )
                else:
                    last_error = error_signature
                    repeat_count = 0
                    self._logger.error(
                        "Error processing %s: %s",
                        symbol,
                        error_signature,
                        exc_info=True,
                    )
                min_delay = min(self._get_poll_delay(tf) for tf in self._timeframes)
                await asyncio.sleep(min_delay)

    async def run(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
        thresholds_map: Dict[str, Thresholds],
    ) -> None:
        await self.refresh_deposit(force=True)
        tasks = []
        for symbol in symbols:
            thresholds = thresholds_map.get(symbol) or thresholds_map.get("default")
            if not thresholds:
                self._logger.warning("No thresholds configured for %s", symbol)
                continue
            tasks.append(asyncio.create_task(self._process_symbol(symbol, thresholds)))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                self._logger.error(
                    "Symbol task finished with error: %s",
                    result,
                    exc_info=(type(result), result, result.__traceback__),
                )

    def _get_poll_delay(self, timeframe: Timeframe) -> float:
        seconds = self._timeframe_to_seconds(timeframe)
        return max(seconds / 4.0, 0.25)

    @staticmethod
    def _timeframe_to_seconds(timeframe: Timeframe) -> float:
        value = timeframe.value
        if value.endswith("m"):
            try:
                minutes = int(value[:-1])
            except ValueError:
                return 60.0
            return float(minutes * 60)
        return 60.0

    async def refresh_deposit(self, force: bool = False) -> DepositSnapshot:
        if not force and self._last_deposit_update:
            delta = (utcnow() - self._last_deposit_update).total_seconds()
            if delta < 3600:
                return self._deposit
        snapshot = await self._provider.update_deposit()
        asset = snapshot.asset or self._deposit.asset
        amount = snapshot.balance
        normalized = DepositSnapshot(asset=asset, balance=amount, raw=snapshot.raw)
        timestamp = utcnow()
        self._deposit = normalized
        self._last_deposit_update = timestamp
        self._state.update_deposit(amount, asset, timestamp)
        return self._deposit

    def _calculate_trade_size(self) -> float:
        balance = self._deposit.balance
        return max(10.0, 0.05 * balance) if balance > 0 else 10.0

    def _update_used_amount(self) -> None:
        open_amount = sum(
            trade.used_margin for trade in self._trades.all() if trade.status == TradeStatus.OPENED
        )
        self._state.set_used_amount(open_amount)
