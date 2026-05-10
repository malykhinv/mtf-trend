"""Strict REST-only micro-live runner for the anomaly wake-up research strategy."""

from __future__ import annotations

import csv
import json
import math
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from uuid import uuid4
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from domain.enums.timeframe import Timeframe
from research_tools.anomaly_continuation_lab import compute_start_verticality_metrics
from strategy.pno.config import PNO_LIVE_TIMEFRAME_PAIRS, validate_pno_timeframe_pair


REQUIRED_PRICE_COLUMNS = ("timestamp", "open", "high", "low", "close")
REQUIRED_FLOW_COLUMNS = ("quote_volume", "number_of_trades")
OPTIONAL_FLOW_COLUMNS = ("taker_buy_quote_volume",)
NATURE_EMOJIS = ("🏔️", "🌋", "🌊", "🌙", "🌓", "🌘", "🌄", "🌅", "🌌", "🌧️", "🌩️", "🪨")
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
    "stop_price",
    "tp1_price",
    "amount",
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
    timeframe_pairs: tuple[tuple[Timeframe, Timeframe], ...] = PNO_LIVE_TIMEFRAME_PAIRS
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
    inactive_batch_size: int = 20
    inactive_batch_with_active: int = 7
    signal_scan_backfill_candles: int = 10
    scan_sleep_seconds: float = 2.0
    network_sleep_seconds: float = 30.0
    max_cycles: int | None = None
    stop_cooldown_hours: float = 12.0
    stop_limit_per_symbol: int = 2
    telegram_cooldown_seconds: float = 900.0
    oi_fresh_ms: int = 5 * 60 * 1000
    trail_lookback_candles: int = 5
    trail_buffer_r: float = 0.10


@dataclass(slots=True)
class LiveSignal:
    category_id: str
    category_label: str
    category_priority: int
    symbol: str
    levels_timeframe: Timeframe
    entry_timeframe: Timeframe
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
    telegram_open_message_id: int | None = None
    telegram_stop_message_id: int | None = None
    remaining_amount: float = 0.0
    tp1_done: bool = False
    current_stop_price: float = 0.0
    realized_pnl_usdt: float = 0.0


class TelegramDispatcher:
    def __init__(self, config: TelegramConfig, *, logger: Callable[[str], None], cooldown_seconds: float) -> None:
        self._config = config
        self._logger = logger
        self._cooldown_seconds = cooldown_seconds
        self._queue: queue.Queue[dict[str, object]] = queue.Queue()
        self._last_sent: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, name="telegram-dispatcher", daemon=True)
        self._thread.start()

    def send(self, *, channel: str, key: str, text: str, reply_to_message_id: int | None = None) -> None:
        self._queue.put(
            {
                "channel": channel,
                "key": key,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
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
            return False

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
            "parse_mode": "Markdown",
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
            "parse_mode": "Markdown",
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

    def _send_photo(self, *, channel: str, photo_path: Path, caption: str, reply_to_message_id: int | None) -> None:
        token = self._config.positions_bot_token if channel == "positions" else self._config.events_bot_token
        chat_id = self._config.positions_chat_id if channel == "positions" else self._config.events_chat_id
        boundary = f"----codex{uuid4().hex}"
        fields: dict[str, object] = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
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
            response.read()


class LiveArtifactWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.root / "live_positions.csv"
        self.events_path = self.root / "live_events.csv"
        self._lock = threading.Lock()
        self._ensure_csv(self.ledger_path, LIVE_LEDGER_COLUMNS)
        self._ensure_csv(self.events_path, ("timestamp_utc", "event", "symbol", "details_json"))

    @staticmethod
    def _ensure_csv(path: Path, columns: tuple[str, ...]) -> None:
        if path.exists():
            return
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=list(columns)).writeheader()

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
                    "entry_price": signal.entry_price,
                    "stop_price": signal.stop_price,
                    "tp1_price": signal.tp1_price,
                    "amount": position.amount,
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
        signal = position.signal
        with self._lock, self.ledger_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(LIVE_LEDGER_COLUMNS))
            writer.writerow(
                {
                    "position_id": position.position_id,
                    "status": "closed",
                    "symbol": signal.symbol,
                    "signal_category_id": signal.category_id,
                    "signal_category_label": signal.category_label,
                    "session": signal.session,
                    "opened_at_utc": position.opened_at_utc,
                    "closed_at_utc": datetime.now(UTC).isoformat(),
                    "entry_price": signal.entry_price,
                    "stop_price": signal.stop_price,
                    "tp1_price": signal.tp1_price,
                    "amount": position.amount,
                    "notional_usdt": position.notional_usdt,
                    "risk_usdt": position.risk_usdt,
                    "realized_pnl_usdt": pnl_usdt,
                    "realized_pnl_pct": pnl_pct,
                    "signal_json": signal.to_json(),
                    "entry_order_id": position.entry_order_id,
                    "stop_order_id": position.stop_order_id,
                    "telegram_open_message_id": position.telegram_open_message_id or "",
                    "telegram_stop_message_id": position.telegram_stop_message_id or "",
                    "telegram_close_message_id": "",
                    "close_reason": reason,
                }
            )


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
        self.logger = logger
        self._pump_categories = _resolve_live_pump_categories(config.pump_categories)
        self.artifacts = LiveArtifactWriter(
            config.results_dir / "live_anomaly_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        )
        self.telegram = TelegramDispatcher(telegram, logger=logger, cooldown_seconds=config.telegram_cooldown_seconds)
        self._open_positions: dict[str, LivePosition] = {}
        self._opening_symbols: set[str] = set()
        self._recent_stops: dict[str, list[float]] = {}
        self._seen_decisions: set[tuple[str, str, str, int]] = set()
        self._opened_positions_total = 0
        self._closed_positions_total = 0
        self._state_lock = threading.RLock()
        self._inactive_cursor = 0
        self._network_degraded = False

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
        self.telegram.send(
            channel="events",
            key="live_started",
            text=(
                "🛰️ *Live запущен*\n"
                "REST-only обход активен. Реальные ордера разрешены явным флагом.\n"
                f"TF: `{_telegram_escape(timeframe_pairs_label)}`.\n"
                f"Категории: `{category_ids}`."
            ),
        )
        cycle = 0
        while self.config.max_cycles is None or cycle < self.config.max_cycles:
            cycle += 1
            cycle_started = time.monotonic()
            try:
                opened_before, _, closed_before = self._live_counts()
                batch = self._next_symbol_batch(symbols)
                signals = self._scan_batch(batch)
                for signal in signals:
                    self._maybe_open_position(signal)
                cycle_seconds = time.monotonic() - cycle_started
                opened_total, active_positions, closed_total = self._live_counts()
                if cycle == 1 or cycle % 10 == 0 or opened_total != opened_before or closed_total != closed_before:
                    opened_delta = opened_total - opened_before
                    self.logger(
                        f"live: {cycle_seconds:.1f}s · открыто {opened_total} (+{opened_delta}) · "
                        f"слежу {active_positions} · закрыто {closed_total}"
                    )
                self._network_degraded = False
                time.sleep(self.config.scan_sleep_seconds)
            except KeyboardInterrupt:
                self.logger("live: остановлено пользователем")
                return 0
            except LiveDataIntegrityError as exc:
                self.logger(f"live: остановлено из-за ошибки целостности live-данных: {exc}")
                self.telegram.send(
                    channel="events",
                    key="live_data_integrity_error",
                    text=f"🧯 *Live остановлен*\nОшибка целостности данных.\n`{_telegram_escape(str(exc)[:600])}`",
                )
                return 3
            except Exception as exc:
                if not self._network_degraded:
                    self.logger(f"live: сеть/API недоступны, жду восстановления. Причина: {exc}")
                    self.telegram.send(
                        channel="events",
                        key="network_degraded",
                        text=f"🪁 *Пауза по сети/API*\nБот не падает, ждёт восстановления.\nПричина: `{_telegram_escape(str(exc)[:300])}`",
                    )
                self._network_degraded = True
                time.sleep(self.config.network_sleep_seconds)
        self.logger("live: достигнут лимит циклов")
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
        with self._state_lock:
            active = sorted({position.signal.symbol for position in self._open_positions.values()})
        if active:
            inactive_size = self.config.inactive_batch_with_active
        else:
            inactive_size = self.config.inactive_batch_size
        inactive: list[str] = []
        attempts = 0
        while len(inactive) < inactive_size and attempts < len(symbols):
            symbol = symbols[self._inactive_cursor % len(symbols)]
            self._inactive_cursor += 1
            attempts += 1
            symbol_key = _position_symbol_key(symbol)
            with self._state_lock:
                symbol_is_active = symbol_key in self._open_positions or symbol_key in self._opening_symbols
            if symbol_is_active or self._symbol_in_stop_cooldown(symbol):
                continue
            inactive.append(symbol)
        return [*active, *inactive]

    def _scan_batch(self, symbols: list[str]) -> list[LiveSignal]:
        signals: list[LiveSignal] = []
        now_ms = int(time.time() * 1000)
        for levels_timeframe, entry_timeframe in self.config.timeframe_pairs:
            timeframe_ms = int(levels_timeframe.to_milliseconds())
            lookback_ms = (
                self.config.baseline_candles
                + self.config.confirmation_candles
                + self.config.signal_scan_backfill_candles
                + 5
            ) * timeframe_ms
            for symbol in symbols:
                frame = self.exchange.fetch_ohlcv(symbol, levels_timeframe, now_ms - lookback_ms, now_ms)
                signals.extend(
                    self._build_recent_signals(
                        symbol,
                        frame,
                        now_ms=now_ms,
                        levels_timeframe=levels_timeframe,
                        entry_timeframe=entry_timeframe,
                    )
                )
        return signals

    def _build_recent_signals(
        self,
        symbol: str,
        frame: pd.DataFrame,
        *,
        now_ms: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> list[LiveSignal]:
        if frame.empty or len(frame) < self.config.baseline_candles + self.config.confirmation_candles + 1:
            return []
        missing_price = [column for column in REQUIRED_PRICE_COLUMNS if column not in frame.columns]
        if missing_price:
            self.artifacts.append_event("reject_missing_price_columns", symbol, {"columns": missing_price})
            return []
        missing_flow = [column for column in REQUIRED_FLOW_COLUMNS if column not in frame.columns]
        if missing_flow:
            self.artifacts.append_event("reject_missing_flow_columns", symbol, {"columns": missing_flow})
            return []
        frame = frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        levels_timeframe_ms = int(levels_timeframe.to_milliseconds())
        closed_before = now_ms - levels_timeframe_ms
        frame = frame.loc[frame["timestamp"].astype(int) <= closed_before].reset_index(drop=True)
        if len(frame) < self.config.baseline_candles + self.config.confirmation_candles + 1:
            return []
        last_start_pos = len(frame) - 1 - self.config.confirmation_candles
        first_start_pos = max(self.config.baseline_candles, last_start_pos - self.config.signal_scan_backfill_candles + 1)
        signals: list[LiveSignal] = []
        for start_pos in range(first_start_pos, last_start_pos + 1):
            decision_ts = int(frame.iloc[start_pos + self.config.confirmation_candles]["timestamp"])
            key = (symbol, levels_timeframe.value, entry_timeframe.value, decision_ts)
            with self._state_lock:
                if key in self._seen_decisions:
                    continue
                self._seen_decisions.add(key)
            signal = self._build_signal_at_start(
                symbol,
                frame,
                now_ms=now_ms,
                start_pos=start_pos,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
            )
            if signal is not None:
                signals.append(signal)
        return signals

    def _build_signal_at_start(
        self,
        symbol: str,
        frame: pd.DataFrame,
        *,
        now_ms: int,
        start_pos: int,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> LiveSignal | None:
        baseline = frame.iloc[start_pos - self.config.baseline_candles : start_pos]
        segment = frame.iloc[start_pos : start_pos + self.config.confirmation_candles + 1]
        if baseline.empty or segment.empty:
            return None

        start = frame.iloc[start_pos]
        decision = segment.iloc[-1]
        baseline_quote = float(pd.to_numeric(baseline["quote_volume"], errors="coerce").median())
        baseline_trades = float(pd.to_numeric(baseline["number_of_trades"], errors="coerce").median())
        start_quote = float(start["quote_volume"])
        start_trades = float(start["number_of_trades"])
        start_open = float(start["open"])
        start_close = float(start["close"])
        start_high = float(start["high"])
        start_low = float(start["low"])
        start_ret = _safe_divide(start_close - start_open, start_open)
        abs_start_ret = abs(start_ret) if math.isfinite(start_ret) else float("nan")
        baseline_avg_trade_quote = _safe_divide(baseline_quote, baseline_trades)
        start_avg_trade_quote = _safe_divide(start_quote, start_trades)
        start_avg_trade_ratio = _safe_divide(start_avg_trade_quote, baseline_avg_trade_quote)
        start_quote_ratio_per_abs_return = _safe_divide(_safe_divide(start_quote, baseline_quote), abs_start_ret)
        baseline_range_pct = float(
            ((baseline["high"].astype(float) - baseline["low"].astype(float)) / baseline["close"].astype(float).replace(0.0, pd.NA)).median()
        )
        start_range_pct_ratio = _safe_divide(_safe_divide(start_high - start_low, start_open), baseline_range_pct)
        quote_ratio = _safe_divide(start_quote, baseline_quote)
        trade_ratio = _safe_divide(start_trades, baseline_trades)
        if not math.isfinite(quote_ratio) or not math.isfinite(trade_ratio):
            self.artifacts.append_event(
                "reject_invalid_flow_ratios",
                symbol,
                {
                    "baseline_quote": _finite_or_none(baseline_quote),
                    "baseline_trades": _finite_or_none(baseline_trades),
                    "start_quote": _finite_or_none(start_quote),
                    "start_trades": _finite_or_none(start_trades),
                    "quote_ratio": _finite_or_none(quote_ratio),
                    "trade_ratio": _finite_or_none(trade_ratio),
                    "decision_timestamp_ms": int(decision["timestamp"]),
                },
            )
            return None
        if quote_ratio < self.config.min_quote_ratio_start or trade_ratio < self.config.min_trade_ratio_start:
            self.artifacts.append_event(
                "reject_weak_start_flow",
                symbol,
                {
                    "quote_ratio": quote_ratio,
                    "trade_ratio": trade_ratio,
                    "min_quote_ratio": self.config.min_quote_ratio_start,
                    "min_trade_ratio": self.config.min_trade_ratio_start,
                    "decision_timestamp_ms": int(decision["timestamp"]),
                },
            )
            return None

        segment_high = float(segment["high"].max())
        segment_low = float(segment["low"].min())
        impulse_range = segment_high - segment_low
        prior_whipsaw = _prior_up_down_whipsaw_to_impulse_range(baseline, impulse_range=impulse_range)
        decision_close = float(decision["close"])
        price_retention = _safe_divide(decision_close - start_open, segment_high - start_open)
        verticality = compute_start_verticality_metrics(segment)
        verticality_score = float(verticality["start_verticality_score"])
        hold_threshold = float(start["close"]) - (float(start["close"]) - float(start["open"])) * 0.50
        hold_count = int((segment["close"].astype(float) >= hold_threshold).sum())
        previous_stop = segment_low - self.config.stop_buffer_range_fraction * impulse_range
        ema20 = frame["close"].astype(float).ewm(span=20, adjust=False).mean()
        decision_ema20 = float(ema20.iloc[start_pos + self.config.confirmation_candles])
        stop_price = max(previous_stop, decision_ema20)
        entry_price = decision_close
        risk = entry_price - stop_price
        initial_risk_pct = _safe_divide(risk, entry_price)
        if not math.isfinite(risk) or risk <= 0.0:
            self.artifacts.append_event(
                "reject_invalid_initial_risk",
                symbol,
                {
                    "entry_price": _finite_or_none(entry_price),
                    "stop_price": _finite_or_none(stop_price),
                    "risk": _finite_or_none(risk),
                    "decision_timestamp_ms": int(decision["timestamp"]),
                },
            )
            return None
        if not math.isfinite(initial_risk_pct) or initial_risk_pct > self.config.max_initial_risk_pct:
            self.artifacts.append_event(
                "reject_initial_risk_too_wide",
                symbol,
                {
                    "initial_risk_pct": _finite_or_none(initial_risk_pct),
                    "max": self.config.max_initial_risk_pct,
                    "decision_timestamp_ms": int(decision["timestamp"]),
                },
            )
            return None
        tp1_price = entry_price + risk

        category_rejections: list[dict[str, object]] = []
        oi_change_loaded = False
        oi_change: float | None = None
        oi_change_reason: str | None = None
        next_taker_share_loaded = False
        next_taker_share = float("nan")
        for category in self._pump_categories:
            max_start_quote_ratio = _category_value(category, self.config, "max_start_quote_ratio")
            if max_start_quote_ratio is not None and quote_ratio > max_start_quote_ratio:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_exhausted_quote_ratio",
                        {"quote_ratio": quote_ratio, "max": max_start_quote_ratio, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            max_start_trade_ratio = _category_value(category, self.config, "max_start_trade_ratio")
            if max_start_trade_ratio is not None and trade_ratio > max_start_trade_ratio:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_exhausted_trade_ratio",
                        {"trade_ratio": trade_ratio, "max": max_start_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            max_avg_trade_ratio = _category_value(category, self.config, "max_start_avg_trade_quote_size_ratio")
            if max_avg_trade_ratio is not None and not math.isfinite(start_avg_trade_ratio):
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_invalid_avg_trade_quote_size_ratio",
                        {"ratio": _finite_or_none(start_avg_trade_ratio), "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            if max_avg_trade_ratio is not None and start_avg_trade_ratio > max_avg_trade_ratio:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_large_print_signature",
                        {"ratio": start_avg_trade_ratio, "max": max_avg_trade_ratio, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            max_quote_per_return = _category_value(category, self.config, "max_start_quote_ratio_per_abs_return")
            if max_quote_per_return is not None and not math.isfinite(start_quote_ratio_per_abs_return):
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_invalid_quote_ratio_per_abs_return",
                        {"ratio": _finite_or_none(start_quote_ratio_per_abs_return), "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            if max_quote_per_return is not None and start_quote_ratio_per_abs_return > max_quote_per_return:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_poor_effort_per_return",
                        {"ratio": start_quote_ratio_per_abs_return, "max": max_quote_per_return, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            max_range_ratio = _category_value(category, self.config, "max_start_range_pct_ratio_to_baseline")
            if max_range_ratio is not None and not math.isfinite(start_range_pct_ratio):
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_invalid_range_expansion_ratio",
                        {"ratio": _finite_or_none(start_range_pct_ratio), "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            if max_range_ratio is not None and start_range_pct_ratio > max_range_ratio:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_extreme_range_expansion",
                        {"ratio": start_range_pct_ratio, "max": max_range_ratio, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            max_prior_whipsaw = self.config.max_prior_up_down_whipsaw_to_impulse_range
            if max_prior_whipsaw is not None and not math.isfinite(prior_whipsaw):
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_invalid_prior_whipsaw",
                        {
                            "prior_up_down_whipsaw_to_impulse_range": _finite_or_none(prior_whipsaw),
                            "decision_timestamp_ms": int(decision["timestamp"]),
                        },
                    )
                )
                continue
            if max_prior_whipsaw is not None and prior_whipsaw > max_prior_whipsaw:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_prior_up_down_whipsaw",
                        {
                            "prior_up_down_whipsaw_to_impulse_range": prior_whipsaw,
                            "max": max_prior_whipsaw,
                            "decision_timestamp_ms": int(decision["timestamp"]),
                        },
                    )
                )
                continue
            if not math.isfinite(price_retention):
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_invalid_price_retention",
                        {"price_retention": _finite_or_none(price_retention), "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            if price_retention < self.config.min_price_retention:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_low_price_retention",
                        {"price_retention": price_retention, "min": self.config.min_price_retention, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            max_price_retention = _category_value(category, self.config, "max_price_retention")
            if max_price_retention is not None and price_retention > max_price_retention:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_overextended_retention",
                        {"price_retention": price_retention, "max": max_price_retention, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            min_next_taker_share = _category_value(category, self.config, "min_next_taker_buy_quote_share")
            if min_next_taker_share is not None:
                if "taker_buy_quote_volume" not in frame.columns:
                    category_rejections.append(
                        self._record_category_reject(
                            category,
                            symbol,
                            "reject_missing_taker_buy_share",
                            {"required": min_next_taker_share, "decision_timestamp_ms": int(decision["timestamp"])},
                        )
                    )
                    continue
                if not next_taker_share_loaded:
                    taker_quote = pd.to_numeric(segment.iloc[1:]["taker_buy_quote_volume"], errors="coerce")
                    quote_volume = pd.to_numeric(segment.iloc[1:]["quote_volume"], errors="coerce")
                    valid_taker_share_rows = taker_quote.notna() & quote_volume.notna() & quote_volume.gt(0.0)
                    if not bool(valid_taker_share_rows.all()):
                        next_taker_share = float("nan")
                    else:
                        next_taker_share = float((taker_quote / quote_volume).mean())
                    next_taker_share_loaded = True
                if not math.isfinite(next_taker_share):
                    category_rejections.append(
                        self._record_category_reject(
                            category,
                            symbol,
                            "reject_invalid_taker_buy_share",
                            {"share": _finite_or_none(next_taker_share), "decision_timestamp_ms": int(decision["timestamp"])},
                        )
                    )
                    continue
                if next_taker_share < min_next_taker_share:
                    category_rejections.append(
                        self._record_category_reject(
                            category,
                            symbol,
                            "reject_weak_next_taker_buy_share",
                            {"share": next_taker_share, "min": min_next_taker_share, "decision_timestamp_ms": int(decision["timestamp"])},
                        )
                    )
                    continue
            if not math.isfinite(verticality_score):
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_invalid_verticality",
                        {"verticality_score": _finite_or_none(verticality_score), "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            if verticality_score < self.config.min_verticality_score:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_low_verticality",
                        {"verticality_score": verticality_score, "min": self.config.min_verticality_score, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            if hold_count < self.config.min_hold_count:
                category_rejections.append(
                    self._record_category_reject(
                        category,
                        symbol,
                        "reject_low_hold_count",
                        {"hold_count": hold_count, "min": self.config.min_hold_count, "decision_timestamp_ms": int(decision["timestamp"])},
                    )
                )
                continue
            if self.config.min_oi_change_pct_3x5m is not None:
                if not oi_change_loaded:
                    oi_result = self._fetch_live_oi_change(symbol, now_ms=now_ms)
                    oi_change = oi_result.value
                    oi_change_reason = oi_result.reason
                    oi_change_loaded = True
                if oi_change is None or not math.isfinite(oi_change) or oi_change <= self.config.min_oi_change_pct_3x5m:
                    category_rejections.append(
                        self._record_category_reject(
                            category,
                            symbol,
                            "reject_oi",
                            {
                                "oi_change_pct_3x5m": _finite_or_none(oi_change),
                                "oi_status": oi_change_reason or "below_threshold",
                                "required_gt": self.config.min_oi_change_pct_3x5m,
                                "decision_timestamp_ms": int(decision["timestamp"]),
                            },
                        )
                    )
                    continue

            strengths = [
                f"категория {category.category_id}",
                f"объём x{quote_ratio:.1f}",
                f"сделки x{trade_ratio:.1f}",
                f"удержание {price_retention:.0%}",
                f"вертикальность {verticality_score:.2f}",
            ]
            if oi_change is not None:
                strengths.append(f"OI {oi_change:.1%}")
            weaknesses: list[str] = []
            if "taker_buy_quote_volume" not in frame.columns:
                weaknesses.append("нет taker-buy в kline")
            signal = LiveSignal(
                category_id=category.category_id,
                category_label=category.label,
                category_priority=category.priority,
                symbol=symbol,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                decision_timestamp_ms=int(decision["timestamp"]),
                start_timestamp_ms=int(start["timestamp"]),
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
            self.artifacts.append_event(
                "category_selected",
                symbol,
                {
                    "category_id": category.category_id,
                    "category_label": category.label,
                    "category_priority": category.priority,
                    "decision_timestamp_ms": int(decision["timestamp"]),
                    "prior_category_rejections": category_rejections,
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
                self.artifacts.append_event(
                    "reject_symbol_position_already_active",
                    signal.symbol,
                    {"symbol_key": symbol_key, "levels_tf": signal.levels_timeframe.value, "entry_tf": signal.entry_timeframe.value},
                )
                return
            if len(self._open_positions) + len(self._opening_symbols) >= self.config.max_open_positions:
                reject_max_positions = True
            if self._symbol_in_stop_cooldown(signal.symbol):
                return
            if not reject_max_positions:
                self._opening_symbols.add(symbol_key)
        if reject_max_positions:
            self.artifacts.append_event("reject_max_positions", signal.symbol, {"max": self.config.max_open_positions})
            return
        try:
            try:
                balance = float(self.exchange.fetch_usdt_free_balance())
            except (TypeError, ValueError) as exc:
                self.artifacts.append_event("reject_invalid_free_balance", signal.symbol, {"free_usdt": None, "reason": str(exc)})
                with self._state_lock:
                    self._opening_symbols.discard(symbol_key)
                return
            if not math.isfinite(balance):
                self.artifacts.append_event("reject_invalid_free_balance", signal.symbol, {"free_usdt": None})
                with self._state_lock:
                    self._opening_symbols.discard(symbol_key)
                return
            if balance <= 0.0:
                self.artifacts.append_event("reject_no_free_balance", signal.symbol, {"free_usdt": balance})
                with self._state_lock:
                    self._opening_symbols.discard(symbol_key)
                return
            notional = self.config.position_notional_usdt
            if not math.isfinite(notional) or notional <= 0.0:
                self.artifacts.append_event("reject_invalid_position_notional", signal.symbol, {"position_notional": _finite_or_none(notional)})
                with self._state_lock:
                    self._opening_symbols.discard(symbol_key)
                return
            if balance < notional:
                self.artifacts.append_event(
                    "reject_insufficient_margin_for_fixed_notional",
                    signal.symbol,
                    {"free_usdt": balance, "position_notional": notional},
                )
                with self._state_lock:
                    self._opening_symbols.discard(symbol_key)
                return
            risk_per_unit = signal.entry_price - signal.stop_price
            amount_by_risk = notional / signal.entry_price
            actual_risk_usdt = amount_by_risk * risk_per_unit
            entry_order = self.exchange.create_market_order(signal.symbol, "buy", amount_by_risk)
            entry_order_id = str(entry_order.get("id", ""))
            try:
                stop_order = self.exchange.create_stop_market_order(signal.symbol, "sell", amount_by_risk, signal.stop_price)
            except Exception:
                self.exchange.create_market_order(signal.symbol, "sell", amount_by_risk, reduce_only=True)
                raise
            stop_order_id = str(stop_order.get("id", ""))
            opened_at_ms = int(time.time() * 1000)
            position = LivePosition(
                position_id=f"{signal.symbol.replace('/', '_').replace(':', '_')}_{signal.decision_timestamp_ms}",
                signal=signal,
                amount=float(amount_by_risk),
                notional_usdt=float(notional),
                risk_usdt=float(actual_risk_usdt),
                entry_order_id=entry_order_id,
                stop_order_id=stop_order_id,
                opened_at_utc=datetime.fromtimestamp(opened_at_ms / 1000, UTC).isoformat(),
                opened_at_ms=opened_at_ms,
                remaining_amount=float(amount_by_risk),
                current_stop_price=float(signal.stop_price),
            )
            with self._state_lock:
                self._open_positions[symbol_key] = position
                self._opened_positions_total += 1
                self._opening_symbols.discard(symbol_key)
        except Exception:
            with self._state_lock:
                self._opening_symbols.discard(symbol_key)
            raise
        self.artifacts.append_position(position)
        self.artifacts.append_event(
            "position_opened",
            signal.symbol,
            {
                "position_id": position.position_id,
                "category_id": signal.category_id,
                "category_label": signal.category_label,
                "category_priority": signal.category_priority,
            },
        )
        try:
            position.telegram_open_message_id = self.telegram.send_sync(
                channel="positions",
                text=_format_open_message(position),
            )
        except Exception as exc:
            self.artifacts.append_event("telegram_open_failed", signal.symbol, {"position_id": position.position_id, "reason": str(exc)})
            self.logger(f"live: позиция {signal.symbol} открыта, но Telegram-вход не отправлен: {exc}")
        self.logger(
            f"live: {_compact_symbol(signal.symbol)} открыт · {signal.levels_timeframe.value}/{signal.entry_timeframe.value} · "
            f"риск {actual_risk_usdt:.2f} · notional {notional:.2f}"
        )
        threading.Thread(
            target=self._monitor_position,
            args=(position,),
            name=f"position-{_compact_symbol(signal.symbol)}",
            daemon=True,
        ).start()

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
        last_stop_price = signal.stop_price
        while True:
            try:
                actual_amount = abs(self.exchange.fetch_symbol_position_amount(signal.symbol))
                if actual_amount <= 0.0:
                    self._finalize_position(position, reason="позиция закрыта на бирже", pnl_price=position.current_stop_price)
                    return
                now_ms = int(time.time() * 1000)
                frame = self.exchange.fetch_ohlcv(
                    signal.symbol,
                    Timeframe.M1,
                    signal.decision_timestamp_ms,
                    now_ms,
                )
                if frame.empty:
                    time.sleep(15.0)
                    continue
                latest = frame.sort_values("timestamp").iloc[-1]
                latest_high = float(latest["high"])
                latest_low = float(latest["low"])
                latest_close = float(latest["close"])
                if not position.tp1_done and latest_high >= signal.tp1_price:
                    close_amount = max(actual_amount * 0.5, 0.0)
                    if close_amount > 0.0:
                        self.exchange.create_market_order(signal.symbol, "sell", close_amount, reduce_only=True)
                        position.realized_pnl_usdt += close_amount * (signal.tp1_price - signal.entry_price)
                    time.sleep(2.0)
                    actual_after_tp1 = abs(self.exchange.fetch_symbol_position_amount(signal.symbol))
                    position.tp1_done = True
                    position.remaining_amount = actual_after_tp1
                    if position.remaining_amount <= 0.0:
                        self._finalize_position(position, reason="TP1 закрыл позицию полностью", pnl_price=signal.tp1_price)
                        return
                    new_stop_order = self.exchange.create_stop_market_order(
                        signal.symbol,
                        "sell",
                        position.remaining_amount,
                        signal.entry_price,
                    )
                    old_stop_order_id = position.stop_order_id
                    position.stop_order_id = str(new_stop_order.get("id", position.stop_order_id))
                    position.current_stop_price = signal.entry_price
                    try:
                        self.exchange.cancel_order(signal.symbol, old_stop_order_id)
                    except Exception as exc:
                        self.artifacts.append_event("old_stop_cancel_failed_after_be", signal.symbol, {"reason": str(exc)})
                    last_stop_price = signal.entry_price
                    self.artifacts.append_event("tp1_and_stop_to_be", signal.symbol, {"position_id": position.position_id})
                    self._send_or_edit_stop_message(
                        position,
                        stop_price=signal.entry_price,
                        reason="tp1_be",
                        text=_format_stop_move_message(position, stop_price=signal.entry_price, label="BE"),
                    )
                if position.tp1_done:
                    latest_ts = int(latest["timestamp"])
                    prior = frame.loc[frame["timestamp"].astype(int) < latest_ts].tail(self.config.trail_lookback_candles)
                    if not prior.empty:
                        trail_low = float(prior["low"].min())
                        structural_stop = trail_low - self.config.trail_buffer_r * signal.initial_risk
                    else:
                        structural_stop = last_stop_price
                    new_stop = max(last_stop_price, structural_stop)
                    if new_stop > last_stop_price and new_stop < latest_close:
                        new_stop_order = self.exchange.create_stop_market_order(
                            signal.symbol,
                            "sell",
                            actual_amount,
                            new_stop,
                        )
                        old_stop_order_id = position.stop_order_id
                        position.stop_order_id = str(new_stop_order.get("id", position.stop_order_id))
                        position.current_stop_price = new_stop
                        try:
                            self.exchange.cancel_order(signal.symbol, old_stop_order_id)
                        except Exception as exc:
                            self.artifacts.append_event("old_stop_cancel_failed_after_trail", signal.symbol, {"reason": str(exc)})
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
                        self._finalize_position(position, reason="стоп исполнен на бирже", pnl_price=last_stop_price)
                        return
                time.sleep(15.0)
            except Exception as exc:
                self.artifacts.append_event("position_monitor_error", signal.symbol, {"reason": str(exc)})
                self.logger(f"live: позиция {signal.symbol}, временная ошибка ведения: {exc}")
                time.sleep(30.0)

    def _finalize_position(self, position: LivePosition, *, reason: str, pnl_price: float | None) -> None:
        symbol_key = _position_symbol_key(position.signal.symbol)
        with self._state_lock:
            self._open_positions.pop(symbol_key, None)
            self._closed_positions_total += 1
        exit_price = pnl_price if pnl_price is not None else position.signal.entry_price
        remaining_amount = position.remaining_amount if position.remaining_amount > 0.0 else position.amount
        pnl_usdt = position.realized_pnl_usdt + remaining_amount * (exit_price - position.signal.entry_price)
        pnl_pct = _safe_divide(pnl_usdt, position.notional_usdt)
        if reason.startswith("стоп"):
            with self._state_lock:
                self._recent_stops.setdefault(symbol_key, []).append(time.time())
        self.artifacts.append_event(
            "position_closed",
            position.signal.symbol,
            {
                "position_id": position.position_id,
                "reason": reason,
                "pnl_pct": pnl_pct,
                "pnl_usdt": pnl_usdt,
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
        if chart_path is not None and self.telegram.send_photo(
            channel="positions",
            photo_path=chart_path,
            caption=close_text,
            reply_to_message_id=position.telegram_open_message_id,
        ):
            return
        self.telegram.send(
            channel="positions",
            key=f"close:{position.position_id}",
            reply_to_message_id=position.telegram_open_message_id,
            text=close_text,
        )

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
            trade_row = {
                "symbol": signal.symbol,
                "status": "closed",
                "anomaly_timestamp_ms": int(signal.start_timestamp_ms),
                "decision_timestamp_ms": int(signal.decision_timestamp_ms),
                "entry_timestamp_ms": int(position.opened_at_ms),
                "entry_timestamp_utc": position.opened_at_utc,
                "exit_timestamp_ms": int(exit_timestamp_ms),
                "entry_price": float(signal.entry_price),
                "initial_stop": float(signal.stop_price),
                "tp1_price": float(signal.tp1_price),
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
        if int(timeframe.to_milliseconds()) >= int(Timeframe.M1.to_milliseconds()):
            return self.exchange.fetch_ohlcv(symbol, timeframe, start_timestamp_ms, end_timestamp_ms)
        return self._fetch_aggtrade_chart_frame(
            symbol,
            timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )

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

    def _symbol_in_stop_cooldown(self, symbol: str) -> bool:
        symbol_key = _position_symbol_key(symbol)
        cutoff = time.time() - self.config.stop_cooldown_hours * 3600.0
        with self._state_lock:
            stops = [ts for ts in self._recent_stops.get(symbol_key, []) if ts >= cutoff]
            self._recent_stops[symbol_key] = stops
        return len(stops) >= self.config.stop_limit_per_symbol

    def _live_counts(self) -> tuple[int, int, int]:
        with self._state_lock:
            return self._opened_positions_total, len(self._open_positions), self._closed_positions_total



def _validate_live_config_values(config: LiveAnomalyConfig) -> None:
    integer_minimums = {
        "baseline_candles": (config.baseline_candles, 1),
        "confirmation_candles": (config.confirmation_candles, 1),
        "min_hold_count": (config.min_hold_count, 1),
        "max_open_positions": (config.max_open_positions, 1),
        "inactive_batch_size": (config.inactive_batch_size, 1),
        "inactive_batch_with_active": (config.inactive_batch_with_active, 1),
        "signal_scan_backfill_candles": (config.signal_scan_backfill_candles, 1),
        "stop_limit_per_symbol": (config.stop_limit_per_symbol, 1),
        "oi_fresh_ms": (config.oi_fresh_ms, 1),
        "trail_lookback_candles": (config.trail_lookback_candles, 1),
    }
    for name, (value, minimum) in integer_minimums.items():
        if not isinstance(value, int) or value < minimum:
            raise LiveStartupError(f"Некорректный live config: {name} должен быть целым >= {minimum}, получено {value!r}")

    required_positive = {
        "min_quote_ratio_start": config.min_quote_ratio_start,
        "min_trade_ratio_start": config.min_trade_ratio_start,
        "max_initial_risk_pct": config.max_initial_risk_pct,
        "position_notional_usdt": config.position_notional_usdt,
        "network_sleep_seconds": config.network_sleep_seconds,
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


def _format_open_message(position: LivePosition) -> str:
    signal = position.signal
    entry_price = float(signal.entry_price)
    tp_pct = _safe_divide(float(signal.tp1_price) - entry_price, entry_price)
    sl_pct = _safe_divide(entry_price - float(signal.stop_price), entry_price)
    detail_lines = [
        f"{signal.levels_timeframe.value}/{signal.entry_timeframe.value} · {signal.category_label} · {signal.session}",
        (
            f"retention {_format_percent(signal.price_retention, precision=0)} · "
            f"q×{signal.quote_ratio_start:.1f} · trades×{signal.trade_ratio_start:.1f}"
        ),
    ]
    if signal.strengths:
        detail_lines.append("+ " + "; ".join(signal.strengths[:2]))
    if signal.weaknesses:
        detail_lines.append("− " + "; ".join(signal.weaknesses[:2]))
    category_review = _format_category_rejection_summary(signal.category_rejections)
    symbol = _compact_symbol(signal.symbol)
    return (
        f"{_nature_emoji('open:' + signal.symbol)} {symbol} ({_coinglass_url(signal.symbol)}) LONG\n\n"
        f"Вход {_format_price(entry_price)}\n\n"
        f"TP {_format_price(signal.tp1_price)} {_format_percent(tp_pct)}\n"
        f"SL {_format_price(signal.stop_price)} {_format_percent(sl_pct)}\n\n"
        + "\n".join(detail_lines)
        + category_review
    )


def _format_close_message(position: LivePosition, *, pnl_usdt: float, pnl_pct: float, exit_price: float) -> str:
    signal = position.signal
    symbol = _compact_symbol(signal.symbol)
    detail_lines = [
        f"выход {_format_price(exit_price)}",
        f"{signal.levels_timeframe.value}/{signal.entry_timeframe.value} · {signal.category_label} · {signal.session}",
    ]
    if position.tp1_done:
        detail_lines.append("TP1 был взят")
    return (
        f"{_nature_emoji('close:' + position.position_id)} {symbol} {_format_usdt(pnl_usdt)} USDT\n\n"
        f"PNL {_format_percent(pnl_pct, signed=False)}\n\n"
        + "\n".join(detail_lines)
    )


def _format_stop_move_message(position: LivePosition, *, stop_price: float, label: str) -> str:
    signal = position.signal
    stop_distance_from_entry = _safe_divide(float(stop_price) - float(signal.entry_price), float(signal.entry_price))
    return (
        f"{_nature_emoji('stop:' + label + ':' + position.position_id)} "
        f"{_compact_symbol(signal.symbol)} {label} {_format_percent(stop_distance_from_entry, signed=True, precision=2)}"
    )


def _format_category_rejection_summary(category_rejections: list[dict[str, object]]) -> str:
    if not category_rejections:
        return ""
    lines = ["", "раньше отвалилось:"]
    for rejection in category_rejections[:3]:
        category_id = str(rejection.get("category_id", "unknown_category"))
        reason = str(rejection.get("reason", "unknown_reason"))
        lines.append(f"{_telegram_escape(category_id)}: {_telegram_escape(reason)}")
    if len(category_rejections) > 3:
        lines.append(f"ещё {len(category_rejections) - 3} в live_events.csv")
    return "\n" + "\n".join(lines)




def _format_timeframe_pairs(timeframe_pairs: tuple[tuple[Timeframe, Timeframe], ...]) -> str:
    return ", ".join(f"{levels.value}/{entry.value}" for levels, entry in timeframe_pairs)


def _position_symbol_key(symbol: str) -> str:
    return _compact_symbol(symbol)


def _resolve_aggtrade_timestamp(row: dict[str, object]) -> int | None:
    for column in ("transact_time", "T"):
        if column not in row:
            continue
        try:
            return int(row[column])
        except (TypeError, ValueError):
            return None
    return None


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
    return aggregated.loc[:, columns].reset_index(drop=True)

def _nature_emoji(key: str) -> str:
    index = sum(ord(char) for char in key) % len(NATURE_EMOJIS)
    return NATURE_EMOJIS[index]


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
    return value.replace("`", "'")
