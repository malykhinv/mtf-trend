from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Iterable, Optional

from crypto_screener.domain.models.active_trade import ActiveTrade
from crypto_screener.domain.models.setup import Trade
from crypto_screener.domain.models.symbol import FuturesSymbol
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_result import TradeResult
from crypto_screener.execution.trade_executor import ExecutionResult


# region Private.
def _normalize_symbol(symbol: str) -> str:
    if "/" in symbol:
        base, tail = symbol.split("/", 1)
        quote = tail.split(":", 1)[0]
        return f"{base}{quote}".upper()
    cleaned = symbol.replace(":", "").replace("/", "")
    return cleaned.upper()


def _format_number(value: float) -> str:
    safe_value = value or 0
    return f"{safe_value:,.0f}".replace(",", " ")


def _format_price(value: Optional[float]) -> str:
    return f"{value:.4f}" if value is not None else "—"


def _format_trades(value: int) -> str:
    safe_value = value or 0
    return f"{safe_value:,}".replace(",", " ")


def _format_listing_age(listing_time: datetime) -> str:
    if listing_time.tzinfo is None:
        listing_time = listing_time.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - listing_time).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "1 день назад"
    return f"{days} дн. назад"


def _tradingview_link(
        symbol: str,
        exchange_name: str
) -> str:
    normalized_symbol = _normalize_symbol(symbol)
    exchange_prefix = exchange_name.upper()
    url = f"https://www.tradingview.com/chart/?symbol={exchange_prefix}:{normalized_symbol}"
    return f'<a href="{url}">{escape(normalized_symbol)}</a>'


def _trade_levels_block(setup: Trade) -> str:
    trade_levels = setup.trade_levels
    lines = [
        f"Entry: {_format_price(trade_levels.entry_price)}",
        f"SL: {_format_price(trade_levels.stop_loss_price)}",
        f"TP: {_format_price(trade_levels.take_profit_price)}",
    ]
    if trade_levels.partial_close_price is not None:
        lines.append(f"PC: {_format_price(trade_levels.partial_close_price)}")
    if trade_levels.breakeven_price is not None:
        lines.append(f"BE: {_format_price(trade_levels.breakeven_price)}")
    return "\n".join(lines)


# endregion

def build_capture_message(
        symbol: FuturesSymbol,
        timeframe: Timeframe,
        exchange_name: str,
        strategy_name: str | None = None,
) -> str:
    link = _tradingview_link(symbol.symbol, exchange_name)
    lines = [
        "<b>Включено слежение</b>",
        f"Инструмент: {link}",
        f"Стратегия: {escape(strategy_name or '')}",
        f"Биржа: {escape(exchange_name)}",
        f"Таймфрейм: {escape(timeframe.tf)}",
        f"Контекст: {escape(symbol.context.value)}",
        f"Объем 24ч: {_format_number(symbol.volume_usdt_24h)} USDT",
        f"Сделок 24ч: {_format_trades(symbol.trades_24h)}",
        f"Листинг: {_format_listing_age(symbol.listing_time)}",
    ]
    return "\n".join(lines)


def build_trade_opened_message(
        setup: Trade,
        timeframe: Timeframe,
        execution_result: ExecutionResult,
        exchange_name: str,
        strategy_name: str | None = None,
) -> str:
    link = _tradingview_link(setup.data.symbol, exchange_name)
    trade_levels = setup.trade_levels
    lines = [
        "<b>Позиция открыта</b>",
        f"Инструмент: {link}",
        f"Стратегия: {escape(strategy_name or '')}",
        f"Биржа: {escape(exchange_name)}",
        f"Таймфрейм: {escape(timeframe.tf)}",
        f"Количество: {execution_result.quantity:.4f}",
        f"Цена входа (план): {_format_price(trade_levels.entry_price)}",
        f"Stop-loss: {_format_price(trade_levels.stop_loss_price)}",
        f"Take-profit: {_format_price(trade_levels.take_profit_price)}",
    ]
    if trade_levels.partial_close_price is not None:
        lines.append(f"Partial close: {_format_price(trade_levels.partial_close_price)}")
    if trade_levels.breakeven_price is not None:
        lines.append(f"Breakeven: {_format_price(trade_levels.breakeven_price)}")
    lines.extend([
        f"Entry order ID: {escape(execution_result.entry_order_id)}",
        f"SL order ID: {escape(execution_result.protective_orders.stop_loss_id or '')}",
        f"TP order ID: {escape(execution_result.protective_orders.take_profit_id or '')}",
    ])
    if execution_result.protective_orders.partial_close_id:
        lines.append(f"PC order ID: {escape(execution_result.protective_orders.partial_close_id)}")
    if execution_result.protective_orders.breakeven_id:
        lines.append(f"BE order ID: {escape(execution_result.protective_orders.breakeven_id)}")
    return "\n".join(lines)


def build_trade_closure_message(
        active_trade: ActiveTrade,
        trade_result: TradeResult,
        profit_pct: float,
        profit_value: float,
        exit_label: str,
        exit_prices: Iterable[float],
        actual_entry_price: float,
        exchange_name: str,
        strategy_name: str | None = None,
) -> str:
    link = _tradingview_link(active_trade.symbol, exchange_name)
    exit_prices_text = ", ".join(f"{price:.4f}" for price in exit_prices) if exit_prices else "—"
    levels_block = _trade_levels_block(active_trade.setup)
    lines = [
        "<b>Сделка закрыта</b>",
        f"Инструмент: {link} ({escape(active_trade.timeframe.tf)})",
        f"Стратегия: {escape(strategy_name or '')}",
        f"Результат: {escape(trade_result.value)} — {escape(exit_label)}",
        f"P&L: {profit_value:+.4f} ({profit_pct:+.2f}%)",
        f"Выходные цены: {exit_prices_text}",
        f"Entry факт: {actual_entry_price:.4f}",
        "",
        "План уровней:",
        levels_block,
    ]
    return "\n".join(lines)


def build_protective_recovery_message(
        active_trade: ActiveTrade,
        reason: str,
        exchange_name: str,
        strategy_name: str | None = None,
) -> str:
    link = _tradingview_link(active_trade.symbol, exchange_name)
    return "\n".join(
        [
            "<b>Восстановление защиты</b>",
            f"Инструмент: {link} ({escape(active_trade.timeframe.tf)})",
            f"Стратегия: {escape(strategy_name or '')}",
            f"Причина: {escape(reason)}",
        ]
    )
