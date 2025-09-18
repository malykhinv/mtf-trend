from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque, Dict, Iterable

from ...data.providers.base import BaseExchangeProvider
from ...data.repositories.signal_repository import SignalRepository
from ...data.repositories.trade_repository import TradeRepository
from ...utils.logging import get_logger
from ..enums import Timeframe, TradeStatus
from ..models.entities import Candle, Signal, Thresholds, Trade
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
        trade_repository: TradeRepository,
        dedup_policy: DeduplicationPolicy,
        window: int,
        poll_interval: float = 5.0,
    ) -> None:
        self._provider = provider
        self._selector = selector
        self._tp_sl_service = tp_sl_service
        self._signals = signal_repository
        self._trades = trade_repository
        self._dedup = dedup_policy
        self._window = window
        self._poll_interval = poll_interval
        self._logger = get_logger(self.__class__.__name__)

    async def _handle_signal(self, signal: Signal) -> None:
        accepted, key = self._dedup.should_accept(signal)
        if not accepted:
            self._logger.debug("Duplicate live signal %s skipped", key)
            return
        trade = Trade(
            id=signal.id,
            signal_id=signal.id,
            exchange=signal.candle.exchange,
            symbol=signal.candle.symbol,
            side=signal.side,
            status=TradeStatus.OPENED,
            entry_price=signal.candle.close,
            size=1.0,
            allow_long=signal.allow_long,
            allow_short=signal.allow_short,
            thresholds_snapshot=signal.thresholds,
            metadata={"mode": "live"},
        )
        self._tp_sl_service.assign(signal, trade)
        self._signals.save(signal)
        self._trades.save(trade)
        self._logger.info("Signal accepted %s", signal.id)

    async def _process_symbol(self, symbol: str, timeframe: Timeframe, thresholds: Thresholds) -> None:
        window: Deque[Candle] = deque(maxlen=self._window)
        while True:
            tf_value = thresholds.metadata.get("timeframe")
            tf = Timeframe(tf_value) if tf_value else timeframe
            candles = await self._provider.fetch_ohlcv(symbol, tf, self._window)
            if candles:
                window.extend(candles[-self._window :])
            if len(window) >= self._window:
                result = self._selector.select(symbol, list(window), thresholds)
                for signal in result.signals:
                    await self._handle_signal(signal)
            await asyncio.sleep(self._poll_interval)

    async def run(
        self,
        symbols: Iterable[str],
        timeframe: Timeframe,
        thresholds_map: Dict[str, Thresholds],
    ) -> None:
        tasks = []
        for symbol in symbols:
            thresholds = thresholds_map.get(symbol) or thresholds_map.get("default")
            if not thresholds:
                self._logger.warning("No thresholds configured for %s", symbol)
                continue
            tasks.append(asyncio.create_task(self._process_symbol(symbol, timeframe, thresholds)))
        await asyncio.gather(*tasks)
