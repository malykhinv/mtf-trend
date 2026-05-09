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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from domain.enums.timeframe import Timeframe
from research_tools.anomaly_continuation_lab import compute_start_verticality_metrics


REQUIRED_FLOW_COLUMNS = ("quote_volume", "number_of_trades")
OPTIONAL_FLOW_COLUMNS = ("taker_buy_quote_volume",)
LIVE_LEDGER_COLUMNS = (
    "position_id",
    "status",
    "symbol",
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
    "telegram_close_message_id",
    "close_reason",
)


class LiveStartupError(RuntimeError):
    """Expected startup validation error for clean CLI output."""


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    events_bot_token: str
    events_chat_id: str
    positions_bot_token: str
    positions_chat_id: str


@dataclass(frozen=True, slots=True)
class LiveAnomalyConfig:
    results_dir: Path
    symbols: tuple[str, ...]
    confirm_real_orders: bool
    baseline_candles: int = 60
    confirmation_candles: int = 4
    min_quote_ratio_start: float = 5.0
    min_trade_ratio_start: float = 5.0
    min_price_retention: float = 0.70
    min_verticality_score: float = 0.25
    min_hold_count: int = 2
    min_oi_change_pct_3x5m: float | None = 0.03
    risk_pct: float = 0.05
    min_notional_usdt: float = 12.0
    max_open_positions: int = 3
    inactive_batch_size: int = 20
    inactive_batch_with_active: int = 7
    scan_sleep_seconds: float = 2.0
    network_sleep_seconds: float = 30.0
    max_cycles: int | None = None
    stop_cooldown_hours: float = 12.0
    stop_limit_per_symbol: int = 2
    telegram_cooldown_seconds: float = 900.0
    oi_fresh_ms: int = 5 * 60 * 1000


@dataclass(slots=True)
class LiveSignal:
    symbol: str
    decision_timestamp_ms: int
    start_timestamp_ms: int
    session: str
    entry_price: float
    stop_price: float
    tp1_price: float
    quote_ratio_start: float
    trade_ratio_start: float
    price_retention: float
    hold_count: int
    verticality_score: float
    oi_change_pct_3x5m: float | None
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True)


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
    telegram_open_message_id: int | None = None
    remaining_amount: float = 0.0
    tp1_done: bool = False


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
        with self._lock, self.events_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp_utc", "event", "symbol", "details_json"])
            writer.writerow(
                {
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "event": event,
                    "symbol": symbol,
                    "details_json": json.dumps(details, ensure_ascii=False, sort_keys=True),
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
        self.artifacts = LiveArtifactWriter(
            config.results_dir / "live_anomaly_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        )
        self.telegram = TelegramDispatcher(telegram, logger=logger, cooldown_seconds=config.telegram_cooldown_seconds)
        self._open_positions: dict[str, LivePosition] = {}
        self._recent_stops: dict[str, list[float]] = {}
        self._inactive_cursor = 0
        self._network_degraded = False

    def run(self) -> int:
        self._validate_startup()
        symbols = list(self.config.symbols) or self.exchange.list_usdt_swap_symbols()
        if not symbols:
            raise LiveStartupError("Нет символов для live-обхода")
        self.logger(f"live: старт, символов {len(symbols)}, max позиций {self.config.max_open_positions}")
        self.logger(f"live: артефакты {self.artifacts.root}")
        self.telegram.send(
            channel="events",
            key="live_started",
            text="🟢 *Live запущен*\nREST-only обход активен. Реальные ордера разрешены явным флагом.",
        )
        cycle = 0
        while self.config.max_cycles is None or cycle < self.config.max_cycles:
            cycle += 1
            try:
                batch = self._next_symbol_batch(symbols)
                signals = self._scan_batch(batch)
                for signal in signals:
                    self._maybe_open_position(signal)
                if cycle == 1 or cycle % 10 == 0:
                    self.logger(
                        f"live: цикл {cycle}, проверено {len(batch)}, сигналов {len(signals)}, открыто {len(self._open_positions)}"
                    )
                self._network_degraded = False
                time.sleep(self.config.scan_sleep_seconds)
            except KeyboardInterrupt:
                self.logger("live: остановлено пользователем")
                return 0
            except Exception as exc:
                if not self._network_degraded:
                    self.logger(f"live: сеть/API недоступны, жду восстановления. Причина: {exc}")
                    self.telegram.send(
                        channel="events",
                        key="network_degraded",
                        text=f"🟡 *Пауза по сети/API*\nБот не падает, ждёт восстановления.\nПричина: `{_telegram_escape(str(exc)[:300])}`",
                    )
                self._network_degraded = True
                time.sleep(self.config.network_sleep_seconds)
        self.logger("live: достигнут лимит циклов")
        return 0

    def _validate_startup(self) -> None:
        if not self.config.confirm_real_orders:
            raise LiveStartupError("Для micro-live нужен явный флаг --confirm-real-orders")
        missing = []
        if not os.getenv("BINANCE_API_KEY"):
            missing.append("BINANCE_API_KEY")
        if not os.getenv("BINANCE_SECRET_KEY"):
            missing.append("BINANCE_SECRET_KEY")
        if missing:
            raise LiveStartupError(f"Не заполнены env переменные: {', '.join(missing)}")
        _ = self.exchange.fetch_usdt_free_balance()

    def _next_symbol_batch(self, symbols: list[str]) -> list[str]:
        active = sorted(self._open_positions)
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
            if symbol in self._open_positions or self._symbol_in_stop_cooldown(symbol):
                continue
            inactive.append(symbol)
        return [*active, *inactive]

    def _scan_batch(self, symbols: list[str]) -> list[LiveSignal]:
        signals: list[LiveSignal] = []
        now_ms = int(time.time() * 1000)
        lookback_ms = (self.config.baseline_candles + self.config.confirmation_candles + 10) * 60_000
        for symbol in symbols:
            frame = self.exchange.fetch_ohlcv(symbol, Timeframe.M1, now_ms - lookback_ms, now_ms)
            signal = self._build_signal(symbol, frame, now_ms=now_ms)
            if signal is not None:
                signals.append(signal)
        return signals

    def _build_signal(self, symbol: str, frame: pd.DataFrame, *, now_ms: int) -> LiveSignal | None:
        if frame.empty or len(frame) < self.config.baseline_candles + self.config.confirmation_candles + 1:
            return None
        missing = [column for column in REQUIRED_FLOW_COLUMNS if column not in frame.columns]
        if missing:
            self.artifacts.append_event("reject_missing_flow_columns", symbol, {"columns": missing})
            return None
        frame = frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        closed_before = now_ms - Timeframe.M1.to_milliseconds()
        frame = frame.loc[frame["timestamp"].astype(int) <= closed_before].reset_index(drop=True)
        if len(frame) < self.config.baseline_candles + self.config.confirmation_candles + 1:
            return None
        start_pos = len(frame) - 1 - self.config.confirmation_candles
        baseline = frame.iloc[start_pos - self.config.baseline_candles : start_pos]
        segment = frame.iloc[start_pos : start_pos + self.config.confirmation_candles + 1]
        if baseline.empty or segment.empty:
            return None

        start = frame.iloc[start_pos]
        baseline_quote = float(pd.to_numeric(baseline["quote_volume"], errors="coerce").median())
        baseline_trades = float(pd.to_numeric(baseline["number_of_trades"], errors="coerce").median())
        start_quote = float(start["quote_volume"])
        start_trades = float(start["number_of_trades"])
        quote_ratio = _safe_divide(start_quote, baseline_quote)
        trade_ratio = _safe_divide(start_trades, baseline_trades)
        if not math.isfinite(quote_ratio) or not math.isfinite(trade_ratio):
            return None
        if quote_ratio < self.config.min_quote_ratio_start or trade_ratio < self.config.min_trade_ratio_start:
            return None

        start_open = float(start["open"])
        segment_high = float(segment["high"].max())
        decision = segment.iloc[-1]
        decision_close = float(decision["close"])
        price_retention = _safe_divide(decision_close - start_open, segment_high - start_open)
        verticality = compute_start_verticality_metrics(segment)
        verticality_score = float(verticality["start_verticality_score"])
        hold_threshold = float(start["close"]) - (float(start["close"]) - float(start["open"])) * 0.50
        hold_count = int((segment["close"].astype(float) >= hold_threshold).sum())
        if price_retention < self.config.min_price_retention:
            return None
        if verticality_score < self.config.min_verticality_score:
            return None
        if hold_count < self.config.min_hold_count:
            return None

        stop_price = float(segment["low"].min())
        entry_price = decision_close
        risk = entry_price - stop_price
        if risk <= 0.0:
            return None
        tp1_price = entry_price + risk
        oi_change = self._fetch_live_oi_change(symbol, now_ms=now_ms)
        if self.config.min_oi_change_pct_3x5m is not None:
            if oi_change is None or oi_change < self.config.min_oi_change_pct_3x5m:
                self.artifacts.append_event(
                    "reject_oi",
                    symbol,
                    {"oi_change_pct_3x5m": oi_change, "required": self.config.min_oi_change_pct_3x5m},
                )
                return None

        strengths = [
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
        return LiveSignal(
            symbol=symbol,
            decision_timestamp_ms=int(decision["timestamp"]),
            start_timestamp_ms=int(start["timestamp"]),
            session=_session_name(int(decision["timestamp"])),
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            quote_ratio_start=quote_ratio,
            trade_ratio_start=trade_ratio,
            price_retention=price_retention,
            hold_count=hold_count,
            verticality_score=verticality_score,
            oi_change_pct_3x5m=oi_change,
            strengths=strengths,
            weaknesses=weaknesses,
        )

    def _fetch_live_oi_change(self, symbol: str, *, now_ms: int) -> float | None:
        end_ms = now_ms
        start_ms = end_ms - 25 * 60_000
        frame = self.exchange.fetch_open_interest(symbol, Timeframe.M5, start_ms, end_ms)
        if frame.empty or "open_interest" not in frame.columns:
            return None
        frame = frame.sort_values("timestamp").dropna(subset=["timestamp", "open_interest"]).reset_index(drop=True)
        if len(frame) < 4:
            return None
        latest_ts = int(frame.iloc[-1]["timestamp"])
        if latest_ts < now_ms - self.config.oi_fresh_ms:
            return None
        current = float(frame.iloc[-1]["open_interest"])
        previous = float(frame.iloc[-4]["open_interest"])
        return _safe_divide(current - previous, previous)

    def _maybe_open_position(self, signal: LiveSignal) -> None:
        if signal.symbol in self._open_positions:
            return
        if len(self._open_positions) >= self.config.max_open_positions:
            self.artifacts.append_event("reject_max_positions", signal.symbol, {"max": self.config.max_open_positions})
            return
        if self._symbol_in_stop_cooldown(signal.symbol):
            return
        balance = self.exchange.fetch_usdt_free_balance()
        risk_usdt = balance * self.config.risk_pct
        risk_per_unit = signal.entry_price - signal.stop_price
        amount_by_risk = risk_usdt / risk_per_unit
        notional = amount_by_risk * signal.entry_price
        if notional < self.config.min_notional_usdt:
            amount_by_risk = self.config.min_notional_usdt / signal.entry_price
            notional = self.config.min_notional_usdt
        entry_order = self.exchange.create_market_order(signal.symbol, "buy", amount_by_risk)
        entry_order_id = str(entry_order.get("id", ""))
        try:
            stop_order = self.exchange.create_stop_market_order(signal.symbol, "sell", amount_by_risk, signal.stop_price)
        except Exception:
            self.exchange.create_market_order(signal.symbol, "sell", amount_by_risk, reduce_only=True)
            raise
        stop_order_id = str(stop_order.get("id", ""))
        position = LivePosition(
            position_id=f"{signal.symbol.replace('/', '_').replace(':', '_')}_{signal.decision_timestamp_ms}",
            signal=signal,
            amount=float(amount_by_risk),
            notional_usdt=float(notional),
            risk_usdt=float(risk_usdt),
            entry_order_id=entry_order_id,
            stop_order_id=stop_order_id,
            opened_at_utc=datetime.now(UTC).isoformat(),
            remaining_amount=float(amount_by_risk),
        )
        position.telegram_open_message_id = self.telegram.send_sync(
            channel="positions",
            text=_format_open_message(position),
        )
        self._open_positions[signal.symbol] = position
        self.artifacts.append_position(position)
        self.artifacts.append_event("position_opened", signal.symbol, {"position_id": position.position_id})
        self.logger(f"live: открыта позиция {signal.symbol}, риск {risk_usdt:.2f} USDT, notional {notional:.2f} USDT")
        threading.Thread(
            target=self._monitor_position,
            args=(position,),
            name=f"position-{signal.symbol}",
            daemon=True,
        ).start()

    def _monitor_position(self, position: LivePosition) -> None:
        signal = position.signal
        last_stop_price = signal.stop_price
        while True:
            try:
                actual_amount = abs(self.exchange.fetch_symbol_position_amount(signal.symbol))
                if actual_amount <= 0.0:
                    self._finalize_position(position, reason="позиция закрыта на бирже", pnl_price=None)
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
                    position.tp1_done = True
                    position.remaining_amount = max(actual_amount - close_amount, 0.0)
                    try:
                        self.exchange.cancel_order(signal.symbol, position.stop_order_id)
                    except Exception as exc:
                        self.artifacts.append_event("stop_cancel_failed", signal.symbol, {"reason": str(exc)})
                    stop_order = self.exchange.create_stop_market_order(
                        signal.symbol,
                        "sell",
                        max(position.remaining_amount, 0.0),
                        signal.entry_price,
                    )
                    position.stop_order_id = str(stop_order.get("id", position.stop_order_id))
                    last_stop_price = signal.entry_price
                    self.artifacts.append_event("tp1_and_stop_to_be", signal.symbol, {"position_id": position.position_id})
                    self.telegram.send(
                        channel="positions",
                        key=f"tp1:{position.position_id}",
                        reply_to_message_id=position.telegram_open_message_id,
                        text=(
                            "🟢 *TP1 взят*\n"
                            f"*{signal.symbol}*: закрыта половина, стоп перенесён в BE `{signal.entry_price:.6g}`."
                        ),
                    )
                if position.tp1_done:
                    trail_low = float(frame.tail(5)["low"].min())
                    new_stop = max(last_stop_price, trail_low)
                    if new_stop > last_stop_price and new_stop < latest_close:
                        try:
                            self.exchange.cancel_order(signal.symbol, position.stop_order_id)
                        except Exception as exc:
                            self.artifacts.append_event("trail_cancel_failed", signal.symbol, {"reason": str(exc)})
                        stop_order = self.exchange.create_stop_market_order(
                            signal.symbol,
                            "sell",
                            actual_amount,
                            new_stop,
                        )
                        position.stop_order_id = str(stop_order.get("id", position.stop_order_id))
                        last_stop_price = new_stop
                        self.telegram.send(
                            channel="positions",
                            key=f"trail:{position.position_id}:{round(new_stop, 8)}",
                            reply_to_message_id=position.telegram_open_message_id,
                            text=f"🟡 *Стоп подтянут*\n*{signal.symbol}*: новый стоп `{new_stop:.6g}`.",
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
        self._open_positions.pop(position.signal.symbol, None)
        exit_price = pnl_price if pnl_price is not None else position.signal.entry_price
        pnl_pct = _safe_divide(exit_price - position.signal.entry_price, position.signal.entry_price)
        pnl_usdt = pnl_pct * position.notional_usdt if math.isfinite(pnl_pct) else float("nan")
        if reason.startswith("стоп"):
            self._recent_stops.setdefault(position.signal.symbol, []).append(time.time())
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
        self.telegram.send(
            channel="positions",
            key=f"close:{position.position_id}",
            reply_to_message_id=position.telegram_open_message_id,
            text=(
                "🔴 *Позиция закрыта*\n"
                f"*{position.signal.symbol}*: {reason}.\n"
                f"PnL `{pnl_usdt:.2f} USDT` / `{pnl_pct:.2%}`."
            ),
        )

    def _symbol_in_stop_cooldown(self, symbol: str) -> bool:
        cutoff = time.time() - self.config.stop_cooldown_hours * 3600.0
        stops = [ts for ts in self._recent_stops.get(symbol, []) if ts >= cutoff]
        self._recent_stops[symbol] = stops
        return len(stops) >= self.config.stop_limit_per_symbol


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


def _format_open_message(position: LivePosition) -> str:
    signal = position.signal
    strengths = "; ".join(signal.strengths)
    weaknesses = "; ".join(signal.weaknesses) if signal.weaknesses else "критичных слабостей нет"
    return (
        "🟢 *Открыта micro-live позиция*\n"
        f"*{signal.symbol}* · сессия: *{signal.session}*\n"
        f"Вход `{signal.entry_price:.6g}`, стоп `{signal.stop_price:.6g}`, TP1 `{signal.tp1_price:.6g}`\n"
        f"Размер `{position.notional_usdt:.2f} USDT`, риск `{position.risk_usdt:.2f} USDT`\n"
        f"Сильные стороны: {strengths}\n"
        f"Слабые стороны: {weaknesses}"
    )


def _safe_divide(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return float(numerator / denominator)


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
