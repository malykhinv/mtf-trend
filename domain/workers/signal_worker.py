from __future__ import annotations

import asyncio
from typing import Callable, Coroutine, Any

from domain.models.signal import Signal
from services.telegram_notifier import TelegramNotifier
from utils.logger import logw


def create_signal_worker() -> Callable[
    [asyncio.Queue[Signal], TelegramNotifier],
    Coroutine[Any, Any, None]
]:
    async def signal_worker(
        signals_queue: asyncio.Queue[Signal],
        notifier: TelegramNotifier
    ) -> None:
        try:
            while True:
                signal = await signals_queue.get()
                try:
                    message = format(signal)
                    await notifier.send_message(message, signal.chart_path)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logw(f"Ошибка обработки сигнала {signal.symbol}: {error}")
                finally:
                    signals_queue.task_done()
        except asyncio.CancelledError:
            pass

    return signal_worker
