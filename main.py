"""Главная точка входа торгового бота.

Модуль загружает параметры из ``config.yaml``, создаёт клиентов бирж и
запускает основные циклы стратегии. Загруженная конфигурация сохраняется в
глобальной переменной ``CONFIG`` для доступа из других модулей.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple, Sequence
from decimal import Decimal
import math

import yaml
from dotenv import load_dotenv

from exchanges import BaseExchange
from exchanges.binance import BinanceExchange
from exchanges.bybit import BybitExchange
from risk import risk_control
from strategies import funding_arbitrage as strategy
from ai.parameter_optimizer import periodic_optimization
from utils.telegram import (
    format_decimal,
    format_duration,
    notify_close,
    notify_open,
    shutdown,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Глобальный словарь конфигурации, доступный другим модулям.
CONFIG: Dict[str, Any] = {}

# Соответствие имени биржи созданному клиенту.
CLIENTS: Dict[str, BaseExchange] = {}

# Белые списки символов для каждой биржи.
WHITELISTS: Dict[str, List[str]] = {}

# Отслеживаем задачи мониторинга открытых позиций,
# чтобы при необходимости отменить их при завершении работы.
POSITION_TASKS: Dict[str, asyncio.Task] = {}

# Отслеживаем последние предупреждения о неполных метриках
# для каждого символа, чтобы подавлять повторяющиеся сообщения.
_METRIC_WARNINGS: Dict[str, Tuple[float, int]] = {}
# Минимальный интервал между предупреждениями по одному символу.
_WARN_COOLDOWN = 60.0


def _log_incomplete_metrics(exchange: str, symbol: str, missing: Sequence[str]) -> None:
    """Логирует предупреждение о неполных метриках с учётом кулдауна."""
    key = f"{exchange}:{symbol}"
    now = time.time()
    last_ts, count = _METRIC_WARNINGS.get(key, (0.0, 0))
    if now - last_ts < _WARN_COOLDOWN:
        _METRIC_WARNINGS[key] = (last_ts, count + 1)
        return
    if count:
        logger.warning(
            "Пропуск %s:%s из-за неполных метрик (%s) (ещё %d раз)",
            exchange,
            symbol,
            ", ".join(missing),
            count,
        )
    else:
        logger.warning(
            "Пропуск %s:%s из-за неполных метрик (%s)",
            exchange,
            symbol,
            ", ".join(missing),
        )
    _METRIC_WARNINGS[key] = (now, 0)


def _reset_metric_warning(exchange: str, symbol: str) -> None:
    """Сбрасывает счётчик предупреждений для символа."""
    _METRIC_WARNINGS.pop(f"{exchange}:{symbol}", None)


def _parse_deposit_value(deposit_raw: Any) -> float | None:
    """Преобразует значение депозита в ``float``.

    При некорректном значении логирует ошибку и возвращает ``None``.
    """
    try:
        deposit = float(deposit_raw)
    except (TypeError, ValueError):
        logger.error("Некорректное значение депозита: %s", deposit_raw)
        return None
    if not math.isfinite(deposit) or deposit < 0:
        logger.error("Некорректное значение депозита: %s", deposit_raw)
        return None
    return deposit


def load_config(path: str = "config.yaml") -> None:
    """Загружает конфигурацию YAML в глобальную переменную ``CONFIG``.

    Параметры
    ---------
    path:
        Путь к файлу конфигурации. По умолчанию ``config.yaml``
        в текущей директории.
    """
    global CONFIG
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as f:
        CONFIG = yaml.safe_load(f) or {}
    # Подгружаем оптимизированные пороги стратегии
    CONFIG["thresholds"] = strategy.get_thresholds(CONFIG.get("thresholds", {}))

    # API-ключи загружаем из переменных окружения
    api_keys = {
        "binance": os.getenv("BINANCE_API_KEY"),
        "binance_secret": os.getenv("BINANCE_API_SECRET"),
        "bybit": os.getenv("BYBIT_API_KEY"),
        "bybit_secret": os.getenv("BYBIT_API_SECRET"),
    }
    CONFIG["api_keys"] = {k: v for k, v in api_keys.items() if v}


async def initialize_bot() -> None:
    """Настраивает клиентов бирж и управление рисками.

    Выполняет асинхронные запросы внутри основного цикла события, чтобы
    клиенты бирж создавали ``ClientSession`` уже после запуска ``asyncio``
    loop. Временные сессии, открытые при первичном сборе данных, закрываются
    перед началом основной работы.
    """

    api_keys = CONFIG.get("api_keys", {})
    logger.info("Инициализация бота с API-ключами: %s", list(api_keys.keys()))

    # Создаём клиентов бирж, если заданы ключи
    binance_key = api_keys.get("binance")
    binance_secret = api_keys.get("binance_secret")
    if binance_key and binance_secret:
        CLIENTS["binance"] = BinanceExchange(binance_key, binance_secret)

    bybit_key = api_keys.get("bybit")
    bybit_secret = api_keys.get("bybit_secret")
    if bybit_key and bybit_secret:
        CLIENTS["bybit"] = BybitExchange(bybit_key, bybit_secret)

    # Загружаем белые списки символов для каждой биржи
    bot_cfg = CONFIG.get("bot", {})
    wl_cfg = bot_cfg.get("whitelist", {})
    if isinstance(wl_cfg, dict):
        WHITELISTS.update({k: list(v) for k, v in wl_cfg.items()})
    else:
        symbols = list(wl_cfg) if isinstance(wl_cfg, list) else []
        for name in CLIENTS:
            WHITELISTS[name] = symbols

    async def _collect(exchange: BaseExchange) -> set[str]:
        futures = await exchange.get_futures_symbols()
        spot = await exchange.get_spot_symbols()
        return set(futures) & set(spot)

    async def _fetch_all() -> dict[str, set[str]]:
        """Получает доступные символы всех бирж параллельно."""

        async def _task(name: str, client: BaseExchange) -> tuple[str, set[str]]:
            try:
                return name, await _collect(client)
            except Exception as exc:  # pragma: no cover - network errors
                logger.error("Не удалось получить символы %s: %s", name, exc)
                return name, set()

        pairs = await asyncio.gather(
            *(_task(n, c) for n, c in CLIENTS.items())
        )
        return dict(pairs)

    available = await _fetch_all() if CLIENTS else {}
    for name, allowed in available.items():
        configured = WHITELISTS.get(name, [])
        filtered = [s for s in configured if s in allowed]
        removed = set(configured) - set(filtered)
        WHITELISTS[name] = filtered
        for sym in sorted(removed):
            logger.warning("Исключён символ %s из whitelist %s", sym, name)

    # Закрываем временные HTTP-сессии, созданные при инициализации, чтобы
    # последующие запросы открывали их уже внутри основного цикла.
    await asyncio.gather(
        *(client.close() for client in CLIENTS.values() if hasattr(client, "close")),
        return_exceptions=True,
    )

    # Конфигурируем контроль рисков
    risk_control.configure(CONFIG.get("risk", {}), bot_cfg.get("deposit_size"))


async def monitor_position(exchange_name: str, symbol: str, quantity: Decimal) -> None:
    """Следит за открытой позицией до срабатывания условий выхода."""
    poll_interval = CONFIG.get("bot", {}).get("poll_interval", 5)
    exchange = CLIENTS[exchange_name]
    position_id = f"{exchange_name}:{symbol}"
    entry: strategy.Position | None = None
    try:
        entry = await strategy.monitor_neutral_position(
            exchange,
            symbol,
            quantity,
            CONFIG.get("thresholds", {}),
            CONFIG,
            poll_interval,
            position_id=position_id,
        )
    except Exception as exc:
        entry = entry or strategy.positions.get(symbol)
        entry_ts = entry.entry_timestamp if entry else time.time()
        hold = time.time() - entry_ts
        funding_pct = format_decimal(
            (entry.entry_funding if entry else 0.0) * 100, 4
        )
        basis_pct = format_decimal(entry.entry_basis if entry else 0.0, 4)
        volume_usd = (
            entry.entry_futures_price * entry.initial_quantity
            if entry
            else Decimal("0")
        )
        volume_fmt = format_decimal(volume_usd, 2)
        asyncio.create_task(
            notify_close(
                position_id,
                (
                    f"Ошибка на {exchange_name} {symbol}: {exc}\n"
                    f"Фандинг: {funding_pct}%\n"
                    f"Базис: {basis_pct}%\n"
                    f"Объём: ${volume_fmt}\n"
                    f"Время в позиции: {format_duration(hold)}"
                ),
            )
        )
        raise
    else:
        if entry is None:
            entry = strategy.positions.get(symbol)
        pnl = entry.pnl if entry else Decimal("0")
        reasons = entry.exit_reasons if entry else []
        exit_ts = entry.exit_timestamp if entry else 0
        hold = exit_ts - (entry.entry_timestamp if entry else 0)
        funding_rate = (
            entry.exit_funding
            if entry and entry.exit_funding is not None
            else (entry.entry_funding if entry else 0.0)
        )
        funding_pct = format_decimal(funding_rate * 100, 4)
        basis_pct = format_decimal(entry.exit_basis if entry else 0.0, 4)
        volume_usd = (
            entry.entry_futures_price * entry.initial_quantity
            if entry
            else Decimal("0")
        )
        volume_fmt = format_decimal(volume_usd, 2)
        pnl_pct = (pnl / volume_usd * 100) if volume_usd else Decimal("0")
        pnl_fmt = format_decimal(pnl, 4)
        pnl_pct_fmt = format_decimal(pnl_pct, 4)
        asyncio.create_task(
            notify_close(
                position_id,
                (
                    f"Закрыта {symbol} на {exchange_name}\n"
                    f"Фандинг: {funding_pct}%\n"
                    f"Базис: {basis_pct}%\n"
                    f"Объём: ${volume_fmt}\n"
                    f"Время в позиции: {format_duration(hold)}\n"
                    f"PnL: {pnl_fmt} ({pnl_pct_fmt}%) Причины: {reasons}"
                ),
            )
        )
    finally:
        entry_final = entry or strategy.positions.get(symbol)
        notional = (
            entry_final.entry_futures_price * entry_final.initial_quantity
            if entry_final
            else Decimal("0")
        )
        await risk_control.update_position(-notional)
        await risk_control.mark_symbol_closed(symbol)
        if position_id in POSITION_TASKS:
            del POSITION_TASKS[position_id]
        if strategy.positions.pop(symbol, None) is not None:
            await strategy.save_positions(CONFIG)


async def start_processing_loops() -> None:
    """Запускает циклы стратегии, рисков и оптимизации параметров."""

    poll_interval = CONFIG.get("bot", {}).get("poll_interval", 5)

    # Восстанавливаем ранее сохранённые позиции
    await strategy.load_positions(CONFIG)

    def _update_thresholds(new: Dict[str, float]) -> None:
        """Обновляет пороги стратегии новыми значениями."""
        if new:
            CONFIG.setdefault("thresholds", {}).update(new)

    async def scan_loop() -> None:
        """Постоянно сканирует рынок в поиске входов."""
        while True:
            thresholds = CONFIG.get("thresholds", {})
            deposit_raw = CONFIG.get("bot", {}).get("deposit_size", float("inf"))
            deposit = _parse_deposit_value(deposit_raw)
            if deposit is None:
                await asyncio.sleep(poll_interval)
                continue
            deposit_pct_raw = thresholds.get("deposit_pct", 0.05)
            deposit_pct = max(0.0, min(1.0, deposit_pct_raw))
            min_trade_usd = thresholds.get("min_trade_size", float("inf"))
            if deposit_pct_raw != deposit_pct or min_trade_usd <= 0:
                logger.warning(
                    "Некорректные параметры торговли: deposit_pct=%s, min_trade_size=%s",
                    format_decimal(deposit_pct_raw, 4),
                    format_decimal(min_trade_usd, 2),
                )
                await asyncio.sleep(poll_interval)
                continue
            # Торгуем не меньше минимального порога: выбираем большую величину
            trade_value = max(min_trade_usd, deposit * deposit_pct)
            if not math.isfinite(trade_value) or trade_value <= 0:
                logger.error(
                    "Некорректное значение trade_value: %s",
                    format_decimal(trade_value, 2),
                )
                await asyncio.sleep(poll_interval)
                continue

            for name, client in CLIENTS.items():
                # Перебираем символы из белого списка
                for symbol in WHITELISTS.get(name, []):
                    if await risk_control.is_paused() or await risk_control.is_symbol_open(symbol):
                        continue
                    try:
                        base_metrics = await strategy.get_market_metrics(
                            symbol, Decimal("1"), client
                        )
                    except strategy.MissingMetricsError as err:
                        _log_incomplete_metrics(name, symbol, err.missing_fields)
                        continue
                    except Exception as exc:
                        logger.error("Ошибка метрик %s %s: %s", name, symbol, exc)
                        continue
                    price = base_metrics.futures_price
                    quantity = (
                        Decimal(str(trade_value)) / Decimal(str(price))
                        if price
                        else Decimal(0)
                    )
                    try:
                        metrics = await strategy.get_market_metrics(
                            symbol, quantity, client
                        )
                    except strategy.MissingMetricsError as err:
                        _log_incomplete_metrics(name, symbol, err.missing_fields)
                        continue
                    except Exception as exc:
                        logger.error("Ошибка метрик %s %s: %s", name, symbol, exc)
                        continue

                    _reset_metric_warning(name, symbol)

                    if await strategy.check_entry_conditions(
                        symbol, quantity, metrics, thresholds, CONFIG
                    ):
                        # Условия входа выполнены – открываем позицию
                        try:
                            await strategy.open_neutral_position(
                                client, symbol, quantity, CONFIG, WHITELISTS
                            )
                            volume_usd = quantity * Decimal(str(metrics.futures_price))
                            funding_pct = format_decimal(metrics.funding_rate * 100, 4)
                            basis_pct = format_decimal(metrics.basis, 4)
                            volume_fmt = format_decimal(volume_usd, 2)
                            asyncio.create_task(
                                notify_open(
                                    f"{name}:{symbol}",
                                    (
                                        f"Открыта {symbol} на {name}\n"
                                        f"Фандинг: {funding_pct}%\n"
                                        f"Базис: {basis_pct}%\n"
                                        f"Объём: ${volume_fmt}\n"
                                        f"Время в позиции: {format_duration(0)}\n"
                                        "Стратегия: Лонг спот / Шорт перп"
                                    ),
                                )
                            )
                            task = asyncio.create_task(
                                monitor_position(name, symbol, quantity)
                            )
                            POSITION_TASKS[f"{name}:{symbol}"] = task
                        except Exception as exc:
                            logger.error("Ошибка открытия %s %s: %s", name, symbol, exc)
            await asyncio.sleep(poll_interval)

    async def risk_loop() -> None:
        """Периодически проверяет, нужно ли приостановить торговлю."""
        while True:
            if await risk_control.is_paused():
                logger.warning("Торговля приостановлена из-за ограничений риска")
            await asyncio.sleep(poll_interval)

    async def optimisation_loop() -> None:
        """Запускает оптимизацию параметров в отдельной задаче."""
        await periodic_optimization(on_update=_update_thresholds)

    async def runner() -> None:
        """Создаёт и управляет основными асинхронными задачами."""
        tasks = [
            asyncio.create_task(scan_loop()),
            asyncio.create_task(risk_loop()),
            asyncio.create_task(optimisation_loop()),
        ]

        # Возобновляем мониторинг ранее открытых позиций
        for symbol, entry in strategy.positions.items():
            exchange_name = entry.get("exchange")
            quantity_raw = entry.get("quantity", 0.0)
            quantity = (
                quantity_raw
                if isinstance(quantity_raw, Decimal)
                else Decimal(str(quantity_raw))
            )
            if exchange_name in CLIENTS and quantity:
                task = asyncio.create_task(
                    monitor_position(exchange_name, symbol, quantity)
                )
                POSITION_TASKS[f"{exchange_name}:{symbol}"] = task
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            for t in POSITION_TASKS.values():
                t.cancel()
            await asyncio.gather(
                *tasks, *POSITION_TASKS.values(), return_exceptions=True
            )
            POSITION_TASKS.clear()

            # Закрываем подключенные биржи
            await asyncio.gather(
                *(client.close() for client in CLIENTS.values() if hasattr(client, "close")),
                return_exceptions=True,
            )

            await shutdown()

    await runner()


async def main() -> None:
    """Запускает загрузку конфигурации и основной цикл работы бота."""
    load_config()
    await initialize_bot()
    await start_processing_loops()


if __name__ == "__main__":  # pragma: no cover - script entry point
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
