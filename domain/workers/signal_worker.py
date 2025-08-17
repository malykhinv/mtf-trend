from __future__ import annotations

import asyncio
from typing import Callable, Coroutine, Any

from domain.models.Signal import Signal
from services.TelegramNotifier import TelegramNotifier
from utils.logger import logw, log
from utils.message_formatter import format_message


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
                    message = format_message(signal)
                    log(f"Готовлю отправку: {signal.symbol} @ {signal.timeframe.value}")
                    await notifier.send_message(message, signal.chart_path)
                    log(f"✉️ Отправил сигнал: {signal.symbol} @ {signal.timeframe.value}")
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logw(f"Ошибка отправки сигнала {signal.symbol}: {error}")
                finally:
                    signals_queue.task_done()
        except asyncio.CancelledError:
            pass

    return signal_worker
