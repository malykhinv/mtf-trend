"""Унифицированный интерфейс бирж для модулей стратегии.

Пакет предоставляет вспомогательные функции :func:`configure`, утилиты для
размещения ордеров и другие помощники, делегирующие работу выбранной
реализации биржи.

Пример
-------
>>> import exchanges
>>> exchanges.configure("binance", api_key="key", api_secret="secret")
>>> await exchanges.fetch_funding("BTCUSDT")
"""
from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
import logging
import time
from typing import Any, Coroutine, Dict, Optional, Set, Tuple, Type

from risk import risk_control

logger = logging.getLogger(__name__)

_OUTSTANDING: Set[Tuple[str, str]] = set()

# ---------------------------------------------------------------------------
# Базовый интерфейс
# ---------------------------------------------------------------------------


class BaseExchange(ABC):
    """Абстрактный базовый класс для реализаций бирж.

    Тестам нужен лишь небольшой поднабор возможностей, поэтому конкретные
    реализации в репозитории намеренно оставляют множество операций заглушками.
    Дополнительные методы управления ордерами здесь задают разумные значения
    по умолчанию, чтобы модульные тесты могли проверять высокоуровневую логику
    без реальных сетевых запросов.
    """

    name: str

    def __init__(self, **_ignored: Any) -> None:
        """Базовый инициализатор, принимающий произвольные параметры.

        Конкретные биржи могут определять собственные сигнатуры ``__init__``,
        однако фабрика :func:`configure` передаёт аргументы через ``**kwargs``.
        Пустая реализация предотвращает предупреждения анализаторов типо о
        "неожиданных аргументах" при создании экземпляра абстрактного класса.
        """
        super().__init__()
        self.name = getattr(
            self, "name", self.__class__.__name__.replace("Exchange", "").lower()
        )

    @abstractmethod
    async def fetch_funding(self, symbol: str) -> float:
        """Возвращает текущую ставку фондирования для ``symbol``."""

    @abstractmethod
    async def place_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Размещает ордер и возвращает ответ биржи."""

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Возвращает актуальный стакан для ``symbol``."""

    @abstractmethod
    async def get_balance(self) -> dict:
        """Возвращает информацию о балансе аккаунта."""

    # Интерфейсы спотовой торговли -------------------------------------------------

    @abstractmethod
    async def place_spot_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Размещает спотовый ордер и возвращает ответ биржи."""

    @abstractmethod
    async def get_spot_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Возвращает актуальный спотовый стакан для ``symbol``."""

    @abstractmethod
    async def get_spot_balance(self) -> dict:
        """Возвращает информацию о спотовом балансе."""

    @abstractmethod
    async def fetch_funding_history(
        self, symbol: str, hours: int = 8, limit: int = 3
    ) -> list[float]:
        """Возвращает историю ставок фондирования для ``symbol`` за указанные ``hours`` часов."""

    @abstractmethod
    async def get_stats(self, symbol: str) -> dict:
        """Возвращает рыночную статистику, такую как 24‑часовой объём и открытый интерес."""

    @abstractmethod
    async def get_futures_symbols(self) -> list[str]:
        """Возвращает список доступных фьючерсных символов."""

    @abstractmethod
    async def get_spot_symbols(self) -> list[str]:
        """Возвращает список доступных спотовых символов."""

    async def get_ohlc(
        self, symbol: str, interval: str, limit: int = 1
    ) -> list[Dict[str, float]]:
        """Возвращает данные OHLC для ``symbol``.

        Биржи должны переопределить метод, чтобы предоставить свежие данные свечей.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Дополнительные помощники управления ордерами
    # ------------------------------------------------------------------

    @staticmethod
    async def get_order_status(order_id: str) -> dict:
        """Возвращает статус ордера ``order_id``.

        Реализации бирж могут переопределить метод и выполнять реальные API‑запросы.
        По умолчанию считается, что ордер исполняется мгновенно, чего достаточно
        для модульных тестов.
        """

        return {"status": "FILLED", "order_id": order_id}

    @staticmethod
    async def cancel_order(order_id: str) -> dict:
        """Отменяет ``order_id`` на рынке ``market``.

        Реализация по умолчанию просто возвращает статус отменённого ордера.
        """

        return {"status": "CANCELED", "order_id": order_id}


# ---------------------------------------------------------------------------
# Фабрика бирж и унифицированные функции
# ---------------------------------------------------------------------------

_EXCHANGES: Dict[str, Type[BaseExchange]] = {}
current: Optional[BaseExchange] = None
API_TIMEOUT = 30


def register(name: str, cls: Type[BaseExchange]) -> None:
    """Регистрирует реализацию биржи."""
    _EXCHANGES[name.lower()] = cls


def configure(name: str, **kwargs) -> None:
    """Активирует биржу по её имени."""
    global current
    try:
        cls = _EXCHANGES[name.lower()]
    except KeyError as exc:  # pragma: no cover - defensive programming
        raise ValueError(f"Неизвестная биржа: {name}") from exc
    current = cls(**kwargs)


async def _handle_timeout() -> None:
    """Отменяет все ордера, закрывает открытые позиции и ставит торговлю на паузу."""

    logger.error(
        "Вызов API превысил %s секунд; отменяем активные ордера", API_TIMEOUT
    )
    if current is not None:
        for market, oid in list(_OUTSTANDING):
            try:
                await asyncio.wait_for(
                    current.cancel_order(oid), API_TIMEOUT
                )
            except Exception as exc:  # pragma: no cover - best effort
                logger.error("Не удалось отменить %s %s: %s", oid, exc)
        _OUTSTANDING.clear()

        # Пытаемся аварийно закрыть все отслеживаемые открытые позиции.
        try:  # pragma: no cover - best effort
            from main import CLIENTS, POSITION_TASKS
            from strategies import funding_arbitrage as strategy
            from utils.logger import log_trade
            from utils.telegram import notify_close, format_duration
            from datetime import datetime
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Не удалось подготовить аварийное закрытие: %s", exc)
        else:
            for pid, task in list(POSITION_TASKS.items()):
                del POSITION_TASKS[pid]
                exchange_name, symbol = pid.split(":", 1)
                client = CLIENTS.get(exchange_name)
                if client is None:
                    continue
                try:
                    entry = strategy.positions.get(symbol, {})
                    quantity = entry.get("quantity") or entry.get(
                        "initial_quantity", 0.0
                    )
                    try:
                        orders = await strategy.close_neutral_position(
                            client, symbol, quantity, final=True
                        )
                    except Exception as exc_close:
                        logger.error(
                            "Не удалось закрыть позицию %s на %s: %s",
                            symbol,
                            exchange_name,
                            exc_close,
                        )
                        hold_time = time.time() - entry.get("entry_timestamp", time.time())
                        funding_pct = entry.get("entry_funding", 0.0) * 100
                        basis_pct = entry.get("entry_basis", 0.0)
                        volume_usd = quantity * entry.get("entry_futures_price", 0.0)
                        asyncio.create_task(
                            notify_close(
                                pid,
                                (
                                    f"Аварийное закрытие не удалось {symbol} на {exchange_name}: {exc_close}\n"
                                    f"Фандинг: {funding_pct:.4f}%\n"
                                    f"Базис: {basis_pct:.4f}%\n"
                                    f"Объём: ${volume_usd:.2f}\n"
                                    f"Время в позиции: {format_duration(hold_time)}"
                                ),
                            )
                        )
                    else:
                        exit_spot = float(
                            orders["long"].get("avgPrice")
                            or orders["long"].get("price")
                            or 0.0
                        )
                        exit_perp = float(
                            orders["short"].get("avgPrice")
                            or orders["short"].get("price")
                            or 0.0
                        )
                        exit_ts = time.time()
                        pnl = (
                            (exit_perp - entry.get("entry_futures_price", 0.0))
                            - (exit_spot - entry.get("entry_spot_price", 0.0))
                        ) * quantity
                        volume_usd = quantity * entry.get("entry_futures_price", 0.0)
                        pnl_pct = (pnl / volume_usd * 100) if volume_usd else 0.0
                        exit_basis = (
                            ((exit_perp - exit_spot) / exit_spot) * 100
                            if exit_spot
                            else float("inf")
                        )
                        log_trade(
                            {
                                "symbol": symbol,
                                "exchange": exchange_name,
                                "entry_time": datetime.fromtimestamp(
                                    entry.get("entry_timestamp", exit_ts)
                                ).isoformat(),
                                "exit_time": datetime.fromtimestamp(exit_ts).isoformat(),
                                "entry_futures_price": entry.get("entry_futures_price"),
                                "exit_futures_price": exit_perp,
                                "entry_spot_price": entry.get("entry_spot_price"),
                                "exit_spot_price": exit_spot,
                                "entry_basis": entry.get("entry_basis"),
                                "exit_basis": exit_basis,
                                "basis_pct": exit_basis,
                                "funding": entry.get("entry_funding"),
                                "quantity": quantity,
                                "volume_usd": volume_usd,
                                "pnl": pnl,
                                "pnl_pct": pnl_pct,
                                "commissions": entry.get("commissions", 0.0),
                                "funding_accrued": entry.get("funding_accrued", 0.0),
                                "slippage": entry.get("slippage", 0.0),
                                "exit_reasons": ["timeout"],
                                "notes": "emergency_exit",
                            }
                        )
                        hold_time = exit_ts - entry.get("entry_timestamp", exit_ts)
                        funding_pct = entry.get("entry_funding", 0.0) * 100
                        asyncio.create_task(
                            notify_close(
                                pid,
                                (
                                    f"Аварийное закрытие {symbol} на {exchange_name}\n"
                                    f"Фандинг: {funding_pct:.4f}%\n"
                                    f"Базис: {exit_basis:.4f}%\n"
                                    f"Объём: ${volume_usd:.2f}\n"
                                    f"Время в позиции: {format_duration(hold_time)}\n"
                                    f"PnL: {pnl:.4f}"
                                ),
                            )
                        )
                finally:
                    pass
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                    strategy.positions.pop(symbol, None)

    risk_control.pause()


async def _await_with_timeout(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        return await asyncio.wait_for(coro, API_TIMEOUT)
    except asyncio.TimeoutError:
        await _handle_timeout()
        raise


def _track_order(market: str, order_id: str) -> None:
    _OUTSTANDING.add((market, order_id))


def _untrack_order(market: str, order_id: str) -> None:
    _OUTSTANDING.discard((market, order_id))


async def place_order(
    symbol: str, side: str, quantity: float, price: float | None = None
) -> dict:
    """Размещает ордер через настроенную биржу."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(
        current.place_order(symbol, side, quantity, price)
    )


async def _poll_fill(order_id: str, market: str) -> None:
    """Ожидает исполнения ордера ``order_id`` до статуса FILLED.

    Функция опирается на :meth:`BaseExchange.get_order_status` и делает короткие
    паузы между запросами. ``BaseExchange`` предоставляет заглушку, чтобы тесты,
    не взаимодействующие с живыми биржами, могли выполняться детерминированно.
    """

    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")

    start = time.monotonic()
    while True:
        status = await _await_with_timeout(
            current.get_order_status(order_id)
        )
        if status.get("status") == "FILLED":
            _untrack_order(market, order_id)
            return
        if time.monotonic() - start > API_TIMEOUT:
            await _handle_timeout()
            raise RuntimeError(f"Ордер {order_id} не исполнен вовремя")
        await asyncio.sleep(0.5)


async def place_spot_order(
    symbol: str, side: str, quantity: float, price: float | None = None
) -> dict:
    """Размещает спотовый ордер и ожидает его полного исполнения."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    order = await _await_with_timeout(
        current.place_spot_order(symbol, side, quantity, price)
    )
    order_id = str(order.get("orderId") or order.get("id") or "")
    _track_order("spot", order_id)
    await _poll_fill(order_id, "spot")
    return order


async def place_perp_order(
    symbol: str, side: str, quantity: float, price: float | None = None
) -> dict:
    """Размещает фьючерсный/перпетуальный ордер и ожидает его полного исполнения."""

    order = await place_order(symbol, side, quantity, price)
    order_id = str(order.get("orderId") or order.get("id") or "")
    _track_order("perp", order_id)
    await _poll_fill(order_id, "perp")
    return order


async def fetch_funding(symbol: str) -> float:
    """Получает ставку фондирования для ``symbol`` с настроенной биржи."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(current.fetch_funding(symbol))


async def get_orderbook(symbol: str, depth: int = 5) -> dict:
    """Возвращает актуальный стакан от настроенной биржи."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(current.get_orderbook(symbol, depth))


async def get_spot_orderbook(symbol: str, depth: int = 5) -> dict:
    """Возвращает актуальный спотовый стакан от настроенной биржи."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(current.get_spot_orderbook(symbol, depth))


async def get_balance() -> dict:
    """Возвращает баланс аккаунта с настроенной биржи."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(current.get_balance())


async def get_spot_balance() -> dict:
    """Возвращает спотовый баланс с настроенной биржи."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(current.get_spot_balance())


async def fetch_funding_history(
    symbol: str, hours: int = 8, limit: int = 3
) -> list[float]:
    """Возвращает историю ставок фондирования для ``symbol`` с биржи."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(
        current.fetch_funding_history(symbol, hours, limit)
    )


async def get_stats(symbol: str) -> dict:
    """Возвращает рыночную статистику, включая 24‑часовой объём и открытый интерес."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(current.get_stats(symbol))


async def get_ohlc(
    symbol: str, interval: str = "15m", limit: int = 1
) -> list[Dict[str, float]]:
    """Возвращает данные OHLC для ``symbol`` с биржи."""
    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")
    return await _await_with_timeout(current.get_ohlc(symbol, interval, limit))


async def hedge(symbol: str, quantity: float) -> Dict[str, Dict]:
    """Размещает компенсирующие спотовый и фьючерсный ордера с откатом при ошибке."""

    if current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Биржа не настроена")

    # Спот-часть -------------------------------------------------------------
    spot_order: Dict = await place_spot_order(symbol, "BUY", quantity)
    spot_id = str(spot_order.get("orderId") or spot_order.get("id") or "")

    # Фьючерсная часть ----------------------------------------------------------
    try:
        perp_order: Dict = await place_perp_order(symbol, "SELL", quantity)
        return {"spot": spot_order, "perp": perp_order}
    except Exception as exc:
        # Откатить спотовую часть, если фьючерсная часть завершается ошибкой.
        try:
            cancel_resp = await _await_with_timeout(
                current.cancel_order(spot_id)
            )
            logger.warning("Откатили спотовый ордер %s: %s", spot_id, cancel_resp)
        except Exception as cancel_exc:  # pragma: no cover - best effort
            logger.error(
                "Не удалось откатить спотовый ордер %s: %s", spot_id, cancel_exc
            )
        raise RuntimeError("Не удалось разместить хедж; спотовая часть откатена") from exc

# Импорт встроенных бирж для регистрации в фабрике.
from . import binance as _binance  # noqa: E402,F401
from . import bybit as _bybit  # noqa: E402,F401
