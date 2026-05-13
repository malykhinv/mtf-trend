"""Strict REST-only micro-live runner for the anomaly wake-up research strategy."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from uuid import uuid4
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.exchanges.ccxt_types import ExchangeTickerSnapshot
from data.storage.parquet_storage import ParquetStorage
from domain.exceptions import ExchangeConnectivityError
from domain.enums.timeframe import Timeframe
from research_tools.anomaly_continuation_lab import compute_start_verticality_metrics
from research_tools.anomaly_config import ANOMALY_LIVE_TIMEFRAME_PAIRS


REQUIRED_PRICE_COLUMNS = ("timestamp", "open", "high", "low", "close")
REQUIRED_FLOW_COLUMNS = ("quote_volume", "number_of_trades")
OPTIONAL_FLOW_COLUMNS = ("taker_buy_quote_volume",)
HOUR_MS = 60 * 60 * 1000
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
SERVICE_WARNING_EMOJI = "⚠️"
SERVICE_WORK_EMOJI = "🚧"
TOP_GROWTH_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "rank",
    "symbol",
    "growth_pct",
    "growth_fraction",
    "open",
    "close",
    "high",
    "low",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
    "threshold_pct",
    "timeframe",
    "source",
)
TOP_GROWTH_STATUS_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "symbol",
    "status",
    "reason",
    "growth_pct",
    "growth_fraction",
    "open",
    "close",
    "high",
    "low",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
    "candle_timestamp_ms",
    "timeframe",
)
TOP_GROWTH_INDEX_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "top_count",
    "symbols_total",
    "ok_count",
    "failed_count",
    "threshold_pct",
    "limit",
    "top_file",
    "status_file",
)
LIVE_LEDGER_COLUMNS = (
    "position_id",
    "status",
    "symbol",
    "signal_category_id",
    "signal_category_label",
    "session",
    "opened_at_utc",
    "closed_at_utc",
    "entry_price",
    "signal_entry_price",
    "entry_fill_timestamp_ms",
    "entry_order_submitted_at_ms",
    "entry_order_status",
    "stop_price",
    "tp1_price",
    "amount",
    "entry_filled_amount",
    "entry_cost_usdt",
    "entry_fee_usdt",
    "pre_position_amount",
    "post_position_amount",
    "position_delta_amount",
    "notional_usdt",
    "risk_usdt",
    "realized_pnl_usdt",
    "realized_pnl_pct",
    "signal_json",
    "entry_order_id",
    "stop_order_id",
    "telegram_open_message_id",
    "telegram_stop_message_id",
    "telegram_close_message_id",
    "close_reason",
)


class LiveStartupError(RuntimeError):
    """Expected startup validation error for clean CLI output."""


class LiveDataIntegrityError(RuntimeError):
    """Live artifact/data integrity failure that must not be hidden as a network issue."""


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    events_bot_token: str
    events_chat_id: str
    positions_bot_token: str
    positions_chat_id: str


@dataclass(frozen=True, slots=True)
class LivePumpCategory:
    category_id: str
    label: str
    priority: int
    max_start_quote_ratio: float | None = None
    max_start_trade_ratio: float | None = None
    max_start_avg_trade_quote_size_ratio: float | None = None
    max_start_quote_ratio_per_abs_return: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = None
    min_next_taker_buy_quote_share: float | None = None
    max_price_retention: float | None = None


@dataclass(frozen=True, slots=True)
class LiveOiChangeResult:
    value: float | None
    reason: str | None = None


SUPPORTED_LIVE_PUMP_CATEGORIES: dict[str, LivePumpCategory] = {
    "balanced_market": LivePumpCategory(
        category_id="balanced_market",
        label="balanced market",
        priority=1,
    ),
    "mild_market": LivePumpCategory(
        category_id="mild_market",
        label="mild market",
        priority=2,
        max_start_quote_ratio=120.0,
        max_start_trade_ratio=60.0,
        max_start_avg_trade_quote_size_ratio=10.0,
        max_start_quote_ratio_per_abs_return=30_000.0,
        max_start_range_pct_ratio_to_baseline=35.0,
        min_next_taker_buy_quote_share=0.46,
        max_price_retention=0.98,
    ),
}


@dataclass(frozen=True, slots=True)
class LiveAnomalyConfig:
    results_dir: Path
    symbols: tuple[str, ...]
    confirm_real_orders: bool
    cache_dir: Path | None = None
    timeframe_pairs: tuple[tuple[Timeframe, Timeframe], ...] = ANOMALY_LIVE_TIMEFRAME_PAIRS
    pump_categories: tuple[str, ...] = ("balanced_market", "mild_market")
    baseline_candles: int = 60
    confirmation_candles: int = 4
    min_quote_ratio_start: float = 5.0
    min_trade_ratio_start: float = 5.0
    max_start_quote_ratio: float | None = 80.0
    max_start_trade_ratio: float | None = 40.0
    max_start_avg_trade_quote_size_ratio: float | None = 7.0
    max_start_quote_ratio_per_abs_return: float | None = 15_000.0
    max_start_range_pct_ratio_to_baseline: float | None = 25.0
    min_next_taker_buy_quote_share: float | None = 0.48
    max_price_retention: float | None = 0.96
    min_price_retention: float = 0.70
    min_verticality_score: float = 0.25
    min_hold_count: int = 2
    min_oi_change_pct_3x5m: float | None = 0.05
    max_initial_risk_pct: float = 0.16
    stop_buffer_range_fraction: float = 0.05
    max_prior_up_down_whipsaw_to_impulse_range: float | None = 0.60
    position_notional_usdt: float = 12.0
    max_open_positions: int = 3
    symbol_batch_size: int = 20
    inactive_scan_slots_per_cycle: int | None = None
    scan_hot_timeframes_per_symbol: bool = True
    active_symbol_ttl_ms: int = 60_000
    ticker_radar_enabled: bool = True
    ticker_radar_interval_seconds: float = 5.0
    ticker_radar_watch_ttl_ms: int = 120_000
    ticker_radar_watch_batch_size: int = 5
    ticker_radar_max_promotions_per_cycle: int = 20
    ticker_radar_min_price_delta_pct: float = 0.003
    ticker_radar_min_quote_volume_delta_usdt: float = 10_000.0
    ticker_radar_min_quote_volume_delta_ratio: float = 3.0
    signal_scan_backfill_candles: int = 10
    max_signal_age_ms: int = 60_000
    max_entry_price_drift_pct: float = 0.003
    min_executable_rr_to_signal_tp1: float = 0.75
    max_position_amount_slippage_ratio: float = 0.05
    max_monitor_empty_ohlcv_cycles: int = 3
    scan_sleep_seconds: float = 2.0
    network_sleep_seconds: float = 30.0
    live_ohlcv_cache_enabled: bool = True
    live_ohlcv_cache_write_enabled: bool = True
    max_cycles: int | None = None
    stop_cooldown_hours: float = 12.0
    stop_limit_per_symbol: int = 2
    telegram_cooldown_seconds: float = 900.0
    oi_fresh_ms: int = 5 * 60 * 1000
    trail_lookback_candles: int = 5
    trail_buffer_r: float = 0.10
    order_reconcile_interval_cycles: int = 10
    order_reconcile_batch_size: int = 25


@dataclass(slots=True)
class LiveSignal:
    category_id: str
    category_label: str
    category_priority: int
    symbol: str
    levels_timeframe: Timeframe
    entry_timeframe: Timeframe
    setup_source: str
    setup_elapsed_fraction: float
    setup_closed_entry_candles: int
    decision_timestamp_ms: int
    start_timestamp_ms: int
    session: str
    entry_price: float
    stop_price: float
    tp1_price: float
    box_high: float
    initial_risk: float
    initial_risk_pct: float
    quote_ratio_start: float
    trade_ratio_start: float
    price_retention: float
    hold_count: int
    verticality_score: float
    oi_change_pct_3x5m: float | None
    category_rejections: list[dict[str, object]] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, allow_nan=False)


@dataclass(slots=True)
class LiveActiveSymbol:
    symbol: str
    reason: str
    expires_at_ms: int
    updated_at_ms: int
    decision_timestamp_ms: int | None = None


@dataclass(slots=True)
class LiveTickerRadarWatch:
    symbol: str
    reason: str
    expires_at_ms: int
    updated_at_ms: int
    score: float
    price_delta_pct: float
    quote_volume_delta: float
    quote_volume_delta_ratio: float | None


@dataclass(slots=True)
class LivePosition:
    position_id: str
    signal: LiveSignal
    amount: float
    notional_usdt: float
    risk_usdt: float
    entry_order_id: str
    stop_order_id: str
    opened_at_utc: str
    opened_at_ms: int
    entry_price: float
    stop_price: float
    tp1_price: float
    initial_risk: float
    entry_fill_timestamp_ms: int
    entry_order_submitted_at_ms: int
    entry_order_status: str
    entry_filled_amount: float
    entry_cost_usdt: float | None
    entry_fee_usdt: float | None
    pre_position_amount: float
    post_position_amount: float
    position_delta_amount: float
    telegram_open_message_id: int | None = None
    telegram_stop_message_id: int | None = None
    remaining_amount: float = 0.0
    tp1_done: bool = False
    current_stop_price: float = 0.0
    realized_pnl_usdt: float = 0.0


class _LiveStatusLogger:
    """Console logger that keeps the live heartbeat on one mutable terminal line."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self._logger = logger
        self._inline_status_enabled = logger is print and sys.stdout.isatty()
        self._lock = threading.RLock()
        self._status_line_open = False
        self._status_line_length = 0

    @property
    def inline_status_enabled(self) -> bool:
        return self._inline_status_enabled

    def __call__(self, message: str) -> None:
        with self._lock:
            self._finish_status_line_if_needed()
            self._logger(message)

    def status(self, message: str) -> None:
        with self._lock:
            if not self._inline_status_enabled:
                self._logger(message)
                return
            padding = " " * max(0, self._status_line_length - len(message))
            sys.stdout.write(f"\r{message}{padding}")
            sys.stdout.flush()
            self._status_line_open = True
            self._status_line_length = len(message)

    def _finish_status_line_if_needed(self) -> None:
        if not self._inline_status_enabled or not self._status_line_open:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_line_open = False
        self._status_line_length = 0


class TelegramDispatcher:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        logger: Callable[[str], None],
        cooldown_seconds: float,
        event_writer: Callable[[str, str, dict[str, object]], None] | None = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._cooldown_seconds = cooldown_seconds
        self._event_writer = event_writer
        self._queue: queue.Queue[dict[str, object]] = queue.Queue()
        self._last_sent: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, name="telegram-dispatcher", daemon=True)
        self._thread.start()

    def send(
        self,
        *,
        channel: str,
        key: str,
        text: str,
        reply_to_message_id: int | None = None,
        symbol: str = "__telegram__",
    ) -> None:
        self._queue.put(
            {
                "channel": channel,
                "key": key,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "symbol": symbol,
            }
        )

    def send_sync(self, *, channel: str, text: str, reply_to_message_id: int | None = None) -> int | None:
        return self._send_message(channel=channel, text=text, reply_to_message_id=reply_to_message_id)

    def edit_sync(self, *, channel: str, message_id: int, text: str) -> int | None:
        return self._edit_message(channel=channel, message_id=message_id, text=text)

    def send_photo(self, *, channel: str, photo_path: Path, caption: str, reply_to_message_id: int | None = None) -> bool:
        try:
            self._send_photo(channel=channel, photo_path=photo_path, caption=caption, reply_to_message_id=reply_to_message_id)
            return True
        except Exception as exc:
            self._logger(f"telegram: график не отправлен, причина: {exc}")
            self._append_event(
                "telegram_photo_send_failed",
                "__telegram__",
                {
                    "channel": channel,
                    "photo_path": str(photo_path),
                    "reply_to_message_id": reply_to_message_id or "",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                },
            )
            return False

    def send_photo_sync(self, *, channel: str, photo_path: Path, caption: str, reply_to_message_id: int | None = None) -> int | None:
        return self._send_photo(channel=channel, photo_path=photo_path, caption=caption, reply_to_message_id=reply_to_message_id)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                key = str(item["key"])
                now = time.monotonic()
                if now - self._last_sent.get(key, 0.0) < self._cooldown_seconds:
                    continue
                message_id = self._send_message(
                    channel=str(item["channel"]),
                    text=str(item["text"]),
                    reply_to_message_id=item.get("reply_to_message_id")
                    if isinstance(item.get("reply_to_message_id"), int)
                    else None,
                )
                self._last_sent[key] = now
                if message_id is not None:
                    item["message_id"] = message_id
            except Exception as exc:
                self._logger(f"telegram: сообщение не отправлено, причина: {exc}")
                self._append_event(
                    "telegram_async_send_failed",
                    str(item.get("symbol") or "__telegram__"),
                    {
                        "channel": str(item.get("channel") or ""),
                        "key": str(item.get("key") or ""),
                        "reply_to_message_id": item.get("reply_to_message_id")
                        if isinstance(item.get("reply_to_message_id"), int)
                        else "",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                        "text_preview": str(item.get("text") or "")[:240],
                    },
                )
            finally:
                self._queue.task_done()

    def _append_event(self, event: str, symbol: str, details: dict[str, object]) -> None:
        if self._event_writer is None:
            return
        try:
            self._event_writer(event, symbol, details)
        except Exception as exc:
            self._logger(f"telegram: artifact event не записан, причина: {exc}")

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

    def _edit_message(self, *, channel: str, message_id: int, text: str) -> int | None:
        if channel == "positions":
            token = self._config.positions_bot_token
            chat_id = self._config.positions_chat_id
        else:
            token = self._config.events_bot_token
            chat_id = self._config.events_chat_id
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/editMessageText", data=data)
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = json.loads(response.read().decode("utf-8"))
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            return None
        edited_id = result.get("message_id")
        return int(edited_id) if edited_id is not None else None

    def _send_photo(self, *, channel: str, photo_path: Path, caption: str, reply_to_message_id: int | None) -> int | None:
        token = self._config.positions_bot_token if channel == "positions" else self._config.events_bot_token
        chat_id = self._config.positions_chat_id if channel == "positions" else self._config.events_chat_id
        boundary = f"----codex{uuid4().hex}"
        fields: dict[str, object] = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        if reply_to_message_id is not None:
            fields["reply_to_message_id"] = reply_to_message_id
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{photo_path.name}\"\r\nContent-Type: image/png\r\n\r\n".encode())
        body.extend(photo_path.read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = json.loads(response.read().decode("utf-8"))
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        return int(message_id) if message_id is not None else None


class LiveArtifactWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.root / "live_positions.csv"
        self.events_path = self.root / "live_events.csv"
        self.top_growth_dir = self.root / "top_growth"
        self.top_growth_dir.mkdir(parents=True, exist_ok=True)
        self.top_growth_index_path = self.top_growth_dir / "top_growth_index.csv"
        self._lock = threading.Lock()
        self._ensure_csv(self.ledger_path, LIVE_LEDGER_COLUMNS)
        self._ensure_csv(self.events_path, ("timestamp_utc", "event", "symbol", "details_json"))
        self._ensure_csv(self.top_growth_index_path, TOP_GROWTH_INDEX_COLUMNS)

    @staticmethod
    def _ensure_csv(path: Path, columns: tuple[str, ...]) -> None:
        if path.exists():
            return
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=list(columns)).writeheader()

    def write_top_growth_snapshot(
        self,
        *,
        period_start_ms: int,
        period_end_ms: int,
        snapshot_utc: str,
        top_rows: list[dict[str, object]],
        status_rows: list[dict[str, object]],
        symbols_total: int,
        threshold_pct: float,
        limit: int,
    ) -> tuple[Path, Path]:
        period_start_utc = datetime.fromtimestamp(period_start_ms / 1000, UTC).isoformat()
        period_end_utc = datetime.fromtimestamp(period_end_ms / 1000, UTC).isoformat()
        stamp = datetime.fromtimestamp(period_start_ms / 1000, UTC).strftime("%Y%m%d_%H0000_UTC")
        top_path = self.top_growth_dir / f"top_growth_{stamp}.csv"
        status_path = self.top_growth_dir / f"top_growth_status_{stamp}.csv"
        enriched_top_rows = [
            {
                "snapshot_utc": snapshot_utc,
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                **row,
            }
            for row in top_rows
        ]
        enriched_status_rows = [
            {
                "snapshot_utc": snapshot_utc,
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                **row,
            }
            for row in status_rows
        ]
        ok_count = sum(1 for row in enriched_status_rows if row.get("status") == "ok")
        failed_count = len(enriched_status_rows) - ok_count
        index_row = {
            "snapshot_utc": snapshot_utc,
            "period_start_utc": period_start_utc,
            "period_end_utc": period_end_utc,
            "top_count": len(enriched_top_rows),
            "symbols_total": symbols_total,
            "ok_count": ok_count,
            "failed_count": failed_count,
            "threshold_pct": threshold_pct,
            "limit": limit,
            "top_file": top_path.name,
            "status_file": status_path.name,
        }
        with self._lock:
            self._write_csv_atomic(top_path, TOP_GROWTH_COLUMNS, enriched_top_rows)
            self._write_csv_atomic(status_path, TOP_GROWTH_STATUS_COLUMNS, enriched_status_rows)
            with self.top_growth_index_path.open("a", newline="", encoding="utf-8-sig") as handle:
                csv.DictWriter(handle, fieldnames=list(TOP_GROWTH_INDEX_COLUMNS)).writerow(index_row)
        return top_path, status_path

    @staticmethod
    def _write_csv_atomic(path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        tmp_path.replace(path)

    def append_event(self, event: str, symbol: str, details: dict[str, object]) -> None:
        try:
            details_json = json.dumps(details, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except ValueError as exc:
            raise LiveDataIntegrityError(
                f"Non-finite live event details: event={event} symbol={symbol} error={exc}"
            ) from exc
        with self._lock, self.events_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp_utc", "event", "symbol", "details_json"])
            writer.writerow(
                {
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "event": event,
                    "symbol": symbol,
                    "details_json": details_json,
                }
            )

    def append_position(self, position: LivePosition, *, status: str = "open") -> None:
        signal = position.signal
        with self._lock, self.ledger_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(LIVE_LEDGER_COLUMNS))
            writer.writerow(
                {
                    "position_id": position.position_id,
                    "status": status,
                    "symbol": signal.symbol,
                    "signal_category_id": signal.category_id,
                    "signal_category_label": signal.category_label,
                    "session": signal.session,
                    "opened_at_utc": position.opened_at_utc,
                    "closed_at_utc": "",
                    "entry_price": position.entry_price,
                    "signal_entry_price": signal.entry_price,
                    "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                    "entry_order_submitted_at_ms": position.entry_order_submitted_at_ms,
                    "entry_order_status": position.entry_order_status,
                    "stop_price": position.stop_price,
                    "tp1_price": position.tp1_price,
                    "amount": position.amount,
                    "entry_filled_amount": position.entry_filled_amount,
                    "entry_cost_usdt": position.entry_cost_usdt if position.entry_cost_usdt is not None else "",
                    "entry_fee_usdt": position.entry_fee_usdt if position.entry_fee_usdt is not None else "",
                    "pre_position_amount": position.pre_position_amount,
                    "post_position_amount": position.post_position_amount,
                    "position_delta_amount": position.position_delta_amount,
                    "notional_usdt": position.notional_usdt,
                    "risk_usdt": position.risk_usdt,
                    "realized_pnl_usdt": "",
                    "realized_pnl_pct": "",
                    "signal_json": signal.to_json(),
                    "entry_order_id": position.entry_order_id,
                    "stop_order_id": position.stop_order_id,
                    "telegram_open_message_id": position.telegram_open_message_id or "",
                    "telegram_stop_message_id": position.telegram_stop_message_id or "",
                    "telegram_close_message_id": "",
                    "close_reason": "",
                }
            )

    def append_position_close(
        self,
        position: LivePosition,
        *,
        reason: str,
        pnl_usdt: float,
        pnl_pct: float,
    ) -> None:
        self._append_position_terminal_row(
            position,
            status="closed",
            reason=reason,
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
        )

    def append_position_exit_unresolved(self, position: LivePosition, *, reason: str) -> None:
        self._append_position_terminal_row(
            position,
            status="exit_unresolved",
            reason=reason,
            pnl_usdt=None,
            pnl_pct=None,
        )

    def _append_position_terminal_row(
        self,
        position: LivePosition,
        *,
        status: str,
        reason: str,
        pnl_usdt: float | None,
        pnl_pct: float | None,
    ) -> None:
        signal = position.signal
        with self._lock, self.ledger_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(LIVE_LEDGER_COLUMNS))
            writer.writerow(
                {
                    "position_id": position.position_id,
                    "status": status,
                    "symbol": signal.symbol,
                    "signal_category_id": signal.category_id,
                    "signal_category_label": signal.category_label,
                    "session": signal.session,
                    "opened_at_utc": position.opened_at_utc,
                    "closed_at_utc": datetime.now(UTC).isoformat(),
                    "entry_price": position.entry_price,
                    "signal_entry_price": signal.entry_price,
                    "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                    "entry_order_submitted_at_ms": position.entry_order_submitted_at_ms,
                    "entry_order_status": position.entry_order_status,
                    "stop_price": position.stop_price,
                    "tp1_price": position.tp1_price,
                    "amount": position.amount,
                    "entry_filled_amount": position.entry_filled_amount,
                    "entry_cost_usdt": position.entry_cost_usdt if position.entry_cost_usdt is not None else "",
                    "entry_fee_usdt": position.entry_fee_usdt if position.entry_fee_usdt is not None else "",
                    "pre_position_amount": position.pre_position_amount,
                    "post_position_amount": position.post_position_amount,
                    "position_delta_amount": position.position_delta_amount,
                    "notional_usdt": position.notional_usdt,
                    "risk_usdt": position.risk_usdt,
                    "realized_pnl_usdt": pnl_usdt if pnl_usdt is not None else "",
                    "realized_pnl_pct": pnl_pct if pnl_pct is not None else "",
                    "signal_json": signal.to_json(),
                    "entry_order_id": position.entry_order_id,
                    "stop_order_id": position.stop_order_id,
                    "telegram_open_message_id": position.telegram_open_message_id or "",
                    "telegram_stop_message_id": position.telegram_stop_message_id or "",
                    "telegram_close_message_id": "",
                    "close_reason": reason,
                }
            )


@dataclass(frozen=True, slots=True)
class TopGrowthSnapshotConfig:
    results_dir: Path
    symbols: tuple[str, ...]
    period_start_ms: int | None = None
    min_return_pct: float = 0.10
    limit: int = 5
    fetch_spacing_seconds: float = 0.05


class TopGrowthSnapshotRunner:
    def __init__(
        self,
        *,
        config: TopGrowthSnapshotConfig,
        exchange_client: CcxtFuturesClient,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.exchange = exchange_client
        self.logger = logger
        self.artifacts = LiveArtifactWriter(
            config.results_dir / "top_growth_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        )

    def run(self) -> int:
        _validate_top_growth_config_values(self.config)
        symbols = list(self.config.symbols) or self.exchange.list_usdt_swap_symbols()
        if not symbols:
            raise LiveStartupError("Нет символов для top-growth snapshot")
        now_ms = int(time.time() * 1000)
        period_start_ms = self.config.period_start_ms
        if period_start_ms is None:
            period_start_ms = _previous_closed_hour_start_ms(now_ms)
        period_end_ms = period_start_ms + HOUR_MS
        if period_end_ms > now_ms:
            raise LiveStartupError("top-growth period должен быть полностью закрытым 1h интервалом")
        snapshot_utc = datetime.now(UTC).isoformat()
        self.artifacts.append_event(
            "top_growth_snapshot_started",
            "__top_growth__",
            {
                "period_start_ms": period_start_ms,
                "period_end_ms": period_end_ms,
                "symbols_total": len(symbols),
                "threshold_pct": self.config.min_return_pct * 100.0,
                "limit": self.config.limit,
                "source": "standalone_command",
            },
        )
        top_rows, status_rows = _collect_top_growth_snapshot(
            exchange=self.exchange,
            symbols=tuple(symbols),
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            snapshot_utc=snapshot_utc,
            threshold_fraction=self.config.min_return_pct,
            limit=self.config.limit,
            fetch_spacing_seconds=self.config.fetch_spacing_seconds,
        )
        top_path, status_path = self.artifacts.write_top_growth_snapshot(
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            snapshot_utc=snapshot_utc,
            top_rows=top_rows,
            status_rows=status_rows,
            symbols_total=len(symbols),
            threshold_pct=self.config.min_return_pct * 100.0,
            limit=self.config.limit,
        )
        self.artifacts.append_event(
            "top_growth_snapshot_saved",
            "__top_growth__",
            {
                "period_start_ms": period_start_ms,
                "period_end_ms": period_end_ms,
                "top_count": len(top_rows),
                "symbols_total": len(symbols),
                "ok_count": sum(1 for row in status_rows if row.get("status") == "ok"),
                "failed_count": sum(1 for row in status_rows if row.get("status") != "ok"),
                "top_file": str(top_path.relative_to(self.artifacts.root)),
                "status_file": str(status_path.relative_to(self.artifacts.root)),
                "source": "standalone_command",
            },
        )
        period_start_utc = datetime.fromtimestamp(period_start_ms / 1000, UTC).isoformat()
        self.logger(
            "top-growth: "
            f"{period_start_utc} · top {len(top_rows)} · "
            f"ok {sum(1 for row in status_rows if row.get('status') == 'ok')}/{len(status_rows)} · "
            f"артефакты {self.artifacts.root}"
        )
        return 0


def _collect_top_growth_snapshot(
    *,
    exchange: CcxtFuturesClient,
    symbols: tuple[str, ...],
    period_start_ms: int,
    period_end_ms: int,
    snapshot_utc: str,
    threshold_fraction: float,
    limit: int,
    fetch_spacing_seconds: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    threshold_pct = threshold_fraction * 100.0
    candidates: list[dict[str, object]] = []
    status_rows: list[dict[str, object]] = []
    for symbol in symbols:
        status_row = _load_top_growth_symbol_row(
            exchange=exchange,
            symbol=symbol,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            snapshot_utc=snapshot_utc,
        )
        status_rows.append(status_row)
        if status_row.get("status") == "ok":
            growth_fraction = _row_float_or_none(status_row, "growth_fraction")
            if growth_fraction is not None and growth_fraction >= threshold_fraction:
                candidates.append(
                    {
                        "symbol": symbol,
                        "growth_pct": status_row.get("growth_pct", ""),
                        "growth_fraction": status_row.get("growth_fraction", ""),
                        "open": status_row.get("open", ""),
                        "close": status_row.get("close", ""),
                        "high": status_row.get("high", ""),
                        "low": status_row.get("low", ""),
                        "quote_volume": status_row.get("quote_volume", ""),
                        "number_of_trades": status_row.get("number_of_trades", ""),
                        "taker_buy_quote_volume": status_row.get("taker_buy_quote_volume", ""),
                        "threshold_pct": threshold_pct,
                        "timeframe": Timeframe.H1.value,
                        "source": "exchange_1h_closed_candle",
                    }
                )
        spacing = float(fetch_spacing_seconds)
        if math.isfinite(spacing) and spacing > 0.0:
            time.sleep(spacing)
    candidates.sort(
        key=lambda row: (
            _row_float_or_none(row, "growth_fraction") or -math.inf,
            _row_float_or_none(row, "quote_volume") or -math.inf,
        ),
        reverse=True,
    )
    top_rows: list[dict[str, object]] = []
    for rank, row in enumerate(candidates[:limit], start=1):
        growth_fraction = _row_float_or_none(row, "growth_fraction")
        top_rows.append({"rank": rank, "growth_multiple": (1.0 + growth_fraction) if growth_fraction is not None else "", **row})
    return top_rows, status_rows


def _load_top_growth_symbol_row(
    *,
    exchange: CcxtFuturesClient,
    symbol: str,
    period_start_ms: int,
    period_end_ms: int,
    snapshot_utc: str,
) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": symbol,
        "status": "failed",
        "reason": "unknown",
        "growth_pct": "",
        "growth_fraction": "",
        "open": "",
        "close": "",
        "high": "",
        "low": "",
        "quote_volume": "",
        "number_of_trades": "",
        "taker_buy_quote_volume": "",
        "candle_timestamp_ms": "",
        "timeframe": Timeframe.H1.value,
    }
    try:
        frame = exchange.fetch_ohlcv(symbol, Timeframe.H1, period_start_ms, period_end_ms - 1)
    except Exception as exc:
        return {**base, "reason": f"fetch_ohlcv_failed:{type(exc).__name__}:{str(exc)[:160]}"}
    if frame.empty:
        return {**base, "reason": "empty_ohlcv"}
    missing = [column for column in REQUIRED_PRICE_COLUMNS if column not in frame.columns]
    if missing:
        return {**base, "reason": "missing_price_columns:" + ",".join(missing)}
    work = frame.copy()
    work["timestamp"] = pd.to_numeric(work["timestamp"], errors="coerce")
    exact = work.loc[work["timestamp"].astype("Int64") == int(period_start_ms)]
    if exact.empty:
        timestamps = pd.to_numeric(work["timestamp"], errors="coerce").dropna().astype("int64")
        reason = "no_exact_hour_candle"
        if not timestamps.empty:
            reason += f":first={int(timestamps.min())}:last={int(timestamps.max())}"
        return {**base, "reason": reason}
    row = exact.sort_values("timestamp").iloc[-1]
    open_price = _series_float_or_none(row, "open")
    close_price = _series_float_or_none(row, "close")
    high_price = _series_float_or_none(row, "high")
    low_price = _series_float_or_none(row, "low")
    if open_price is None or close_price is None or high_price is None or low_price is None or open_price <= 0.0:
        return {
            **base,
            "reason": "invalid_price_values",
            "open": _finite_or_blank(open_price),
            "close": _finite_or_blank(close_price),
            "high": _finite_or_blank(high_price),
            "low": _finite_or_blank(low_price),
            "candle_timestamp_ms": int(period_start_ms),
        }
    growth_fraction = (close_price - open_price) / open_price
    return {
        **base,
        "status": "ok",
        "reason": "ok",
        "growth_pct": growth_fraction * 100.0,
        "growth_fraction": growth_fraction,
        "open": open_price,
        "close": close_price,
        "high": high_price,
        "low": low_price,
        "quote_volume": _finite_or_blank(_series_float_or_none(row, "quote_volume")),
        "number_of_trades": _finite_or_blank(_series_float_or_none(row, "number_of_trades")),
        "taker_buy_quote_volume": _finite_or_blank(_series_float_or_none(row, "taker_buy_quote_volume")),
        "candle_timestamp_ms": int(period_start_ms),
    }


def parse_top_growth_period_start_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LiveStartupError("period-start-utc должен быть ISO timestamp, например 2026-05-12T04:00:00Z") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc.minute != 0 or parsed_utc.second != 0 or parsed_utc.microsecond != 0:
        raise LiveStartupError("period-start-utc должен указывать на начало закрытой 1h свечи")
    return int(parsed_utc.timestamp() * 1000)


def _previous_closed_hour_start_ms(now_ms: int) -> int:
    return ((now_ms // HOUR_MS) - 1) * HOUR_MS


def _validate_top_growth_config_values(config: TopGrowthSnapshotConfig) -> None:
    if config.limit < 1:
        raise LiveStartupError(f"Некорректный top-growth config: limit должен быть >= 1, получено {config.limit!r}")
    _require_finite_config_number("top_growth_min_return_pct", config.min_return_pct, min_value=0.0, allow_equal_min=False)
    _require_finite_config_number("top_growth_fetch_spacing_seconds", config.fetch_spacing_seconds, min_value=0.0, allow_equal_min=True)


class AnomalyMicroLiveRunner:
    def __init__(
        self,
        *,
        config: LiveAnomalyConfig,
        telegram: TelegramConfig,
        exchange_client: CcxtFuturesClient,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.exchange = exchange_client
        self._status_logger = _LiveStatusLogger(logger)
        self.logger = self._status_logger
        self._pump_categories = _resolve_live_pump_categories(config.pump_categories)
        self.artifacts = LiveArtifactWriter(
            config.results_dir / "live_anomaly_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        )
        self.telegram = TelegramDispatcher(
            telegram,
            logger=logger,
            cooldown_seconds=config.telegram_cooldown_seconds,
            event_writer=self.artifacts.append_event,
        )
        self._open_positions: dict[str, LivePosition] = {}
        self._opening_symbols: set[str] = set()
        self._recent_stops: dict[str, list[float]] = {}
        self._active_symbols: dict[str, LiveActiveSymbol] = {}
        self._ticker_radar_watch: dict[str, LiveTickerRadarWatch] = {}
        self._ticker_radar_snapshots: dict[str, ExchangeTickerSnapshot] = {}
        self._ticker_radar_quote_delta_history: dict[str, deque[float]] = {}
        self._last_ticker_radar_at_ms = 0
        self._seen_decisions: set[tuple[str, str, str, int]] = set()
        self._last_signal_scan_closed_at: dict[tuple[str, str], int] = {}
        self._opened_positions_total = 0
        self._closed_positions_total = 0
        self._orphan_orders_cancelled_total = 0
        self._state_lock = threading.RLock()
        self._inactive_cursor = 0
        self._order_reconcile_cursor = 0
        self._network_degraded = False
        self._ohlcv_cache_storage = (
            ParquetStorage(base_dir=config.cache_dir)
            if config.live_ohlcv_cache_enabled and config.cache_dir is not None
            else None
        )
        self._live_ohlcv_frame_cache: dict[tuple[str, str], pd.DataFrame] = {}

    def run(self) -> int:
        self._validate_startup()
        symbols = list(self.config.symbols) or self.exchange.list_usdt_swap_symbols()
        if not symbols:
            raise LiveStartupError("Нет символов для live-обхода")
        category_ids = ",".join(category.category_id for category in self._pump_categories)
        timeframe_pairs_label = _format_timeframe_pairs(self.config.timeframe_pairs)
        self.logger(
            f"live: старт · символов {len(symbols)} · TF {timeframe_pairs_label} · max {self.config.max_open_positions}"
        )
        self.logger(f"live: артефакты {self.artifacts.root}")
        trading_mode = "с торговлей" if self.config.confirm_real_orders else "без торговли"
        self.artifacts.append_event(
            "live_cache_config",
            "__live__",
            {
                "live_ohlcv_cache_enabled": bool(self.config.live_ohlcv_cache_enabled),
                "live_ohlcv_cache_write_enabled": bool(self.config.live_ohlcv_cache_write_enabled),
                "cache_dir": str(self.config.cache_dir) if self.config.cache_dir is not None else "",
                "cache_provider": "parquet_tail_fetch_v1" if self._ohlcv_cache_storage is not None else "disabled",
            },
        )
        self.telegram.send(
            channel="events",
            key="live_started",
            text=(
                f"{SERVICE_WORK_EMOJI} <b>Запуск {trading_mode}</b>\n\n"
                f"{_telegram_code(timeframe_pairs_label)}"
            ),
        )
        cycle = 0
        while self.config.max_cycles is None or cycle < self.config.max_cycles:
            cycle += 1
            cycle_started = time.monotonic()
            try:
                opened_before, _, closed_before, orphan_before = self._live_counts()
                self._maybe_update_ticker_radar(symbols)
                batch = self._next_symbol_batch(symbols)
                signals = self._scan_batch(batch)
                for signal in signals:
                    self._maybe_open_position(signal)
                orphan_cancelled = self._reconcile_orphan_orders(symbols, cycle=cycle)
                cycle_seconds = time.monotonic() - cycle_started
                opened_total, active_positions, closed_total, orphan_total = self._live_counts()
                should_log_status = self._status_logger.inline_status_enabled or (
                    cycle == 1
                    or cycle % 10 == 0
                    or opened_total != opened_before
                    or closed_total != closed_before
                    or orphan_total != orphan_before
                )
                if should_log_status:
                    opened_delta = opened_total - opened_before
                    orphan_text = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
                    self._status_logger.status(
                        f"live: цикл {cycle_seconds:.1f}s · открыто {opened_total} (+{opened_delta}) · "
                        f"слежу {active_positions} · закрыто {closed_total}{orphan_text}"
                    )
                if self._network_degraded:
                    self.artifacts.append_event(
                        "network_recovered",
                        "__live__",
                        {"cycle": cycle, "cycle_seconds": cycle_seconds},
                    )
                self._network_degraded = False
                time.sleep(self.config.scan_sleep_seconds)
            except KeyboardInterrupt:
                orphan_cancelled = self._reconcile_orphan_orders(symbols, cycle=cycle, force=True)
                suffix = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
                self.logger(f"live: остановлено пользователем{suffix}")
                return 0
            except LiveDataIntegrityError as exc:
                self.logger(f"live: остановлено из-за ошибки целостности live-данных: {exc}")
                self.telegram.send(
                    channel="events",
                    key="live_data_integrity_error",
                    text=f"{SERVICE_WARNING_EMOJI} <b>Ошибка</b>\n\n{_telegram_code(str(exc)[:600])}",
                )
                return 3
            except ExchangeConnectivityError as exc:
                if not self._network_degraded:
                    self.logger(f"live: сеть/API недоступны, жду восстановления. Причина: {exc}")
                    self.artifacts.append_event(
                        "network_degraded",
                        "__live__",
                        {
                            "cycle": cycle,
                            "sleep_seconds": self.config.network_sleep_seconds,
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc)[:1000],
                        },
                    )
                    self.telegram.send(
                        channel="events",
                        key="network_degraded",
                        text=f"{SERVICE_WORK_EMOJI} <b>Пауза</b>\n\n{_telegram_code(str(exc)[:300])}",
                    )
                self._network_degraded = True
                time.sleep(self.config.network_sleep_seconds)
            except Exception as exc:
                self.logger(f"live: остановлено из-за внутренней ошибки: {type(exc).__name__}: {exc}")
                self.artifacts.append_event(
                    "live_internal_error",
                    "__live__",
                    {"exception_type": type(exc).__name__, "exception_message": str(exc)[:1000]},
                )
                self.telegram.send(
                    channel="events",
                    key="live_internal_error",
                    text=f"{SERVICE_WARNING_EMOJI} <b>Ошибка</b>\n\n{_telegram_code(type(exc).__name__ + ': ' + str(exc)[:500])}",
                )
                return 4
        orphan_cancelled = self._reconcile_orphan_orders(symbols, cycle=cycle, force=True)
        suffix = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
        self.logger(f"live: достигнут лимит циклов{suffix}")
        return 0

    def _validate_startup(self) -> None:
        if not self.config.confirm_real_orders:
            raise LiveStartupError("Для micro-live нужен явный флаг --confirm-real-orders")
        _validate_live_config_values(self.config)
        missing = []
        if not os.getenv("BINANCE_API_KEY"):
            missing.append("BINANCE_API_KEY")
        if not os.getenv("BINANCE_SECRET_KEY"):
            missing.append("BINANCE_SECRET_KEY")
        if missing:
            raise LiveStartupError(f"Не заполнены env переменные: {', '.join(missing)}")
        if not self._pump_categories:
            raise LiveStartupError("Не заданы live pump categories")
        try:
            balance = float(self.exchange.fetch_usdt_free_balance())
        except (TypeError, ValueError) as exc:
            raise LiveStartupError("Биржа вернула нечисловой free USDT balance") from exc
        if not math.isfinite(balance):
            raise LiveStartupError("Биржа вернула нечисловой free USDT balance")

    def _next_symbol_batch(self, symbols: list[str]) -> list[str]:
        now_ms = int(time.time() * 1000)
        active_due, active_waiting = self._active_symbol_batch(now_ms=now_ms)
        active_keys = {_position_symbol_key(symbol) for symbol in [*active_due, *active_waiting]}
        radar_due, radar_waiting = self._ticker_radar_batch(now_ms=now_ms, excluded_keys=active_keys)
        radar_due_keys = {_position_symbol_key(symbol) for symbol in radar_due}
        radar_waiting_keys = {_position_symbol_key(symbol) for symbol in radar_waiting}
        if self.config.inactive_scan_slots_per_cycle is None:
            inactive_slots = max(0, self.config.symbol_batch_size - len(active_due))
        else:
            inactive_slots = max(0, int(self.config.inactive_scan_slots_per_cycle))
        inactive: list[str] = []
        attempts = 0
        while len(inactive) < inactive_slots and attempts < len(symbols):
            symbol = symbols[self._inactive_cursor % len(symbols)]
            self._inactive_cursor += 1
            attempts += 1
            symbol_key = _position_symbol_key(symbol)
            with self._state_lock:
                symbol_is_opening = symbol_key in self._opening_symbols
            if (
                symbol_key in active_keys
                or symbol_key in radar_due_keys
                or symbol_key in radar_waiting_keys
                or symbol_is_opening
                or self._symbol_in_stop_cooldown(symbol)
            ):
                continue
            inactive.append(symbol)
        batch = [*active_due, *radar_due, *inactive]
        self.artifacts.append_event(
            "symbol_batch_selected",
            "__live__",
            {
                "active_symbols": active_due,
                "active_count": len(active_due),
                "active_waiting_count": len(active_waiting),
                "active_waiting_symbols": active_waiting,
                "ticker_radar_symbols": radar_due,
                "ticker_radar_count": len(radar_due),
                "ticker_radar_waiting_count": len(radar_waiting),
                "ticker_radar_waiting_symbols": radar_waiting,
                "inactive_count": len(inactive),
                "inactive_scan_slots_per_cycle": (
                    self.config.inactive_scan_slots_per_cycle
                    if self.config.inactive_scan_slots_per_cycle is not None
                    else ""
                ),
                "scan_hot_timeframes_per_symbol": bool(self.config.scan_hot_timeframes_per_symbol),
                "symbol_batch_size": self.config.symbol_batch_size,
                "effective_scan_count": len(batch),
            },
        )
        return batch

    def _active_symbol_batch(self, *, now_ms: int) -> tuple[list[str], list[str]]:
        with self._state_lock:
            expired = self._prune_active_symbols_locked(now_ms)
            symbols_by_key: dict[str, str] = {}
            for position in self._open_positions.values():
                symbols_by_key[_position_symbol_key(position.signal.symbol)] = position.signal.symbol
            for state in self._active_symbols.values():
                symbols_by_key[_position_symbol_key(state.symbol)] = state.symbol
            active_symbols = sorted(symbols_by_key.values())
        active_due: list[str] = []
        active_waiting: list[str] = []
        for symbol in active_symbols:
            if self._signal_scan_due_for_symbol(symbol, now_ms=now_ms):
                active_due.append(symbol)
            else:
                active_waiting.append(symbol)
        for state in expired:
            self.artifacts.append_event(
                "active_symbol_expired",
                state.symbol,
                {
                    "reason": state.reason,
                    "expires_at_ms": state.expires_at_ms,
                    "decision_timestamp_ms": state.decision_timestamp_ms if state.decision_timestamp_ms is not None else "",
                },
            )
        return active_due, active_waiting

    def _ticker_radar_batch(self, *, now_ms: int, excluded_keys: set[str]) -> tuple[list[str], list[str]]:
        if not self.config.ticker_radar_enabled or self.config.ticker_radar_watch_batch_size <= 0:
            return [], []
        with self._state_lock:
            expired = self._prune_ticker_radar_watch_locked(now_ms)
            watch_items = sorted(
                self._ticker_radar_watch.values(),
                key=lambda item: (item.score, item.updated_at_ms, item.symbol),
                reverse=True,
            )
        for item in expired:
            self.artifacts.append_event(
                "ticker_radar_watch_expired",
                item.symbol,
                {
                    "reason": item.reason,
                    "score": item.score,
                    "expires_at_ms": item.expires_at_ms,
                    "price_delta_pct": item.price_delta_pct,
                    "quote_volume_delta": item.quote_volume_delta,
                    "quote_volume_delta_ratio": item.quote_volume_delta_ratio if item.quote_volume_delta_ratio is not None else "",
                },
            )
        due: list[str] = []
        waiting: list[str] = []
        for item in watch_items:
            symbol_key = _position_symbol_key(item.symbol)
            with self._state_lock:
                symbol_is_opening = symbol_key in self._opening_symbols
            if symbol_key in excluded_keys or symbol_is_opening or self._symbol_in_stop_cooldown(item.symbol):
                continue
            if self._signal_scan_due_for_symbol(item.symbol, now_ms=now_ms):
                if len(due) < self.config.ticker_radar_watch_batch_size:
                    due.append(item.symbol)
            else:
                waiting.append(item.symbol)
        return due, waiting

    def _maybe_update_ticker_radar(self, symbols: list[str]) -> None:
        if not self.config.ticker_radar_enabled:
            return
        now_ms = int(time.time() * 1000)
        interval_ms = int(self.config.ticker_radar_interval_seconds * 1000.0)
        if now_ms - self._last_ticker_radar_at_ms < max(1, interval_ms):
            return
        self._last_ticker_radar_at_ms = now_ms
        try:
            snapshots = self.exchange.fetch_ticker_snapshots(tuple(symbols))
        except Exception as exc:
            self.artifacts.append_event(
                "ticker_radar_failed",
                "__live__",
                {"exception_type": type(exc).__name__, "exception_message": str(exc)[:500]},
            )
            return
        promotions = self._evaluate_ticker_radar_snapshots(snapshots, now_ms=now_ms)
        for promotion in promotions[: self.config.ticker_radar_max_promotions_per_cycle]:
            self._mark_ticker_radar_watch(
                str(promotion["symbol"]),
                now_ms=now_ms,
                reason="ticker_radar",
                score=float(promotion["score"]),
                price_delta_pct=float(promotion["price_delta_pct"]),
                quote_volume_delta=float(promotion["quote_volume_delta"]),
                quote_volume_delta_ratio=(
                    None
                    if promotion["quote_volume_delta_ratio"] is None
                    else float(promotion["quote_volume_delta_ratio"])
                ),
            )
        missing_count = sum(1 for snapshot in snapshots if snapshot.status != "ok")
        self.artifacts.append_event(
            "ticker_radar_snapshot",
            "__live__",
            {
                "symbols_total": len(snapshots),
                "ok_count": len(snapshots) - missing_count,
                "missing_count": missing_count,
                "promoted_count": min(len(promotions), self.config.ticker_radar_max_promotions_per_cycle),
                "promotion_candidates_count": len(promotions),
                "interval_seconds": self.config.ticker_radar_interval_seconds,
                "watch_batch_size": self.config.ticker_radar_watch_batch_size,
            },
        )

    def _evaluate_ticker_radar_snapshots(
        self,
        snapshots: list[ExchangeTickerSnapshot],
        *,
        now_ms: int,
    ) -> list[dict[str, object]]:
        promotions: list[dict[str, object]] = []
        for snapshot in snapshots:
            symbol_key = _position_symbol_key(snapshot.symbol)
            previous = self._ticker_radar_snapshots.get(symbol_key)
            if snapshot.status != "ok":
                self.artifacts.append_event(
                    "ticker_radar_missing_fields",
                    snapshot.symbol,
                    {
                        "status": snapshot.status,
                        "reason": snapshot.reason or "",
                        "last_price_source": snapshot.last_price_source,
                        "quote_volume_source": snapshot.quote_volume_source,
                        "trade_count_source": snapshot.trade_count_source,
                    },
                )
                self._ticker_radar_snapshots[symbol_key] = snapshot
                continue
            self._ticker_radar_snapshots[symbol_key] = snapshot
            if previous is None or previous.status != "ok":
                continue
            price_delta_pct = _safe_divide(
                float(snapshot.last_price or float("nan")) - float(previous.last_price or float("nan")),
                float(previous.last_price or float("nan")),
            )
            quote_volume_delta = float(snapshot.quote_volume_24h or 0.0) - float(previous.quote_volume_24h or 0.0)
            if quote_volume_delta > 0.0:
                history = self._ticker_radar_quote_delta_history.setdefault(symbol_key, deque(maxlen=24))
                baseline = _median_positive(list(history))
                quote_volume_delta_ratio = _safe_divide(quote_volume_delta, baseline) if baseline is not None else None
                history.append(quote_volume_delta)
            else:
                quote_volume_delta_ratio = None
            if not math.isfinite(price_delta_pct) or price_delta_pct < self.config.ticker_radar_min_price_delta_pct:
                continue
            if quote_volume_delta < self.config.ticker_radar_min_quote_volume_delta_usdt:
                continue
            if (
                quote_volume_delta_ratio is not None
                and quote_volume_delta_ratio < self.config.ticker_radar_min_quote_volume_delta_ratio
            ):
                continue
            score = (
                price_delta_pct * 100.0
                + math.log1p(max(0.0, quote_volume_delta / max(1.0, self.config.ticker_radar_min_quote_volume_delta_usdt)))
                + (math.log1p(quote_volume_delta_ratio) if quote_volume_delta_ratio is not None else 0.0)
            )
            promotions.append(
                {
                    "symbol": snapshot.symbol,
                    "score": score,
                    "price_delta_pct": price_delta_pct,
                    "quote_volume_delta": quote_volume_delta,
                    "quote_volume_delta_ratio": quote_volume_delta_ratio,
                    "last_price_source": snapshot.last_price_source,
                    "quote_volume_source": snapshot.quote_volume_source,
                    "trade_count_source": snapshot.trade_count_source,
                    "now_ms": now_ms,
                }
            )
        promotions.sort(key=lambda item: (float(item["score"]), str(item["symbol"])), reverse=True)
        return promotions

    def _mark_ticker_radar_watch(
        self,
        symbol: str,
        *,
        now_ms: int,
        reason: str,
        score: float,
        price_delta_pct: float,
        quote_volume_delta: float,
        quote_volume_delta_ratio: float | None,
    ) -> None:
        expires_at_ms = now_ms + max(1, int(self.config.ticker_radar_watch_ttl_ms))
        symbol_key = _position_symbol_key(symbol)
        event_payload: dict[str, object] | None = None
        with self._state_lock:
            current = self._ticker_radar_watch.get(symbol_key)
            should_emit = current is None or current.expires_at_ms < now_ms or score > current.score
            self._ticker_radar_watch[symbol_key] = LiveTickerRadarWatch(
                symbol=symbol,
                reason=reason,
                expires_at_ms=expires_at_ms,
                updated_at_ms=now_ms,
                score=score,
                price_delta_pct=price_delta_pct,
                quote_volume_delta=quote_volume_delta,
                quote_volume_delta_ratio=quote_volume_delta_ratio,
            )
            if should_emit:
                event_payload = {
                    "reason": reason,
                    "expires_at_ms": expires_at_ms,
                    "ttl_ms": int(self.config.ticker_radar_watch_ttl_ms),
                    "score": score,
                    "price_delta_pct": price_delta_pct,
                    "quote_volume_delta": quote_volume_delta,
                    "quote_volume_delta_ratio": quote_volume_delta_ratio if quote_volume_delta_ratio is not None else "",
                    "source": "ticker_delta_priority_only",
                }
        if event_payload is not None:
            self.artifacts.append_event("ticker_radar_promoted", symbol, event_payload)

    def _prune_ticker_radar_watch_locked(self, now_ms: int) -> list[LiveTickerRadarWatch]:
        expired_keys = [key for key, state in self._ticker_radar_watch.items() if state.expires_at_ms < now_ms]
        expired: list[LiveTickerRadarWatch] = []
        for key in expired_keys:
            state = self._ticker_radar_watch.pop(key, None)
            if state is not None:
                expired.append(state)
        return expired

    def _mark_active_symbol(
        self,
        symbol: str,
        *,
        reason: str,
        now_ms: int,
        ttl_ms: int | None = None,
        decision_timestamp_ms: int | None = None,
    ) -> None:
        ttl = self.config.active_symbol_ttl_ms if ttl_ms is None else ttl_ms
        expires_at_ms = now_ms + max(1, int(ttl))
        symbol_key = _position_symbol_key(symbol)
        event_payload: dict[str, object] | None = None
        cleared_radar_watch: LiveTickerRadarWatch | None = None
        with self._state_lock:
            current = self._active_symbols.get(symbol_key)
            should_emit = (
                current is None
                or current.reason != reason
                or current.decision_timestamp_ms != decision_timestamp_ms
                or current.expires_at_ms < now_ms
            )
            self._active_symbols[symbol_key] = LiveActiveSymbol(
                symbol=symbol,
                reason=reason,
                expires_at_ms=expires_at_ms,
                updated_at_ms=now_ms,
                decision_timestamp_ms=decision_timestamp_ms,
            )
            cleared_radar_watch = self._ticker_radar_watch.pop(symbol_key, None)
            if should_emit:
                event_payload = {
                    "reason": reason,
                    "expires_at_ms": expires_at_ms,
                    "ttl_ms": int(ttl),
                    "decision_timestamp_ms": decision_timestamp_ms if decision_timestamp_ms is not None else "",
                }
        if cleared_radar_watch is not None:
            self.artifacts.append_event(
                "ticker_radar_watch_cleared",
                symbol,
                {
                    "reason": "promoted_to_active_symbol",
                    "active_reason": reason,
                    "watch_reason": cleared_radar_watch.reason,
                    "score": cleared_radar_watch.score,
                    "expires_at_ms": cleared_radar_watch.expires_at_ms,
                    "price_delta_pct": cleared_radar_watch.price_delta_pct,
                    "quote_volume_delta": cleared_radar_watch.quote_volume_delta,
                    "quote_volume_delta_ratio": (
                        cleared_radar_watch.quote_volume_delta_ratio
                        if cleared_radar_watch.quote_volume_delta_ratio is not None
                        else ""
                    ),
                },
            )
        if event_payload is not None:
            self.artifacts.append_event("active_symbol_marked", symbol, event_payload)

    def _clear_active_symbol(self, symbol: str, *, reason: str) -> None:
        symbol_key = _position_symbol_key(symbol)
        removed: LiveActiveSymbol | None = None
        with self._state_lock:
            removed = self._active_symbols.pop(symbol_key, None)
        if removed is not None:
            self.artifacts.append_event(
                "active_symbol_cleared",
                symbol,
                {
                    "reason": reason,
                    "previous_reason": removed.reason,
                    "previous_expires_at_ms": removed.expires_at_ms,
                    "decision_timestamp_ms": removed.decision_timestamp_ms if removed.decision_timestamp_ms is not None else "",
                },
            )

    def _prune_active_symbols_locked(self, now_ms: int) -> list[LiveActiveSymbol]:
        expired_keys = [key for key, state in self._active_symbols.items() if state.expires_at_ms < now_ms]
        expired: list[LiveActiveSymbol] = []
        for key in expired_keys:
            state = self._active_symbols.pop(key, None)
            if state is not None:
                expired.append(state)
        return expired

    def _mark_signal_decision_consumed(self, signal: LiveSignal, *, reason: str) -> None:
        key = (signal.symbol, signal.levels_timeframe.value, signal.entry_timeframe.value, int(signal.decision_timestamp_ms))
        with self._state_lock:
            already_seen = key in self._seen_decisions
            self._seen_decisions.add(key)
        if not already_seen:
            self.artifacts.append_event(
                "signal_decision_consumed",
                signal.symbol,
                {
                    "reason": reason,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                },
            )

    def _signal_scan_due_for_symbol(self, symbol: str, *, now_ms: int) -> bool:
        symbol_key = _position_symbol_key(symbol)
        with self._state_lock:
            for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
                closed_timestamp_ms = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
                scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
                if self._last_signal_scan_closed_at.get(scan_key) != closed_timestamp_ms:
                    return True
        return False

    def _signal_scan_due_for_timeframe(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        *,
        closed_timestamp_ms: int,
    ) -> bool:
        symbol_key = _position_symbol_key(symbol)
        scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
        with self._state_lock:
            return self._last_signal_scan_closed_at.get(scan_key) != int(closed_timestamp_ms)

    def _mark_signal_scan_closed_at(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        *,
        closed_timestamp_ms: int,
    ) -> None:
        symbol_key = _position_symbol_key(symbol)
        scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
        with self._state_lock:
            self._last_signal_scan_closed_at[scan_key] = int(closed_timestamp_ms)

    def _scan_batch(self, symbols: list[str]) -> list[LiveSignal]:
        if self.config.scan_hot_timeframes_per_symbol:
            return self._scan_batch_by_symbol(symbols)
        return self._scan_batch_by_timeframe(symbols)

    def _scan_batch_by_symbol(self, symbols: list[str]) -> list[LiveSignal]:
        signals: list[LiveSignal] = []
        now_ms = int(time.time() * 1000)
        setup_cache: dict[tuple[str, str, int], pd.DataFrame] = {}
        entry_cache: dict[tuple[str, str, int, int], pd.DataFrame] = {}
        for symbol in symbols:
            started_at = time.monotonic()
            due_count = 0
            evaluated_count = 0
            setup_fetch_count = 0
            entry_fetch_count = 0
            signal_count = 0
            fetch_failures = 0
            skipped_not_due = 0
            for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
                levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
                setup_lookback_ms = (self.config.baseline_candles + 5) * levels_timeframe_ms
                latest_closed_entry_ts = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
                setup_start_ts = (latest_closed_entry_ts // levels_timeframe_ms) * levels_timeframe_ms
                entry_lookback_start_ms = setup_start_ts
                if not self._signal_scan_due_for_timeframe(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    closed_timestamp_ms=latest_closed_entry_ts,
                ):
                    skipped_not_due += 1
                    continue
                due_count += 1
                setup_key = (symbol, levels_timeframe.value, setup_start_ts)
                if setup_key not in setup_cache:
                    try:
                        setup_cache[setup_key] = self._fetch_chart_frame(
                            symbol,
                            levels_timeframe,
                            start_timestamp_ms=setup_start_ts - setup_lookback_ms,
                            end_timestamp_ms=now_ms,
                        )
                        setup_fetch_count += 1
                    except Exception as exc:
                        fetch_failures += 1
                        self.artifacts.append_event(
                            "signal_setup_fetch_failed",
                            symbol,
                            {
                                "levels_tf": levels_timeframe.value,
                                "entry_tf": entry_timeframe.value,
                                "reason": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        continue
                entry_key = (symbol, entry_timeframe.value, entry_lookback_start_ms, now_ms)
                if entry_key not in entry_cache:
                    try:
                        entry_cache[entry_key] = self._fetch_chart_frame(
                            symbol,
                            entry_timeframe,
                            start_timestamp_ms=entry_lookback_start_ms,
                            end_timestamp_ms=now_ms,
                        )
                        entry_fetch_count += 1
                    except Exception as exc:
                        fetch_failures += 1
                        self.artifacts.append_event(
                            "signal_entry_fetch_failed",
                            symbol,
                            {
                                "levels_tf": levels_timeframe.value,
                                "entry_tf": entry_timeframe.value,
                                "reason": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        continue
                signal = self._build_forming_setup_signal(
                    symbol,
                    setup_cache[setup_key],
                    entry_cache[entry_key],
                    now_ms=now_ms,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    setup_start_ts=setup_start_ts,
                    latest_closed_entry_ts=latest_closed_entry_ts,
                )
                evaluated_count += 1
                self._mark_signal_scan_closed_at(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    closed_timestamp_ms=latest_closed_entry_ts,
                )
                if signal is not None:
                    signals.append(signal)
                    signal_count += 1
            self.artifacts.append_event(
                "signal_symbol_scan_summary",
                symbol,
                {
                    "mode": "timeframes_per_symbol",
                    "timeframe_pairs": [f"{levels.value}/{entry.value}" for levels, entry in self.config.timeframe_pairs],
                    "due_timeframe_count": due_count,
                    "evaluated_timeframe_count": evaluated_count,
                    "skipped_not_due_count": skipped_not_due,
                    "setup_fetch_count": setup_fetch_count,
                    "entry_fetch_count": entry_fetch_count,
                    "fetch_failure_count": fetch_failures,
                    "signal_count": signal_count,
                    "duration_ms": round((time.monotonic() - started_at) * 1000.0, 3),
                },
            )
        return signals

    def _scan_batch_by_timeframe(self, symbols: list[str]) -> list[LiveSignal]:
        signals: list[LiveSignal] = []
        now_ms = int(time.time() * 1000)
        for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
            levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
            entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
            setup_lookback_ms = (self.config.baseline_candles + 5) * levels_timeframe_ms
            latest_closed_entry_ts = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
            setup_start_ts = (latest_closed_entry_ts // levels_timeframe_ms) * levels_timeframe_ms
            entry_lookback_start_ms = setup_start_ts
            for symbol in symbols:
                if not self._signal_scan_due_for_timeframe(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    closed_timestamp_ms=latest_closed_entry_ts,
                ):
                    continue
                try:
                    setup_frame = self._fetch_chart_frame(
                        symbol,
                        levels_timeframe,
                        start_timestamp_ms=setup_start_ts - setup_lookback_ms,
                        end_timestamp_ms=now_ms,
                    )
                except Exception as exc:
                    self.artifacts.append_event(
                        "signal_setup_fetch_failed",
                        symbol,
                        {
                            "levels_tf": levels_timeframe.value,
                            "entry_tf": entry_timeframe.value,
                            "reason": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    continue
                try:
                    entry_frame = self._fetch_chart_frame(
                        symbol,
                        entry_timeframe,
                        start_timestamp_ms=entry_lookback_start_ms,
                        end_timestamp_ms=now_ms,
                    )
                except Exception as exc:
                    self.artifacts.append_event(
                        "signal_entry_fetch_failed",
                        symbol,
                        {
                            "levels_tf": levels_timeframe.value,
                            "entry_tf": entry_timeframe.value,
                            "reason": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    continue
                signal = self._build_forming_setup_signal(
                    symbol,
                    setup_frame,
                    entry_frame,
                    now_ms=now_ms,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    setup_start_ts=setup_start_ts,
                    latest_closed_entry_ts=latest_closed_entry_ts,
                )
                self._mark_signal_scan_closed_at(
                    symbol,
                    levels_timeframe,
                    entry_timeframe,
                    closed_timestamp_ms=latest_closed_entry_ts,
                )
                if signal is not None:
                    signals.append(signal)
        return signals

    def _build_forming_setup_signal(
        self,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        *,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        setup_start_ts: int,
        latest_closed_entry_ts: int,
    ) -> LiveSignal | None:
        if setup_frame.empty or entry_frame.empty:
            self.artifacts.append_event(
                "signal_scan_empty_ohlcv",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_start_timestamp_ms": int(setup_start_ts),
                    "reason": "empty_setup_or_entry_frame",
                },
            )
            return None
        missing_setup_price = [column for column in REQUIRED_PRICE_COLUMNS if column not in setup_frame.columns]
        missing_setup_flow = [column for column in REQUIRED_FLOW_COLUMNS if column not in setup_frame.columns]
        missing_entry_price = [column for column in REQUIRED_PRICE_COLUMNS if column not in entry_frame.columns]
        missing_entry_flow = [column for column in REQUIRED_FLOW_COLUMNS if column not in entry_frame.columns]
        if missing_setup_price or missing_setup_flow or missing_entry_price or missing_entry_flow:
            self.artifacts.append_event(
                "reject_missing_signal_columns",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "missing_setup_price": missing_setup_price,
                    "missing_setup_flow": missing_setup_flow,
                    "missing_entry_price": missing_entry_price,
                    "missing_entry_flow": missing_entry_flow,
                },
            )
            return None
        levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
        entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
        setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        setup_history = setup_frame.loc[setup_frame["timestamp"].astype(int) < int(setup_start_ts)].tail(self.config.baseline_candles).copy()
        entry_segment = entry_frame.loc[
            (entry_frame["timestamp"].astype(int) >= int(setup_start_ts))
            & (entry_frame["timestamp"].astype(int) <= int(latest_closed_entry_ts))
        ].copy()
        if len(setup_history) < self.config.baseline_candles or entry_segment.empty:
            return None
        seed_close = float(setup_history.iloc[-1]["close"])
        entry_segment = _fill_missing_ohlcv_buckets(
            entry_segment,
            start_timestamp_ms=int(setup_start_ts),
            end_timestamp_ms=int(latest_closed_entry_ts),
            timeframe_ms=entry_timeframe_ms,
            seed_close=seed_close,
        )
        if len(entry_segment) < self.config.confirmation_candles:
            self.artifacts.append_event(
                "reject_setup_too_early",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_start_timestamp_ms": int(setup_start_ts),
                    "closed_entry_candles": int(len(entry_segment)),
                    "min_closed_entry_candles": int(self.config.confirmation_candles),
                },
            )
            return None
        setup_elapsed_fraction = min(1.0, len(entry_segment) * entry_timeframe_ms / levels_timeframe_ms)
        forming_setup = _aggregate_frame_to_candle(entry_segment, timestamp_ms=int(setup_start_ts))
        if forming_setup is None:
            return None
        decision_ts = int(entry_segment.iloc[-1]["timestamp"])
        key = (symbol, levels_timeframe.value, entry_timeframe.value, decision_ts)
        with self._state_lock:
            if key in self._seen_decisions:
                return None
        freshness = _decision_freshness_details(
            decision_timestamp_ms=decision_ts,
            signal_timeframe=entry_timeframe,
            now_ms=now_ms,
            max_signal_age_ms=self.config.max_signal_age_ms,
        )
        if freshness["signal_age_ms"] > self.config.max_signal_age_ms:
            with self._state_lock:
                self._seen_decisions.add(key)
            self.artifacts.append_event(
                "reject_stale_signal",
                symbol,
                {
                    **freshness,
                    "stage": "prescan",
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_source": "forming_htf_from_entry_tf",
                },
            )
            return None
        signal = self._build_signal_from_components(
            symbol=symbol,
            baseline=setup_history,
            setup_row=forming_setup,
            entry_segment=entry_segment,
            now_ms=now_ms,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            setup_source="forming_htf_from_entry_tf",
            setup_elapsed_fraction=setup_elapsed_fraction,
            setup_closed_entry_candles=len(entry_segment),
        )
        if signal is None:
            with self._state_lock:
                self._seen_decisions.add(key)
        return signal

    def _build_signal_from_components(
        self,
        *,
        symbol: str,
        baseline: pd.DataFrame,
        setup_row: pd.Series,
        entry_segment: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        setup_source: str,
        setup_elapsed_fraction: float,
        setup_closed_entry_candles: int,
    ) -> LiveSignal | None:
        if baseline.empty or entry_segment.empty:
            return None
        decision = entry_segment.iloc[-1]
        baseline_quote = float(pd.to_numeric(baseline["quote_volume"], errors="coerce").median())
        baseline_trades = float(pd.to_numeric(baseline["number_of_trades"], errors="coerce").median())
        start_quote = float(setup_row["quote_volume"])
        start_trades = float(setup_row["number_of_trades"])
        start_open = float(setup_row["open"])
        start_close = float(setup_row["close"])
        start_high = float(setup_row["high"])
        start_low = float(setup_row["low"])
        start_ret = _safe_divide(start_close - start_open, start_open)
        abs_start_ret = abs(start_ret) if math.isfinite(start_ret) else float("nan")
        baseline_avg_trade_quote = _safe_divide(baseline_quote, baseline_trades)
        start_avg_trade_quote = _safe_divide(start_quote, start_trades)
        start_avg_trade_ratio = _safe_divide(start_avg_trade_quote, baseline_avg_trade_quote)
        raw_quote_ratio_for_return = _safe_divide(start_quote, baseline_quote)
        start_quote_ratio_per_abs_return = _safe_divide(raw_quote_ratio_for_return, abs_start_ret)
        baseline_range_pct = float(
            ((baseline["high"].astype(float) - baseline["low"].astype(float)) / baseline["close"].astype(float).replace(0.0, pd.NA)).median()
        )
        start_range_pct_ratio = _safe_divide(_safe_divide(start_high - start_low, start_open), baseline_range_pct)
        raw_quote_ratio = _safe_divide(start_quote, baseline_quote)
        raw_trade_ratio = _safe_divide(start_trades, baseline_trades)
        elapsed_for_ratio = max(1e-9, min(1.0, float(setup_elapsed_fraction)))
        if setup_source.startswith("forming_htf"):
            quote_ratio = _safe_divide(raw_quote_ratio, elapsed_for_ratio)
            trade_ratio = _safe_divide(raw_trade_ratio, elapsed_for_ratio)
            min_raw_quote_ratio = self.config.min_quote_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
            min_raw_trade_ratio = self.config.min_trade_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
        else:
            quote_ratio = raw_quote_ratio
            trade_ratio = raw_trade_ratio
            min_raw_quote_ratio = self.config.min_quote_ratio_start
            min_raw_trade_ratio = self.config.min_trade_ratio_start
        if not math.isfinite(quote_ratio) or not math.isfinite(trade_ratio) or not math.isfinite(raw_quote_ratio) or not math.isfinite(raw_trade_ratio):
            self.artifacts.append_event(
                "reject_invalid_flow_ratios",
                symbol,
                {
                    "baseline_quote": _finite_or_none(baseline_quote),
                    "baseline_trades": _finite_or_none(baseline_trades),
                    "start_quote": _finite_or_none(start_quote),
                    "start_trades": _finite_or_none(start_trades),
                    "raw_quote_ratio": _finite_or_none(raw_quote_ratio),
                    "raw_trade_ratio": _finite_or_none(raw_trade_ratio),
                    "quote_pace_ratio": _finite_or_none(quote_ratio),
                    "trade_pace_ratio": _finite_or_none(trade_ratio),
                    "setup_elapsed_fraction": float(setup_elapsed_fraction),
                    "decision_timestamp_ms": int(decision["timestamp"]),
                    "setup_source": setup_source,
                },
            )
            return None
        if (
            quote_ratio < self.config.min_quote_ratio_start
            or trade_ratio < self.config.min_trade_ratio_start
            or raw_quote_ratio < min_raw_quote_ratio
            or raw_trade_ratio < min_raw_trade_ratio
        ):
            self.artifacts.append_event(
                "reject_weak_start_flow",
                symbol,
                {
                    "raw_quote_ratio": raw_quote_ratio,
                    "raw_trade_ratio": raw_trade_ratio,
                    "quote_pace_ratio": quote_ratio,
                    "trade_pace_ratio": trade_ratio,
                    "min_quote_pace_ratio": self.config.min_quote_ratio_start,
                    "min_trade_pace_ratio": self.config.min_trade_ratio_start,
                    "min_raw_quote_ratio": min_raw_quote_ratio,
                    "min_raw_trade_ratio": min_raw_trade_ratio,
                    "setup_elapsed_fraction": float(setup_elapsed_fraction),
                    "decision_timestamp_ms": int(decision["timestamp"]),
                    "setup_source": setup_source,
                },
            )
            return None

        latest_decision_ts = int(decision["timestamp"])
        decision_available_ms = latest_decision_ts + int(entry_timeframe.to_milliseconds())
        if 0 <= now_ms - decision_available_ms <= self.config.max_signal_age_ms:
            self._mark_active_symbol(
                symbol,
                reason="pump_flow_candidate",
                now_ms=now_ms,
                ttl_ms=self.config.active_symbol_ttl_ms,
                decision_timestamp_ms=latest_decision_ts,
            )

        segment_high = float(max(start_high, pd.to_numeric(entry_segment["high"], errors="coerce").max()))
        segment_low = float(min(start_low, pd.to_numeric(entry_segment["low"], errors="coerce").min()))
        impulse_range = segment_high - segment_low
        prior_whipsaw = _prior_up_down_whipsaw_to_impulse_range(baseline, impulse_range=impulse_range)
        decision_close = float(decision["close"])
        price_retention = _safe_divide(decision_close - start_open, segment_high - start_open)
        verticality = compute_start_verticality_metrics(entry_segment)
        verticality_score = float(verticality["start_verticality_score"])
        activation_price = start_open + max(0.0, start_close - start_open) * 0.50
        hold_count = int((entry_segment["close"].astype(float) >= activation_price).sum())
        setup_with_current = pd.concat([baseline, pd.DataFrame([setup_row.to_dict()])], ignore_index=True)
        ema20 = setup_with_current["close"].astype(float).ewm(span=20, adjust=False).mean()
        decision_ema20 = float(ema20.iloc[-1])
        previous_stop = segment_low - self.config.stop_buffer_range_fraction * impulse_range
        stop_price = max(previous_stop, decision_ema20)
        entry_price = decision_close
        risk = entry_price - stop_price
        initial_risk_pct = _safe_divide(risk, entry_price)
        if not math.isfinite(risk) or risk <= 0.0:
            self._clear_active_symbol(symbol, reason="invalid_initial_risk")
            self.artifacts.append_event(
                "reject_invalid_initial_risk",
                symbol,
                {
                    "entry_price": _finite_or_none(entry_price),
                    "stop_price": _finite_or_none(stop_price),
                    "risk": _finite_or_none(risk),
                    "decision_timestamp_ms": int(decision["timestamp"]),
                    "setup_source": setup_source,
                },
            )
            return None
        if not math.isfinite(initial_risk_pct) or initial_risk_pct > self.config.max_initial_risk_pct:
            self._clear_active_symbol(symbol, reason="initial_risk_too_wide")
            self.artifacts.append_event(
                "reject_initial_risk_too_wide",
                symbol,
                {
                    "initial_risk_pct": _finite_or_none(initial_risk_pct),
                    "max": self.config.max_initial_risk_pct,
                    "decision_timestamp_ms": int(decision["timestamp"]),
                    "setup_source": setup_source,
                },
            )
            return None
        base_tp1_price = entry_price + risk
        tp1_price, tp1_round_step = _round_up_tp1_to_market_number(
            base_tp1_price,
            reference_price=entry_price,
            movement=max(risk, segment_high - segment_low),
        )

        category_rejections: list[dict[str, object]] = []
        oi_change_loaded = False
        oi_change: float | None = None
        oi_change_reason: str | None = None
        next_taker_share_loaded = False
        next_taker_share = float("nan")
        for category in self._pump_categories:
            max_start_quote_ratio = _category_value(category, self.config, "max_start_quote_ratio")
            if max_start_quote_ratio is not None and quote_ratio > max_start_quote_ratio:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_exhausted_quote_ratio", {"quote_ratio": quote_ratio, "max": max_start_quote_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_start_trade_ratio = _category_value(category, self.config, "max_start_trade_ratio")
            if max_start_trade_ratio is not None and trade_ratio > max_start_trade_ratio:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_exhausted_trade_ratio", {"trade_ratio": trade_ratio, "max": max_start_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_avg_trade_ratio = _category_value(category, self.config, "max_start_avg_trade_quote_size_ratio")
            if max_avg_trade_ratio is not None and not math.isfinite(start_avg_trade_ratio):
                category_rejections.append(self._record_category_reject(category, symbol, "reject_invalid_avg_trade_quote_size_ratio", {"ratio": _finite_or_none(start_avg_trade_ratio), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_avg_trade_ratio is not None and start_avg_trade_ratio > max_avg_trade_ratio:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_large_print_signature", {"ratio": start_avg_trade_ratio, "max": max_avg_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_quote_per_return = _category_value(category, self.config, "max_start_quote_ratio_per_abs_return")
            if max_quote_per_return is not None and not math.isfinite(start_quote_ratio_per_abs_return):
                category_rejections.append(self._record_category_reject(category, symbol, "reject_invalid_quote_ratio_per_abs_return", {"ratio": _finite_or_none(start_quote_ratio_per_abs_return), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_quote_per_return is not None and start_quote_ratio_per_abs_return > max_quote_per_return:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_poor_effort_per_return", {"ratio": start_quote_ratio_per_abs_return, "max": max_quote_per_return, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_range_ratio = _category_value(category, self.config, "max_start_range_pct_ratio_to_baseline")
            if max_range_ratio is not None and not math.isfinite(start_range_pct_ratio):
                category_rejections.append(self._record_category_reject(category, symbol, "reject_invalid_range_expansion_ratio", {"ratio": _finite_or_none(start_range_pct_ratio), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_range_ratio is not None and start_range_pct_ratio > max_range_ratio:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_extreme_range_expansion", {"ratio": start_range_pct_ratio, "max": max_range_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_prior_whipsaw = self.config.max_prior_up_down_whipsaw_to_impulse_range
            if max_prior_whipsaw is not None and not math.isfinite(prior_whipsaw):
                category_rejections.append(self._record_category_reject(category, symbol, "reject_invalid_prior_whipsaw", {"prior_up_down_whipsaw_to_impulse_range": _finite_or_none(prior_whipsaw), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_prior_whipsaw is not None and prior_whipsaw > max_prior_whipsaw:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_prior_up_down_whipsaw", {"prior_up_down_whipsaw_to_impulse_range": prior_whipsaw, "max": max_prior_whipsaw, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if not math.isfinite(price_retention):
                category_rejections.append(self._record_category_reject(category, symbol, "reject_invalid_price_retention", {"price_retention": _finite_or_none(price_retention), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if price_retention < self.config.min_price_retention:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_low_price_retention", {"price_retention": price_retention, "min": self.config.min_price_retention, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_price_retention = _category_value(category, self.config, "max_price_retention")
            if max_price_retention is not None and price_retention > max_price_retention:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_overextended_retention", {"price_retention": price_retention, "max": max_price_retention, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_next_taker_share = _category_value(category, self.config, "min_next_taker_buy_quote_share")
            if min_next_taker_share is not None:
                if "taker_buy_quote_volume" not in entry_segment.columns:
                    category_rejections.append(self._record_category_reject(category, symbol, "reject_missing_taker_buy_share", {"required": min_next_taker_share, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
                if not next_taker_share_loaded:
                    taker_quote = pd.to_numeric(entry_segment["taker_buy_quote_volume"], errors="coerce")
                    quote_volume = pd.to_numeric(entry_segment["quote_volume"], errors="coerce")
                    valid_taker_share_rows = taker_quote.notna() & quote_volume.notna() & quote_volume.gt(0.0)
                    if not bool(valid_taker_share_rows.all()):
                        next_taker_share = float("nan")
                    else:
                        next_taker_share = float((taker_quote / quote_volume).mean())
                    next_taker_share_loaded = True
                if not math.isfinite(next_taker_share):
                    category_rejections.append(self._record_category_reject(category, symbol, "reject_invalid_taker_buy_share", {"share": _finite_or_none(next_taker_share), "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
                if next_taker_share < min_next_taker_share:
                    category_rejections.append(self._record_category_reject(category, symbol, "reject_weak_next_taker_buy_share", {"share": next_taker_share, "min": min_next_taker_share, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            if not math.isfinite(verticality_score):
                category_rejections.append(self._record_category_reject(category, symbol, "reject_invalid_verticality", {"verticality_score": _finite_or_none(verticality_score), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if verticality_score < self.config.min_verticality_score:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_low_verticality", {"verticality_score": verticality_score, "min": self.config.min_verticality_score, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if hold_count < self.config.min_hold_count:
                category_rejections.append(self._record_category_reject(category, symbol, "reject_low_hold_count", {"hold_count": hold_count, "min": self.config.min_hold_count, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if self.config.min_oi_change_pct_3x5m is not None:
                if not oi_change_loaded:
                    oi_result = self._fetch_live_oi_change(symbol, now_ms=now_ms)
                    oi_change = oi_result.value
                    oi_change_reason = oi_result.reason
                    oi_change_loaded = True
                if oi_change is None or not math.isfinite(oi_change) or oi_change <= self.config.min_oi_change_pct_3x5m:
                    category_rejections.append(self._record_category_reject(category, symbol, "reject_oi", {"oi_change_pct_3x5m": _finite_or_none(oi_change), "oi_status": oi_change_reason or "below_threshold", "required_gt": self.config.min_oi_change_pct_3x5m, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue

            strengths = [
                f"категория {category.category_id}",
                f"setup flow x{quote_ratio:.1f}/{trade_ratio:.1f}",
                f"entry hold {hold_count}",
                f"удержание {price_retention:.0%}",
                f"вертикальность {verticality_score:.2f}",
            ]
            if oi_change is not None:
                strengths.append(f"OI {oi_change:.1%}")
            weaknesses: list[str] = []
            if "taker_buy_quote_volume" not in entry_segment.columns:
                weaknesses.append("нет taker-buy в entry flow")
            signal = LiveSignal(
                category_id=category.category_id,
                category_label=category.label,
                category_priority=category.priority,
                symbol=symbol,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                setup_source=setup_source,
                setup_elapsed_fraction=float(setup_elapsed_fraction),
                setup_closed_entry_candles=int(setup_closed_entry_candles),
                decision_timestamp_ms=int(decision["timestamp"]),
                start_timestamp_ms=int(setup_row["timestamp"]),
                session=_session_name(int(decision["timestamp"])),
                entry_price=entry_price,
                stop_price=stop_price,
                tp1_price=tp1_price,
                box_high=segment_high,
                initial_risk=risk,
                initial_risk_pct=initial_risk_pct,
                quote_ratio_start=quote_ratio,
                trade_ratio_start=trade_ratio,
                price_retention=price_retention,
                hold_count=hold_count,
                verticality_score=verticality_score,
                oi_change_pct_3x5m=oi_change,
                category_rejections=list(category_rejections),
                strengths=strengths,
                weaknesses=weaknesses,
            )
            self._mark_active_symbol(
                symbol,
                reason="entry_signal_selected",
                now_ms=now_ms,
                ttl_ms=self.config.max_signal_age_ms,
                decision_timestamp_ms=int(decision["timestamp"]),
            )
            self.artifacts.append_event(
                "category_selected",
                symbol,
                {
                    "category_id": category.category_id,
                    "category_label": category.label,
                    "category_priority": category.priority,
                    "decision_timestamp_ms": int(decision["timestamp"]),
                    "prior_category_rejections": category_rejections,
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "setup_source": setup_source,
                    "setup_elapsed_fraction": float(setup_elapsed_fraction),
                    "setup_closed_entry_candles": int(setup_closed_entry_candles),
                    "raw_quote_ratio": _finite_or_none(raw_quote_ratio),
                    "raw_trade_ratio": _finite_or_none(raw_trade_ratio),
                    "quote_pace_ratio": _finite_or_none(quote_ratio),
                    "trade_pace_ratio": _finite_or_none(trade_ratio),
                    "base_tp1_price": _finite_or_none(base_tp1_price),
                    "tp1_price": _finite_or_none(tp1_price),
                    "tp1_round_step": _finite_or_none(tp1_round_step),
                },
            )
            if category_rejections:
                rejected_ids = ",".join(str(row.get("category_id")) for row in category_rejections)
                self.logger(
                    f"live: {_compact_symbol(symbol)} {levels_timeframe.value}/{entry_timeframe.value} · "
                    f"сигнал {category.category_id} · раньше отвалилось {rejected_ids}"
                )
            return signal
        return None

    def _record_category_reject(
        self,
        category: LivePumpCategory,
        symbol: str,
        reason: str,
        details: dict[str, object],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "category_id": category.category_id,
            "category_label": category.label,
            "category_priority": category.priority,
            "reason": reason,
            **details,
        }
        self.artifacts.append_event("category_rejected", symbol, payload)
        return payload

    def _fetch_live_oi_change(self, symbol: str, *, now_ms: int) -> LiveOiChangeResult:
        end_ms = now_ms
        start_ms = end_ms - 25 * 60_000
        frame = self.exchange.fetch_open_interest(symbol, Timeframe.M5, start_ms, end_ms)
        if frame.empty:
            return LiveOiChangeResult(None, "oi_frame_empty")
        if "open_interest" not in frame.columns:
            return LiveOiChangeResult(None, "oi_column_missing")
        frame = frame.sort_values("timestamp").dropna(subset=["timestamp", "open_interest"]).reset_index(drop=True)
        if len(frame) < 4:
            return LiveOiChangeResult(None, "oi_history_too_short")
        latest_ts = int(frame.iloc[-1]["timestamp"])
        if latest_ts < now_ms - self.config.oi_fresh_ms:
            return LiveOiChangeResult(None, "oi_stale")
        current = float(frame.iloc[-1]["open_interest"])
        previous = float(frame.iloc[-4]["open_interest"])
        if not math.isfinite(current) or not math.isfinite(previous) or previous <= 0.0:
            return LiveOiChangeResult(None, "oi_invalid_values")
        value = _safe_divide(current - previous, previous)
        if not math.isfinite(value):
            return LiveOiChangeResult(None, "oi_invalid_change")
        return LiveOiChangeResult(value)

    def _maybe_open_position(self, signal: LiveSignal) -> None:
        symbol_key = _position_symbol_key(signal.symbol)
        reject_max_positions = False
        with self._state_lock:
            if symbol_key in self._open_positions or symbol_key in self._opening_symbols:
                self._mark_active_symbol(
                    signal.symbol,
                    reason="position_already_active",
                    now_ms=int(time.time() * 1000),
                    decision_timestamp_ms=signal.decision_timestamp_ms,
                )
                self._mark_signal_decision_consumed(signal, reason="symbol_position_already_active")
                self.artifacts.append_event(
                    "reject_symbol_position_already_active",
                    signal.symbol,
                    {"symbol_key": symbol_key, "levels_tf": signal.levels_timeframe.value, "entry_tf": signal.entry_timeframe.value},
                )
                return
            if len(self._open_positions) + len(self._opening_symbols) >= self.config.max_open_positions:
                reject_max_positions = True
            if self._symbol_in_stop_cooldown(signal.symbol):
                self._clear_active_symbol(signal.symbol, reason="stop_cooldown")
                self._mark_signal_decision_consumed(signal, reason="stop_cooldown")
                self.artifacts.append_event(
                    "reject_stop_cooldown",
                    signal.symbol,
                    {
                        "symbol_key": symbol_key,
                        "levels_tf": signal.levels_timeframe.value,
                        "entry_tf": signal.entry_timeframe.value,
                        "decision_timestamp_ms": signal.decision_timestamp_ms,
                        "stop_cooldown_hours": self.config.stop_cooldown_hours,
                        "stop_limit_per_symbol": self.config.stop_limit_per_symbol,
                    },
                )
                return
            if not reject_max_positions:
                self._opening_symbols.add(symbol_key)
        if not reject_max_positions:
            self._mark_active_symbol(
                signal.symbol,
                reason="opening_position",
                now_ms=int(time.time() * 1000),
                ttl_ms=self.config.max_signal_age_ms,
                decision_timestamp_ms=signal.decision_timestamp_ms,
            )
        if reject_max_positions:
            self._mark_active_symbol(
                signal.symbol,
                reason="entry_waiting_for_free_slot",
                now_ms=int(time.time() * 1000),
                ttl_ms=self.config.max_signal_age_ms,
                decision_timestamp_ms=signal.decision_timestamp_ms,
            )
            self.artifacts.append_event("reject_max_positions", signal.symbol, {"max": self.config.max_open_positions})
            return
        try:
            if not self._validate_signal_freshness(signal):
                return
            pre_position_amount = float(self.exchange.fetch_symbol_position_amount(signal.symbol))
            if not math.isfinite(pre_position_amount):
                self._clear_active_symbol(signal.symbol, reason="invalid_existing_exchange_position")
                self._mark_signal_decision_consumed(signal, reason="invalid_existing_exchange_position")
                self.artifacts.append_event(
                    "reject_invalid_existing_exchange_position",
                    signal.symbol,
                    {"exchange_position_amount": _finite_or_none(pre_position_amount)},
                )
                return
            if abs(pre_position_amount) > 0.0:
                self._clear_active_symbol(signal.symbol, reason="existing_exchange_position")
                self._mark_signal_decision_consumed(signal, reason="existing_exchange_position")
                self.artifacts.append_event(
                    "reject_existing_exchange_position",
                    signal.symbol,
                    {"exchange_position_amount": pre_position_amount},
                )
                return
            live_price = float(self.exchange.fetch_last_price(signal.symbol))
            if not self._validate_signal_executable(signal, live_price=live_price):
                return
            try:
                balance = float(self.exchange.fetch_usdt_free_balance())
            except (TypeError, ValueError) as exc:
                self.artifacts.append_event("reject_invalid_free_balance", signal.symbol, {"free_usdt": None, "reason": str(exc)})
                return
            if not math.isfinite(balance):
                self.artifacts.append_event("reject_invalid_free_balance", signal.symbol, {"free_usdt": None})
                return
            if balance <= 0.0:
                self.artifacts.append_event("reject_no_free_balance", signal.symbol, {"free_usdt": balance})
                return
            notional = self.config.position_notional_usdt
            if not math.isfinite(notional) or notional <= 0.0:
                self._mark_signal_decision_consumed(signal, reason="invalid_position_notional")
                self.artifacts.append_event("reject_invalid_position_notional", signal.symbol, {"position_notional": _finite_or_none(notional)})
                return
            if balance < notional:
                self.artifacts.append_event(
                    "reject_insufficient_margin_for_fixed_notional",
                    signal.symbol,
                    {"free_usdt": balance, "position_notional": notional},
                )
                return
            amount_requested = notional / live_price
            if not math.isfinite(amount_requested) or amount_requested <= 0.0:
                self.artifacts.append_event(
                    "reject_invalid_order_amount",
                    signal.symbol,
                    {"live_price": _finite_or_none(live_price), "position_notional": _finite_or_none(notional)},
                )
                return
            entry_order_submitted_at_ms = int(time.time() * 1000)
            fill = self.exchange.create_market_order_with_fill(signal.symbol, "buy", amount_requested)
            post_position_amount = float(self.exchange.fetch_symbol_position_amount(signal.symbol))
            position_delta_amount = post_position_amount - pre_position_amount
            if not math.isfinite(post_position_amount) or not math.isfinite(position_delta_amount):
                raise LiveDataIntegrityError(
                    f"invalid post-entry position amount: symbol={signal.symbol} pre={pre_position_amount} post={post_position_amount}"
                )
            if position_delta_amount <= 0.0:
                raise LiveDataIntegrityError(
                    f"entry order filled but exchange position did not increase: symbol={signal.symbol} order_id={fill.order_id} "
                    f"pre={pre_position_amount} post={post_position_amount}"
                )
            fill_position_slippage = abs(position_delta_amount - fill.filled_amount) / max(fill.filled_amount, 1e-12)
            if fill_position_slippage > self.config.max_position_amount_slippage_ratio:
                raise LiveDataIntegrityError(
                    f"entry fill/position amount mismatch: symbol={signal.symbol} order_id={fill.order_id} "
                    f"filled={fill.filled_amount} delta={position_delta_amount}"
                )
            actual_entry_price = float(fill.average_price)
            actual_stop_price = float(signal.stop_price)
            actual_initial_risk = actual_entry_price - actual_stop_price
            actual_initial_risk_pct = _safe_divide(actual_initial_risk, actual_entry_price)
            if not math.isfinite(actual_initial_risk) or actual_initial_risk <= 0.0:
                self.exchange.create_market_order(signal.symbol, "sell", position_delta_amount, reduce_only=True)
                raise LiveDataIntegrityError(
                    f"invalid actual initial risk after fill: symbol={signal.symbol} entry={actual_entry_price} stop={actual_stop_price}"
                )
            if not math.isfinite(actual_initial_risk_pct) or actual_initial_risk_pct > self.config.max_initial_risk_pct:
                self.exchange.create_market_order(signal.symbol, "sell", position_delta_amount, reduce_only=True)
                raise LiveDataIntegrityError(
                    f"actual initial risk too wide after fill: symbol={signal.symbol} risk_pct={actual_initial_risk_pct} "
                    f"max={self.config.max_initial_risk_pct}"
                )
            actual_base_tp1_price = actual_entry_price + actual_initial_risk
            actual_tp1_price, actual_tp1_round_step = _round_up_tp1_to_market_number(
                actual_base_tp1_price,
                reference_price=actual_entry_price,
                movement=max(actual_initial_risk, abs(float(signal.box_high) - actual_entry_price)),
            )
            actual_notional = actual_entry_price * position_delta_amount
            actual_risk_usdt = position_delta_amount * actual_initial_risk
            position_id = f"{signal.symbol.replace('/', '_').replace(':', '_')}_{signal.decision_timestamp_ms}_{fill.order_id}"
            try:
                stop_order_id = self._create_verified_position_stop_order(
                    signal.symbol,
                    amount=position_delta_amount,
                    stop_price=actual_stop_price,
                    position_id=position_id,
                    reason="initial_stop",
                )
            except Exception as exc:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=position_id,
                    reason=f"initial_stop_failed:{type(exc).__name__}",
                )
                raise
            opened_at_ms = int(fill.timestamp_ms)
            position = LivePosition(
                position_id=position_id,
                signal=signal,
                amount=float(position_delta_amount),
                notional_usdt=float(actual_notional),
                risk_usdt=float(actual_risk_usdt),
                entry_order_id=fill.order_id,
                stop_order_id=stop_order_id,
                opened_at_utc=datetime.fromtimestamp(opened_at_ms / 1000, UTC).isoformat(),
                opened_at_ms=opened_at_ms,
                entry_price=actual_entry_price,
                stop_price=actual_stop_price,
                tp1_price=actual_tp1_price,
                initial_risk=actual_initial_risk,
                entry_fill_timestamp_ms=int(fill.timestamp_ms),
                entry_order_submitted_at_ms=entry_order_submitted_at_ms,
                entry_order_status=fill.status,
                entry_filled_amount=float(fill.filled_amount),
                entry_cost_usdt=fill.cost,
                entry_fee_usdt=fill.fee_cost,
                pre_position_amount=pre_position_amount,
                post_position_amount=post_position_amount,
                position_delta_amount=position_delta_amount,
                remaining_amount=float(position_delta_amount),
                current_stop_price=actual_stop_price,
            )
            with self._state_lock:
                self._open_positions[symbol_key] = position
                self._opened_positions_total += 1
                self._active_symbols.pop(symbol_key, None)
            self._mark_signal_decision_consumed(signal, reason="position_opened")
        except Exception:
            with self._state_lock:
                self._opening_symbols.discard(symbol_key)
            raise
        finally:
            with self._state_lock:
                self._opening_symbols.discard(symbol_key)
        self.artifacts.append_position(position)
        self.artifacts.append_event(
            "position_opened",
            signal.symbol,
            {
                "position_id": position.position_id,
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "category_priority": signal.category_priority,
                "signal_entry_price": signal.entry_price,
                "actual_entry_price": position.entry_price,
                "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                "entry_order_submitted_at_ms": position.entry_order_submitted_at_ms,
                "entry_filled_amount": position.entry_filled_amount,
                "position_delta_amount": position.position_delta_amount,
                "base_tp1_price": _finite_or_none(actual_base_tp1_price),
                "tp1_price": position.tp1_price,
                "tp1_round_step": _finite_or_none(actual_tp1_round_step),
                "stop_price": position.stop_price,
            },
        )
        open_text = _format_open_message(position)
        open_chart_path = self._render_open_chart(position)
        try:
            if open_chart_path is not None:
                try:
                    position.telegram_open_message_id = self.telegram.send_photo_sync(
                        channel="positions",
                        photo_path=open_chart_path,
                        caption=open_text,
                    )
                    if position.telegram_open_message_id is None:
                        self.artifacts.append_event(
                            "telegram_open_chart_missing_id",
                            signal.symbol,
                            {"position_id": position.position_id, "chart_path": str(open_chart_path)},
                        )
                except Exception as exc:
                    self.artifacts.append_event(
                        "telegram_open_chart_failed",
                        signal.symbol,
                        {"position_id": position.position_id, "chart_path": str(open_chart_path), "reason": str(exc)},
                    )
                    self.logger(f"live: позиция {signal.symbol} открыта, но Telegram-график входа не отправлен: {exc}")
            if position.telegram_open_message_id is None:
                self.artifacts.append_event(
                    "telegram_open_text_fallback",
                    signal.symbol,
                    {
                        "position_id": position.position_id,
                        "reason": "chart_unavailable" if open_chart_path is None else "chart_photo_unavailable",
                    },
                )
                position.telegram_open_message_id = self.telegram.send_sync(
                    channel="positions",
                    text=open_text,
                )
                if position.telegram_open_message_id is None:
                    self.artifacts.append_event(
                        "telegram_open_text_missing_id",
                        signal.symbol,
                        {"position_id": position.position_id},
                    )
        except Exception as exc:
            self.artifacts.append_event("telegram_open_failed", signal.symbol, {"position_id": position.position_id, "reason": str(exc)})
            self.logger(f"live: позиция {signal.symbol} открыта, но Telegram-вход не отправлен: {exc}")
        self.logger(
            f"live: {_compact_symbol(signal.symbol)} открыт · {signal.levels_timeframe.value}/{signal.entry_timeframe.value} · "
            f"entry {position.entry_price:.6g} · риск {position.risk_usdt:.2f} · notional {position.notional_usdt:.2f}"
        )
        threading.Thread(
            target=self._monitor_position,
            args=(position,),
            name=f"position-{_compact_symbol(signal.symbol)}",
            daemon=True,
        ).start()

    def _validate_signal_freshness(self, signal: LiveSignal) -> bool:
        now_ms = int(time.time() * 1000)
        details = _decision_freshness_details(
            decision_timestamp_ms=int(signal.decision_timestamp_ms),
            signal_timeframe=signal.entry_timeframe,
            now_ms=now_ms,
            max_signal_age_ms=self.config.max_signal_age_ms,
        )
        if details["signal_age_ms"] < 0:
            self.artifacts.append_event("reject_signal_not_closed_yet", signal.symbol, details)
            return False
        if details["signal_age_ms"] > self.config.max_signal_age_ms:
            self._clear_active_symbol(signal.symbol, reason="stale_signal")
            self._mark_signal_decision_consumed(signal, reason="stale_signal")
            self.artifacts.append_event("reject_stale_signal", signal.symbol, {**details, "stage": "execution_guard"})
            self._notify_order_blocked(signal, event="reject_stale_signal", details=details)
            return False
        return True

    def _validate_signal_executable(self, signal: LiveSignal, *, live_price: float) -> bool:
        signed_drift_pct = _safe_divide(live_price - signal.entry_price, signal.entry_price)
        abs_drift_pct = abs(signed_drift_pct) if math.isfinite(signed_drift_pct) else float("nan")
        actual_risk = live_price - signal.stop_price
        actual_risk_pct = _safe_divide(actual_risk, live_price)
        rr_to_signal_tp1 = _safe_divide(signal.tp1_price - live_price, actual_risk)
        details = {
            "live_price": _finite_or_none(live_price),
            "signal_entry_price": _finite_or_none(signal.entry_price),
            "signal_tp1_price": _finite_or_none(signal.tp1_price),
            "stop_price": _finite_or_none(signal.stop_price),
            "drift_pct": _finite_or_none(signed_drift_pct),
            "abs_drift_pct": _finite_or_none(abs_drift_pct),
            "actual_risk_pct": _finite_or_none(actual_risk_pct),
            "rr_to_signal_tp1": _finite_or_none(rr_to_signal_tp1),
            "decision_timestamp_ms": signal.decision_timestamp_ms,
        }
        if not math.isfinite(live_price) or live_price <= 0.0:
            return self._reject_live_order(signal, event="reject_invalid_live_price", details=details)
        if live_price >= signal.tp1_price:
            return self._reject_live_order(signal, event="reject_tp1_already_reached", details=details)
        if not math.isfinite(actual_risk) or actual_risk <= 0.0:
            return self._reject_live_order(signal, event="reject_invalid_actual_risk_at_live_price", details=details)
        if not math.isfinite(actual_risk_pct) or actual_risk_pct > self.config.max_initial_risk_pct:
            return self._reject_live_order(
                signal,
                event="reject_actual_risk_too_wide_at_live_price",
                details={**details, "max_initial_risk_pct": self.config.max_initial_risk_pct},
            )
        if not math.isfinite(abs_drift_pct) or abs_drift_pct > self.config.max_entry_price_drift_pct:
            return self._reject_live_order(
                signal,
                event="reject_entry_price_drift",
                details={**details, "max_entry_price_drift_pct": self.config.max_entry_price_drift_pct},
            )
        if not math.isfinite(rr_to_signal_tp1) or rr_to_signal_tp1 < self.config.min_executable_rr_to_signal_tp1:
            return self._reject_live_order(
                signal,
                event="reject_rr_collapsed",
                details={**details, "min_executable_rr_to_signal_tp1": self.config.min_executable_rr_to_signal_tp1},
            )
        return True

    def _reject_live_order(self, signal: LiveSignal, *, event: str, details: dict[str, object]) -> bool:
        self._clear_active_symbol(signal.symbol, reason=event)
        self._mark_signal_decision_consumed(signal, reason=event)
        self.artifacts.append_event(event, signal.symbol, details)
        self._notify_order_blocked(signal, event=event, details=details)
        return False

    def _notify_order_blocked(self, signal: LiveSignal, *, event: str, details: dict[str, object]) -> None:
        try:
            self.telegram.send(
                channel="events",
                key=f"order_blocked:{event}:{_position_symbol_key(signal.symbol)}",
                text=_format_order_blocked_message(signal, event=event, details=details),
                symbol=signal.symbol,
            )
        except Exception as exc:
            self.artifacts.append_event(
                "telegram_order_blocked_enqueue_failed",
                signal.symbol,
                {"blocked_event": event, "reason": str(exc)},
            )

    def _send_or_edit_stop_message(self, position: LivePosition, *, text: str, stop_price: float, reason: str) -> None:
        if position.telegram_open_message_id is None:
            self.artifacts.append_event(
                "telegram_stop_message_skipped_no_parent",
                position.signal.symbol,
                {"position_id": position.position_id, "reason": reason, "stop_price": stop_price},
            )
            return
        if position.telegram_stop_message_id is None:
            try:
                message_id = self.telegram.send_sync(
                    channel="positions",
                    reply_to_message_id=position.telegram_open_message_id,
                    text=text,
                )
            except Exception as exc:
                self.artifacts.append_event(
                    "telegram_stop_message_send_failed",
                    position.signal.symbol,
                    {"position_id": position.position_id, "reason": reason, "stop_price": stop_price, "error": str(exc)},
                )
                self.logger(f"live: стоп-сообщение {position.signal.symbol} не отправлено: {exc}")
                return
            if message_id is None:
                self.artifacts.append_event(
                    "telegram_stop_message_missing_id",
                    position.signal.symbol,
                    {"position_id": position.position_id, "reason": reason, "stop_price": stop_price},
                )
                return
            position.telegram_stop_message_id = message_id
            self.artifacts.append_event(
                "telegram_stop_message_sent",
                position.signal.symbol,
                {"position_id": position.position_id, "message_id": message_id, "reason": reason, "stop_price": stop_price},
            )
            return
        try:
            edited_message_id = self.telegram.edit_sync(
                channel="positions",
                message_id=position.telegram_stop_message_id,
                text=text,
            )
        except Exception as exc:
            self.artifacts.append_event(
                "telegram_stop_message_edit_failed",
                position.signal.symbol,
                {
                    "position_id": position.position_id,
                    "message_id": position.telegram_stop_message_id,
                    "reason": reason,
                    "stop_price": stop_price,
                    "error": str(exc),
                },
            )
            self.logger(f"live: стоп-сообщение {position.signal.symbol} не отредактировано: {exc}")
            return
        if edited_message_id is None:
            self.artifacts.append_event(
                "telegram_stop_message_edit_missing_id",
                position.signal.symbol,
                {
                    "position_id": position.position_id,
                    "message_id": position.telegram_stop_message_id,
                    "reason": reason,
                    "stop_price": stop_price,
                },
            )
            return
        self.artifacts.append_event(
            "telegram_stop_message_edited",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "message_id": position.telegram_stop_message_id,
                "reason": reason,
                "stop_price": stop_price,
            },
        )

    def _monitor_position(self, position: LivePosition) -> None:
        signal = position.signal
        last_stop_price = position.stop_price
        empty_ohlcv_cycles = 0
        while True:
            try:
                actual_amount = abs(self.exchange.fetch_symbol_position_amount(signal.symbol))
                managed_amount = min(actual_amount, max(position.remaining_amount, 0.0))
                if actual_amount <= 0.0:
                    self.artifacts.append_event(
                        "position_closed_externally_unverified_exit_price",
                        signal.symbol,
                        {
                            "position_id": position.position_id,
                            "last_known_stop_price": position.current_stop_price,
                            "reason": "exchange_position_amount_zero_before_monitor_decision",
                        },
                    )
                    self._finalize_unresolved_position_exit(
                        position,
                        reason="позиция закрыта на бирже, но exit fill не восстановлен",
                        source_event="position_closed_externally_unverified_exit_price",
                        details={
                            "exchange_position_amount": actual_amount,
                            "last_known_stop_price": position.current_stop_price,
                        },
                        cancel_stop_order=True,
                        record_stop_cooldown=False,
                    )
                    return
                if managed_amount <= 0.0:
                    raise LiveDataIntegrityError(f"managed position amount is zero while exchange position is open: {position.position_id}")
                now_ms = int(time.time() * 1000)
                frame = self.exchange.fetch_ohlcv(
                    signal.symbol,
                    Timeframe.M1,
                    position.entry_fill_timestamp_ms,
                    now_ms,
                )
                if frame.empty:
                    empty_ohlcv_cycles += 1
                    details = {
                        "position_id": position.position_id,
                        "empty_ohlcv_cycles": empty_ohlcv_cycles,
                        "max_empty_ohlcv_cycles": self.config.max_monitor_empty_ohlcv_cycles,
                        "from_timestamp_ms": position.entry_fill_timestamp_ms,
                        "to_timestamp_ms": now_ms,
                    }
                    self.artifacts.append_event("position_monitor_empty_ohlcv", signal.symbol, details)
                    if empty_ohlcv_cycles >= self.config.max_monitor_empty_ohlcv_cycles:
                        raise LiveDataIntegrityError(
                            f"monitor OHLCV empty for {empty_ohlcv_cycles} consecutive cycles: position_id={position.position_id}"
                        )
                    time.sleep(15.0)
                    continue
                empty_ohlcv_cycles = 0
                latest = frame.sort_values("timestamp").iloc[-1]
                latest_high = float(latest["high"])
                latest_low = float(latest["low"])
                latest_close = float(latest["close"])
                if not position.tp1_done and latest_high >= position.tp1_price:
                    previous_remaining_amount = max(position.remaining_amount, 0.0)
                    close_amount = max(min(managed_amount, previous_remaining_amount) * 0.5, 0.0)
                    tp1_fill = None
                    if close_amount > 0.0:
                        tp1_fill = self.exchange.create_market_order_with_fill(signal.symbol, "sell", close_amount, reduce_only=True)
                        if tp1_fill.filled_amount <= 0.0 or not math.isfinite(tp1_fill.average_price):
                            raise LiveDataIntegrityError(
                                f"TP1 reduce-only fill invalid: position_id={position.position_id} order_id={tp1_fill.order_id}"
                            )
                        position.realized_pnl_usdt += tp1_fill.filled_amount * (tp1_fill.average_price - position.entry_price)
                        self.artifacts.append_event(
                            "tp1_partial_exit_filled",
                            signal.symbol,
                            {
                                "position_id": position.position_id,
                                "order_id": tp1_fill.order_id,
                                "status": tp1_fill.status,
                                "fill_timestamp_ms": tp1_fill.timestamp_ms,
                                "fill_price": tp1_fill.average_price,
                                "filled_amount": tp1_fill.filled_amount,
                                "requested_amount": close_amount,
                                "cost": tp1_fill.cost,
                                "fee_cost": tp1_fill.fee_cost,
                                "realized_pnl_usdt": position.realized_pnl_usdt,
                            },
                        )
                    time.sleep(2.0)
                    actual_after_tp1 = abs(self.exchange.fetch_symbol_position_amount(signal.symbol))
                    position.tp1_done = True
                    filled_close_amount = tp1_fill.filled_amount if tp1_fill is not None else close_amount
                    expected_remaining_amount = max(previous_remaining_amount - filled_close_amount, 0.0)
                    position.remaining_amount = min(actual_after_tp1, expected_remaining_amount)
                    if position.remaining_amount <= 0.0:
                        unresolved_threshold = previous_remaining_amount * self.config.max_position_amount_slippage_ratio
                        if expected_remaining_amount > max(unresolved_threshold, 1e-12):
                            unresolved_details = {
                                "position_id": position.position_id,
                                "previous_remaining_amount": previous_remaining_amount,
                                "tp1_filled_amount": filled_close_amount,
                                "expected_remaining_amount": expected_remaining_amount,
                                "exchange_position_amount_after_tp1": actual_after_tp1,
                                "realized_pnl_usdt": position.realized_pnl_usdt,
                            }
                            self.artifacts.append_event("tp1_remaining_exit_unresolved", signal.symbol, unresolved_details)
                            self._finalize_unresolved_position_exit(
                                position,
                                reason="TP1 fill подтверждён, но остаток позиции исчез без exit fill",
                                source_event="tp1_remaining_exit_unresolved",
                                details=unresolved_details,
                                cancel_stop_order=True,
                                record_stop_cooldown=False,
                            )
                            return
                        self._finalize_position(position, reason="TP1 закрыл позицию полностью", pnl_price=position.tp1_price, exit_amount=0.0)
                        return
                    self._replace_position_stop_order(
                        position,
                        amount=position.remaining_amount,
                        stop_price=position.entry_price,
                        reason="tp1_be",
                    )
                    last_stop_price = position.entry_price
                    self.artifacts.append_event("tp1_and_stop_to_be", signal.symbol, {"position_id": position.position_id})
                    self._send_or_edit_stop_message(
                        position,
                        stop_price=position.entry_price,
                        reason="tp1_be",
                        text=_format_stop_move_message(position, stop_price=position.entry_price, label="BE"),
                    )
                if position.tp1_done:
                    latest_ts = int(latest["timestamp"])
                    prior = frame.loc[frame["timestamp"].astype(int) < latest_ts].tail(self.config.trail_lookback_candles)
                    if not prior.empty:
                        trail_low = float(prior["low"].min())
                        structural_stop = trail_low - self.config.trail_buffer_r * position.initial_risk
                    else:
                        structural_stop = last_stop_price
                    new_stop = max(last_stop_price, structural_stop)
                    if new_stop > last_stop_price and new_stop < latest_close:
                        self._replace_position_stop_order(
                            position,
                            amount=min(actual_amount, position.remaining_amount),
                            stop_price=new_stop,
                            reason="structural_trail",
                        )
                        last_stop_price = new_stop
                        self._send_or_edit_stop_message(
                            position,
                            stop_price=new_stop,
                            reason="structural_trail",
                            text=_format_stop_move_message(position, stop_price=new_stop, label="SL"),
                        )
                if latest_low <= last_stop_price:
                    time.sleep(3.0)
                    if abs(self.exchange.fetch_symbol_position_amount(signal.symbol)) <= 0.0:
                        stop_exit_price = last_stop_price
                        stop_exit_reason = "стоп исполнен на бирже"
                        try:
                            stop_fill = self.exchange.fetch_order_fill(signal.symbol, position.stop_order_id)
                        except Exception as exc:
                            unresolved_details = {
                                "position_id": position.position_id,
                                "stop_order_id": position.stop_order_id,
                                "last_known_stop_price": last_stop_price,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                            self.artifacts.append_event("stop_exit_fill_unresolved", signal.symbol, unresolved_details)
                            self._finalize_unresolved_position_exit(
                                position,
                                reason="стоп закрыл позицию на бирже, но exit fill не восстановлен",
                                source_event="stop_exit_fill_unresolved",
                                details=unresolved_details,
                                cancel_stop_order=False,
                                record_stop_cooldown=True,
                            )
                            return
                        else:
                            stop_exit_price = float(stop_fill.average_price)
                            self.artifacts.append_event(
                                "stop_exit_filled",
                                signal.symbol,
                                {
                                    "position_id": position.position_id,
                                    "order_id": stop_fill.order_id,
                                    "status": stop_fill.status,
                                    "fill_timestamp_ms": stop_fill.timestamp_ms,
                                    "fill_price": stop_fill.average_price,
                                    "filled_amount": stop_fill.filled_amount,
                                    "cost": stop_fill.cost,
                                    "fee_cost": stop_fill.fee_cost,
                                },
                            )
                        self._finalize_position(position, reason=stop_exit_reason, pnl_price=stop_exit_price)
                        return
                time.sleep(15.0)
            except LiveDataIntegrityError as exc:
                self._record_position_integrity_error(position, reason=str(exc))
                return
            except Exception as exc:
                self.artifacts.append_event(
                    "position_monitor_error",
                    signal.symbol,
                    {"position_id": position.position_id, "reason": f"{type(exc).__name__}: {exc}"},
                )
                self.logger(f"live: позиция {signal.symbol}, временная ошибка ведения: {exc}")
                time.sleep(30.0)

    def _record_position_integrity_error(self, position: LivePosition, *, reason: str) -> None:
        symbol = position.signal.symbol
        details = {
            "position_id": position.position_id,
            "reason": reason,
            "entry_price": position.entry_price,
            "remaining_amount": position.remaining_amount,
            "stop_order_id": position.stop_order_id,
            "current_stop_price": position.current_stop_price,
        }
        self.artifacts.append_event("position_integrity_error", symbol, details)
        self.telegram.send(
            channel="events",
            key=f"position_integrity_error:{position.position_id}",
            text=_format_position_integrity_error_message(position, reason=reason),
            symbol=symbol,
        )
        self.logger(f"live: {_compact_symbol(symbol)} integrity error · {reason}")

    def _finalize_unresolved_position_exit(
        self,
        position: LivePosition,
        *,
        reason: str,
        source_event: str,
        details: dict[str, object],
        cancel_stop_order: bool,
        record_stop_cooldown: bool,
    ) -> None:
        symbol = position.signal.symbol
        symbol_key = _position_symbol_key(symbol)
        with self._state_lock:
            self._open_positions.pop(symbol_key, None)
            self._closed_positions_total += 1
            if record_stop_cooldown:
                self._recent_stops.setdefault(symbol_key, []).append(time.time())
        if cancel_stop_order:
            self._cancel_position_stop_order(position, reason=reason)
        event_details = {
            "position_id": position.position_id,
            "reason": reason,
            "source_event": source_event,
            "pnl_status": "unresolved",
            **details,
        }
        self.artifacts.append_event("position_exit_unresolved", symbol, event_details)
        self.artifacts.append_position_exit_unresolved(position, reason=reason)
        self.telegram.send(
            channel="events",
            key=f"position_exit_unresolved:{position.position_id}",
            text=_format_position_integrity_error_message(position, reason=reason),
            symbol=symbol,
        )
        self.logger(f"live: {_compact_symbol(symbol)} exit unresolved · {reason}")

    def _finalize_position(
        self,
        position: LivePosition,
        *,
        reason: str,
        pnl_price: float | None,
        exit_amount: float | None = None,
    ) -> None:
        symbol_key = _position_symbol_key(position.signal.symbol)
        with self._state_lock:
            self._open_positions.pop(symbol_key, None)
            self._closed_positions_total += 1
        exit_price = pnl_price if pnl_price is not None else position.entry_price
        terminal_exit_amount = max(position.remaining_amount, 0.0) if exit_amount is None else exit_amount
        if not math.isfinite(terminal_exit_amount) or terminal_exit_amount < 0.0:
            raise LiveDataIntegrityError(
                f"invalid terminal exit amount: position_id={position.position_id} exit_amount={terminal_exit_amount}"
            )
        pnl_usdt = position.realized_pnl_usdt + terminal_exit_amount * (exit_price - position.entry_price)
        pnl_pct = _safe_divide(pnl_usdt, position.notional_usdt)
        if reason.startswith("стоп"):
            with self._state_lock:
                self._recent_stops.setdefault(symbol_key, []).append(time.time())
        else:
            self._cancel_position_stop_order(position, reason=reason)
        self.artifacts.append_event(
            "position_closed",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "reason": reason,
                "pnl_pct": pnl_pct,
                "pnl_usdt": pnl_usdt,
                "realized_pnl_before_terminal_exit_usdt": position.realized_pnl_usdt,
                "terminal_exit_amount": terminal_exit_amount,
                "terminal_exit_price": exit_price,
            },
        )
        self.artifacts.append_position_close(position, reason=reason, pnl_usdt=pnl_usdt, pnl_pct=pnl_pct)
        exit_timestamp_ms = int(time.time() * 1000)
        chart_path = self._render_close_chart(
            position,
            exit_price=exit_price,
            exit_timestamp_ms=exit_timestamp_ms,
            reason=reason,
            pnl_pct=pnl_pct,
        )
        close_text = _format_close_message(position, pnl_usdt=pnl_usdt, pnl_pct=pnl_pct, exit_price=exit_price)
        if chart_path is not None:
            try:
                close_message_id = self.telegram.send_photo_sync(
                    channel="positions",
                    photo_path=chart_path,
                    caption=close_text,
                    reply_to_message_id=position.telegram_open_message_id,
                )
                if close_message_id is not None:
                    self.artifacts.append_event(
                        "telegram_close_photo_sent",
                        position.signal.symbol,
                        {"position_id": position.position_id, "message_id": close_message_id, "chart_path": str(chart_path)},
                    )
                    return
                self.artifacts.append_event(
                    "telegram_close_photo_missing_id",
                    position.signal.symbol,
                    {"position_id": position.position_id, "chart_path": str(chart_path)},
                )
            except Exception as exc:
                self.artifacts.append_event(
                    "telegram_close_photo_failed",
                    position.signal.symbol,
                    {
                        "position_id": position.position_id,
                        "chart_path": str(chart_path),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                    },
                )
                self.logger(f"live: позиция {position.signal.symbol} закрыта, но Telegram-график закрытия не отправлен: {exc}")
        self.artifacts.append_event(
            "telegram_close_text_fallback",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "reason": "chart_unavailable" if chart_path is None else "chart_photo_unavailable",
                "reply_to_message_id": position.telegram_open_message_id or "",
            },
        )
        self.telegram.send(
            channel="positions",
            key=f"close:{position.position_id}",
            reply_to_message_id=position.telegram_open_message_id,
            text=close_text,
            symbol=position.signal.symbol,
        )

    def _render_open_chart(self, position: LivePosition) -> Path | None:
        signal = position.signal
        try:
            from research_tools.anomaly_strategy_backtest import render_anomaly_trade_chart

            entry_timeframe_ms = int(signal.entry_timeframe.to_milliseconds())
            opened_at_ms = int(position.opened_at_ms)
            start_ms = int(signal.start_timestamp_ms) - max(35 * 60_000, 70 * entry_timeframe_ms)
            end_ms = max(int(time.time() * 1000), opened_at_ms + max(60_000, 4 * entry_timeframe_ms))
            frame = self._fetch_chart_frame(
                signal.symbol,
                signal.entry_timeframe,
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
            )
            if frame.empty:
                self.artifacts.append_event(
                    "chart_render_failed",
                    signal.symbol,
                    {"position_id": position.position_id, "stage": "open", "reason": "empty_plot_source_frame"},
                )
                return None

            try:
                hourly_context_frame = self._fetch_chart_frame(
                    signal.symbol,
                    Timeframe.H1,
                    start_timestamp_ms=opened_at_ms - 4 * 24 * HOUR_MS - HOUR_MS,
                    end_timestamp_ms=opened_at_ms + HOUR_MS,
                )
            except Exception as exc:
                hourly_context_frame = pd.DataFrame()
                self.artifacts.append_event(
                    "chart_context_fetch_failed",
                    signal.symbol,
                    {
                        "position_id": position.position_id,
                        "stage": "open",
                        "timeframe": Timeframe.H1.value,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )

            chart_dir = self.artifacts.root / "charts"
            chart_dir.mkdir(parents=True, exist_ok=True)
            path = chart_dir / f"{position.position_id}_open.png"
            trade_row = {
                "symbol": signal.symbol,
                "status": "open",
                "anomaly_timestamp_ms": int(signal.start_timestamp_ms),
                "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                "entry_timestamp_ms": int(position.opened_at_ms),
                "entry_timestamp_utc": position.opened_at_utc,
                "entry_price": float(position.entry_price),
                "signal_entry_price": float(signal.entry_price),
                "signal_entry_timestamp_ms": int(signal.decision_timestamp_ms),
                "initial_stop": float(position.stop_price),
                "tp1_price": float(position.tp1_price),
                "box_high": float(signal.box_high),
                "net_return": 0.0,
                "exit_reason": "open",
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "levels_timeframe": signal.levels_timeframe.value,
                "entry_timeframe": signal.entry_timeframe.value,
            }
            render_anomaly_trade_chart(
                frame=frame,
                trade=trade_row,
                output_path=path,
                context_timeframe_ms=int(signal.levels_timeframe.to_milliseconds()),
                hourly_context_frame=hourly_context_frame,
                draw_risk_reward_blocks=False,
                draw_exit_marker=False,
            )
            self.artifacts.append_event(
                "chart_rendered",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "stage": "open",
                    "chart_path": str(path),
                    "renderer": "anomaly_backtest_trade_chart",
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                },
            )
            return path
        except Exception as exc:
            self.artifacts.append_event(
                "chart_render_failed",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "stage": "open",
                    "renderer": "anomaly_backtest_trade_chart",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return None


    def _render_close_chart(
        self,
        position: LivePosition,
        *,
        exit_price: float,
        exit_timestamp_ms: int,
        reason: str,
        pnl_pct: float,
    ) -> Path | None:
        signal = position.signal
        try:
            from research_tools.anomaly_strategy_backtest import render_anomaly_trade_chart

            entry_timeframe_ms = int(signal.entry_timeframe.to_milliseconds())
            start_ms = int(signal.start_timestamp_ms) - max(35 * 60_000, 70 * entry_timeframe_ms)
            end_ms = max(int(time.time() * 1000), int(exit_timestamp_ms) + max(60_000, 4 * entry_timeframe_ms))
            frame = self._fetch_chart_frame(
                signal.symbol,
                signal.entry_timeframe,
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
            )
            if frame.empty:
                self.artifacts.append_event(
                    "chart_render_failed",
                    signal.symbol,
                    {"position_id": position.position_id, "reason": "empty_plot_source_frame"},
                )
                return None
            chart_dir = self.artifacts.root / "charts"
            chart_dir.mkdir(parents=True, exist_ok=True)
            path = chart_dir / f"{position.position_id}.png"
            try:
                hourly_context_frame = self._fetch_chart_frame(
                    signal.symbol,
                    Timeframe.H1,
                    start_timestamp_ms=end_ms - 4 * 24 * HOUR_MS - HOUR_MS,
                    end_timestamp_ms=end_ms + HOUR_MS,
                )
            except Exception as exc:
                hourly_context_frame = pd.DataFrame()
                self.artifacts.append_event(
                    "chart_context_fetch_failed",
                    signal.symbol,
                    {
                        "position_id": position.position_id,
                        "stage": "close",
                        "timeframe": Timeframe.H1.value,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
            trade_row = {
                "symbol": signal.symbol,
                "status": "closed",
                "anomaly_timestamp_ms": int(signal.start_timestamp_ms),
                "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                "entry_timestamp_ms": int(position.opened_at_ms),
                "entry_timestamp_utc": position.opened_at_utc,
                "exit_timestamp_ms": int(exit_timestamp_ms),
                "entry_price": float(position.entry_price),
                "signal_entry_price": float(signal.entry_price),
                "signal_entry_timestamp_ms": int(signal.decision_timestamp_ms),
                "initial_stop": float(position.stop_price),
                "tp1_price": float(position.tp1_price),
                "box_high": float(signal.box_high),
                "exit_price": float(exit_price),
                "net_return": float(pnl_pct),
                "exit_reason": reason,
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "levels_timeframe": signal.levels_timeframe.value,
                "entry_timeframe": signal.entry_timeframe.value,
            }
            render_anomaly_trade_chart(
                frame=frame,
                trade=trade_row,
                output_path=path,
                context_timeframe_ms=int(signal.levels_timeframe.to_milliseconds()),
                hourly_context_frame=hourly_context_frame,
            )
            self.artifacts.append_event(
                "chart_rendered",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "chart_path": str(path),
                    "renderer": "anomaly_backtest_trade_chart",
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                },
            )
            return path
        except Exception as exc:
            self.artifacts.append_event(
                "chart_render_failed",
                signal.symbol,
                {
                    "position_id": position.position_id,
                    "renderer": "anomaly_backtest_trade_chart",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return None


    def _fetch_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        if self._ohlcv_cache_storage is not None:
            return self._fetch_cached_chart_frame(
                symbol,
                timeframe,
                start_timestamp_ms=start_timestamp_ms,
                end_timestamp_ms=end_timestamp_ms,
            )
        return self._fetch_uncached_chart_frame(
            symbol,
            timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )

    def _fetch_uncached_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        if int(timeframe.to_milliseconds()) >= int(Timeframe.M1.to_milliseconds()):
            return self.exchange.fetch_ohlcv(symbol, timeframe, start_timestamp_ms, end_timestamp_ms)
        return self._fetch_aggtrade_chart_frame(
            symbol,
            timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )

    def _fetch_cached_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        storage = self._ohlcv_cache_storage
        if storage is None:
            return self._fetch_uncached_chart_frame(
                symbol,
                timeframe,
                start_timestamp_ms=start_timestamp_ms,
                end_timestamp_ms=end_timestamp_ms,
            )
        timeframe_ms = int(timeframe.to_milliseconds())
        cache_end_ms = _latest_closed_candle_start_ms(timeframe, now_ms=int(end_timestamp_ms))
        expected_start_ms = (int(start_timestamp_ms) // timeframe_ms) * timeframe_ms
        expected_end_ms = min(cache_end_ms, (int(end_timestamp_ms) // timeframe_ms) * timeframe_ms)
        memory_key = (_position_symbol_key(symbol), timeframe.value)
        if memory_key in self._live_ohlcv_frame_cache:
            load_status = "memory_hit"
            load_reason = "process_memory_cache"
            cached_rows_before = int(len(self._live_ohlcv_frame_cache[memory_key]))
            cached = self._live_ohlcv_frame_cache[memory_key]
        else:
            load_result = storage.load_result(symbol, timeframe)
            load_status = load_result.status
            load_reason = load_result.reason
            cached_rows_before = int(len(load_result.frame)) if load_result.frame is not None else 0
            cached = _prepare_cached_ohlcv_frame(load_result.frame)
            self._live_ohlcv_frame_cache[memory_key] = cached
        fetched_rows = 0
        added_rows = 0
        fetched_ranges: list[str] = []
        fetched_frames: list[pd.DataFrame] = []
        missing_ranges = _missing_ohlcv_ranges(
            cached,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
            timeframe_ms=timeframe_ms,
        )
        for missing_start_ms, missing_end_ms in missing_ranges:
            fetched = self._fetch_uncached_chart_frame(
                symbol,
                timeframe,
                start_timestamp_ms=missing_start_ms,
                end_timestamp_ms=missing_end_ms,
            )
            if not fetched.empty:
                fetched = fetched.copy()
                fetched["live_cache_source"] = (
                    "exchange_ohlcv" if timeframe_ms >= int(Timeframe.M1.to_milliseconds()) else "binance_futures_aggTrades"
                )
                fetched["live_cache_target_timeframe"] = timeframe.value
                fetched["live_cache_version"] = "p168_live_ohlcv_cache_v1"
                fetched_rows += int(len(fetched))
                fetched_ranges.append(f"{missing_start_ms}:{missing_end_ms}")
                fetched_frames.append(fetched)
        if fetched_frames:
            fetched_combined = _prepare_cached_ohlcv_frame(pd.concat(fetched_frames, ignore_index=True))
            if self.config.live_ohlcv_cache_write_enabled:
                added_rows = int(storage.save_incremental(symbol, timeframe, fetched_combined))
            cached = _prepare_cached_ohlcv_frame(pd.concat([cached, fetched_combined], ignore_index=True))
            self._live_ohlcv_frame_cache[memory_key] = cached
        window = cached.loc[
            (cached["timestamp"].astype("int64") >= int(start_timestamp_ms))
            & (cached["timestamp"].astype("int64") <= int(end_timestamp_ms))
        ].copy()
        remaining_ranges = _missing_ohlcv_ranges(
            window,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
            timeframe_ms=timeframe_ms,
        )
        cache_status = "hit" if not missing_ranges else "filled"
        if remaining_ranges:
            cache_status = "gap"
            self.artifacts.append_event(
                "live_ohlcv_cache_gap",
                symbol,
                {
                    "timeframe": timeframe.value,
                    "cache_status": load_status,
                    "cache_reason": load_reason,
                    "start_timestamp_ms": int(start_timestamp_ms),
                    "end_timestamp_ms": int(end_timestamp_ms),
                    "expected_start_ms": int(expected_start_ms),
                    "expected_end_ms": int(expected_end_ms),
                    "missing_ranges": [f"{start}:{end}" for start, end in remaining_ranges],
                    "fetched_ranges": fetched_ranges,
                    "fetched_rows": fetched_rows,
                    "added_rows": added_rows,
                    "write_enabled": bool(self.config.live_ohlcv_cache_write_enabled),
                },
            )
        self.artifacts.append_event(
            "live_ohlcv_cache_read",
            symbol,
            {
                "timeframe": timeframe.value,
                "status": cache_status,
                "load_status": load_status,
                "load_reason": load_reason,
                "cached_rows_before": cached_rows_before,
                "window_rows": int(len(window)),
                "missing_range_count": int(len(missing_ranges)),
                "remaining_gap_count": int(len(remaining_ranges)),
                "fetched_rows": fetched_rows,
                "added_rows": added_rows,
                "write_enabled": bool(self.config.live_ohlcv_cache_write_enabled),
            },
        )
        return window.sort_values("timestamp").reset_index(drop=True)

    def _fetch_aggtrade_chart_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        timeframe_ms = int(timeframe.to_milliseconds())
        if timeframe_ms <= 0 or timeframe_ms >= int(Timeframe.M1.to_milliseconds()):
            raise ValueError(f"invalid_seconds_chart_timeframe:{timeframe.value}")
        market_id = self.exchange.get_market_id(symbol)
        all_rows: list[dict[str, object]] = []
        next_from_id: int | None = None
        previous_last_id: int | None = None
        while True:
            params: dict[str, object] = {"symbol": market_id, "limit": 1000}
            if next_from_id is None:
                params["startTime"] = int(start_timestamp_ms)
                params["endTime"] = int(end_timestamp_ms)
            else:
                params["fromId"] = int(next_from_id)
            rows = self.exchange.fetch_binance_agg_trades(symbol=symbol, params=params)
            if not rows:
                break
            all_rows.extend(dict(row) for row in rows)
            last_row = rows[-1]
            last_id = _resolve_aggtrade_id(last_row)
            last_ts = _resolve_aggtrade_timestamp(last_row)
            if last_id is None or (previous_last_id is not None and last_id <= previous_last_id):
                break
            previous_last_id = last_id
            next_from_id = last_id + 1
            if last_ts is not None and last_ts > int(end_timestamp_ms):
                break
            if len(rows) < 1000:
                break
        if not all_rows:
            return pd.DataFrame(columns=list(REQUIRED_PRICE_COLUMNS) + ["volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"])
        return _aggregate_aggtrades_to_ohlcv_frame(
            pd.DataFrame(all_rows),
            timeframe_ms=timeframe_ms,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )

    def _reconcile_orphan_orders(self, symbols: list[str], *, cycle: int, force: bool = False) -> int:
        if not symbols:
            return 0
        if not force and cycle != 1 and cycle % self.config.order_reconcile_interval_cycles != 0:
            return 0
        checked_symbols: set[str] = set()
        cancelled_total = 0
        max_checks = len(symbols) if force else min(self.config.order_reconcile_batch_size, len(symbols))
        for _ in range(max_checks):
            symbol = symbols[self._order_reconcile_cursor % len(symbols)]
            self._order_reconcile_cursor += 1
            symbol_key = _position_symbol_key(symbol)
            if symbol_key in checked_symbols:
                continue
            checked_symbols.add(symbol_key)
            with self._state_lock:
                if symbol_key in self._open_positions or symbol_key in self._opening_symbols:
                    continue
            try:
                cancelled_total += self._cancel_orphan_orders_for_symbol(symbol, symbol_key=symbol_key)
            except Exception as exc:
                self.artifacts.append_event(
                    "orphan_order_reconcile_failed",
                    symbol,
                    {"symbol_key": symbol_key, "reason": f"{type(exc).__name__}: {exc}"},
                )
        if cancelled_total:
            with self._state_lock:
                self._orphan_orders_cancelled_total += cancelled_total
        return cancelled_total

    def _cancel_orphan_orders_for_symbol(self, symbol: str, *, symbol_key: str) -> int:
        orders = self.exchange.fetch_open_orders(symbol)
        order_ids = [_resolve_order_id(order) for order in orders]
        order_ids = [order_id for order_id in order_ids if order_id]
        if not order_ids:
            return 0
        actual_amount = abs(self.exchange.fetch_symbol_position_amount(symbol))
        if actual_amount > 0.0:
            self.artifacts.append_event(
                "orphan_order_reconcile_kept_with_position",
                symbol,
                {"symbol_key": symbol_key, "open_orders": len(order_ids), "exchange_position_amount": actual_amount},
            )
            return 0
        cancelled = 0
        failed = 0
        for order_id in order_ids:
            try:
                self.exchange.cancel_order(symbol, order_id)
                cancelled += 1
            except Exception as exc:
                failed += 1
                self.artifacts.append_event(
                    "orphan_order_cancel_failed",
                    symbol,
                    {"symbol_key": symbol_key, "order_id": order_id, "reason": f"{type(exc).__name__}: {exc}"},
                )
        self.artifacts.append_event(
            "orphan_orders_reconciled",
            symbol,
            {"symbol_key": symbol_key, "seen": len(order_ids), "cancelled": cancelled, "failed": failed},
        )
        return cancelled

    def _create_verified_position_stop_order(
        self,
        symbol: str,
        *,
        amount: float,
        stop_price: float,
        position_id: str,
        reason: str,
    ) -> str:
        if not math.isfinite(amount) or amount <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop order amount: position_id={position_id} amount={amount}")
        if not math.isfinite(stop_price) or stop_price <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop order price: position_id={position_id} stop_price={stop_price}")
        stop_order = self.exchange.create_stop_market_order(symbol, "sell", amount, stop_price)
        stop_order_id = str(stop_order.get("id") or "").strip()
        if not stop_order_id:
            raise LiveDataIntegrityError(f"stop order returned no id: position_id={position_id} symbol={symbol}")
        self._verify_open_stop_order(
            symbol,
            order_id=stop_order_id,
            expected_side="sell",
            expected_amount=amount,
            expected_stop_price=stop_price,
            position_id=position_id,
            reason=reason,
        )
        self.artifacts.append_event(
            "position_stop_order_verified",
            symbol,
            {
                "position_id": position_id,
                "order_id": stop_order_id,
                "stop_price": stop_price,
                "amount": amount,
                "reason": reason,
            },
        )
        return stop_order_id

    def _verify_open_stop_order(
        self,
        symbol: str,
        *,
        order_id: str,
        expected_side: str,
        expected_amount: float,
        expected_stop_price: float,
        position_id: str,
        reason: str,
    ) -> None:
        open_orders = self.exchange.fetch_open_orders(symbol)
        order = next(
            (row for row in open_orders if isinstance(row, dict) and str(row.get("id") or "").strip() == order_id),
            None,
        )
        if order is None:
            raise LiveDataIntegrityError(
                f"stop order not visible in open orders: position_id={position_id} order_id={order_id} reason={reason}"
            )
        side = _order_text_field(order, "side")
        if side is None or side.lower() != expected_side.lower():
            raise LiveDataIntegrityError(
                f"stop order side not verified: position_id={position_id} order_id={order_id} side={side!r} expected={expected_side}"
            )
        order_type = _order_text_field(order, "type")
        if order_type is None or "stop" not in order_type.lower():
            raise LiveDataIntegrityError(
                f"stop order type not verified: position_id={position_id} order_id={order_id} type={order_type!r}"
            )
        reduce_only = _order_bool_field(order, "reduceOnly")
        if reduce_only is not True:
            raise LiveDataIntegrityError(
                f"stop order reduceOnly not verified: position_id={position_id} order_id={order_id} reduceOnly={reduce_only!r}"
            )
        amount = _order_float_field(order, "amount", "origQty")
        amount_delta = abs(amount - expected_amount) if amount is not None else float("nan")
        amount_delta_ratio = _safe_divide(amount_delta, expected_amount) if amount is not None else float("nan")
        if amount is None or not math.isfinite(amount_delta_ratio) or amount_delta_ratio > self.config.max_position_amount_slippage_ratio:
            raise LiveDataIntegrityError(
                f"stop order amount not verified: position_id={position_id} order_id={order_id} "
                f"amount={amount!r} expected={expected_amount}"
            )
        stop_price = _order_float_field(order, "stopPrice")
        price_delta_ratio = _safe_divide(abs(stop_price - expected_stop_price), expected_stop_price) if stop_price is not None else float("nan")
        if stop_price is None or not math.isfinite(price_delta_ratio) or price_delta_ratio > 1e-4:
            raise LiveDataIntegrityError(
                f"stop order stopPrice not verified: position_id={position_id} order_id={order_id} "
                f"stopPrice={stop_price!r} expected={expected_stop_price}"
            )

    def _close_unprotected_entry_exposure(
        self,
        symbol: str,
        *,
        amount: float,
        position_id: str,
        reason: str,
    ) -> None:
        try:
            fill = self.exchange.create_market_order_with_fill(symbol, "sell", amount, reduce_only=True)
        except Exception as exc:
            self.artifacts.append_event(
                "unprotected_entry_reduce_only_exit_failed",
                symbol,
                {
                    "position_id": position_id,
                    "amount": amount,
                    "reason": reason,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise LiveDataIntegrityError(
                f"unprotected entry exposure could not be closed: position_id={position_id} symbol={symbol} reason={reason}"
            ) from exc
        self.artifacts.append_event(
            "unprotected_entry_reduce_only_exit_filled",
            symbol,
            {
                "position_id": position_id,
                "amount_requested": amount,
                "order_id": fill.order_id,
                "status": fill.status,
                "fill_timestamp_ms": fill.timestamp_ms,
                "fill_price": fill.average_price,
                "filled_amount": fill.filled_amount,
                "cost": fill.cost,
                "fee_cost": fill.fee_cost,
                "reason": reason,
            },
        )

    def _replace_position_stop_order(
        self,
        position: LivePosition,
        *,
        amount: float,
        stop_price: float,
        reason: str,
    ) -> None:
        old_stop_order_id = str(position.stop_order_id or "").strip()
        if not math.isfinite(amount) or amount <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop replacement amount: position_id={position.position_id} amount={amount}")
        if not math.isfinite(stop_price) or stop_price <= 0.0:
            raise LiveDataIntegrityError(f"invalid stop replacement price: position_id={position.position_id} stop_price={stop_price}")
        new_stop_order_id = self._create_verified_position_stop_order(
            position.signal.symbol,
            amount=amount,
            stop_price=stop_price,
            position_id=position.position_id,
            reason=reason,
        )
        cancel_error: str | None = None
        if old_stop_order_id:
            try:
                self.exchange.cancel_order(position.signal.symbol, old_stop_order_id)
            except Exception as exc:
                cancel_error = f"{type(exc).__name__}: {exc}"
        open_orders = self.exchange.fetch_open_orders(position.signal.symbol)
        open_order_ids = {str(order.get("id") or "").strip() for order in open_orders if isinstance(order, dict)}
        if cancel_error is not None and old_stop_order_id in open_order_ids:
            raise LiveDataIntegrityError(
                f"old stop cancel failed and old order remains open: position_id={position.position_id} "
                f"old_order_id={old_stop_order_id} error={cancel_error}"
            )
        position.stop_order_id = new_stop_order_id
        position.current_stop_price = stop_price
        self.artifacts.append_event(
            "position_stop_order_replaced",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "old_order_id": old_stop_order_id,
                "new_order_id": new_stop_order_id,
                "stop_price": stop_price,
                "amount": amount,
                "reason": reason,
                "old_cancel_error": cancel_error or "",
            },
        )

    def _cancel_position_stop_order(self, position: LivePosition, *, reason: str) -> None:
        order_id = str(position.stop_order_id or "").strip()
        if not order_id:
            return
        try:
            self.exchange.cancel_order(position.signal.symbol, order_id)
        except Exception as exc:
            self.artifacts.append_event(
                "position_stop_order_cancel_failed",
                position.signal.symbol,
                {"position_id": position.position_id, "order_id": order_id, "reason": reason, "error": f"{type(exc).__name__}: {exc}"},
            )
            return
        self.artifacts.append_event(
            "position_stop_order_cancelled",
            position.signal.symbol,
            {"position_id": position.position_id, "order_id": order_id, "reason": reason},
        )

    def _symbol_in_stop_cooldown(self, symbol: str) -> bool:
        symbol_key = _position_symbol_key(symbol)
        cutoff = time.time() - self.config.stop_cooldown_hours * 3600.0
        with self._state_lock:
            stops = [ts for ts in self._recent_stops.get(symbol_key, []) if ts >= cutoff]
            self._recent_stops[symbol_key] = stops
        return len(stops) >= self.config.stop_limit_per_symbol

    def _live_counts(self) -> tuple[int, int, int, int]:
        with self._state_lock:
            return (
                self._opened_positions_total,
                len(self._open_positions),
                self._closed_positions_total,
                self._orphan_orders_cancelled_total,
            )



def _validate_live_config_values(config: LiveAnomalyConfig) -> None:
    integer_minimums = {
        "baseline_candles": (config.baseline_candles, 1),
        "confirmation_candles": (config.confirmation_candles, 1),
        "min_hold_count": (config.min_hold_count, 1),
        "max_open_positions": (config.max_open_positions, 1),
        "symbol_batch_size": (config.symbol_batch_size, 1),
        "active_symbol_ttl_ms": (config.active_symbol_ttl_ms, 1),
        "ticker_radar_watch_ttl_ms": (config.ticker_radar_watch_ttl_ms, 1),
        "ticker_radar_max_promotions_per_cycle": (config.ticker_radar_max_promotions_per_cycle, 1),
        "signal_scan_backfill_candles": (config.signal_scan_backfill_candles, 1),
        "max_signal_age_ms": (config.max_signal_age_ms, 1),
        "stop_limit_per_symbol": (config.stop_limit_per_symbol, 1),
        "oi_fresh_ms": (config.oi_fresh_ms, 1),
        "trail_lookback_candles": (config.trail_lookback_candles, 1),
        "order_reconcile_interval_cycles": (config.order_reconcile_interval_cycles, 1),
        "order_reconcile_batch_size": (config.order_reconcile_batch_size, 1),
        "max_monitor_empty_ohlcv_cycles": (config.max_monitor_empty_ohlcv_cycles, 1),
    }
    for name, (value, minimum) in integer_minimums.items():
        if not isinstance(value, int) or value < minimum:
            raise LiveStartupError(f"Некорректный live config: {name} должен быть целым >= {minimum}, получено {value!r}")
    if not isinstance(config.ticker_radar_watch_batch_size, int) or config.ticker_radar_watch_batch_size < 0:
        raise LiveStartupError(
            "Некорректный live config: ticker_radar_watch_batch_size должен быть целым >= 0, "
            f"получено {config.ticker_radar_watch_batch_size!r}"
        )

    if config.inactive_scan_slots_per_cycle is not None and (
        not isinstance(config.inactive_scan_slots_per_cycle, int) or config.inactive_scan_slots_per_cycle < 0
    ):
        raise LiveStartupError(
            "invalid live config: inactive_scan_slots_per_cycle must be an integer >= 0 or None, "
            f"got {config.inactive_scan_slots_per_cycle!r}"
        )

    required_positive = {
        "min_quote_ratio_start": config.min_quote_ratio_start,
        "min_trade_ratio_start": config.min_trade_ratio_start,
        "max_initial_risk_pct": config.max_initial_risk_pct,
        "position_notional_usdt": config.position_notional_usdt,
        "network_sleep_seconds": config.network_sleep_seconds,
        "min_executable_rr_to_signal_tp1": config.min_executable_rr_to_signal_tp1,
        "max_position_amount_slippage_ratio": config.max_position_amount_slippage_ratio,
        "ticker_radar_interval_seconds": config.ticker_radar_interval_seconds,
        "ticker_radar_min_quote_volume_delta_ratio": config.ticker_radar_min_quote_volume_delta_ratio,
    }
    for name, value in required_positive.items():
        _require_finite_config_number(name, value, min_value=0.0, allow_equal_min=False)

    required_non_negative = {
        "min_price_retention": config.min_price_retention,
        "min_verticality_score": config.min_verticality_score,
        "stop_buffer_range_fraction": config.stop_buffer_range_fraction,
        "scan_sleep_seconds": config.scan_sleep_seconds,
        "stop_cooldown_hours": config.stop_cooldown_hours,
        "telegram_cooldown_seconds": config.telegram_cooldown_seconds,
        "trail_buffer_r": config.trail_buffer_r,
        "max_entry_price_drift_pct": config.max_entry_price_drift_pct,
        "ticker_radar_min_price_delta_pct": config.ticker_radar_min_price_delta_pct,
        "ticker_radar_min_quote_volume_delta_usdt": config.ticker_radar_min_quote_volume_delta_usdt,
    }
    for name, value in required_non_negative.items():
        _require_finite_config_number(name, value, min_value=0.0, allow_equal_min=True)

    optional_positive = {
        "max_start_quote_ratio": config.max_start_quote_ratio,
        "max_start_trade_ratio": config.max_start_trade_ratio,
        "max_start_avg_trade_quote_size_ratio": config.max_start_avg_trade_quote_size_ratio,
        "max_start_quote_ratio_per_abs_return": config.max_start_quote_ratio_per_abs_return,
        "max_start_range_pct_ratio_to_baseline": config.max_start_range_pct_ratio_to_baseline,
        "min_next_taker_buy_quote_share": config.min_next_taker_buy_quote_share,
        "max_price_retention": config.max_price_retention,
        "min_oi_change_pct_3x5m": config.min_oi_change_pct_3x5m,
        "max_prior_up_down_whipsaw_to_impulse_range": config.max_prior_up_down_whipsaw_to_impulse_range,
    }
    for name, value in optional_positive.items():
        if value is not None:
            _require_finite_config_number(name, value, min_value=0.0, allow_equal_min=False)

    if config.max_cycles is not None and (not isinstance(config.max_cycles, int) or config.max_cycles < 0):
        raise LiveStartupError(f"Некорректный live config: max_cycles должен быть целым >= 0, получено {config.max_cycles!r}")


def _require_finite_config_number(name: str, value: float, *, min_value: float, allow_equal_min: bool) -> None:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LiveStartupError(f"Некорректный live config: {name} должен быть числом, получено {value!r}") from exc
    if not math.isfinite(number):
        raise LiveStartupError(f"Некорректный live config: {name} должен быть finite, получено {value!r}")
    if allow_equal_min:
        valid = number >= min_value
        relation = ">="
    else:
        valid = number > min_value
        relation = ">"
    if not valid:
        raise LiveStartupError(f"Некорректный live config: {name} должен быть {relation} {min_value}, получено {number!r}")


def build_telegram_config_from_env() -> TelegramConfig:
    values = {
        "TELEGRAM_EVENTS_BOT_TOKEN": os.getenv("TELEGRAM_EVENTS_BOT_TOKEN", "").strip(),
        "TELEGRAM_EVENTS_CHAT_ID": os.getenv("TELEGRAM_EVENTS_CHAT_ID", "").strip(),
        "TELEGRAM_POSITIONS_BOT_TOKEN": os.getenv("TELEGRAM_POSITIONS_BOT_TOKEN", "").strip(),
        "TELEGRAM_POSITIONS_CHAT_ID": os.getenv("TELEGRAM_POSITIONS_CHAT_ID", "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise LiveStartupError(f"Не заполнены Telegram env переменные: {', '.join(missing)}")
    return TelegramConfig(
        events_bot_token=values["TELEGRAM_EVENTS_BOT_TOKEN"],
        events_chat_id=values["TELEGRAM_EVENTS_CHAT_ID"],
        positions_bot_token=values["TELEGRAM_POSITIONS_BOT_TOKEN"],
        positions_chat_id=values["TELEGRAM_POSITIONS_CHAT_ID"],
    )


def _resolve_live_pump_categories(category_ids: tuple[str, ...]) -> tuple[LivePumpCategory, ...]:
    resolved: list[LivePumpCategory] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw_category_id in category_ids:
        category_id = raw_category_id.strip()
        if not category_id or category_id in seen:
            continue
        category = SUPPORTED_LIVE_PUMP_CATEGORIES.get(category_id)
        if category is None:
            unknown.append(category_id)
            continue
        resolved.append(category)
        seen.add(category_id)
    if unknown:
        raise LiveStartupError(
            "Неизвестные live pump categories: "
            f"{', '.join(sorted(unknown))}. Доступны: {', '.join(sorted(SUPPORTED_LIVE_PUMP_CATEGORIES))}"
        )
    return tuple(sorted(resolved, key=lambda item: item.priority))


def _category_value(category: LivePumpCategory, config: LiveAnomalyConfig, field_name: str) -> float | None:
    value = getattr(category, field_name)
    if value is not None:
        return value
    return getattr(config, field_name)


def _latest_closed_candle_start_ms(timeframe: Timeframe, *, now_ms: int) -> int:
    timeframe_ms = int(timeframe.to_milliseconds())
    if timeframe_ms <= 0:
        raise ValueError(f"invalid timeframe milliseconds: {timeframe.value}")
    return ((int(now_ms) - timeframe_ms) // timeframe_ms) * timeframe_ms


def _prepare_cached_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame(columns=list(REQUIRED_PRICE_COLUMNS) + ["volume", "quote_volume", "number_of_trades"])
    prepared = frame.copy()
    for column in prepared.columns:
        if column == "timestamp" or column in REQUIRED_PRICE_COLUMNS or column in REQUIRED_FLOW_COLUMNS or column in OPTIONAL_FLOW_COLUMNS:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.loc[prepared["timestamp"].notna()].copy()
    prepared["timestamp"] = prepared["timestamp"].astype("int64")
    return prepared.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _missing_ohlcv_ranges(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int,
) -> list[tuple[int, int]]:
    if timeframe_ms <= 0 or int(end_timestamp_ms) < int(start_timestamp_ms):
        return []
    expected = range(int(start_timestamp_ms), int(end_timestamp_ms) + 1, int(timeframe_ms))
    present = set()
    if frame is not None and not frame.empty and "timestamp" in frame.columns:
        present = set(pd.to_numeric(frame["timestamp"], errors="coerce").dropna().astype("int64").tolist())
    ranges: list[tuple[int, int]] = []
    range_start: int | None = None
    previous_missing: int | None = None
    for timestamp_ms in expected:
        if int(timestamp_ms) in present:
            if range_start is not None and previous_missing is not None:
                ranges.append((range_start, previous_missing))
                range_start = None
                previous_missing = None
            continue
        if range_start is None:
            range_start = int(timestamp_ms)
        previous_missing = int(timestamp_ms)
    if range_start is not None and previous_missing is not None:
        ranges.append((range_start, previous_missing))
    return ranges


def _decision_freshness_details(
    *,
    decision_timestamp_ms: int,
    now_ms: int,
    max_signal_age_ms: int,
    levels_timeframe: Timeframe | None = None,
    signal_timeframe: Timeframe | None = None,
) -> dict[str, object]:
    resolved_timeframe = signal_timeframe or levels_timeframe
    if resolved_timeframe is None:
        raise ValueError("decision freshness requires a timeframe")
    signal_timeframe_ms = int(resolved_timeframe.to_milliseconds())
    decision_available_ms = int(decision_timestamp_ms) + signal_timeframe_ms
    signal_age_ms = int(now_ms) - decision_available_ms
    return {
        "decision_timestamp_ms": int(decision_timestamp_ms),
        "decision_available_ms": decision_available_ms,
        "now_ms": int(now_ms),
        "signal_age_ms": signal_age_ms,
        "max_signal_age_ms": int(max_signal_age_ms),
    }


BLOCKED_ORDER_REASON_LABELS = {
    "reject_stale_signal": "сигнал устарел",
    "reject_invalid_live_price": "live-price невалидный",
    "reject_tp1_already_reached": "TP1 уже достигнут",
    "reject_invalid_actual_risk_at_live_price": "риск от live-price невалидный",
    "reject_actual_risk_too_wide_at_live_price": "риск от live-price слишком широкий",
    "reject_entry_price_drift": "live-price слишком далеко от цены сигнала",
    "reject_rr_collapsed": "RR до TP1 развалился",
}


def _format_order_blocked_message(signal: LiveSignal, *, event: str, details: dict[str, object]) -> str:
    reason = BLOCKED_ORDER_REASON_LABELS.get(event, event)
    return (
        f"{_symbol_emoji(signal.symbol)} "
        f"<b>{_telegram_symbol_link(signal.symbol)} Позиция не открыта</b>\n\n"
        f"{_telegram_escape(reason)}\n\n"
        f"{_telegram_signal_context(signal)}"
    )


def _format_position_integrity_error_message(position: LivePosition, *, reason: str) -> str:
    return (
        f"{_symbol_emoji(position.signal.symbol)} "
        f"<b>{_telegram_symbol_link(position.signal.symbol)} Ошибка ведения позиции</b>\n\n"
        f"{_telegram_code(reason)}\n\n"
        f"ID: {_telegram_code(position.position_id)}"
    )

def _order_info(order: dict[str, object]) -> dict[str, object]:
    info = order.get("info")
    return info if isinstance(info, dict) else {}


def _order_text_field(order: dict[str, object], key: str) -> str | None:
    value = order.get(key)
    if value is None or value == "":
        value = _order_info(order).get(key)
    if value is None or value == "":
        return None
    return str(value)


def _order_float_field(order: dict[str, object], *keys: str) -> float | None:
    info = _order_info(order)
    for key in keys:
        for source in (order, info):
            value = source.get(key)
            if value is None or value == "":
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed):
                return parsed
    return None


def _order_bool_field(order: dict[str, object], key: str) -> bool | None:
    value = order.get(key)
    if value is None or value == "":
        value = _order_info(order).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    return None


def _format_open_message(position: LivePosition) -> str:
    signal = position.signal
    entry_price = float(position.entry_price)
    tp_pct = _safe_divide(float(position.tp1_price) - entry_price, entry_price)
    sl_pct = _safe_divide(entry_price - float(position.stop_price), entry_price)
    weaknesses = _format_weaknesses(signal.weaknesses)
    return (
        f"{_symbol_emoji(signal.symbol)} <b>{_telegram_symbol_link(signal.symbol)} LONG</b>\n\n"
        f"Сигнал: {_format_price(signal.entry_price)}\n\n"
        f"Вход: {_format_price(entry_price)}\n\n"
        f"TP1: {_format_price(position.tp1_price)} {_format_percent(tp_pct)}\n"
        f"SL: {_format_price(position.stop_price)} {_format_percent(sl_pct)}\n\n"
        f"Препятствия: {weaknesses}\n\n"
        f"{_telegram_signal_context(signal)}"
    )


def _format_close_message(position: LivePosition, *, pnl_usdt: float, pnl_pct: float, exit_price: float) -> str:
    exit_zone = _format_exit_zone(position, exit_price=exit_price)
    return (
        f"{_symbol_emoji(position.signal.symbol)} "
        f"<b>{_telegram_symbol_link(position.signal.symbol)} {_format_usdt(pnl_usdt)} USDT</b>\n\n"
        f"PNL {_format_percent(pnl_pct, signed=False)}\n\n"
        f"{exit_zone}"
    )


def _format_stop_move_message(position: LivePosition, *, stop_price: float, label: str) -> str:
    stop_distance_from_entry = _safe_divide(float(stop_price) - float(position.entry_price), float(position.entry_price))
    display_label = _format_stop_zone(position, stop_price=stop_price, fallback_label=label)
    return (
        f"{_symbol_emoji(position.signal.symbol)} "
        f"<b>{_telegram_symbol_link(position.signal.symbol)} {display_label} "
        f"{_format_percent(stop_distance_from_entry, signed=True, precision=2)}</b>"
    )


def _nice_market_round_step(*, reference_price: float, movement: float) -> float:
    if not math.isfinite(reference_price) or reference_price <= 0.0:
        return float("nan")
    raw_step = max(abs(float(movement)) * 0.25, abs(float(reference_price)) * 0.0002, 1e-12)
    exponent = math.floor(math.log10(raw_step))
    base = 10.0 ** exponent
    normalized = raw_step / base
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        if normalized <= multiplier:
            return multiplier * base
    return 10.0 * base


def _round_up_tp1_to_market_number(base_tp1_price: float, *, reference_price: float, movement: float) -> tuple[float, float]:
    if not math.isfinite(base_tp1_price) or base_tp1_price <= 0.0:
        return base_tp1_price, float("nan")
    step = _nice_market_round_step(reference_price=reference_price, movement=movement)
    if not math.isfinite(step) or step <= 0.0:
        return base_tp1_price, float("nan")
    rounded = math.ceil((base_tp1_price - step * 1e-9) / step) * step
    tolerance = max(abs(float(base_tp1_price)) * 1e-12, step * 1e-9)
    if rounded <= base_tp1_price + tolerance:
        rounded += step
    decimals = max(0, int(math.ceil(-math.log10(step))) + 2) if step < 1.0 else 8
    rounded = round(float(rounded), min(decimals, 12))
    return rounded, float(step)

def _format_timeframe_pairs(timeframe_pairs: tuple[tuple[Timeframe, Timeframe], ...]) -> str:
    return ", ".join(f"{levels.value}/{entry.value}" for levels, entry in timeframe_pairs)


def _position_symbol_key(symbol: str) -> str:
    return _compact_symbol(symbol)


def _resolve_order_id(order: dict[str, object]) -> str | None:
    value = order.get("id")
    if value is None:
        info = order.get("info")
        if isinstance(info, dict):
            value = info.get("orderId") or info.get("clientOrderId")
    if value is None:
        return None
    order_id = str(value).strip()
    return order_id or None


def _resolve_aggtrade_timestamp(row: dict[str, object]) -> int | None:
    for column in ("transact_time", "T"):
        if column not in row:
            continue
        try:
            return int(row[column])
        except (TypeError, ValueError):
            return None
    return None


def _fill_missing_ohlcv_buckets(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int,
    seed_close: float,
) -> pd.DataFrame:
    if timeframe_ms <= 0 or end_timestamp_ms < start_timestamp_ms:
        return frame.copy()
    full_index = pd.DataFrame(
        {"timestamp": list(range(int(start_timestamp_ms), int(end_timestamp_ms) + int(timeframe_ms), int(timeframe_ms)))}
    )
    merged = full_index.merge(frame.copy(), on="timestamp", how="left")
    merged["close"] = pd.to_numeric(merged["close"], errors="coerce").ffill().fillna(float(seed_close))
    for price_column in ("open", "high", "low"):
        merged[price_column] = pd.to_numeric(merged[price_column], errors="coerce").fillna(merged["close"])
    for volume_column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if volume_column not in merged.columns:
            merged[volume_column] = 0.0
        merged[volume_column] = pd.to_numeric(merged[volume_column], errors="coerce").fillna(0.0)
    return merged.reset_index(drop=True)


def _aggregate_frame_to_candle(frame: pd.DataFrame, *, timestamp_ms: int) -> pd.Series | None:
    if frame.empty:
        return None
    required = [column for column in REQUIRED_PRICE_COLUMNS if column in frame.columns]
    if len(required) < len(REQUIRED_PRICE_COLUMNS):
        return None
    ordered = frame.copy().sort_values("timestamp")
    row: dict[str, object] = {
        "timestamp": int(timestamp_ms),
        "open": float(ordered.iloc[0]["open"]),
        "high": float(pd.to_numeric(ordered["high"], errors="coerce").max()),
        "low": float(pd.to_numeric(ordered["low"], errors="coerce").min()),
        "close": float(ordered.iloc[-1]["close"]),
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in ordered.columns:
            row[column] = float(pd.to_numeric(ordered[column], errors="coerce").fillna(0.0).sum())
    return pd.Series(row)

def _resolve_aggtrade_id(row: dict[str, object]) -> int | None:
    for column in ("aggregate_trade_id", "a"):
        if column not in row:
            continue
        try:
            return int(row[column])
        except (TypeError, ValueError):
            return None
    return None


def _aggregate_aggtrades_to_ohlcv_frame(
    trades: pd.DataFrame,
    *,
    timeframe_ms: int,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_quote_volume",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    timestamp_column = "transact_time" if "transact_time" in trades.columns else "T"
    price_column = "price" if "price" in trades.columns else "p"
    quantity_column = "quantity" if "quantity" in trades.columns else "q"
    maker_column = "is_buyer_maker" if "is_buyer_maker" in trades.columns else "m"
    required = (timestamp_column, price_column, quantity_column, maker_column)
    missing = [column for column in required if column not in trades.columns]
    if missing:
        return pd.DataFrame(columns=columns)
    work = trades.copy()
    work["timestamp"] = pd.to_numeric(work[timestamp_column], errors="coerce")
    work["price"] = pd.to_numeric(work[price_column], errors="coerce")
    work["quantity"] = pd.to_numeric(work[quantity_column], errors="coerce")
    work = work.loc[
        work["timestamp"].notna()
        & work["price"].notna()
        & work["quantity"].notna()
        & (work["timestamp"] >= int(start_timestamp_ms))
        & (work["timestamp"] <= int(end_timestamp_ms))
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)
    work["timestamp"] = work["timestamp"].astype("int64")
    work["bucket"] = (work["timestamp"] // int(timeframe_ms)) * int(timeframe_ms)
    work["quote_volume"] = work["price"].astype("float64") * work["quantity"].astype("float64")
    buyer_is_maker = work[maker_column].astype(str).str.lower().isin(("true", "1"))
    work["taker_buy_quote_volume"] = work["quote_volume"].where(~buyer_is_maker, 0.0)
    aggregated = (
        work.groupby("bucket", sort=True)
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
            quote_volume=("quote_volume", "sum"),
            number_of_trades=("quantity", "size"),
            taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
        )
        .reset_index()
        .rename(columns={"bucket": "timestamp"})
    )
    first_bucket = int((int(start_timestamp_ms) // int(timeframe_ms)) * int(timeframe_ms))
    last_bucket = int((int(end_timestamp_ms) // int(timeframe_ms)) * int(timeframe_ms))
    full_index = pd.DataFrame({"timestamp": list(range(first_bucket, last_bucket + int(timeframe_ms), int(timeframe_ms)))})
    aggregated = full_index.merge(aggregated, on="timestamp", how="left")
    aggregated["close"] = aggregated["close"].ffill().bfill()
    for price_column in ("open", "high", "low"):
        aggregated[price_column] = aggregated[price_column].fillna(aggregated["close"])
    for volume_column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        aggregated[volume_column] = aggregated[volume_column].fillna(0.0)
    return aggregated.loc[:, columns].reset_index(drop=True)

def _telegram_signal_context(signal: LiveSignal) -> str:
    return _telegram_code(
        f"{signal.levels_timeframe.value}/{signal.entry_timeframe.value} · "
        f"{signal.category_label} · {signal.session}"
    )


def _symbol_emoji(symbol: str) -> str:
    compact = _compact_symbol(symbol).upper()
    digest = hashlib.sha256(compact.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(ANIMAL_EMOJIS)
    return ANIMAL_EMOJIS[index]


QUOTE_SYMBOL_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD")


def _compact_symbol(symbol: str) -> str:
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


def _coinglass_url(symbol: str) -> str:
    return f"https://www.coinglass.com/tv/Binance_{_compact_symbol(symbol)}USDT"


def _telegram_symbol_link(symbol: str) -> str:
    compact = _telegram_escape(_compact_symbol(symbol))
    return f'<a href="{_coinglass_url(symbol)}">{compact}</a>'


def _format_weaknesses(weaknesses: list[str]) -> str:
    if not weaknesses:
        return "нет"
    return ", ".join(_telegram_escape(str(item)) for item in weaknesses)


def _format_exit_zone(position: LivePosition, *, exit_price: float) -> str:
    if not position.tp1_done:
        return "SL"
    entry_price = float(position.entry_price)
    if math.isfinite(exit_price) and math.isfinite(entry_price):
        tolerance = max(abs(entry_price), 1.0) * 1e-4
        if abs(float(exit_price) - entry_price) <= tolerance:
            return "BE"
    return _format_stop_zone(position, stop_price=exit_price, fallback_label="SL")


def _format_stop_zone(position: LivePosition, *, stop_price: float, fallback_label: str) -> str:
    if not position.tp1_done:
        return fallback_label
    price = float(stop_price)
    tp1_price = float(position.tp1_price)
    if not math.isfinite(price) or not math.isfinite(tp1_price):
        return fallback_label
    return "TP-" if price < tp1_price else "TP+"


def _format_price(value: float) -> str:
    return f"{float(value):.6g}"


def _format_percent(value: float, *, signed: bool = False, precision: int = 1) -> str:
    if not math.isfinite(value):
        return "n/a"
    pct = float(value) * 100.0
    sign = "+" if signed and pct >= 0.0 else ""
    return f"{sign}{pct:.{precision}f}%"


def _format_usdt(value: float) -> str:
    sign = "+" if value >= 0.0 else "-"
    rendered = f"{abs(float(value)):.3f}".rstrip("0").rstrip(".")
    return f"{sign}{rendered.replace('.', ',')}"


def _series_float_or_none(row: pd.Series, column: str) -> float | None:
    if column not in row.index:
        return None
    return _finite_or_none(row.get(column))


def _row_float_or_none(row: dict[str, object], column: str) -> float | None:
    return _finite_or_none(row.get(column))


def _finite_or_blank(value: float | None) -> float | str:
    finite = _finite_or_none(value)
    return finite if finite is not None else ""


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_divide(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return float(numerator / denominator)


def _median_positive(values: list[float]) -> float | None:
    finite_positive = sorted(float(value) for value in values if math.isfinite(float(value)) and float(value) > 0.0)
    if not finite_positive:
        return None
    mid = len(finite_positive) // 2
    if len(finite_positive) % 2:
        return finite_positive[mid]
    return (finite_positive[mid - 1] + finite_positive[mid]) / 2.0


def _prior_up_down_whipsaw_to_impulse_range(baseline: pd.DataFrame, *, impulse_range: float) -> float:
    if baseline.empty or not math.isfinite(impulse_range) or impulse_range <= 0.0:
        return float("nan")
    highs = baseline["high"].astype(float).to_numpy()
    lows = baseline["low"].astype(float).to_numpy()
    if highs.size == 0 or lows.size == 0:
        return float("nan")
    high_pos = max(range(len(highs)), key=lambda pos: highs[pos] if math.isfinite(highs[pos]) else -math.inf)
    lows_before = [float(value) for value in lows[: high_pos + 1] if math.isfinite(float(value))]
    lows_after = [float(value) for value in lows[high_pos:] if math.isfinite(float(value))]
    if not lows_before or not lows_after or not math.isfinite(float(highs[high_pos])):
        return float("nan")
    low_before_high = min(lows_before)
    low_after_high = min(lows_after)
    high_value = float(highs[high_pos])
    up_leg = high_value - low_before_high
    down_leg = high_value - low_after_high
    return min(_safe_divide(up_leg, impulse_range), _safe_divide(down_leg, impulse_range))

def _session_name(timestamp_ms: int) -> str:
    hour = datetime.fromtimestamp(timestamp_ms / 1000, UTC).hour
    if 0 <= hour < 7:
        return "Азия"
    if 7 <= hour < 13:
        return "Европа"
    if 13 <= hour < 21:
        return "Америка"
    return "поздняя Америка/Азия"


def _telegram_escape(value: str) -> str:
    return html.escape(str(value), quote=False)


def _telegram_code(value: str) -> str:
    return f"<code>{_telegram_escape(str(value))}</code>"
