"""Notifier interface and Telegram implementation."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

import requests

from bot import config
from bot.domain.models.signal import Signal
from bot.domain.models.trade import Trade
from bot.domain.models.trade_status import TradeStatus
from bot.utils.logging import get_logger


class Notifier(Protocol):
    """Contract for services that deliver trading signals to the user."""

    def send_signal(self, signal: Signal) -> None:  # pragma: no cover - interface definition
        ...

    def send_trade(self, trade: Trade) -> None:  # pragma: no cover - interface definition
        ...


SendRequest = Callable[[str, dict[str, object], float], requests.Response]


@dataclass(slots=True)
class TelegramNotifier(Notifier):
    """Notifier that posts formatted signals to a Telegram chat."""

    token: str | None = None
    chat_id: str | None = None
    base_url: str = "https://api.telegram.org"
    timeout_sec: float = 5.0
    _request: SendRequest | None = None

    def __post_init__(self) -> None:
        token = self.token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("Не задан TELEGRAM_BOT_TOKEN ни в окружении, ни в .env")
        self.token = token
        chat_id = self.chat_id or os.getenv("TELEGRAM_CHAT_ID")
        if not chat_id:
            raise ValueError("Не задан TELEGRAM_CHAT_ID ни в окружении, ни в .env")
        self.chat_id = chat_id
        self._endpoint = f"{self.base_url}/bot{token}/sendMessage"
        self._request = self._request or self._default_request
        self._logger = get_logger(__name__)

    @staticmethod
    def _default_request(url: str, payload: dict[str, object], timeout: float) -> requests.Response:
        return requests.post(url, json=payload, timeout=timeout)

    def send_signal(self, signal: Signal) -> None:
        text = self._format_signal(signal)
        self._send_text(text, f"сигнала {signal.signal_id}")

    def send_trade(self, trade: Trade) -> None:
        text = self._format_trade(trade)
        self._send_text(text, f"сделки {trade.trade_id}")

    def _send_text(self, text: str, context: str) -> None:
        payload: dict[str, object] = {"chat_id": self.chat_id, "text": text}
        try:
            response = self._request(self._endpoint, payload, self.timeout_sec)
        except Exception as exc:  # pragma: no cover - network failure
            self._logger.exception("Не удалось отправить уведомление %s в Telegram", context)
            raise RuntimeError("Ошибка отправки Telegram уведомления") from exc

        if not response.ok:
            content = response.text
            self._logger.error(
                "Telegram API вернул ошибку %s при отправке %s: %s",
                response.status_code,
                context,
                content,
            )
            raise RuntimeError(f"Telegram API error: {response.status_code}")

        try:
            data = response.json()
        except json.JSONDecodeError:
            self._logger.error("Ответ Telegram не является JSON при отправке %s", context)
            raise RuntimeError("Некорректный ответ Telegram API")

        if not data.get("ok"):
            description = data.get("description", "unknown error")
            self._logger.error(
                "Telegram сообщил об ошибке при отправке %s: %s",
                context,
                description,
            )
            raise RuntimeError(f"Telegram API returned error: {description}")

        self._logger.info("Отправлено уведомление для %s", context)

    def _format_signal(self, signal: Signal) -> str:
        bar = signal.bar
        thresholds = signal.thresholds
        levels = signal.levels
        open_time = _to_timezone(bar.open_time)
        close_time = _to_timezone(bar.close_time)
        direction = "Лонг" if signal.direction.is_long else "Шорт"
        lines = [
            "🚨 Новый торговый сигнал",
            f"ID: {signal.signal_id}",
            f"Биржа: {signal.exchange.value}",
            f"Инструмент: {signal.symbol}",
            f"Таймфрейм: {signal.timeframe.value}",
            f"Направление: {direction}",
            f"Свеча: {open_time:%Y-%m-%d %H:%M} → {close_time:%Y-%m-%d %H:%M}",
            f"OHLC: {bar.open:.4f} / {bar.high:.4f} / {bar.low:.4f} / {bar.close:.4f}",
            (
                "Уровни: "
                f"вход {levels.entry_price:.4f}, "
                f"TP {levels.take_profit_price:.4f}, "
                f"SL {levels.stop_loss_price:.4f}"
            ),
            (
                "Пороги: "
                f"Δ% ≥ {thresholds.min_pct_move:.2f}, "
                f"ATRx ≥ {thresholds.min_atr_mult:.2f}, "
                f"RelVol ≥ {thresholds.min_relative_volume:.2f}"
            ),
        ]
        return "\n".join(lines)

    def _format_trade(self, trade: Trade) -> str:
        status_titles = {
            TradeStatus.OPENED: "🟢 Открыта сделка",
            TradeStatus.CLOSED_TP: "🎯 Сделка закрыта по тейк-профиту",
            TradeStatus.CLOSED_SL: "🛑 Сделка закрыта по стоп-лоссу",
            TradeStatus.CLOSED_MANUAL: "⚙️ Сделка закрыта вручную",
            TradeStatus.CANCELLED: "⚪️ Сделка отменена",
        }
        title = status_titles.get(trade.status, "ℹ️ Статус сделки обновлён")
        open_time = _to_timezone(trade.timestamp_open)
        close_time = (
            _to_timezone(trade.timestamp_close)
            if trade.timestamp_close is not None
            else None
        )
        direction = "Лонг" if trade.side.is_long else "Шорт"
        lines = [
            title,
            f"ID сделки: {trade.trade_id}",
            f"Сигнал: {trade.source_signal_id}",
            f"Биржа: {trade.exchange.value}",
            f"Инструмент: {trade.symbol}",
            f"Таймфрейм: {trade.timeframe.value}",
            f"Направление: {direction}",
            f"Объём: {trade.executed_qty:.4f}",
            f"Вход: {trade.entry_price:.4f}",
            f"TP: {trade.take_profit_price:.4f}",
            f"SL: {trade.stop_loss_price:.4f}",
            f"Открыта: {open_time:%Y-%m-%d %H:%M}",
        ]
        if close_time is not None:
            lines.append(f"Закрыта: {close_time:%Y-%m-%d %H:%M}")
        if trade.avg_fill_price is not None:
            lines.append(f"Средняя цена исполнения: {trade.avg_fill_price:.4f}")
        if trade.reason_close is not None:
            lines.append(f"Причина закрытия: {trade.reason_close.value}")
        if trade.sl_be_at is not None:
            sl_time = _to_timezone(trade.sl_be_at)
            lines.append(f"SL в безубытке с: {sl_time:%Y-%m-%d %H:%M}")
        return "\n".join(lines)



def _to_timezone(value: datetime) -> datetime:
    """Return ``value`` converted to the configured timezone."""

    if value.tzinfo is None:
        return value.replace(tzinfo=config.TIMEZONE)
    return value.astimezone(config.TIMEZONE)
