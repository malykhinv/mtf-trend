from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Deque, Dict, Iterable, Optional

from ...data.providers.base import BaseExchangeProvider
from ...data.repositories.signal_repository import SignalRepository
from ...data.repositories.state_repository import StateRepository
from ...data.repositories.trade_repository import TradeRepository
from ...utils.logging import get_logger
from ...utils.clock import utcnow
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
        state_repository: StateRepository,
        trade_repository: TradeRepository,
        dedup_policy: DeduplicationPolicy,
        window: int,
        poll_interval: float = 5.0,
    ) -> None:
        self._provider = provider
        self._selector = selector
        self._tp_sl_service = tp_sl_service
        self._signals = signal_repository
        self._state = state_repository
        self._trades = trade_repository
        self._dedup = dedup_policy
        self._window = window
        self._poll_interval = poll_interval
        self._logger = get_logger(self.__class__.__name__)
        self._deposit_usdt = state_repository.get_deposit()
        self._deposit_asset = state_repository.get_deposit_asset()
        self._last_deposit_update = state_repository.get_last_deposit_update()

    async def _handle_signal(self, signal: Signal) -> None:
        accepted, key = self._dedup.should_accept(signal)
        if not accepted:
            self._logger.debug("Duplicate live signal %s skipped", key)
            return
        snapshot = await self.refresh_deposit()
        trade_size = self._calculate_trade_size()
        timestamp = utcnow()
        source_signal_id = (
            signal.metadata.get("source_signal_id")
            if isinstance(signal.metadata, dict) and signal.metadata.get("source_signal_id")
            else signal.id
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
            metadata={"mode": "live", "deposit_snapshot": snapshot},
        )
        self._tp_sl_service.assign(signal, trade)
        self._signals.save(signal)
        self._trades.save(trade)
        self._update_used_amount()
        self._logger.info("Signal accepted %s", signal.id)

    async def _process_symbol(self, symbol: str, timeframe: Timeframe, thresholds: Thresholds) -> None:
        window: Deque[Candle] = deque(maxlen=self._window)
        last_error: Optional[str] = None
        repeat_count = 0
        while True:
            try:
                tf_value = thresholds.metadata.get("timeframe")
                tf = Timeframe(tf_value) if tf_value else timeframe
                candles = await self._provider.fetch_ohlcv(symbol, tf, self._window)
                if candles:
                    window.extend(candles[-self._window :])
                if len(window) >= self._window:
                    result = self._selector.select(symbol, list(window), thresholds)
                    for signal in result.signals:
                        await self._handle_signal(signal)
                last_error = None
                repeat_count = 0
                await asyncio.sleep(self._poll_interval)
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
                await asyncio.sleep(self._poll_interval)

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
            tasks.append(asyncio.create_task(self._process_symbol(symbol, timeframe, thresholds)))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                self._logger.error(
                    "Symbol task finished with error: %s",
                    result,
                    exc_info=(type(result), result, result.__traceback__),
                )

    async def refresh_deposit(self, force: bool = False) -> Dict[str, Any]:
        if not force and self._last_deposit_update:
            delta = (utcnow() - self._last_deposit_update).total_seconds()
            if delta < 3600:
                return self._deposit_snapshot()
        balance = await self._provider.update_deposit()
        asset, amount = self._extract_deposit(balance)
        timestamp = utcnow()
        self._deposit_usdt = amount
        self._deposit_asset = asset
        self._last_deposit_update = timestamp
        self._state.update_deposit(amount, asset, timestamp)
        return self._deposit_snapshot()

    def _extract_deposit(self, balance: Any) -> tuple[str, float]:
        if isinstance(balance, dict):
            asset = str(balance.get("asset") or self._deposit_asset or "USDT")
            for key in ("balance", "availableBalance", "available", "amount", "equity"):
                value = balance.get(key)
                if value is not None:
                    try:
                        return asset, float(value)
                    except (TypeError, ValueError):
                        continue
            try:
                return asset, float(balance.get(asset, 0.0))
            except (TypeError, ValueError):
                pass
        return self._deposit_asset or "USDT", self._deposit_usdt

    def _calculate_trade_size(self) -> float:
        return max(10.0, 0.0005 * self._deposit_usdt) if self._deposit_usdt > 0 else 10.0

    def _deposit_snapshot(self) -> Dict[str, Any]:
        return {
            "asset": self._deposit_asset,
            "balance": self._deposit_usdt,
            "updated_at": self._last_deposit_update.isoformat() if self._last_deposit_update else None,
        }

    def _update_used_amount(self) -> None:
        open_amount = sum(
            trade.used_margin for trade in self._trades.all() if trade.status == TradeStatus.OPENED
        )
        self._state.set_used_amount(open_amount)
