"""Telegram operator messages for anomaly live2.

Telegram is an operator UI only. Source of truth stays in live2 artifacts.
Messages intentionally mirror the compact style of the legacy live runtime while
keeping the live2 contract strict: integrity errors are critical, and symbol
names are clickable Coinglass links.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .contracts import Live2Component, Live2Event, Live2Severity
from .deadline import Live2DecisionRecord
from .position_supervisor import Live2PositionSupervisorAction

SERVICE_WARNING_EMOJI = "⚠️"
SERVICE_WORK_EMOJI = "🚧"
ANIMAL_EMOJIS = (
    "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨", "🐯",
    "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🐦", "🦆", "🦅",
    "🦉", "🦇", "🐺", "🐗", "🐴", "🦄", "🐝", "🐛", "🦋", "🐌",
    "🐞", "🐜", "🦗", "🕷️", "🦂", "🐢", "🐍", "🦎", "🦖", "🦕",
    "🐙", "🦑", "🦐", "🦞", "🦀", "🐡", "🐠", "🐟", "🐬", "🐳",
    "🐋", "🦈", "🐊", "🐅", "🐆", "🦓", "🦍", "🦧", "🐘", "🦛",
    "🦏", "🐪", "🐫", "🦒", "🦘", "🦬", "🐃", "🐂", "🐄", "🐎",
    "🐖", "🐏", "🐑", "🦙", "🐐", "🦌", "🐕", "🐩", "🐈", "🐓",
    "🦃", "🦚", "🦜", "🦢", "🦩", "🕊️", "🐇", "🦝", "🦨", "🦡",
    "🦫", "🦦", "🦥", "🐁", "🐀", "🐿️", "🦔",
)
QUOTE_SYMBOL_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD")


@dataclass(frozen=True, slots=True)
class Live2TelegramConfig:
    events_bot_token: str
    events_chat_id: str
    positions_bot_token: str
    positions_chat_id: str
    cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.events_bot_token:
            raise ValueError("events_bot_token is required")
        if not self.events_chat_id:
            raise ValueError("events_chat_id is required")
        if not self.positions_bot_token:
            raise ValueError("positions_bot_token is required")
        if not self.positions_chat_id:
            raise ValueError("positions_chat_id is required")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")


def build_live2_telegram_config_from_env() -> Live2TelegramConfig:
    values = {
        "TELEGRAM_EVENTS_BOT_TOKEN": os.getenv("TELEGRAM_EVENTS_BOT_TOKEN", "").strip(),
        "TELEGRAM_EVENTS_CHAT_ID": os.getenv("TELEGRAM_EVENTS_CHAT_ID", "").strip(),
        "TELEGRAM_POSITIONS_BOT_TOKEN": os.getenv("TELEGRAM_POSITIONS_BOT_TOKEN", "").strip(),
        "TELEGRAM_POSITIONS_CHAT_ID": os.getenv("TELEGRAM_POSITIONS_CHAT_ID", "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Не заполнены Telegram env переменные: {', '.join(missing)}")
    return Live2TelegramConfig(
        events_bot_token=values["TELEGRAM_EVENTS_BOT_TOKEN"],
        events_chat_id=values["TELEGRAM_EVENTS_CHAT_ID"],
        positions_bot_token=values["TELEGRAM_POSITIONS_BOT_TOKEN"],
        positions_chat_id=values["TELEGRAM_POSITIONS_CHAT_ID"],
    )


class Live2TelegramDispatcher:
    """Small Telegram dispatcher for live2 operator messages."""

    def __init__(
        self,
        config: Live2TelegramConfig,
        *,
        logger: Callable[[str], None] | None = None,
        event_writer: Callable[[Live2Event], None] | None = None,
    ) -> None:
        self._config = config
        self._logger = logger or (lambda message: None)
        self._event_writer = event_writer
        self._queue: queue.Queue[dict[str, object]] = queue.Queue()
        self._last_sent: dict[str, float] = {}
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="live2-telegram-dispatcher", daemon=True)
        self._thread.start()

    def set_event_writer(self, event_writer: Callable[[Live2Event], None]) -> None:
        self._event_writer = event_writer

    def close(self) -> None:
        self._closed = True
        self._queue.put({"kind": "stop"})
        self._thread.join(timeout=5.0)

    def send_startup(self, *, output_dir: Path, universe_size: int, runtime_generation: str) -> None:
        text = (
            f"{SERVICE_WORK_EMOJI} <b>Запуск live2</b>\n\n"
            f"universe {int(universe_size)} · режим strict · {telegram_code(runtime_generation)}\n"
            f"артефакты {telegram_code(str(output_dir))}"
        )
        self.send(channel="events", key="live2_startup", text=text, symbol="__telegram__")

    def notify_decision(self, decision: Live2DecisionRecord) -> None:
        if decision.verdict == "selected" and decision.execution_verdict == "selected":
            self.send_sync(channel="positions", text=format_entry_message(decision), symbol=decision.symbol)
            return
        if decision.verdict == "position_integrity_error" or decision.execution_integrity_error:
            self.send_critical_sync(
                channel="positions",
                key=f"live2_integrity_{decision.symbol}",
                text=format_integrity_message(
                    symbol=decision.symbol,
                    reason=decision.execution_reason or decision.reason,
                    position_id=decision.execution_position_id,
                ),
                symbol=decision.symbol,
            )

    def notify_supervisor_action(self, action: Live2PositionSupervisorAction) -> None:
        if action.event_type in {"position_tp1_full_close_verified", "position_tp1_filled_be_stop_verified"}:
            self.send_sync(channel="positions", text=format_tp1_message(action), symbol=action.symbol)
        elif action.event_type == "position_final_close_verified":
            self.send_sync(channel="positions", text=format_final_close_message(action), symbol=action.symbol)
        elif action.event_type == "position_integrity_error" or action.severity == Live2Severity.ERROR:
            self.send_critical_sync(
                channel="positions",
                key=f"live2_position_integrity_{action.symbol}",
                text=format_integrity_message(
                    symbol=action.symbol,
                    reason=action.message or str(action.data.get("reason") or "unknown"),
                    position_id=action.position_id,
                ),
                symbol=action.symbol,
            )

    def notify_runtime_gate_update(self, gate_status: dict[str, object], *, previous_allowed: bool, current_allowed: bool) -> None:
        # Runtime entry gates flap naturally during startup, reconnects, and warm-up.
        # Keep this visible in the terminal grid and artifacts, but do not spam Telegram.
        return

    def send(
        self,
        *,
        channel: str,
        key: str,
        text: str,
        symbol: str,
        reply_to_message_id: int | None = None,
    ) -> None:
        self._queue.put(
            {
                "channel": channel,
                "key": key,
                "text": text,
                "symbol": symbol,
                "reply_to_message_id": reply_to_message_id,
            }
        )

    def send_sync(
        self,
        *,
        channel: str,
        text: str,
        symbol: str,
        reply_to_message_id: int | None = None,
    ) -> int | None:
        try:
            message_id = self._send_message(channel=channel, text=text, reply_to_message_id=reply_to_message_id)
        except Exception as exc:
            self._record_send_failure(
                event_type="telegram_sync_send_failed",
                channel=channel,
                key="sync",
                symbol=symbol,
                text=text,
                exc=exc,
                reply_to_message_id=reply_to_message_id,
            )
            return None
        self._record_send_success(channel=channel, key="sync", symbol=symbol, message_id=message_id)
        return message_id

    def send_critical_sync(
        self,
        *,
        channel: str,
        key: str,
        text: str,
        symbol: str,
        reply_to_message_id: int | None = None,
    ) -> int | None:
        try:
            message_id = self._send_message(channel=channel, text=text, reply_to_message_id=reply_to_message_id)
        except Exception as exc:
            self._record_send_failure(
                event_type="telegram_critical_send_failed",
                channel=channel,
                key=key,
                symbol=symbol,
                text=text,
                exc=exc,
                reply_to_message_id=reply_to_message_id,
            )
            return None
        self._record_send_success(channel=channel, key=key, symbol=symbol, message_id=message_id)
        return message_id

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item.get("kind") == "stop":
                    return
                key = str(item.get("key") or "")
                now = time.monotonic()
                if now - self._last_sent.get(key, 0.0) < self._config.cooldown_seconds:
                    continue
                channel = str(item.get("channel") or "events")
                text = str(item.get("text") or "")
                symbol = str(item.get("symbol") or "__telegram__")
                reply_to_message_id = item.get("reply_to_message_id")
                message_id = self._send_message(
                    channel=channel,
                    text=text,
                    reply_to_message_id=reply_to_message_id if isinstance(reply_to_message_id, int) else None,
                )
                self._last_sent[key] = now
                self._record_send_success(channel=channel, key=key, symbol=symbol, message_id=message_id)
            except Exception as exc:
                self._record_send_failure(
                    event_type="telegram_async_send_failed",
                    channel=str(item.get("channel") or "events"),
                    key=str(item.get("key") or ""),
                    symbol=str(item.get("symbol") or "__telegram__"),
                    text=str(item.get("text") or ""),
                    exc=exc,
                    reply_to_message_id=item.get("reply_to_message_id") if isinstance(item.get("reply_to_message_id"), int) else None,
                )
            finally:
                self._queue.task_done()

    def _send_message(self, *, channel: str, text: str, reply_to_message_id: int | None) -> int | None:
        if channel == "positions":
            token = self._config.positions_bot_token
            chat_id = self._config.positions_chat_id
        else:
            token = self._config.events_bot_token
            chat_id = self._config.events_chat_id
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = json.loads(response.read().decode("utf-8"))
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        return int(message_id) if message_id is not None else None

    def _record_send_success(self, *, channel: str, key: str, symbol: str, message_id: int | None) -> None:
        if self._event_writer is None:
            return
        self._event_writer(
            Live2Event(
                event_type="telegram_message_sent",
                component=Live2Component.RUNNER,
                symbol=symbol,
                message="telegram message sent",
                data={"channel": channel, "key": key, "message_id": message_id or ""},
            )
        )

    def _record_send_failure(
        self,
        *,
        event_type: str,
        channel: str,
        key: str,
        symbol: str,
        text: str,
        exc: Exception,
        reply_to_message_id: int | None,
    ) -> None:
        self._logger(f"telegram: сообщение не отправлено, причина: {exc}")
        if self._event_writer is None:
            return
        self._event_writer(
            Live2Event(
                event_type=event_type,
                component=Live2Component.RUNNER,
                severity=Live2Severity.ERROR,
                symbol=symbol,
                message=str(exc)[:500],
                data={
                    "channel": channel,
                    "key": key,
                    "reply_to_message_id": reply_to_message_id or "",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "text_preview": text[:240],
                },
            )
        )


def format_entry_message(decision: Live2DecisionRecord) -> str:
    entry_price = float(decision.execution_entry_fill_price or 0.0)
    signal_entry = float(decision.signal_entry_price or 0.0)
    drift_pct = _safe_divide(entry_price - signal_entry, signal_entry)
    tp1_price = float(decision.tp1_at_decision or 0.0)
    stop_price = float(decision.execution_stop_price or decision.initial_stop_at_decision or 0.0)
    tp_pct = _safe_divide(tp1_price - entry_price, entry_price)
    sl_pct = _safe_divide(entry_price - stop_price, entry_price)
    return (
        f"{symbol_emoji(decision.symbol)} <b>{telegram_symbol_link(decision.symbol)} LONG</b>\n\n"
        f"Сигнал: {format_price(signal_entry)}\n\n"
        f"Вход: {format_percent(drift_pct, signed=True)}\n\n"
        f"TP: {format_price(tp1_price)} {format_percent(tp_pct)}\n"
        f"SL: {format_price(stop_price)} {format_percent(sl_pct)}\n\n"
        f"live2 · {telegram_escape(decision.category_id or 'category_unknown')} · "
        f"fill {format_price(entry_price)} · amount {format_price(float(decision.execution_entry_filled_amount or 0.0))}"
    )


def format_tp1_message(action: Live2PositionSupervisorAction) -> str:
    tp1_fill = _dict(action.data.get("tp1_fill"))
    position = _dict(action.data.get("position"))
    fill_price = _float_or_none(tp1_fill.get("average_price"))
    filled_amount = _float_or_none(tp1_fill.get("filled_amount"))
    realized_pnl = _float_or_none(action.data.get("realized_pnl_usdt"))
    if realized_pnl is None:
        realized_pnl = _float_or_none(position.get("realized_pnl_usdt"))
    if action.event_type == "position_tp1_full_close_verified":
        return (
            f"{symbol_emoji(action.symbol)} <b>{telegram_symbol_link(action.symbol)} TP1 full "
            f"{format_usdt(realized_pnl or 0.0)} USDT</b>\n\n"
            f"Выход: {format_price(fill_price)} · amount {format_price(filled_amount)}\n"
            "Позиция: закрыта полностью\n"
            "Stop: старый initial stop отменён"
        )

    breakeven_stop = _float_or_none(action.data.get("breakeven_stop_price"))
    remaining_amount = _float_or_none(action.data.get("remaining_amount"))
    return (
        f"{symbol_emoji(action.symbol)} <b>{telegram_symbol_link(action.symbol)} TP1 "
        f"{format_usdt(realized_pnl or 0.0)} USDT</b>\n\n"
        f"Выход: {format_price(fill_price)} · amount {format_price(filled_amount)}\n"
        f"BE: {format_price(breakeven_stop)}\n"
        f"Остаток: {format_price(remaining_amount)}"
    )


def format_final_close_message(action: Live2PositionSupervisorAction) -> str:
    position = _dict(action.data.get("position"))
    reason = str(action.data.get("reason") or position.get("close_reason") or action.message or "unknown")
    realized_pnl = _float_or_none(position.get("realized_pnl_usdt"))
    pnl_line = f"PNL: {format_usdt(realized_pnl)} USDT\n" if realized_pnl is not None else "PNL: n/a\n"
    return (
        f"{symbol_emoji(action.symbol)} <b>{telegram_symbol_link(action.symbol)} позиция закрыта</b>\n\n"
        f"{pnl_line}\n"
        f"Причина: {telegram_code(reason)}"
    )


def format_integrity_message(*, symbol: str, reason: str, position_id: str = "") -> str:
    return (
        f"{symbol_emoji(symbol)} <b>{telegram_symbol_link(symbol)} Live2 остановлен</b>\n\n"
        f"<b>Проблема ордера/позиции</b>\n{telegram_code(reason[:600])}\n\n"
        f"ID: {telegram_code(position_id or 'unknown')}\n"
        "Режим: strict"
    )


def symbol_emoji(symbol: str) -> str:
    compact = compact_symbol(symbol).upper()
    digest = hashlib.sha256(compact.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(ANIMAL_EMOJIS)
    return ANIMAL_EMOJIS[index]


def compact_symbol(symbol: str) -> str:
    raw = str(symbol).upper().strip()
    compact = raw.split(":", 1)[0]
    if "/" in compact:
        compact = compact.split("/", 1)[0]
    else:
        for suffix in QUOTE_SYMBOL_SUFFIXES:
            if compact.endswith(suffix) and len(compact) > len(suffix):
                compact = compact[: -len(suffix)]
                break
    return compact or raw


def coinglass_url(symbol: str) -> str:
    return f"https://www.coinglass.com/tv/Binance_{compact_symbol(symbol)}USDT"


def telegram_symbol_link(symbol: str) -> str:
    compact = telegram_escape(compact_symbol(symbol))
    url = html.escape(coinglass_url(symbol), quote=True)
    return f'<a href="{url}">{compact}</a>'


def telegram_escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def telegram_code(value: object) -> str:
    return f"<code>{telegram_escape(value)}</code>"


def format_price(value: object) -> str:
    parsed = _float_or_none(value)
    if parsed is None:
        return "n/a"
    return f"{parsed:.6g}"


def format_percent(value: object, *, signed: bool = False, precision: int = 1) -> str:
    parsed = _float_or_none(value)
    if parsed is None:
        return "n/a"
    pct = parsed * 100.0
    sign = "+" if signed and pct >= 0.0 else ""
    return f"{sign}{pct:.{precision}f}%"


def format_usdt(value: object) -> str:
    parsed = _float_or_none(value)
    if parsed is None:
        return "n/a"
    sign = "+" if parsed >= 0.0 else "-"
    rendered = f"{abs(parsed):.3f}".rstrip("0").rstrip(".")
    return f"{sign}{rendered.replace('.', ',')}"


def _safe_divide(numerator: float, denominator: float) -> float | None:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or abs(denominator) <= 1e-12:
        return None
    return numerator / denominator


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
