from __future__ import annotations

try:  # pragma: no cover - imported lazily for environments without websockets
    import websockets
    from websockets import WebSocketClientProtocol
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
except Exception:  # pragma: no cover - handled at runtime
    websockets = None  # type: ignore[assignment]
    WebSocketClientProtocol = object  # type: ignore[misc]
    ConnectionClosed = ConnectionClosedError = ConnectionClosedOK = Exception  # type: ignore[assignment]


__all__ = [
    "websockets",
    "WebSocketClientProtocol",
    "ConnectionClosed",
    "ConnectionClosedError",
    "ConnectionClosedOK",
]
