from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..enums import TradeStatus
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
                    status=TradeStatus.CLOSED,
                    entry_price=signal.candle.close,
                    size=1.0,
                    metadata={"mode": "backtest"},
                )
                self._tp_sl_service.assign(signal, trade)
                trade.opened_at = signal.candle.closed_at
                trade.closed_at = signal.candle.closed_at
                trade.pnl = trade.tp_price - trade.entry_price if trade.tp_price else 0.0
                self._signals.save(signal)
                self._trades.save(trade)
                generated_signals.append(signal)
                generated_trades.append(trade)
        return BacktestResult(trades=generated_trades, signals=generated_signals)
