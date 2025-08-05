from __future__ import annotations

import asyncio
from pathlib import Path

from config.constants import MIN_MARKET_CAP
from data.coin_info_provider import filter_by_market_cap
from data.memory_client import MemoryExchangeClient
from domain.detection import detect_extremums
from domain.extremum_tracker import ExtremumTracker
from domain.timeframe import Timeframe
from services.plotter import Plotter
from services.telegram import TelegramSender
from utils.atr import atr
from utils.logger import logger


class DummyPlotter:
    def plot(self, bars, extremums, path: str) -> None:  # type: ignore[override]
        logger.info("Сохранение графика в %s", path)


class DummySender:
    async def send_message(self, text: str) -> None:  # type: ignore[override]
        logger.info("Отправка сообщения: %s", text)


async def analysis_worker(
    symbols: asyncio.Queue[str],
    signals: asyncio.Queue[tuple[str, float]],
    client: MemoryExchangeClient,
) -> None:
    while True:
        symbol = await symbols.get()
        for timeframe in Timeframe:
            bars = await client.fetch_bars(symbol, timeframe, 100)
            tracker = ExtremumTracker()
            exts = detect_extremums(bars)
            tracker.update(exts)
            if tracker.last():
                signal_price = tracker.last().price  # type: ignore[union-attr]
                signals.put_nowait((symbol, signal_price))
        symbols.task_done()


async def signal_worker(signals: asyncio.Queue[tuple[str, float]], sender: TelegramSender) -> None:
    while True:
        symbol, price = await signals.get()
        await sender.send_message(f"Сигнал по {symbol}: {price:.2f}")
        signals.task_done()


async def main() -> None:
    bars = {}
    market_caps = {"BTCUSDT": 1_000_000_000}
    client = MemoryExchangeClient(bars, market_caps)
    symbols = asyncio.Queue[str]()
    signals = asyncio.Queue[tuple[str, float]]()
    sender = DummySender()
    _plotter = DummyPlotter()

    caps = await client.fetch_market_caps()
    symbols_list = filter_by_market_cap(caps, MIN_MARKET_CAP)
    for sym in symbols_list:
        await symbols.put(sym)

    workers = [
        asyncio.create_task(analysis_worker(symbols, signals, client)),
        asyncio.create_task(signal_worker(signals, sender)),
    ]

    await symbols.join()
    await asyncio.sleep(0)
    for w in workers:
        w.cancel()


if __name__ == "__main__":
    asyncio.run(main())

