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

getcontext().prec = 10

from exchanges import BaseExchange
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


@dataclass
class MarketMetrics:
    """Набор рыночных метрик, используемых стратегией."""

    funding_rate: Decimal
    spread: float
    liquidity: float
    volatility: float  # 15-minute price change percentage
    spot_price: float
    futures_price: float
    volume: float
    open_interest: float
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

    futures_price = (asks[0][0] + bids[0][0]) / 2
    futures_slippage = _calc_slippage(asks, trade_size, futures_price)

    spot_price = (spot_asks[0][0] + spot_bids[0][0]) / 2
    spot_slippage = _calc_slippage(spot_asks, trade_size, spot_price)

    # Суммарное проскальзывание обеих ног
    slippage = futures_slippage + spot_slippage

    spread = abs(futures_price - spot_price)

    if exchange is not None:
        stats = await exchange.get_stats(symbol)
    else:
        stats = await get_stats(symbol)
    if not stats:
        return None

    volume = float(stats.get("volume_24h", 0.0))
    open_interest = float(stats.get("open_interest", 0.0))
    if futures_price and not math.isnan(futures_price):
        open_interest *= futures_price
    else:
        open_interest = 0.0
    liquidity = sum(q for _, q in bids) + sum(q for _, q in asks)
    basis = calculate_basis(futures_price, spot_price)

    # Изменение цены за последние 15 минут для оценки волатильности
    if exchange is not None:
        ohlc = await exchange.get_ohlc(symbol, interval="15m", limit=1)
    else:
        ohlc = await get_ohlc(symbol, interval="15m", limit=1)
    if ohlc:
        candle = ohlc[0]
        o = candle.get("open") or 0.0
        c = candle.get("close") or 0.0
        volatility = ((c - o) / o) if o else float("inf")
    else:
        volatility = float("nan")

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
    ) -> bool:
    """Возвращает ``True``, если выполнены все условия входа.

    Порог ``basis`` задаётся в процентных пунктах.
    """
    if metrics is None:
        logger.warning("Skipping %s: incomplete market metrics", symbol)
        return False

    quantity = Decimal(str(quantity))
    deposit = Decimal(str(CONFIG.get("bot", {}).get("deposit_size", "Infinity")))
    max_deposit_trade = deposit * Decimal(str(thresholds.get("deposit_pct", 1.0)))
    spread_pct = (
        Decimal(str(metrics.spread)) / Decimal(str(metrics.futures_price))
        if metrics.futures_price
        else Decimal("Infinity")
    )
    notional = quantity * Decimal(str(metrics.futures_price))
    max_basis_pct = Decimal(str(thresholds.get("basis", float("inf"))))
    basis_abs = abs(Decimal(str(metrics.basis)))
    combined_slippage = Decimal(str(metrics.slippage))
    slippage_limit = Decimal(str(thresholds.get("slippage", 0.003)))

    return (
        metrics.funding_rate > Decimal(0)
        and metrics.funding_rate >= Decimal(str(thresholds.get("funding_rate", 0.0)))
        and spread_pct <= Decimal(str(thresholds.get("spread", float("inf"))))
        and basis_abs <= max_basis_pct
        and Decimal(str(metrics.liquidity)) >= Decimal(str(thresholds.get("liquidity", 0.0)))
        and Decimal(str(metrics.volume)) >= Decimal(str(thresholds.get("volume", 0.0)))
        and abs(Decimal(str(metrics.volatility)))
        <= Decimal(str(thresholds.get("volatility", float("inf"))))
        and Decimal(str(metrics.open_interest)) <= Decimal(str(metrics.volume)) * Decimal(2)
        and combined_slippage <= slippage_limit
        and notional <= Decimal(str(thresholds.get("max_trade_size", float("inf"))))
        and notional <= max_deposit_trade
        and not risk_control.is_symbol_open(symbol)
    )


def check_exit_conditions(metrics: MarketMetrics, thresholds: Dict[str, float]) -> bool:
    """Возвращает ``True``, если выполнено любое условие выхода."""
    return (
        abs(metrics.funding_rate) <= thresholds.get("funding_rate", float("inf"))
        or metrics.spread >= thresholds.get("spread", float("-inf"))
        or metrics.liquidity <= thresholds.get("liquidity", float("inf"))
        or abs(metrics.volatility)
        >= thresholds.get("volatility", float("-inf"))
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
    pnl: float = 0.0
    funding_accrued: float = 0.0
    commissions: float = 0.0
    last_funding_timestamp: float = 0.0
    exchange: str = ""
    exit_timestamp: float | None = None
    exit_reasons: list[str] = field(default_factory=list)
    exit_futures_price: float | None = None
    exit_spot_price: float | None = None
    exit_basis: float | None = None
    exit_funding: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

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
            pnl=float(data.get("pnl", 0.0)),
            funding_accrued=float(data.get("funding_accrued", 0.0)),
            commissions=float(data.get("commissions", 0.0)),
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


def load_positions(path: Path | str | None = None) -> None:
    """Загружает ранее сохранённые позиции из ``path``."""

    path = _get_positions_path(path)
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return
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
            risk_control.update_position(Decimal(str(notional)))
        risk_control.mark_symbol_open(symbol)


def save_positions(path: Path | str | None = None) -> None:
    """Сохраняет текущие открытые позиции в ``path``."""

    path = _get_positions_path(path)
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump({s: p.to_dict() for s, p in positions.items()}, fh)


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
    notional = quantity * Decimal(str(entry_metrics.futures_price))
    deposit = Decimal(str(bot_cfg.get("deposit_size", "Infinity")))
    deposit_pct = Decimal(str(CONFIG.get("thresholds", {}).get("deposit_pct", 1.0)))
    max_trade = deposit * deposit_pct
    if notional > deposit or notional > max_trade:
        raise RuntimeError("Размер сделки превышает лимиты депозита")
    if not risk_control.can_open_position(notional) or risk_control.is_symbol_open(symbol):
        raise RuntimeError("Превышены лимиты риска, торговля приостановлена или позиция уже открыта")
    # Хеджируем позицию на споте и фьючерсе
    spot_order = await exchange.place_spot_order(symbol, "BUY", float(quantity))
    try:
        perp_order = await exchange.place_order(symbol, "SELL", float(quantity))
    except Exception as exc:
        order_id = str(spot_order.get("orderId") or spot_order.get("id") or "")
        try:
            await exchange.cancel_order(order_id)
        except Exception:
            pass
        raise RuntimeError(
            "Не удалось разместить хедж; спотовая часть откатена"
        ) from exc
    orders = {"spot": spot_order, "perp": perp_order}
    risk_control.update_position(notional)
    risk_control.mark_symbol_open(symbol)
    now = time.time()
    commission = sum(Decimal(str(o.get("fee", 0.0))) for o in orders.values())
    positions[symbol] = Position(
        entry_timestamp=now,
        entry_futures_price=entry_metrics.futures_price,
        entry_spot_price=entry_metrics.spot_price,
        entry_basis=entry_metrics.basis,
        entry_funding=float(entry_metrics.funding_rate),
        quantity=float(quantity),
        initial_quantity=float(quantity),
        pnl=0.0,
        funding_accrued=0.0,
        commissions=float(commission),
        last_funding_timestamp=now,
        exchange=exchange_name,
    )
    save_positions()
    volume_usd = float(notional)
    log_trade(
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
    quantity = Decimal(str(quantity))
    pnl = Decimal(str(pnl))
    close_long = await exchange.place_order(symbol, "SELL", float(quantity))
    try:
        close_short = await exchange.place_order(symbol, "BUY", float(quantity))
    except Exception as exc:
        # В случае ошибки возвращаем длинную позицию
        await exchange.place_order(symbol, "BUY", float(quantity))
        raise RuntimeError("Не удалось закрыть хедж; длинная нога откатена") from exc
    entry = positions.get(symbol)
    commission = Decimal(str(close_long.get("fee", 0.0))) + Decimal(
        str(close_short.get("fee", 0.0))
    )
    if entry is not None:
        entry.commissions += float(commission)
        ref_price = Decimal(str(entry.entry_futures_price))
    else:
        ref_price = Decimal(0)
    risk_control.update_position(-(quantity * ref_price))
    net_pnl = pnl - commission
    risk_control.record_pnl(net_pnl)
    if final:
        risk_control.mark_symbol_closed(symbol)
        positions.pop(symbol, None)
        save_positions()
    return {"long": close_long, "short": close_short, "commission": float(commission)}


async def monitor_neutral_position(
    exchange: BaseExchange,
    symbol: str,
    quantity: float,
    exit_thresholds: Dict[str, float],
    poll_interval: float = 5.0,
    position_id: str | None = None,
) -> None:
    """Следит за позицией и закрывает её при срабатывании условий выхода.

    Поддерживается частичное закрытие: если размер позиции больше двух
    минимальных, закрывается половина и отслеживание продолжается. Обновления
    отправляются через :func:`notify_partial_close`, чтобы редактировать одно
    сообщение вместо отправки новых.
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
            quantity_d = Decimal(str(quantity))
            price_d = Decimal(str(metrics.futures_price))
            elapsed = Decimal(str(now - last))
            funding_fee = (
                quantity_d
                * price_d
                * metrics.funding_rate
                * elapsed
                / Decimal(8 * 3600)
            )
            # Накапливаем полученное фондирование
            entry.funding_accrued = float(Decimal(str(entry.funding_accrued)) + funding_fee)
            entry.last_funding_timestamp = now
        reasons: list[str] = []
        exit_slippage = 0.0
        if check_exit_conditions(metrics, exit_thresholds):
            reasons.append("threshold")
        if metrics.funding_rate < Decimal("0.0001"):
            reasons.append("low_funding")
        if entry and metrics.funding_rate < 0 <= entry.entry_funding:
            reasons.append("funding_negative")
        if abs(metrics.basis) >= exit_thresholds.get("basis", float("inf")):
            reasons.append("basis")
        if entry:
            exit_slippage = abs(
                metrics.futures_price - entry.entry_futures_price
            ) / max(entry.entry_futures_price, 1e-9)
            if exit_slippage > 0.005:
                reasons.append("slippage")
            hold_time = now - entry.entry_timestamp
            max_hold = exit_thresholds.get("holding_time", 48 * 3600)
            if hold_time > max_hold:
                reasons.append("time")
            pnl = (
                (metrics.futures_price - entry.entry_futures_price)
                - (metrics.spot_price - entry.entry_spot_price)
            ) * quantity
            if pnl + entry.funding_accrued - entry.commissions < 0:
                reasons.append("pnl_vs_cost")
        else:
            pnl = 0.0
        if reasons:
            min_trade_usd = exit_thresholds.get("min_trade_size", 0.0)
            min_trade_qty = (
                min_trade_usd / metrics.futures_price
                if metrics.futures_price
                else 0.0
            )
            if entry and quantity > max(min_trade_qty * 2, 0.0):
                partial_qty = quantity / 2
                pnl_part = (
                    (metrics.futures_price - entry.entry_futures_price)
                    - (metrics.spot_price - entry.entry_spot_price)
                ) * partial_qty
                orders = await close_neutral_position(
                    exchange, symbol, partial_qty, pnl_part, final=False
                )
                # Обновляем запись о позиции после частичного выхода
                quantity -= partial_qty
                entry.quantity = quantity
                entry.pnl += pnl_part
                save_positions()
                exit_ts = time.time()
                exit_basis = calculate_basis(
                    metrics.futures_price, metrics.spot_price, signed=True
                )
                commission = float(orders.get("commission", 0.0))
                pnl_net = pnl_part - commission
                volume_usd = partial_qty * entry.entry_futures_price
                pnl_pct = (pnl_net / volume_usd * 100) if volume_usd else 0.0
                hold_time = exit_ts - entry.entry_timestamp
                exchange_name = type(exchange).__name__.replace("Exchange", "").lower()
                log_trade(
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
                        "volume_usd": volume_usd,
                        "pnl": pnl_net,
                        "pnl_pct": pnl_pct,
                        "commissions": commission,
                        "funding_accrued": entry.funding_accrued,
                        "slippage": exit_slippage,
                        "exit_reasons": ["partial"],
                        "notes": "частичный выход",
                    }
                )
                if position_id:
                    total_volume = entry.entry_futures_price * entry.initial_quantity
                    pnl_total = (
                        entry.pnl + entry.funding_accrued - entry.commissions
                    )
                    pnl_pct_total = (
                        pnl_total / total_volume * 100 if total_volume else 0.0
                    )
                    asyncio.create_task(
                        notify_partial_close(
                            position_id,
                            (
                                f"Частичное закрытие {symbol}: осталось {quantity:.4f}\n"
                                f"Фандинг: {metrics.funding_rate * 100:.4f}%\n"
                                f"Базис: {exit_basis:.4f}%\n"
                                f"Объём: ${volume_usd:.2f}\n"
                                f"Время в позиции: {format_duration(hold_time)}\n"
                                f"Накопленный фандинг: {entry.funding_accrued:.4f} / "
                                f"PnL: {pnl_total:+.4f} ({pnl_pct_total:+.2f} %)"
                            ),
                        )
                    )
                continue
            await close_neutral_position(exchange, symbol, quantity, pnl, final=True)
            if entry:
                exit_basis = calculate_basis(
                    metrics.futures_price, metrics.spot_price, signed=True
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
                volume_usd = entry.entry_futures_price * entry.initial_quantity
                pnl_pct = (net_pnl / volume_usd * 100) if volume_usd else 0.0
                log_trade(
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
                        "volume_usd": volume_usd,
                        "pnl": net_pnl,
                        "pnl_pct": pnl_pct,
                        "commissions": entry.commissions,
                        "funding_accrued": entry.funding_accrued,
                        "slippage": exit_slippage,
                        "exit_reasons": reasons,
                        "notes": None,
                    }
                )
            break
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
        приоритет над этими значениями.
    """

    return _load_thresholds(config_thresholds)
