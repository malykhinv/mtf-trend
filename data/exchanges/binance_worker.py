from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, Generic, Iterable, Optional, Tuple, TypeVar

from config.config import STREAM_METRICS_LOG_INTERVAL_MIN
from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink

from .binance_websocket import (
    ConnectionClosed,
    WebSocketClientProtocol,
    websockets,
)
from .command_budget import CommandBudget
from .events import ResyncReason, StreamEvent, StreamEventType
from .stream_buffer import StreamBuffer
from .stream_consumer import SnapshotFactory, StreamValidationError
from .stream_metrics import StreamMetrics

T = TypeVar("T")


class BinanceStreamWorker(Generic[T]):
    """Dedicated websocket worker used for standalone stream consumption."""

    __slots__ = (
        "_stream",
        "_symbol",
        "_params",
        "_buffer",
        "_parser",
        "_metrics",
        "_silence_timeout_s",
        "_delay_threshold_ms",
        "_log",
        "_budget",
        "_snapshot_factory",
        "_stop_event",
        "_thread",
        "_command_queue",
        "_inflight",
        "_next_command_id",
        "_last_ping",
        "_report_interval",
    )

    _BASE_ENDPOINT = "wss://fstream.binance.com/stream"

    def __init__(
        self,
        *,
        stream: str,
        symbol: str,
        params: Iterable[str],
        buffer: StreamBuffer[T],
        parser: Callable[[Dict[str, Any]], Iterable[StreamEvent[T]]],
        metrics: StreamMetrics,
        silence_timeout_ms: int,
        log_writer: LogSink,
        budget: CommandBudget,
        snapshot_factory: SnapshotFactory[T] = None,
    ) -> None:
        self._stream = stream
        self._symbol = symbol
        self._params = tuple(params)
        self._buffer = buffer
        self._parser = parser
        self._metrics = metrics
        self._silence_timeout_s = max(float(silence_timeout_ms) / 1000.0, 1.0)
        self._delay_threshold_ms = max(float(silence_timeout_ms) / 4.0, 250.0)
        self._log = log_writer
        self._budget = budget
        self._snapshot_factory = snapshot_factory
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"binance-{stream}-{symbol}",
            daemon=True,
        )
        self._command_queue: Deque[tuple[str, Tuple[str, ...], str]] = deque()
        self._inflight: Dict[int, tuple[str, Tuple[str, ...], str]] = {}
        self._next_command_id = 1
        self._last_ping = 0.0
        self._report_interval = timedelta(minutes=STREAM_METRICS_LOG_INTERVAL_MIN)

    def start(self) -> None:
        if websockets is None:
            self._emit_resync(
                ResyncReason.CONNECTION_LOST,
                "websockets package is not available",
                enqueue_resubscribe=False,
            )
            return
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # pragma: no cover - background thread safety
            now = datetime.now(tz=CURRENT_TIMEZONE)
            self._metrics.register_exception(now)
            self._maybe_report(now)
            self._emit_resync(
                ResyncReason.CONNECTION_LOST,
                f"stream {self._stream} crashed: {exc}",
            )

    async def _main(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
            except Exception as exc:  # pragma: no cover - network safety
                if self._stop_event.is_set():
                    break
                details = f"{self._stream} connection lost: {exc}"
                now = datetime.now(tz=CURRENT_TIMEZONE)
                self._metrics.register_exception(now)
                self._maybe_report(now)
                self._emit_resync(ResyncReason.CONNECTION_LOST, details)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _connect_once(self) -> None:
        self._log(f"connecting {self._stream} stream for {self._symbol}")
        try:
            async with websockets.connect(  # type: ignore[union-attr]
                self._BASE_ENDPOINT,
                ping_interval=None,
                close_timeout=5,
            ) as ws:
                await self._on_connected(ws)
                await self._recv_loop(ws)
        except ConnectionClosed as exc:
            if self._stop_event.is_set():
                return
            raise RuntimeError(f"connection closed: {exc}")

    async def _on_connected(self, ws: WebSocketClientProtocol) -> None:
        self._metrics.reconnects += 1
        self._queue_command("SUBSCRIBE", self._params, priority="resync")
        self._last_ping = time.monotonic()
        await self._flush_commands(ws)

    async def _recv_loop(self, ws: WebSocketClientProtocol) -> None:
        while not self._stop_event.is_set():
            await self._flush_commands(ws)
            await self._maybe_send_ping(ws)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._silence_timeout_s)
            except asyncio.TimeoutError:
                self._handle_timeout()
                self._queue_command("SUBSCRIBE", self._params, priority="resync")
                await self._flush_commands(ws)
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
            try:
                events = list(self._parser(data))
            except StreamValidationError as exc:
                self._emit_resync(exc.reason, exc.details, enqueue_resubscribe=exc.resubscribe)
                self._push_snapshot()
                continue
            except Exception as exc:
                now = datetime.now(tz=CURRENT_TIMEZONE)
                self._metrics.register_exception(now)
                self._maybe_report(now)
                detail = f"parser exception: {exc}"
                self._emit_resync(
                    ResyncReason.CONNECTION_LOST,
                    detail,
                    enqueue_resubscribe=True,
                )
                self._push_snapshot()
                continue
            for event in events:
                self._record_latency(event.timestamp)
                self._push_event(event)

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

    def _queue_command(
        self,
        method: str,
        params: Iterable[str],
        *,
        priority: str = "normal",
    ) -> None:
        normalized_method = method.upper()
        normalized_params = tuple(params)
        for queued_method, queued_params, _ in self._command_queue:
            if queued_method == normalized_method and queued_params == normalized_params:
                return
        if normalized_method in {"SUBSCRIBE", "UNSUBSCRIBE"}:
            for inflight_method, inflight_params, _ in self._inflight.values():
                if inflight_method == normalized_method and inflight_params == normalized_params:
                    return
        command = (normalized_method, normalized_params, priority)
        if priority == "resync":
            self._command_queue.appendleft(command)
        else:
            self._command_queue.append(command)

    async def _flush_commands(self, ws: WebSocketClientProtocol) -> None:
        while self._command_queue and not self._stop_event.is_set():
            method, params, priority = self._command_queue[0]
            if not self._budget.consume(priority=priority):
                break
            self._command_queue.popleft()
            command_id = self._next_command_id
            self._next_command_id += 1
            command = {"id": command_id, "method": method, "params": list(params)}
            await ws.send(json.dumps(command))
            self._inflight[command_id] = (method, params, priority)

    def _handle_ack(self, payload: Dict[str, Any]) -> None:
        command_id = int(payload.get("id", -1))
        inflight = self._inflight.pop(command_id, None)
        now = datetime.now(tz=CURRENT_TIMEZONE)
        if inflight is None:
            return
        method, params, _priority = inflight
        error = payload.get("error")
        if error is not None:
            self._emit_resync(
                ResyncReason.CONNECTION_LOST,
                f"command error for {params}: {error}",
            )
        else:
            details = f"{method.lower()} confirmed for {' '.join(params)}"
            event = StreamEvent(StreamEventType.DATA, None, now, details=details)
            self._metrics.register_message(now)
            self._maybe_report(now)
            self._push_event(event)
        if method == "UNSUBSCRIBE" and not self._command_queue:
            self._stop_event.set()

    def _handle_timeout(self) -> None:
        details = f"{self._stream} silence timeout for {self._symbol}"
        self._emit_resync(
            ResyncReason.SILENCE_TIMEOUT,
            details,
            enqueue_resubscribe=True,
        )
        self._push_snapshot()

    def _record_latency(self, timestamp: datetime) -> None:
        now = datetime.now(tz=CURRENT_TIMEZONE)
        latency_ms = max((now - timestamp).total_seconds() * 1000.0, 0.0)
        self._metrics.record_latency(latency_ms, self._delay_threshold_ms)
        self._metrics.register_message(now)
        self._maybe_report(now)

    def _emit_resync(
        self,
        reason: ResyncReason,
        details: str,
        *,
        enqueue_resubscribe: bool = True,
    ) -> None:
        now = datetime.now(tz=CURRENT_TIMEZONE)
        self._metrics.record_resync(reason)
        self._metrics.register_resync(now)
        try:
            self._log(
                f"[{self._stream}:{self._symbol}] {details} ({reason.name})",
            )
        except Exception:  # pragma: no cover - logging failures ignored
            pass
        self._maybe_report(now)
        event = StreamEvent(
            StreamEventType.RESYNC,
            None,
            now,
            reason=reason,
            details=details,
        )
        self._push_event(event)
        if enqueue_resubscribe:
            self._queue_command("SUBSCRIBE", self._params, priority="resync")

    def _push_event(self, event: StreamEvent[T]) -> None:
        appended = self._buffer.append(event)
        if appended:
            return
        self._metrics.record_resync(ResyncReason.QUEUE_OVERFLOW)
        now = datetime.now(tz=CURRENT_TIMEZONE)
        self._metrics.register_resync(now)
        self._maybe_report(now)
        overflow = StreamEvent(
            StreamEventType.RESYNC,
            None,
            now,
            reason=ResyncReason.QUEUE_OVERFLOW,
            details=f"buffer overflow on {self._stream} stream",
        )
        self._buffer.append(overflow)

    def _push_snapshot(self) -> None:
        if self._snapshot_factory is None:
            return
        try:
            snapshot_result = self._snapshot_factory()
        except StreamValidationError as exc:
            self._emit_resync(
                exc.reason,
                exc.details,
                enqueue_resubscribe=exc.resubscribe,
            )
            return
        if snapshot_result is None:
            return
        snapshot_event, replay_events = snapshot_result
        if snapshot_event is not None:
            self._push_event(snapshot_event)
        replay_list = list(replay_events)
        if replay_list:
            self._buffer.extend(replay_list)

    def _maybe_report(self, now: datetime) -> None:
        summary = self._metrics.consume_report(now, self._report_interval)
        if summary is None:
            return
        messages, resyncs, exceptions = summary
        report = (
            f"[{self._stream}:{self._symbol}] Сводка за "
            f"{STREAM_METRICS_LOG_INTERVAL_MIN}м: "
            f"сообщений={messages}, ресинки={resyncs}, исключения={exceptions}"
        )
        try:
            self._log(report)
        except Exception:  # pragma: no cover - logging failures ignored
            pass


__all__ = ["BinanceStreamWorker"]
