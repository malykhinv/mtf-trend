import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from utils.async_websocket import AsyncWebSocketClient, WebSocketTimeoutError
from utils.metrics import METRICS

from .base import ExchangeLogger, ResyncReason, StreamBuffer
from .stream_scheduler import (
    CommandResult,
    CommandRetryPolicy,
    StreamCommandScheduler,
)

from config.stream_limits import (
    DEFAULT_BINANCE_STREAM_PROFILES,
    StreamLoadProfile,
)

@dataclass(slots=True)
class _Registration:
    symbol: str
    buffer: StreamBuffer[Any]
    on_message: Callable[[dict[str, Any]], None]
    on_error: Callable[[ResyncReason, str], None]
    weight: float


@dataclass(slots=True)
class _Binding:
    symbol: str
    buffer: StreamBuffer[Any]
    on_message: Callable[[dict[str, Any]], None]
    on_error: Callable[[ResyncReason, str], None]
    weight: float
    worker: "_CombinedStreamWorker"
    release_wrapper: Callable[[], None]


class _RateWindow:
    def __init__(self, window: float, limit: int, reserve: int) -> None:
        self._window = max(float(window), 0.01)
        self._limit = max(int(limit), 1)
        self._reserve = max(0, min(int(reserve), self._limit - 1))
        self._timestamps: deque[float] = deque()

    @property
    def window(self) -> float:
        return self._window

    @property
    def limit(self) -> int:
        return self._limit

    def effective_limit(self) -> int:
        return max(min(self._limit, self._limit - self._reserve), 1)

    def prune(self, now: float) -> None:
        boundary = now - self._window
        while self._timestamps and self._timestamps[0] <= boundary:
            self._timestamps.popleft()

    def register(self, timestamp: float) -> None:
        self.prune(timestamp)
        self._timestamps.append(timestamp)

    def required_delay(self, now: float) -> float:
        self.prune(now)
        limit = self.effective_limit()
        if len(self._timestamps) < limit:
            return 0.0
        earliest_allowed = self._timestamps[0] + self._window
        return max(0.0, earliest_allowed - now)


class _CommandRateLimiter:
    def __init__(self, profile: StreamLoadProfile, logger: ExchangeLogger, name: str) -> None:
        self._steady = _RateWindow(
            profile.steady.window_seconds,
            profile.steady.command_limit,
            profile.minimum_command_reserve,
        )
        self._burst = _RateWindow(
            profile.burst.window_seconds,
            profile.burst.command_limit,
            profile.minimum_command_reserve,
        )
        self._logger = logger
        self._name = name
        self._last_command_timestamp = 0.0

    async def throttle(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            delay, window = self._required_delay(now)
            if delay <= 0.0:
                return
            limit = window.limit if window is not None else 0
            window_size = window.window if window is not None else 0.0
            self._logger.log(
                (
                    "Binance {stream} stream: достигнут предел {limit} команд за {window:.2f}с, "
                    "ожидаем {sleep:.2f}с"
                ).format(
                    stream=self._name,
                    limit=limit,
                    window=window_size,
                    sleep=delay,
                )
            )
            await asyncio.sleep(delay)

    def _required_delay(self, now: float) -> tuple[float, _RateWindow | None]:
        windows = (self._steady, self._burst)
        delays = [(window.required_delay(now), window) for window in windows]
        delay, window = max(delays, key=lambda item: item[0])
        if delay <= 0.0:
            return 0.0, None
        return delay, window

    def record(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        for window in (self._steady, self._burst):
            window.register(now)
        self._last_command_timestamp = now

    def resubscribe_delay(self) -> float:
        limit = self._steady.effective_limit()
        if limit <= 0:
            return 0.05
        return max(0.05, self._steady.window / limit)

    @property
    def last_command_timestamp(self) -> float:
        return self._last_command_timestamp


class _CombinedStreamWorker:
    def __init__(
        self,
        name: str,
        ws_base: str,
        stream_suffix: str,
        *,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]] = None,
        max_streams: int,
        command_profile: StreamLoadProfile,
        on_degraded: Callable[["_CombinedStreamWorker", str, str], None],
    ) -> None:
        self._name = name
        self._ws_base = ws_base
        self._stream_suffix = stream_suffix
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
        self._max_streams = max_streams
        self._lock = threading.Lock()
        self._update_event = threading.Event()
        self._stop_event = threading.Event()
        self._registrations: Dict[str, _Registration] = {}
        self._logger = ExchangeLogger(f"Binance pool:{name}", log_writer)
        self._command_queue: "Queue[tuple[str, str]]" = Queue()
        self._command_limiter = _CommandRateLimiter(command_profile, self._logger, name)
        self._last_resubscribe: Dict[str, float] = {}
        self._on_degraded = on_degraded
        self._scheduler = StreamCommandScheduler(
            logger=self._logger,
            metrics_sink=self._publish_metric,
        )
        self._subscribe_retry = CommandRetryPolicy(max_attempts=4, delay_seconds=0.25, backoff_multiplier=2.0)
        self._unsubscribe_retry = CommandRetryPolicy(max_attempts=3, delay_seconds=0.25, backoff_multiplier=1.5)
        self._resubscribe_retry = CommandRetryPolicy(max_attempts=5, delay_seconds=0.5, backoff_multiplier=1.5)
        self._property_retry = CommandRetryPolicy(max_attempts=3, delay_seconds=0.5, backoff_multiplier=2.0)
        self._worker_label = f"{name}-{id(self):x}"
        self._thread = threading.Thread(
            target=self._run_thread,
            name=f"binance-pool-{self._worker_label}",
            daemon=True,
        )
        self._thread.start()

    def _run_thread(self) -> None:
        asyncio.run(self._run())

    def _publish_metric(self, name: str, payload: Mapping[str, Any]) -> None:
        values = payload.get("values", {}) if isinstance(payload, Mapping) else {}
        tags = payload.get("tags", {}) if isinstance(payload, Mapping) else {}
        normalized_tags = {str(key): str(value) for key, value in tags.items()}
        command_name = normalized_tags.get("command", "command")
        latency = values.get("latency")
        numeric_latency_val: Optional[float] = None
        if latency is not None:
            try:
                numeric_latency_val = float(latency)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                numeric_latency_val = 0.0
            self._logger.metric(
                f"{self._name}.{command_name}.latency",
                numeric_latency_val,
                tags=normalized_tags,
            )
        attempts = values.get("attempts")
        numeric_attempts_val: Optional[float] = None
        if attempts is not None:
            try:
                numeric_attempts_val = float(attempts)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                numeric_attempts_val = 0.0
            self._logger.metric(
                f"{self._name}.{command_name}.attempts",
                numeric_attempts_val,
                tags=normalized_tags,
            )
        error = values.get("error")
        if error:
            self._logger.log(
                (
                    "Binance {stream} stream: ошибка команды {command}: {error}"  # noqa: ISC003
                ).format(stream=self._name, command=command_name, error=error)
            )
        if numeric_latency_val is not None:
            attempts_value = numeric_attempts_val if numeric_attempts_val is not None else 0.0
            METRICS.observe_command_latency(
                self._name,
                normalized_tags,
                float(numeric_latency_val),
                float(attempts_value),
            )

    def register(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
        *,
        weight: float,
    ) -> Callable[[], None]:
        symbol = symbol.upper()

        if on_error is None:
            def default_error(reason: ResyncReason, details: str) -> None:
                buffer.push_resync(reason, details)

            on_error = default_error

        registration = _Registration(
            symbol=symbol,
            buffer=buffer,
            on_message=on_message,
            on_error=on_error,
            weight=max(float(weight), 0.0),
        )

        with self._lock:
            if symbol in self._registrations:
                raise ValueError(f"Stream {self._name} already registered for {symbol}")
            if len(self._registrations) >= self._max_streams:
                raise ValueError(
                    f"Stream {self._name} достиг предела в {self._max_streams} символов"
                )
            self._registrations[symbol] = registration
            current = len(self._registrations)
            self._update_event.set()

        self._logger.log(
            (
                "Binance {stream} stream: добавлен символ {symbol}, "
                "активных {count}/{limit}"
            ).format(
                stream=self._name,
                symbol=symbol,
                count=current,
                limit=self._max_streams,
            )
        )
        self._update_active_metric(current)

        released = threading.Event()
        self._command_queue.put(("subscribe", symbol))

        def release() -> None:
            if released.is_set():
                return
            with self._lock:
                self._registrations.pop(symbol, None)
                current = len(self._registrations)
                self._update_event.set()
            released.set()
            self._command_queue.put(("unsubscribe", symbol))
            self._logger.log(
                (
                    "Binance {stream} stream: удален символ {symbol}, "
                    "активных {count}/{limit}"
                ).format(
                    stream=self._name,
                    symbol=symbol,
                    count=current,
                    limit=self._max_streams,
                )
            )
            self._update_active_metric(current)

        return release

    def has_capacity(self) -> bool:
        with self._lock:
            return len(self._registrations) < self._max_streams and not self._stop_event.is_set()

    def is_empty(self) -> bool:
        with self._lock:
            return not self._registrations

    def registration_count(self) -> int:
        with self._lock:
            return len(self._registrations)

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._logger.log(
            f"Binance {self._name} stream: остановка, активных символов нет"
        )
        self._stop_event.set()
        self._update_event.set()
        self._update_active_metric(0)

    def _update_active_metric(self, count: Optional[int] = None) -> None:
        if count is None:
            with self._lock:
                count = len(self._registrations)
        METRICS.set_active_symbols(self._name, self._worker_label, int(count))

    def _current_symbols(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._registrations.keys())

    def _get_registration(self, symbol: str) -> Optional[_Registration]:
        with self._lock:
            return self._registrations.get(symbol)

    def _restart_requested(self) -> None:
        with self._lock:
            registrations = list(self._registrations.values())

        processed: list[tuple[str, ResyncReason]] = []
        suppressed: list[str] = []
        now = time.monotonic()
        for registration in registrations:
            reason = registration.buffer.consume_restart_request()
            if reason is None:
                continue

            actual_reason = reason or ResyncReason.CONNECTION_LOST
            grace = max(registration.buffer.restart_grace_period(), 0.0)
            last_request = self._last_resubscribe.get(registration.symbol)
            if last_request is not None and now - last_request < grace:
                suppressed.append(registration.symbol)
                continue

            self._last_resubscribe[registration.symbol] = now
            processed.append((registration.symbol, actual_reason))
            self._command_queue.put(("resubscribe", registration.symbol))
            details = (
                "Binance {stream} stream: {reason} для {symbol}, "
                "запрос повторной синхронизации"
            ).format(
                stream=self._name,
                reason=(
                    "таймаут тишины"
                    if actual_reason == ResyncReason.SILENCE_TIMEOUT
                    else "обрыв соединения"
                ),
                symbol=registration.symbol,
            )

            try:
                registration.on_error(actual_reason, details)
            except Exception:
                pass

        if processed:
            joined = ", ".join(
                f"{symbol} ({reason.name})" for symbol, reason in processed
            )
            self._logger.log(
                (
                    "Binance {stream} stream: обработан запрос повторной "
                    "синхронизации из буферов: {symbols}"
                ).format(stream=self._name, symbols=joined)
            )
        if suppressed:
            joined = ", ".join(suppressed)
            self._logger.log(
                (
                    "Binance {stream} stream: проигнорированы повторные запросы "
                    "синхронизации: {symbols}"
                ).format(stream=self._name, symbols=joined)
            )

    def _notify_all(self, reason: ResyncReason, details: str) -> None:
        with self._lock:
            registrations = list(self._registrations.values())
        for registration in registrations:
            try:
                detailed_message = f"{details} (symbol {registration.symbol})"
                registration.on_error(reason, detailed_message)
            except Exception:
                pass

    def _extract_symbol(self, payload: dict[str, Any]) -> Optional[str]:
        data = payload.get("data")
        symbol = None
        if isinstance(data, dict):
            raw_symbol = data.get("s")
            if isinstance(raw_symbol, str):
                symbol = raw_symbol
        if symbol is None:
            raw_stream = payload.get("stream")
            if isinstance(raw_stream, str):
                symbol = raw_stream.split("@", 1)[0]
        if symbol is None and isinstance(payload.get("s"), str):
            symbol = str(payload.get("s")).upper()
        if symbol is None:
            return None
        return symbol.upper()

    def _stream_name(self, symbol: str) -> str:
        return f"{symbol.lower()}@{self._stream_suffix}"

    async def _throttle_if_needed(self) -> None:
        await self._command_limiter.throttle()

    def _resubscribe_delay(self) -> float:
        return self._command_limiter.resubscribe_delay()

    async def _process_commands(
        self,
        client: AsyncWebSocketClient,
        active_symbols: set[str],
    ) -> None:
        resubscribe_offset = 0.0
        while True:
            try:
                action, symbol = self._command_queue.get_nowait()
            except Empty:
                break
            if action == "subscribe":
                self._schedule_subscribe(symbol, active_symbols)
            elif action == "unsubscribe":
                self._schedule_unsubscribe(symbol, active_symbols)
            elif action == "resubscribe":
                self._schedule_resubscribe(symbol, active_symbols, resubscribe_offset)
                resubscribe_offset += self._resubscribe_delay()
        await self._scheduler.dispatch(
            throttle=self._throttle_if_needed,
            send=client.send_json,
            after_send=lambda _: self._command_limiter.record(),
        )

    def _schedule_subscribe(self, symbol: str, active_symbols: set[str]) -> None:
        normalized = symbol.upper()
        if self._get_registration(normalized) is None or normalized in active_symbols:
            return
        stream_name = self._stream_name(normalized)
        self._scheduler.schedule(
            "SUBSCRIBE",
            [stream_name],
            timeout=self._ws_timeout,
            retry_policy=self._subscribe_retry,
            metadata={"type": "subscribe", "symbols": (normalized,)},
        )

    def _schedule_unsubscribe(self, symbol: str, active_symbols: set[str]) -> None:
        normalized = symbol.upper()
        if normalized not in active_symbols:
            return
        stream_name = self._stream_name(normalized)
        self._scheduler.schedule(
            "UNSUBSCRIBE",
            [stream_name],
            timeout=self._ws_timeout,
            retry_policy=self._unsubscribe_retry,
            metadata={"type": "unsubscribe", "symbols": (normalized,)},
        )

    def _schedule_resubscribe(
        self,
        symbol: str,
        active_symbols: set[str],
        offset: float,
    ) -> None:
        normalized = symbol.upper()
        if self._get_registration(normalized) is None:
            return
        stream_name = self._stream_name(normalized)
        if normalized in active_symbols:
            self._scheduler.schedule(
                "UNSUBSCRIBE",
                [stream_name],
                timeout=self._ws_timeout,
                retry_policy=self._unsubscribe_retry,
                metadata={"type": "resubscribe", "phase": "unsubscribe", "symbols": (normalized,)},
            )
            active_symbols.discard(normalized)
        delay = offset + self._resubscribe_delay()
        self._scheduler.schedule(
            "SUBSCRIBE",
            [stream_name],
            timeout=self._ws_timeout,
            retry_policy=self._resubscribe_retry,
            metadata={"type": "resubscribe", "phase": "subscribe", "symbols": (normalized,)},
            delay=delay,
        )

    def _prime_connection(self, symbols: Iterable[str]) -> None:
        streams: list[str] = []
        metadata_symbols: list[str] = []
        for symbol in symbols:
            normalized = symbol.upper()
            if self._get_registration(normalized) is None:
                continue
            streams.append(self._stream_name(normalized))
            metadata_symbols.append(normalized)
        if not streams:
            return
        self._scheduler.schedule(
            "SET_PROPERTY",
            ["combined", True],
            timeout=self._ws_timeout,
            retry_policy=self._property_retry,
            metadata={"type": "set_property", "symbols": ()},
        )
        self._scheduler.schedule(
            "SUBSCRIBE",
            streams,
            timeout=self._ws_timeout,
            retry_policy=self._subscribe_retry,
            metadata={"type": "subscribe", "phase": "initial", "symbols": tuple(metadata_symbols)},
        )

    def _metadata_symbols(self, metadata: Mapping[str, Any]) -> tuple[str, ...]:
        symbols = metadata.get("symbols")
        if isinstance(symbols, str):
            return (symbols.upper(),)
        if isinstance(symbols, (list, tuple)):
            return tuple(str(item).upper() for item in symbols)
        return ()

    def _handle_command_result(
        self,
        result: CommandResult,
        active_symbols: set[str],
    ) -> None:
        metadata = result.command.metadata if isinstance(result.command.metadata, Mapping) else {}
        command_type = metadata.get("type")
        symbols = self._metadata_symbols(metadata)
        if command_type == "subscribe":
            if result.success:
                for symbol in symbols:
                    active_symbols.add(symbol)
            else:
                details = result.error_details or "subscribe error"
                for symbol in symbols:
                    self._failover_symbol(symbol, details, active_symbols)
        elif command_type == "unsubscribe":
            if result.success:
                for symbol in symbols:
                    active_symbols.discard(symbol)
            else:
                details = result.error_details or "unsubscribe error"
                self._logger.log(
                    (
                        "Binance {stream} stream: не удалось отписаться от {symbols}: {details}"  # noqa: ISC003
                    ).format(stream=self._name, symbols=", ".join(symbols), details=details)
                )
        elif command_type == "resubscribe":
            phase = metadata.get("phase")
            if phase == "unsubscribe":
                if result.success:
                    for symbol in symbols:
                        active_symbols.discard(symbol)
                else:
                    details = result.error_details or "resubscribe unsubscribe error"
                    self._logger.log(
                        (
                            "Binance {stream} stream: сбой отписки при ресинке {symbols}: {details}"  # noqa: ISC003
                        ).format(stream=self._name, symbols=", ".join(symbols), details=details)
                    )
            elif phase == "subscribe":
                if result.success:
                    for symbol in symbols:
                        active_symbols.add(symbol)
                else:
                    details = result.error_details or "resubscribe subscribe error"
                    for symbol in symbols:
                        self._failover_symbol(symbol, details, active_symbols)
        elif command_type == "set_property":
            return

    def _handle_command_failures(
        self,
        failures: Iterable[CommandResult],
        active_symbols: set[str],
    ) -> None:
        for result in failures:
            metadata = result.command.metadata if isinstance(result.command.metadata, Mapping) else {}
            symbols = self._metadata_symbols(metadata)
            if not symbols:
                continue
            details = result.error_details or "timeout"
            for symbol in symbols:
                self._failover_symbol(symbol, details, active_symbols)

    def _failover_symbol(
        self,
        symbol: str,
        details: str,
        active_symbols: set[str],
    ) -> None:
        normalized = symbol.upper()
        active_symbols.discard(normalized)
        registration = self._get_registration(normalized)
        if registration is not None:
            try:
                registration.on_error(ResyncReason.CONNECTION_LOST, details)
            except Exception:
                pass
        predicate = lambda command: normalized in self._metadata_symbols(command.metadata)  # noqa: E731
        cancelled = self._scheduler.cancel(predicate)
        if cancelled:
            self._logger.log(
                (
                    "Binance {stream} stream: отменены {count} команд(ы) для символа {symbol}"  # noqa: ISC003
                ).format(stream=self._name, count=len(cancelled), symbol=normalized)
            )
        self._logger.log(
            (
                "Binance {stream} stream: символ {symbol} переводится на резерв из-за ошибки: {details}"  # noqa: ISC003
            ).format(stream=self._name, symbol=normalized, details=details)
        )
        try:
            self._on_degraded(self, normalized, details)
        except Exception:
            pass

    def _drain_pending_commands(self) -> None:
        while True:
            try:
                self._command_queue.get_nowait()
            except Empty:
                break

    async def _wait_for_symbols(self) -> tuple[str, ...]:
        while not self._stop_event.is_set():
            symbols = self._current_symbols()
            if symbols:
                return symbols
            if self._update_event.is_set():
                self._update_event.clear()
            await asyncio.sleep(1.0)
        return ()

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            symbols = await self._wait_for_symbols()
            if not symbols:
                continue

            self._scheduler.reset()
            client: Optional[AsyncWebSocketClient] = None
            try:
                client = AsyncWebSocketClient(
                    self._ws_base,
                    timeout=self._ws_timeout,
                    heartbeat_interval=self._ws_timeout / 2 if self._ws_timeout > 0 else None,
                    heartbeat_timeout=self._ws_timeout,
                )
                await client.connect()
                await client.set_timeout(self._ws_timeout)
                self._logger.log(
                    (
                        "Binance {stream} stream: открыто соединение для {count} "
                        "символов из {limit}"
                    ).format(
                        stream=self._name,
                        count=len(symbols),
                        limit=self._max_streams,
                        symbols=", ".join(symbols),
                    )
                )
                active_symbols: set[str] = set()
                self._prime_connection(symbols)
                await self._scheduler.dispatch(
                    throttle=self._throttle_if_needed,
                    send=client.send_json,
                    after_send=lambda _: self._command_limiter.record(),
                )
                self._handle_command_failures(self._scheduler.expire(), active_symbols)
                while not self._stop_event.is_set():
                    await self._process_commands(client, active_symbols)
                    self._handle_command_failures(self._scheduler.expire(), active_symbols)
                    self._restart_requested()
                    if (
                        not self._current_symbols()
                        and not active_symbols
                        and not self._scheduler.has_activity()
                    ):
                        break
                    try:
                        payload = await client.recv_json()
                    except WebSocketTimeoutError:
                        continue
                    except json.JSONDecodeError:
                        continue
                    except Exception as exc:  # noqa: BLE001
                        if self._stop_event.is_set():
                            break
                        details = (
                            "Binance {stream} stream: ошибка чтения сокета: {error}"
                        ).format(stream=self._name, error=exc)
                        self._logger.log(details)
                        self._notify_all(ResyncReason.CONNECTION_LOST, details)
                        break
                    result: Optional[CommandResult] = None
                    if isinstance(payload, Mapping):
                        result = self._scheduler.handle_response(payload)
                    if result is not None:
                        self._handle_command_result(result, active_symbols)
                        self._handle_command_failures(self._scheduler.expire(), active_symbols)
                        continue
                    await self._process_commands(client, active_symbols)
                    self._handle_command_failures(self._scheduler.expire(), active_symbols)
                    if not isinstance(payload, dict):
                        continue
                    symbol = self._extract_symbol(payload)
                    if symbol is None and isinstance(payload.get("s"), str):
                        symbol = str(payload.get("s")).upper()

                    error_details: Optional[str] = None
                    error_code: Optional[Any] = None
                    error_message: Optional[str] = None
                    error_section: Optional[dict[str, Any]] = None

                    raw_error = payload.get("error")
                    if isinstance(raw_error, dict):
                        error_section = raw_error
                        error_code = raw_error.get("code")
                        raw_message = raw_error.get("msg") or raw_error.get("message")
                        if isinstance(raw_message, str):
                            error_message = raw_message
                        elif raw_message is not None:
                            error_message = str(raw_message)
                    elif "code" in payload and "msg" in payload:
                        error_code = payload.get("code")
                        raw_message = payload.get("msg")
                        if isinstance(raw_message, str):
                            error_message = raw_message
                        elif raw_message is not None:
                            error_message = str(raw_message)
                        error_section = {"code": error_code, "msg": error_message}
                    elif payload.get("status") == "error":
                        error_code = payload.get("code")
                        for key in ("msg", "error", "errorMessage"):
                            candidate = payload.get(key)
                            if isinstance(candidate, str):
                                error_message = candidate
                                break
                            if candidate is not None and error_message is None:
                                error_message = str(candidate)
                        error_section = {
                            "status": payload.get("status"),
                            "code": error_code,
                            "msg": error_message,
                        }

                    if error_section is not None:
                        parts: list[str] = []
                        if error_code is not None:
                            parts.append(f"code={error_code}")
                        if error_message:
                            parts.append(f"msg={error_message}")
                        if not parts:
                            parts.append(str(error_section))
                        try:
                            payload_dump = json.dumps(payload, ensure_ascii=False)
                        except (TypeError, ValueError):  # pragma: no cover - safety
                            payload_dump = str(payload)
                        error_details = ", ".join(parts)
                        details = (
                            "Binance {stream} stream: получен ошибочный ответ"
                            " для {symbol}: {details}. Payload: {payload}"
                        ).format(
                            stream=self._name,
                            symbol=symbol or "неизвестного символа",
                            details=error_details,
                            payload=payload_dump,
                        )
                        self._logger.log(details)
                        if symbol is not None:
                            self._failover_symbol(symbol, details, active_symbols)
                        continue

                    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
                    if data is None and isinstance(payload.get("s"), str):
                        data = payload
                    if symbol is None or data is None:
                        continue
                    registration = self._get_registration(symbol)
                    if registration is None:
                        continue
                    if symbol not in active_symbols:
                        active_symbols.add(symbol)
                    if not isinstance(data, dict):
                        continue
                    try:
                        registration.on_message(data)
                    except Exception as exc:  # noqa: BLE001
                        details = (
                            "Binance {stream} stream: ошибка обработки сообщения для {symbol}:"
                            " {error}"
                        ).format(stream=self._name, symbol=symbol, error=exc)
                        self._logger.log(details)
                        registration.on_error(
                            ResyncReason.CONNECTION_LOST,
                            details,
                        )
            except Exception as exc:  # noqa: BLE001
                if self._stop_event.is_set():
                    break
                details = f"Binance {self._name} stream: {exc}"
                self._logger.log(details)
                self._notify_all(ResyncReason.CONNECTION_LOST, details)
                await asyncio.sleep(self._reconnect_delay)
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass
                self._scheduler.reset()
                self._drain_pending_commands()


class _StreamWorkerPool:
    def __init__(
        self,
        *,
        name: str,
        ws_base: str,
        stream_suffix: str,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]] = None,
        profile: StreamLoadProfile,
    ) -> None:
        self._name = name
        self._ws_base = ws_base
        self._stream_suffix = stream_suffix
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
        self._log_writer = log_writer
        self._profile = profile
        self._lock = threading.Lock()
        self._workers: list[_CombinedStreamWorker] = []
        self._worker_loads: dict[_CombinedStreamWorker, float] = {}
        self._bindings: dict[str, _Binding] = {}
        self._counter = 0

    def _create_worker(self) -> _CombinedStreamWorker:
        self._counter += 1
        worker_name = f"{self._name}#{self._counter}"
        worker = _CombinedStreamWorker(
            worker_name,
            self._ws_base,
            self._stream_suffix,
            ws_timeout=self._ws_timeout,
            reconnect_delay=self._reconnect_delay,
            log_writer=self._log_writer,
            max_streams=self._profile.max_streams_per_connection,
            command_profile=self._profile,
            on_degraded=self._handle_degraded,
        )
        self._workers.append(worker)
        self._worker_loads[worker] = 0.0
        return worker

    def _acquire_worker(
        self,
        *,
        exclude: Optional[_CombinedStreamWorker] = None,
    ) -> _CombinedStreamWorker:
        available = [
            worker
            for worker in self._workers
            if worker.has_capacity() and worker is not exclude
        ]
        if not available:
            worker = self._create_worker()
            if worker is exclude:
                worker = self._create_worker()
            return worker
        return min(
            available,
            key=lambda worker: (
                self._worker_loads.get(worker, 0.0),
                worker.registration_count(),
            ),
        )

    def ensure_workers(self, count: int) -> None:
        target = max(0, int(count))
        with self._lock:
            while len(self._workers) < target:
                self._create_worker()

    def worker_count(self) -> int:
        with self._lock:
            return len(self._workers)

    def register(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
        *,
        weight: float = 1.0,
    ) -> Callable[[], None]:
        normalized_symbol = symbol.upper()
        normalized_weight = max(float(weight), 0.0)
        resolved_on_error = on_error
        if resolved_on_error is None:
            def _default_error(reason: ResyncReason, details: str) -> None:
                buffer.push_resync(reason, details)

            resolved_on_error = _default_error
        with self._lock:
            worker = self._acquire_worker()
            self._worker_loads[worker] = self._worker_loads.get(worker, 0.0) + normalized_weight

        try:
            release = worker.register(
                normalized_symbol,
                buffer,
                on_message,
                resolved_on_error,
                weight=normalized_weight,
            )
        except Exception:
            with self._lock:
                current = self._worker_loads.get(worker, 0.0) - normalized_weight
                self._worker_loads[worker] = max(current, 0.0)
            raise

        release_wrapper = self._wrap_release(worker, normalized_weight, release)
        binding = _Binding(
            symbol=normalized_symbol,
            buffer=buffer,
            on_message=on_message,
            on_error=resolved_on_error,
            weight=normalized_weight,
            worker=worker,
            release_wrapper=release_wrapper,
        )
        with self._lock:
            self._bindings[normalized_symbol] = binding

        def _release_handle() -> None:
            self._release_binding(normalized_symbol, remove=True)

        return _release_handle

    def _wrap_release(
        self,
        worker: _CombinedStreamWorker,
        weight: float,
        release: Callable[[], None],
    ) -> Callable[[], None]:
        released = threading.Event()

        def _release_wrapper() -> None:
            if released.is_set():
                return
            release()
            with self._lock:
                current = self._worker_loads.get(worker, 0.0) - weight
                if current <= 0:
                    self._worker_loads.pop(worker, None)
                else:
                    self._worker_loads[worker] = current
                if worker.is_empty() and worker in self._workers:
                    self._workers.remove(worker)
                    worker.stop()
            released.set()

        return _release_wrapper

    def _release_binding(self, symbol: str, *, remove: bool) -> Optional[_Binding]:
        normalized = symbol.upper()
        with self._lock:
            binding = self._bindings.get(normalized)
        if binding is None:
            return None
        binding.release_wrapper()
        if remove:
            with self._lock:
                self._bindings.pop(normalized, None)
        return binding

    def _handle_degraded(
        self,
        worker: _CombinedStreamWorker,
        symbol: str,
        details: str,
    ) -> None:
        binding = self._release_binding(symbol, remove=False)
        if binding is None or binding.worker is not worker:
            return
        with self._lock:
            self._bindings[binding.symbol] = binding

    def migrate(self, symbol: str, details: str) -> bool:
        normalized = symbol.upper()
        with self._lock:
            binding = self._bindings.get(normalized)
        if binding is None:
            return False
        self._logger.log(
            (
                "Binance {stream} stream: инициирована миграция символа {symbol}: {details}"
            ).format(stream=self._name, symbol=normalized, details=details)
        )
        self._handle_degraded(binding.worker, normalized, details)
        return True
        new_worker = self._acquire_worker(exclude=worker)
        with self._lock:
            self._worker_loads[new_worker] = self._worker_loads.get(new_worker, 0.0) + binding.weight
        try:
            release = new_worker.register(
                binding.symbol,
                binding.buffer,
                binding.on_message,
                binding.on_error,
                weight=binding.weight,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                current = self._worker_loads.get(new_worker, 0.0) - binding.weight
                if current <= 0:
                    self._worker_loads.pop(new_worker, None)
                else:
                    self._worker_loads[new_worker] = current
            try:
                binding.buffer.push_resync(
                    ResyncReason.CONNECTION_LOST,
                    f"Не удалось переключить поток {binding.symbol}: {exc}",
                )
            except Exception:
                pass
            with self._lock:
                self._bindings.pop(binding.symbol, None)
            return
        release_wrapper = self._wrap_release(new_worker, binding.weight, release)
        binding.worker = new_worker
        binding.release_wrapper = release_wrapper
        with self._lock:
            self._bindings[binding.symbol] = binding


class BinanceStreamPool:
    def __init__(
        self,
        *,
        endpoints_ws_base: str,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]] = None,
        depth_stream_interval_ms: int = 100,
        profiles: Mapping[str, StreamLoadProfile] | None = None,
    ) -> None:
        base = endpoints_ws_base.rstrip("/")
        if not base.endswith("/ws"):
            base = f"{base}/ws"
        self._ws_base = base
        interval = max(1, int(depth_stream_interval_ms))
        depth_suffix = f"depth@{interval}ms"
        self._depth_stream_interval_ms = interval
        resolved_profiles = dict(DEFAULT_BINANCE_STREAM_PROFILES)
        if profiles is not None:
            resolved_profiles.update(profiles)
        depth_profile = resolved_profiles.get("depth", DEFAULT_BINANCE_STREAM_PROFILES["depth"])
        trades_profile = resolved_profiles.get("trades", DEFAULT_BINANCE_STREAM_PROFILES["trades"])
        book_profile = resolved_profiles.get("book", DEFAULT_BINANCE_STREAM_PROFILES["book"])
        self._depth_worker = _StreamWorkerPool(
            name=depth_suffix,
            ws_base=self._ws_base,
            stream_suffix=depth_suffix,
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
            profile=depth_profile,
        )
        self._trades_worker = _StreamWorkerPool(
            name="trades",
            ws_base=self._ws_base,
            stream_suffix="aggTrade",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
            profile=trades_profile,
        )
        self._book_worker = _StreamWorkerPool(
            name="book_ticker",
            ws_base=self._ws_base,
            stream_suffix="bookTicker",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
            profile=book_profile,
        )

    def register_depth(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
    ) -> Callable[[], None]:
        return self._depth_worker.register(symbol, buffer, on_message, on_error)

    def register_trades(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
    ) -> Callable[[], None]:
        return self._trades_worker.register(symbol, buffer, on_message, on_error)

    def register_book_ticker(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
    ) -> Callable[[], None]:
        return self._book_worker.register(symbol, buffer, on_message, on_error)


__all__ = ["BinanceStreamPool"]
