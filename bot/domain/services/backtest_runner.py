from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..enums import BreakDirection, Side, TradeStatus
from ..models.entities import Candle, Signal, Thresholds, Trade
from ...data.repositories.signal_repository import SignalRepository
from ...data.repositories.trade_repository import TradeRepository
from ...utils.logging import get_logger
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
        trade_repository: TradeRepository,
        dedup_policy: DeduplicationPolicy,
        window: int = 50,
    ) -> None:
        self._selector = selector
        self._tp_sl_service = tp_sl_service
        self._signals = signal_repository
        self._trades = trade_repository
        self._dedup = dedup_policy
        self._window = window
        self._logger = get_logger(self.__class__.__name__)

    def run(
        self,
        symbol: str,
        candles: Sequence[Candle],
        thresholds: Thresholds,
    ) -> BacktestResult:
        generated_signals: list[Signal] = []
        generated_trades: list[Trade] = []
        for index in range(self._window, len(candles)):
            window_candles = candles[index - self._window : index]
            result = self._selector.select(symbol, window_candles, thresholds)
            for signal in result.signals:
                accepted, key = self._dedup.should_accept(signal)
                if not accepted:
                    self._logger.debug("Skipping duplicate signal %s", key)
                    continue
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
                    metadata={"mode": "backtest"},
                )
                self._tp_sl_service.assign(signal, trade)
                trade.opened_at = signal.candle.closed_at
                self._apply_backtest_outcome(signal, trade, candles[index:])
                self._signals.save(signal)
                self._trades.save(trade)
                generated_signals.append(signal)
                generated_trades.append(trade)
        return BacktestResult(trades=generated_trades, signals=generated_signals)

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
            trade.pnl = self._calculate_pnl(trade, exit_price)
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
