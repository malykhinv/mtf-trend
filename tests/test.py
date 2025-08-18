# test/test.py
from __future__ import annotations

import os
import asyncio
from datetime import datetime, timedelta
from typing import Sequence

from config.constants import (
    TIMEFRAMES,            # можно заменить своим списком ТФ
    OUTPUT_PLOT_PATH,
    WINDOW_TAIL,
    TIMEZONE,
)
from data.BinanceClient import BinanceClient
from domain.models.Timeframe import Timeframe
from domain.models.Extremum import Extremum
from domain.models.ExtremumType import ExtremumType
from domain.detection import detect, _has_downtrend
from services.Plotter import Plotter
from utils.logger import log, logw
from utils.atr import atr  # Wilder ATR (как в основной детекции)

# ====== НАСТРОЙКИ ТЕСТА (редактируй тут) ======
SYMBOL = "BTC/USDT"

YEAR, MONTH, DAY = 2025, 8, 1
HOUR, MINUTE = 12, 0

# Можно использовать общий список из constants, или задать вручную:
TIMEFRAMES_TO_TEST: Sequence[Timeframe] = TIMEFRAMES  # например: [Timeframe.M5, Timeframe.M15, Timeframe.H1]

# Сколько баров минимум захватывать «в хвост» относительно выбранного времени
TAIL_BARS = WINDOW_TAIL
# ==============================================


async def _fetch_bars_until(
    client: BinanceClient,
    symbol: str,
    timeframe: Timeframe,
    end_dt: datetime,
    tail_bars: int,
):
    """
    Тянем OHLCV так, чтобы последний бар был не правее end_dt.
    Используем ccxt.fetch_ohlcv с параметром since.
    """
    # запас по времени: хвост + небольшой буфер
    minutes_span = tail_bars * timeframe.minutes + 10
    since_dt = end_dt - timedelta(minutes=minutes_span)
    since_ms = int(since_dt.timestamp() * 1000)
    limit = tail_bars + 200  # небольшой запас

    # ccxt: [ts, open, high, low, close, volume]
    raw = await client._client.fetch_ohlcv(symbol, timeframe.value, since=since_ms, limit=limit)
    bars = [client._to_bar(row) for row in raw]  # используем готовый конвертер клиента
    bars = [b for b in bars if b.time <= end_dt]  # обрезаем правый край под нужное время
    return bars


async def main() -> None:
    plotter = Plotter()
    client = BinanceClient(
        api_key=os.getenv("BINANCE_API_KEY") or "",
        api_secret=os.getenv("BINANCE_API_SECRET") or "",
    )

    try:
        target_dt = datetime(YEAR, MONTH, DAY, HOUR, MINUTE, tzinfo=TIMEZONE)
        os.makedirs(OUTPUT_PLOT_PATH, exist_ok=True)

        log(f"Тест детектора: {SYMBOL} | ТФ: {', '.join(tf.value for tf in TIMEFRAMES_TO_TEST)} | момент: {target_dt.isoformat()}")

        for tf in TIMEFRAMES_TO_TEST:
            try:
                bars = await _fetch_bars_until(client, SYMBOL, tf, target_dt, TAIL_BARS)
                if len(bars) < 100:
                    logw(f"[{SYMBOL} {tf.value}] Недостаточно баров для анализа: {len(bars)}. Пропуск.")
                    continue

                # Запуск основной детекции на правом краю (как если бы «сейчас» = target_dt)
                signal = detect(SYMBOL, client.exchange, bars, tf, plotter)
                if signal:
                    log(f"[{SYMBOL} {tf.value}] ✅ Сигнал обнаружен. График: {signal.chart_path}")
                    continue  # уже всё отрисовано внутри detect

                # Если сигнала нет — найдём последнюю валидную эпоху и всё равно нарисуем экстремумы
                atrs = atr(bars, 14)  # period из constants в основной детекции
                s = max(0, len(bars) - TAIL_BARS)
                e = len(bars) - 1
                from config.constants import TREND_ITERATIONS, FRESH_MAX_AGE

                r = _has_downtrend(bars, atrs, s, e, TREND_ITERATIONS)
                if getattr(r, "has_downtrend", False):
                    bar1_idx, sh_last_idx, last_ll_idx = r.bar1_idx, r.sh_last_idx, r.last_ll_idx
                    age = e - last_ll_idx
                    if age <= FRESH_MAX_AGE:
                        exts = [
                            Extremum(bar=bars[bar1_idx], type=ExtremumType.LOW),
                            Extremum(bar=bars[sh_last_idx], type=ExtremumType.HIGH),
                        ]
                        if last_ll_idx not in (bar1_idx, sh_last_idx):
                            exts.append(Extremum(bar=bars[last_ll_idx], type=ExtremumType.LOW))

                        stamp = target_dt.strftime("%Y%m%d_%H%M")
                        chart_path = os.path.join(
                            OUTPUT_PLOT_PATH,
                            f"epoch__{SYMBOL.replace('/', '-') }__{tf.value}__{stamp}.png",
                        )
                        plotter.plot(bars, exts, path=chart_path)
                        log(
                            f"[{SYMBOL} {tf.value}] Эпоха найдена (без сигнала). "
                            f"bar1={bar1_idx}, bar2={sh_last_idx}, last_ll={last_ll_idx}. "
                            f"Сохранил график: {chart_path}"
                        )
                    else:
                        logw(f"[{SYMBOL} {tf.value}] Эпоха устарела: age={age} > FRESH_MAX_AGE.")
                else:
                    logw(f"[{SYMBOL} {tf.value}] Нисходящая структура не найдена — нечего рисовать.")
            except Exception as err:
                logw(f"[{SYMBOL} {tf.value}] Ошибка: {err!r}")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
