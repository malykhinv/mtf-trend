import asyncio
import json
import threading
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any, Callable, Dict, Optional

from data.logger import LogLineWriter
from utils.async_websocket import AsyncWebSocketClient, WebSocketTimeoutError

from .base import ExchangeLogger, ResyncReason, StreamBuffer


MAX_STREAMS_PER_CONNECTION = 200


@dataclass(slots=True)
class _Registration:
    symbol: str
    buffer: StreamBuffer[Any]
    on_message: Callable[[dict[str, Any]], None]
    on_error: Callable[[ResyncReason, str], None]


class _CombinedStreamWorker:
    def __init__(
        self,
        name: str,
        ws_base: str,
        stream_suffix: str,
        *,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[LogLineWriter] = None,
        max_streams: int,
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
        self._next_request_id = 1
        self._thread = threading.Thread(
            target=self._run_thread,
            name=f"binance-pool-{name}",
            daemon=True,
        )
        self._thread.start()

    def _run_thread(self) -> None:
        asyncio.run(self._run())

    def register(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
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

        self._logger.log_info(
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
            self._logger.log_info(
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

        return release

    def has_capacity(self) -> bool:
        with self._lock:
            return len(self._registrations) < self._max_streams and not self._stop_event.is_set()

    def is_empty(self) -> bool:
        with self._lock:
            return not self._registrations

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._logger.log_info(
            f"Binance {self._name} stream: остановка, активных символов нет"
        )
        self._stop_event.set()
        self._update_event.set()

    def _current_symbols(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._registrations.keys())

    def _get_registration(self, symbol: str) -> Optional[_Registration]:
        with self._lock:
            return self._registrations.get(symbol)

    def _restart_requested(self) -> None:
        with self._lock:
            registrations = list(self._registrations.values())

        processed_symbols: list[str] = []
        for registration in registrations:
            if not registration.buffer.consume_restart_request():
                continue

            processed_symbols.append(registration.symbol)
            self._command_queue.put(("resubscribe", registration.symbol))
            details = (
                "Binance {stream} stream: таймаут тишины для {symbol}, "
                "запрос повторной синхронизации"
            ).format(stream=self._name, symbol=registration.symbol)

            try:
                registration.on_error(ResyncReason.SILENCE_TIMEOUT, details)
            except Exception:
                pass

        if processed_symbols:
            joined = ", ".join(processed_symbols)
            self._logger.log_error(
                (
                    "Binance {stream} stream: обработан запрос повторной "
                    "синхронизации из буферов: {symbols}"
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

    def _enqueue_all_symbols(self) -> None:
        with self._lock:
            symbols = tuple(self._registrations.keys())
        for symbol in symbols:
            self._command_queue.put(("subscribe", symbol))

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

    async def _send_command(self, client: AsyncWebSocketClient, method: str, params: list[Any]) -> None:
        request_id = self._next_request_id
        self._next_request_id += 1
        payload = {"method": method, "params": params, "id": request_id}
        await client.send_json(payload)

    async def _send_set_combined(self, client: AsyncWebSocketClient) -> None:
        try:
            await self._send_command(client, "SET_PROPERTY", ["combined", True])
        except Exception:
            pass

    async def _process_commands(
        self,
        client: AsyncWebSocketClient,
        active_symbols: set[str],
    ) -> None:
        while True:
            try:
                action, symbol = self._command_queue.get_nowait()
            except Empty:
                break
            if action == "subscribe":
                if self._get_registration(symbol) is None or symbol in active_symbols:
                    continue
                stream_name = self._stream_name(symbol)
                try:
                    await self._send_command(client, "SUBSCRIBE", [stream_name])
                except Exception:
                    continue
                active_symbols.add(symbol)
            elif action == "unsubscribe":
                if symbol not in active_symbols:
                    continue
                stream_name = self._stream_name(symbol)
                try:
                    await self._send_command(client, "UNSUBSCRIBE", [stream_name])
                except Exception:
                    pass
                active_symbols.discard(symbol)
            elif action == "resubscribe":
                if self._get_registration(symbol) is None:
                    continue
                stream_name = self._stream_name(symbol)
                if symbol in active_symbols:
                    try:
                        await self._send_command(client, "UNSUBSCRIBE", [stream_name])
                    except Exception:
                        pass
                    await asyncio.sleep(0.05)
                    active_symbols.discard(symbol)
                try:
                    await self._send_command(client, "SUBSCRIBE", [stream_name])
                except Exception:
                    continue
                active_symbols.add(symbol)

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

            self._enqueue_all_symbols()
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
                self._next_request_id = 1
                self._logger.log_info(
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
                await self._send_set_combined(client)
                while not self._stop_event.is_set():
                    await self._process_commands(client, active_symbols)
                    self._restart_requested()
                    if not self._current_symbols() and not active_symbols:
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
                        self._logger.log_error(details)
                        self._notify_all(ResyncReason.CONNECTION_LOST, details)
                        break
                    await self._process_commands(client, active_symbols)
                    if not isinstance(payload, dict):
                        continue
                    if "result" in payload:
                        continue
                    symbol = self._extract_symbol(payload)
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
                    if symbol is None and isinstance(payload.get("s"), str):
                        symbol = str(payload.get("s")).upper()
                        if data is None:
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
                        self._logger.log_error(details)
                        registration.on_error(
                            ResyncReason.CONNECTION_LOST,
                            details,
                        )
            except Exception as exc:  # noqa: BLE001
                if self._stop_event.is_set():
                    break
                details = f"Binance {self._name} stream: {exc}"
                self._logger.log_error(details)
                self._notify_all(ResyncReason.CONNECTION_LOST, details)
                await asyncio.sleep(self._reconnect_delay)
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass
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
        log_writer: Optional[LogLineWriter] = None,
    ) -> None:
        self._name = name
        self._ws_base = ws_base
        self._stream_suffix = stream_suffix
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
        self._log_writer = log_writer
        self._lock = threading.Lock()
        self._workers: list[_CombinedStreamWorker] = []
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
            max_streams=MAX_STREAMS_PER_CONNECTION,
        )
        self._workers.append(worker)
        return worker

    def _acquire_worker(self) -> _CombinedStreamWorker:
        for worker in self._workers:
            if worker.has_capacity():
                return worker
        return self._create_worker()

    def register(
        self,
        symbol: str,
        buffer: StreamBuffer[Any],
        on_message: Callable[[dict[str, Any]], None],
        on_error: Optional[Callable[[ResyncReason, str], None]] = None,
    ) -> Callable[[], None]:
        with self._lock:
            worker = self._acquire_worker()

        release = worker.register(symbol, buffer, on_message, on_error)
        released = threading.Event()

        def _release_wrapper() -> None:
            if released.is_set():
                return
            release()
            with self._lock:
                if worker.is_empty() and worker in self._workers:
                    self._workers.remove(worker)
                    worker.stop()
            released.set()

        return _release_wrapper


class BinanceStreamPool:
    def __init__(
        self,
        *,
        endpoints_ws_base: str,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[LogLineWriter] = None,
    ) -> None:
        base = endpoints_ws_base.rstrip("/")
        if not base.endswith("/ws"):
            base = f"{base}/ws"
        self._ws_base = base
        self._depth_worker = _StreamWorkerPool(
            name="depth",
            ws_base=self._ws_base,
            stream_suffix="depth@100ms",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._trades_worker = _StreamWorkerPool(
            name="trades",
            ws_base=self._ws_base,
            stream_suffix="aggTrade",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._book_worker = _StreamWorkerPool(
            name="book_ticker",
            ws_base=self._ws_base,
            stream_suffix="bookTicker",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
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
