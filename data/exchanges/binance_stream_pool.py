from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from websocket import WebSocketTimeoutException, create_connection

from .base import ExchangeLogger, ResyncReason, StreamBuffer


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
    ) -> None:
        self._name = name
        self._combined_base = combined_base
        self._stream_suffix = stream_suffix
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
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
            self._registrations[symbol] = registration
            self._update_event.set()

        released = threading.Event()

        def release() -> None:
            if released.is_set():
                return
            with self._lock:
                self._registrations.pop(symbol, None)
                self._update_event.set()
            released.set()

        return release

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
                    f"Binance {self._name} stream: открыто соединение для {len(symbols)} символов"
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
        self._depth_worker = _CombinedStreamWorker(
            "depth",
            self._combined_base,
            "depth@100ms",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._trades_worker = _CombinedStreamWorker(
            "trades",
            self._combined_base,
            "aggTrade",
            ws_timeout=ws_timeout,
            reconnect_delay=reconnect_delay,
            log_writer=log_writer,
        )
        self._book_worker = _CombinedStreamWorker(
            "book_ticker",
            self._combined_base,
            "bookTicker",
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
