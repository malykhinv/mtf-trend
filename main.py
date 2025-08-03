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
from typing import Any, Dict, List
from decimal import Decimal

import yaml
from dotenv import load_dotenv

from exchanges import BaseExchange
from exchanges.binance import BinanceExchange
from exchanges.bybit import BybitExchange
from risk import risk_control
from strategies import funding_arbitrage as strategy
from ai.parameter_optimizer import periodic_optimization
from utils.telegram import format_duration, notify_close, notify_open

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
        "secret": os.getenv("BINANCE_API_SECRET"),
        "bybit": os.getenv("BYBIT_API_KEY"),
        "bybit_secret": os.getenv("BYBIT_API_SECRET"),
    }
    CONFIG["api_keys"] = {k: v for k, v in api_keys.items() if v}


def initialize_bot() -> None:
    """Настраивает клиентов бирж и управление рисками."""

    api_keys = CONFIG.get("api_keys", {})
    logger.info("Инициализация бота с API-ключами: %s", list(api_keys.keys()))

    # Создаём клиентов бирж, если заданы ключи
    binance_key = api_keys.get("binance")
    binance_secret = api_keys.get("secret") or api_keys.get("binance_secret")
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

    available = asyncio.run(_fetch_all()) if CLIENTS else {}
    for name, allowed in available.items():
        configured = WHITELISTS.get(name, [])
        filtered = [s for s in configured if s in allowed]
        removed = set(configured) - set(filtered)
        WHITELISTS[name] = filtered
        for sym in sorted(removed):
            logger.warning("Исключён символ %s из whitelist %s", sym, name)

    # Конфигурируем контроль рисков
    risk_control.configure(CONFIG.get("risk", {}), bot_cfg.get("deposit_size"))


def start_processing_loops() -> None:
    """Запускает циклы стратегии, рисков и оптимизации параметров."""

    poll_interval = CONFIG.get("bot", {}).get("poll_interval", 5)

    # Восстанавливаем ранее сохранённые позиции
    asyncio.run(strategy.load_positions())

    def _update_thresholds(new: Dict[str, float]) -> None:
        """Обновляет пороги стратегии новыми значениями."""
        if new:
            CONFIG.setdefault("thresholds", {}).update(new)

    async def monitor_position(
        exchange_name: str, symbol: str, quantity: float
    ) -> None:
        """Следит за открытой позицией до срабатывания условий выхода."""
        exchange = CLIENTS[exchange_name]
        position_id = f"{exchange_name}:{symbol}"
        try:
            await strategy.monitor_neutral_position(
                exchange,
                symbol,
                quantity,
                CONFIG.get("thresholds", {}),
                poll_interval,
                position_id=position_id,
            )
        except Exception as exc:
            # При ошибке закрываем позицию и уведомляем
            entry = strategy.positions.get(symbol, {})
            hold = time.time() - entry.get("entry_timestamp", time.time())
            funding_pct = entry.get("entry_funding", 0.0) * 100
            basis_pct = entry.get("entry_basis", 0.0)
            volume_usd = entry.get("entry_futures_price", 0.0) * entry.get(
                "initial_quantity", entry.get("quantity", 0.0)
            )
            asyncio.create_task(
                notify_close(
                    position_id,
                    (
                        f"Ошибка на {exchange_name} {symbol}: {exc}\n"
                        f"Фандинг: {funding_pct:.4f}%\n"
                        f"Базис: {basis_pct:.4f}%\n"
                        f"Объём: ${volume_usd:.2f}\n"
                        f"Время в позиции: {format_duration(hold)}"
                    ),
                )
            )
            raise
        else:
            # Успешное завершение позиции
            entry = strategy.positions.get(symbol, {})
            pnl = entry.get("pnl", 0.0)
            reasons = entry.get("exit_reasons")
            hold = entry.get("exit_timestamp", 0) - entry.get("entry_timestamp", 0)
            funding_pct = (
                entry.get("exit_funding", entry.get("entry_funding", 0.0)) * 100
            )
            basis_pct = entry.get("exit_basis", 0.0)
            volume_usd = entry.get("entry_futures_price", 0.0) * entry.get(
                "initial_quantity", entry.get("quantity", 0.0)
            )
            pnl_pct = (pnl / volume_usd * 100) if volume_usd else 0.0
            asyncio.create_task(
                notify_close(
                    position_id,
                    (
                        f"Закрыта {symbol} на {exchange_name}\n"
                        f"Фандинг: {funding_pct:.4f}%\n"
                        f"Базис: {basis_pct:.4f}%\n"
                        f"Объём: ${volume_usd:.2f}\n"
                        f"Время в позиции: {format_duration(hold)}\n"
                        f"PnL: {pnl:.4f} ({pnl_pct:.4f}%) Причины: {reasons}"
                    ),
                )
            )
        finally:
            # Снимаем нагрузку по рискам и удаляем задачу из списка активных
            entry = strategy.positions.get(symbol, {})
            notional = (
                Decimal(str(entry.get("entry_futures_price", 0.0)))
                * Decimal(
                    str(entry.get("initial_quantity", entry.get("quantity", 0.0)))
                )
            )
            await risk_control.update_position(-notional)
            await risk_control.mark_symbol_closed(symbol)
            # Удаляем задачу из списка активных
            if position_id in POSITION_TASKS:
                del POSITION_TASKS[position_id]
            if strategy.positions.pop(symbol, None) is not None:
                await strategy.save_positions()

    async def scan_loop() -> None:
        """Постоянно сканирует рынок в поиске входов."""
        while True:
            thresholds = CONFIG.get("thresholds", {})
            deposit = CONFIG.get("bot", {}).get("deposit_size", float("inf"))
            deposit_pct_raw = thresholds.get("deposit_pct", 0.05)
            deposit_pct = max(0.0, min(1.0, deposit_pct_raw))
            min_trade_usd = thresholds.get("min_trade_size", float("inf"))
            if deposit_pct_raw != deposit_pct or min_trade_usd <= 0:
                logger.warning(
                    "Некорректные параметры торговли: deposit_pct=%s, min_trade_size=%s",
                    deposit_pct_raw,
                    min_trade_usd,
                )
                await asyncio.sleep(poll_interval)
                continue
            # Торгуем не меньше минимального порога: выбираем большую величину
            trade_value = max(min_trade_usd, deposit * deposit_pct)
            if trade_value in (0.0, float("inf")):
                logger.error(
                    "Некорректное значение trade_value: %s", trade_value
                )
                await asyncio.sleep(poll_interval)
                continue

            for name, client in CLIENTS.items():
                # Перебираем символы из белого списка
                for symbol in WHITELISTS.get(name, []):
                    if await risk_control.is_paused() or await risk_control.is_symbol_open(symbol):
                        continue
                    try:
                        base_metrics = await strategy.get_market_metrics(symbol, 1.0, client)
                        if base_metrics is None:
                            logger.warning(
                                "Пропуск %s:%s из-за неполных метрик", name, symbol
                            )
                            continue
                    except Exception as exc:
                        logger.error("Ошибка метрик %s %s: %s", name, symbol, exc)
                        continue
                    price = base_metrics.futures_price
                    quantity = trade_value / price if price else 0.0
                    try:
                        metrics = await strategy.get_market_metrics(symbol, quantity, client)
                        if metrics is None:
                            logger.warning(
                                "Пропуск %s:%s из-за неполных метрик", name, symbol
                            )
                            continue
                    except Exception as exc:
                        logger.error("Ошибка метрик %s %s: %s", name, symbol, exc)
                        continue

                    if await strategy.check_entry_conditions(
                        symbol, quantity, metrics, thresholds
                    ):
                        # Условия входа выполнены – открываем позицию
                        try:
                            await strategy.open_neutral_position(client, symbol, quantity)
                            volume_usd = quantity * metrics.futures_price
                            asyncio.create_task(
                                notify_open(
                                    f"{name}:{symbol}",
                                    (
                                        f"Открыта {symbol} на {name}\n"
                                        f"Фандинг: {metrics.funding_rate * 100:.4f}%\n"
                                        f"Базис: {metrics.basis:.4f}%\n"
                                        f"Объём: ${volume_usd:.2f}\n"
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
            quantity = entry.get("quantity", 0.0)
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

    asyncio.run(runner())


def main() -> None:
    """Запускает загрузку конфигурации и основной цикл работы бота."""
    load_config()
    initialize_bot()
    start_processing_loops()


if __name__ == "__main__":  # pragma: no cover - script entry point
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
