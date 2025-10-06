from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Awaitable, Optional, TypeVar, Union

import websockets
from websockets.client import WebSocketClientProtocol

__all__ = [
    "AsyncWebSocketClient",
    "ThreadedWebSocketClient",
    "WebSocketTimeoutError",
]


class WebSocketTimeoutError(TimeoutError):
    """Exception raised when websocket operations exceed the configured timeout."""


class AsyncWebSocketClient:
    """High-level helper around :func:`websockets.connect` with heartbeat support."""

    def __init__(
        self,
        uri: str,
        *,
        timeout: float = 10.0,
        heartbeat_interval: Optional[float] = None,
        heartbeat_payload: Union[str, bytes, None] = None,
        heartbeat_timeout: Optional[float] = None,
    ) -> None:
        self._uri = uri
        self._timeout = max(0.0, float(timeout))
        self._heartbeat_interval = (
            float(heartbeat_interval) if heartbeat_interval and heartbeat_interval > 0 else None
        )
        self._heartbeat_payload = heartbeat_payload
        self._heartbeat_timeout = float(heartbeat_timeout) if heartbeat_timeout else self._timeout
        self._connection: WebSocketClientProtocol | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()

    async def connect(self) -> None:
        if self._connection is not None:
            return
        self._connection = await websockets.connect(
            self._uri,
            ping_interval=None,
            open_timeout=self._timeout or None,
            close_timeout=self._timeout or None,
        )
        self._closed.clear()
        if self._heartbeat_interval is not None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="ws-heartbeat")

    async def _ensure_connection(self) -> WebSocketClientProtocol:
        if self._connection is None:
            await self.connect()
        assert self._connection is not None
        return self._connection

    async def _heartbeat(self) -> None:
        assert self._connection is not None
        try:
            while not self._closed.is_set():
                await asyncio.sleep(self._heartbeat_interval or 0)
                if self._closed.is_set():
                    break
                try:
                    await self._send_heartbeat()
                except Exception:
                    await self.close()
                    break
        except asyncio.CancelledError:
            pass

    async def _send_heartbeat(self) -> None:
        connection = await self._ensure_connection()
        timeout = self._heartbeat_timeout or self._timeout
        if self._heartbeat_payload is None:
            waiter = await connection.ping()
            await asyncio.wait_for(waiter, timeout=timeout if timeout > 0 else None)
        else:
            await asyncio.wait_for(connection.send(self._heartbeat_payload), timeout=timeout if timeout > 0 else None)

    async def set_timeout(self, timeout: float) -> None:
        self._timeout = max(0.0, float(timeout))

    async def send(self, data: Union[str, bytes]) -> None:
        connection = await self._ensure_connection()
        try:
            await asyncio.wait_for(connection.send(data), timeout=self._timeout or None)
        except asyncio.TimeoutError as exc:  # pragma: no cover - defensive
            raise WebSocketTimeoutError(str(exc)) from exc

    async def recv(self) -> str:
        connection = await self._ensure_connection()
        try:
            message = await asyncio.wait_for(connection.recv(), timeout=self._timeout or None)
        except asyncio.TimeoutError as exc:
            raise WebSocketTimeoutError(str(exc)) from exc
        if isinstance(message, bytes):
            return message.decode("utf-8", errors="replace")
        return message

    async def send_json(self, payload: Any) -> None:
        await self.send(json.dumps(payload))

    async def recv_json(self) -> Any:
        raw = await self.recv()
        return json.loads(raw)

    async def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            finally:
                self._heartbeat_task = None
        if self._connection is not None:
            try:
                await self._connection.close()
            finally:
                self._connection = None


T = TypeVar("T")


class ThreadedWebSocketClient:
    """Synchronous facade over :class:`AsyncWebSocketClient`."""

    def __init__(
        self,
        uri: str,
        *,
        timeout: float = 10.0,
        heartbeat_interval: Optional[float] = None,
        heartbeat_payload: Union[str, bytes, None] = None,
        heartbeat_timeout: Optional[float] = None,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ws-client",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait()
        self._client = self._run(
            self._create_client(
                uri,
                timeout=timeout,
                heartbeat_interval=heartbeat_interval,
                heartbeat_payload=heartbeat_payload,
                heartbeat_timeout=heartbeat_timeout,
            )
        )
        self._closed = False

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    async def _create_client(
        self,
        uri: str,
        *,
        timeout: float,
        heartbeat_interval: Optional[float],
        heartbeat_payload: Union[str, bytes, None],
        heartbeat_timeout: Optional[float],
    ) -> AsyncWebSocketClient:
        client = AsyncWebSocketClient(
            uri,
            timeout=timeout,
            heartbeat_interval=heartbeat_interval,
            heartbeat_payload=heartbeat_payload,
            heartbeat_timeout=heartbeat_timeout,
        )
        await client.connect()
        return client

    def _run(self, awaitable: Awaitable[T]) -> T:
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def settimeout(self, timeout: float) -> None:
        self._ensure_open()
        self._run(self._client.set_timeout(timeout))

    def send(self, data: Union[str, bytes]) -> None:
        self._ensure_open()
        try:
            self._run(self._client.send(data))
        except WebSocketTimeoutError:
            raise

    def recv(self) -> str:
        self._ensure_open()
        try:
            return self._run(self._client.recv())
        except WebSocketTimeoutError:
            raise

    def send_json(self, payload: Any) -> None:
        self._ensure_open()
        self._run(self._client.send_json(payload))

    def recv_json(self) -> Any:
        self._ensure_open()
        return self._run(self._client.recv_json())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._run(self._client.close())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=1.0)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("WebSocket client is closed")

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        try:
            self.close()
        except Exception:
            pass
