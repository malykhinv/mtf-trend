import asyncio
import os
from typing import Set, Tuple

from config.constants import TIMEFRAMES
from data.BinanceClient import BinanceClient
from data.Database import Database
from domain.exchange_client import ExchangeClient
from domain.models.Signal import Signal
from domain.models.Timeframe import Timeframe
from domain.workers.analysis_worker import create_analysis_worker
from domain.workers.signal_worker import create_signal_worker
from domain.workers.scheduler import scheduler
from services.Plotter import Plotter
from services.TelegramNotifier import TelegramNotifier
from utils.logger import log

ANALYZERS_PER_CLIENT = 10


async def run_for_client(
    client: ExchangeClient,
    telegram_notifier: TelegramNotifier,
    plotter: Plotter,
    db: Database,
) -> None:
    # Загружаем список инструментов
    symbols = await client.fetch_symbols()

    # Ограниченные очереди
    jobs_queue: asyncio.Queue[tuple[str, Timeframe]] = asyncio.Queue(maxsize=max(1, len(symbols) * len(TIMEFRAMES)))
    signals_queue: asyncio.Queue[Signal] = asyncio.Queue(maxsize=1000)

    # Набор активных ключей (symbol, timeframe), чтобы не ставить дубликаты
    inflight: Set[Tuple[str, Timeframe]] = set()

    # Событие остановки для планировщика
    stop_event = asyncio.Event()

    # Фабрики воркеров → корутины
    analysis_worker = create_analysis_worker()
    signal_worker = create_signal_worker()

    # Задачи: планировщик, пул анализаторов, отправка сигналов
    scheduler_task = asyncio.create_task(
        scheduler(symbols, jobs_queue, inflight, stop_event),
        name="scheduler"
    )
    analyzer_tasks = [
        asyncio.create_task(
            analysis_worker(jobs_queue, signals_queue, client, plotter, db, inflight),
            name=f"analyzer:{i}",
        )
        for i in range(ANALYZERS_PER_CLIENT)
    ]
    sender_task = asyncio.create_task(
        signal_worker(signals_queue, telegram_notifier), name="signal_sender"
    )

    try:
        # Работаем до внешней отмены/CTRL+C
        await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Переходим к аккуратной остановке
        pass
    finally:
        # 1) Останавливаем планировщик и дожидаемся его завершения
        stop_event.set()
        await asyncio.gather(scheduler_task, return_exceptions=True)

        # 2) Дожидаемся, пока анализаторы закончат все работы
        await jobs_queue.join()
        for t in analyzer_tasks:
            t.cancel()
        await asyncio.gather(*analyzer_tasks, return_exceptions=True)

        # 3) Дожидаемся отправки всех сигналов, затем останавливаем отправщика
        await signals_queue.join()
        sender_task.cancel()
        await asyncio.gather(sender_task, return_exceptions=True)
        log("main: отправщик сигналов остановлен.")
        log("main: остановка завершена.")


async def main(
    exchange_clients: list[ExchangeClient],
    telegram_notifier: TelegramNotifier,
    plotter: Plotter,
    db: Database,
) -> None:
    await asyncio.gather(*(run_for_client(client, telegram_notifier, plotter, db) for client in exchange_clients))


if __name__ == "__main__":
    clients: list[ExchangeClient] = [
        BinanceClient(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET"),
        )
    ]
    notifier = TelegramNotifier(
        token=os.getenv("TELEGRAM_TOKEN"),
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
    )
    plotter = Plotter()
    database = Database()

    asyncio.run(main(clients, notifier, plotter, database))
