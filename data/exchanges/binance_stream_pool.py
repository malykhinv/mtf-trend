from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from websocket import WebSocketTimeoutException, create_connection

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
        combined_base: str,
        stream_suffix: str,
        *,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]] = None,
        max_streams: int,
    ) -> None:
        self._name = name
        self._combined_base = combined_base
        self._stream_suffix = stream_suffix
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
        self._max_streams = max_streams
        self._lock = threading.Lock()
        self._update_event = threading.Event()
        self._stop_event = threading.Event()
        self._registrations: Dict[str, _Registration] = {}
        self._logger = ExchangeLogger(f"Binance pool:{name}", log_writer)
        self._thread = threading.Thread(
            target=self._run,
            name=f"binance-pool-{name}",
            daemon=True,
        )
        self._thread.start()

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

        released = threading.Event()

        def release() -> None:
            if released.is_set():
                return
            with self._lock:
                self._registrations.pop(symbol, None)
                current = len(self._registrations)
                self._update_event.set()
            released.set()
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
        self._logger.log(
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

    def _restart_requested(self) -> bool:
        with self._lock:
            registrations = list(self._registrations.values())
        restart_symbols: list[str] = []
        for registration in registrations:
            if registration.buffer.consume_restart_request():
                restart_symbols.append(registration.symbol)
        if restart_symbols:
            joined = ", ".join(restart_symbols)
            self._logger.log(
                f"Binance {self._name} stream: перезапуск по запросу буферов: {joined}"
            )
            return True
        return False

    def _notify_all(self, reason: ResyncReason, details: str) -> None:
        with self._lock:
            registrations = list(self._registrations.values())
        for registration in registrations:
            try:
                registration.on_error(reason, details)
            except Exception:
                pass

    def _wait_for_symbols(self) -> tuple[str, ...]:
        while not self._stop_event.is_set():
            symbols = self._current_symbols()
            if symbols:
                return symbols
            self._update_event.wait(timeout=1.0)
            self._update_event.clear()
        return ()

    def _build_url(self, symbols: tuple[str, ...]) -> str:
        streams = "/".join(f"{symbol.lower()}@{self._stream_suffix}" for symbol in symbols)
        return f"{self._combined_base}{streams}"

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
        if symbol is None:
            return None
        return symbol.upper()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            symbols = self._wait_for_symbols()
            if not symbols:
                continue

            url = self._build_url(symbols)
            ws = None
            try:
                ws = create_connection(
                    url,
                    timeout=self._ws_timeout,
                    enable_multithread=True,
                )
                ws.settimeout(self._ws_timeout)
                self._logger.log(
                    (
                        "Binance {stream} stream: открыто соединение для {count} "
                        "символов из {limit}: {symbols}"
                    ).format(
                        stream=self._name,
                        count=len(symbols),
                        limit=self._max_streams,
                        symbols=", ".join(symbols),
                    )
                )
                while not self._stop_event.is_set():
                    if self._update_event.is_set():
                        self._update_event.clear()
                        break
                    if self._restart_requested():
                        break
                    try:
                        raw = ws.recv()
                    except WebSocketTimeoutException:
                        continue
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    symbol = self._extract_symbol(payload)
                    if symbol is None:
                        continue
                    registration = self._get_registration(symbol)
                    if registration is None:
                        continue
                    data = payload.get("data")
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
                time.sleep(self._reconnect_delay)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass


class _StreamWorkerPool:
    def __init__(
        self,
        *,
        name: str,
        combined_base: str,
        stream_suffix: str,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._name = name
        self._combined_base = combined_base
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
            self._combined_base,
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
        log_writer: Optional[Callable[[str], None]] = None,
    ) -> None:
        base = endpoints_ws_base.rstrip("/")
        if base.endswith("ws"):
            base = base[: -2]
        if base.endswith("/"):
            base = base[:-1]
        self._combined_base = f"{base}/stream?streams="
        self._depth_worker = _StreamWorkerPool(
            name="depth",
            combined_base=self._combined_base,
            stream_suffix="depth@100ms",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._trades_worker = _StreamWorkerPool(
            name="trades",
            combined_base=self._combined_base,
            stream_suffix="aggTrade",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._book_worker = _StreamWorkerPool(
            name="book_ticker",
            combined_base=self._combined_base,
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
