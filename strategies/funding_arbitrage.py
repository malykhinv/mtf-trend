"""Утилиты для стратегии арбитража по ставке фондирования.

Модуль содержит функции для оценки условий входа и выхода на основе ставок
фондирования и простых микроструктурных метрик. Также включает вспомогательные
функции для открытия и мониторинга нейтральных позиций.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from decimal import Decimal, getcontext
import os
from tempfile import NamedTemporaryFile

getcontext().prec = 10

from exchanges import BaseExchange, API_TIMEOUT
from risk import risk_control
from ai.parameter_optimizer import load_thresholds as _load_thresholds
from main import CONFIG, WHITELISTS
from utils.logger import log_trade
from utils.telegram import format_duration, notify_partial_close


DEFAULT_POSITIONS_FILE = "open_positions.json"


logger = logging.getLogger(__name__)


async def fetch_funding_history(symbol: str, hours: int = 8, limit: int = 3) -> list[float]:
    """Заглушка для истории фондирования."""
    raise NotImplementedError


async def get_orderbook(symbol: str, depth: int = 5) -> dict:
    """Заглушка для получения стакана."""
    raise NotImplementedError


async def get_spot_orderbook(symbol: str, depth: int = 5) -> dict:
    """Заглушка для получения спотового стакана."""
    raise NotImplementedError


async def get_stats(symbol: str) -> dict:
    """Заглушка для рыночной статистики."""
    raise NotImplementedError


async def get_ohlc(symbol: str, interval: str = "15m", limit: int = 1) -> list[Dict[str, float]]:
    """Заглушка для данных OHLC."""
    raise NotImplementedError


async def _wait_filled(exchange: BaseExchange, order_id: str) -> bool:
    """Ожидает исполнения ордера и возвращает ``True`` при полном исполнении.

    Если ордер получает статус ``PARTIALLY_FILLED``, функция возвращает ``False``.
    При превышении ``API_TIMEOUT`` возбуждается ``RuntimeError``.
    """

    start = time.monotonic()
    while True:
        status = await exchange.get_order_status(order_id)
        state = (
            status.get("status")
            or status.get("orderStatus")
            or status.get("result", {}).get("status")
            or status.get("result", {}).get("orderStatus")
        )
        normalized = str(state or "").replace(" ", "_").upper()
        if normalized == "FILLED":
            return True
        if normalized in {"PARTIALLY_FILLED", "PARTIALLYFILLED"}:
            return False
        if time.monotonic() - start > API_TIMEOUT:
            raise RuntimeError(f"Ордер {order_id} не исполнен вовремя")
        await asyncio.sleep(0.5)


@dataclass
class MarketMetrics:
    """Набор рыночных метрик, используемых стратегией."""

    funding_rate: Decimal
    spread: Decimal
    liquidity: Decimal
    volatility: float  # 15-minute price change percentage
    spot_price: Decimal
    futures_price: Decimal
    volume: Decimal
    open_interest: Decimal
    spot_slippage: float
    futures_slippage: float
    slippage: float  # combined spot–futures slippage
    basis: float  # spot–futures basis percentage


def calculate_basis(
    futures_price: float, spot_price: float, signed: bool = False
) -> float:
    """Расчёт процентного базиса между фьючерсом и спотом.

    Parameters
    ----------
    futures_price : float
        Текущая цена фьючерса.
    spot_price : float
        Текущая цена спота.
    signed : bool, optional
        Если ``True``, знак сохраняется. Иначе возвращается абсолютное значение.
    """

    if not spot_price:
        return float("inf")
    value = (futures_price - spot_price) / spot_price * 100
    return value if signed else abs(value)


async def get_market_metrics(
    symbol: str,
    trade_size: float,
    exchange: BaseExchange | None = None,
    depth: int = 5,
) -> MarketMetrics | None:
    """Получает расширенные рыночные метрики для ``symbol``.

    ``exchange`` может быть ``None`` – в этом случае используются
    модульные заглушки, что упрощает тестирование.
    """

    if exchange is not None:
        history = await exchange.fetch_funding_history(symbol, hours=8, limit=3)
    else:
        history = await fetch_funding_history(symbol, hours=8, limit=3)
    if history:
        k = Decimal(2) / Decimal(len(history) + 1)
        funding = Decimal(str(history[0]))
        for rate in history[1:]:
            rate_d = Decimal(str(rate))
            funding = rate_d * k + funding * (Decimal(1) - k)
    else:
        funding = Decimal(0)

    # Загружаем стаканы фьючерса и спота
    if exchange is not None:
        perp_book = await exchange.get_orderbook(symbol, depth=depth)
        spot_book = await exchange.get_spot_orderbook(symbol, depth=depth)
    else:
        perp_book = await get_orderbook(symbol, depth=depth)
        spot_book = await get_spot_orderbook(symbol, depth=depth)

    bids = [(float(p), float(q)) for p, q in perp_book.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in perp_book.get("asks", [])]
    spot_bids = [(float(p), float(q)) for p, q in spot_book.get("bids", [])]
    spot_asks = [(float(p), float(q)) for p, q in spot_book.get("asks", [])]

    # If any order book side is empty, market data is incomplete
    if not (bids and asks and spot_bids and spot_asks):
        return None

    def _calc_slippage(orders, size, mid):
        """Оценивает проскальзывание при выполнении ``size`` по стакану."""
        if size <= 0 or math.isnan(size):
            return float("inf")
        if mid <= 0 or not math.isfinite(mid):
            return float("inf")
        remaining = size
        cost = 0.0
        for price, qty in orders:
            # Берём доступный объём из каждой заявки
            take = min(remaining, qty)
            cost += take * price
            remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            return float("inf")
        avg_price = cost / size
        return abs(avg_price - mid) / mid

    futures_price = Decimal(str((asks[0][0] + bids[0][0]) / 2))
    futures_slippage = _calc_slippage(asks, trade_size, float(futures_price))

    spot_price = Decimal(str((spot_asks[0][0] + spot_bids[0][0]) / 2))
    spot_slippage = _calc_slippage(spot_asks, trade_size, float(spot_price))

    # Суммарное проскальзывание обеих ног
    slippage = futures_slippage + spot_slippage

    spread = abs(futures_price - spot_price)

    if exchange is not None:
        stats = await exchange.get_stats(symbol)
    else:
        stats = await get_stats(symbol)
    if not stats:
        return None

    volume = Decimal(str(stats.get("volume_24h", 0.0)))
    open_interest = Decimal(str(stats.get("open_interest", 0.0)))
    if futures_price and math.isfinite(float(futures_price)):
        open_interest *= futures_price
    else:
        open_interest = Decimal(0)
    liquidity = sum(Decimal(str(q)) for _, q in bids) + sum(
        Decimal(str(q)) for _, q in asks
    )
    basis = calculate_basis(float(futures_price), float(spot_price))

    # Изменение цены за последние 15 минут для оценки волатильности
    if exchange is not None:
        ohlc = await exchange.get_ohlc(symbol, interval="15m", limit=1)
    else:
        ohlc = await get_ohlc(symbol, interval="15m", limit=1)
    if not ohlc:
        # Without recent OHLC data we cannot estimate volatility.
        # Returning ``None`` signals to the caller that metrics are incomplete
        # so the strategy can skip trading for this symbol.
        return None

    candle = ohlc[0]
    o = float(candle.get("open", float("nan")))
    c = float(candle.get("close", float("nan")))
    if o == 0.0 or not math.isfinite(o) or not math.isfinite(c):
        # Treat zero/invalid open or close price as infinite volatility so that
        # the strategy can trigger protective actions.
        volatility = float("inf")
    else:
        volatility = (c - o) / o

    return MarketMetrics(
        funding,
        spread,
        liquidity,
        volatility,
        spot_price,
        futures_price,
        volume,
        open_interest,
        spot_slippage,
        futures_slippage,
        slippage,
        basis,
    )


# ---------------------------------------------------------------------------
# Проверка условий
# ---------------------------------------------------------------------------

def check_entry_conditions(
    symbol: str,
    quantity: Decimal,
    metrics: MarketMetrics | None,
    thresholds: Dict[str, float],
) -> bool | asyncio.Future:
    """Возвращает ``True`` при выполнении всех условий входа.

    Порог ``basis`` для входа задаётся в процентных пунктах. Функцию можно
    вызывать синхронно или с ``await``.
    """

    async def _inner() -> bool:
        if metrics is None:
            logger.warning("Skipping %s: incomplete market metrics", symbol)
            return False
        quantity_d = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
        if not quantity_d.is_finite() or quantity_d <= 0:
            logger.warning("Skipping %s: invalid quantity %s", symbol, quantity_d)
            return False

        metrics_map = {
            "funding_rate": metrics.funding_rate,
            "spread": metrics.spread,
            "liquidity": metrics.liquidity,
            "volatility": metrics.volatility,
            "spot_price": metrics.spot_price,
            "futures_price": metrics.futures_price,
            "volume": metrics.volume,
            "open_interest": metrics.open_interest,
            "spot_slippage": metrics.spot_slippage,
            "futures_slippage": metrics.futures_slippage,
            "slippage": metrics.slippage,
            "basis": metrics.basis,
        }
        for name, value in metrics_map.items():
            finite = value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)
            if not finite:
                logger.warning("Skipping %s: non-finite %s=%s", symbol, name, value)
                return False

        def _threshold(name: str, default: float) -> float:
            val = thresholds.get(name, default)
            if val is None or not math.isfinite(val):
                logger.warning(
                    "Invalid threshold %s=%s; using default %s", name, val, default
                )
                return default
            return val

        deposit = Decimal(str(CONFIG.get("bot", {}).get("deposit_size", "Infinity")))
        deposit_pct = _threshold("deposit_pct", 1.0)
        spread_limit = _threshold("spread", float("inf"))
        basis_limit = _threshold("basis", float("inf"))
        liquidity_limit = _threshold("liquidity", 0.0)
        volume_limit = _threshold("volume", 0.0)
        volatility_limit = _threshold("volatility", float("inf"))
        slippage_limit = _threshold("slippage", 0.003)
        funding_rate_limit = _threshold("funding_rate", 0.0)
        max_trade_size_limit = _threshold("max_trade_size", float("inf"))

        max_deposit_trade = deposit * Decimal(str(deposit_pct))
        spread_pct = (
            metrics.spread / metrics.futures_price
            if metrics.futures_price
            else Decimal("Infinity")
        )
        notional = quantity_d * metrics.futures_price
        max_basis_pct = Decimal(str(basis_limit))
        basis_abs = abs(Decimal(str(metrics.basis)))
        combined_slippage = Decimal(str(metrics.slippage))
        slippage_limit_d = Decimal(str(slippage_limit))

        return (
            metrics.funding_rate > Decimal(0)
            and metrics.funding_rate >= Decimal(str(funding_rate_limit))
            and spread_pct <= Decimal(str(spread_limit))
            and basis_abs <= max_basis_pct
            and metrics.liquidity >= Decimal(str(liquidity_limit))
            and metrics.volume >= Decimal(str(volume_limit))
            and abs(Decimal(str(metrics.volatility))) <= Decimal(str(volatility_limit))
            and metrics.open_interest <= metrics.volume * Decimal(2)
            and combined_slippage <= slippage_limit_d
            and notional <= Decimal(str(max_trade_size_limit))
            and notional <= max_deposit_trade
            and not await risk_control.is_symbol_open(symbol)
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_inner())
    return loop.create_task(_inner())


def check_exit_conditions(metrics: MarketMetrics, thresholds: Dict[str, float]) -> bool:
    """Возвращает ``True``, если выполнено любое условие выхода.

    Включает проверку порога ``exit_basis`` (в процентных пунктах).
    """
    if not math.isfinite(metrics.volatility):
        return True
    return (
        abs(metrics.funding_rate) <= thresholds.get("funding_rate", float("inf"))
        or metrics.spread >= thresholds.get("spread", float("inf"))
        or metrics.liquidity <= thresholds.get("liquidity", float("inf"))
        or abs(metrics.volatility)
        >= thresholds.get("volatility", float("inf"))
        or abs(metrics.basis) >= thresholds.get("exit_basis", float("inf"))
    )


# ---------------------------------------------------------------------------
# Управление позициями
# ---------------------------------------------------------------------------


@dataclass
class Position:
    """Модель открытой позиции."""

    entry_timestamp: float
    entry_futures_price: float
    entry_spot_price: float
    entry_basis: float
    entry_funding: float
    quantity: float
    initial_quantity: float
    pnl: Decimal = Decimal(0)
    funding_accrued: Decimal = Decimal(0)
    commissions: Decimal = Decimal(0)
    last_funding_timestamp: float = 0.0
    exchange: str = ""
    exit_timestamp: float | None = None
    exit_reasons: list[str] = field(default_factory=list)
    exit_futures_price: float | None = None
    exit_spot_price: float | None = None
    exit_basis: float | None = None
    exit_funding: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["pnl"] = float(self.pnl)
        data["funding_accrued"] = float(self.funding_accrued)
        data["commissions"] = float(self.commissions)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        if "quantity" not in data:
            raise ValueError("missing field quantity")
        return cls(
            entry_timestamp=float(data.get("entry_timestamp", 0.0)),
            entry_futures_price=float(data.get("entry_futures_price", 0.0)),
            entry_spot_price=float(data.get("entry_spot_price", 0.0)),
            entry_basis=float(data.get("entry_basis", 0.0)),
            entry_funding=float(data.get("entry_funding", 0.0)),
            quantity=float(data["quantity"]),
            initial_quantity=float(data.get("initial_quantity", data["quantity"])),
            pnl=Decimal(str(data.get("pnl", 0.0))),
            funding_accrued=Decimal(str(data.get("funding_accrued", 0.0))),
            commissions=Decimal(str(data.get("commissions", 0.0))),
            last_funding_timestamp=float(
                data.get("last_funding_timestamp", data.get("entry_timestamp", 0.0))
            ),
            exchange=str(data.get("exchange", "")),
            exit_timestamp=data.get("exit_timestamp"),
            exit_reasons=list(data.get("exit_reasons", [])),
            exit_futures_price=data.get("exit_futures_price"),
            exit_spot_price=data.get("exit_spot_price"),
            exit_basis=data.get("exit_basis"),
            exit_funding=data.get("exit_funding"),
        )


positions: Dict[str, Position] = {}

# Global lock to serialize access to positions file across async tasks
_positions_lock = asyncio.Lock()

# Lock to protect in-memory position modifications
positions_lock = asyncio.Lock()


def _get_positions_path(path: Path | str | None = None) -> Path:
    """Возвращает путь к файлу с позициями из env, config или ``DEFAULT_POSITIONS_FILE``."""

    if path is not None:
        return Path(path)
    env_path = os.getenv("POSITIONS_FILE_PATH")
    if env_path:
        return Path(env_path)
    bot_cfg = CONFIG.get("bot", {})
    cfg_path = bot_cfg.get("positions_file")
    return Path(cfg_path or DEFAULT_POSITIONS_FILE)


async def load_positions(path: Path | str | None = None) -> None:
    """Загружает ранее сохранённые позиции из ``path``."""

    path = _get_positions_path(path)
    try:
        async with _positions_lock:
            with Path(path).open("r", encoding="utf-8") as fh:
                data = json.load(fh)
    except FileNotFoundError:
        return
    except json.JSONDecodeError:
        logger.warning("Некорректный файл позиций %s, начинаем с пустого", path)
        data = {}
    positions.clear()
    for symbol, entry in data.items():
        try:
            pos = Position.from_dict(entry)
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid position for %s: %s", symbol, exc)
            continue
        positions[symbol] = pos
        notional = pos.quantity * pos.entry_futures_price
        if notional:
            await risk_control.update_position(Decimal(str(notional)))
        await risk_control.mark_symbol_open(symbol)


async def save_positions(path: Path | str | None = None) -> None:
    """Сохраняет текущие открытые позиции в ``path``."""

    path = _get_positions_path(path)
    async with _positions_lock:
        data = {s: p.to_dict() for s, p in positions.items()}
        tmp_name: str | None = None
        try:
            dir_path = Path(path).resolve().parent
            dir_path.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w", dir=dir_path, delete=False, encoding="utf-8"
            ) as fh:
                json.dump(data, fh)
                tmp_name = fh.name
            os.replace(tmp_name, path)
        except Exception as exc:  # pragma: no cover - log errors
            logger.error("Ошибка сохранения позиций %s: %s", path, exc)
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass



async def open_neutral_position(
    exchange: BaseExchange, symbol: str, quantity: Decimal
) -> Dict[str, Dict]:
    """Открывает компенсирующие длинную и короткую позиции и сохраняет данные."""
    bot_cfg = CONFIG.get("bot", {})
    exchange_name = exchange.name
    if symbol not in WHITELISTS.get(exchange_name, []):
        raise RuntimeError("Символ отсутствует в белом списке")
    # Получаем метрики рынка для оценки сделки
    entry_metrics = await get_market_metrics(symbol, float(quantity), exchange)
    if entry_metrics is None:
        raise RuntimeError("Рыночные метрики недоступны, сделка пропущена")
    notional = quantity * Decimal(str(entry_metrics.futures_price))
    deposit_raw = bot_cfg.get("deposit_size", "Infinity")
    deposit = Decimal(str(deposit_raw))
    if not deposit.is_finite() or deposit < 0:
        logger.error("Некорректное значение депозита: %s", deposit_raw)
        raise RuntimeError("Некорректное значение депозита")
    deposit_pct = Decimal(str(CONFIG.get("thresholds", {}).get("deposit_pct", 1.0)))
    if deposit_pct < 0:
        logger.warning("Некорректное значение deposit_pct=%s; устанавливаем 0", deposit_pct)
        deposit_pct = Decimal("0")
    elif deposit_pct > 1:
        logger.warning("Некорректное значение deposit_pct=%s; устанавливаем 1", deposit_pct)
        deposit_pct = Decimal("1")
    max_trade = deposit * deposit_pct
    if notional > deposit or notional > max_trade:
        raise RuntimeError("Размер сделки превышает лимиты депозита")
    if not await risk_control.try_open_position(symbol, notional):
        raise RuntimeError(
            "Превышены лимиты риска, торговля приостановлена или позиция уже открыта"
        )
    updated = False
    error: Exception | None = None
    try:
        # Хеджируем позицию на споте и фьючерсе
        spot_order = await exchange.place_spot_order(symbol, "BUY", float(quantity))
        spot_id = str(spot_order.get("orderId") or spot_order.get("id") or "")
        if not await _wait_filled(exchange, spot_id):
            await exchange.cancel_order(symbol, spot_id)
            raise RuntimeError("Спотовый ордер выполнен частично")
        try:
            perp_order = await exchange.place_order(symbol, "SELL", float(quantity))
        except Exception as exc:
            # При ошибке размещения второй ноги отменяем первую
            await exchange.cancel_order(symbol, spot_id)
            raise RuntimeError(
                "Не удалось разместить хедж; спотовый ордер отменён"
            ) from exc
        perp_id = str(perp_order.get("orderId") or perp_order.get("id") or "")
        if not await _wait_filled(exchange, perp_id):
            await exchange.cancel_order(symbol, perp_id)
            # Откатываем спотовую позицию
            try:
                rollback_order = await exchange.place_spot_order(
                    symbol, "SELL", float(quantity)
                )
                rollback_id = str(
                    rollback_order.get("orderId")
                    or rollback_order.get("id")
                    or ""
                )
                if not await _wait_filled(exchange, rollback_id):
                    logger.error(
                        "Откат спотовой позиции %s исполнен частично", rollback_id
                    )
                    raise RuntimeError(
                        "Не удалось полностью откатить спотовую позицию"
                    )
            except Exception as exc:
                logger.error("Ошибка отката спотовой позиции: %s", exc)
                raise RuntimeError(
                    "Не удалось откатить спотовую позицию"
                ) from exc
            raise RuntimeError(
                "Не удалось полностью захеджировать позицию; спот откатан"
            )
        orders = {"spot": spot_order, "perp": perp_order}
        commission = sum(Decimal(str(o.get("fee", 0.0))) for o in orders.values())
        now = time.time()
        async with positions_lock:
            await risk_control.update_position(notional)
            updated = True
            positions[symbol] = Position(
                entry_timestamp=now,
                entry_futures_price=entry_metrics.futures_price,
                entry_spot_price=entry_metrics.spot_price,
                entry_basis=entry_metrics.basis,
                entry_funding=float(entry_metrics.funding_rate),
                quantity=float(quantity),
                initial_quantity=float(quantity),
                pnl=Decimal(0),
                funding_accrued=Decimal(0),
                commissions=commission,
                last_funding_timestamp=now,
                exchange=exchange_name,
            )
            await save_positions()
        volume_usd = float(notional)
        await log_trade(
            {
                "symbol": symbol,
                "exchange": exchange_name,
                "entry_time": datetime.fromtimestamp(now).isoformat(),
                "exit_time": None,
                "entry_futures_price": entry_metrics.futures_price,
                "exit_futures_price": None,
                "entry_spot_price": entry_metrics.spot_price,
                "exit_spot_price": None,
                "entry_basis": entry_metrics.basis,
                "exit_basis": None,
                "basis_pct": entry_metrics.basis,
                "funding": entry_metrics.funding_rate,
                "quantity": float(quantity),
                "volume_usd": volume_usd,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "commissions": float(commission),
                "funding_accrued": 0.0,
                "slippage": entry_metrics.slippage,
                "exit_reasons": None,
                "notes": "open",
            }
        )
        return orders
    except Exception as exc:
        error = exc
        await risk_control.mark_symbol_closed(symbol)
        raise
    finally:
        if updated and error is not None:
            try:
                await risk_control.update_position(-notional)
            except Exception:  # pragma: no cover - best effort cleanup
                pass


async def close_neutral_position(
    exchange: BaseExchange,
    symbol: str,
    quantity: Decimal,
    pnl: Decimal = Decimal(0),
    final: bool = True,
) -> Dict[str, Dict]:
    """Закрывает нейтральную позицию и фиксирует результат.

    Параметры
    ---------
    symbol:
        Торговая пара.
    quantity:
        Объём закрытия.
    pnl:
        Полученная прибыль или убыток.
    final:
        Если ``True``, позиция закрывается полностью и риски сбрасываются.
    """
    quantity = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
    pnl = Decimal(str(pnl))
    close_long = await exchange.place_order(symbol, "SELL", float(quantity))
    long_id = str(close_long.get("orderId") or close_long.get("id") or "")
    if not await _wait_filled(exchange, long_id):
        await exchange.cancel_order(symbol, long_id)
        raise RuntimeError("Не удалось закрыть длинную ногу")

    try:
        close_short = await exchange.place_order(symbol, "BUY", float(quantity))
        short_id = str(close_short.get("orderId") or close_short.get("id") or "")
        short_fee = Decimal(str(close_short.get("fee", 0.0)))
        if not await _wait_filled(exchange, short_id):
            status = await exchange.get_order_status(short_id)
            filled = Decimal(str(status.get("executedQty") or status.get("filled") or 0))
            await exchange.cancel_order(symbol, short_id)
            remaining = quantity - filled
            if remaining > 0:
                retry = await exchange.place_order(symbol, "BUY", float(remaining))
                retry_id = str(retry.get("orderId") or retry.get("id") or "")
                if not await _wait_filled(exchange, retry_id):
                    await exchange.cancel_order(symbol, retry_id)
                    rollback = await exchange.place_order(symbol, "BUY", float(quantity))
                    rollback_id = str(rollback.get("orderId") or rollback.get("id") or "")
                    if not await _wait_filled(exchange, rollback_id):
                        logger.error("Сбой отката для %s", symbol)
                    raise RuntimeError("Не удалось закрыть хедж; длинная нога откатана")
                short_fee += Decimal(str(retry.get("fee", 0.0)))
                close_short = retry
            else:
                rollback = await exchange.place_order(symbol, "BUY", float(quantity))
                rollback_id = str(rollback.get("orderId") or rollback.get("id") or "")
                if not await _wait_filled(exchange, rollback_id):
                    logger.error("Сбой отката для %s", symbol)
                raise RuntimeError("Не удалось закрыть хедж; длинная нога откатана")
    except Exception as exc:
        # В случае ошибки возвращаем длинную позицию
        rollback = await exchange.place_order(symbol, "BUY", float(quantity))
        rollback_id = str(rollback.get("orderId") or rollback.get("id") or "")
        try:
            if not await _wait_filled(exchange, rollback_id):
                logger.error("Сбой отката для %s", symbol)
        except Exception:
            logger.error("Сбой отката для %s", symbol)
        raise RuntimeError("Не удалось закрыть хедж; длинная нога откатана") from exc
    commission = Decimal(str(close_long.get("fee", 0.0))) + short_fee
    async with positions_lock:
        entry = positions.get(symbol)
        if entry is not None:
            entry.commissions += commission
            ref_price = Decimal(str(entry.entry_futures_price))
        else:
            ref_price = Decimal(0)
        await risk_control.update_position(-(quantity * ref_price))
        net_pnl = pnl - commission
        await risk_control.record_pnl(net_pnl)
        if final:
            await risk_control.mark_symbol_closed(symbol)
            positions.pop(symbol, None)
            await save_positions()
    return {"long": close_long, "short": close_short, "commission": float(commission)}


async def monitor_neutral_position(
    exchange: BaseExchange,
    symbol: str,
    quantity: float,
    exit_thresholds: Dict[str, float],
    poll_interval: float = 5.0,
    position_id: str | None = None,
) -> Position | None:
    """Следит за позицией и закрывает её при срабатывании условий выхода.

    В качестве одного из критериев используется ``exit_basis`` – предельное
    значение базиса между спотом и фьючерсом. Поддерживается частичное
    закрытие: если размер позиции больше двух минимальных, закрывается
    половина и отслеживание продолжается. Обновления отправляются через
    :func:`notify_partial_close`, чтобы редактировать одно сообщение вместо
    отправки новых.
    """
    entry = positions.get(symbol)
    while True:
        try:
            metrics = await get_market_metrics(symbol, quantity, exchange)
            if metrics is None:
                logger.warning("Неполные рыночные данные для %s", symbol)
                await asyncio.sleep(poll_interval)
                continue
        except Exception as exc:
            logger.error("Ошибка мониторинга %s: %s", symbol, exc)
            await asyncio.sleep(poll_interval)
            continue
        now = time.time()
        if entry:
            last = entry.last_funding_timestamp or now
            quantity_d = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
            price_d = metrics.futures_price
            elapsed = Decimal(str(now - last))
            funding_fee = (
                quantity_d
                * price_d
                * metrics.funding_rate
                * elapsed
                / Decimal(8 * 3600)
            )
            # Накапливаем полученное фондирование
            entry.funding_accrued += funding_fee
            entry.last_funding_timestamp = now
        reasons: list[str] = []
        exit_slippage = 0.0
        if check_exit_conditions(metrics, exit_thresholds):
            reasons.append("threshold")
        if metrics.funding_rate < Decimal("0.0001"):
            reasons.append("low_funding")
        if entry and metrics.funding_rate < 0 <= entry.entry_funding:
            reasons.append("funding_negative")
        if abs(metrics.basis) >= exit_thresholds.get("exit_basis", float("inf")):
            reasons.append("basis")
        if entry:
            entry_price_d = Decimal(str(entry.entry_futures_price))
            exit_slippage = abs(
                metrics.futures_price - entry_price_d
            ) / max(entry_price_d, Decimal("1e-9"))
            slippage_limit = exit_thresholds.get(
                "exit_slippage", exit_thresholds.get("slippage")
            )
            if slippage_limit is not None and exit_slippage > slippage_limit:
                reasons.append("slippage")
            hold_time = now - entry.entry_timestamp
            max_hold = exit_thresholds.get("holding_time")
            if max_hold is not None and hold_time > max_hold:
                reasons.append("time")
            fut_diff = metrics.futures_price - Decimal(str(entry.entry_futures_price))
            spot_diff = metrics.spot_price - Decimal(str(entry.entry_spot_price))
            pnl = (fut_diff - spot_diff) * quantity_d
            if (
                pnl + entry.funding_accrued - entry.commissions < Decimal(0)
            ):
                reasons.append("pnl_vs_cost")
        else:
            pnl = Decimal(0)
        if reasons:
            min_trade_usd = exit_thresholds.get("min_trade_size", 0.0)
            min_trade_qty = (
                min_trade_usd / float(metrics.futures_price)
                if metrics.futures_price
                else 0.0
            )
            if entry and quantity > max(min_trade_qty * 2, 0.0):
                partial_qty = quantity / 2
                partial_qty_d = Decimal(str(partial_qty))
                pnl_part = (fut_diff - spot_diff) * partial_qty_d
                orders = await close_neutral_position(
                    exchange, symbol, partial_qty_d, pnl_part, final=False
                )
                # Обновляем запись о позиции после частичного выхода
                async with positions_lock:
                    quantity -= partial_qty
                    entry.quantity = quantity
                    entry.pnl += pnl_part
                    await save_positions()
                exit_ts = time.time()
                exit_basis = calculate_basis(
                    float(metrics.futures_price),
                    float(metrics.spot_price),
                    signed=True,
                )
                commission = Decimal(str(orders.get("commission", 0.0)))
                pnl_net = pnl_part - commission
                volume_usd = partial_qty_d * Decimal(str(entry.entry_futures_price))
                pnl_pct = (
                    pnl_net / volume_usd * 100 if volume_usd != 0 else Decimal(0)
                )
                hold_time = exit_ts - entry.entry_timestamp
                exchange_name = type(exchange).__name__.replace("Exchange", "").lower()
                await log_trade(
                    {
                        "symbol": symbol,
                        "exchange": exchange_name,
                        "entry_time": datetime.fromtimestamp(entry.entry_timestamp).isoformat(),
                        "exit_time": datetime.fromtimestamp(exit_ts).isoformat(),
                        "entry_futures_price": entry.entry_futures_price,
                        "exit_futures_price": metrics.futures_price,
                        "entry_spot_price": entry.entry_spot_price,
                        "exit_spot_price": metrics.spot_price,
                        "entry_basis": entry.entry_basis,
                        "exit_basis": exit_basis,
                        "basis_pct": exit_basis,
                        "funding": entry.entry_funding,
                        "quantity": partial_qty,
                        "volume_usd": float(volume_usd),
                        "pnl": float(pnl_net),
                        "pnl_pct": float(pnl_pct),
                        "commissions": float(commission),
                        "funding_accrued": float(entry.funding_accrued),
                        "slippage": exit_slippage,
                        "exit_reasons": ["partial"],
                        "notes": "частичный выход",
                    }
                )
                if position_id:
                    total_volume = Decimal(str(entry.entry_futures_price)) * Decimal(
                        str(entry.initial_quantity)
                    )
                    pnl_total = (
                        entry.pnl + entry.funding_accrued - entry.commissions
                    )
                    pnl_pct_total = (
                        pnl_total / total_volume * 100 if total_volume != 0 else Decimal(0)
                    )
                    asyncio.create_task(
                        notify_partial_close(
                            position_id,
                            (
                                f"Частичное закрытие {symbol}: осталось {quantity:.4f}\n"
                                f"Фандинг: {metrics.funding_rate * 100:.4f}%\n"
                                f"Базис: {exit_basis:.4f}%\n"
                                f"Объём: ${float(volume_usd):.2f}\n"
                                f"Время в позиции: {format_duration(hold_time)}\n"
                                f"Накопленный фандинг: {entry.funding_accrued:.4f} / "
                                f"PnL: {pnl_total:+.4f} ({pnl_pct_total:+.2f} %)"
                            ),
                        )
                    )
                continue
            await close_neutral_position(
                exchange, symbol, Decimal(str(quantity)), pnl, final=True
            )
            if entry:
                exit_basis = calculate_basis(
                    float(metrics.futures_price),
                    float(metrics.spot_price),
                    signed=True,
                )
                exit_ts = time.time()
                total_pnl = entry.pnl + pnl
                net_pnl = total_pnl + entry.funding_accrued - entry.commissions
                entry.exit_timestamp = exit_ts
                entry.exit_reasons = reasons
                entry.exit_futures_price = metrics.futures_price
                entry.exit_spot_price = metrics.spot_price
                entry.exit_basis = exit_basis
                entry.exit_funding = float(metrics.funding_rate)
                entry.pnl = net_pnl
                exchange_name = type(exchange).__name__.replace("Exchange", "").lower()
                volume_usd = Decimal(str(entry.entry_futures_price)) * Decimal(
                    str(entry.initial_quantity)
                )
                pnl_pct = (
                    net_pnl / volume_usd * 100 if volume_usd != 0 else Decimal(0)
                )
                await log_trade(
                    {
                        "symbol": symbol,
                        "exchange": exchange_name,
                        "entry_time": datetime.fromtimestamp(entry.entry_timestamp).isoformat(),
                        "exit_time": datetime.fromtimestamp(exit_ts).isoformat(),
                        "entry_futures_price": entry.entry_futures_price,
                        "exit_futures_price": metrics.futures_price,
                        "entry_spot_price": entry.entry_spot_price,
                        "exit_spot_price": metrics.spot_price,
                        "entry_basis": entry.entry_basis,
                        "exit_basis": exit_basis,
                        "basis_pct": exit_basis,
                        "funding": entry.entry_funding,
                        "quantity": entry.initial_quantity,
                        "volume_usd": float(volume_usd),
                        "pnl": float(net_pnl),
                        "pnl_pct": float(pnl_pct),
                        "commissions": float(entry.commissions),
                        "funding_accrued": float(entry.funding_accrued),
                        "slippage": exit_slippage,
                        "exit_reasons": reasons,
                        "notes": None,
                    }
                )
            return entry
        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Динамическая загрузка параметров
# ---------------------------------------------------------------------------

def get_thresholds(config_thresholds: Dict[str, float]) -> Dict[str, float]:
    """Возвращает пороги стратегии с учётом оптимизированных значений.

    Параметры
    ---------
    config_thresholds:
        Пороговые значения из ``config.yaml``. Результаты оптимизатора имеют
        приоритет над этими значениями. Если ``exit_basis`` не указан, берётся
        значение ``1.0``.
    """

    defaults = {"exit_basis": 1.0, **config_thresholds}
    return _load_thresholds(defaults)
