from __future__ import annotations

import asyncio
import heapq
import random
from typing import List, Set, Tuple

from config.constants import TIMEFRAMES
from domain.models.timeframe import Timeframe

async def scheduler(
    symbols: List[str],
    jobs_queue: asyncio.Queue[Tuple[str, Timeframe]],
    inflight: Set[Tuple[str, Timeframe]],
    stop_event: asyncio.Event,
    *,
    throttle_divisor: int = 4,          # правило "не чаще, чем minutes // 4"
    min_interval_seconds: int = 60,     # нижняя граница интервала запуска
    initial_spread: float = 1.0,        # разнос первой волны задач в пределах одного интервала
    fire_jitter_fraction: float = 0.05, # небольшой джиттер при перепланировании (0..1)
) -> None:
    """
    Централизованный планировщик задач (symbol, timeframe) для анализаторов.

    Гарантии:
      - каждый ключ (symbol, timeframe) помещается в очередь не чаще, чем раз в
        max(60 сек, timeframe.minutes // throttle_divisor * 60)
      - пока ключ в работе (в inflight), новая постановка по нему не создаётся
      - используется монотоничное время event-loop (устойчиво к смене системных часов)
      - корректная остановка по stop_event

    Бэкпрешер:
      - если jobs_queue заполнена, scheduler ждёт освобождения места (естественный бэкпрешер)
    """
    # Санитизация параметров
    if throttle_divisor < 1:
        throttle_divisor = 1
    if min_interval_seconds < 1:
        min_interval_seconds = 1
    if initial_spread < 0.0:
        initial_spread = 0.0
    if not (0.0 <= fire_jitter_fraction <= 1.0):
        fire_jitter_fraction = 0.05

    loop = asyncio.get_running_loop()
    now = loop.time()

    def period_seconds(tf: Timeframe) -> float:
        raw = (tf.minutes // throttle_divisor) * 60
        secs = raw if raw >= min_interval_seconds else min_interval_seconds
        return float(secs)

    # Построение расписания: (due_ts, symbol, timeframe, interval_seconds)
    heap: list[tuple[float, str, Timeframe, float]] = []
    for s in symbols:
        for tf in TIMEFRAMES:
            interval = period_seconds(tf)
            spread = interval * initial_spread
            first_due = now + (random.uniform(0.0, spread) if spread > 0 else 0.0)
            heap.append((first_due, s, tf, interval))
    heapq.heapify(heap)

    # Нечего планировать — ждём остановку
    if not heap:
        await stop_event.wait()
        return

    try:
        while not stop_event.is_set():
            due, symbol, timeframe, interval = heap[0]  # ближайшая задача
            sleep_s = due - loop.time()
            if sleep_s > 0:
                # Спим до ближайшего дедлайна или до сигнала остановки
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=sleep_s)
                    break  # получили стоп — выходим
                except asyncio.TimeoutError:
                    pass  # время пришло

            # Наступил срок ближайшей задачи
            heapq.heappop(heap)
            key = (symbol, timeframe)

            if key not in inflight:
                # Ставим в работу; при полной очереди ждём
                await jobs_queue.put(key)
                inflight.add(key)

            # Стабильная каденция: перепланируем от предыдущего due, с небольшим джиттером
            jitter = interval * fire_jitter_fraction
            next_due = due + interval + (random.uniform(0.0, jitter) if jitter > 0 else 0.0)
            heapq.heappush(heap, (next_due, symbol, timeframe, interval))
    except asyncio.CancelledError:
        # Аккуратный выход по отмене
        return
