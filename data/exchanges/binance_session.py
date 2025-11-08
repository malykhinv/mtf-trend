from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque, Dict, Generic, Mapping, Optional, Tuple, TypeVar

from config.config import (
    MAX_RESUBSCRIBE_PER_CYCLE,
    POLICY_VIOLATION_GLOBAL_COOLDOWN_S,
    POLICY_VIOLATION_STREAM_COOLDOWN_S,
    STREAM_METRICS_LOG_INTERVAL_MIN,
)
from data.logger import LogSink
from .binance_websocket import WebSocketClientProtocol, websockets
from .command_budget import CommandBudget
from .limits import StreamLimit
from .stream_consumer import StreamConsumer
from .stream_subscription import StreamLimitError, StreamSubscription

T = TypeVar("T")


@dataclass(slots=True)
class SessionCommand(Generic[T]):
    method: str
    params: Tuple[str, ...]
    priority: float
    consumer: StreamConsumer[T]
    use_reserve: bool = False


@dataclass(slots=True)
class ResubscribeCooldownState:
    attempts: int
    next_allowed_at: datetime
    last_failure_at: datetime


@dataclass(slots=True)
class StreamRegistration(Generic[T]):
    consumer: StreamConsumer[T]
    subscription: StreamSubscription[T]


class BinanceStreamSession:
    """Shared websocket session that fans out events to registered consumers."""

    _BASE_ENDPOINT = "wss://fstream.binance.com/stream"
    _global_policy_lock = threading.Lock()
    _global_policy_violation_until = 0.0

    def __init__(
            self,
            *,
            stream: str,
            limit: StreamLimit,
            silence_timeout_ms: int,
            log_writer: LogSink,
    ) -> None:
        self._stream = stream
        self._limit = limit
        self._log = log_writer
        self._silence_timeout_s = max(float(silence_timeout_ms) / 1000.0, 1.0)
        self._budget = CommandBudget(limit)
        self._max_weight = float(limit.max_weight)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"binance-session-{stream}",
            daemon=True,
        )
        self._command_queue: list[tuple[float, int, SessionCommand[Any]]] = []
        self._command_lock = threading.Lock()
        self._sequence = 0
        self._inflight: Dict[int, SessionCommand[Any]] = {}
        self._next_command_id = 1
        self._consumers: Dict[str, StreamConsumer[Any]] = {}
        self._symbol_params: Dict[str, str] = {}
        self._param_weights: Dict[str, float] = {}
        self._total_weight = 0.0
        self._usable_capacity = self._compute_capacity(limit)
        self._last_ping = 0.0
        self._last_rate_limit_log = 0.0
        self._status_report_interval = float(STREAM_METRICS_LOG_INTERVAL_MIN) * 60.0
        self._last_status_log = 0.0
        self._pending_resubscribe: Deque[SessionCommand[Any]] = deque()
        self._resubscribe_quota_total = max(int(MAX_RESUBSCRIBE_PER_CYCLE), 0)
        self._resubscribe_quota = self._resubscribe_quota_total
        self._policy_violation_until = 0.0

    @staticmethod
    def _compute_capacity(limit: StreamLimit) -> int:
        if limit.max_symbols <= 0:
            return 0
        reserve = max(0, limit.resubscribe_buffer)
        usable = max(limit.max_symbols - reserve, 0)
        return usable if usable > 0 else limit.max_symbols

    def start(self) -> None:
        if websockets is None:
            return
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def max_capacity(self) -> int:
        return self._usable_capacity

    def available_capacity(self) -> float:
        if self._max_weight > 0:
            remaining = self._max_weight - self._total_weight
            return max(remaining, 0.0)
        if self._usable_capacity <= 0:
            return float("inf")
        return float(max(self._usable_capacity - len(self._consumers), 0))

    def available_symbols(self) -> float:
        if self._usable_capacity <= 0:
            return float("inf")
        return float(max(self._usable_capacity - len(self._consumers), 0))

    def diagnostic_state(self) -> Dict[str, Any]:
        with self._command_lock:
            queue_len = len(self._command_queue)
            inflight = len(self._inflight)
            total_params = len(self._consumers)
            symbol_count = len(self._symbol_params)
        weight_left = float("inf")
        if self._max_weight > 0:
            weight_left = max(self._max_weight - self._total_weight, 0.0)
        budget_state = self._budget.debug_state()
        return {
            "queue": queue_len,
            "inflight": inflight,
            "params": total_params,
            "symbols": symbol_count,
            "weight_left": float(weight_left),
            "capacity_left": float(self.available_symbols()),
            "budget": budget_state,
        }

    def can_accept(self, weight: float | None = None) -> tuple[bool, str, Dict[str, Any]]:
        weight_value = max(float(weight or 0.0), 0.0)
        with self._command_lock:
            consumers = len(self._consumers)
        capacity_left = float(self.available_symbols())
        weight_left = float("inf")
        if self._max_weight > 0:
            weight_left = max(self._max_weight - self._total_weight, 0.0)
        if self._usable_capacity > 0 and capacity_left <= 0:
            return False, "capacity", {
                "capacity_left": capacity_left,
                "weight_left": weight_left,
            }
        if self._max_weight > 0 and weight_value - weight_left > 1e-9:
            return False, "weight", {
                "capacity_left": capacity_left,
                "weight_left": weight_left,
            }
        return True, "ok", {
            "capacity_left": capacity_left,
            "weight_left": weight_left,
        }

    def register_consumer(
            self,
            consumer: StreamConsumer[Any],
            *,
            priority: float,
            weight: float,
            use_reserve: bool = False,
    ) -> None:
        params = consumer.params
        weight = max(float(weight), 0.0)
        with self._command_lock:
            for param in params:
                if param in self._consumers:
                    continue
                if self._usable_capacity > 0 and len(self._consumers) >= self._usable_capacity:
                    symbol = getattr(consumer, "symbol", "unknown")
                    msg = (
                        f"[{self._stream}] Лимит символов: {symbol} не может быть добавлен. "
                        f"Занято {len(self._consumers)}/{self._usable_capacity}."
                    )
                    self._log(msg)
                    raise StreamLimitError("max stream capacity reached")
                if self._max_weight > 0:
                    projected = self._total_weight + weight
                    if projected - self._max_weight > 1e-9:
                        symbol = getattr(consumer, "symbol", "unknown")
                        msg = (
                            f"[{self._stream}] Лимит веса: {symbol} требует {weight:.1f}, "
                            f"доступно {self._max_weight - self._total_weight:.1f} "
                            f"из {self._max_weight:.1f}."
                        )
                        self._log(msg)
                        raise StreamLimitError("max stream weight reached")
                self._consumers[param] = consumer
                self._symbol_params[consumer.symbol] = param
                self._param_weights[param] = weight
                self._total_weight += weight
                command = SessionCommand(
                    "SUBSCRIBE",
                    (param,),
                    priority,
                    consumer,
                    use_reserve=use_reserve,
                )
                self._enqueue_command(command)

    def unregister_consumer(
            self,
            consumer: StreamConsumer[Any],
            *,
            priority: float,
    ) -> None:
        params = tuple(param for param, entry in self._consumers.items() if entry is consumer)
        if not params:
            return
        with self._command_lock:
            for param in params:
                command = SessionCommand("UNSUBSCRIBE", (param,), priority, consumer)
                self._enqueue_command(command)

    def resubscribe_consumer(
            self,
            consumer: StreamConsumer[Any],
            *,
            priority: float,
    ) -> None:
        now = time.monotonic()
        local_remaining = max(self._policy_violation_until - now, 0.0)
        global_remaining = self._global_cooldown_remaining(now)
        remaining = max(local_remaining, global_remaining)
        if remaining > 0.0:
            try:
                self._log(
                    (
                        f"[{self._stream}] Ресабскрипция {consumer.symbol} отложена: "
                        f"policy cooldown ещё {remaining:.1f}с"
                    )
                )
            except Exception:
                pass
            return
        params = tuple(param for param, entry in self._consumers.items() if entry is consumer)
        if not params:
            return
        with self._command_lock:
            for param in params:
                command = SessionCommand(
                    "SUBSCRIBE",
                    (param,),
                    priority,
                    consumer,
                    use_reserve=True,
                )
                quota_exhausted = (
                        self._resubscribe_quota_total > 0
                        and self._resubscribe_quota <= 0
                )
                if quota_exhausted or self._budget.reserve_remaining() <= 0:
                    self._pending_resubscribe.append(command)
                    try:
                        self._log(
                            (
                                f"[{self._stream}] Ресинк {consumer.symbol} отложен:"
                                " лимит пересабскрипций или reserve исчерпан."
                            )
                        )
                    except Exception:
                        pass
                    continue
                self._enqueue_command(command, allow_duplicates=True)
                if self._resubscribe_quota_total > 0:
                    self._resubscribe_quota = max(self._resubscribe_quota - 1, 0)

    def _enqueue_command(
            self,
            command: SessionCommand[Any],
            *,
            allow_duplicates: bool = False,
    ) -> None:
        if not allow_duplicates:
            for _, _, queued in self._command_queue:
                if (
                        queued.method == command.method
                        and queued.params == command.params
                ):
                    return
            for inflight in self._inflight.values():
                if (
                        inflight.method == command.method
                        and inflight.params == command.params
                ):
                    return
        score = -float(command.priority)
        self._sequence += 1
        self._command_queue.append((score, self._sequence, command))
        try:
            queue_len = len(self._command_queue)
            budget_state = self._budget.debug_state()
            self._log(
                (
                    f"[{self._stream}] Команда {command.method} {command.params}"
                    f" поставлена в очередь. Размер очереди={queue_len},"
                    f" inflight={len(self._inflight)},"
                    f" бюджет={budget_state}."
                )
            )
        except Exception:
            pass

    async def _flush_commands(self, ws: WebSocketClientProtocol) -> None:
        quota_initialized = False
        while not self._stop_event.is_set():
            with self._command_lock:
                if not quota_initialized and self._resubscribe_quota_total > 0:
                    self._resubscribe_quota = self._resubscribe_quota_total
                    quota_initialized = True
                while self._pending_resubscribe and self._budget.reserve_remaining() > 0:
                    command = self._pending_resubscribe.popleft()
                    if self._resubscribe_quota_total > 0 and self._resubscribe_quota <= 0:
                        self._pending_resubscribe.appendleft(command)
                        break
                    self._enqueue_command(command, allow_duplicates=True)
                    if self._resubscribe_quota_total > 0:
                        self._resubscribe_quota = max(self._resubscribe_quota - 1, 0)
            if not self._command_queue:
                break
            self._command_queue.sort()
            score, seq, command = self._command_queue[0]
            budget_priority = "resync" if command.use_reserve else "normal"
            if not self._budget.consume(priority=budget_priority):
                delay = self._budget.failure_delay()
                now = time.monotonic()
                if now - self._last_rate_limit_log > 30.0:
                    symbol = getattr(command.consumer, "symbol", "unknown")
                    queue_len = len(self._command_queue)
                    budget_state = self._budget.debug_state()
                    log_msg = (
                        f"[{self._stream}] Rate limit: {command.method} для {symbol} "
                        f"заблокирован на {delay:.1f}с. "
                        f"В очереди {queue_len} команд(а/ы). "
                        f"Статус бюджета: {budget_state}. "
                    )
                    self._log(log_msg)
                    self._last_rate_limit_log = now
                if delay > 0.0:
                    await asyncio.sleep(delay)
                break
            self._command_queue.pop(0)
            command_id = self._next_command_id
            self._next_command_id += 1
            payload = {
                "id": command_id,
                "method": command.method,
                "params": list(command.params),
            }
            await ws.send(json.dumps(payload))
            self._inflight[command_id] = command
            try:
                queue_len = len(self._command_queue)
                self._log(
                    (
                        f"[{self._stream}] Отправлена команда {command.method}"
                        f" {command.params} (приоритет={command.priority:.1f})."
                        f" Очередь после отправки={queue_len}."
                    )
                )
            except Exception:
                pass

    async def _maybe_send_ping(self, ws: WebSocketClientProtocol) -> None:
        now = time.monotonic()
        interval = max(self._silence_timeout_s / 2.0, 10.0)
        if now - self._last_ping < interval:
            return
        try:
            await ws.ping()
            self._last_ping = now
        except Exception as exc:
            raise RuntimeError(f"ping failed: {exc}")

    def _maybe_log_status(self) -> None:
        interval = self._status_report_interval
        if interval <= 0.0:
            return
        now = time.monotonic()
        if now - self._last_status_log < interval:
            return
        with self._command_lock:
            total_params = len(self._consumers)
            symbol_count = len(self._symbol_params)
            queue_len = len(self._command_queue)
            inflight = len(self._inflight)
            total_weight = self._total_weight
            max_weight = self._max_weight
            usable_capacity = self._usable_capacity
            reserve_buffer = getattr(self._limit, "resubscribe_buffer", 0)
        if usable_capacity > 0:
            capacity_info = f"{total_params}/{usable_capacity}"
            available_slots_str = str(max(usable_capacity - total_params, 0))
        else:
            capacity_info = "без ограничений"
            available_slots_str = "∞"
        if max_weight > 0.0:
            weight_info = f"{total_weight:.1f}/{max_weight:.1f}"
            available_weight_str = f"{max(max_weight - total_weight, 0.0):.1f}"
        else:
            weight_info = "без ограничений"
            available_weight_str = "∞"
        status = (
            f"[{self._stream}] Статус потоков: символов={symbol_count}, подписок={total_params}, "
            f"очередь={queue_len}, inflight={inflight}, емкость={capacity_info} "
            f"(доступно {available_slots_str}), вес={weight_info} "
            f"(доступно {available_weight_str}), резерв_ресинка={reserve_buffer}."
        )
        self._log(status)
        self._last_status_log = now

    async def _recv_loop(self, ws: WebSocketClientProtocol) -> None:
        while not self._stop_event.is_set():
            await self._flush_commands(ws)
            await self._maybe_send_ping(ws)
            self._maybe_log_status()
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._silence_timeout_s)
            except asyncio.TimeoutError:
                self._handle_timeout()
                continue
            payload = self._decode_payload(raw)
            if payload is None:
                continue
            if "id" in payload and "result" in payload:
                self._handle_ack(payload)
                continue
            data = payload.get("data", payload)
            if not isinstance(data, dict):
                continue
            consumer = self._resolve_consumer(payload, data)
            if consumer is None:
                continue
            try:
                needs_resubscribe = consumer.process(data)
            except Exception as exc:
                detail = f"исключение обработчика: {exc}"
                enqueue = consumer.handle_exception(detail)
                if enqueue:
                    self.resubscribe_consumer(consumer, priority=float("inf"))
                continue
            if needs_resubscribe:
                self.resubscribe_consumer(consumer, priority=consumer.metrics.messages + 1.0)

    def _handle_timeout(self) -> None:
        consumers = list(dict.fromkeys(self._consumers.values()))
        for consumer in consumers:
            if consumer.handle_timeout():
                self.resubscribe_consumer(consumer, priority=float("inf"))

    def _decode_payload(self, raw: Any) -> Optional[Dict[str, Any]]:
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(payload, dict):
                return payload
            return None
        if isinstance(raw, dict):
            return raw
        return None

    def _resolve_consumer(
            self,
            envelope: Mapping[str, Any],
            data: Mapping[str, Any],
    ) -> Optional[StreamConsumer[Any]]:
        stream_id = envelope.get("stream")
        if isinstance(stream_id, str):
            return self._consumers.get(stream_id)
        symbol = data.get("s")
        if isinstance(symbol, str):
            param = self._symbol_params.get(symbol.upper())
            if param is not None:
                return self._consumers.get(param)
        return None

    def _handle_ack(self, payload: Dict[str, Any]) -> None:
        command_id = int(payload.get("id", -1))
        command = self._inflight.pop(command_id, None)
        if command is None:
            return
        error = payload.get("error")
        consumer = command.consumer
        needs_resubscribe = consumer.handle_ack(command.method, command.params, error)
        if command.method == "SUBSCRIBE" and error is not None:
            code = None
            if isinstance(error, Mapping):
                code = error.get("code")
            if code in {1008, 1013}:
                self._activate_policy_violation()
                try:
                    self._log(
                        f"[{self._stream}] Policy violation ack для {consumer.symbol}: code={code}"
                    )
                except Exception:
                    pass
            for param in command.params:
                weight = self._param_weights.pop(param, 0.0)
                if weight > 0.0:
                    self._total_weight = max(self._total_weight - weight, 0.0)
                if self._symbol_params.get(consumer.symbol) == param:
                    self._symbol_params.pop(consumer.symbol, None)
        if needs_resubscribe:
            self.resubscribe_consumer(consumer, priority=float("inf"))

    def _run(self) -> None:
        if websockets is None:  # pragma: no cover - network optional
            return
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._main())
                backoff = 1.0
            except Exception as exc:  # pragma: no cover - connection safety
                if self._stop_event.is_set():
                    break
                try:
                    self._log(f"[{self._stream}:session] connection lost: {exc}")
                except Exception:  # pragma: no cover - logging safety
                    pass
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _main(self) -> None:
        self._last_ping = time.monotonic()
        async with websockets.connect(  # type: ignore[union-attr]
                self._BASE_ENDPOINT,
                ping_interval=None,
                close_timeout=5,
        ) as ws:
            self._policy_violation_until = 0.0
            self._resubscribe_quota = self._resubscribe_quota_total
            await self._on_connected(ws)
            await self._recv_loop(ws)

    @classmethod
    def _global_cooldown_remaining(cls, now: float) -> float:
        with cls._global_policy_lock:
            return max(cls._global_policy_violation_until - now, 0.0)

    def _activate_policy_violation(self) -> None:
        now = time.monotonic()
        if POLICY_VIOLATION_STREAM_COOLDOWN_S > 0:
            stream_until = now + float(POLICY_VIOLATION_STREAM_COOLDOWN_S)
            self._policy_violation_until = max(self._policy_violation_until, stream_until)
        if POLICY_VIOLATION_GLOBAL_COOLDOWN_S <= 0:
            return
        global_until = now + float(POLICY_VIOLATION_GLOBAL_COOLDOWN_S)
        cls = type(self)
        with cls._global_policy_lock:
            cls._global_policy_violation_until = max(
                cls._global_policy_violation_until,
                global_until,
            )

    async def _on_connected(self, ws: WebSocketClientProtocol) -> None:
        with self._command_lock:
            self._command_queue.clear()
            self._inflight.clear()
            self._last_status_log = time.monotonic() - self._status_report_interval
        consumers: list[StreamConsumer[Any]] = []
        seen_ids: set[int] = set()
        for consumer in self._consumers.values():
            consumer_id = id(consumer)
            if consumer_id in seen_ids:
                continue
            seen_ids.add(consumer_id)
            consumers.append(consumer)
        for consumer in consumers:
            for param in consumer.params:
                command = SessionCommand(
                    "SUBSCRIBE",
                    (param,),
                    priority=float("inf"),
                    consumer=consumer,
                    use_reserve=True,
                )
                self._enqueue_command(command, allow_duplicates=True)
        await self._flush_commands(ws)


__all__ = [
    "BinanceStreamSession",
    "StreamRegistration",
    "SessionCommand",
    "ResubscribeCooldownState",
]
