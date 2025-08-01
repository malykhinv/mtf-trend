"""Утилиты для стратегии арбитража по ставке фондирования.

Модуль содержит функции для оценки условий входа и выхода на основе ставок
фондирования и простых микроструктурных метрик. Также включает вспомогательные
функции для открытия и мониторинга нейтральных позиций.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict
import time
from datetime import datetime
import math

from exchanges import (
    fetch_funding_history,
    get_orderbook,
    get_spot_orderbook,
    get_stats,
    get_ohlc,
    hedge,
    place_order,
)
import exchanges
from risk import risk_control
from ai.parameter_optimizer import load_thresholds as _load_thresholds
from main import CONFIG
from utils.logger import log_trade
from utils.telegram import format_duration, notify_partial_close


@dataclass
class MarketMetrics:
    """Набор рыночных метрик, используемых стратегией."""

    funding_rate: float
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


async def get_market_metrics(
    symbol: str, trade_size: float, depth: int = 5
) -> MarketMetrics:
    """Получает расширенные рыночные метрики для ``symbol``.

    Параметры
    ---------
    symbol:
        Торговая пара, поддерживаемая текущей биржей.
    trade_size:
        Объём сделки для оценки проскальзывания.
    depth:
        Глубина стакана для расчёта ликвидности. По умолчанию ``5``.

    Возвращает
    ----------
    MarketMetrics
        Метрики рынка, включая оценку проскальзывания по каждой ноге.
    """
    history = await fetch_funding_history(symbol, hours=8, limit=3)
    if history:
        k = 2 / (len(history) + 1)
        funding = history[0]
        for rate in history[1:]:
            # Экспоненциальное сглаживание ставок фондирования
            funding = rate * k + funding * (1 - k)
    else:
        funding = 0.0

    # Загружаем стаканы фьючерса и спота
    perp_book = await get_orderbook(symbol, depth=depth)
    spot_book = await get_spot_orderbook(symbol, depth=depth)

    bids = [(float(p), float(q)) for p, q in perp_book.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in perp_book.get("asks", [])]
    spot_bids = [(float(p), float(q)) for p, q in spot_book.get("bids", [])]
    spot_asks = [(float(p), float(q)) for p, q in spot_book.get("asks", [])]

    def _calc_slippage(orders, size, mid):
        """Оценивает проскальзывание при выполнении ``size`` по стакану."""
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

    if bids and asks:
        futures_price = (asks[0][0] + bids[0][0]) / 2
        futures_slippage = _calc_slippage(asks, trade_size, futures_price)
    else:
        futures_price = float("nan")
        futures_slippage = float("inf")

    if spot_bids and spot_asks:
        spot_price = (spot_asks[0][0] + spot_bids[0][0]) / 2
        spot_slippage = _calc_slippage(spot_asks, trade_size, spot_price)
    else:
        spot_price = float("nan")
        spot_slippage = float("inf")

    # Суммарное проскальзывание обеих ног
    slippage = futures_slippage + spot_slippage

    if bids and asks and spot_bids and spot_asks:
        spread = abs(futures_price - spot_price)
    else:
        spread = float("inf")

    stats = await get_stats(symbol)
    volume = float(stats.get("volume_24h", 0.0))
    open_interest = float(stats.get("open_interest", 0.0))
    if futures_price and not math.isnan(futures_price):
        open_interest *= futures_price
    else:
        open_interest = 0.0
    liquidity = sum(q for _, q in bids) + sum(q for _, q in asks)
    basis = (
        (spread / spot_price * 100) if spot_price else float("inf")
    )

    # Изменение цены за последние 15 минут для оценки волатильности
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
    quantity: float,
    metrics: MarketMetrics,
    thresholds: Dict[str, float],
    ) -> bool:
    """Возвращает ``True``, если выполнены все условия входа.

    Порог ``basis`` задаётся в процентных пунктах.
    """

    whitelist = CONFIG.get("bot", {}).get("whitelist", [])
    deposit = CONFIG.get("bot", {}).get("deposit_size", float("inf"))
    max_deposit_trade = deposit * thresholds.get("deposit_pct", 1.0)
    spread_pct = (
        metrics.spread / metrics.futures_price
        if metrics.futures_price
        else float("inf")
    )
    notional = quantity * metrics.futures_price
    max_basis_pct = thresholds.get("basis", float("inf"))
    combined_slippage = metrics.slippage
    slippage_limit = thresholds.get("slippage", 0.003)

    return (
        metrics.funding_rate > 0
        and metrics.funding_rate >= thresholds.get("funding_rate", 0.0)
        and spread_pct <= thresholds.get("spread", float("inf"))
        and metrics.basis <= max_basis_pct
        and metrics.liquidity >= thresholds.get("liquidity", 0.0)
        and metrics.volume >= thresholds.get("volume", 0.0)
        and abs(metrics.volatility)
        <= thresholds.get("volatility", float("inf"))
        and metrics.open_interest <= metrics.volume * 2
        and combined_slippage <= slippage_limit
        and notional <= thresholds.get("max_trade_size", float("inf"))
        and notional <= max_deposit_trade
        and symbol in whitelist
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

_positions: Dict[str, Dict[str, Any]] = {}


async def open_neutral_position(symbol: str, quantity: float) -> Dict[str, Dict]:
    """Открывает компенсирующие длинную и короткую позиции и сохраняет данные."""
    bot_cfg = CONFIG.get("bot", {})
    if symbol not in bot_cfg.get("whitelist", []):
        raise RuntimeError("Символ отсутствует в белом списке")
    # Получаем метрики рынка для оценки сделки
    entry_metrics = await get_market_metrics(symbol, quantity)
    notional = quantity * entry_metrics.futures_price
    deposit = bot_cfg.get("deposit_size", float("inf"))
    deposit_pct = CONFIG.get("thresholds", {}).get("deposit_pct", 1.0)
    max_trade = deposit * deposit_pct
    if notional > deposit or notional > max_trade:
        raise RuntimeError("Размер сделки превышает лимиты депозита")
    if not risk_control.can_open_position(notional) or risk_control.is_symbol_open(symbol):
        raise RuntimeError("Превышены лимиты риска, торговля приостановлена или позиция уже открыта")
    # Хеджируем позицию на споте и фьючерсе
    orders = await hedge(symbol, quantity)
    risk_control.update_position(notional)
    risk_control.mark_symbol_open(symbol)
    now = time.time()
    commission = sum(float(o.get("fee", 0.0)) for o in orders.values())
    _positions[symbol] = {
        "entry_timestamp": now,
        "entry_futures_price": entry_metrics.futures_price,
        "entry_spot_price": entry_metrics.spot_price,
        "entry_basis": entry_metrics.basis,
        "entry_funding": entry_metrics.funding_rate,
        "quantity": quantity,
        "initial_quantity": quantity,
        "pnl": 0.0,
        "funding_accrued": 0.0,
        "commissions": commission,
        "last_funding_timestamp": now,
    }
    exchange_name = (
        type(exchanges._current).__name__.replace("Exchange", "").lower()
        if getattr(exchanges, "_current", None)
        else "unknown"
    )
    volume_usd = notional
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
            "quantity": quantity,
            "volume_usd": volume_usd,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "commissions": commission,
            "funding_accrued": 0.0,
            "slippage": entry_metrics.slippage,
            "exit_reasons": None,
            "notes": "open",
        }
    )
    return orders


async def close_neutral_position(
    symbol: str, quantity: float, pnl: float = 0.0, final: bool = True
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
    close_long = await place_order(symbol, "SELL", quantity)
    try:
        close_short = await place_order(symbol, "BUY", quantity)
    except Exception as exc:
        # В случае ошибки возвращаем длинную позицию
        await place_order(symbol, "BUY", quantity)
        raise RuntimeError("Не удалось закрыть хедж; длинная нога откатена") from exc
    entry = _positions.get(symbol)
    commission = float(close_long.get("fee", 0.0)) + float(
        close_short.get("fee", 0.0)
    )
    if entry is not None:
        entry["commissions"] = entry.get("commissions", 0.0) + commission
    ref_price = entry.get("entry_futures_price", 0.0) if entry else 0.0
    risk_control.update_position(-(quantity * ref_price))
    risk_control.record_pnl(pnl)
    if final:
        risk_control.mark_symbol_closed(symbol)
    return {"long": close_long, "short": close_short}


async def monitor_neutral_position(
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
    entry = _positions.get(symbol, {})
    while True:
        try:
            metrics = await get_market_metrics(symbol, quantity)
        except asyncio.TimeoutError:
            await close_neutral_position(symbol, quantity, final=True)
            break
        now = time.time()
        if entry:
            last = entry.get("last_funding_timestamp", now)
            funding_fee = (
                quantity
                * metrics.futures_price
                * metrics.funding_rate
                * (now - last)
                / (8 * 3600)
            )
            # Накапливаем полученное фондирование
            entry["funding_accrued"] = entry.get("funding_accrued", 0.0) + funding_fee
            entry["last_funding_timestamp"] = now
        reasons: list[str] = []
        if check_exit_conditions(metrics, exit_thresholds):
            reasons.append("threshold")
        if metrics.funding_rate < 0.0001:
            reasons.append("low_funding")
        if entry and metrics.funding_rate < 0 and entry.get("entry_funding", 0) >= 0:
            reasons.append("funding_negative")
        if metrics.basis > 1.0:
            reasons.append("basis")
        if entry:
            exit_slippage = abs(
                metrics.futures_price - entry.get("entry_futures_price", 0.0)
            ) / max(entry.get("entry_futures_price", 1.0), 1e-9)
            if exit_slippage > 0.005:
                reasons.append("slippage")
            hold_time = now - entry.get("entry_timestamp", now)
            max_hold = exit_thresholds.get("holding_time", 48 * 3600)
            if hold_time > max_hold:
                reasons.append("time")
            pnl = (
                (metrics.futures_price - entry.get("entry_futures_price", 0.0))
                - (metrics.spot_price - entry.get("entry_spot_price", 0.0))
            ) * quantity
            if pnl + entry.get("funding_accrued", 0.0) - entry.get("commissions", 0.0) < 0:
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
                    (metrics.futures_price - entry.get("entry_futures_price", 0.0))
                    - (metrics.spot_price - entry.get("entry_spot_price", 0.0))
                ) * partial_qty
                orders = await close_neutral_position(
                    symbol, partial_qty, pnl_part, final=False
                )
                # Обновляем запись о позиции после частичного выхода
                quantity -= partial_qty
                entry["quantity"] = quantity
                entry["pnl"] = entry.get("pnl", 0.0) + pnl_part
                exit_ts = time.time()
                exit_basis = (
                    ((metrics.futures_price - metrics.spot_price) / metrics.spot_price)
                    * 100
                    if metrics.spot_price
                    else float("inf")
                )
                commission = float(orders["long"].get("fee", 0.0)) + float(
                    orders["short"].get("fee", 0.0)
                )
                volume_usd = partial_qty * entry.get("entry_futures_price", 0.0)
                pnl_pct = (pnl_part / volume_usd * 100) if volume_usd else 0.0
                hold_time = exit_ts - entry.get("entry_timestamp", exit_ts)
                exchange_name = (
                    type(exchanges._current).__name__.replace("Exchange", "").lower()
                    if getattr(exchanges, "_current", None)
                    else "unknown"
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
                        "exit_futures_price": metrics.futures_price,
                        "entry_spot_price": entry.get("entry_spot_price"),
                        "exit_spot_price": metrics.spot_price,
                        "entry_basis": entry.get("entry_basis"),
                        "exit_basis": exit_basis,
                        "basis_pct": exit_basis,
                        "funding": entry.get("entry_funding"),
                        "quantity": partial_qty,
                        "volume_usd": volume_usd,
                        "pnl": pnl_part,
                        "pnl_pct": pnl_pct,
                        "commissions": commission,
                        "funding_accrued": entry.get("funding_accrued", 0.0),
                        "slippage": exit_slippage,
                        "exit_reasons": ["partial"],
                        "notes": "частичный выход",
                    }
                )
                if position_id:
                    total_volume = entry.get("entry_futures_price", 0.0) * entry.get(
                        "initial_quantity", entry.get("quantity", 0.0)
                    )
                    pnl_total = entry.get("pnl", 0.0)
                    pnl_pct_total = (
                        pnl_total / total_volume * 100
                        if total_volume
                        else 0.0
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
                                f"Накопленный фандинг: {entry.get('funding_accrued', 0.0):.4f} / "
                                f"PnL: {pnl_total:+.4f} ({pnl_pct_total:+.2f} %)"
                            ),
                        )
                    )
                continue
            await close_neutral_position(symbol, quantity, pnl, final=True)
            if entry:
                exit_basis = (
                    ((metrics.futures_price - metrics.spot_price) / metrics.spot_price) * 100
                    if metrics.spot_price
                    else float("inf")
                )
                exit_ts = time.time()
                total_pnl = entry.get("pnl", 0.0) + pnl
                entry.update(
                    {
                        "exit_timestamp": exit_ts,
                        "exit_reasons": reasons,
                        "exit_futures_price": metrics.futures_price,
                        "exit_spot_price": metrics.spot_price,
                        "exit_basis": exit_basis,
                        "exit_funding": metrics.funding_rate,
                        "pnl": total_pnl,
                    }
                )
                exchange_name = (
                    type(exchanges._current).__name__.replace("Exchange", "").lower()
                    if getattr(exchanges, "_current", None)
                    else "unknown"
                )
                volume_usd = entry.get("entry_futures_price", 0.0) * entry.get(
                    "initial_quantity", entry.get("quantity", 0.0)
                )
                pnl_pct = (total_pnl / volume_usd * 100) if volume_usd else 0.0
                log_trade(
                    {
                        "symbol": symbol,
                        "exchange": exchange_name,
                        "entry_time": datetime.fromtimestamp(
                            entry.get("entry_timestamp", exit_ts)
                        ).isoformat(),
                        "exit_time": datetime.fromtimestamp(exit_ts).isoformat(),
                        "entry_futures_price": entry.get("entry_futures_price"),
                        "exit_futures_price": metrics.futures_price,
                        "entry_spot_price": entry.get("entry_spot_price"),
                        "exit_spot_price": metrics.spot_price,
                        "entry_basis": entry.get("entry_basis"),
                        "exit_basis": exit_basis,
                        "basis_pct": exit_basis,
                        "funding": entry.get("entry_funding"),
                        "quantity": entry.get("initial_quantity", entry.get("quantity")),
                        "volume_usd": volume_usd,
                        "pnl": total_pnl,
                        "pnl_pct": pnl_pct,
                        "commissions": entry.get("commissions", 0.0),
                        "funding_accrued": entry.get("funding_accrued", 0.0),
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
