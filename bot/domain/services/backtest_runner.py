from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..enums import Side, Timeframe, TradeStatus
from ..models.entities import Candle, Signal, Thresholds, Trade
from ..models.metadata import (
    DepositSnapshotMetadata,
    SignalMetadata,
    TradeMetadata,
)
from ...data.providers.base import BaseExchangeProvider
from ...data.repositories.signal_repository import SignalRepository
from ...data.repositories.state_repository import StateRepository
from ...data.repositories.trade_repository import TradeRepository
from ...data.models import DepositSnapshot
from ...utils.logging import get_logger
from ...utils.clock import utcnow
from .dedup_policy import DeduplicationPolicy
from .selector_service import SignalSelectorService
from .tp_sl_service import TpSlService


@dataclass(slots=True)
class BacktestResult:
    timeframe: Timeframe | None
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
        initial_asset = state_repository.get_deposit_asset()
        initial_balance = state_repository.get_deposit()
        self._deposit = DepositSnapshot(asset=initial_asset, balance=initial_balance)
        self._last_deposit_update = state_repository.get_last_deposit_update()

    async def run(
        self,
        symbol: str,
        candles: Sequence[Candle],
        thresholds: Thresholds,
        provider: BaseExchangeProvider,
        timeframe: Timeframe | None = None,
    ) -> BacktestResult:
        generated_signals: list[Signal] = []
        generated_trades: list[Trade] = []
        await self.refresh_deposit(provider, force=True)
        effective_timeframe: Timeframe | None = timeframe
        if effective_timeframe is None and candles:
            effective_timeframe = candles[-1].timeframe
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
                resolved_timeframe = (
                    effective_timeframe or signal.timeframe or signal.candle.timeframe
                )
                if resolved_timeframe:
                    signal.timeframe = resolved_timeframe
                    signal.candle.timeframe = resolved_timeframe
                    if signal.metadata is None:
                        signal.metadata = SignalMetadata(
                            symbol=signal.candle.symbol,
                            timeframe=resolved_timeframe.value,
                        )
                    else:
                        signal.metadata.timeframe = resolved_timeframe.value
                accepted, key = self._dedup.should_accept(signal)
                if not accepted:
                    self._logger.debug("Skipping duplicate signal %s", key)
                    continue
                await self.refresh_deposit(provider)
                snapshot = self._deposit_snapshot()
                trade_size = self._calculate_trade_size()
                timestamp = utcnow()
                source_signal_id = (
                    signal.metadata.source_signal_id if signal.metadata else None
                ) or signal.id
                trade = Trade(
                    id=signal.id,
                    signal_id=signal.id,
                    source_signal_id=source_signal_id,
                    exchange=signal.candle.exchange,
                    symbol=signal.candle.symbol,
                    timeframe=resolved_timeframe,
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
                    metadata=TradeMetadata(mode="backtest", deposit_snapshot=snapshot),
                )
                self._tp_sl_service.assign(signal, trade)
                trade.opened_at = signal.candle.closed_at
                self._apply_backtest_outcome(signal, trade, candles[index:])
                self._signals.save(signal)
                self._trades.save(trade)
                self._update_used_amount()
                generated_signals.append(signal)
                generated_trades.append(trade)
        return BacktestResult(
            timeframe=effective_timeframe,
            trades=generated_trades,
            signals=generated_signals,
        )

    async def refresh_deposit(
        self, provider: BaseExchangeProvider, force: bool = False
    ) -> DepositSnapshot:
        if not force and self._last_deposit_update:
            delta = (utcnow() - self._last_deposit_update).total_seconds()
            if delta < 3600:
                return self._deposit
        snapshot = await provider.update_deposit()
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

    def _deposit_snapshot(self) -> DepositSnapshotMetadata:
        updated_at = self._last_deposit_update
        return DepositSnapshotMetadata(
            asset=self._deposit.asset,
            balance=self._deposit.balance,
            updated_at=updated_at,
        )

    def _update_used_amount(self) -> None:
        open_amount = sum(
            trade.used_margin for trade in self._trades.all() if trade.status == TradeStatus.OPENED
        )
        self._state.set_used_amount(open_amount)

    def _apply_backtest_outcome(
        self, signal: Signal, trade: Trade, future_candles: Sequence[Candle]
    ) -> None:
        snapshot = signal.metrics_snapshot
        if snapshot is None:
            self._logger.debug("Signal %s missing metrics snapshot", signal.id)
            return

        pct_to_high_break = snapshot.pct_to_high_break
        pct_to_low_break = snapshot.pct_to_low_break

        for candle in future_candles:
            hit_tp, hit_sl = self._tp_sl_service.check_levels(trade, candle)
            status = self._tp_sl_service.resolve_status(signal, trade, candle, hit_tp, hit_sl)
            if status is None:
                continue
            trade.status = status
            trade.closed_at = candle.closed_at
            result_pct: float
            if trade.side == Side.LONG:
                if status == TradeStatus.CLOSED_TP:
                    result_pct = pct_to_high_break
                else:
                    result_pct = -pct_to_low_break
                exit_price = trade.entry_price * (1 + result_pct / 100)
            else:
                if status == TradeStatus.CLOSED_TP:
                    result_pct = pct_to_low_break
                else:
                    result_pct = -pct_to_high_break
                exit_price = trade.entry_price * (1 - result_pct / 100)
            trade.exit_price = exit_price
            trade.pnl = trade.size * (result_pct / 100)
            trade.pnl_pct = result_pct
            if trade.metadata is None:
                trade.metadata = TradeMetadata(mode="backtest")
            backtest_meta = trade.metadata.ensure_backtest()
            backtest_meta.result_pct = result_pct
            backtest_meta.pct_to_high_break = pct_to_high_break
            backtest_meta.pct_to_low_break = pct_to_low_break
            trade.updated_at = utcnow()
            return

