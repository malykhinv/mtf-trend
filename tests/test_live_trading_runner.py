from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta

import pytest

from bot.data.io.storage import Storage
from bot.data.repositories.signal_repository import SignalRepository
from bot.data.repositories.state_repository import StateRepository
from bot.data.repositories.trade_repository import TradeRepository
from bot.domain.enums import BreakDirection, Exchange, Side, Timeframe, TradeStatus
from bot.domain.models.entities import Candle, Signal, Thresholds
from bot.domain.services.dedup_policy import DeduplicationPolicy
from bot.domain.services.live_runner import LiveTradingRunner
from bot.domain.services.tp_sl_service import TpSlService


class FakeProvider:
    exchange = Exchange.BINANCE

    def __init__(self, candles_by_key: dict[tuple[str, Timeframe], list[list[Candle]]]) -> None:
        self._candles_by_key: dict[tuple[str, Timeframe], deque[list[Candle]]] = {
            key: deque(sequence) for key, sequence in candles_by_key.items()
        }

    async def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        queue = self._candles_by_key.setdefault((symbol, timeframe), deque())
        if queue:
            return queue.popleft()
        return []

    async def update_deposit(self) -> dict[str, float | str]:
        return {"asset": "USDT", "balance": 10_000.0}


def _build_signal(
    *,
    identifier: str,
    side: Side,
    direction: BreakDirection,
    timeframe: Timeframe,
    candle_close: datetime,
    entry_price: float,
    high: float,
    low: float,
) -> Signal:
    candle = Candle(
        id=f"candle-{identifier}",
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        timeframe=timeframe,
        open=entry_price,
        high=high,
        low=low,
        close=entry_price,
        volume=1000.0,
        started_at=candle_close - timedelta(minutes=15),
        closed_at=candle_close,
    )
    thresholds = Thresholds(
        id=f"thr-{identifier}",
        min_relative_volume=1.0,
        max_relative_volume=5.0,
        metadata={"timeframe": timeframe.value},
    )
    return Signal(
        id=f"sig-{identifier}",
        candle=candle,
        candle_id=candle.id,
        side=side,
        direction=direction,
        score=10.0,
        triggered_at=candle_close,
        thresholds=thresholds,
        timeframe=timeframe,
        created_at=candle_close,
        updated_at=candle_close,
    )


async def _execute_take_profit(tmp_path) -> None:
    now = datetime.utcnow().replace(microsecond=0)
    signal = _build_signal(
        identifier="tp",
        side=Side.LONG,
        direction=BreakDirection.HIGH_FIRST,
        timeframe=Timeframe.M15,
        candle_close=now,
        entry_price=100.0,
        high=110.0,
        low=90.0,
    )
    follow_up_candle = Candle(
        id="candle-follow-tp",
        symbol=signal.candle.symbol,
        exchange=signal.candle.exchange,
        timeframe=signal.candle.timeframe,
        open=101.0,
        high=111.0,
        low=99.0,
        close=110.5,
        volume=1500.0,
        started_at=signal.candle.closed_at,
        closed_at=signal.candle.closed_at + timedelta(minutes=15),
    )
    provider = FakeProvider(
        {(
            signal.candle.symbol,
            signal.candle.timeframe,
        ): [[follow_up_candle]]}
    )
    storage = Storage(tmp_path / "storage.xlsx")
    signal_repo = SignalRepository(storage)
    trade_repo = TradeRepository(storage)
    state_repo = StateRepository(storage)
    runner = LiveTradingRunner(
        provider=provider,
        selector=None,  # type: ignore[arg-type]
        tp_sl_service=TpSlService(),
        signal_repository=signal_repo,
        state_repository=state_repo,
        trade_repository=trade_repo,
        dedup_policy=DeduplicationPolicy(),
        window=1,
        poll_interval=0.01,
    )

    await runner._handle_signal(signal)

    async def _wait_for_status() -> TradeStatus:
        while True:
            trade = trade_repo.get(signal.id)
            if trade and trade.status != TradeStatus.OPENED:
                return trade.status
            await asyncio.sleep(0.01)

    status = await asyncio.wait_for(_wait_for_status(), timeout=1.0)
    assert status == TradeStatus.CLOSED_TP
    trade = trade_repo.get(signal.id)
    assert trade is not None
    assert trade.exit_price == pytest.approx(trade.tp_price)
    assert trade.pnl_pct == pytest.approx(trade.tp_pct)
    assert trade.pnl == pytest.approx(trade.size * (trade.pnl_pct or 0) / 100)
    assert trade.metadata.get("live", {}).get("closed_status") == TradeStatus.CLOSED_TP.value


async def _execute_stop_loss(tmp_path) -> None:
    now = datetime.utcnow().replace(microsecond=0)
    signal = _build_signal(
        identifier="sl",
        side=Side.SHORT,
        direction=BreakDirection.LOW_FIRST,
        timeframe=Timeframe.M15,
        candle_close=now,
        entry_price=200.0,
        high=210.0,
        low=190.0,
    )
    follow_up_candle = Candle(
        id="candle-follow-sl",
        symbol=signal.candle.symbol,
        exchange=signal.candle.exchange,
        timeframe=signal.candle.timeframe,
        open=201.0,
        high=212.0,
        low=198.0,
        close=211.0,
        volume=1600.0,
        started_at=signal.candle.closed_at,
        closed_at=signal.candle.closed_at + timedelta(minutes=15),
    )
    provider = FakeProvider(
        {(
            signal.candle.symbol,
            signal.candle.timeframe,
        ): [[follow_up_candle]]}
    )
    storage = Storage(tmp_path / "storage_sl.xlsx")
    signal_repo = SignalRepository(storage)
    trade_repo = TradeRepository(storage)
    state_repo = StateRepository(storage)
    runner = LiveTradingRunner(
        provider=provider,
        selector=None,  # type: ignore[arg-type]
        tp_sl_service=TpSlService(),
        signal_repository=signal_repo,
        state_repository=state_repo,
        trade_repository=trade_repo,
        dedup_policy=DeduplicationPolicy(),
        window=1,
        poll_interval=0.01,
    )

    await runner._handle_signal(signal)

    async def _wait_for_status() -> TradeStatus:
        while True:
            trade = trade_repo.get(signal.id)
            if trade and trade.status != TradeStatus.OPENED:
                return trade.status
            await asyncio.sleep(0.01)

    status = await asyncio.wait_for(_wait_for_status(), timeout=1.0)
    assert status == TradeStatus.CLOSED_SL
    trade = trade_repo.get(signal.id)
    assert trade is not None
    assert trade.exit_price == pytest.approx(trade.sl_price)
    assert trade.pnl_pct == pytest.approx(trade.sl_pct)
    assert trade.pnl == pytest.approx(trade.size * (trade.pnl_pct or 0) / 100)
    assert trade.metadata.get("live", {}).get("closed_status") == TradeStatus.CLOSED_SL.value


def test_trade_watcher_closes_on_take_profit(tmp_path) -> None:
    asyncio.run(_execute_take_profit(tmp_path))


def test_trade_watcher_closes_on_stop_loss(tmp_path) -> None:
    asyncio.run(_execute_stop_loss(tmp_path))
