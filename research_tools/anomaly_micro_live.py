"""Strict REST-only micro-live runner for the anomaly wake-up research strategy."""

from __future__ import annotations

import asyncio
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
from typing import Callable, Protocol

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
CACHED_OHLCV_DTYPES = {
    "timestamp": "int64",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "quote_volume": "float64",
    "number_of_trades": "float64",
    "taker_buy_quote_volume": "float64",
}
HOUR_MS = 60 * 60 * 1000
BINANCE_FUTURES_ALL_TICKER_WS_URL = "wss://fstream.binance.com/market/ws/!ticker@arr"
BINANCE_FUTURES_COMBINED_WS_URL = "wss://fstream.binance.com/market/stream"
DEFAULT_LIVE_WS_AGGTRADE_MAX_BACKFILL_MS = 360_000
DEFAULT_LIVE_WS_HEALTH_BOOTSTRAP_SECONDS = 180.0
DEFAULT_LIVE_OHLCV_CACHE_FLUSH_MAX_SYMBOL_TIMEFRAMES = 4
DEFAULT_LIVE_AGGTRADE_REST_CACHE_TTL_MS = 20 * 60_000
DEFAULT_LIVE_AGGTRADE_REST_CACHE_PADDING_MS = 60_000
DANGER_DEFAULT_INACTIVE_COLD_COVERAGE_SLOTS_PER_CYCLE = 5
DANGER_ADAPTIVE_COLD_COVERAGE_MAX_SLOTS_PER_CYCLE = 10
DANGER_ADAPTIVE_COLD_COVERAGE_MIN_SCORE = 0.30
DANGER_ADAPTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO = 0.95
DANGER_ADAPTIVE_COLD_COVERAGE_FULL_WS_HEALTH_RATIO = 0.995
DANGER_ADAPTIVE_COLD_COVERAGE_FAST_CYCLE_SECONDS = 2.0
DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS = 8.0
DANGER_ADAPTIVE_COLD_COVERAGE_CYCLE_EWMA_ALPHA = 0.25
DANGER_ADAPTIVE_COLD_COVERAGE_PRESSURE_EWMA_ALPHA = 0.25
DANGER_ADAPTIVE_COLD_COVERAGE_ACTIVE_WAITING_SOFT_CAP = 3
DANGER_ADAPTIVE_COLD_COVERAGE_NETWORK_CALLS_HIGH = 4
DANGER_ADAPTIVE_COLD_COVERAGE_REST_FETCHED_MS_HIGH = 120_000
DANGER_ADAPTIVE_COLD_COVERAGE_PENDING_GAPS_HIGH = 3
DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO = DANGER_ADAPTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO
DANGER_INACTIVE_COLD_COVERAGE_SOURCE = "DANGER_default_precise_cold_coverage_subminute"
EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE = "explicit_precise_cold_coverage"
COLD_COVERAGE_GATED_OFF_SOURCE = "precise_cold_coverage_gated_off"
RETRYABLE_CATEGORY_STATUS_PREFIXES = (
    "mark_context_",
    "no_mark_before_decision",
    "oi_",
    "context=",
    "fetch_failed:",
    "fetch_empty",
    "cache_",
    "empty:",
    "missing_",
)
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
    "first_executable_entry_timestamp_ms",
    "entry_lag_ms",
    "entry_lag_ltf_candles",
    "entered_late_vs_first_executable",
    "entry_order_submit_lag_ms",
    "entry_order_submit_lag_ltf_candles",
    "previous_live_scan_closed_timestamp_ms",
    "first_unscanned_decision_timestamp_ms",
    "live_scan_gap_ltf_candles",
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


class LiveWsAggTradeCoveragePending(RuntimeError):
    """Raised when strict WS aggTrade coverage is insufficient for subminute signal evaluation."""

    def __init__(
        self,
        message: str,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        missing_ranges: tuple[tuple[int, int], ...],
        status: str,
        reason: str | None,
    ) -> None:
        super().__init__(message)
        self.symbol = symbol
        self.start_timestamp_ms = int(start_timestamp_ms)
        self.end_timestamp_ms = int(end_timestamp_ms)
        self.missing_ranges = missing_ranges
        self.status = status
        self.reason = reason


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
    min_oi_change_pct_3x5m: float | None = None
    min_mark_close_vs_decision_close_basis: float | None = None
    max_start_quote_ratio: float | None = None
    max_start_trade_ratio: float | None = None
    max_start_avg_trade_quote_size_ratio: float | None = None
    max_start_quote_ratio_per_abs_return: float | None = None
    max_start_trade_ratio_per_abs_return: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = None
    min_next_taker_buy_quote_share: float | None = None
    max_start_taker_buy_quote_share_delta: float | None = None
    max_price_retention: float | None = None
    min_flow_hold_count: int | None = None
    min_start_lower_wick_to_range: float | None = None
    max_start_upper_wick_to_range: float | None = None
    max_prior_fast_fade_count_72h: int | None = None


@dataclass(frozen=True, slots=True)
class AggTradeRawRange:
    start_timestamp_ms: int
    end_timestamp_ms: int
    rows: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class WsAggTradeReadResult:
    rows: tuple[dict[str, object], ...]
    missing_ranges: tuple[tuple[int, int], ...]
    status: str
    reason: str | None
    subscribed: bool
    connection_status: str
    last_error: str | None
    last_trade_timestamp_ms: int | None
    last_receive_at_ms: int | None
    buffer_row_count: int


@dataclass(frozen=True, slots=True)
class LiveOiChangeResult:
    value: float | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LiveMarkBasisResult:
    value: float | None
    reason: str | None = None
    timestamp_ms: int | None = None
    age_ms: int | None = None


LIVE_CATEGORY_CONTRACT = "live_category_overlay_v5_retryable_dependencies_cold_coverage"
LIVE_DEFAULT_PUMP_CATEGORY_IDS: tuple[str, ...] = (
    "runner_oi_confirmed",
    "runner_flow",
    "runner_reclaim",
    "runner_balanced",
)
LIVE_TIMEFRAME_CATEGORY_PRIORITY: dict[tuple[str, str], tuple[str, ...]] = {
    ("5m", "30s"): ("runner_flow", "runner_oi_confirmed", "runner_reclaim", "runner_balanced"),
    ("1m", "15s"): ("runner_oi_confirmed", "runner_flow", "runner_reclaim", "runner_balanced"),
    ("1m", "5s"): ("runner_oi_confirmed", "runner_flow", "runner_balanced", "runner_reclaim"),
}


class LiveTickerSnapshotSource(Protocol):
    @property
    def source_id(self) -> str:
        """Stable diagnostics id for the ticker snapshot source."""

    def fetch_snapshots(self, symbols: tuple[str, ...]) -> list[ExchangeTickerSnapshot]:
        """Return ticker snapshots for the requested live universe."""


@dataclass(frozen=True, slots=True)
class RestLiveTickerSnapshotSource:
    exchange: CcxtFuturesClient

    @property
    def source_id(self) -> str:
        return "rest_fetch_tickers"

    def fetch_snapshots(self, symbols: tuple[str, ...]) -> list[ExchangeTickerSnapshot]:
        return self.exchange.fetch_ticker_snapshots(symbols)


def _optional_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ws_error_short_label(error: str | None) -> str:
    text = str(error or "")
    lowered = text.lower()
    if "clientconnectordnserror" in lowered or "could not contact dns servers" in lowered:
        return "dns"
    if "timeout" in lowered:
        return "timeout"
    if "ssl" in lowered:
        return "ssl"
    if "proxy" in lowered:
        return "proxy"
    if "connection refused" in lowered or "connect call failed" in lowered:
        return "connect"
    return "error" if text else ""


def _retryable_category_reasons(category_rejections: list[dict[str, object]]) -> tuple[str, ...]:
    retryable: list[str] = []
    seen: set[str] = set()
    for row in category_rejections:
        reason = str(row.get("category_reject_reason") or row.get("reason") or "")
        if reason == "reject_prior_fast_fade_filter_unavailable":
            status_values = (
                str(row.get("coverage_reason") or ""),
                str(row.get("detail_reason") or ""),
            )
            is_retryable = any(value.startswith(RETRYABLE_CATEGORY_STATUS_PREFIXES) for value in status_values if value)
        elif reason == "reject_mark_basis_below_min":
            status = str(row.get("mark_basis_status") or "")
            is_retryable = bool(status and status != "ok" and status.startswith(("mark_context_", "no_mark_before_decision")))
        elif reason == "reject_oi":
            status = str(row.get("oi_status") or "")
            is_retryable = bool(status and status != "below_threshold" and status.startswith("oi_"))
        else:
            is_retryable = False
        if is_retryable and reason not in seen:
            retryable.append(reason)
            seen.add(reason)
    return tuple(retryable)


def _aiohttp_ws_connector() -> object:
    import aiohttp

    # Force aiohttp to use the same OS getaddrinfo resolver path as REST/ccxt.
    # In environments where aiodns/c-ares cannot contact DNS servers, REST can work
    # while aiohttp WebSockets fail with ClientConnectorDNSError. This is not a
    # data fallback: WS still has to connect or report the real transport error.
    return aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), ttl_dns_cache=300)


class BinanceWsAllTickerSnapshotSource:
    source_id = "binance_ws_all_ticker"

    def __init__(
        self,
        *,
        exchange: CcxtFuturesClient,
        stale_ms: int,
        startup_wait_seconds: float,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.exchange = exchange
        self.stale_ms = int(stale_ms)
        self.startup_wait_seconds = float(startup_wait_seconds)
        self.logger = logger
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._ticker_by_market_id: dict[str, ExchangeTickerSnapshot] = {}
        self._last_message_at_ms: int | None = None
        self._last_error: str | None = None
        self._connection_status = "starting"
        self._last_payload_source = "none"
        self._seeded_at_ms: int | None = None
        self._seeded_count = 0
        self._startup_wait_until_monotonic = time.monotonic() + max(0.0, self.startup_wait_seconds)
        self._thread = threading.Thread(target=self._run_thread, name="binance-ws-all-ticker", daemon=True)
        self._thread.start()

    def fetch_snapshots(self, symbols: tuple[str, ...]) -> list[ExchangeTickerSnapshot]:
        if not symbols:
            return []
        wait_seconds = max(0.0, self._startup_wait_until_monotonic - time.monotonic())
        if not self._ready_event.wait(timeout=wait_seconds):
            raise RuntimeError(self._status_reason("ws_ticker_not_ready"))
        now_ms = int(time.time() * 1000)
        with self._lock:
            last_message_at_ms = self._last_message_at_ms
            last_error = self._last_error
            connection_status = self._connection_status
        if last_message_at_ms is None:
            raise RuntimeError(self._status_reason("ws_ticker_no_messages"))
        if now_ms - int(last_message_at_ms) > self.stale_ms:
            raise RuntimeError(self._status_reason("ws_ticker_stale"))
        snapshots: list[ExchangeTickerSnapshot] = []
        with self._lock:
            ticker_by_market_id = dict(self._ticker_by_market_id)
        for symbol in symbols:
            market_id = self.exchange.get_market_id(symbol)
            snapshot = ticker_by_market_id.get(market_id)
            if snapshot is None:
                snapshots.append(
                    ExchangeTickerSnapshot(
                        symbol=symbol,
                        fetched_at_ms=now_ms,
                        last_price=None,
                        quote_volume_24h=None,
                        trade_count_24h=None,
                        last_price_source="binance_ws_all_ticker",
                        quote_volume_source="binance_ws_all_ticker",
                        trade_count_source="binance_ws_all_ticker",
                        status="missing",
                        reason=f"ws_ticker_missing_market_id:{market_id}",
                    )
                )
            else:
                snapshots.append(
                    ExchangeTickerSnapshot(
                        symbol=symbol,
                        fetched_at_ms=snapshot.fetched_at_ms,
                        last_price=snapshot.last_price,
                        quote_volume_24h=snapshot.quote_volume_24h,
                        trade_count_24h=snapshot.trade_count_24h,
                        last_price_source=snapshot.last_price_source,
                        quote_volume_source=snapshot.quote_volume_source,
                        trade_count_source=snapshot.trade_count_source,
                        status=snapshot.status,
                        reason=snapshot.reason,
                    )
                )
        return snapshots

    def seed_from_snapshots(self, snapshots: list[ExchangeTickerSnapshot]) -> dict[str, object]:
        now_ms = int(time.time() * 1000)
        updates: dict[str, ExchangeTickerSnapshot] = {}
        ok_count = 0
        missing_count = 0
        for snapshot in snapshots:
            if snapshot.status != "ok":
                missing_count += 1
                continue
            try:
                market_id = str(self.exchange.get_market_id(snapshot.symbol)).strip().upper()
            except Exception:
                missing_count += 1
                continue
            if not market_id:
                missing_count += 1
                continue
            ok_count += 1
            updates[market_id] = ExchangeTickerSnapshot(
                symbol=snapshot.symbol,
                fetched_at_ms=now_ms,
                last_price=snapshot.last_price,
                quote_volume_24h=snapshot.quote_volume_24h,
                trade_count_24h=snapshot.trade_count_24h,
                last_price_source=f"rest_startup_seed.{snapshot.last_price_source}",
                quote_volume_source=f"rest_startup_seed.{snapshot.quote_volume_source}",
                trade_count_source=f"rest_startup_seed.{snapshot.trade_count_source}",
                status="ok",
                reason=None,
            )
        with self._lock:
            if updates:
                self._ticker_by_market_id.update(updates)
                self._last_message_at_ms = now_ms
                self._last_error = None
                self._last_payload_source = "rest_startup_seed"
                self._seeded_at_ms = now_ms
                self._seeded_count = len(updates)
                self._ready_event.set()
            return {
                "seeded_count": len(updates),
                "ok_count": ok_count,
                "missing_count": missing_count,
                "seeded_at_ms": self._seeded_at_ms if updates else "",
            }

    def source_status(self) -> tuple[str, str]:
        with self._lock:
            if self._last_payload_source == "rest_startup_seed":
                return (
                    "primary_seeded_rest",
                    f"seeded_at_ms={self._seeded_at_ms if self._seeded_at_ms is not None else ''};"
                    f"seeded_count={self._seeded_count}",
                )
        return "primary", ""

    def close(self) -> None:
        self._stop_event.set()

    def _status_reason(self, reason: str) -> str:
        with self._lock:
            parts = [
                reason,
                f"status={self._connection_status}",
                f"last_message_at_ms={self._last_message_at_ms if self._last_message_at_ms is not None else ''}",
            ]
            if self._last_error:
                parts.append(f"last_error={self._last_error[:240]}")
        return ";".join(parts)

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._run_ws_loop())
            except Exception as exc:
                self._set_status("error", f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            if not self._stop_event.is_set():
                time.sleep(2.0)

    async def _run_ws_loop(self) -> None:
        import aiohttp

        self._set_status("connecting", None)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
        connector = _aiohttp_ws_connector()
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.ws_connect(BINANCE_FUTURES_ALL_TICKER_WS_URL, heartbeat=20) as ws:
                self._set_status("connected", None)
                async for message in ws:
                    if self._stop_event.is_set():
                        await ws.close()
                        return
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self._handle_ws_payload(message.data)
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                        self._set_status("closed", "ws_closed")
                        return
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        self._set_status("error", f"ws_error:{ws.exception()}")
                        return

    def _handle_ws_payload(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._set_status("payload_error", f"json:{exc}")
            return
        rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            self._set_status("payload_error", f"unexpected_payload:{type(payload).__name__}")
            return
        now_ms = int(time.time() * 1000)
        updates: dict[str, ExchangeTickerSnapshot] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            market_id = str(row.get("s", "")).strip().upper()
            if not market_id:
                continue
            last_price = _optional_float(row.get("c"))
            quote_volume = _optional_float(row.get("q"))
            trade_count = _optional_int(row.get("n"))
            status = "ok" if last_price is not None and quote_volume is not None else "missing_fields"
            reason = None if status == "ok" else "ws_ticker_missing_last_or_quote_volume"
            updates[market_id] = ExchangeTickerSnapshot(
                symbol=market_id,
                fetched_at_ms=now_ms,
                last_price=last_price,
                quote_volume_24h=quote_volume,
                trade_count_24h=trade_count,
                last_price_source="binance_ws_all_ticker",
                quote_volume_source="binance_ws_all_ticker",
                trade_count_source="binance_ws_all_ticker",
                status=status,
                reason=reason,
            )
        if not updates:
            self._set_status("payload_error", "no_valid_ticker_rows")
            return
        with self._lock:
            self._ticker_by_market_id.update(updates)
            self._last_message_at_ms = now_ms
            self._last_error = None
            self._last_payload_source = "ws"
            self._connection_status = "connected"
            self._ready_event.set()

    def _set_status(self, status: str, error: str | None) -> None:
        with self._lock:
            self._connection_status = status
            self._last_error = error


class BinanceWsAggTradeBuffer:
    source_id = "binance_ws_aggtrade"

    def __init__(
        self,
        *,
        exchange: CcxtFuturesClient,
        buffer_minutes: int,
        stale_ms: int,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.exchange = exchange
        self.buffer_ms = max(60_000, int(buffer_minutes) * 60_000)
        self.stale_ms = int(stale_ms)
        self.logger = logger
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._target_market_ids: set[str] = set()
        self._symbol_by_market_id: dict[str, str] = {}
        self._subscribed_market_ids: set[str] = set()
        self._pending_subscribe_request_ids: dict[int, list[str]] = {}
        self._pending_unsubscribe_request_ids: dict[int, list[str]] = {}
        self._rows_by_market_id: dict[str, deque[dict[str, object]]] = {}
        self._active_since_ms_by_market_id: dict[str, int] = {}
        self._covered_until_ms_by_market_id: dict[str, int] = {}
        self._gap_ranges_by_market_id: dict[str, list[tuple[int, int]]] = {}
        self._last_aggtrade_id_by_market_id: dict[str, int] = {}
        self._last_trade_timestamp_by_market_id: dict[str, int] = {}
        self._last_receive_at_by_market_id: dict[str, int] = {}
        self._connection_status = "starting"
        self._last_error: str | None = None
        self._subscription_request_id = 0
        self._thread = threading.Thread(target=self._run_thread, name="binance-ws-aggtrade", daemon=True)
        self._thread.start()

    def set_symbols(self, symbols: tuple[str, ...] | list[str] | set[str]) -> dict[str, object]:
        target_market_ids: set[str] = set()
        symbol_by_market_id: dict[str, str] = {}
        for symbol in symbols:
            try:
                market_id = self.exchange.get_market_id(symbol)
            except Exception:
                continue
            market_id = str(market_id).strip().upper()
            if not market_id:
                continue
            target_market_ids.add(market_id)
            symbol_by_market_id[market_id] = str(symbol)
        now_ms = int(time.time() * 1000)
        with self._lock:
            removed = self._target_market_ids - target_market_ids
            self._target_market_ids = target_market_ids
            self._symbol_by_market_id = symbol_by_market_id
            for market_id in removed:
                self._rows_by_market_id.pop(market_id, None)
                self._active_since_ms_by_market_id.pop(market_id, None)
                self._covered_until_ms_by_market_id.pop(market_id, None)
                self._gap_ranges_by_market_id.pop(market_id, None)
                self._last_aggtrade_id_by_market_id.pop(market_id, None)
                self._last_trade_timestamp_by_market_id.pop(market_id, None)
                self._last_receive_at_by_market_id.pop(market_id, None)
            return {
                "source": self.source_id,
                "target_count": len(target_market_ids),
                "subscribed_count": len(self._subscribed_market_ids),
                "connection_status": self._connection_status,
                "last_error": self._last_error or "",
                "updated_at_ms": now_ms,
                "target_symbols": [symbol_by_market_id[market_id] for market_id in sorted(target_market_ids)],
            }

    def read_rows(self, symbol: str, *, start_timestamp_ms: int, end_timestamp_ms: int) -> WsAggTradeReadResult:
        market_id = str(self.exchange.get_market_id(symbol)).strip().upper()
        request_start = int(start_timestamp_ms)
        request_end = int(end_timestamp_ms)
        if request_start > request_end:
            return WsAggTradeReadResult((), (), "empty_request", None, False, "empty_request", None, None, None, 0)
        now_ms = int(time.time() * 1000)
        with self._lock:
            rows_deque = self._rows_by_market_id.get(market_id, deque())
            rows = [dict(row) for row in rows_deque]
            subscribed = market_id in self._subscribed_market_ids
            connection_status = self._connection_status
            last_error = self._last_error
            gap_ranges = list(self._gap_ranges_by_market_id.get(market_id, []))
            active_since_ms = self._active_since_ms_by_market_id.get(market_id)
            covered_until_ms = self._covered_until_ms_by_market_id.get(market_id)
            last_trade_timestamp_ms = self._last_trade_timestamp_by_market_id.get(market_id)
            last_receive_at_ms = self._last_receive_at_by_market_id.get(market_id)
            buffer_row_count = len(rows_deque)
        filtered = _filter_aggtrade_rows_by_time(
            rows,
            start_timestamp_ms=request_start,
            end_timestamp_ms=request_end,
        )
        filtered = _dedupe_aggtrade_rows(filtered)
        if not subscribed:
            return WsAggTradeReadResult(
                tuple(filtered),
                ((request_start, request_end),),
                "not_subscribed",
                "symbol_not_in_ws_subscription",
                False,
                connection_status,
                last_error,
                last_trade_timestamp_ms,
                last_receive_at_ms,
                buffer_row_count,
            )
        if connection_status != "connected":
            return WsAggTradeReadResult(
                tuple(filtered),
                ((request_start, request_end),),
                "not_connected",
                f"ws_status={connection_status}",
                True,
                connection_status,
                last_error,
                last_trade_timestamp_ms,
                last_receive_at_ms,
                buffer_row_count,
            )
        if covered_until_ms is not None and now_ms - int(covered_until_ms) > self.stale_ms:
            return WsAggTradeReadResult(
                tuple(filtered),
                ((request_start, request_end),),
                "stale",
                f"coverage_age_ms={now_ms - int(covered_until_ms)}",
                True,
                connection_status,
                last_error,
                last_trade_timestamp_ms,
                last_receive_at_ms,
                buffer_row_count,
            )
        coverage_start_ms = int(active_since_ms) if active_since_ms is not None else request_end + 1
        coverage_end_ms = int(covered_until_ms) if covered_until_ms is not None else now_ms
        missing_ranges = self._missing_ranges_from_coverage(
            request_start=request_start,
            request_end=request_end,
            coverage_start=coverage_start_ms,
            coverage_end=coverage_end_ms,
        )
        missing_ranges.extend(
            (max(request_start, gap_start), min(request_end, gap_end))
            for gap_start, gap_end in gap_ranges
            if max(request_start, gap_start) <= min(request_end, gap_end)
        )
        missing_ranges = self._merge_time_ranges(missing_ranges)
        status = "covered" if not missing_ranges else "partial"
        reason = None if not missing_ranges else "ws_rows_do_not_cover_full_requested_interval"
        return WsAggTradeReadResult(
            tuple(filtered),
            tuple(missing_ranges),
            status,
            reason,
            True,
            connection_status,
            last_error,
            last_trade_timestamp_ms,
            last_receive_at_ms,
            buffer_row_count,
        )

    def add_backfill_rows(
        self,
        symbol: str,
        rows: list[dict[str, object]],
        *,
        start_timestamp_ms: int | None = None,
        end_timestamp_ms: int | None = None,
    ) -> None:
        market_id = str(self.exchange.get_market_id(symbol)).strip().upper()
        now_ms = int(time.time() * 1000)
        with self._lock:
            if start_timestamp_ms is not None and end_timestamp_ms is not None:
                self._active_since_ms_by_market_id[market_id] = min(
                    int(start_timestamp_ms),
                    int(self._active_since_ms_by_market_id.get(market_id, start_timestamp_ms)),
                )
                self._covered_until_ms_by_market_id[market_id] = max(
                    int(end_timestamp_ms),
                    int(self._covered_until_ms_by_market_id.get(market_id, end_timestamp_ms)),
                )
                self._remove_gap_coverage_locked(
                    market_id,
                    start_timestamp_ms=int(start_timestamp_ms),
                    end_timestamp_ms=int(end_timestamp_ms),
                )
            if not rows:
                return
            rows_deque = self._rows_by_market_id.setdefault(market_id, deque())
            for row in rows:
                row_copy = dict(row)
                timestamp_ms = _resolve_aggtrade_timestamp(row_copy)
                if timestamp_ms is None:
                    continue
                rows_deque.append(row_copy)
                self._last_trade_timestamp_by_market_id[market_id] = max(
                    int(timestamp_ms),
                    int(self._last_trade_timestamp_by_market_id.get(market_id, timestamp_ms)),
                )
                self._last_receive_at_by_market_id[market_id] = now_ms
            self._prune_market_locked(market_id, now_ms=now_ms)

    def close(self) -> None:
        self._stop_event.set()

    def wait_for_targets(self, *, timeout_seconds: float) -> dict[str, object]:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            with self._lock:
                target = set(self._target_market_ids)
                subscribed = set(self._subscribed_market_ids)
                status = self._connection_status
                if target.issubset(subscribed) or time.monotonic() >= deadline or status != "connected":
                    return {
                        "target_count": len(target),
                        "subscribed_count": len(subscribed),
                        "connection_status": status,
                        "last_error": self._last_error or "",
                    }
            time.sleep(0.01)

    @staticmethod
    def _missing_ranges_from_rows(
        rows: list[dict[str, object]],
        *,
        request_start: int,
        request_end: int,
    ) -> list[tuple[int, int]]:
        if not rows:
            return [(request_start, request_end)]
        timestamps = [
            int(timestamp_ms)
            for timestamp_ms in (_resolve_aggtrade_timestamp(row) for row in rows)
            if timestamp_ms is not None
        ]
        if not timestamps:
            return [(request_start, request_end)]
        first_ts = min(timestamps)
        last_ts = max(timestamps)
        missing: list[tuple[int, int]] = []
        if request_start < first_ts:
            missing.append((request_start, first_ts - 1))
        if last_ts < request_end:
            missing.append((last_ts + 1, request_end))
        return missing

    @staticmethod
    def _missing_ranges_from_coverage(
        *,
        request_start: int,
        request_end: int,
        coverage_start: int,
        coverage_end: int,
    ) -> list[tuple[int, int]]:
        missing: list[tuple[int, int]] = []
        if coverage_start > request_start:
            missing.append((request_start, min(request_end, coverage_start - 1)))
        if coverage_end < request_end:
            missing.append((max(request_start, coverage_end + 1), request_end))
        return [(start, end) for start, end in missing if start <= end]

    @staticmethod
    def _merge_time_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        normalized = sorted((int(start), int(end)) for start, end in ranges if int(start) <= int(end))
        if not normalized:
            return []
        merged = [normalized[0]]
        for start, end in normalized[1:]:
            if start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _remove_gap_coverage_locked(self, market_id: str, *, start_timestamp_ms: int, end_timestamp_ms: int) -> None:
        remaining: list[tuple[int, int]] = []
        for gap_start, gap_end in self._gap_ranges_by_market_id.get(market_id, []):
            if int(end_timestamp_ms) < gap_start or int(start_timestamp_ms) > gap_end:
                remaining.append((gap_start, gap_end))
                continue
            if int(start_timestamp_ms) > gap_start:
                remaining.append((gap_start, int(start_timestamp_ms) - 1))
            if int(end_timestamp_ms) < gap_end:
                remaining.append((int(end_timestamp_ms) + 1, gap_end))
        if remaining:
            self._gap_ranges_by_market_id[market_id] = remaining
        else:
            self._gap_ranges_by_market_id.pop(market_id, None)

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._run_ws_loop())
            except Exception as exc:
                self._set_status("error", f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            if not self._stop_event.is_set():
                time.sleep(2.0)

    async def _run_ws_loop(self) -> None:
        import aiohttp

        self._set_status("connecting", None)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
        connector = _aiohttp_ws_connector()
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.ws_connect(BINANCE_FUTURES_COMBINED_WS_URL, heartbeat=20) as ws:
                with self._lock:
                    self._subscribed_market_ids = set()
                    self._pending_subscribe_request_ids = {}
                    self._pending_unsubscribe_request_ids = {}
                self._set_status("connected", None)
                while not self._stop_event.is_set():
                    await self._sync_subscriptions(ws)
                    try:
                        message = await asyncio.wait_for(ws.receive(), timeout=0.25)
                    except asyncio.TimeoutError:
                        self._mark_subscribed_coverage_until(int(time.time() * 1000))
                        continue
                    self._mark_subscribed_coverage_until(int(time.time() * 1000))
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self._handle_ws_payload(message.data)
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                        self._set_status("closed", "ws_closed")
                        return
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        self._set_status("error", f"ws_error:{ws.exception()}")
                        return
                await ws.close()

    def _mark_subscribed_coverage_until(self, timestamp_ms: int) -> None:
        with self._lock:
            if self._connection_status != "connected":
                return
            for market_id in self._subscribed_market_ids:
                self._covered_until_ms_by_market_id[market_id] = max(
                    int(timestamp_ms),
                    int(self._covered_until_ms_by_market_id.get(market_id, timestamp_ms)),
                )

    async def _sync_subscriptions(self, ws: object) -> None:
        with self._lock:
            target = set(self._target_market_ids)
            subscribed = set(self._subscribed_market_ids)
            pending_subscribe = {market_id for market_ids in self._pending_subscribe_request_ids.values() for market_id in market_ids}
            pending_unsubscribe = {market_id for market_ids in self._pending_unsubscribe_request_ids.values() for market_id in market_ids}
        to_subscribe = sorted(target - subscribed - pending_subscribe)
        to_unsubscribe = sorted((subscribed - target) - pending_unsubscribe)
        if to_subscribe:
            request_id = await self._send_subscription_message(ws, "SUBSCRIBE", to_subscribe)
            with self._lock:
                self._pending_subscribe_request_ids[request_id] = list(to_subscribe)
        if to_unsubscribe:
            request_id = await self._send_subscription_message(ws, "UNSUBSCRIBE", to_unsubscribe)
            with self._lock:
                self._pending_unsubscribe_request_ids[request_id] = list(to_unsubscribe)

    async def _send_subscription_message(self, ws: object, method: str, market_ids: list[str]) -> int:
        if not market_ids:
            return 0
        with self._lock:
            self._subscription_request_id += 1
            request_id = self._subscription_request_id
        payload = {
            "method": method,
            "params": [f"{market_id.lower()}@aggTrade" for market_id in market_ids],
            "id": request_id,
        }
        await ws.send_json(payload)
        return request_id

    def _handle_ws_payload(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._set_status("payload_error", f"json:{exc}")
            return
        if isinstance(payload, dict) and "result" in payload and "id" in payload:
            self._handle_subscription_ack(payload)
            return
        if isinstance(payload, dict) and ("code" in payload or "msg" in payload):
            request_id = _optional_int(payload.get("id"))
            if request_id is not None:
                with self._lock:
                    self._pending_subscribe_request_ids.pop(request_id, None)
                    self._pending_unsubscribe_request_ids.pop(request_id, None)
            self._set_status("subscription_error", f"{payload.get('code', '')}:{payload.get('msg', '')}"[:500])
            return
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            self._set_status("payload_error", f"unexpected_payload:{type(payload).__name__}")
            return
        if data.get("e") != "aggTrade":
            return
        market_id = str(data.get("s", "")).strip().upper()
        if not market_id:
            return
        timestamp_ms = _optional_int(data.get("T"))
        price = _optional_float(data.get("p"))
        quantity = _optional_float(data.get("q"))
        if timestamp_ms is None or price is None or quantity is None:
            self._set_status("payload_error", f"aggtrade_missing_required_fields:{market_id}")
            return
        aggtrade_id = _optional_int(data.get("a"))
        row = {
            "a": aggtrade_id if aggtrade_id is not None else data.get("a"),
            "p": str(data.get("p")),
            "q": str(data.get("q")),
            "T": int(timestamp_ms),
            "m": bool(data.get("m")),
        }
        now_ms = int(time.time() * 1000)
        with self._lock:
            previous_id = self._last_aggtrade_id_by_market_id.get(market_id)
            previous_ts = self._last_trade_timestamp_by_market_id.get(market_id)
            if aggtrade_id is not None and previous_id is not None and aggtrade_id > previous_id + 1 and previous_ts is not None:
                gap_start = min(int(previous_ts) + 1, int(timestamp_ms))
                gap_end = max(int(previous_ts) + 1, int(timestamp_ms) - 1)
                if gap_start <= gap_end:
                    gaps = self._gap_ranges_by_market_id.setdefault(market_id, [])
                    gaps.append((gap_start, gap_end))
                    self._gap_ranges_by_market_id[market_id] = self._merge_time_ranges(gaps)
            rows_deque = self._rows_by_market_id.setdefault(market_id, deque())
            rows_deque.append(row)
            if aggtrade_id is not None:
                self._last_aggtrade_id_by_market_id[market_id] = int(aggtrade_id)
            self._last_trade_timestamp_by_market_id[market_id] = int(timestamp_ms)
            self._last_receive_at_by_market_id[market_id] = now_ms
            self._covered_until_ms_by_market_id[market_id] = max(
                int(timestamp_ms),
                int(self._covered_until_ms_by_market_id.get(market_id, timestamp_ms)),
            )
            self._connection_status = "connected"
            self._last_error = None
            self._prune_market_locked(market_id, now_ms=now_ms)

    def _handle_subscription_ack(self, payload: dict[str, object]) -> None:
        request_id = _optional_int(payload.get("id"))
        if request_id is None:
            return
        now_ms = int(time.time() * 1000)
        with self._lock:
            subscribed = self._pending_subscribe_request_ids.pop(request_id, None)
            unsubscribed = self._pending_unsubscribe_request_ids.pop(request_id, None)
            if subscribed:
                for market_id in subscribed:
                    self._subscribed_market_ids.add(market_id)
                    self._rows_by_market_id[market_id] = deque()
                    self._active_since_ms_by_market_id[market_id] = now_ms
                    self._covered_until_ms_by_market_id[market_id] = now_ms
                    self._gap_ranges_by_market_id.pop(market_id, None)
                    self._last_aggtrade_id_by_market_id.pop(market_id, None)
                    self._last_trade_timestamp_by_market_id.pop(market_id, None)
                    self._last_receive_at_by_market_id.pop(market_id, None)
            if unsubscribed:
                for market_id in unsubscribed:
                    self._subscribed_market_ids.discard(market_id)
                    self._rows_by_market_id.pop(market_id, None)
                    self._active_since_ms_by_market_id.pop(market_id, None)
                    self._covered_until_ms_by_market_id.pop(market_id, None)
                    self._gap_ranges_by_market_id.pop(market_id, None)
                    self._last_aggtrade_id_by_market_id.pop(market_id, None)
                    self._last_trade_timestamp_by_market_id.pop(market_id, None)
                    self._last_receive_at_by_market_id.pop(market_id, None)
            if subscribed or unsubscribed:
                self._connection_status = "connected"
                self._last_error = None

    def _prune_market_locked(self, market_id: str, *, now_ms: int) -> None:
        rows_deque = self._rows_by_market_id.get(market_id)
        if rows_deque is None:
            return
        min_timestamp_ms = int(now_ms) - self.buffer_ms
        while rows_deque:
            timestamp_ms = _resolve_aggtrade_timestamp(rows_deque[0])
            if timestamp_ms is None or int(timestamp_ms) >= min_timestamp_ms:
                break
            rows_deque.popleft()

    def _set_status(self, status: str, error: str | None) -> None:
        with self._lock:
            self._connection_status = status
            self._last_error = error


SUPPORTED_LIVE_PUMP_CATEGORIES: dict[str, LivePumpCategory] = {
    "runner_oi_confirmed": LivePumpCategory(
        category_id="runner_oi_confirmed",
        label="runner OI confirmed",
        priority=10,
        min_oi_change_pct_3x5m=0.003,
        min_mark_close_vs_decision_close_basis=0.003,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=250.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=3_000.0,
        max_start_taker_buy_quote_share_delta=0.25,
        max_prior_fast_fade_count_72h=0,
    ),
    "runner_flow": LivePumpCategory(
        category_id="runner_flow",
        label="runner flow",
        priority=20,
        min_mark_close_vs_decision_close_basis=0.001,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=250.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=3_000.0,
        max_start_taker_buy_quote_share_delta=0.25,
        min_flow_hold_count=1,
        max_prior_fast_fade_count_72h=0,
    ),
    "runner_reclaim": LivePumpCategory(
        category_id="runner_reclaim",
        label="runner reclaim",
        priority=30,
        min_mark_close_vs_decision_close_basis=0.001,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=250.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=3_000.0,
        max_start_taker_buy_quote_share_delta=0.25,
        min_start_lower_wick_to_range=0.0,
        max_start_upper_wick_to_range=0.20,
        max_prior_fast_fade_count_72h=0,
    ),
    "runner_balanced": LivePumpCategory(
        category_id="runner_balanced",
        label="runner balanced",
        priority=40,
        min_mark_close_vs_decision_close_basis=0.001,
        max_start_quote_ratio=1000.0,
        max_start_trade_ratio=250.0,
        max_start_quote_ratio_per_abs_return=20_000.0,
        max_start_trade_ratio_per_abs_return=3_000.0,
        max_start_taker_buy_quote_share_delta=0.25,
        max_prior_fast_fade_count_72h=0,
    ),
    "balanced_market": LivePumpCategory(
        category_id="balanced_market",
        label="balanced market",
        priority=90,
    ),
    "mild_market": LivePumpCategory(
        category_id="mild_market",
        label="mild market",
        priority=100,
        max_start_quote_ratio=120.0,
        max_start_trade_ratio=60.0,
        max_start_avg_trade_quote_size_ratio=10.0,
        max_start_quote_ratio_per_abs_return=30_000.0,
        max_start_range_pct_ratio_to_baseline=35.0,
        min_next_taker_buy_quote_share=0.46,
        max_price_retention=0.98,
    ),
}

LIVE_DEFAULT_EXCLUDED_HIGH_CAP_BASES: frozenset[str] = frozenset(
    {
        "BTC",
        "ETH",
        "BNB",
        "SOL",
        "XRP",
        "DOGE",
        "ADA",
        "TRX",
        "LINK",
        "AVAX",
        "TON",
        "SHIB",
        "SUI",
        "HBAR",
        "XLM",
        "UNI",
        "ETC",
        "NEAR",
        "APT",
        "ICP",
        "ATOM",
        "FIL",
        "ARB",
        "OP",
        "AAVE",
        "INJ",
        "TIA",
        "WIF",
        "SEI",
        "ENA",
        "TAO",
        "WLD",
        "FET",
        "RENDER",
        "ALGO",
        "VET",
        "LTC",
        "BCH",
        "DOT",
    }
)


@dataclass(frozen=True, slots=True)
class LiveAnomalyConfig:
    results_dir: Path
    symbols: tuple[str, ...]
    confirm_real_orders: bool
    cache_dir: Path | None = None
    timeframe_pairs: tuple[tuple[Timeframe, Timeframe], ...] = ANOMALY_LIVE_TIMEFRAME_PAIRS
    pump_categories: tuple[str, ...] = LIVE_DEFAULT_PUMP_CATEGORY_IDS
    baseline_candles: int = 60
    confirmation_candles: int = 4
    min_quote_ratio_start: float = 5.0
    min_trade_ratio_start: float = 5.0
    max_start_quote_ratio: float | None = 80.0
    max_start_trade_ratio: float | None = 40.0
    max_start_avg_trade_quote_size_ratio: float | None = 7.0
    max_start_quote_ratio_per_abs_return: float | None = 15_000.0
    max_start_trade_ratio_per_abs_return: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = 25.0
    min_next_taker_buy_quote_share: float | None = 0.48
    max_start_taker_buy_quote_share_delta: float | None = None
    max_price_retention: float | None = 0.96
    min_price_retention: float = 0.70
    min_verticality_score: float = 0.25
    min_hold_count: int = 2
    min_oi_change_pct_3x5m: float | None = None
    min_mark_close_vs_decision_close_basis: float | None = None
    max_initial_risk_pct: float = 0.16
    stop_buffer_range_fraction: float = 0.05
    max_prior_up_down_whipsaw_to_impulse_range: float | None = 0.60
    position_notional_usdt: float = 12.0
    max_open_positions: int = 3
    exclude_default_high_cap_symbols: bool = True
    symbol_batch_size: int = 20
    inactive_scan_slots_per_cycle: int | None = None
    scan_hot_timeframes_per_symbol: bool = True
    active_symbol_ttl_ms: int = 60_000
    ticker_radar_enabled: bool = True
    live_ws_ticker_enabled: bool = True
    live_ws_ticker_stale_ms: int = 5_000
    live_ws_ticker_startup_wait_seconds: float = 10.0
    live_ws_ticker_startup_seed_enabled: bool = True
    live_ws_aggtrade_enabled: bool = True
    live_ws_aggtrade_stale_ms: int = 5_000
    live_ws_aggtrade_buffer_minutes: int = 20
    live_ws_aggtrade_max_backfill_ms: int = DEFAULT_LIVE_WS_AGGTRADE_MAX_BACKFILL_MS
    live_aggtrade_rest_cache_ttl_ms: int = DEFAULT_LIVE_AGGTRADE_REST_CACHE_TTL_MS
    live_aggtrade_rest_cache_padding_ms: int = DEFAULT_LIVE_AGGTRADE_REST_CACHE_PADDING_MS
    ticker_radar_interval_seconds: float = 5.0
    ticker_radar_watch_ttl_ms: int = 120_000
    ticker_radar_watch_batch_size: int = 5
    ticker_radar_max_promotions_per_cycle: int = 20
    max_precise_scan_symbols_per_cycle: int | None = None
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
    live_ohlcv_cache_flush_interval_seconds: float = 30.0
    live_ohlcv_cache_max_buffer_rows: int = 50_000
    live_ohlcv_cache_flush_max_symbol_timeframes: int | None = DEFAULT_LIVE_OHLCV_CACHE_FLUSH_MAX_SYMBOL_TIMEFRAMES
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
    previous_live_scan_closed_timestamp_ms: int | None = None
    first_unscanned_decision_timestamp_ms: int | None = None
    live_scan_gap_ltf_candles: int = 0
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


@dataclass(frozen=True, slots=True)
class LiveSymbolBatchSelection:
    scheduler_source: str
    active_due: tuple[str, ...]
    active_waiting: tuple[str, ...]
    radar_due: tuple[str, ...]
    radar_waiting: tuple[str, ...]
    inactive: tuple[str, ...]
    batch: tuple[str, ...]
    scan_modes: dict[str, str]
    inactive_cursor_before: int
    inactive_cursor_after: int
    batch_in_full_cycle: int
    full_symbol_cycle: int
    precise_budget_remaining_after_active: int | None
    precise_budget_remaining_after_radar: int | None
    inactive_scan_slots: int
    inactive_scan_slots_source: str
    inactive_cold_coverage_danger: bool
    inactive_cold_coverage_gate_reason: str
    inactive_cold_coverage_health_pct: float
    inactive_cold_coverage_health_threshold_pct: float
    inactive_cold_coverage_active_blocked: bool
    inactive_cold_coverage_position_blocked: bool
    inactive_cold_coverage_active_due_count: int
    inactive_cold_coverage_active_waiting_count: int
    inactive_cold_coverage_adaptive_score: float
    inactive_cold_coverage_health_factor: float
    inactive_cold_coverage_speed_factor: float
    inactive_cold_coverage_active_factor: float
    inactive_cold_coverage_load_factor: float
    inactive_cold_coverage_pressure_ewma: float
    inactive_cold_coverage_cycle_seconds_ewma: float
    inactive_cold_coverage_base_slots: int
    inactive_cold_coverage_max_slots: int


@dataclass(frozen=True, slots=True)
class LiveSignalScanResult:
    signal: LiveSignal | None
    decision_timestamp_ms: int | None = None
    retryable_dependency: bool = False
    retry_reason: str = ""
    retryable_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveTickerRadarCycleStats:
    enabled: bool
    attempted: bool
    status: str
    source: str
    symbols_total: int = 0
    ok_count: int = 0
    missing_count: int = 0
    promoted_count: int = 0
    promotion_candidates_count: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LiveWsAggTradeSubscriptionStats:
    enabled: bool
    source: str
    target_count: int = 0
    subscribed_count: int | str = ""
    connection_status: str = ""
    last_error: str = ""


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
    first_executable_entry_timestamp_ms: int
    entry_lag_ms: int
    entry_lag_ltf_candles: int
    entered_late_vs_first_executable: bool
    entry_order_submit_lag_ms: int
    entry_order_submit_lag_ltf_candles: int
    previous_live_scan_closed_timestamp_ms: int | None
    first_unscanned_decision_timestamp_ms: int | None
    live_scan_gap_ltf_candles: int
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
    """Console logger for live heartbeat/status messages."""

    def __init__(self, logger: Callable[[str], None]) -> None:
        self._logger = logger
        self._inline_status_enabled = logger is print and sys.stdout.isatty()
        self._lock = threading.RLock()
        self._status_line_open = False

    @property
    def inline_status_enabled(self) -> bool:
        return self._inline_status_enabled

    def __call__(self, message: str) -> None:
        with self._lock:
            self._finish_status_line_if_needed()
            self._logger(message)

    def status(self, message: str, *, highlight: bool = False) -> None:
        with self._lock:
            if not self._inline_status_enabled:
                self._logger(message)
                return
            rendered = f"\033[1;33m{message}\033[0m" if highlight else message
            sys.stdout.write(f"\r\033[2K{rendered}")
            sys.stdout.flush()
            self._status_line_open = True

    def _finish_status_line_if_needed(self) -> None:
        if not self._inline_status_enabled or not self._status_line_open:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_line_open = False


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
        self._events_written = self._count_existing_csv_rows(self.events_path)
        self._ensure_csv(self.ledger_path, LIVE_LEDGER_COLUMNS)
        self._ensure_csv(self.events_path, ("timestamp_utc", "event", "symbol", "details_json"))
        self._ensure_csv(self.top_growth_index_path, TOP_GROWTH_INDEX_COLUMNS)

    @staticmethod
    def _count_existing_csv_rows(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", newline="", encoding="utf-8") as handle:
            return max(sum(1 for _ in handle) - 1, 0)

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
            self._events_written += 1

    @property
    def events_written(self) -> int:
        with self._lock:
            return self._events_written

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
                    "first_executable_entry_timestamp_ms": position.first_executable_entry_timestamp_ms,
                    "entry_lag_ms": position.entry_lag_ms,
                    "entry_lag_ltf_candles": position.entry_lag_ltf_candles,
                    "entered_late_vs_first_executable": position.entered_late_vs_first_executable,
                    "entry_order_submit_lag_ms": position.entry_order_submit_lag_ms,
                    "entry_order_submit_lag_ltf_candles": position.entry_order_submit_lag_ltf_candles,
                    "previous_live_scan_closed_timestamp_ms": position.previous_live_scan_closed_timestamp_ms or "",
                    "first_unscanned_decision_timestamp_ms": position.first_unscanned_decision_timestamp_ms or "",
                    "live_scan_gap_ltf_candles": position.live_scan_gap_ltf_candles,
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
                    "first_executable_entry_timestamp_ms": position.first_executable_entry_timestamp_ms,
                    "entry_lag_ms": position.entry_lag_ms,
                    "entry_lag_ltf_candles": position.entry_lag_ltf_candles,
                    "entered_late_vs_first_executable": position.entered_late_vs_first_executable,
                    "entry_order_submit_lag_ms": position.entry_order_submit_lag_ms,
                    "entry_order_submit_lag_ltf_candles": position.entry_order_submit_lag_ltf_candles,
                    "previous_live_scan_closed_timestamp_ms": position.previous_live_scan_closed_timestamp_ms or "",
                    "first_unscanned_decision_timestamp_ms": position.first_unscanned_decision_timestamp_ms or "",
                    "live_scan_gap_ltf_candles": position.live_scan_gap_ltf_candles,
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
        ticker_snapshot_source: LiveTickerSnapshotSource | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.exchange = exchange_client
        self._status_logger = _LiveStatusLogger(logger)
        self.logger = self._status_logger
        if ticker_snapshot_source is not None:
            self.ticker_snapshot_source = ticker_snapshot_source
        elif config.live_ws_ticker_enabled:
            self.ticker_snapshot_source = BinanceWsAllTickerSnapshotSource(
                exchange=exchange_client,
                stale_ms=config.live_ws_ticker_stale_ms,
                startup_wait_seconds=config.live_ws_ticker_startup_wait_seconds,
                logger=self.logger,
            )
        else:
            self.ticker_snapshot_source = RestLiveTickerSnapshotSource(exchange_client)
        self.aggtrade_source = (
            BinanceWsAggTradeBuffer(
                exchange=exchange_client,
                buffer_minutes=config.live_ws_aggtrade_buffer_minutes,
                stale_ms=config.live_ws_aggtrade_stale_ms,
                logger=self.logger,
            )
            if config.live_ws_aggtrade_enabled
            else None
        )
        self._pump_categories = _resolve_live_pump_categories(config.pump_categories)
        self._pump_categories_by_id = {category.category_id: category for category in self._pump_categories}
        self._prior_fast_fade_cache: dict[tuple[str, str, str, int], dict[str, object]] = {}
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
        self._order_reconcile_symbols_by_key: dict[str, str] = {}
        self._recent_stops: dict[str, list[float]] = {}
        self._active_symbols: dict[str, LiveActiveSymbol] = {}
        self._active_symbols_seen: set[str] = set()
        self._ticker_radar_watch: dict[str, LiveTickerRadarWatch] = {}
        self._ticker_radar_snapshots: dict[str, ExchangeTickerSnapshot] = {}
        self._ticker_radar_quote_delta_history: dict[str, deque[float]] = {}
        self._last_ticker_radar_at_ms = 0
        self._last_ticker_radar_health_status = "unknown"
        self._seen_decisions: set[tuple[str, str, str, int]] = set()
        self._last_signal_scan_closed_at: dict[tuple[str, str], int] = {}
        self._opened_positions_total = 0
        self._closed_positions_total = 0
        self._closed_pnl_usdt_total = 0.0
        self._closed_notional_usdt_total = 0.0
        self._orphan_orders_cancelled_total = 0
        self._detected_anomalies_total = 0
        self._state_lock = threading.RLock()
        self._inactive_cursor = 0
        self._order_reconcile_cursor = 0
        self._network_degraded = False
        self._current_cycle_aggtrade_cache: dict[str, list[AggTradeRawRange]] = {}
        self._aggtrade_raw_process_cache: dict[str, list[AggTradeRawRange]] = {}
        self._cycle_aggtrade_requests = 0
        self._cycle_aggtrade_network_calls = 0
        self._cycle_aggtrade_cache_hits = 0
        self._cycle_aggtrade_process_cache_hits = 0
        self._cycle_aggtrade_coalesced_missing_ranges = 0
        self._cycle_aggtrade_rest_fetched_ms = 0
        self._cycle_ws_aggtrade_backfill_reads = 0
        self._cycle_ws_aggtrade_backfilled_rows = 0
        self._cycle_ws_aggtrade_coverage_pending = 0
        self._cycle_ws_aggtrade_not_connected_backfill_reads = 0
        self._cycle_aggtrade_gap_prefetch_symbols = 0
        self._cycle_aggtrade_gap_prefetch_requested_ranges = 0
        self._cycle_aggtrade_gap_prefetch_missing_ranges = 0
        self._cycle_aggtrade_gap_prefetch_backfill_ranges = 0
        self._cycle_aggtrade_gap_prefetch_rows = 0
        self._cycle_aggtrade_gap_prefetch_pending = 0
        self._current_batch_symbol_scan_mode: dict[str, str] = {}
        self._current_scheduler_source = "uninitialized"
        self._current_inactive_scan_slots = 0
        self._current_inactive_scan_slots_source = ""
        self._current_inactive_cold_coverage_gate_reason = ""
        self._current_inactive_cold_coverage_health_pct = 0.0
        self._current_inactive_cold_coverage_health_threshold_pct = DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0
        self._current_inactive_cold_coverage_adaptive_score = 0.0
        self._current_inactive_cold_coverage_health_factor = 0.0
        self._current_inactive_cold_coverage_speed_factor = 0.0
        self._current_inactive_cold_coverage_active_factor = 0.0
        self._current_inactive_cold_coverage_load_factor = 0.0
        self._current_inactive_cold_coverage_pressure_ewma = 0.0
        self._current_inactive_cold_coverage_cycle_seconds_ewma = DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS
        self._cold_coverage_pressure_ewma = 0.0
        self._cycle_precise_scan_symbols = 0
        self._cycle_inactive_visit_symbols = 0
        self._cycle_deferred_inactive_subminute_pairs = 0
        self._ws_health_observed_seconds = 0.0
        self._ws_health_healthy_seconds = 0.0
        self._last_ws_health_sample_at = time.monotonic()
        self._symbol_universe_scan_started_at = time.monotonic()
        self._last_symbol_universe_cycle_seconds: float | None = None
        self._symbol_universe_cycle_index = 1
        self._symbol_universe_batch_index = 0
        self._current_symbol_universe_cycle_index = 1
        self._current_symbol_universe_batch_index = 1
        self._ohlcv_cache_storage = (
            ParquetStorage(base_dir=config.cache_dir)
            if config.live_ohlcv_cache_enabled and config.cache_dir is not None
            else None
        )
        self._live_ohlcv_frame_cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._live_ohlcv_write_buffer: dict[tuple[str, str, str], list[pd.DataFrame]] = {}
        self._live_ohlcv_pending_rows = 0
        self._last_live_ohlcv_cache_flush_at = time.monotonic()
        self._live_sources_closed = False

    def shutdown(self, *, reason: str) -> None:
        self._flush_live_ohlcv_cache_if_due(force=True, reason=reason)
        self._close_live_sources()

    def run(self) -> int:
        self._validate_startup()
        explicit_symbols = bool(self.config.symbols)
        symbols = list(self.config.symbols) if explicit_symbols else self.exchange.list_usdt_swap_symbols()
        symbols = self._filter_live_symbol_universe(symbols, explicit_symbols=explicit_symbols)
        if not symbols:
            raise LiveStartupError("Нет символов для live-обхода")
        category_ids = ",".join(category.category_id for category in self._pump_categories)
        timeframe_pairs_label = _format_timeframe_pairs(self.config.timeframe_pairs)
        self.logger(
            f"старт · символов {len(symbols)} · TF {timeframe_pairs_label} · max {self.config.max_open_positions}"
        )
        self.logger(f"артефакты {self.artifacts.root}")
        trading_mode = "с торговлей" if self.config.confirm_real_orders else "без торговли"
        self.artifacts.append_event(
            "live_cache_config",
            "__live__",
            {
                "live_ohlcv_cache_enabled": bool(self.config.live_ohlcv_cache_enabled),
                "live_ohlcv_cache_write_enabled": bool(self.config.live_ohlcv_cache_write_enabled),
                "live_ohlcv_cache_flush_interval_seconds": self.config.live_ohlcv_cache_flush_interval_seconds,
                "live_ohlcv_cache_max_buffer_rows": self.config.live_ohlcv_cache_max_buffer_rows,
                "live_ohlcv_cache_flush_max_symbol_timeframes": (
                    self.config.live_ohlcv_cache_flush_max_symbol_timeframes
                    if self.config.live_ohlcv_cache_flush_max_symbol_timeframes is not None
                    else ""
                ),
                "cache_dir": str(self.config.cache_dir) if self.config.cache_dir is not None else "",
                "cache_provider": "parquet_tail_fetch_v1" if self._ohlcv_cache_storage is not None else "disabled",
                "inactive_subminute_scan_policy": "DANGER_default_precise_cold_coverage_idle_health_gated",
                "inactive_scan_slots_default_policy": (
                    f"DANGER default {DANGER_DEFAULT_INACTIVE_COLD_COVERAGE_SLOTS_PER_CYCLE} precise cold-coverage "
                    "slots per cycle for subminute ticker-radar live, gated by WS health > "
                    f"{DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0:.1f}% and no active/opening/open positions; "
                    "set inactive_scan_slots_per_cycle=0 to disable cold coverage"
                ),
                "inactive_cold_coverage_default_danger": True,
                "inactive_cold_coverage_health_gate_min_pct": DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0,
                "inactive_cold_coverage_requires_no_active_or_position": True,
                "symbol_batch_size_role": "legacy inactive scan cap, not WS market discovery",
                "subminute_entry_pairs_present": bool(_live_config_has_subminute_entry_pairs(self.config)),
                "ticker_radar_required_for_inactive_subminute_gate": bool(
                    _live_config_has_subminute_entry_pairs(self.config)
                ),
                "ticker_snapshot_source": self.ticker_snapshot_source.source_id,
                "ticker_snapshot_degraded_source_policy": "rest_fetch_tickers_after_primary_ws_failure_with_live_events",
                "live_ws_ticker_enabled": bool(self.config.live_ws_ticker_enabled),
                "live_ws_ticker_stale_ms": int(self.config.live_ws_ticker_stale_ms),
                "live_ws_ticker_startup_wait_seconds": float(self.config.live_ws_ticker_startup_wait_seconds),
                "live_ws_ticker_startup_seed_enabled": bool(self.config.live_ws_ticker_startup_seed_enabled),
                "aggtrade_source": self.aggtrade_source.source_id if self.aggtrade_source is not None else "rest_fetch_aggtrades",
                "live_ws_aggtrade_enabled": bool(self.config.live_ws_aggtrade_enabled),
                "live_ws_aggtrade_stale_ms": int(self.config.live_ws_aggtrade_stale_ms),
                "live_ws_aggtrade_buffer_minutes": int(self.config.live_ws_aggtrade_buffer_minutes),
                "live_ws_aggtrade_max_backfill_ms": int(self.config.live_ws_aggtrade_max_backfill_ms),
                "live_aggtrade_rest_cache_ttl_ms": int(self.config.live_aggtrade_rest_cache_ttl_ms),
                "live_aggtrade_rest_cache_padding_ms": int(self.config.live_aggtrade_rest_cache_padding_ms),
                "exclude_default_high_cap_symbols": bool(self.config.exclude_default_high_cap_symbols),
                "excluded_high_cap_bases": sorted(LIVE_DEFAULT_EXCLUDED_HIGH_CAP_BASES),
            },
        )
        if self.config.confirm_real_orders:
            self._validate_live_account_mode()
            self._close_startup_exchange_positions(symbols)
        self._seed_startup_ticker_radar(symbols)
        try:
            self._validate_required_ticker_radar_source(symbols)
        except LiveStartupError:
            self._close_live_sources()
            raise
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
            self._reset_cycle_fetch_state()
            try:
                opened_before, _, closed_before, orphan_before = self._live_counts()
                ticker_started = time.monotonic()
                ticker_stats = self._maybe_update_ticker_radar(symbols)
                ticker_seconds = time.monotonic() - ticker_started
                batch_select_started = time.monotonic()
                batch = self._next_symbol_batch(symbols)
                batch_select_seconds = time.monotonic() - batch_select_started
                ws_aggtrade_started = time.monotonic()
                ws_aggtrade_stats = self._update_ws_aggtrade_subscriptions(batch)
                ws_aggtrade_seconds = time.monotonic() - ws_aggtrade_started
                scan_started = time.monotonic()
                signals = self._scan_batch(batch)
                scan_seconds = time.monotonic() - scan_started
                open_started = time.monotonic()
                for signal in signals:
                    self._maybe_open_position(signal)
                open_seconds = time.monotonic() - open_started
                reconcile_started = time.monotonic()
                orphan_cancelled = self._reconcile_orphan_orders(symbols, cycle=cycle)
                reconcile_seconds = time.monotonic() - reconcile_started
                flush_started = time.monotonic()
                self._flush_live_ohlcv_cache_if_due(reason="cycle")
                cache_flush_seconds = time.monotonic() - flush_started
                cycle_seconds = time.monotonic() - cycle_started
                opened_total, open_positions, closed_total, orphan_total = self._live_counts()
                active_symbol_count = self._active_live_symbol_count()
                active_symbols_seen_total = self._active_live_symbols_seen_total()
                closed_pnl_pct = self._closed_pnl_pct_total()
                detected_anomalies_total = self._detected_anomalies_count()
                full_cycle_seconds = self._full_symbol_cycle_seconds(
                    batch_seconds=cycle_seconds,
                    batch_size=len(batch),
                    symbols_total=len(symbols),
                )
                ws_healthy, ws_health_reason = self._is_ws_healthy_for_cycle(ticker_stats, ws_aggtrade_stats)
                ws_health_pct = self._record_ws_health_sample(healthy=ws_healthy)
                self._record_scheduler_cycle_seconds(cycle_seconds)
                self._record_cold_coverage_pressure_sample()
                self.artifacts.append_event(
                    "live_cycle_summary",
                    "__live__",
                    {
                        "cycle": cycle,
                        "scheduler_cycle_seconds": round(cycle_seconds, 3),
                        "batch_in_full_cycle": self._current_symbol_universe_batch_index,
                        "full_symbol_cycle": self._current_symbol_universe_cycle_index,
                        "legacy_batch_seconds": round(cycle_seconds, 3),
                        "batch_seconds": round(cycle_seconds, 3),
                        "scheduler_source": self._current_scheduler_source,
                        "inactive_scan_slots_per_cycle": self._current_inactive_scan_slots,
                        "inactive_scan_slots_source": self._current_inactive_scan_slots_source,
                        "ticker_radar_seconds": round(ticker_seconds, 3),
                        "ticker_radar_status": ticker_stats.status,
                        "ticker_radar_attempted": bool(ticker_stats.attempted),
                        "ticker_radar_source": ticker_stats.source,
                        "ticker_radar_symbols_total": ticker_stats.symbols_total,
                        "ticker_radar_ok_count": ticker_stats.ok_count,
                        "ticker_radar_missing_count": ticker_stats.missing_count,
                        "ticker_radar_promoted_count": ticker_stats.promoted_count,
                        "ticker_radar_promotion_candidates_count": ticker_stats.promotion_candidates_count,
                        "detected_anomalies_total": detected_anomalies_total,
                        "batch_select_seconds": round(batch_select_seconds, 3),
                        "ws_aggtrade_subscription_seconds": round(ws_aggtrade_seconds, 3),
                        "ws_aggtrade_enabled": bool(ws_aggtrade_stats.enabled),
                        "ws_aggtrade_source": ws_aggtrade_stats.source,
                        "ws_aggtrade_target_count": ws_aggtrade_stats.target_count,
                        "ws_aggtrade_subscribed_count": ws_aggtrade_stats.subscribed_count,
                        "ws_aggtrade_connection_status": ws_aggtrade_stats.connection_status,
                        "ws_aggtrade_last_error": ws_aggtrade_stats.last_error[:500],
                        "ws_aggtrade_error_label": _ws_error_short_label(ws_aggtrade_stats.last_error),
                        "ticker_radar_error_label": (
                            _ws_error_short_label(ticker_stats.reason)
                            if ticker_stats.status in {"failed", "degraded_rest_fallback"}
                            else ""
                        ),
                        "ws_healthy": bool(ws_healthy),
                        "ws_health_reason": ws_health_reason,
                        "ws_health_pct": round(ws_health_pct, 4),
                        "ws_health_observed_seconds": round(self._ws_health_observed_seconds, 3),
                        "ws_health_healthy_seconds": round(self._ws_health_healthy_seconds, 3),
                        "signal_scan_seconds": round(scan_seconds, 3),
                        "open_signal_seconds": round(open_seconds, 3),
                        "order_reconcile_seconds": round(reconcile_seconds, 3),
                        "cache_flush_seconds": round(cache_flush_seconds, 3),
                        "full_symbol_cycle_seconds": round(full_cycle_seconds, 3),
                        "opened_total": opened_total,
                        "open_positions": open_positions,
                        "active_symbol_count": active_symbol_count,
                        "active_symbols_seen_total": active_symbols_seen_total,
                        "closed_total": closed_total,
                        "closed_pnl_pct": closed_pnl_pct,
                        "aggtrade_requests": self._cycle_aggtrade_requests,
                        "aggtrade_network_calls": self._cycle_aggtrade_network_calls,
                        "aggtrade_cache_hits": self._cycle_aggtrade_cache_hits,
                        "aggtrade_process_cache_hits": self._cycle_aggtrade_process_cache_hits,
                        "aggtrade_coalesced_missing_ranges": self._cycle_aggtrade_coalesced_missing_ranges,
                        "aggtrade_rest_fetched_ms": self._cycle_aggtrade_rest_fetched_ms,
                        "aggtrade_gap_prefetch_symbols": self._cycle_aggtrade_gap_prefetch_symbols,
                        "aggtrade_gap_prefetch_requested_ranges": self._cycle_aggtrade_gap_prefetch_requested_ranges,
                        "aggtrade_gap_prefetch_missing_ranges": self._cycle_aggtrade_gap_prefetch_missing_ranges,
                        "aggtrade_gap_prefetch_backfill_ranges": self._cycle_aggtrade_gap_prefetch_backfill_ranges,
                        "aggtrade_gap_prefetch_rows": self._cycle_aggtrade_gap_prefetch_rows,
                        "aggtrade_gap_prefetch_pending": self._cycle_aggtrade_gap_prefetch_pending,
                        "ws_aggtrade_backfill_reads": self._cycle_ws_aggtrade_backfill_reads,
                        "ws_aggtrade_backfilled_rows": self._cycle_ws_aggtrade_backfilled_rows,
                        "ws_aggtrade_not_connected_backfill_reads": self._cycle_ws_aggtrade_not_connected_backfill_reads,
                        "ws_aggtrade_coverage_pending_count": self._cycle_ws_aggtrade_coverage_pending,
                        "ws_aggtrade_effective_source": self._ws_aggtrade_effective_source_for_cycle(ws_aggtrade_stats),
                        "precise_scan_symbols": self._cycle_precise_scan_symbols,
                        "inactive_visit_symbols": self._cycle_inactive_visit_symbols,
                        "deferred_inactive_subminute_pairs": self._cycle_deferred_inactive_subminute_pairs,
                        "inactive_cold_coverage_danger": (
                            self._current_inactive_scan_slots > 0
                            and self._current_inactive_scan_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE
                        ),
                        "inactive_cold_coverage_gate_reason": self._current_inactive_cold_coverage_gate_reason,
                        "inactive_cold_coverage_health_pct_at_selection": round(
                            self._current_inactive_cold_coverage_health_pct, 3
                        ),
                        "inactive_cold_coverage_health_threshold_pct": round(
                            self._current_inactive_cold_coverage_health_threshold_pct, 3
                        ),
                        "inactive_cold_coverage_adaptive_score": round(
                            self._current_inactive_cold_coverage_adaptive_score, 4
                        ),
                        "inactive_cold_coverage_health_factor": round(
                            self._current_inactive_cold_coverage_health_factor, 4
                        ),
                        "inactive_cold_coverage_speed_factor": round(
                            self._current_inactive_cold_coverage_speed_factor, 4
                        ),
                        "inactive_cold_coverage_active_factor": round(
                            self._current_inactive_cold_coverage_active_factor, 4
                        ),
                        "inactive_cold_coverage_load_factor": round(
                            self._current_inactive_cold_coverage_load_factor, 4
                        ),
                        "inactive_cold_coverage_pressure_ewma": round(
                            self._current_inactive_cold_coverage_pressure_ewma, 4
                        ),
                        "inactive_cold_coverage_cycle_seconds_ewma": round(
                            self._current_inactive_cold_coverage_cycle_seconds_ewma, 3
                        ),
                        "orphan_orders_cancelled": orphan_cancelled,
                    },
                )
                should_log_status = self._status_logger.inline_status_enabled or (
                    cycle == 1
                    or cycle % 10 == 0
                    or opened_total != opened_before
                    or closed_total != closed_before
                    or orphan_total != orphan_before
                )
                if should_log_status:
                    connection_text = self._live_connection_status_text(
                        ticker_stats=ticker_stats,
                        aggtrade_stats=ws_aggtrade_stats,
                        ws_healthy=ws_healthy,
                        ws_health_reason=ws_health_reason,
                    )
                    coverage_text = ""
                    if self._current_inactive_scan_slots > 0 and self._inactive_slots_are_precise_cold_coverage(
                        self._current_inactive_scan_slots_source
                    ):
                        danger_prefix = "DANGER " if self._current_inactive_scan_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE else ""
                        coverage_text = f" · {danger_prefix}cold ~{full_cycle_seconds:.0f}s"
                    elif self._current_inactive_cold_coverage_gate_reason:
                        coverage_text = f" · cold off {self._current_inactive_cold_coverage_gate_reason}"
                    orphan_text = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
                    self._status_logger.status(
                        _format_live_heartbeat(
                            cycle_seconds=cycle_seconds,
                            connection_health_pct=ws_health_pct,
                            anomalies_total=detected_anomalies_total,
                            active_now=active_symbol_count,
                            active_seen=active_symbols_seen_total,
                            open_positions=open_positions,
                            closed_positions=closed_total,
                            pnl_pct=closed_pnl_pct,
                            connection_text=connection_text,
                            suffix=f"{coverage_text}{orphan_text}",
                        ),
                        highlight=open_positions > 0,
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
                reconcile_symbols = self._start_graceful_shutdown(reason="keyboard_interrupt", cycle=cycle, symbols=symbols)
                try:
                    orphan_cancelled = self._reconcile_orphan_orders(reconcile_symbols, cycle=cycle, force=True)
                    self._flush_live_ohlcv_cache_if_due(force=True, reason="keyboard_interrupt")
                except KeyboardInterrupt:
                    self.artifacts.append_event(
                        "live_shutdown_forced",
                        "__live__",
                        {"reason": "second_keyboard_interrupt", "cycle": cycle},
                    )
                    self.logger("graceful shutdown прерван повторным Ctrl+C")
                    self._close_live_sources()
                    return 130
                suffix = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
                self.logger(f"остановлено пользователем{suffix}")
                self._close_live_sources()
                return 0
            except LiveDataIntegrityError as exc:
                self._flush_live_ohlcv_cache_if_due(force=True, reason="data_integrity_error")
                self.logger(f"остановлено из-за ошибки целостности live-данных: {exc}")
                self.telegram.send(
                    channel="events",
                    key="live_data_integrity_error",
                    text=f"{SERVICE_WARNING_EMOJI} <b>Ошибка</b>\n\n{_telegram_code(str(exc)[:600])}",
                )
                self._close_live_sources()
                return 3
            except ExchangeConnectivityError as exc:
                if not self._network_degraded:
                    self.logger(f"сеть/API недоступны, жду восстановления. Причина: {exc}")
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
                self._record_ws_health_sample(healthy=False)
            except Exception as exc:
                self.logger(f"остановлено из-за внутренней ошибки: {type(exc).__name__}: {exc}")
                self._flush_live_ohlcv_cache_if_due(force=True, reason="internal_error")
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
                self._close_live_sources()
                return 4
        reconcile_symbols = self._forced_orphan_reconcile_symbols(symbols)
        orphan_cancelled = self._reconcile_orphan_orders(reconcile_symbols, cycle=cycle, force=True)
        self._flush_live_ohlcv_cache_if_due(force=True, reason="max_cycles")
        suffix = f" · ордера -{orphan_cancelled}" if orphan_cancelled else ""
        self.logger(f"достигнут лимит циклов{suffix}")
        self._close_live_sources()
        return 0

    def _track_order_reconcile_symbol(self, symbol: str, *, reason: str) -> None:
        symbol_key = _position_symbol_key(symbol)
        with self._state_lock:
            already_tracked = symbol_key in self._order_reconcile_symbols_by_key
            self._order_reconcile_symbols_by_key[symbol_key] = symbol
        if not already_tracked:
            self.artifacts.append_event(
                "order_reconcile_symbol_tracked",
                symbol,
                {"symbol_key": symbol_key, "reason": reason},
            )

    def _forced_orphan_reconcile_symbols(self, symbols: list[str]) -> list[str]:
        by_key: dict[str, str] = {}
        with self._state_lock:
            by_key.update(self._order_reconcile_symbols_by_key)
            for position in self._open_positions.values():
                by_key.setdefault(_position_symbol_key(position.signal.symbol), position.signal.symbol)
            for active in self._active_symbols.values():
                if active.reason in {"opening_position", "position_already_active"}:
                    by_key.setdefault(_position_symbol_key(active.symbol), active.symbol)
        if not by_key:
            return []
        ordered: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            symbol_key = _position_symbol_key(symbol)
            if symbol_key in by_key and symbol_key not in seen:
                ordered.append(symbol)
                seen.add(symbol_key)
        for symbol_key, symbol in by_key.items():
            if symbol_key not in seen:
                ordered.append(symbol)
                seen.add(symbol_key)
        return ordered

    def _start_graceful_shutdown(self, *, reason: str, cycle: int, symbols: list[str]) -> list[str]:
        reconcile_symbols = self._forced_orphan_reconcile_symbols(symbols)
        self.logger(
            f"получен сигнал остановки · graceful shutdown · "
            f"reason={reason} · reconcile_symbols={len(reconcile_symbols)}"
        )
        self.artifacts.append_event(
            "live_shutdown_started",
            "__live__",
            {
                "reason": reason,
                "cycle": cycle,
                "orphan_reconcile_scope": "run_trade_symbols_only",
                "orphan_reconcile_symbols": len(reconcile_symbols),
                "tracked_trade_symbols": len(self._order_reconcile_symbols_by_key),
                "open_positions": len(self._open_positions),
                "opening_symbols": len(self._opening_symbols),
            },
        )
        return reconcile_symbols

    def _close_live_sources(self) -> None:
        if self._live_sources_closed:
            return
        self._live_sources_closed = True
        for source in (self.ticker_snapshot_source, self.aggtrade_source):
            close = getattr(source, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    self.artifacts.append_event(
                        "live_source_close_failed",
                        "__live__",
                        {"source": getattr(source, "source_id", type(source).__name__), "reason": f"{type(exc).__name__}: {exc}"},
                    )

    def _ticker_radar_required_for_subminute_gate(self) -> bool:
        return bool(self.config.ticker_radar_enabled and _live_config_has_subminute_entry_pairs(self.config))

    def _fetch_ticker_radar_snapshots(
        self,
        symbols: list[str],
        *,
        stage: str,
    ) -> tuple[list[ExchangeTickerSnapshot], str, str, str]:
        primary_source = self.ticker_snapshot_source
        try:
            snapshots = primary_source.fetch_snapshots(tuple(symbols))
            source_status = "primary"
            source_reason = ""
            status_provider = getattr(primary_source, "source_status", None)
            if callable(status_provider):
                source_status, source_reason = status_provider()
            return snapshots, primary_source.source_id, source_status, source_reason
        except Exception as primary_exc:
            if primary_source.source_id == "rest_fetch_tickers":
                raise
            self.artifacts.append_event(
                "ticker_radar_primary_source_failed",
                "__live__",
                {
                    "stage": stage,
                    "primary_source": primary_source.source_id,
                    "exception_type": type(primary_exc).__name__,
                    "exception_message": str(primary_exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "try_explicit_rest_ticker_radar_after_primary_ws_failure",
                },
            )
            fallback_source = RestLiveTickerSnapshotSource(self.exchange)
            try:
                snapshots = fallback_source.fetch_snapshots(tuple(symbols))
            except Exception as fallback_exc:
                self.artifacts.append_event(
                    "ticker_radar_fallback_source_failed",
                    "__live__",
                    {
                        "stage": stage,
                        "primary_source": primary_source.source_id,
                        "fallback_source": fallback_source.source_id,
                        "primary_exception_type": type(primary_exc).__name__,
                        "primary_exception_message": str(primary_exc)[:500],
                        "fallback_exception_type": type(fallback_exc).__name__,
                        "fallback_exception_message": str(fallback_exc)[:500],
                        "symbols_total": len(symbols),
                        "policy": "refuse_without_any_working_ticker_radar_source",
                    },
                )
                raise ExchangeConnectivityError(
                    "ticker radar primary source failed and explicit REST ticker fallback also failed; "
                    f"primary_source={primary_source.source_id}; primary_error={type(primary_exc).__name__}: {primary_exc}; "
                    f"fallback_source={fallback_source.source_id}; fallback_error={type(fallback_exc).__name__}: {fallback_exc}"
                ) from fallback_exc
            missing_count = sum(1 for snapshot in snapshots if snapshot.status != "ok")
            self.artifacts.append_event(
                "ticker_radar_source_degraded",
                "__live__",
                {
                    "stage": stage,
                    "primary_source": primary_source.source_id,
                    "fallback_source": fallback_source.source_id,
                    "primary_exception_type": type(primary_exc).__name__,
                    "primary_exception_message": str(primary_exc)[:500],
                    "symbols_total": len(snapshots),
                    "ok_count": len(snapshots) - missing_count,
                    "missing_count": missing_count,
                    "policy": "explicit_rest_ticker_radar_fallback_no_ohlcv_or_aggtrade_full_scan",
                },
            )
            return (
                snapshots,
                fallback_source.source_id,
                "degraded_rest_fallback",
                f"primary {primary_source.source_id} failed: {type(primary_exc).__name__}: {str(primary_exc)[:240]}",
            )

    def _seed_startup_ticker_radar(self, symbols: list[str]) -> None:
        if not (
            self.config.live_ws_ticker_startup_seed_enabled
            and self._ticker_radar_required_for_subminute_gate()
        ):
            return
        seed_consumer = getattr(self.ticker_snapshot_source, "seed_from_snapshots", None)
        if not callable(seed_consumer):
            return
        seed_source = RestLiveTickerSnapshotSource(self.exchange)
        try:
            snapshots = seed_source.fetch_snapshots(tuple(symbols))
            seed_result = seed_consumer(snapshots)
        except Exception as exc:
            self.artifacts.append_event(
                "ticker_radar_startup_seed_failed",
                "__live__",
                {
                    "seed_source": seed_source.source_id,
                    "primary_source": self.ticker_snapshot_source.source_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "continue_with_primary_ws_or_explicit_degraded_fallback",
                },
            )
            return
        self.artifacts.append_event(
            "ticker_radar_startup_seeded",
            "__live__",
            {
                "seed_source": seed_source.source_id,
                "primary_source": self.ticker_snapshot_source.source_id,
                "symbols_total": len(symbols),
                "seeded_count": seed_result.get("seeded_count", 0),
                "ok_count": seed_result.get("ok_count", 0),
                "missing_count": seed_result.get("missing_count", 0),
                "seeded_at_ms": seed_result.get("seeded_at_ms", ""),
                "policy": "one_rest_snapshot_initializes_ws_ticker_cache_only",
            },
        )

    def _validate_required_ticker_radar_source(self, symbols: list[str]) -> None:
        if not self._ticker_radar_required_for_subminute_gate():
            return
        try:
            snapshots, source, source_status, reason = self._fetch_ticker_radar_snapshots(symbols, stage="startup")
        except ExchangeConnectivityError as exc:
            self.artifacts.append_event(
                "ticker_radar_startup_failed",
                "__live__",
                {
                    "source": self.ticker_snapshot_source.source_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "startup_refuse_without_any_working_ticker_radar_source",
                },
            )
            raise LiveStartupError(
                "subminute live discovery requires a working ticker radar source before the first cycle; "
                f"error={type(exc).__name__}: {exc}"
            ) from exc
        except Exception as exc:
            self.artifacts.append_event(
                "ticker_radar_startup_failed",
                "__live__",
                {
                    "source": self.ticker_snapshot_source.source_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                    "symbols_total": len(symbols),
                    "policy": "startup_refuse_without_required_ticker_radar",
                },
            )
            raise LiveStartupError(
                "subminute live discovery requires a working ticker radar source before the first cycle; "
                f"source={self.ticker_snapshot_source.source_id}; error={type(exc).__name__}: {exc}"
            ) from exc
        missing_count = sum(1 for snapshot in snapshots if snapshot.status != "ok")
        if snapshots and missing_count == len(snapshots):
            self.artifacts.append_event(
                "ticker_radar_startup_failed",
                "__live__",
                {
                    "source": source,
                    "source_status": source_status,
                    "symbols_total": len(snapshots),
                    "missing_count": missing_count,
                    "policy": "startup_refuse_without_valid_ticker_radar_payload",
                    "reason": "all_ticker_snapshots_missing",
                },
            )
            raise LiveStartupError(
                "subminute live discovery requires ticker radar snapshots with price and quote_volume; "
                f"source={source}; all {len(snapshots)} snapshots are missing"
            )
        self.artifacts.append_event(
            "ticker_radar_startup_ready",
            "__live__",
            {
                "source": source,
                "source_status": source_status,
                "source_reason": reason,
                "symbols_total": len(snapshots),
                "ok_count": len(snapshots) - missing_count,
                "missing_count": missing_count,
                "policy": "required_for_inactive_subminute_gate",
            },
        )

    def _filter_live_symbol_universe(self, symbols: list[str], *, explicit_symbols: bool) -> list[str]:
        if explicit_symbols or not self.config.exclude_default_high_cap_symbols:
            self.artifacts.append_event(
                "live_symbol_universe_filter",
                "__live__",
                {
                    "source": "explicit_symbols" if explicit_symbols else "exchange_usdt_swap_symbols",
                    "input_count": len(symbols),
                    "output_count": len(symbols),
                    "excluded_count": 0,
                    "excluded_symbols": [],
                    "excluded_bases": [],
                    "reason": "disabled_for_explicit_symbols" if explicit_symbols else "high_cap_filter_disabled",
                },
            )
            return symbols
        kept: list[str] = []
        excluded: list[str] = []
        for symbol in symbols:
            base = _compact_symbol(symbol)
            if base in LIVE_DEFAULT_EXCLUDED_HIGH_CAP_BASES:
                excluded.append(symbol)
            else:
                kept.append(symbol)
        self.artifacts.append_event(
            "live_symbol_universe_filter",
            "__live__",
            {
                "source": "exchange_usdt_swap_symbols",
                "input_count": len(symbols),
                "output_count": len(kept),
                "excluded_count": len(excluded),
                "excluded_symbols": excluded,
                "excluded_bases": sorted({_compact_symbol(symbol) for symbol in excluded}),
                "reason": "static_high_cap_exclusion",
            },
        )
        return kept

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

    def _validate_live_account_mode(self) -> None:
        try:
            preflight = self.exchange.fetch_live_account_preflight()
        except Exception as exc:
            self.artifacts.append_event(
                "live_account_preflight_failed",
                "__live__",
                {"reason": f"{type(exc).__name__}: {exc}"},
            )
            raise LiveStartupError("Live account-mode preflight failed before real-order startup") from exc
        if preflight.hedge_mode_enabled:
            self.artifacts.append_event(
                "live_account_preflight_failed",
                "__live__",
                {
                    "exchange": preflight.exchange,
                    "position_mode": preflight.position_mode,
                    "hedge_mode_enabled": preflight.hedge_mode_enabled,
                    "required_position_mode": "one_way",
                    "reason": "hedge_mode_not_supported_by_live_execution_contract",
                },
            )
            raise LiveStartupError("Binance account is in hedge mode; live runner requires one-way position mode")
        self.artifacts.append_event(
            "live_account_preflight_ok",
            "__live__",
            {
                "exchange": preflight.exchange,
                "position_mode": preflight.position_mode,
                "hedge_mode_enabled": preflight.hedge_mode_enabled,
                "required_position_mode": "one_way",
                "policy": "fail_fast_before_any_real_order",
            },
        )

    def _close_startup_exchange_positions(self, symbols: list[str]) -> None:
        try:
            snapshots = self.exchange.fetch_position_snapshots(tuple(symbols))
        except Exception as exc:
            self.artifacts.append_event(
                "startup_position_cleanup_failed",
                "__live__",
                {"stage": "fetch_positions", "reason": f"{type(exc).__name__}: {exc}"},
            )
            raise LiveStartupError("Startup exchange-position cleanup failed before live loop") from exc
        nonzero_snapshots = [snapshot for snapshot in snapshots if abs(float(snapshot.signed_amount)) > 0.0]
        self.artifacts.append_event(
            "startup_position_cleanup_started",
            "__live__",
            {
                "symbols_checked": len(symbols),
                "position_rows": len(snapshots),
                "nonzero_positions": len(nonzero_snapshots),
                "policy": "reduce_only_close_all_existing_exchange_positions_before_live_loop",
            },
        )
        closed = 0
        failed = 0
        for snapshot in nonzero_snapshots:
            symbol = snapshot.symbol
            signed_amount = float(snapshot.signed_amount)
            try:
                self._close_startup_exchange_position(
                    symbol,
                    signed_amount=signed_amount,
                    reason="startup_existing_exchange_position",
                )
                closed += 1
            except Exception as exc:
                failed += 1
                self.artifacts.append_event(
                    "startup_position_close_failed",
                    symbol,
                    {
                        "exchange_position_amount": signed_amount,
                        "exchange_position_side": snapshot.side,
                        "source": snapshot.source,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise LiveStartupError(
                    f"Startup exchange position could not be closed: symbol={symbol} amount={signed_amount}"
                ) from exc
        self.artifacts.append_event(
            "startup_position_cleanup_finished",
            "__live__",
            {"closed": closed, "failed": failed},
        )

    def _close_startup_exchange_position(
        self,
        symbol: str,
        *,
        signed_amount: float,
        reason: str,
    ) -> None:
        amount = abs(float(signed_amount))
        if not math.isfinite(amount) or amount <= 0.0:
            raise LiveDataIntegrityError(f"invalid startup exchange position amount: symbol={symbol} amount={signed_amount}")
        side = "sell" if signed_amount > 0.0 else "buy"
        client_order_id = _live_client_order_id("startup", symbol, reason, int(time.time() * 1000))
        fill = self.exchange.create_market_order_with_fill(
            symbol,
            side,
            amount,
            reduce_only=True,
            client_order_id=client_order_id,
        )
        self._track_order_reconcile_symbol(symbol, reason=reason)
        post_amount = float(self.exchange.fetch_symbol_position_amount(symbol))
        if not math.isfinite(post_amount) or abs(post_amount) > max(amount * self.config.max_position_amount_slippage_ratio, 1e-12):
            self.artifacts.append_event(
                "startup_position_close_unverified",
                symbol,
                {
                    "reason": reason,
                    "initial_exchange_position_amount": signed_amount,
                    "post_exchange_position_amount": _finite_or_none(post_amount),
                    "client_order_id": client_order_id,
                    "order_id": fill.order_id,
                },
            )
            raise LiveDataIntegrityError(
                f"startup exchange position close not verified: symbol={symbol} post_amount={post_amount}"
            )
        cancelled_orders = self._cancel_orphan_orders_for_symbol(symbol, symbol_key=_position_symbol_key(symbol))
        self.artifacts.append_event(
            "startup_position_closed",
            symbol,
            {
                "reason": reason,
                "initial_exchange_position_amount": signed_amount,
                "close_side": side,
                "close_amount": amount,
                "client_order_id": client_order_id,
                "order_id": fill.order_id,
                "status": fill.status,
                "fill_timestamp_ms": fill.timestamp_ms,
                "fill_price": fill.average_price,
                "filled_amount": fill.filled_amount,
                "post_exchange_position_amount": post_amount,
                "orphan_orders_cancelled": cancelled_orders,
            },
        )

    def _reset_cycle_fetch_state(self) -> None:
        self._current_cycle_aggtrade_cache = {}
        self._cycle_aggtrade_requests = 0
        self._cycle_aggtrade_network_calls = 0
        self._cycle_aggtrade_cache_hits = 0
        self._cycle_aggtrade_process_cache_hits = 0
        self._cycle_aggtrade_coalesced_missing_ranges = 0
        self._cycle_aggtrade_rest_fetched_ms = 0
        self._cycle_ws_aggtrade_backfill_reads = 0
        self._cycle_ws_aggtrade_backfilled_rows = 0
        self._cycle_ws_aggtrade_coverage_pending = 0
        self._cycle_ws_aggtrade_not_connected_backfill_reads = 0
        self._cycle_aggtrade_gap_prefetch_symbols = 0
        self._cycle_aggtrade_gap_prefetch_requested_ranges = 0
        self._cycle_aggtrade_gap_prefetch_missing_ranges = 0
        self._cycle_aggtrade_gap_prefetch_backfill_ranges = 0
        self._cycle_aggtrade_gap_prefetch_rows = 0
        self._cycle_aggtrade_gap_prefetch_pending = 0
        self._current_batch_symbol_scan_mode = {}
        self._cycle_precise_scan_symbols = 0
        self._cycle_inactive_visit_symbols = 0
        self._cycle_deferred_inactive_subminute_pairs = 0

    def _full_symbol_cycle_seconds(self, *, batch_seconds: float, batch_size: int, symbols_total: int) -> float:
        if self._last_symbol_universe_cycle_seconds is not None:
            return self._last_symbol_universe_cycle_seconds
        if batch_size <= 0 or symbols_total <= 0:
            return float(batch_seconds)
        batches_per_universe = math.ceil(symbols_total / max(1, batch_size))
        return float(batch_seconds) * float(max(1, batches_per_universe))

    def _active_live_symbol_count(self) -> int:
        with self._state_lock:
            active_keys = set(self._active_symbols)
            active_keys.update(self._opening_symbols)
            active_keys.update(_position_symbol_key(position.signal.symbol) for position in self._open_positions.values())
            return len(active_keys)

    def _active_live_symbols_seen_total(self) -> int:
        with self._state_lock:
            return len(self._active_symbols_seen)

    def _closed_pnl_pct_total(self) -> float:
        pnl_total = float(self._closed_pnl_usdt_total)
        notional_total = float(self._closed_notional_usdt_total)
        if not math.isfinite(pnl_total) or not math.isfinite(notional_total):
            raise LiveDataIntegrityError(
                "Non-finite local closed PnL counters: "
                f"pnl_usdt={self._closed_pnl_usdt_total!r} "
                f"notional_usdt={self._closed_notional_usdt_total!r}"
            )
        if abs(notional_total) <= 1e-12:
            return 0.0
        return pnl_total / notional_total

    def _next_symbol_batch(self, symbols: list[str]) -> list[str]:
        selection = self._select_next_symbol_batch(symbols)
        self._current_batch_symbol_scan_mode = dict(selection.scan_modes)
        self._current_scheduler_source = selection.scheduler_source
        self._current_inactive_scan_slots = selection.inactive_scan_slots
        self._current_inactive_scan_slots_source = selection.inactive_scan_slots_source
        self._current_inactive_cold_coverage_gate_reason = selection.inactive_cold_coverage_gate_reason
        self._current_inactive_cold_coverage_health_pct = selection.inactive_cold_coverage_health_pct
        self._current_inactive_cold_coverage_health_threshold_pct = selection.inactive_cold_coverage_health_threshold_pct
        self._current_inactive_cold_coverage_adaptive_score = selection.inactive_cold_coverage_adaptive_score
        self._current_inactive_cold_coverage_health_factor = selection.inactive_cold_coverage_health_factor
        self._current_inactive_cold_coverage_speed_factor = selection.inactive_cold_coverage_speed_factor
        self._current_inactive_cold_coverage_active_factor = selection.inactive_cold_coverage_active_factor
        self._current_inactive_cold_coverage_load_factor = selection.inactive_cold_coverage_load_factor
        self._current_inactive_cold_coverage_pressure_ewma = selection.inactive_cold_coverage_pressure_ewma
        self._current_inactive_cold_coverage_cycle_seconds_ewma = selection.inactive_cold_coverage_cycle_seconds_ewma
        self.artifacts.append_event(
            "symbol_batch_selected",
            "__live__",
            {
                "scheduler_source": selection.scheduler_source,
                "active_symbols": list(selection.active_due),
                "active_count": len(selection.active_due),
                "active_waiting_count": len(selection.active_waiting),
                "active_waiting_symbols": list(selection.active_waiting),
                "ticker_radar_symbols": list(selection.radar_due),
                "ticker_radar_count": len(selection.radar_due),
                "ticker_radar_waiting_count": len(selection.radar_waiting),
                "ticker_radar_waiting_symbols": list(selection.radar_waiting),
                "max_precise_scan_symbols_per_cycle": (
                    self.config.max_precise_scan_symbols_per_cycle
                    if self.config.max_precise_scan_symbols_per_cycle is not None
                    else ""
                ),
                "precise_budget_remaining_after_active": (
                    selection.precise_budget_remaining_after_active
                    if selection.precise_budget_remaining_after_active is not None
                    else ""
                ),
                "precise_budget_remaining_after_radar": (
                    selection.precise_budget_remaining_after_radar
                    if selection.precise_budget_remaining_after_radar is not None
                    else ""
                ),
                "inactive_count": len(selection.inactive),
                "inactive_scan_slots_per_cycle": selection.inactive_scan_slots,
                "inactive_scan_slots_source": selection.inactive_scan_slots_source,
                "configured_inactive_scan_slots_per_cycle": (
                    self.config.inactive_scan_slots_per_cycle
                    if self.config.inactive_scan_slots_per_cycle is not None
                    else ""
                ),
                "inactive_cold_coverage_danger": bool(selection.inactive_cold_coverage_danger),
                "inactive_cold_coverage_gate_reason": selection.inactive_cold_coverage_gate_reason,
                "inactive_cold_coverage_health_pct": round(selection.inactive_cold_coverage_health_pct, 3),
                "inactive_cold_coverage_health_threshold_pct": round(
                    selection.inactive_cold_coverage_health_threshold_pct, 3
                ),
                "inactive_cold_coverage_active_blocked": bool(selection.inactive_cold_coverage_active_blocked),
                "inactive_cold_coverage_position_blocked": bool(selection.inactive_cold_coverage_position_blocked),
                "inactive_cold_coverage_active_due_count": selection.inactive_cold_coverage_active_due_count,
                "inactive_cold_coverage_active_waiting_count": selection.inactive_cold_coverage_active_waiting_count,
                "inactive_cold_coverage_adaptive_score": round(selection.inactive_cold_coverage_adaptive_score, 4),
                "inactive_cold_coverage_health_factor": round(selection.inactive_cold_coverage_health_factor, 4),
                "inactive_cold_coverage_speed_factor": round(selection.inactive_cold_coverage_speed_factor, 4),
                "inactive_cold_coverage_active_factor": round(selection.inactive_cold_coverage_active_factor, 4),
                "inactive_cold_coverage_load_factor": round(selection.inactive_cold_coverage_load_factor, 4),
                "inactive_cold_coverage_pressure_ewma": round(selection.inactive_cold_coverage_pressure_ewma, 4),
                "inactive_cold_coverage_cycle_seconds_ewma": round(selection.inactive_cold_coverage_cycle_seconds_ewma, 3),
                "inactive_cold_coverage_base_slots": selection.inactive_cold_coverage_base_slots,
                "inactive_cold_coverage_max_slots": selection.inactive_cold_coverage_max_slots,
                "inactive_subminute_scan_policy": "DANGER_adaptive_precise_cold_coverage_health_latency_pressure_gated",
                "subminute_entry_pairs_present": bool(_live_config_has_subminute_entry_pairs(self.config)),
                "scan_hot_timeframes_per_symbol": bool(self.config.scan_hot_timeframes_per_symbol),
                "symbol_batch_size": self.config.symbol_batch_size,
                "symbol_batch_size_role": "legacy_inactive_scan_cap_when_no_explicit_inactive_budget",
                "batch_in_full_cycle": selection.batch_in_full_cycle,
                "full_symbol_cycle": selection.full_symbol_cycle,
                "effective_scan_count": len(selection.batch),
                "scan_reason_by_symbol": {
                    symbol: selection.scan_modes.get(_position_symbol_key(symbol), "unknown")
                    for symbol in selection.batch
                },
                "inactive_cursor_before": selection.inactive_cursor_before,
                "inactive_cursor_after": selection.inactive_cursor_after,
                "last_full_symbol_cycle_seconds": (
                    round(self._last_symbol_universe_cycle_seconds, 3)
                    if self._last_symbol_universe_cycle_seconds is not None
                    else ""
                ),
            },
        )
        return list(selection.batch)

    def _update_ws_aggtrade_subscriptions(self, batch: list[str]) -> LiveWsAggTradeSubscriptionStats:
        if self.aggtrade_source is None:
            return LiveWsAggTradeSubscriptionStats(
                enabled=False,
                source="disabled",
                connection_status="disabled",
            )
        if not _live_config_has_subminute_entry_pairs(self.config):
            target_symbols: tuple[str, ...] = ()
        else:
            target_by_key = {
                _position_symbol_key(symbol): symbol
                for symbol in batch
                if self._subminute_entry_scan_allowed(symbol)
            }
            with self._state_lock:
                for state in self._active_symbols.values():
                    target_by_key[_position_symbol_key(state.symbol)] = state.symbol
                for watch in self._ticker_radar_watch.values():
                    target_by_key[_position_symbol_key(watch.symbol)] = watch.symbol
            target_symbols = tuple(target_by_key.values())
        status = self.aggtrade_source.set_symbols(target_symbols)
        if int(status.get("target_count", 0) or 0) > int(status.get("subscribed_count", 0) or 0):
            wait_status = self.aggtrade_source.wait_for_targets(timeout_seconds=0.5)
            status = {**status, **wait_status}
        self.artifacts.append_event(
            "ws_aggtrade_subscription_target",
            "__live__",
            {
                "source": status.get("source", self.aggtrade_source.source_id),
                "target_count": status.get("target_count", len(target_symbols)),
                "subscribed_count": status.get("subscribed_count", ""),
                "connection_status": status.get("connection_status", ""),
                "last_error": str(status.get("last_error", ""))[:500],
                "target_symbols": status.get("target_symbols", list(target_symbols)),
            },
        )
        return LiveWsAggTradeSubscriptionStats(
            enabled=True,
            source=str(status.get("source", self.aggtrade_source.source_id)),
            target_count=int(status.get("target_count", len(target_symbols)) or 0),
            subscribed_count=status.get("subscribed_count", ""),
            connection_status=str(status.get("connection_status", "")),
            last_error=str(status.get("last_error", ""))[:500],
        )


    def _ws_aggtrade_effective_source_for_cycle(self, stats: LiveWsAggTradeSubscriptionStats) -> str:
        if not stats.enabled:
            return "disabled"
        if self._cycle_ws_aggtrade_coverage_pending > 0:
            return "uncovered_ws_pending"
        connection_status = (stats.connection_status or "").lower()
        if connection_status == "connected":
            if self._cycle_ws_aggtrade_backfill_reads > 0:
                return "ws_with_rest_gap_backfill"
            return "ws"
        if self._cycle_ws_aggtrade_backfill_reads > 0:
            return "rest_backfill_degraded"
        return f"ws_{connection_status or 'unknown'}_no_entry_read"

    def _is_ws_healthy_for_cycle(
        self,
        ticker_stats: LiveTickerRadarCycleStats,
        aggtrade_stats: LiveWsAggTradeSubscriptionStats,
    ) -> tuple[bool, str]:
        ticker_health_status = self._ticker_health_status(status=ticker_stats.status, source=ticker_stats.source)
        if ticker_health_status == "not_due":
            ticker_health_status = self._last_ticker_radar_health_status
        bootstrap = self._ws_health_observed_seconds <= DEFAULT_LIVE_WS_HEALTH_BOOTSTRAP_SECONDS
        if ticker_health_status == "primary_seeded_rest" and bootstrap:
            ticker_health_status = "primary"
        if ticker_health_status != "primary":
            return False, f"ticker_{ticker_health_status or 'unknown'}"
        if not aggtrade_stats.enabled or aggtrade_stats.target_count <= 0:
            return True, "ticker_primary_no_flow_targets"
        connection_status = (aggtrade_stats.connection_status or "").lower()
        if connection_status != "connected":
            return False, f"flow_{connection_status or 'unknown'}"
        subscribed_count = _optional_int(aggtrade_stats.subscribed_count)
        if subscribed_count is None or subscribed_count < aggtrade_stats.target_count:
            return False, "flow_subscription_mismatch"
        if self._cycle_ws_aggtrade_coverage_pending > 0:
            return False, "flow_coverage_pending"
        if self._cycle_ws_aggtrade_backfill_reads > 0:
            if bootstrap:
                return True, "startup_flow_rest_backfill"
            return False, "flow_rest_backfill"
        return True, "primary_ws"

    def _live_connection_status_text(
        self,
        *,
        ticker_stats: LiveTickerRadarCycleStats,
        aggtrade_stats: LiveWsAggTradeSubscriptionStats,
        ws_healthy: bool,
        ws_health_reason: str,
    ) -> str:
        ticker_error_label = _ws_error_short_label(ticker_stats.reason)
        flow_error_label = _ws_error_short_label(aggtrade_stats.last_error)
        if ticker_stats.status == "primary_seeded_rest":
            return "ticker seed"
        if ws_healthy:
            if not aggtrade_stats.enabled or aggtrade_stats.target_count <= 0:
                return "ticker ok"
            return "ticker ok · flow ok"
        if ticker_stats.status == "failed":
            return "ticker нет" + (f"/{ticker_error_label}" if ticker_error_label else "")
        if ticker_stats.status == "degraded_rest_fallback":
            return "ticker REST" + (f"/{ticker_error_label}" if ticker_error_label else "")
        if ws_health_reason.startswith("ticker_"):
            ticker_reason = ws_health_reason.removeprefix("ticker_")
            if ticker_reason == "rest_fetch_tickers_ok":
                return "ticker REST ok"
            return ticker_reason.replace("_", " ")
        if aggtrade_stats.enabled and aggtrade_stats.target_count > 0:
            subscribed_count = _optional_int(aggtrade_stats.subscribed_count)
            connection_status = (aggtrade_stats.connection_status or "").lower()
            if connection_status != "connected":
                if self._cycle_ws_aggtrade_coverage_pending > 0:
                    return "flow pending" + (f"/{flow_error_label}" if flow_error_label else "")
                if self._cycle_ws_aggtrade_backfill_reads > 0:
                    return "flow REST" + (f"/{flow_error_label}" if flow_error_label else "")
                return "flow нет" + (f"/{flow_error_label}" if flow_error_label else "")
            if subscribed_count is None or subscribed_count < aggtrade_stats.target_count:
                return "flow подписка"
            if self._cycle_ws_aggtrade_backfill_reads > 0:
                return "flow gap REST"
            if self._cycle_ws_aggtrade_coverage_pending > 0:
                return "flow pending"
        return ws_health_reason.replace("_", " ")

    def _ticker_health_status(self, *, status: str, source: str) -> str:
        if status == "ok" and source == "binance_ws_all_ticker":
            return "primary"
        if status == "ok":
            return f"{source}_ok"
        return status

    def _record_ws_health_sample(self, *, healthy: bool) -> float:
        now = time.monotonic()
        sample_seconds = max(0.0, now - self._last_ws_health_sample_at)
        self._last_ws_health_sample_at = now
        self._ws_health_observed_seconds += sample_seconds
        if healthy:
            self._ws_health_healthy_seconds += sample_seconds
        if self._ws_health_observed_seconds <= 0.0:
            return 0.0
        return self._ws_health_healthy_seconds / self._ws_health_observed_seconds

    def _current_ws_health_ratio(self) -> float:
        if self._ws_health_observed_seconds <= 0.0:
            return 0.0
        return self._ws_health_healthy_seconds / self._ws_health_observed_seconds

    def _record_scheduler_cycle_seconds(self, cycle_seconds: float) -> None:
        if not math.isfinite(cycle_seconds) or cycle_seconds < 0.0:
            return
        alpha = DANGER_ADAPTIVE_COLD_COVERAGE_CYCLE_EWMA_ALPHA
        if not math.isfinite(self._scheduler_cycle_seconds_ewma):
            self._scheduler_cycle_seconds_ewma = cycle_seconds
            return
        self._scheduler_cycle_seconds_ewma = (
            alpha * cycle_seconds + (1.0 - alpha) * self._scheduler_cycle_seconds_ewma
        )

    def _record_cold_coverage_pressure_sample(self) -> None:
        network_pressure = self._linear_ramp(
            float(self._cycle_aggtrade_network_calls),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_NETWORK_CALLS_HIGH),
        )
        fetched_ms_pressure = self._linear_ramp(
            float(self._cycle_aggtrade_rest_fetched_ms),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_REST_FETCHED_MS_HIGH),
        )
        pending_count = int(self._cycle_ws_aggtrade_coverage_pending) + int(self._cycle_aggtrade_gap_prefetch_pending)
        pending_pressure = self._linear_ramp(
            float(pending_count),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_PENDING_GAPS_HIGH),
        )
        pressure = max(0.0, min(1.0, max(network_pressure, fetched_ms_pressure, pending_pressure)))
        alpha = DANGER_ADAPTIVE_COLD_COVERAGE_PRESSURE_EWMA_ALPHA
        if not math.isfinite(self._cold_coverage_pressure_ewma):
            self._cold_coverage_pressure_ewma = pressure
            return
        self._cold_coverage_pressure_ewma = (
            alpha * pressure + (1.0 - alpha) * self._cold_coverage_pressure_ewma
        )

    @staticmethod
    def _linear_ramp(value: float, low: float, high: float) -> float:
        if not math.isfinite(value):
            return 0.0
        if high <= low:
            return 1.0 if value >= high else 0.0
        if value <= low:
            return 0.0
        if value >= high:
            return 1.0
        return (value - low) / (high - low)

    def _adaptive_cold_coverage_slots(
        self,
        *,
        base_slots: int,
        inactive_slots_source: str,
        health_ratio: float,
        active_due_count: int,
        active_waiting_count: int,
        position_blocked: bool,
    ) -> tuple[int, str, str, float, float, float, float, float, float, float, int]:
        base_slots = max(0, int(base_slots))
        cycle_seconds_ewma = max(0.0, float(self._scheduler_cycle_seconds_ewma))
        pressure_ewma = max(0.0, min(1.0, float(self._cold_coverage_pressure_ewma)))
        if not self._inactive_slots_are_precise_cold_coverage(inactive_slots_source) or base_slots <= 0:
            return base_slots, inactive_slots_source, "", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, base_slots
        max_slots = max(0, DANGER_ADAPTIVE_COLD_COVERAGE_MAX_SLOTS_PER_CYCLE)
        if self.config.inactive_scan_slots_per_cycle is not None:
            # Explicit operator value remains DANGER cold coverage but is treated as a hard cap.
            max_slots = min(max_slots, base_slots)
        if max_slots <= 0:
            return 0, COLD_COVERAGE_GATED_OFF_SOURCE, "configured_zero", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, max_slots
        if position_blocked:
            return 0, COLD_COVERAGE_GATED_OFF_SOURCE, "open_or_opening_position_present", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, max_slots
        if active_due_count > 0:
            return 0, COLD_COVERAGE_GATED_OFF_SOURCE, "active_due_symbols_present", 0.0, 0.0, 0.0, 0.0, 0.0, pressure_ewma, cycle_seconds_ewma, max_slots
        health_factor = self._linear_ramp(
            health_ratio,
            DANGER_ADAPTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO,
            DANGER_ADAPTIVE_COLD_COVERAGE_FULL_WS_HEALTH_RATIO,
        )
        speed_factor = 1.0 - self._linear_ramp(
            cycle_seconds_ewma,
            DANGER_ADAPTIVE_COLD_COVERAGE_FAST_CYCLE_SECONDS,
            DANGER_ADAPTIVE_COLD_COVERAGE_SLOW_CYCLE_SECONDS,
        )
        active_factor = 1.0 - self._linear_ramp(
            float(active_waiting_count),
            0.0,
            float(DANGER_ADAPTIVE_COLD_COVERAGE_ACTIVE_WAITING_SOFT_CAP),
        )
        load_factor = 1.0 - pressure_ewma
        adaptive_score = max(0.0, min(1.0, health_factor * speed_factor * active_factor * load_factor))
        if adaptive_score < DANGER_ADAPTIVE_COLD_COVERAGE_MIN_SCORE:
            reason = "adaptive_score_below_threshold"
            if health_factor <= 0.0:
                reason = "ws_health_below_adaptive_min"
            elif speed_factor <= 0.0:
                reason = "heartbeat_too_slow"
            elif active_factor <= 0.0:
                reason = "active_waiting_soft_cap_reached"
            elif load_factor <= 0.0:
                reason = "rest_or_cache_pressure_high"
            return (
                0,
                COLD_COVERAGE_GATED_OFF_SOURCE,
                reason,
                adaptive_score,
                health_factor,
                speed_factor,
                active_factor,
                load_factor,
                pressure_ewma,
                cycle_seconds_ewma,
                max_slots,
            )
        slots = max(1, int(math.ceil(float(max_slots) * adaptive_score)))
        slots = min(max_slots, slots)
        return (
            slots,
            inactive_slots_source,
            "",
            adaptive_score,
            health_factor,
            speed_factor,
            active_factor,
            load_factor,
            pressure_ewma,
            cycle_seconds_ewma,
            max_slots,
        )

    def _default_inactive_scan_slots(self) -> tuple[int, str]:
        if self.config.inactive_scan_slots_per_cycle is not None:
            return max(0, int(self.config.inactive_scan_slots_per_cycle)), EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE
        if self.config.ticker_radar_enabled and _live_config_has_subminute_entry_pairs(self.config):
            return DANGER_DEFAULT_INACTIVE_COLD_COVERAGE_SLOTS_PER_CYCLE, DANGER_INACTIVE_COLD_COVERAGE_SOURCE
        return max(0, int(self.config.symbol_batch_size)), "legacy_symbol_batch_size"

    @staticmethod
    def _inactive_slots_are_precise_cold_coverage(source: str) -> bool:
        return source in {DANGER_INACTIVE_COLD_COVERAGE_SOURCE, EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE}

    def _select_next_symbol_batch(self, symbols: list[str]) -> LiveSymbolBatchSelection:
        now_ms = int(time.time() * 1000)
        inactive_cursor_before = self._inactive_cursor
        batch_full_cycle = self._symbol_universe_cycle_index
        batch_in_full_cycle = self._symbol_universe_batch_index + 1
        self._current_symbol_universe_cycle_index = batch_full_cycle
        self._current_symbol_universe_batch_index = batch_in_full_cycle
        active_due, active_waiting = self._active_symbol_batch(now_ms=now_ms)
        active_keys = {_position_symbol_key(symbol) for symbol in [*active_due, *active_waiting]}
        precise_budget_remaining = None
        if self.config.max_precise_scan_symbols_per_cycle is not None:
            precise_budget_remaining = max(0, int(self.config.max_precise_scan_symbols_per_cycle) - len(active_due))
        radar_due, radar_waiting = self._ticker_radar_batch(
            now_ms=now_ms,
            excluded_keys=active_keys,
            max_due=precise_budget_remaining,
        )
        radar_due_keys = {_position_symbol_key(symbol) for symbol in radar_due}
        radar_waiting_keys = {_position_symbol_key(symbol) for symbol in radar_waiting}
        base_inactive_slots, inactive_slots_source = self._default_inactive_scan_slots()
        cold_coverage_health_ratio = self._current_ws_health_ratio()
        cold_coverage_gate_reason = ""
        active_blocked = bool(active_due)
        with self._state_lock:
            position_blocked = bool(self._open_positions or self._opening_symbols)
        precise_budget_remaining_after_radar = None
        if self.config.max_precise_scan_symbols_per_cycle is not None:
            precise_budget_remaining_after_radar = max(
                0,
                int(self.config.max_precise_scan_symbols_per_cycle) - len(active_due) - len(radar_due),
            )
        if self.config.inactive_scan_slots_per_cycle is None and inactive_slots_source == "legacy_symbol_batch_size":
            inactive_slots = max(0, base_inactive_slots - len(active_due))
            cold_coverage_adaptive_score = 0.0
            cold_coverage_health_factor = 0.0
            cold_coverage_speed_factor = 0.0
            cold_coverage_active_factor = 0.0
            cold_coverage_load_factor = 0.0
            cold_coverage_pressure_ewma = self._cold_coverage_pressure_ewma
            cold_coverage_cycle_seconds_ewma = self._scheduler_cycle_seconds_ewma
            cold_coverage_max_slots = max(0, inactive_slots)
        else:
            (
                inactive_slots,
                inactive_slots_source,
                cold_coverage_gate_reason,
                cold_coverage_adaptive_score,
                cold_coverage_health_factor,
                cold_coverage_speed_factor,
                cold_coverage_active_factor,
                cold_coverage_load_factor,
                cold_coverage_pressure_ewma,
                cold_coverage_cycle_seconds_ewma,
                cold_coverage_max_slots,
            ) = self._adaptive_cold_coverage_slots(
                base_slots=base_inactive_slots,
                inactive_slots_source=inactive_slots_source,
                health_ratio=cold_coverage_health_ratio,
                active_due_count=len(active_due),
                active_waiting_count=len(active_waiting),
                position_blocked=position_blocked,
            )
        if self._inactive_slots_are_precise_cold_coverage(inactive_slots_source) and precise_budget_remaining_after_radar is not None:
            inactive_slots_before_budget = inactive_slots
            inactive_slots = min(inactive_slots, precise_budget_remaining_after_radar)
            if inactive_slots_before_budget > 0 and inactive_slots <= 0:
                inactive_slots_source = COLD_COVERAGE_GATED_OFF_SOURCE
                cold_coverage_gate_reason = "precise_scan_budget_exhausted"
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
        batch_scan_modes: dict[str, str] = {}
        for symbol in active_due:
            batch_scan_modes[_position_symbol_key(symbol)] = "precise_active"
        for symbol in radar_due:
            batch_scan_modes[_position_symbol_key(symbol)] = "precise_ticker_radar"
        inactive_scan_mode = (
            "precise_DANGER_cold_coverage"
            if inactive_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE
            else (
                "precise_explicit_cold_coverage"
                if inactive_slots_source == EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE
                else "inactive_deferred_subminute"
            )
        )
        for symbol in inactive:
            batch_scan_modes[_position_symbol_key(symbol)] = inactive_scan_mode
        if symbols and self._inactive_cursor // len(symbols) > inactive_cursor_before // len(symbols):
            now_monotonic = time.monotonic()
            self._last_symbol_universe_cycle_seconds = now_monotonic - self._symbol_universe_scan_started_at
            self._symbol_universe_scan_started_at = now_monotonic
            completed_cycles = max(
                1,
                self._inactive_cursor // len(symbols) - inactive_cursor_before // len(symbols),
            )
            self._symbol_universe_cycle_index += completed_cycles
            self._symbol_universe_batch_index = 0
        else:
            self._symbol_universe_batch_index = batch_in_full_cycle
        if inactive_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE:
            scheduler_source = "DANGER_ws_event_driven_plus_precise_cold_coverage"
        elif inactive_slots_source == EXPLICIT_INACTIVE_COLD_COVERAGE_SOURCE:
            scheduler_source = "configured_precise_cold_coverage_scheduler"
        elif inactive_slots_source == COLD_COVERAGE_GATED_OFF_SOURCE:
            scheduler_source = "ws_event_driven_scheduler_cold_coverage_gated"
        elif inactive_slots_source == "default_ws_event_driven_subminute":
            scheduler_source = "ws_event_driven_scheduler"
        else:
            scheduler_source = "legacy_rest_round_robin_scheduler"
        return LiveSymbolBatchSelection(
            scheduler_source=scheduler_source,
            active_due=tuple(active_due),
            active_waiting=tuple(active_waiting),
            radar_due=tuple(radar_due),
            radar_waiting=tuple(radar_waiting),
            inactive=tuple(inactive),
            batch=tuple(batch),
            scan_modes=batch_scan_modes,
            inactive_cursor_before=inactive_cursor_before,
            inactive_cursor_after=self._inactive_cursor,
            batch_in_full_cycle=batch_in_full_cycle,
            full_symbol_cycle=batch_full_cycle,
            precise_budget_remaining_after_active=precise_budget_remaining,
            precise_budget_remaining_after_radar=precise_budget_remaining_after_radar,
            inactive_scan_slots=inactive_slots,
            inactive_scan_slots_source=inactive_slots_source,
            inactive_cold_coverage_danger=(
                inactive_slots > 0 and inactive_slots_source == DANGER_INACTIVE_COLD_COVERAGE_SOURCE
            ),
            inactive_cold_coverage_gate_reason=cold_coverage_gate_reason,
            inactive_cold_coverage_health_pct=cold_coverage_health_ratio * 100.0,
            inactive_cold_coverage_health_threshold_pct=DANGER_INACTIVE_COLD_COVERAGE_MIN_WS_HEALTH_RATIO * 100.0,
            inactive_cold_coverage_active_blocked=active_blocked,
            inactive_cold_coverage_position_blocked=position_blocked,
            inactive_cold_coverage_active_due_count=len(active_due),
            inactive_cold_coverage_active_waiting_count=len(active_waiting),
            inactive_cold_coverage_adaptive_score=cold_coverage_adaptive_score,
            inactive_cold_coverage_health_factor=cold_coverage_health_factor,
            inactive_cold_coverage_speed_factor=cold_coverage_speed_factor,
            inactive_cold_coverage_active_factor=cold_coverage_active_factor,
            inactive_cold_coverage_load_factor=cold_coverage_load_factor,
            inactive_cold_coverage_pressure_ewma=cold_coverage_pressure_ewma,
            inactive_cold_coverage_cycle_seconds_ewma=cold_coverage_cycle_seconds_ewma,
            inactive_cold_coverage_base_slots=max(0, int(base_inactive_slots)),
            inactive_cold_coverage_max_slots=max(0, int(cold_coverage_max_slots)),
        )

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

    def _ticker_radar_batch(
        self,
        *,
        now_ms: int,
        excluded_keys: set[str],
        max_due: int | None = None,
    ) -> tuple[list[str], list[str]]:
        if not self.config.ticker_radar_enabled or self.config.ticker_radar_watch_batch_size <= 0:
            return [], []
        due_limit = self.config.ticker_radar_watch_batch_size if max_due is None else min(
            self.config.ticker_radar_watch_batch_size,
            max(0, int(max_due)),
        )
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
                if len(due) < due_limit:
                    due.append(item.symbol)
                else:
                    waiting.append(item.symbol)
            else:
                waiting.append(item.symbol)
        return due, waiting

    def _maybe_update_ticker_radar(self, symbols: list[str]) -> LiveTickerRadarCycleStats:
        configured_source = self.ticker_snapshot_source.source_id
        if not self.config.ticker_radar_enabled:
            return LiveTickerRadarCycleStats(
                enabled=False,
                attempted=False,
                status="disabled",
                source=configured_source,
                reason="ticker_radar_disabled",
            )
        now_ms = int(time.time() * 1000)
        interval_ms = int(self.config.ticker_radar_interval_seconds * 1000.0)
        if now_ms - self._last_ticker_radar_at_ms < max(1, interval_ms):
            return LiveTickerRadarCycleStats(
                enabled=True,
                attempted=False,
                status="not_due",
                source=configured_source,
                reason="interval_not_elapsed",
            )
        self._last_ticker_radar_at_ms = now_ms
        try:
            snapshots, source, source_status, source_reason = self._fetch_ticker_radar_snapshots(symbols, stage="cycle")
        except Exception as exc:
            payload = {
                "source": configured_source,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:500],
                "required_for_inactive_subminute_gate": bool(self._ticker_radar_required_for_subminute_gate()),
            }
            self.artifacts.append_event("ticker_radar_failed", "__live__", payload)
            self._last_ticker_radar_health_status = "failed"
            if self._ticker_radar_required_for_subminute_gate():
                raise ExchangeConnectivityError(
                    "ticker radar source is required for inactive subminute discovery and is unavailable; "
                    f"source={configured_source}; error={type(exc).__name__}: {exc}"
                ) from exc
            return LiveTickerRadarCycleStats(
                enabled=True,
                attempted=True,
                status="failed",
                source=configured_source,
                symbols_total=len(symbols),
                reason=f"{type(exc).__name__}: {str(exc)[:240]}",
            )
        missing_count = sum(1 for snapshot in snapshots if snapshot.status != "ok")
        if snapshots and missing_count == len(snapshots):
            self.artifacts.append_event(
                "ticker_radar_snapshot_all_missing",
                "__live__",
                {
                    "source": source,
                    "source_status": source_status,
                    "source_reason": source_reason,
                    "symbols_total": len(snapshots),
                    "missing_count": missing_count,
                    "required_for_inactive_subminute_gate": bool(self._ticker_radar_required_for_subminute_gate()),
                },
            )
            self._last_ticker_radar_health_status = "all_missing"
            if self._ticker_radar_required_for_subminute_gate():
                raise ExchangeConnectivityError(
                    "ticker radar source returned no usable snapshots for inactive subminute discovery; "
                    f"source={source}; symbols_total={len(snapshots)}"
                )
            return LiveTickerRadarCycleStats(
                enabled=True,
                attempted=True,
                status="all_missing",
                source=source,
                symbols_total=len(snapshots),
                missing_count=missing_count,
                reason="all_ticker_snapshots_missing",
            )
        promotions = self._evaluate_ticker_radar_snapshots(snapshots, now_ms=now_ms)
        promoted_count = min(len(promotions), self.config.ticker_radar_max_promotions_per_cycle)
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
        ok_count = len(snapshots) - missing_count
        snapshot_status = "ok" if ok_count > 0 else "all_missing"
        self.artifacts.append_event(
            "ticker_radar_snapshot",
            "__live__",
            {
                "symbols_total": len(snapshots),
                "ok_count": ok_count,
                "missing_count": missing_count,
                "promoted_count": promoted_count,
                "promotion_candidates_count": len(promotions),
                "interval_seconds": self.config.ticker_radar_interval_seconds,
                "watch_batch_size": self.config.ticker_radar_watch_batch_size,
                "source": source,
                "source_status": source_status,
                "source_reason": source_reason,
                "status": snapshot_status if source_status == "primary" else source_status,
                "required_for_inactive_subminute_gate": bool(self._ticker_radar_required_for_subminute_gate()),
            },
        )
        if snapshots and ok_count == 0 and self._ticker_radar_required_for_subminute_gate():
            self._last_ticker_radar_health_status = "all_missing"
            raise ExchangeConnectivityError(
                "ticker radar source returned no usable snapshots while inactive subminute discovery depends on it; "
                f"source={source}; missing={missing_count}/{len(snapshots)}"
            )
        self._last_ticker_radar_health_status = self._ticker_health_status(
            status=snapshot_status if source_status == "primary" else source_status,
            source=source,
        )
        return LiveTickerRadarCycleStats(
            enabled=True,
            attempted=True,
            status=snapshot_status if source_status == "primary" else source_status,
            source=source,
            symbols_total=len(snapshots),
            ok_count=ok_count,
            missing_count=missing_count,
            promoted_count=promoted_count,
            promotion_candidates_count=len(promotions),
            reason=source_reason,
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

    def _detected_anomalies_count(self) -> int:
        with self._state_lock:
            return self._detected_anomalies_total

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
                self._detected_anomalies_total += 1
                event_payload = {
                    "detected_anomaly_index": self._detected_anomalies_total,
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
            self._active_symbols_seen.add(symbol_key)
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

    def _forget_signal_scan_closed_at(self, signal: LiveSignal, *, reason: str) -> None:
        symbol_key = _position_symbol_key(signal.symbol)
        scan_key = (symbol_key, signal.levels_timeframe.value, signal.entry_timeframe.value)
        removed_timestamp_ms: int | None = None
        with self._state_lock:
            current = self._last_signal_scan_closed_at.get(scan_key)
            if current == int(signal.decision_timestamp_ms):
                removed_timestamp_ms = self._last_signal_scan_closed_at.pop(scan_key)
        if removed_timestamp_ms is not None:
            self.artifacts.append_event(
                "signal_scan_retry_enabled",
                signal.symbol,
                {
                    "reason": reason,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                    "removed_scan_closed_timestamp_ms": int(removed_timestamp_ms),
                },
            )

    def _previous_signal_scan_closed_at(
        self,
        symbol: str,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> int | None:
        symbol_key = _position_symbol_key(symbol)
        scan_key = (symbol_key, levels_timeframe.value, entry_timeframe.value)
        with self._state_lock:
            return self._last_signal_scan_closed_at.get(scan_key)

    def _batch_scan_mode_for_symbol(self, symbol: str) -> str:
        return self._current_batch_symbol_scan_mode.get(_position_symbol_key(symbol), "precise_direct")

    def _subminute_entry_scan_allowed(self, symbol: str) -> bool:
        return self._batch_scan_mode_for_symbol(symbol).startswith("precise")

    def _should_defer_inactive_subminute_pair(self, symbol: str, entry_timeframe: Timeframe) -> bool:
        if int(entry_timeframe.to_milliseconds()) >= int(Timeframe.M1.to_milliseconds()):
            return False
        return not self._subminute_entry_scan_allowed(symbol)

    def _count_symbol_scan_mode(self, symbol: str) -> None:
        if self._batch_scan_mode_for_symbol(symbol).startswith("precise"):
            self._cycle_precise_scan_symbols += 1
        else:
            self._cycle_inactive_visit_symbols += 1

    def _categories_for_timeframe(
        self,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> tuple[LivePumpCategory, ...]:
        priority = LIVE_TIMEFRAME_CATEGORY_PRIORITY.get((levels_timeframe.value, entry_timeframe.value))
        if priority is None:
            return self._pump_categories
        ordered: list[LivePumpCategory] = []
        seen: set[str] = set()
        for category_id in priority:
            category = self._pump_categories_by_id.get(category_id)
            if category is not None:
                ordered.append(category)
                seen.add(category_id)
        ordered.extend(category for category in self._pump_categories if category.category_id not in seen)
        return tuple(ordered)

    def _load_cached_window_allow_trailing_gap(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        fetch_missing: bool = False,
    ) -> tuple[pd.DataFrame, str, int | None]:
        storage = self._ohlcv_cache_storage
        if storage is None and not fetch_missing:
            return pd.DataFrame(), "cache_storage_unavailable", None
        timeframe_ms = int(timeframe.to_milliseconds())
        if timeframe_ms <= 0:
            return pd.DataFrame(), "invalid_timeframe", None
        expected_start_ms = (int(start_timestamp_ms) // timeframe_ms) * timeframe_ms
        expected_end_ms = (int(end_timestamp_ms) // timeframe_ms) * timeframe_ms
        if expected_end_ms < expected_start_ms:
            return pd.DataFrame(), "invalid_window", None
        if fetch_missing:
            try:
                frame = _prepare_cached_ohlcv_frame(
                    self._fetch_chart_frame(
                        symbol,
                        timeframe,
                        start_timestamp_ms=expected_start_ms,
                        end_timestamp_ms=expected_end_ms,
                    )
                )
            except Exception as exc:
                return pd.DataFrame(), f"fetch_failed:{type(exc).__name__}:{str(exc)[:120]}", None
            if frame.empty:
                return pd.DataFrame(), "fetch_empty", None
        else:
            assert storage is not None
            result = storage.load_window_result(
                symbol,
                timeframe,
                start_timestamp_ms=expected_start_ms,
                end_timestamp_ms=expected_end_ms,
            )
            frame = _prepare_cached_ohlcv_frame(result.frame)
            if frame.empty:
                status = getattr(result, "status", "empty") or "empty"
                reason = getattr(result, "reason", "cache_window_empty") or "cache_window_empty"
                return pd.DataFrame(), f"{status}:{reason}", None
        timestamps = frame["timestamp"].astype("int64")
        window = frame.loc[(timestamps >= expected_start_ms) & (timestamps <= expected_end_ms)].copy()
        if window.empty:
            return pd.DataFrame(), "cache_window_empty", None
        window.sort_values("timestamp", inplace=True)
        window.reset_index(drop=True, inplace=True)
        min_cached_ms = int(window["timestamp"].iloc[0])
        max_cached_ms = int(window["timestamp"].iloc[-1])
        if min_cached_ms > expected_start_ms:
            return pd.DataFrame(), f"cache_start_gap:{min_cached_ms - expected_start_ms}", None
        effective_end_ms = min(max_cached_ms, expected_end_ms)
        if effective_end_ms < expected_start_ms:
            return pd.DataFrame(), "cache_before_window", None
        missing_ranges = _missing_ohlcv_ranges(
            window,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=effective_end_ms,
            timeframe_ms=timeframe_ms,
        )
        if missing_ranges:
            return pd.DataFrame(), f"cache_gap:{len(missing_ranges)}", None
        effective_window = window.loc[window["timestamp"].astype("int64") <= effective_end_ms].copy()
        if effective_window.empty:
            return pd.DataFrame(), "cache_window_empty", None
        return effective_window.sort_values("timestamp").reset_index(drop=True), "ok", effective_end_ms

    def _live_prior_fast_fade_72h(
        self,
        symbol: str,
        *,
        decision_timestamp_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> dict[str, object]:
        decision_ts = int(decision_timestamp_ms)
        cache_key = (symbol, levels_timeframe.value, entry_timeframe.value, decision_ts)
        cached = self._prior_fast_fade_cache.get(cache_key)
        if cached is not None:
            return cached
        setup_ms = int(levels_timeframe.to_milliseconds())
        entry_ms = int(entry_timeframe.to_milliseconds())
        context_timeframe = levels_timeframe
        context_ms = setup_ms
        maturity_ms = max(240, 60) * entry_ms
        history_start_ms = decision_ts - 3 * 86_400_000
        context_start_ms = history_start_ms - int(self.config.baseline_candles) * context_ms
        context_frame, context_status, context_cache_end_ms = self._load_cached_window_allow_trailing_gap(
            symbol,
            context_timeframe,
            start_timestamp_ms=context_start_ms,
            end_timestamp_ms=decision_ts,
            fetch_missing=True,
        )
        if context_status != "ok" or context_cache_end_ms is None:
            result = {
                "status": "unavailable",
                "reason": f"context={context_status}",
                "coverage_reason": context_status,
                "coverage_policy": "levels_timeframe_context_fetch_missing_ignore_trailing_gap",
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "history_start_timestamp_ms": history_start_ms,
                "context_timeframe": context_timeframe.value,
                "context_start_timestamp_ms": context_start_ms,
                "context_cache_end_timestamp_ms": context_cache_end_ms if context_cache_end_ms is not None else "",
                "setup_cache_end_timestamp_ms": context_cache_end_ms if context_cache_end_ms is not None else "",
                "entry_cache_end_timestamp_ms": context_cache_end_ms if context_cache_end_ms is not None else "",
                "effective_cache_end_timestamp_ms": "",
                "ignored_tail_ms": "",
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        effective_cache_end_ms = min(int(context_cache_end_ms), decision_ts)
        if effective_cache_end_ms <= history_start_ms:
            result = {
                "status": "unavailable",
                "reason": "context_cache_effective_end_before_history_start",
                "coverage_reason": "context_cache_effective_end_before_history_start",
                "coverage_policy": "levels_timeframe_context_fetch_missing_ignore_trailing_gap",
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "history_start_timestamp_ms": history_start_ms,
                "context_timeframe": context_timeframe.value,
                "context_start_timestamp_ms": context_start_ms,
                "context_cache_end_timestamp_ms": int(context_cache_end_ms),
                "setup_cache_end_timestamp_ms": int(context_cache_end_ms),
                "entry_cache_end_timestamp_ms": int(context_cache_end_ms),
                "effective_cache_end_timestamp_ms": effective_cache_end_ms,
                "ignored_tail_ms": max(0, decision_ts - effective_cache_end_ms),
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        try:
            from research_tools.anomaly_continuation_lab import AnomalyLabConfig
            from research_tools.anomaly_strategy_backtest import AnomalyBacktestConfig, _collect_symbol_pair_rows

            forward_high_candles = max(1, math.ceil(60 * entry_ms / context_ms))
            forward_low_candles = max(1, math.ceil(30 * entry_ms / context_ms))
            lab_config = AnomalyLabConfig(
                cache_dir=self.config.cache_dir or Path("."),
                output_dir=self.config.results_dir,
                timeframe=context_timeframe.value,
                days=3,
                end_timestamp_ms=effective_cache_end_ms,
                baseline_candles=int(self.config.baseline_candles),
                confirmation_candles=int(self.config.confirmation_candles),
                forward_high_candles=forward_high_candles,
                forward_low_candles=forward_low_candles,
                min_quote_ratio_start=float(self.config.min_quote_ratio_start),
                min_trade_ratio_start=float(self.config.min_trade_ratio_start),
            )
            backtest_config = AnomalyBacktestConfig(
                lab_config=lab_config,
                setup_timeframe=context_timeframe.value,
                entry_timeframe=context_timeframe.value,
                feature_contract="live_prior_fast_fade_levels_context_v1",
            )
            rows = _collect_symbol_pair_rows(
                symbol=symbol,
                setup_frame=context_frame,
                entry_frame=context_frame,
                config=backtest_config,
                entry_flow_source="live_cache_prior_fast_fade_levels_context",
            )
        except Exception as exc:
            result = {
                "status": "unavailable",
                "reason": f"compute_error:{type(exc).__name__}:{str(exc)[:160]}",
                "coverage_policy": "levels_timeframe_context_fetch_missing_ignore_trailing_gap",
                "prior_fast_fade_count_72h": None,
                "prior_spike_count_72h": None,
                "history_start_timestamp_ms": history_start_ms,
                "context_timeframe": context_timeframe.value,
                "context_start_timestamp_ms": context_start_ms,
                "context_cache_end_timestamp_ms": int(context_cache_end_ms),
                "setup_cache_end_timestamp_ms": int(context_cache_end_ms),
                "entry_cache_end_timestamp_ms": int(context_cache_end_ms),
                "effective_cache_end_timestamp_ms": effective_cache_end_ms,
                "ignored_tail_ms": max(0, decision_ts - effective_cache_end_ms),
            }
            self._prior_fast_fade_cache[cache_key] = result
            return result
        mature_cutoff = decision_ts - maturity_ms
        prior_rows = [
            row
            for row in rows
            if history_start_ms <= int(row.get("decision_timestamp_ms", 0)) < effective_cache_end_ms
            and int(row.get("decision_timestamp_ms", 0)) <= mature_cutoff
        ]
        fast_fades = [row for row in prior_rows if str(row.get("outcome_label", "")) == "fast_fade"]
        result = {
            "status": "ok",
            "reason": "ok",
            "coverage_policy": "levels_timeframe_context_fetch_missing_ignore_trailing_gap",
            "prior_fast_fade_count_72h": int(len(fast_fades)),
            "prior_spike_count_72h": int(len(prior_rows)),
            "history_start_timestamp_ms": history_start_ms,
            "context_timeframe": context_timeframe.value,
            "context_start_timestamp_ms": context_start_ms,
            "context_cache_end_timestamp_ms": int(context_cache_end_ms),
            "setup_cache_end_timestamp_ms": int(context_cache_end_ms),
            "entry_cache_end_timestamp_ms": int(context_cache_end_ms),
            "effective_cache_end_timestamp_ms": effective_cache_end_ms,
            "ignored_tail_ms": max(0, decision_ts - effective_cache_end_ms),
            "ignored_tail_entry_candles": max(0, (decision_ts - effective_cache_end_ms) // entry_ms),
            "prior_fast_fade_context_forward_high_candles": forward_high_candles,
            "prior_fast_fade_context_forward_low_candles": forward_low_candles,
        }
        self._prior_fast_fade_cache[cache_key] = result
        return result

    def _due_subminute_entry_raw_ranges(
        self,
        symbol: str,
        *,
        now_ms: int,
    ) -> list[tuple[int, int]]:
        if not self._subminute_entry_scan_allowed(symbol):
            return []
        ranges: list[tuple[int, int]] = []
        for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
            if int(entry_timeframe.to_milliseconds()) >= int(Timeframe.M1.to_milliseconds()):
                continue
            levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
            latest_closed_entry_ts = _latest_closed_candle_start_ms(entry_timeframe, now_ms=now_ms)
            setup_start_ts = (latest_closed_entry_ts // levels_timeframe_ms) * levels_timeframe_ms
            if not self._signal_scan_due_for_timeframe(
                symbol,
                levels_timeframe,
                entry_timeframe,
                closed_timestamp_ms=latest_closed_entry_ts,
            ):
                continue
            if self._should_defer_inactive_subminute_pair(symbol, entry_timeframe):
                continue
            ranges.append((int(setup_start_ts), int(now_ms)))
        return _merge_time_ranges(ranges)

    def _prefetch_symbol_subminute_entry_gap_debt(
        self,
        symbol: str,
        *,
        now_ms: int,
        reason: str,
    ) -> None:
        source = self.aggtrade_source
        if source is None:
            return
        requested_ranges = self._due_subminute_entry_raw_ranges(symbol, now_ms=now_ms)
        if not requested_ranges:
            return
        missing_ranges: list[tuple[int, int]] = []
        read_status_counts: dict[str, int] = {}
        ws_rows_total = 0
        buffer_rows_total = 0
        connection_statuses: set[str] = set()
        for start_ms, end_ms in requested_ranges:
            read_result = source.read_rows(
                symbol,
                start_timestamp_ms=int(start_ms),
                end_timestamp_ms=int(end_ms),
            )
            ws_rows_total += int(len(read_result.rows))
            buffer_rows_total += int(read_result.buffer_row_count)
            read_status_counts[read_result.status] = read_status_counts.get(read_result.status, 0) + 1
            if read_result.connection_status:
                connection_statuses.add(str(read_result.connection_status))
            missing_ranges.extend((int(start), int(end)) for start, end in read_result.missing_ranges)
        missing_ranges = _merge_time_ranges(missing_ranges)
        if not missing_ranges:
            return
        missing_total_ms = self._time_ranges_duration_ms(missing_ranges)
        max_backfill_ms = int(self.config.live_ws_aggtrade_max_backfill_ms)
        self._cycle_aggtrade_gap_prefetch_symbols += 1
        self._cycle_aggtrade_gap_prefetch_requested_ranges += int(len(requested_ranges))
        self._cycle_aggtrade_gap_prefetch_missing_ranges += int(len(missing_ranges))
        if missing_total_ms > max_backfill_ms:
            self._cycle_aggtrade_gap_prefetch_pending += 1
            self.artifacts.append_event(
                "aggtrade_rest_gap_prefetch",
                symbol,
                {
                    "status": "coverage_pending",
                    "reason": "missing_total_exceeds_backfill_budget",
                    "scan_mode": self._batch_scan_mode_for_symbol(symbol),
                    "prefetch_reason": reason,
                    "source": source.source_id,
                    "requested_ranges": [f"{start}:{end}" for start, end in requested_ranges],
                    "requested_range_count": int(len(requested_ranges)),
                    "missing_ranges": [f"{start}:{end}" for start, end in missing_ranges],
                    "missing_range_count": int(len(missing_ranges)),
                    "missing_total_ms": int(missing_total_ms),
                    "backfill_max_ms": int(max_backfill_ms),
                    "ws_rows": int(ws_rows_total),
                    "buffer_row_count_total": int(buffer_rows_total),
                    "read_status_counts": read_status_counts,
                    "connection_statuses": sorted(connection_statuses),
                    "backfill_ranges": [],
                    "backfilled_rows": 0,
                },
            )
            return
        request_start_ms = min(start for start, _end in requested_ranges)
        request_end_ms = max(end for _start, end in requested_ranges)
        self._cycle_aggtrade_requests += 1
        rows, backfill_ranges, fetched_rows_total, cache_missing_count = self._fetch_aggtrade_raw_ranges_cached(
            symbol,
            missing_ranges,
            fetch_window_start_ms=int(request_start_ms),
            fetch_window_end_ms=int(request_end_ms),
        )
        for missing_start_ms, missing_end_ms in missing_ranges:
            source.add_backfill_rows(
                symbol,
                _filter_aggtrade_rows_by_time(
                    rows,
                    start_timestamp_ms=int(missing_start_ms),
                    end_timestamp_ms=int(missing_end_ms),
                ),
                start_timestamp_ms=int(missing_start_ms),
                end_timestamp_ms=int(missing_end_ms),
            )
        self._cycle_aggtrade_gap_prefetch_backfill_ranges += int(len(backfill_ranges))
        self._cycle_aggtrade_gap_prefetch_rows += int(fetched_rows_total)
        self.artifacts.append_event(
            "aggtrade_rest_gap_prefetch",
            symbol,
            {
                "status": "backfilled",
                "reason": "coalesced_symbol_subminute_entry_debt",
                "scan_mode": self._batch_scan_mode_for_symbol(symbol),
                "prefetch_reason": reason,
                "source": source.source_id,
                "requested_ranges": [f"{start}:{end}" for start, end in requested_ranges],
                "requested_range_count": int(len(requested_ranges)),
                "missing_ranges": [f"{start}:{end}" for start, end in missing_ranges],
                "missing_range_count": int(len(missing_ranges)),
                "missing_total_ms": int(missing_total_ms),
                "backfill_max_ms": int(max_backfill_ms),
                "cache_missing_range_count": int(cache_missing_count),
                "network_backfill_range_count": int(len(backfill_ranges)),
                "backfill_ranges": backfill_ranges,
                "backfilled_rows": int(fetched_rows_total),
                "ws_rows": int(ws_rows_total),
                "buffer_row_count_total": int(buffer_rows_total),
                "read_status_counts": read_status_counts,
                "connection_statuses": sorted(connection_statuses),
            },
        )

    def _scan_batch(self, symbols: list[str]) -> list[LiveSignal]:
        for symbol in symbols:
            self._count_symbol_scan_mode(symbol)
        if self.config.scan_hot_timeframes_per_symbol:
            return self._scan_batch_by_symbol(symbols)
        now_ms = int(time.time() * 1000)
        for symbol in symbols:
            self._prefetch_symbol_subminute_entry_gap_debt(symbol, now_ms=now_ms, reason="batch_by_timeframe")
        return self._scan_batch_by_timeframe(symbols)

    def _scan_batch_by_symbol(self, symbols: list[str]) -> list[LiveSignal]:
        signals: list[LiveSignal] = []
        now_ms = int(time.time() * 1000)
        setup_cache: dict[tuple[str, str, int], pd.DataFrame] = {}
        entry_cache: dict[tuple[str, str, int, int], pd.DataFrame] = {}
        for symbol in symbols:
            self._prefetch_symbol_subminute_entry_gap_debt(symbol, now_ms=now_ms, reason="batch_by_symbol")
            started_at = time.monotonic()
            due_count = 0
            evaluated_count = 0
            setup_fetch_count = 0
            entry_fetch_count = 0
            signal_count = 0
            retryable_dependency_count = 0
            fetch_failures = 0
            entry_ws_coverage_pending = 0
            skipped_not_due = 0
            skipped_inactive_subminute = 0
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
                if self._should_defer_inactive_subminute_pair(symbol, entry_timeframe):
                    skipped_inactive_subminute += 1
                    self._cycle_deferred_inactive_subminute_pairs += 1
                    continue
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
                    except LiveWsAggTradeCoveragePending as exc:
                        entry_ws_coverage_pending += 1
                        self.artifacts.append_event(
                            "signal_entry_ws_aggtrade_pending",
                            symbol,
                            {
                                "levels_tf": levels_timeframe.value,
                                "entry_tf": entry_timeframe.value,
                                "reason": str(exc),
                                "ws_status": exc.status,
                                "ws_reason": exc.reason or "",
                                "start_timestamp_ms": exc.start_timestamp_ms,
                                "end_timestamp_ms": exc.end_timestamp_ms,
                                "missing_ranges": [f"{start}:{end}" for start, end in exc.missing_ranges],
                                "backfill_max_ms": int(self.config.live_ws_aggtrade_max_backfill_ms),
                            },
                        )
                        continue
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
                scan_result = self._build_forming_setup_signal(
                    symbol,
                    setup_cache[setup_key],
                    entry_cache[entry_key],
                    now_ms=now_ms,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    setup_start_ts=setup_start_ts,
                    latest_closed_entry_ts=latest_closed_entry_ts,
                    previous_scan_closed_ts=self._previous_signal_scan_closed_at(symbol, levels_timeframe, entry_timeframe),
                )
                evaluated_count += 1
                if scan_result.retryable_dependency:
                    retryable_dependency_count += 1
                else:
                    self._mark_signal_scan_closed_at(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        closed_timestamp_ms=latest_closed_entry_ts,
                    )
                if scan_result.signal is not None:
                    signals.append(scan_result.signal)
                    signal_count += 1
            self.artifacts.append_event(
                "signal_symbol_scan_summary",
                symbol,
                {
                    "mode": "timeframes_per_symbol",
                    "scan_mode": self._batch_scan_mode_for_symbol(symbol),
                    "subminute_entry_scan_allowed": bool(self._subminute_entry_scan_allowed(symbol)),
                    "timeframe_pairs": [f"{levels.value}/{entry.value}" for levels, entry in self.config.timeframe_pairs],
                    "due_timeframe_count": due_count,
                    "evaluated_timeframe_count": evaluated_count,
                    "skipped_not_due_count": skipped_not_due,
                    "skipped_inactive_subminute_count": skipped_inactive_subminute,
                    "setup_fetch_count": setup_fetch_count,
                    "entry_fetch_count": entry_fetch_count,
                    "fetch_failure_count": fetch_failures,
                    "entry_ws_aggtrade_pending_count": entry_ws_coverage_pending,
                    "retryable_dependency_count": retryable_dependency_count,
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
                if self._should_defer_inactive_subminute_pair(symbol, entry_timeframe):
                    self._cycle_deferred_inactive_subminute_pairs += 1
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
                except LiveWsAggTradeCoveragePending as exc:
                    self.artifacts.append_event(
                        "signal_entry_ws_aggtrade_pending",
                        symbol,
                        {
                            "levels_tf": levels_timeframe.value,
                            "entry_tf": entry_timeframe.value,
                            "reason": str(exc),
                            "ws_status": exc.status,
                            "ws_reason": exc.reason or "",
                            "start_timestamp_ms": exc.start_timestamp_ms,
                            "end_timestamp_ms": exc.end_timestamp_ms,
                            "missing_ranges": [f"{start}:{end}" for start, end in exc.missing_ranges],
                            "backfill_max_ms": int(self.config.live_ws_aggtrade_max_backfill_ms),
                        },
                    )
                    continue
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
                scan_result = self._build_forming_setup_signal(
                    symbol,
                    setup_frame,
                    entry_frame,
                    now_ms=now_ms,
                    levels_timeframe=levels_timeframe,
                    entry_timeframe=entry_timeframe,
                    setup_start_ts=setup_start_ts,
                    latest_closed_entry_ts=latest_closed_entry_ts,
                    previous_scan_closed_ts=self._previous_signal_scan_closed_at(symbol, levels_timeframe, entry_timeframe),
                )
                if not scan_result.retryable_dependency:
                    self._mark_signal_scan_closed_at(
                        symbol,
                        levels_timeframe,
                        entry_timeframe,
                        closed_timestamp_ms=latest_closed_entry_ts,
                    )
                if scan_result.signal is not None:
                    signals.append(scan_result.signal)
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
        previous_scan_closed_ts: int | None = None,
    ) -> LiveSignalScanResult:
        def no_signal(
            *,
            decision_timestamp_ms: int | None = None,
            retryable_dependency: bool = False,
            retry_reason: str = "",
            retryable_reasons: tuple[str, ...] = (),
        ) -> LiveSignalScanResult:
            return LiveSignalScanResult(
                signal=None,
                decision_timestamp_ms=decision_timestamp_ms,
                retryable_dependency=retryable_dependency,
                retry_reason=retry_reason,
                retryable_reasons=retryable_reasons,
            )

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
            return no_signal()
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
            return no_signal()
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
            return no_signal()
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
            return no_signal()
        setup_elapsed_fraction = min(1.0, len(entry_segment) * entry_timeframe_ms / levels_timeframe_ms)
        forming_setup = _aggregate_frame_to_candle(entry_segment, timestamp_ms=int(setup_start_ts))
        if forming_setup is None:
            return no_signal()
        decision_ts = int(entry_segment.iloc[-1]["timestamp"])
        key = (symbol, levels_timeframe.value, entry_timeframe.value, decision_ts)
        with self._state_lock:
            if key in self._seen_decisions:
                return no_signal(decision_timestamp_ms=decision_ts)
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
            return no_signal(decision_timestamp_ms=decision_ts)
        category_rejections: list[dict[str, object]] = []
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
            emit_diagnostics=True,
            category_rejections_out=category_rejections,
        )
        if signal is None:
            retryable_reasons = _retryable_category_reasons(category_rejections)
            if retryable_reasons:
                self.artifacts.append_event(
                    "signal_scan_retryable_dependency_blocked",
                    symbol,
                    {
                        "levels_tf": levels_timeframe.value,
                        "entry_tf": entry_timeframe.value,
                        "decision_timestamp_ms": decision_ts,
                        "retryable_reasons": list(retryable_reasons),
                        "retry_policy": "do_not_consume_decision_until_stale_or_final_reject",
                        "category_contract": LIVE_CATEGORY_CONTRACT,
                    },
                )
                return no_signal(
                    decision_timestamp_ms=decision_ts,
                    retryable_dependency=True,
                    retry_reason="category_dependency_unavailable",
                    retryable_reasons=retryable_reasons,
                )
            with self._state_lock:
                self._seen_decisions.add(key)
            return no_signal(decision_timestamp_ms=decision_ts)
        gap = _live_scan_gap_details(
            decision_timestamp_ms=decision_ts,
            previous_scan_closed_timestamp_ms=previous_scan_closed_ts,
            entry_timeframe=entry_timeframe,
        )
        signal.previous_live_scan_closed_timestamp_ms = gap["previous_live_scan_closed_timestamp_ms"]
        signal.first_unscanned_decision_timestamp_ms = gap["first_unscanned_decision_timestamp_ms"]
        signal.live_scan_gap_ltf_candles = int(gap["live_scan_gap_ltf_candles"])
        if signal.live_scan_gap_ltf_candles > 0:
            self._start_missed_entry_replay_probe(
                symbol=symbol,
                setup_frame=setup_frame,
                entry_frame=entry_frame,
                now_ms=now_ms,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                current_signal=signal,
            )
        return LiveSignalScanResult(signal=signal, decision_timestamp_ms=decision_ts)

    def _start_missed_entry_replay_probe(
        self,
        *,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        current_signal: LiveSignal,
    ) -> None:
        threading.Thread(
            target=self._run_missed_entry_replay_probe,
            kwargs={
                "symbol": symbol,
                "setup_frame": setup_frame.copy(deep=False),
                "entry_frame": entry_frame.copy(deep=False),
                "now_ms": int(now_ms),
                "levels_timeframe": levels_timeframe,
                "entry_timeframe": entry_timeframe,
                "current_signal": current_signal,
            },
            name=f"missed-entry-probe-{_compact_symbol(symbol)}-{entry_timeframe.value}",
            daemon=True,
        ).start()

    def _run_missed_entry_replay_probe(
        self,
        *,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        current_signal: LiveSignal,
    ) -> None:
        try:
            result = self._probe_first_missed_entry_signal(
                symbol=symbol,
                setup_frame=setup_frame,
                entry_frame=entry_frame,
                now_ms=now_ms,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                current_signal=current_signal,
            )
            self.artifacts.append_event("missed_entry_replay_probe", symbol, result)
        except Exception as exc:
            self.artifacts.append_event(
                "missed_entry_replay_probe_failed",
                symbol,
                {
                    "levels_tf": levels_timeframe.value,
                    "entry_tf": entry_timeframe.value,
                    "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:1000],
                },
            )

    def _probe_first_missed_entry_signal(
        self,
        *,
        symbol: str,
        setup_frame: pd.DataFrame,
        entry_frame: pd.DataFrame,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
        current_signal: LiveSignal,
    ) -> dict[str, object]:
        entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
        levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
        previous_ts = current_signal.previous_live_scan_closed_timestamp_ms
        if previous_ts is None or entry_timeframe_ms <= 0 or levels_timeframe_ms <= 0:
            return {
                "status": "no_previous_scan",
                "levels_tf": levels_timeframe.value,
                "entry_tf": entry_timeframe.value,
                "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
            }
        first_candidate_ts = int(previous_ts) + entry_timeframe_ms
        last_candidate_ts = int(current_signal.decision_timestamp_ms) - entry_timeframe_ms
        if first_candidate_ts > last_candidate_ts:
            return {
                "status": "no_missed_closed_candles",
                "levels_tf": levels_timeframe.value,
                "entry_tf": entry_timeframe.value,
                "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
                "previous_live_scan_closed_timestamp_ms": int(previous_ts),
            }
        max_probe = max(1, int(self.config.signal_scan_backfill_candles))
        all_candidate_timestamps = list(range(first_candidate_ts, last_candidate_ts + 1, entry_timeframe_ms))
        probe_truncated = len(all_candidate_timestamps) > max_probe
        candidate_timestamps = all_candidate_timestamps[:max_probe]
        setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        for candidate_ts in candidate_timestamps:
            setup_start_ts = (int(candidate_ts) // levels_timeframe_ms) * levels_timeframe_ms
            setup_history = setup_frame.loc[setup_frame["timestamp"].astype(int) < setup_start_ts].tail(self.config.baseline_candles).copy()
            if len(setup_history) < self.config.baseline_candles:
                continue
            entry_segment = entry_frame.loc[
                (entry_frame["timestamp"].astype(int) >= setup_start_ts)
                & (entry_frame["timestamp"].astype(int) <= int(candidate_ts))
            ].copy()
            if entry_segment.empty:
                continue
            seed_close = float(setup_history.iloc[-1]["close"])
            entry_segment = _fill_missing_ohlcv_buckets(
                entry_segment,
                start_timestamp_ms=setup_start_ts,
                end_timestamp_ms=int(candidate_ts),
                timeframe_ms=entry_timeframe_ms,
                seed_close=seed_close,
            )
            if len(entry_segment) < self.config.confirmation_candles:
                continue
            forming_setup = _aggregate_frame_to_candle(entry_segment, timestamp_ms=setup_start_ts)
            if forming_setup is None:
                continue
            setup_elapsed_fraction = min(1.0, len(entry_segment) * entry_timeframe_ms / levels_timeframe_ms)
            signal = self._build_signal_from_components(
                symbol=symbol,
                baseline=setup_history,
                setup_row=forming_setup,
                entry_segment=entry_segment,
                now_ms=now_ms,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                setup_source="forming_htf_from_entry_tf_replay_probe",
                setup_elapsed_fraction=setup_elapsed_fraction,
                setup_closed_entry_candles=len(entry_segment),
                emit_diagnostics=False,
            )
            if signal is None:
                continue
            missed_lag_ltf = int((int(current_signal.decision_timestamp_ms) - int(signal.decision_timestamp_ms)) // entry_timeframe_ms)
            return {
                "status": "first_prior_signal_found",
                "levels_tf": levels_timeframe.value,
                "entry_tf": entry_timeframe.value,
                "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
                "previous_live_scan_closed_timestamp_ms": int(previous_ts),
                "first_unscanned_decision_timestamp_ms": int(first_candidate_ts),
                "first_prior_signal_decision_timestamp_ms": int(signal.decision_timestamp_ms),
                "missed_signal_lag_ltf_candles": missed_lag_ltf,
                "missed_signal_lag_ms": missed_lag_ltf * entry_timeframe_ms,
                "candidate_decision_count_total": len(all_candidate_timestamps),
                "probed_decision_count": len(candidate_timestamps),
                "probe_max_candles": max_probe,
                "probe_truncated": bool(probe_truncated),
                "unprobed_newer_decision_count": max(0, len(all_candidate_timestamps) - len(candidate_timestamps)),
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "signal_entry_price": _finite_or_none(signal.entry_price),
                "signal_stop_price": _finite_or_none(signal.stop_price),
                "signal_tp1_price": _finite_or_none(signal.tp1_price),
            }
        status = "no_prior_signal_found_in_probed_prefix" if probe_truncated else "no_prior_signal_found"
        return {
            "status": status,
            "levels_tf": levels_timeframe.value,
            "entry_tf": entry_timeframe.value,
            "current_decision_timestamp_ms": int(current_signal.decision_timestamp_ms),
            "previous_live_scan_closed_timestamp_ms": int(previous_ts),
            "first_unscanned_decision_timestamp_ms": int(first_candidate_ts),
            "last_probed_decision_timestamp_ms": int(candidate_timestamps[-1]),
            "candidate_decision_count_total": len(all_candidate_timestamps),
            "probed_decision_count": len(candidate_timestamps),
            "probe_max_candles": max_probe,
            "probe_truncated": bool(probe_truncated),
            "unprobed_newer_decision_count": max(0, len(all_candidate_timestamps) - len(candidate_timestamps)),
        }

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
        emit_diagnostics: bool = True,
        category_rejections_out: list[dict[str, object]] | None = None,
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
        raw_trade_ratio_for_return = _safe_divide(start_trades, baseline_trades)
        start_trade_ratio_per_abs_return = _safe_divide(raw_trade_ratio_for_return, abs_start_ret)
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
            if emit_diagnostics:
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
            if emit_diagnostics:
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
        if emit_diagnostics and 0 <= now_ms - decision_available_ms <= self.config.max_signal_age_ms:
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
        flow_hold_count = int(
            (
                pd.to_numeric(entry_segment["quote_volume"], errors="coerce").ge(max(0.35 * start_quote, 3.0 * baseline_quote))
                & pd.to_numeric(entry_segment["number_of_trades"], errors="coerce").ge(max(0.35 * start_trades, 3.0 * baseline_trades))
            ).sum()
        )
        start_range = start_high - start_low
        start_lower_wick_to_range = _safe_divide(min(start_open, start_close) - start_low, start_range)
        start_upper_wick_to_range = _safe_divide(start_high - max(start_open, start_close), start_range)
        setup_with_current = pd.concat([baseline, pd.DataFrame([setup_row.to_dict()])], ignore_index=True)
        ema20 = setup_with_current["close"].astype(float).ewm(span=20, adjust=False).mean()
        decision_ema20 = float(ema20.iloc[-1])
        previous_stop = segment_low - self.config.stop_buffer_range_fraction * impulse_range
        stop_price = max(previous_stop, decision_ema20)
        entry_price = decision_close
        risk = entry_price - stop_price
        initial_risk_pct = _safe_divide(risk, entry_price)
        if not math.isfinite(risk) or risk <= 0.0:
            if emit_diagnostics:
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
            if emit_diagnostics:
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
        mark_basis_loaded = False
        mark_basis: LiveMarkBasisResult | None = None
        next_taker_share_loaded = False
        next_taker_share = float("nan")
        valid_taker_share_count = 0
        total_taker_share_rows = 0
        start_taker_share_delta_loaded = False
        start_taker_share_delta = float("nan")
        prior_fast_fade_loaded = False
        prior_fast_fade_result: dict[str, object] | None = None

        def record_category_reject(
            category: LivePumpCategory,
            symbol: str,
            reason: str,
            details: dict[str, object],
        ) -> dict[str, object]:
            row = self._record_category_reject(
                category,
                symbol,
                reason,
                details,
                emit=emit_diagnostics,
            )
            if category_rejections_out is not None:
                category_rejections_out.append(row)
            return row

        for category in self._categories_for_timeframe(levels_timeframe, entry_timeframe):
            max_prior_fast_fade = _category_value(category, self.config, "max_prior_fast_fade_count_72h")
            if max_prior_fast_fade is not None:
                if not prior_fast_fade_loaded:
                    prior_fast_fade_result = self._live_prior_fast_fade_72h(
                        symbol,
                        decision_timestamp_ms=int(decision["timestamp"]),
                        levels_timeframe=levels_timeframe,
                        entry_timeframe=entry_timeframe,
                    )
                    prior_fast_fade_loaded = True
                assert prior_fast_fade_result is not None
                prior_fast_fade_count = prior_fast_fade_result.get("prior_fast_fade_count_72h")
                prior_fast_fade_details = {
                    **prior_fast_fade_result,
                    "max_prior_fast_fade_count_72h": int(max_prior_fast_fade),
                    "decision_timestamp_ms": int(decision["timestamp"]),
                }
                if prior_fast_fade_result.get("status") != "ok" or prior_fast_fade_count is None:
                    category_rejections.append(record_category_reject(category, symbol, "reject_prior_fast_fade_filter_unavailable", prior_fast_fade_details))
                    continue
                if int(prior_fast_fade_count) > int(max_prior_fast_fade):
                    category_rejections.append(record_category_reject(category, symbol, "reject_prior_fast_fade_72h", prior_fast_fade_details))
                    continue

            min_mark_basis = _category_value(category, self.config, "min_mark_close_vs_decision_close_basis")
            if min_mark_basis is not None:
                if not mark_basis_loaded:
                    mark_basis = self._fetch_live_mark_basis(
                        symbol,
                        decision_timestamp_ms=int(decision["timestamp"]),
                        decision_close=decision_close,
                    )
                    mark_basis_loaded = True
                basis_value = mark_basis.value if mark_basis is not None else None
                if basis_value is None or not math.isfinite(basis_value) or basis_value < min_mark_basis:
                    category_rejections.append(
                        record_category_reject(
                            category,
                            symbol,
                            "reject_mark_basis_below_min",
                            {
                                "mark_close_vs_decision_close_basis": _finite_or_none(basis_value),
                                "mark_basis_status": mark_basis.reason if mark_basis is not None else "not_loaded",
                                "mark_timestamp_ms": mark_basis.timestamp_ms if mark_basis is not None and mark_basis.timestamp_ms is not None else "",
                                "mark_age_ms": mark_basis.age_ms if mark_basis is not None and mark_basis.age_ms is not None else "",
                                "min": min_mark_basis,
                                "decision_timestamp_ms": int(decision["timestamp"]),
                            },
                        )
                    )
                    continue
            max_start_quote_ratio = _category_value(category, self.config, "max_start_quote_ratio")
            if max_start_quote_ratio is not None and quote_ratio > max_start_quote_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_exhausted_quote_ratio", {"quote_ratio": quote_ratio, "max": max_start_quote_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_start_trade_ratio = _category_value(category, self.config, "max_start_trade_ratio")
            if max_start_trade_ratio is not None and trade_ratio > max_start_trade_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_exhausted_trade_ratio", {"trade_ratio": trade_ratio, "max": max_start_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_avg_trade_ratio = _category_value(category, self.config, "max_start_avg_trade_quote_size_ratio")
            if max_avg_trade_ratio is not None and not math.isfinite(start_avg_trade_ratio):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_avg_trade_quote_size_ratio", {"ratio": _finite_or_none(start_avg_trade_ratio), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_avg_trade_ratio is not None and start_avg_trade_ratio > max_avg_trade_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_large_print_signature", {"ratio": start_avg_trade_ratio, "max": max_avg_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_quote_per_return = _category_value(category, self.config, "max_start_quote_ratio_per_abs_return")
            if max_quote_per_return is not None and not math.isfinite(start_quote_ratio_per_abs_return):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_quote_ratio_per_abs_return", {"ratio": _finite_or_none(start_quote_ratio_per_abs_return), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_quote_per_return is not None and start_quote_ratio_per_abs_return > max_quote_per_return:
                category_rejections.append(record_category_reject(category, symbol, "reject_poor_effort_per_return", {"ratio": start_quote_ratio_per_abs_return, "max": max_quote_per_return, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_trade_per_return = _category_value(category, self.config, "max_start_trade_ratio_per_abs_return")
            if max_trade_per_return is not None and not math.isfinite(start_trade_ratio_per_abs_return):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_trade_ratio_per_abs_return", {"ratio": _finite_or_none(start_trade_ratio_per_abs_return), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_trade_per_return is not None and start_trade_ratio_per_abs_return > max_trade_per_return:
                category_rejections.append(record_category_reject(category, symbol, "reject_poor_trade_effort_per_return", {"ratio": start_trade_ratio_per_abs_return, "max": max_trade_per_return, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_range_ratio = _category_value(category, self.config, "max_start_range_pct_ratio_to_baseline")
            if max_range_ratio is not None and not math.isfinite(start_range_pct_ratio):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_range_expansion_ratio", {"ratio": _finite_or_none(start_range_pct_ratio), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_range_ratio is not None and start_range_pct_ratio > max_range_ratio:
                category_rejections.append(record_category_reject(category, symbol, "reject_extreme_range_expansion", {"ratio": start_range_pct_ratio, "max": max_range_ratio, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_prior_whipsaw = self.config.max_prior_up_down_whipsaw_to_impulse_range
            if max_prior_whipsaw is not None and not math.isfinite(prior_whipsaw):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_prior_whipsaw", {"prior_up_down_whipsaw_to_impulse_range": _finite_or_none(prior_whipsaw), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if max_prior_whipsaw is not None and prior_whipsaw > max_prior_whipsaw:
                category_rejections.append(record_category_reject(category, symbol, "reject_prior_up_down_whipsaw", {"prior_up_down_whipsaw_to_impulse_range": prior_whipsaw, "max": max_prior_whipsaw, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_flow_hold_count = _category_value(category, self.config, "min_flow_hold_count")
            if min_flow_hold_count is not None and flow_hold_count < int(min_flow_hold_count):
                category_rejections.append(record_category_reject(category, symbol, "reject_low_flow_hold_count", {"flow_hold_count": flow_hold_count, "min": int(min_flow_hold_count), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_lower_wick = _category_value(category, self.config, "min_start_lower_wick_to_range")
            if min_lower_wick is not None:
                if not math.isfinite(start_lower_wick_to_range) or start_lower_wick_to_range <= min_lower_wick:
                    category_rejections.append(record_category_reject(category, symbol, "reject_low_start_lower_wick", {"start_lower_wick_to_range": _finite_or_none(start_lower_wick_to_range), "min": min_lower_wick, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            max_upper_wick = _category_value(category, self.config, "max_start_upper_wick_to_range")
            if max_upper_wick is not None:
                if not math.isfinite(start_upper_wick_to_range) or start_upper_wick_to_range > max_upper_wick:
                    category_rejections.append(record_category_reject(category, symbol, "reject_high_start_upper_wick", {"start_upper_wick_to_range": _finite_or_none(start_upper_wick_to_range), "max": max_upper_wick, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            if not math.isfinite(price_retention):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_price_retention", {"price_retention": _finite_or_none(price_retention), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if price_retention < self.config.min_price_retention:
                category_rejections.append(record_category_reject(category, symbol, "reject_low_price_retention", {"price_retention": price_retention, "min": self.config.min_price_retention, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            max_price_retention = _category_value(category, self.config, "max_price_retention")
            if max_price_retention is not None and price_retention > max_price_retention:
                category_rejections.append(record_category_reject(category, symbol, "reject_overextended_retention", {"price_retention": price_retention, "max": max_price_retention, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_next_taker_share = _category_value(category, self.config, "min_next_taker_buy_quote_share")
            if min_next_taker_share is not None:
                if "taker_buy_quote_volume" not in entry_segment.columns:
                    category_rejections.append(record_category_reject(category, symbol, "reject_missing_taker_buy_share", {"required": min_next_taker_share, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
                if not next_taker_share_loaded:
                    taker_quote = pd.to_numeric(entry_segment["taker_buy_quote_volume"], errors="coerce")
                    quote_volume = pd.to_numeric(entry_segment["quote_volume"], errors="coerce")
                    valid_taker_share_rows = taker_quote.notna() & quote_volume.notna() & quote_volume.gt(0.0)
                    valid_taker_share_count = int(valid_taker_share_rows.sum())
                    total_taker_share_rows = int(len(entry_segment))
                    if valid_taker_share_count <= 0:
                        next_taker_share = float("nan")
                    else:
                        next_taker_share = float((taker_quote[valid_taker_share_rows] / quote_volume[valid_taker_share_rows]).mean())
                    next_taker_share_loaded = True
                if not math.isfinite(next_taker_share):
                    category_rejections.append(record_category_reject(category, symbol, "reject_invalid_taker_buy_share", {"share": _finite_or_none(next_taker_share), "valid_taker_share_rows": valid_taker_share_count, "total_taker_share_rows": total_taker_share_rows, "taker_share_contract": "mean_over_valid_quote_volume_rows", "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
                if next_taker_share < min_next_taker_share:
                    category_rejections.append(record_category_reject(category, symbol, "reject_weak_next_taker_buy_share", {"share": next_taker_share, "min": min_next_taker_share, "valid_taker_share_rows": valid_taker_share_count, "total_taker_share_rows": total_taker_share_rows, "taker_share_contract": "mean_over_valid_quote_volume_rows", "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            max_start_taker_delta = _category_value(category, self.config, "max_start_taker_buy_quote_share_delta")
            if max_start_taker_delta is not None:
                if "taker_buy_quote_volume" not in entry_segment.columns or "taker_buy_quote_volume" not in baseline.columns:
                    category_rejections.append(record_category_reject(category, symbol, "reject_missing_start_taker_buy_delta", {"required_max": max_start_taker_delta, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
                if not start_taker_share_delta_loaded:
                    start_quote_volume = float(pd.to_numeric(pd.Series([entry_segment.iloc[0]["quote_volume"]]), errors="coerce").iloc[0])
                    start_taker_quote = float(pd.to_numeric(pd.Series([entry_segment.iloc[0]["taker_buy_quote_volume"]]), errors="coerce").iloc[0])
                    baseline_quote_volume = pd.to_numeric(baseline["quote_volume"], errors="coerce")
                    baseline_taker_quote = pd.to_numeric(baseline["taker_buy_quote_volume"], errors="coerce")
                    baseline_share = float((baseline_taker_quote / baseline_quote_volume.replace(0.0, pd.NA)).median())
                    start_share = _safe_divide(start_taker_quote, start_quote_volume)
                    start_taker_share_delta = start_share - baseline_share
                    start_taker_share_delta_loaded = True
                if not math.isfinite(start_taker_share_delta):
                    category_rejections.append(record_category_reject(category, symbol, "reject_invalid_start_taker_buy_delta", {"delta": _finite_or_none(start_taker_share_delta), "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
                if start_taker_share_delta > max_start_taker_delta:
                    category_rejections.append(record_category_reject(category, symbol, "reject_start_taker_buy_delta_above_max", {"delta": start_taker_share_delta, "max": max_start_taker_delta, "decision_timestamp_ms": int(decision["timestamp"])}))
                    continue
            if not math.isfinite(verticality_score):
                category_rejections.append(record_category_reject(category, symbol, "reject_invalid_verticality", {"verticality_score": _finite_or_none(verticality_score), "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if verticality_score < self.config.min_verticality_score:
                category_rejections.append(record_category_reject(category, symbol, "reject_low_verticality", {"verticality_score": verticality_score, "min": self.config.min_verticality_score, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            if hold_count < self.config.min_hold_count:
                category_rejections.append(record_category_reject(category, symbol, "reject_low_hold_count", {"hold_count": hold_count, "min": self.config.min_hold_count, "decision_timestamp_ms": int(decision["timestamp"])}))
                continue
            min_oi_change = _category_value(category, self.config, "min_oi_change_pct_3x5m")
            if min_oi_change is not None:
                if not oi_change_loaded:
                    oi_result = self._fetch_live_oi_change(
                        symbol,
                        decision_timestamp_ms=int(decision["timestamp"]),
                    )
                    oi_change = oi_result.value
                    oi_change_reason = oi_result.reason
                    oi_change_loaded = True
                if oi_change is None or not math.isfinite(oi_change) or oi_change <= min_oi_change:
                    category_rejections.append(record_category_reject(category, symbol, "reject_oi", {"oi_change_pct_3x5m": _finite_or_none(oi_change), "oi_status": oi_change_reason or "below_threshold", "required_gt": min_oi_change, "decision_timestamp_ms": int(decision["timestamp"])}))
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
                previous_live_scan_closed_timestamp_ms=None,
                first_unscanned_decision_timestamp_ms=None,
                live_scan_gap_ltf_candles=0,
                category_rejections=list(category_rejections),
                strengths=strengths,
                weaknesses=weaknesses,
            )
            if emit_diagnostics:
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
                        "category_contract": LIVE_CATEGORY_CONTRACT,
                        "category_priority": category.priority,
                        "prior_fast_fade_filter_status": prior_fast_fade_result.get("status") if prior_fast_fade_result is not None else "not_required",
                        "prior_fast_fade_filter_reason": prior_fast_fade_result.get("reason") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_count_72h": prior_fast_fade_result.get("prior_fast_fade_count_72h") if prior_fast_fade_result is not None else "",
                        "prior_spike_count_72h": prior_fast_fade_result.get("prior_spike_count_72h") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_filter_coverage_policy": prior_fast_fade_result.get("coverage_policy") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_filter_effective_cache_end_timestamp_ms": prior_fast_fade_result.get("effective_cache_end_timestamp_ms") if prior_fast_fade_result is not None else "",
                        "prior_fast_fade_filter_ignored_tail_ms": prior_fast_fade_result.get("ignored_tail_ms") if prior_fast_fade_result is not None else "",
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
                        "flow_hold_count_next_n_candles": flow_hold_count,
                        "start_lower_wick_to_range": _finite_or_none(start_lower_wick_to_range),
                        "start_upper_wick_to_range": _finite_or_none(start_upper_wick_to_range),
                        "base_tp1_price": _finite_or_none(base_tp1_price),
                        "tp1_price": _finite_or_none(tp1_price),
                        "tp1_round_step": _finite_or_none(tp1_round_step),
                        "mark_close_vs_decision_close_basis": _finite_or_none(
                            mark_basis.value if mark_basis is not None else None
                        ),
                        "mark_basis_status": mark_basis.reason if mark_basis is not None else "",
                        "mark_timestamp_ms": mark_basis.timestamp_ms
                        if mark_basis is not None and mark_basis.timestamp_ms is not None
                        else "",
                        "mark_age_ms": mark_basis.age_ms
                        if mark_basis is not None and mark_basis.age_ms is not None
                        else "",
                        "oi_change_pct_3x5m": _finite_or_none(oi_change),
                        "oi_status": oi_change_reason or ("ok" if oi_change is not None else ""),
                    },
                )
            if emit_diagnostics and category_rejections:
                rejected_ids = ",".join(str(row.get("category_id")) for row in category_rejections)
                self.logger(
                    f"{_compact_symbol(symbol)} {levels_timeframe.value}/{entry_timeframe.value} · "
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
        emit: bool = True,
    ) -> dict[str, object]:
        detail_payload = dict(details)
        detail_reason = detail_payload.pop("reason", None)
        payload: dict[str, object] = {
            "category_id": category.category_id,
            "category_label": category.label,
            "category_priority": category.priority,
            "reason": reason,
            "category_reject_reason": reason,
            **detail_payload,
        }
        if detail_reason is not None:
            payload.setdefault("coverage_reason", detail_reason)
            payload["detail_reason"] = detail_reason
        if emit:
            self.artifacts.append_event("category_rejected", symbol, payload)
        return payload

    def _fetch_live_oi_change(self, symbol: str, *, decision_timestamp_ms: int) -> LiveOiChangeResult:
        end_ms = int(decision_timestamp_ms)
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
        if latest_ts < end_ms - self.config.oi_fresh_ms:
            return LiveOiChangeResult(None, "oi_stale")
        current = float(frame.iloc[-1]["open_interest"])
        previous = float(frame.iloc[-4]["open_interest"])
        if not math.isfinite(current) or not math.isfinite(previous) or previous <= 0.0:
            return LiveOiChangeResult(None, "oi_invalid_values")
        value = _safe_divide(current - previous, previous)
        if not math.isfinite(value):
            return LiveOiChangeResult(None, "oi_invalid_change")
        return LiveOiChangeResult(value)

    def _fetch_live_mark_basis(
        self,
        symbol: str,
        *,
        decision_timestamp_ms: int,
        decision_close: float,
    ) -> LiveMarkBasisResult:
        if not math.isfinite(decision_close) or decision_close <= 0.0:
            return LiveMarkBasisResult(None, "invalid_decision_close")
        fetcher = getattr(self.exchange, "fetch_binance_derivatives_context", None)
        if not callable(fetcher):
            return LiveMarkBasisResult(None, "mark_context_fetch_unavailable")
        end_ms = int(decision_timestamp_ms)
        start_ms = end_ms - 20 * 60_000
        try:
            frame = fetcher(
                symbol=symbol,
                source="mark",
                start_timestamp_ms=start_ms,
                end_timestamp_ms=end_ms,
                period="5m",
                limit=20,
            )
        except Exception as exc:
            return LiveMarkBasisResult(None, f"mark_context_error:{type(exc).__name__}")
        if frame.empty:
            return LiveMarkBasisResult(None, "mark_context_empty")
        if "timestamp" not in frame.columns or "close" not in frame.columns:
            return LiveMarkBasisResult(None, "mark_context_missing_columns")
        prepared = frame.sort_values("timestamp").dropna(subset=["timestamp", "close"]).reset_index(drop=True)
        prepared = prepared.loc[pd.to_numeric(prepared["timestamp"], errors="coerce").le(end_ms)]
        if prepared.empty:
            return LiveMarkBasisResult(None, "no_mark_before_decision")
        row = prepared.iloc[-1]
        mark_ts = int(row["timestamp"])
        mark_close = float(row["close"])
        age_ms = int(end_ms - mark_ts)
        if age_ms > 5 * 60_000:
            return LiveMarkBasisResult(None, "mark_context_stale", timestamp_ms=mark_ts, age_ms=age_ms)
        basis = _safe_divide(mark_close - decision_close, decision_close)
        if not math.isfinite(basis):
            return LiveMarkBasisResult(None, "invalid_mark_basis", timestamp_ms=mark_ts, age_ms=age_ms)
        return LiveMarkBasisResult(basis, "ok", timestamp_ms=mark_ts, age_ms=age_ms)

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
            self._forget_signal_scan_closed_at(signal, reason="entry_waiting_for_free_slot")
            self.artifacts.append_event(
                "reject_max_positions",
                signal.symbol,
                {
                    "max": self.config.max_open_positions,
                    "levels_tf": signal.levels_timeframe.value,
                    "entry_tf": signal.entry_timeframe.value,
                    "decision_timestamp_ms": signal.decision_timestamp_ms,
                    "retry_until_stale": True,
                },
            )
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
            entry_client_order_id = _live_client_order_id(
                "entry",
                signal.symbol,
                signal.levels_timeframe.value,
                signal.entry_timeframe.value,
                signal.decision_timestamp_ms,
                entry_order_submitted_at_ms,
            )
            fill = self.exchange.create_market_order_with_fill(
                signal.symbol,
                "buy",
                amount_requested,
                reduce_only=False,
                client_order_id=entry_client_order_id,
            )
            self._track_order_reconcile_symbol(signal.symbol, reason="entry_order_filled")
            post_position_amount = float(self.exchange.fetch_symbol_position_amount(signal.symbol))
            position_delta_amount = post_position_amount - pre_position_amount
            if not math.isfinite(post_position_amount) or not math.isfinite(position_delta_amount):
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=fill.filled_amount,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                    reason="invalid_post_entry_position_amount",
                )
                raise LiveDataIntegrityError(
                    f"invalid post-entry position amount: symbol={signal.symbol} pre={pre_position_amount} post={post_position_amount}"
                )
            if position_delta_amount <= 0.0:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=fill.filled_amount,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                    reason="entry_fill_without_position_delta",
                )
                raise LiveDataIntegrityError(
                    f"entry order filled but exchange position did not increase: symbol={signal.symbol} order_id={fill.order_id} "
                    f"pre={pre_position_amount} post={post_position_amount}"
                )
            fill_position_slippage = abs(position_delta_amount - fill.filled_amount) / max(fill.filled_amount, 1e-12)
            if fill_position_slippage > self.config.max_position_amount_slippage_ratio:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                    reason="entry_fill_position_amount_mismatch",
                )
                raise LiveDataIntegrityError(
                    f"entry fill/position amount mismatch: symbol={signal.symbol} order_id={fill.order_id} "
                    f"filled={fill.filled_amount} delta={position_delta_amount}"
                )
            actual_entry_price = float(fill.average_price)
            actual_stop_price = float(signal.stop_price)
            actual_initial_risk = actual_entry_price - actual_stop_price
            actual_initial_risk_pct = _safe_divide(actual_initial_risk, actual_entry_price)
            if not math.isfinite(actual_initial_risk) or actual_initial_risk <= 0.0:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                    reason="invalid_actual_initial_risk_after_fill",
                )
                raise LiveDataIntegrityError(
                    f"invalid actual initial risk after fill: symbol={signal.symbol} entry={actual_entry_price} stop={actual_stop_price}"
                )
            if not math.isfinite(actual_initial_risk_pct) or actual_initial_risk_pct > self.config.max_initial_risk_pct:
                self._close_unprotected_entry_exposure(
                    signal.symbol,
                    amount=position_delta_amount,
                    position_id=f"entry_unresolved_{signal.decision_timestamp_ms}_{fill.order_id}",
                    reason="actual_initial_risk_too_wide_after_fill",
                )
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
            entry_fill_lag = _entry_lag_details(signal, observed_timestamp_ms=int(fill.timestamp_ms))
            entry_submit_lag = _entry_lag_details(signal, observed_timestamp_ms=entry_order_submitted_at_ms)
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
                first_executable_entry_timestamp_ms=int(entry_fill_lag["first_executable_entry_timestamp_ms"]),
                entry_lag_ms=int(entry_fill_lag["entry_lag_ms"]),
                entry_lag_ltf_candles=int(entry_fill_lag["entry_lag_ltf_candles"]),
                entered_late_vs_first_executable=bool(entry_fill_lag["entered_late_vs_first_executable"]),
                entry_order_submit_lag_ms=int(entry_submit_lag["entry_lag_ms"]),
                entry_order_submit_lag_ltf_candles=int(entry_submit_lag["entry_lag_ltf_candles"]),
                previous_live_scan_closed_timestamp_ms=signal.previous_live_scan_closed_timestamp_ms,
                first_unscanned_decision_timestamp_ms=signal.first_unscanned_decision_timestamp_ms,
                live_scan_gap_ltf_candles=signal.live_scan_gap_ltf_candles,
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
                "category_contract": LIVE_CATEGORY_CONTRACT,
                "category_priority": signal.category_priority,
                "signal_entry_price": signal.entry_price,
                "actual_entry_price": position.entry_price,
                "first_executable_entry_timestamp_ms": position.first_executable_entry_timestamp_ms,
                "entry_lag_ms": position.entry_lag_ms,
                "entry_lag_ltf_candles": position.entry_lag_ltf_candles,
                "entered_late_vs_first_executable": position.entered_late_vs_first_executable,
                "entry_order_submit_lag_ms": position.entry_order_submit_lag_ms,
                "entry_order_submit_lag_ltf_candles": position.entry_order_submit_lag_ltf_candles,
                "previous_live_scan_closed_timestamp_ms": position.previous_live_scan_closed_timestamp_ms or "",
                "first_unscanned_decision_timestamp_ms": position.first_unscanned_decision_timestamp_ms or "",
                "live_scan_gap_ltf_candles": position.live_scan_gap_ltf_candles,
                "entry_fill_timestamp_ms": position.entry_fill_timestamp_ms,
                "entry_order_submitted_at_ms": position.entry_order_submitted_at_ms,
                "entry_client_order_id": entry_client_order_id,
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
                    self.logger(f"позиция {signal.symbol} открыта, но Telegram-график входа не отправлен: {exc}")
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
            self.logger(f"позиция {signal.symbol} открыта, но Telegram-вход не отправлен: {exc}")
        self.logger(
            f"{_compact_symbol(signal.symbol)} открыт · {signal.levels_timeframe.value}/{signal.entry_timeframe.value} · "
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
        details.update(_entry_lag_details(signal, observed_timestamp_ms=now_ms))
        details.update(
            {
                "previous_live_scan_closed_timestamp_ms": signal.previous_live_scan_closed_timestamp_ms or "",
                "first_unscanned_decision_timestamp_ms": signal.first_unscanned_decision_timestamp_ms or "",
                "live_scan_gap_ltf_candles": signal.live_scan_gap_ltf_candles,
            }
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
        now_ms = int(time.time() * 1000)
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
            "previous_live_scan_closed_timestamp_ms": signal.previous_live_scan_closed_timestamp_ms or "",
            "first_unscanned_decision_timestamp_ms": signal.first_unscanned_decision_timestamp_ms or "",
            "live_scan_gap_ltf_candles": signal.live_scan_gap_ltf_candles,
            **_entry_lag_details(signal, observed_timestamp_ms=now_ms),
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
                self.logger(f"стоп-сообщение {position.signal.symbol} не отправлено: {exc}")
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
            self.logger(f"стоп-сообщение {position.signal.symbol} не отредактировано: {exc}")
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
                        tp1_client_order_id = _live_client_order_id(
                            "tp1",
                            signal.symbol,
                            position.position_id,
                            int(time.time() * 1000),
                        )
                        tp1_fill = self.exchange.create_market_order_with_fill(
                            signal.symbol,
                            "sell",
                            close_amount,
                            reduce_only=True,
                            client_order_id=tp1_client_order_id,
                        )
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
                                "client_order_id": tp1_client_order_id,
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
            except ExchangeConnectivityError as exc:
                self.artifacts.append_event(
                    "position_monitor_network_degraded",
                    signal.symbol,
                    {"position_id": position.position_id, "reason": f"{type(exc).__name__}: {exc}"},
                )
                self.logger(f"позиция {signal.symbol}, сеть/API временно недоступны: {exc}")
                time.sleep(30.0)
            except Exception as exc:
                self.artifacts.append_event(
                    "position_monitor_internal_error",
                    signal.symbol,
                    {"position_id": position.position_id, "reason": f"{type(exc).__name__}: {exc}"},
                )
                self._record_position_integrity_error(
                    position,
                    reason=f"position monitor internal error: {type(exc).__name__}: {exc}",
                )
                return

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
        self.logger(f"{_compact_symbol(symbol)} integrity error · {reason}")

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
        self.logger(f"{_compact_symbol(symbol)} exit unresolved · {reason}")

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
        if math.isfinite(pnl_usdt) and math.isfinite(position.notional_usdt) and position.notional_usdt > 0.0:
            with self._state_lock:
                self._closed_pnl_usdt_total += float(pnl_usdt)
                self._closed_notional_usdt_total += float(position.notional_usdt)
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
                self.logger(f"позиция {position.signal.symbol} закрыта, но Telegram-график закрытия не отправлен: {exc}")
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

    def _buffer_live_ohlcv_cache_write(self, symbol: str, timeframe: Timeframe, frame: pd.DataFrame) -> int:
        if self._ohlcv_cache_storage is None or not self.config.live_ohlcv_cache_write_enabled or frame.empty:
            return 0
        prepared = _prepare_cached_ohlcv_frame(frame)
        if prepared.empty:
            return 0
        symbol_key = _position_symbol_key(symbol)
        buffer_key = (symbol_key, symbol, timeframe.value)
        self._live_ohlcv_write_buffer.setdefault(buffer_key, []).append(prepared)
        buffered_rows = int(len(prepared))
        self._live_ohlcv_pending_rows += buffered_rows
        self.artifacts.append_event(
            "live_ohlcv_cache_buffered",
            symbol,
            {
                "timeframe": timeframe.value,
                "buffered_rows": buffered_rows,
                "pending_rows": int(self._live_ohlcv_pending_rows),
                "min_timestamp_ms": int(prepared["timestamp"].min()),
                "max_timestamp_ms": int(prepared["timestamp"].max()),
            },
        )
        return buffered_rows

    def _flush_live_ohlcv_cache_if_due(self, *, force: bool = False, reason: str) -> int:
        storage = self._ohlcv_cache_storage
        if storage is None or not self._live_ohlcv_write_buffer:
            return 0
        elapsed = time.monotonic() - self._last_live_ohlcv_cache_flush_at
        if (
            not force
            and elapsed < float(self.config.live_ohlcv_cache_flush_interval_seconds)
            and self._live_ohlcv_pending_rows < int(self.config.live_ohlcv_cache_max_buffer_rows)
        ):
            return 0
        pending_items = self._live_ohlcv_write_buffer
        pending_rows = int(self._live_ohlcv_pending_rows)
        self._live_ohlcv_write_buffer = {}
        self._live_ohlcv_pending_rows = 0
        flushed_rows_total = 0
        remaining: dict[tuple[str, str, str], list[pd.DataFrame]] = {}
        remaining_rows = 0
        failed_symbol_timeframes = 0
        flush_limit = None if force else self.config.live_ohlcv_cache_flush_max_symbol_timeframes
        items = list(pending_items.items())
        selected_items = items if flush_limit is None else items[: max(0, int(flush_limit))]
        deferred_items = items[len(selected_items) :]
        for key, frames in deferred_items:
            remaining[key] = frames
            remaining_rows += int(sum(len(frame) for frame in frames))
        for (_symbol_key, symbol, timeframe_value), frames in selected_items:
            try:
                timeframe = Timeframe(timeframe_value)
                combined = _concat_cached_ohlcv_frames(frames)
                added_rows = int(storage.save_incremental(symbol, timeframe, combined))
                flushed_rows_total += int(len(combined))
                self.artifacts.append_event(
                    "live_ohlcv_cache_flushed",
                    symbol,
                    {
                        "timeframe": timeframe.value,
                        "reason": reason,
                        "input_rows": int(len(combined)),
                        "added_rows": added_rows,
                        "pending_rows_before_flush": pending_rows,
                    },
                )
            except Exception as exc:
                remaining[(_symbol_key, symbol, timeframe_value)] = frames
                failed_symbol_timeframes += 1
                failed_rows = sum(len(frame) for frame in frames)
                remaining_rows += int(failed_rows)
                self.artifacts.append_event(
                    "live_ohlcv_cache_flush_failed",
                    symbol,
                    {
                        "timeframe": timeframe_value,
                        "reason": reason,
                        "input_rows": int(failed_rows),
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:1000],
                    },
                )
        self._live_ohlcv_write_buffer = remaining
        self._live_ohlcv_pending_rows = remaining_rows
        self._last_live_ohlcv_cache_flush_at = time.monotonic()
        self.artifacts.append_event(
            "live_ohlcv_cache_flush_summary",
            "__live__",
            {
                "reason": reason,
                "force": bool(force),
                "pending_rows_before_flush": pending_rows,
                "flushed_rows": int(flushed_rows_total),
                "remaining_rows": int(remaining_rows),
                "failed_symbol_timeframes": int(failed_symbol_timeframes),
                "flushed_symbol_timeframes": int(len(selected_items)),
                "deferred_symbol_timeframes": int(len(deferred_items)),
                "flush_max_symbol_timeframes": flush_limit if flush_limit is not None else "",
            },
        )
        return flushed_rows_total


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
        symbol_key = _position_symbol_key(symbol)
        memory_key = (symbol_key, timeframe.value)
        if memory_key in self._live_ohlcv_frame_cache and _cached_frame_covers_window(
            self._live_ohlcv_frame_cache[memory_key],
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
        ):
            load_status = "memory_hit"
            load_reason = "process_memory_cache"
            cached_rows_before = int(len(self._live_ohlcv_frame_cache[memory_key]))
            cached = self._live_ohlcv_frame_cache[memory_key]
        else:
            load_result = storage.load_window_result(
                symbol,
                timeframe,
                start_timestamp_ms=expected_start_ms,
                end_timestamp_ms=expected_end_ms,
            )
            load_status = load_result.status
            load_reason = load_result.reason
            cached_rows_before = int(len(load_result.frame)) if load_result.frame is not None else 0
            cached = _prepare_cached_ohlcv_frame(load_result.frame)
            if memory_key in self._live_ohlcv_frame_cache:
                cached = _concat_cached_ohlcv_frames([self._live_ohlcv_frame_cache[memory_key], cached])
            self._live_ohlcv_frame_cache[memory_key] = cached
        fetched_rows = 0
        buffered_rows = 0
        fetched_ranges: list[str] = []
        fetched_frames: list[pd.DataFrame] = []
        missing_ranges = _missing_ohlcv_ranges(
            cached,
            start_timestamp_ms=expected_start_ms,
            end_timestamp_ms=expected_end_ms,
            timeframe_ms=timeframe_ms,
        )
        for missing_start_ms, missing_end_ms in missing_ranges:
            fetch_end_ms = int(missing_end_ms) + timeframe_ms - 1
            fetched = self._fetch_uncached_chart_frame(
                symbol,
                timeframe,
                start_timestamp_ms=missing_start_ms,
                end_timestamp_ms=fetch_end_ms,
            )
            if not fetched.empty:
                fetched = fetched.copy()
                fetched["live_cache_source"] = (
                    "exchange_ohlcv"
                    if timeframe_ms >= int(Timeframe.M1.to_milliseconds())
                    else "binance_futures_ws_aggTrade_explicit_backfill"
                    if self.aggtrade_source is not None
                    else "binance_futures_aggTrades"
                )
                fetched["live_cache_target_timeframe"] = timeframe.value
                fetched["live_cache_version"] = "p168_live_ohlcv_cache_v1"
                fetched_rows += int(len(fetched))
                fetched_ranges.append(f"{missing_start_ms}:{fetch_end_ms}")
                fetched_frames.append(fetched)
        if fetched_frames:
            fetched_combined = _concat_cached_ohlcv_frames(fetched_frames)
            if self.config.live_ohlcv_cache_write_enabled:
                buffered_rows = self._buffer_live_ohlcv_cache_write(symbol, timeframe, fetched_combined)
            cached = _concat_cached_ohlcv_frames([cached, fetched_combined])
            self._live_ohlcv_frame_cache[memory_key] = cached
        window_end_ms = min(int(end_timestamp_ms), int(expected_end_ms))
        window = cached.loc[
            (cached["timestamp"].astype("int64") >= int(start_timestamp_ms))
            & (cached["timestamp"].astype("int64") <= int(window_end_ms))
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
                    "buffered_rows": buffered_rows,
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
                "expected_start_ms": int(expected_start_ms),
                "expected_end_ms": int(expected_end_ms),
                "window_end_ms": int(window_end_ms),
                "missing_range_count": int(len(missing_ranges)),
                "remaining_gap_count": int(len(remaining_ranges)),
                "fetched_rows": fetched_rows,
                "buffered_rows": buffered_rows,
                "pending_rows": int(self._live_ohlcv_pending_rows),
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
        if self.aggtrade_source is not None:
            all_rows = self._fetch_ws_aggtrade_raw_rows(
                symbol,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
        else:
            all_rows = self._fetch_aggtrade_raw_rows_cached(
                symbol,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
        if not all_rows:
            return pd.DataFrame(columns=list(REQUIRED_PRICE_COLUMNS) + ["volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"])
        return _aggregate_aggtrades_to_ohlcv_frame(
            pd.DataFrame(all_rows),
            timeframe_ms=timeframe_ms,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )

    @staticmethod
    def _time_ranges_duration_ms(ranges: tuple[tuple[int, int], ...] | list[tuple[int, int]]) -> int:
        return int(sum(max(0, int(end) - int(start) + 1) for start, end in ranges))

    def _prune_aggtrade_process_cache(self, symbol_key: str, *, now_ms: int) -> None:
        ttl_ms = max(0, int(self.config.live_aggtrade_rest_cache_ttl_ms))
        if ttl_ms <= 0:
            self._aggtrade_raw_process_cache.pop(symbol_key, None)
            return
        cutoff_ms = int(now_ms) - ttl_ms
        ranges = [
            cached_range
            for cached_range in self._aggtrade_raw_process_cache.get(symbol_key, [])
            if int(cached_range.end_timestamp_ms) >= cutoff_ms
        ]
        if ranges:
            self._aggtrade_raw_process_cache[symbol_key] = ranges
        else:
            self._aggtrade_raw_process_cache.pop(symbol_key, None)

    def _aggtrade_cached_ranges(self, symbol_key: str, *, now_ms: int | None = None) -> list[AggTradeRawRange]:
        if now_ms is not None:
            self._prune_aggtrade_process_cache(symbol_key, now_ms=now_ms)
        ranges = list(self._aggtrade_raw_process_cache.get(symbol_key, []))
        ranges.extend(self._current_cycle_aggtrade_cache.get(symbol_key, []))
        return ranges

    def _remember_aggtrade_raw_range(
        self,
        symbol_key: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        rows: list[dict[str, object]],
    ) -> None:
        raw_range = AggTradeRawRange(
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
            rows=tuple(dict(row) for row in rows),
        )
        self._current_cycle_aggtrade_cache.setdefault(symbol_key, []).append(raw_range)
        if int(self.config.live_aggtrade_rest_cache_ttl_ms) > 0:
            self._aggtrade_raw_process_cache.setdefault(symbol_key, []).append(raw_range)
            self._prune_aggtrade_process_cache(symbol_key, now_ms=int(time.time() * 1000))

    def _coalesce_aggtrade_missing_ranges(
        self,
        ranges: list[tuple[int, int]],
        *,
        request_start_ms: int,
        request_end_ms: int,
    ) -> list[tuple[int, int]]:
        padding_ms = max(0, int(self.config.live_aggtrade_rest_cache_padding_ms))
        padded = [
            (max(int(request_start_ms), int(start) - padding_ms), min(int(request_end_ms), int(end) + padding_ms))
            for start, end in ranges
            if int(start) <= int(end)
        ]
        return _merge_time_ranges(padded)

    def _fetch_aggtrade_raw_ranges_cached(
        self,
        symbol: str,
        requested_ranges: list[tuple[int, int]],
        *,
        fetch_window_start_ms: int | None = None,
        fetch_window_end_ms: int | None = None,
    ) -> tuple[list[dict[str, object]], list[str], int, int]:
        requested_ranges = _merge_time_ranges(
            [(int(start), int(end)) for start, end in requested_ranges if int(start) <= int(end)]
        )
        if not requested_ranges:
            return [], [], 0, 0
        symbol_key = _position_symbol_key(symbol)
        request_start_ms = min(start for start, _end in requested_ranges)
        request_end_ms = max(end for _start, end in requested_ranges)
        fetch_window_start = int(fetch_window_start_ms) if fetch_window_start_ms is not None else request_start_ms
        fetch_window_end = int(fetch_window_end_ms) if fetch_window_end_ms is not None else request_end_ms
        cached_ranges = self._aggtrade_cached_ranges(symbol_key, now_ms=int(time.time() * 1000))
        missing_ranges: list[tuple[int, int]] = []
        for requested_start, requested_end in requested_ranges:
            missing_ranges.extend(
                _missing_aggtrade_raw_ranges(
                    cached_ranges,
                    start_timestamp_ms=requested_start,
                    end_timestamp_ms=requested_end,
                )
            )
        missing_ranges = _merge_time_ranges(missing_ranges)
        if not missing_ranges:
            self._cycle_aggtrade_cache_hits += 1
            self._cycle_aggtrade_process_cache_hits += 1
        elif len(missing_ranges) < len(requested_ranges):
            self._cycle_aggtrade_cache_hits += 1
        original_missing_count = len(missing_ranges)
        fetch_ranges = self._coalesce_aggtrade_missing_ranges(
            missing_ranges,
            request_start_ms=fetch_window_start,
            request_end_ms=fetch_window_end,
        )
        if original_missing_count > len(fetch_ranges):
            self._cycle_aggtrade_coalesced_missing_ranges += original_missing_count - len(fetch_ranges)
        backfill_ranges: list[str] = []
        fetched_rows_total = 0
        for fetch_start_ms, fetch_end_ms in fetch_ranges:
            rows = self._fetch_aggtrade_raw_rows(
                symbol,
                start_timestamp_ms=int(fetch_start_ms),
                end_timestamp_ms=int(fetch_end_ms),
            )
            fetched_rows_total += int(len(rows))
            self._cycle_aggtrade_rest_fetched_ms += max(0, int(fetch_end_ms) - int(fetch_start_ms) + 1)
            backfill_ranges.append(f"{fetch_start_ms}:{fetch_end_ms}")
            self._remember_aggtrade_raw_range(
                symbol_key,
                start_timestamp_ms=int(fetch_start_ms),
                end_timestamp_ms=int(fetch_end_ms),
                rows=rows,
            )
        all_ranges = self._aggtrade_cached_ranges(symbol_key)
        all_rows: list[dict[str, object]] = []
        for requested_start, requested_end in requested_ranges:
            for cached_range in all_ranges:
                if cached_range.end_timestamp_ms < requested_start or cached_range.start_timestamp_ms > requested_end:
                    continue
                all_rows.extend(
                    _filter_aggtrade_rows_by_time(
                        cached_range.rows,
                        start_timestamp_ms=requested_start,
                        end_timestamp_ms=requested_end,
                    )
                )
        return _dedupe_aggtrade_rows(all_rows), backfill_ranges, fetched_rows_total, original_missing_count

    def _fetch_ws_aggtrade_raw_rows(
        self,
        symbol: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> list[dict[str, object]]:
        source = self.aggtrade_source
        if source is None:
            raise LiveDataIntegrityError("ws aggTrade source is disabled on WS fetch path")
        self._cycle_aggtrade_requests += 1
        request_start_ms = int(start_timestamp_ms)
        request_end_ms = int(end_timestamp_ms)
        read_result = source.read_rows(
            symbol,
            start_timestamp_ms=request_start_ms,
            end_timestamp_ms=request_end_ms,
        )
        all_rows = [dict(row) for row in read_result.rows]
        backfilled_rows = 0
        backfill_ranges: list[str] = []
        missing_total_ms = self._time_ranges_duration_ms(read_result.missing_ranges)
        max_backfill_ms = int(self.config.live_ws_aggtrade_max_backfill_ms)
        if read_result.missing_ranges and missing_total_ms > max_backfill_ms:
            self._cycle_ws_aggtrade_coverage_pending += 1
            self.artifacts.append_event(
                "ws_aggtrade_frame_read",
                symbol,
                {
                    "source": source.source_id,
                    "status": "coverage_pending",
                    "read_status": read_result.status,
                    "reason": read_result.reason or "",
                    "subscribed": bool(read_result.subscribed),
                    "connection_status": read_result.connection_status,
                    "last_error": (read_result.last_error or "")[:500],
                    "start_timestamp_ms": request_start_ms,
                    "end_timestamp_ms": request_end_ms,
                    "ws_rows": int(len(read_result.rows)),
                    "buffer_row_count": int(read_result.buffer_row_count),
                    "missing_range_count": int(len(read_result.missing_ranges)),
                    "missing_ranges": [f"{start}:{end}" for start, end in read_result.missing_ranges],
                    "missing_total_ms": int(missing_total_ms),
                    "backfill_max_ms": int(max_backfill_ms),
                    "backfill_skipped": True,
                    "backfill_ranges": [],
                    "backfilled_rows": 0,
                    "result_rows": 0,
                    "last_trade_timestamp_ms": read_result.last_trade_timestamp_ms if read_result.last_trade_timestamp_ms is not None else "",
                    "last_receive_at_ms": read_result.last_receive_at_ms if read_result.last_receive_at_ms is not None else "",
                },
            )
            raise LiveWsAggTradeCoveragePending(
                "ws_aggtrade_coverage_pending;"
                f"status={read_result.status};"
                f"missing_total_ms={missing_total_ms};"
                f"backfill_max_ms={max_backfill_ms}",
                symbol=symbol,
                start_timestamp_ms=request_start_ms,
                end_timestamp_ms=request_end_ms,
                missing_ranges=tuple(read_result.missing_ranges),
                status=read_result.status,
                reason=read_result.reason,
            )
        if read_result.missing_ranges:
            cached_rows, backfill_ranges, backfilled_rows, original_missing_count = self._fetch_aggtrade_raw_ranges_cached(
                symbol,
                [(int(start), int(end)) for start, end in read_result.missing_ranges],
                fetch_window_start_ms=request_start_ms,
                fetch_window_end_ms=request_end_ms,
            )
            all_rows.extend(cached_rows)
            for missing_start_ms, missing_end_ms in read_result.missing_ranges:
                source.add_backfill_rows(
                    symbol,
                    _filter_aggtrade_rows_by_time(
                        cached_rows,
                        start_timestamp_ms=int(missing_start_ms),
                        end_timestamp_ms=int(missing_end_ms),
                    ),
                    start_timestamp_ms=int(missing_start_ms),
                    end_timestamp_ms=int(missing_end_ms),
                )
        all_rows = _dedupe_aggtrade_rows(all_rows)
        if read_result.missing_ranges:
            self._cycle_ws_aggtrade_backfill_reads += 1
            self._cycle_ws_aggtrade_backfilled_rows += int(backfilled_rows)
            if (read_result.connection_status or "").lower() != "connected":
                self._cycle_ws_aggtrade_not_connected_backfill_reads += 1
        else:
            self._cycle_aggtrade_cache_hits += 1
        self.artifacts.append_event(
            "ws_aggtrade_frame_read",
            symbol,
            {
                "source": source.source_id,
                "status": read_result.status,
                "reason": read_result.reason or "",
                "subscribed": bool(read_result.subscribed),
                "connection_status": read_result.connection_status,
                "last_error": (read_result.last_error or "")[:500],
                "start_timestamp_ms": request_start_ms,
                "end_timestamp_ms": request_end_ms,
                "ws_rows": int(len(read_result.rows)),
                "buffer_row_count": int(read_result.buffer_row_count),
                "missing_range_count": int(len(read_result.missing_ranges)),
                "cache_missing_range_count": int(original_missing_count) if read_result.missing_ranges else 0,
                "network_backfill_range_count": int(len(backfill_ranges)),
                "missing_ranges": [f"{start}:{end}" for start, end in read_result.missing_ranges],
                "missing_total_ms": int(missing_total_ms),
                "backfill_max_ms": int(max_backfill_ms),
                "backfill_skipped": False,
                "backfill_ranges": backfill_ranges,
                "backfilled_rows": int(backfilled_rows),
                "result_rows": int(len(all_rows)),
                "last_trade_timestamp_ms": read_result.last_trade_timestamp_ms if read_result.last_trade_timestamp_ms is not None else "",
                "last_receive_at_ms": read_result.last_receive_at_ms if read_result.last_receive_at_ms is not None else "",
            },
        )
        return all_rows

    def _fetch_aggtrade_raw_rows_cached(
        self,
        symbol: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> list[dict[str, object]]:
        self._cycle_aggtrade_requests += 1
        request_start_ms = int(start_timestamp_ms)
        request_end_ms = int(end_timestamp_ms)
        rows, _backfill_ranges, _fetched_rows_total, _missing_count = self._fetch_aggtrade_raw_ranges_cached(
            symbol,
            [(request_start_ms, request_end_ms)],
            fetch_window_start_ms=request_start_ms,
            fetch_window_end_ms=request_end_ms,
        )
        return rows

    def _fetch_aggtrade_raw_rows(
        self,
        symbol: str,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> list[dict[str, object]]:
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
                params["endTime"] = int(end_timestamp_ms)
            self._cycle_aggtrade_network_calls += 1
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
            if last_ts is not None and last_ts >= int(end_timestamp_ms):
                break
            if len(rows) < 1000:
                break
        return all_rows

    def _reconcile_orphan_orders(self, symbols: list[str], *, cycle: int, force: bool = False) -> int:
        if not force and cycle != 1 and cycle % self.config.order_reconcile_interval_cycles != 0:
            return 0
        max_checks = len(symbols) if force else min(self.config.order_reconcile_batch_size, len(symbols))
        if force:
            self.artifacts.append_event(
                "orphan_order_reconcile_started",
                "__live__",
                {"scope": "run_trade_symbols_only", "symbols_to_check": max_checks, "cycle": cycle},
            )
        if not symbols:
            return 0
        checked_symbols: set[str] = set()
        cancelled_total = 0
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
        stop_client_order_id = _live_client_order_id("stop", symbol, position_id, reason)
        stop_order = self.exchange.create_stop_market_order(
            symbol,
            "sell",
            amount,
            stop_price,
            client_order_id=stop_client_order_id,
        )
        stop_order_id = _resolve_order_id(stop_order) or ""
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
                "client_order_id": stop_client_order_id,
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
            actual_amount = abs(float(self.exchange.fetch_symbol_position_amount(symbol)))
        except Exception as exc:
            self.artifacts.append_event(
                "unprotected_entry_position_read_failed",
                symbol,
                {
                    "position_id": position_id,
                    "amount_requested": amount,
                    "reason": reason,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise LiveDataIntegrityError(
                f"unprotected entry exposure amount could not be read: position_id={position_id} symbol={symbol} reason={reason}"
            ) from exc
        if not math.isfinite(actual_amount):
            self.artifacts.append_event(
                "unprotected_entry_position_amount_invalid",
                symbol,
                {"position_id": position_id, "amount_requested": amount, "actual_amount": actual_amount, "reason": reason},
            )
            raise LiveDataIntegrityError(
                f"unprotected entry exposure amount is invalid: position_id={position_id} symbol={symbol} reason={reason}"
            )
        if actual_amount <= 0.0:
            self.artifacts.append_event(
                "unprotected_entry_no_exchange_exposure",
                symbol,
                {"position_id": position_id, "amount_requested": amount, "reason": reason},
            )
            return
        close_amount = min(float(amount), actual_amount) if math.isfinite(amount) and amount > 0.0 else actual_amount
        try:
            client_order_id = _live_client_order_id("protect", symbol, position_id, reason, int(time.time() * 1000))
            fill = self.exchange.create_market_order_with_fill(
                symbol,
                "sell",
                close_amount,
                reduce_only=True,
                client_order_id=client_order_id,
            )
            self._track_order_reconcile_symbol(symbol, reason="unprotected_entry_reduce_only_exit")
        except Exception as exc:
            self.artifacts.append_event(
                "unprotected_entry_reduce_only_exit_failed",
                symbol,
                {
                    "position_id": position_id,
                    "amount_requested": amount,
                    "actual_amount": actual_amount,
                    "close_amount": close_amount,
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
                "actual_amount": actual_amount,
                "close_amount": close_amount,
                "client_order_id": client_order_id,
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



def _live_config_has_subminute_entry_pairs(config: LiveAnomalyConfig) -> bool:
    return any(
        int(entry_timeframe.to_milliseconds()) < int(Timeframe.M1.to_milliseconds())
        for _, entry_timeframe in config.timeframe_pairs
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
        "live_ws_ticker_stale_ms": (config.live_ws_ticker_stale_ms, 1),
        "live_ws_aggtrade_stale_ms": (config.live_ws_aggtrade_stale_ms, 1),
        "live_ws_aggtrade_buffer_minutes": (config.live_ws_aggtrade_buffer_minutes, 1),
        "signal_scan_backfill_candles": (config.signal_scan_backfill_candles, 1),
        "max_signal_age_ms": (config.max_signal_age_ms, 1),
        "stop_limit_per_symbol": (config.stop_limit_per_symbol, 1),
        "oi_fresh_ms": (config.oi_fresh_ms, 1),
        "trail_lookback_candles": (config.trail_lookback_candles, 1),
        "order_reconcile_interval_cycles": (config.order_reconcile_interval_cycles, 1),
        "order_reconcile_batch_size": (config.order_reconcile_batch_size, 1),
        "max_monitor_empty_ohlcv_cycles": (config.max_monitor_empty_ohlcv_cycles, 1),
        "live_ohlcv_cache_max_buffer_rows": (config.live_ohlcv_cache_max_buffer_rows, 1),
        "live_aggtrade_rest_cache_ttl_ms": (config.live_aggtrade_rest_cache_ttl_ms, 1),
    }
    for name, (value, minimum) in integer_minimums.items():
        if not isinstance(value, int) or value < minimum:
            raise LiveStartupError(f"Некорректный live config: {name} должен быть целым >= {minimum}, получено {value!r}")
    if not isinstance(config.ticker_radar_watch_batch_size, int) or config.ticker_radar_watch_batch_size < 0:
        raise LiveStartupError(
            "Некорректный live config: ticker_radar_watch_batch_size должен быть целым >= 0, "
            f"получено {config.ticker_radar_watch_batch_size!r}"
        )
    if config.max_precise_scan_symbols_per_cycle is not None and (
        not isinstance(config.max_precise_scan_symbols_per_cycle, int)
        or config.max_precise_scan_symbols_per_cycle < 1
    ):
        raise LiveStartupError(
            "Некорректный live config: max_precise_scan_symbols_per_cycle должен быть целым >= 1 или None, "
            f"получено {config.max_precise_scan_symbols_per_cycle!r}"
        )
    if (
        not isinstance(config.live_ws_aggtrade_max_backfill_ms, int)
        or config.live_ws_aggtrade_max_backfill_ms < 0
    ):
        raise LiveStartupError(
            "Некорректный live config: live_ws_aggtrade_max_backfill_ms должен быть целым >= 0, "
            f"получено {config.live_ws_aggtrade_max_backfill_ms!r}"
        )
    if (
        not isinstance(config.live_aggtrade_rest_cache_padding_ms, int)
        or config.live_aggtrade_rest_cache_padding_ms < 0
    ):
        raise LiveStartupError(
            "Некорректный live config: live_aggtrade_rest_cache_padding_ms должен быть целым >= 0, "
            f"получено {config.live_aggtrade_rest_cache_padding_ms!r}"
        )
    if config.live_ohlcv_cache_flush_max_symbol_timeframes is not None and (
        not isinstance(config.live_ohlcv_cache_flush_max_symbol_timeframes, int)
        or config.live_ohlcv_cache_flush_max_symbol_timeframes < 1
    ):
        raise LiveStartupError(
            "Некорректный live config: live_ohlcv_cache_flush_max_symbol_timeframes должен быть целым >= 1 или None, "
            f"получено {config.live_ohlcv_cache_flush_max_symbol_timeframes!r}"
        )

    if _live_config_has_subminute_entry_pairs(config):
        if not config.ticker_radar_enabled:
            raise LiveStartupError(
                "Некорректный live config: subminute entry TF требует ticker_radar_enabled=true. "
                "Live uses ticker-radar/active symbols plus explicitly labeled DANGER cold coverage; "
                "there is no silent fallback to full inactive aggTrades scans."
            )
        if config.ticker_radar_watch_batch_size < 1:
            raise LiveStartupError(
                "Некорректный live config: subminute entry TF требует ticker_radar_watch_batch_size >= 1, "
                "иначе ticker-radar symbols не смогут попасть в precise scan; cold coverage remains bounded and DANGER-labeled."
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
        "live_ws_ticker_startup_wait_seconds": config.live_ws_ticker_startup_wait_seconds,
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
        "live_ohlcv_cache_flush_interval_seconds": config.live_ohlcv_cache_flush_interval_seconds,
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
    return getattr(config, field_name, None)


def _latest_closed_candle_start_ms(timeframe: Timeframe, *, now_ms: int) -> int:
    timeframe_ms = int(timeframe.to_milliseconds())
    if timeframe_ms <= 0:
        raise ValueError(f"invalid timeframe milliseconds: {timeframe.value}")
    return ((int(now_ms) - timeframe_ms) // timeframe_ms) * timeframe_ms


def _empty_cached_ohlcv_frame() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype=dtype) for column, dtype in CACHED_OHLCV_DTYPES.items()})


def _prepare_cached_ohlcv_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return _empty_cached_ohlcv_frame()
    prepared = frame.copy()
    for column in prepared.columns:
        if column == "timestamp" or column in REQUIRED_PRICE_COLUMNS or column in REQUIRED_FLOW_COLUMNS or column in OPTIONAL_FLOW_COLUMNS:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.loc[prepared["timestamp"].notna()].copy()
    if prepared.empty:
        return _empty_cached_ohlcv_frame()
    prepared["timestamp"] = prepared["timestamp"].astype("int64")
    return prepared.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _concat_cached_ohlcv_frames(frames: list[pd.DataFrame | None]) -> pd.DataFrame:
    prepared_frames: list[pd.DataFrame] = []
    for frame in frames:
        prepared = _prepare_cached_ohlcv_frame(frame)
        if not prepared.empty:
            prepared_frames.append(prepared)
    if not prepared_frames:
        return _empty_cached_ohlcv_frame()
    if len(prepared_frames) == 1:
        return prepared_frames[0].copy()
    return _prepare_cached_ohlcv_frame(pd.concat(prepared_frames, ignore_index=True))


def _cached_frame_covers_window(frame: pd.DataFrame, *, start_timestamp_ms: int, end_timestamp_ms: int) -> bool:
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return False
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna()
    if timestamps.empty:
        return False
    return int(timestamps.min()) <= int(start_timestamp_ms) and int(timestamps.max()) >= int(end_timestamp_ms)


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


def _entry_lag_details(signal: LiveSignal, *, observed_timestamp_ms: int) -> dict[str, object]:
    entry_timeframe_ms = int(signal.entry_timeframe.to_milliseconds())
    first_executable_ms = int(signal.decision_timestamp_ms) + entry_timeframe_ms
    lag_ms = int(observed_timestamp_ms) - first_executable_ms
    lag_candles = int(math.floor(lag_ms / entry_timeframe_ms)) if entry_timeframe_ms > 0 else 0
    return {
        "first_executable_entry_timestamp_ms": first_executable_ms,
        "observed_entry_check_timestamp_ms": int(observed_timestamp_ms),
        "entry_lag_ms": lag_ms,
        "entry_lag_ltf_candles": lag_candles,
        "entered_late_vs_first_executable": bool(lag_ms >= entry_timeframe_ms),
    }


def _live_scan_gap_details(
    *,
    decision_timestamp_ms: int,
    previous_scan_closed_timestamp_ms: int | None,
    entry_timeframe: Timeframe,
) -> dict[str, object]:
    entry_timeframe_ms = int(entry_timeframe.to_milliseconds())
    if previous_scan_closed_timestamp_ms is None or entry_timeframe_ms <= 0:
        return {
            "previous_live_scan_closed_timestamp_ms": None,
            "first_unscanned_decision_timestamp_ms": None,
            "live_scan_gap_ltf_candles": 0,
        }
    previous_ts = int(previous_scan_closed_timestamp_ms)
    decision_ts = int(decision_timestamp_ms)
    skipped = max(0, int((decision_ts - previous_ts) // entry_timeframe_ms) - 1)
    return {
        "previous_live_scan_closed_timestamp_ms": previous_ts,
        "first_unscanned_decision_timestamp_ms": previous_ts + entry_timeframe_ms if skipped > 0 else decision_ts,
        "live_scan_gap_ltf_candles": skipped,
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
        f"Category: {_telegram_code(signal.category_id)} ({_telegram_escape(signal.category_label)})\n\n"
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


def _live_client_order_id(prefix: str, symbol: str, *parts: object) -> str:
    compact_symbol = re.sub(r"[^A-Za-z0-9]", "", _compact_symbol(symbol).upper()) or "SYM"
    raw = "|".join([prefix, compact_symbol, *(str(part) for part in parts)])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    normalized_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", str(prefix).strip())[:8] or "live"
    return f"pa_{normalized_prefix}_{compact_symbol[:8]}_{digest}"[:36]


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


def _filter_aggtrade_rows_by_time(
    rows: tuple[dict[str, object], ...] | list[dict[str, object]],
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> list[dict[str, object]]:
    filtered: list[dict[str, object]] = []
    for row in rows:
        timestamp_ms = _resolve_aggtrade_timestamp(row)
        if timestamp_ms is None:
            continue
        if int(start_timestamp_ms) <= timestamp_ms <= int(end_timestamp_ms):
            filtered.append(dict(row))
    return filtered


def _missing_aggtrade_raw_ranges(
    cached_ranges: list[AggTradeRawRange],
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> list[tuple[int, int]]:
    request_start = int(start_timestamp_ms)
    request_end = int(end_timestamp_ms)
    if request_start > request_end:
        return []
    coverage: list[tuple[int, int]] = []
    for cached_range in cached_ranges:
        overlap_start = max(request_start, int(cached_range.start_timestamp_ms))
        overlap_end = min(request_end, int(cached_range.end_timestamp_ms))
        if overlap_start <= overlap_end:
            coverage.append((overlap_start, overlap_end))
    if not coverage:
        return [(request_start, request_end)]
    coverage.sort()
    merged: list[tuple[int, int]] = []
    for start, end in coverage:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    missing: list[tuple[int, int]] = []
    cursor = request_start
    for start, end in merged:
        if cursor < start:
            missing.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= request_end:
        missing.append((cursor, request_end))
    return missing


def _merge_time_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    normalized = sorted((int(start), int(end)) for start, end in ranges if int(start) <= int(end))
    if not normalized:
        return []
    merged = [normalized[0]]
    for start, end in normalized[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _dedupe_aggtrade_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: dict[tuple[object, int], dict[str, object]] = {}
    fallback_index = 0
    for row in rows:
        agg_id = _resolve_aggtrade_id(row)
        timestamp_ms = _resolve_aggtrade_timestamp(row)
        if timestamp_ms is None:
            fallback_index += 1
            key = (f"missing_ts:{fallback_index}", fallback_index)
        elif agg_id is None:
            fallback_index += 1
            key = (f"missing_id:{fallback_index}", timestamp_ms)
        else:
            key = (agg_id, timestamp_ms)
        deduped[key] = dict(row)
    return sorted(
        deduped.values(),
        key=lambda row: (
            _resolve_aggtrade_timestamp(row) if _resolve_aggtrade_timestamp(row) is not None else -1,
            _resolve_aggtrade_id(row) if _resolve_aggtrade_id(row) is not None else -1,
        ),
    )


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


def _format_live_heartbeat(
    *,
    cycle_seconds: float,
    connection_health_pct: float,
    anomalies_total: int,
    active_now: int,
    active_seen: int,
    open_positions: int,
    closed_positions: int,
    pnl_pct: float,
    connection_text: str,
    suffix: str = "",
) -> str:
    compact_width = 9
    anomaly_text = f"anl {anomalies_total}"
    active_text = f"act {active_now}/{active_seen}"
    position_text = f"pos {open_positions}/{closed_positions}"
    pnl_text = f"pnl {_format_percent(pnl_pct, signed=False)}"
    connection_status_text = connection_text.strip() or "status n/a"
    return (
        f"{f'{cycle_seconds:.1f}s':>{compact_width}} · "
        f"{_format_percent(connection_health_pct, signed=False, precision=1):>{compact_width}} · "
        f"{anomaly_text:>{compact_width}} · "
        f"{active_text:>{compact_width}} · "
        f"{position_text:>{compact_width}} · "
        f"{pnl_text:>{compact_width}} · "
        f"{connection_status_text}{suffix}"
    )


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
