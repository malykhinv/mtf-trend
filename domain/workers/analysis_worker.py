# domain/workers/analysis_worker.py
from __future__ import annotations

import asyncio
from typing import Callable, Coroutine, Any, Set

from data.database import Database
from domain.detection import detect
from domain.exchange_client import ExchangeClient
from domain.models.signal import Signal
from domain.models.timeframe import Timeframe
from services.plotter import Plotter
from utils.logger import log, logw


def create_analysis_worker() -> Callable[
    [asyncio.Queue[tuple[str, Timeframe]], asyncio.Queue[Signal], ExchangeClient, Plotter, Database, Set[tuple[str, Timeframe]]],
    Coroutine[Any, Any, None]
]:
    async def analysis_worker(
        jobs_queue: asyncio.Queue[tuple[str, Timeframe]],
        signals_queue: asyncio.Queue[Signal],
        client: ExchangeClient,
        plotter: Plotter,
        db: Database,
        inflight: Set[tuple[str, Timeframe]],
    ) -> None:
        while True:
            symbol, timeframe = await jobs_queue.get()
            key = (symbol, timeframe)
            try:
                # Не чаще, чем четверть ТФ (минимум 1 мин)
                throttle_minutes = max(1, timeframe.minutes // 4)
                if await db.check_if_recent(symbol, timeframe, minutes=throttle_minutes):
                    continue

                bars = await client.fetch_bars(symbol, timeframe, limit=500)
                if not bars:
                    continue

                signal = detect(bars, timeframe, plotter)
                if signal:
                    await signals_queue.put(signal)
                    await db.save_signal(symbol, timeframe)
                    log(f"📈 Найден сигнал: {symbol} @ {timeframe.value} — отправляю в канал.")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logw(f"Ошибка анализа {symbol} ({timeframe.value}): {error}")
            finally:
                inflight.discard(key)
                jobs_queue.task_done()

    return analysis_worker
