from __future__ import annotations

import asyncio
import os
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config.constants import (
    OUTPUT_PLOT_PATH,
    WINDOW_TAIL,   # сколько баров тянем в «хвост»
    TIMEZONE,      # ZoneInfo, например ZoneInfo("Europe/Belgrade")
)

from data.BinanceClient import BinanceClient
from domain.detection import detect
from domain.models.Bar import Bar
from domain.models.Timeframe import Timeframe
from services.Plotter import Plotter
from utils.logger import log, logw


# ====== НАСТРОЙКИ ТЕСТА ======
SYMBOL: str = "VINE/USDT"
TARGET_DT: datetime = datetime(2025, 7, 5, 12, 0, tzinfo=TIMEZONE)
MICROSTEP: timedelta = timedelta(milliseconds=1)
TIMEFRAMES: list[Timeframe] = [
    # Timeframe.M1,
    # Timeframe.M5,
    # Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    # Timeframe.D1
]


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """
    Вернёт dt в указанной таймзоне tz:
    - если dt naive: присваиваем tz как tzinfo (считаем, что dt уже в локальном времени),
    - если dt aware: приводим к tz через astimezone.
    """
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def bar_time(bar: Bar) -> datetime:
    """Строго берём время бара и приводим к TIMEZONE."""
    return to_local(bar.time, TIMEZONE)


async def fetch_bars_until(
    client: BinanceClient,
    symbol: str,
    timeframe: Timeframe,
    tail: int,
    end_dt: datetime,
) -> list[Bar]:
    """
    Тянем последние `tail` баров так, чтобы правый край не был позже `end_dt`.
    Требуется поддержка параметра `end_dt` в BinanceClient.fetch_bars(...).
    """
    result: list[Bar] = []
    cursor: datetime = to_local(end_dt, TIMEZONE)
    max_pages: int = 50  # предохранитель

    while len(result) < tail and max_pages > 0:
        limit = min(1000, tail - len(result))
        chunk: list[Bar] = await client.fetch_bars(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            end_dt=cursor,
        )
        if not chunk:
            break

        # CCXT обычно возвращает бары по возрастанию времени.
        # Конкатенируем так, чтобы итог был по времени: (более старые) + (что уже накопили).
        result = list(chunk) + result

        # Сдвигаем курсор левее самого левого бара на микрошаг — чтобы исключить дубликаты при следующем запросе.
        cursor = bar_time(result[0]) - MICROSTEP
        max_pages -= 1

    return result[-tail:]


async def main() -> None:
    # Каталог для графиков
    Path(OUTPUT_PLOT_PATH).mkdir(parents=True, exist_ok=True)

    client = BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
    )
    plotter = Plotter()

    log(
        f"Тест для {SYMBOL} до {TARGET_DT.isoformat()} | "
        f"таймфреймы: {[tf.name for tf in TIMEFRAMES]}"
    )

    for tf in TIMEFRAMES:
        try:
            bars = await fetch_bars_until(client, SYMBOL, tf, WINDOW_TAIL, TARGET_DT)
            if len(bars) < 10:
                logw(f"[{tf.name}] Недостаточно баров: {len(bars)}")
                # даже если мало баров, detect сам решит — в тестовом режиме он все равно попытается сохранить график
                # (при < min_needed он нарисует «чистый» график без меток)
            # detect сам рисует и сохраняет график в OUTPUT_PLOT_PATH (в тесте — всегда)
            signal = detect(SYMBOL, client.exchange, bars, tf, plotter, test_mode=True)
            if signal:
                log(f"[{tf.name}] СИГНАЛ найден: {signal}")
            else:
                logw(f"[{tf.name}] Сигнал не найден")

        except Exception as exc:
            logw(f"[{tf.name}] Ошибка теста: {exc}\n{traceback.format_exc()}")

    log("Готово.")


if __name__ == "__main__":
    asyncio.run(main())
