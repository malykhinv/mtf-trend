from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence

from ..enums import BreakDirection, Side, TradeStatus
from ..models.entities import Candle, Signal, Thresholds, Trade
from ...data.providers.base import BaseExchangeProvider
from ...data.repositories.signal_repository import SignalRepository
from ...data.repositories.state_repository import StateRepository
from ...data.repositories.trade_repository import TradeRepository
from ...utils.logging import get_logger
from ...utils.clock import utcnow
from .dedup_policy import DeduplicationPolicy
from .selector_service import SignalSelectorService
from .tp_sl_service import TpSlService


@dataclass(slots=True)
class BacktestResult:
    trades: Sequence[Trade]
    signals: Sequence[Signal]


class BacktestRunner:
    def __init__(
        self,
        selector: SignalSelectorService,
        tp_sl_service: TpSlService,
        signal_repository: SignalRepository,
        state_repository: StateRepository,
        trade_repository: TradeRepository,
        dedup_policy: DeduplicationPolicy,
        window: int = 50,
    ) -> None:
        self._selector = selector
        self._tp_sl_service = tp_sl_service
        self._signals = signal_repository
        self._state = state_repository
        self._trades = trade_repository
        self._dedup = dedup_policy
        self._window = window
        self._logger = get_logger(self.__class__.__name__)
        self._deposit_usdt = state_repository.get_deposit()
        self._deposit_asset = state_repository.get_deposit_asset()
        self._last_deposit_update = state_repository.get_last_deposit_update()

    async def run(
        self,
        symbol: str,
        candles: Sequence[Candle],
        thresholds: Thresholds,
        provider: BaseExchangeProvider,
    ) -> BacktestResult:
        generated_signals: list[Signal] = []
        generated_trades: list[Trade] = []
        await self.refresh_deposit(provider, force=True)
        for index in range(self._window, len(candles)):
            window_candles = candles[index - self._window : index]
            future_candles = candles[index :]
            result = self._selector.select(
                symbol,
                window_candles,
                thresholds,
                future_candles=future_candles,
            )
            for signal in result.signals:
                accepted, key = self._dedup.should_accept(signal)
                if not accepted:
                    self._logger.debug("Skipping duplicate signal %s", key)
                    continue
                await self.refresh_deposit(provider)
                snapshot = self._deposit_snapshot()
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
                    metadata={"mode": "backtest", "deposit_snapshot": snapshot},
                )
                self._tp_sl_service.assign(signal, trade)
                trade.opened_at = signal.candle.closed_at
                self._apply_backtest_outcome(signal, trade, candles[index:])
                self._signals.save(signal)
                self._trades.save(trade)
                self._update_used_amount()
                generated_signals.append(signal)
                generated_trades.append(trade)
        return BacktestResult(trades=generated_trades, signals=generated_signals)

    async def refresh_deposit(
        self, provider: BaseExchangeProvider, force: bool = False
    ) -> None:
        if not force and self._last_deposit_update:
            delta = (utcnow() - self._last_deposit_update).total_seconds()
            if delta < 3600:
                return
        balance = await provider.update_deposit()
        asset, amount = self._extract_deposit(balance)
        timestamp = utcnow()
        self._deposit_usdt = amount
        self._deposit_asset = asset
        self._last_deposit_update = timestamp
        self._state.update_deposit(amount, asset, timestamp)

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

    def _apply_backtest_outcome(
        self, signal: Signal, trade: Trade, future_candles: Sequence[Candle]
    ) -> None:
        for candle in future_candles:
            hit_tp, hit_sl = self._check_thresholds(trade, candle)
            status = self._resolve_trade_status(signal, trade, candle, hit_tp, hit_sl)
            if status is None:
                continue
            trade.status = status
            trade.closed_at = candle.closed_at
            exit_price = trade.tp_price if status == TradeStatus.CLOSED_TP else trade.sl_price
            trade.exit_price = exit_price
            trade.pnl = self._calculate_pnl(trade, exit_price)
            trade.pnl_pct = (
                (trade.pnl / trade.used_margin) * 100.0
                if trade.pnl is not None and trade.used_margin
                else None
            )
            trade.updated_at = utcnow()
            return

    def _check_thresholds(self, trade: Trade, candle: Candle) -> tuple[bool, bool]:
        tp_price = trade.tp_price
        sl_price = trade.sl_price
        if tp_price is None or sl_price is None:
            return False, False
        if trade.side == Side.LONG:
            hit_tp = candle.high >= tp_price
            hit_sl = candle.low <= sl_price
        else:
            hit_tp = candle.low <= tp_price
            hit_sl = candle.high >= sl_price
        return hit_tp, hit_sl

    def _resolve_trade_status(
        self,
        signal: Signal,
        trade: Trade,
        candle: Candle,
        hit_tp: bool,
        hit_sl: bool,
    ) -> TradeStatus | None:
        if not hit_tp and not hit_sl:
            return None
        if hit_tp and hit_sl:
            direction = signal.direction
            if direction == BreakDirection.HIGH_FIRST:
                hit_sl = False
            elif direction == BreakDirection.LOW_FIRST:
                hit_tp = False
            else:
                tp_price = trade.tp_price
                sl_price = trade.sl_price
                if trade.side == Side.LONG:
                    if tp_price is not None and candle.open >= tp_price:
                        hit_sl = False
                    elif sl_price is not None and candle.open <= sl_price:
                        hit_tp = False
                else:
                    if tp_price is not None and candle.open <= tp_price:
                        hit_sl = False
                    elif sl_price is not None and candle.open >= sl_price:
                        hit_tp = False
        if hit_tp:
            return TradeStatus.CLOSED_TP
        if hit_sl:
            return TradeStatus.CLOSED_SL
        return None

    def _calculate_pnl(self, trade: Trade, exit_price: float | None) -> float | None:
        if exit_price is None:
            return None
        if trade.side == Side.LONG:
            return exit_price - trade.entry_price
        return trade.entry_price - exit_price
