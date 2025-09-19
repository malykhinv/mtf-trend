from __future__ import annotations

import asyncio
import types
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bot.data.io.storage import Storage
from bot.data.models import DepositSnapshot
from bot.data.repositories.signal_repository import SignalRepository
from bot.data.repositories.state_repository import StateRepository
from bot.data.repositories.trade_repository import TradeRepository
from bot.domain.enums import BreakDirection, Exchange, Side, Timeframe, TradeStatus
from bot.domain.models.entities import Candle, Signal, Thresholds
from bot.domain.models.metadata import ThresholdsMetadata
from bot.domain.services.metrics_service import SelectionMetricsSnapshot
from bot.domain.services.dedup_policy import DeduplicationPolicy
from bot.domain.services.live_runner import LiveTradingRunner
from bot.domain.services.tp_sl_service import TpSlService
from bot.domain.services.selector_service import SelectionResult


class FakeProvider:
    exchange = Exchange.BINANCE

    def __init__(self, candles_by_key: dict[tuple[str, Timeframe], list[list[Candle]]]) -> None:
        self._candles_by_key: dict[tuple[str, Timeframe], deque[list[Candle]]] = {
            key: deque(sequence) for key, sequence in candles_by_key.items()
        }

    async def fetch_ohlcv(
        self, symbol: str, timeframe: Timeframe, limit: int, since: int | None = None
    ) -> list[Candle]:
        queue = self._candles_by_key.setdefault((symbol, timeframe), deque())
        if queue:
            return queue.popleft()
        return []

    async def update_deposit(self) -> DepositSnapshot:
        return DepositSnapshot(asset="USDT", balance=10_000.0)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


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
        metadata=ThresholdsMetadata(timeframe=timeframe),
    )
    snapshot = SelectionMetricsSnapshot(
        atr=1.0,
        average_volume=2.0,
        momentum=3.0,
        pct_move=4.0,
        relative_volume=5.0,
        atr_multiple=6.0,
        upper_wick_pct=7.0,
        body_pct=8.0,
        lower_wick_pct=9.0,
        pct_to_high=10.0,
        pct_to_low=-11.0,
        pct_to_high_break=12.0,
        pct_to_low_break=13.0,
        break_direction=BreakDirection.NONE,
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
        metrics_snapshot=snapshot,
    )


async def _execute_take_profit(tmp_path) -> None:
    now = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
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
    )

    runner._get_poll_delay = types.MethodType(lambda self, timeframe: 0.01, runner)

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
    assert trade.size == pytest.approx(500.0)
    assert trade.exit_price == pytest.approx(trade.tp_price)
    assert trade.pnl_pct == pytest.approx(trade.tp_pct)
    assert trade.pnl == pytest.approx(trade.size * (trade.pnl_pct or 0) / 100)
    assert trade.metadata is not None
    assert trade.metadata.live is not None
    assert trade.metadata.live.closed_status == TradeStatus.CLOSED_TP


async def _execute_stop_loss(tmp_path) -> None:
    now = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
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
    )

    runner._get_poll_delay = types.MethodType(lambda self, timeframe: 0.01, runner)

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
    assert trade.size == pytest.approx(500.0)
    assert trade.exit_price == pytest.approx(trade.sl_price)
    assert trade.pnl_pct == pytest.approx(trade.sl_pct)
    assert trade.pnl == pytest.approx(trade.size * (trade.pnl_pct or 0) / 100)
    assert trade.metadata is not None
    assert trade.metadata.live is not None
    assert trade.metadata.live.closed_status == TradeStatus.CLOSED_SL


def test_trade_watcher_closes_on_take_profit(tmp_path) -> None:
    asyncio.run(_execute_take_profit(tmp_path))


def test_trade_watcher_closes_on_stop_loss(tmp_path) -> None:
    asyncio.run(_execute_stop_loss(tmp_path))


async def _assert_timeframe_cadence(tmp_path, monkeypatch) -> None:
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = FakeClock(base_time)
    monkeypatch.setattr("bot.domain.services.live_runner.utcnow", clock.now)

    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        clock.advance(delay)
        await real_sleep(0)

    monkeypatch.setattr("bot.domain.services.live_runner.asyncio.sleep", fake_sleep)

    class CadenceProvider:
        exchange = Exchange.BINANCE

        def __init__(self, clock: FakeClock) -> None:
            self._clock = clock
            self.calls: dict[Timeframe, list[datetime]] = {
                tf: [] for tf in (Timeframe.M1, Timeframe.M3, Timeframe.M5, Timeframe.M15)
            }
            self._last_close: dict[Timeframe, datetime] = {
                tf: clock.now() - timedelta(seconds=_seconds_for_timeframe(tf))
                for tf in self.calls
            }

        async def fetch_ohlcv(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int,
            since: int | None = None,
        ) -> list[Candle]:
            self.calls[timeframe].append(self._clock.now())
            seconds = _seconds_for_timeframe(timeframe)
            next_close = self._last_close[timeframe] + timedelta(seconds=seconds)
            self._last_close[timeframe] = next_close
            candle = Candle(
                id=f"{symbol}-{timeframe.value}-{len(self.calls[timeframe])}",
                symbol=symbol,
                exchange=Exchange.BINANCE,
                timeframe=timeframe,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000.0,
                started_at=next_close - timedelta(seconds=seconds),
                closed_at=next_close,
            )
            return [candle]

        async def update_deposit(self) -> DepositSnapshot:
            return DepositSnapshot(asset="USDT", balance=10_000.0)

    def _seconds_for_timeframe(timeframe: Timeframe) -> int:
        return {
            Timeframe.M1: 60,
            Timeframe.M3: 180,
            Timeframe.M5: 300,
            Timeframe.M15: 900,
        }[timeframe]

    provider = CadenceProvider(clock)
    storage = Storage(tmp_path / "cadence.xlsx")
    runner = LiveTradingRunner(
        provider=provider,
        selector=_NoopSelector(),
        tp_sl_service=TpSlService(),
        signal_repository=SignalRepository(storage),
        state_repository=StateRepository(storage),
        trade_repository=TradeRepository(storage),
        dedup_policy=DeduplicationPolicy(),
        window=1,
    )

    thresholds = Thresholds(id="thr-cadence")
    task = asyncio.create_task(runner._process_symbol("BTCUSDT", thresholds))

    async def _wait_for_samples() -> None:
        while min(len(samples) for samples in provider.calls.values()) < 3:
            await real_sleep(0)

    await asyncio.wait_for(_wait_for_samples(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for timeframe, timestamps in provider.calls.items():
        assert len(timestamps) >= 3
        expected = runner._get_poll_delay(timeframe)
        for earlier, later in zip(timestamps, timestamps[1:]):
            delta = (later - earlier).total_seconds()
            assert delta >= expected - 1e-6


class _NoopSelector:
    def select(
        self,
        symbol: str,
        candles: list[Candle],
        thresholds: Thresholds,
        future_candles: list[Candle] | None = None,
    ) -> SelectionResult:
        return SelectionResult(signals=[], rejected=[])


def test_timeframe_cadence_respects_limits(tmp_path, monkeypatch) -> None:
    asyncio.run(_assert_timeframe_cadence(tmp_path, monkeypatch))
