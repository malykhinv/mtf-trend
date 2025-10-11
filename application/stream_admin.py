from __future__ import annotations

import argparse
import json
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from config.config import CONFIG

AdminCallback = Callable[[str], bool]
ResyncCallback = Callable[[str, Optional[str], Optional[str]], bool]
StatusCallback = Callable[[Optional[str]], Mapping[str, Any]]


@dataclass(frozen=True)
class StreamAdminInterface:
    pause: AdminCallback
    resume: AdminCallback
    migrate: ResyncCallback
    force_resync: ResyncCallback
    status: StatusCallback

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "operations": ("pause", "resume", "migrate", "resync", "status"),
        }


class StreamAdminRequestHandler(BaseHTTPRequestHandler):
    interface: StreamAdminInterface

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/status":
            self._send_error(HTTPStatus.NOT_FOUND, "endpoint not found")
            return
        query = parse_qs(parsed.query)
        symbol = query.get("symbol", [None])[0]
        try:
            payload = self.interface.status(symbol.upper() if symbol else None)
        except Exception as exc:  # noqa: BLE001
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(payload)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0] != "symbols":
            self._send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        symbol = path_parts[1].upper()
        command = path_parts[2] if len(path_parts) > 2 else ""
        body = self._read_json_body()
        if command == "pause":
            result = self.interface.pause(symbol)
            self._send_json({"symbol": symbol, "paused": result})
            return
        if command == "resume":
            result = self.interface.resume(symbol)
            self._send_json({"symbol": symbol, "resumed": result})
            return
        if command == "migrate":
            stream = self._extract_field(body, "stream")
            details = self._extract_field(body, "details")
            result = self.interface.migrate(symbol, stream, details)
            self._send_json({"symbol": symbol, "migrated": result, "stream": stream})
            return
        if command == "resync":
            stream = self._extract_field(body, "stream")
            reason = self._extract_field(body, "reason")
            result = self.interface.force_resync(symbol, stream, reason)
            self._send_json({"symbol": symbol, "resync": result, "stream": stream})
            return
        self._send_error(HTTPStatus.NOT_FOUND, f"unsupported command {command}")

    def log_message(self, _format: str, *_args: object) -> None:  # pragma: no cover - suppress noisy logs
        return

    def _read_json_body(self) -> Mapping[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        payload = json.dumps({"error": message, "status": status.value})
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _extract_field(body: Mapping[str, Any], field_name: str) -> Optional[str]:
        value = body.get(field_name)
        if value is None:
            return None
        return str(value)


class StreamAdminServer:
    """Threaded HTTP server exposing administrative controls."""

    def __init__(self, interface: StreamAdminInterface, host: str, port: int) -> None:
        self._interface = interface
        self._host = host
        self._port = port
        handler = self._build_handler(interface)
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="stream-admin",
            daemon=True,
        )

    @staticmethod
    def _build_handler(interface: StreamAdminInterface) -> Callable[[Any, Any, Any], StreamAdminRequestHandler]:
        def _handler(*args: Any, **kwargs: Any) -> StreamAdminRequestHandler:
            handler = StreamAdminRequestHandler(*args, **kwargs)
            handler.interface = interface
            return handler

        return _handler

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _http_post(host: str, port: int, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    connection = HTTPConnection(host, port, timeout=CONFIG.admin.request_timeout_s)
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Content-Length": str(len(data))}
    connection.request("POST", path, body=data, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    if response.status >= 400:
        raise RuntimeError(raw.decode("utf-8") or f"HTTP {response.status}")
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _http_get(host: str, port: int, path: str) -> Mapping[str, Any]:
    connection = HTTPConnection(host, port, timeout=CONFIG.admin.request_timeout_s)
    connection.request("GET", path)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    if response.status >= 400:
        raise RuntimeError(raw.decode("utf-8") or f"HTTP {response.status}")
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def run_cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Stream administration CLI")
    parser.add_argument("--host", default=CONFIG.admin.host)
    parser.add_argument("--port", default=CONFIG.admin.port, type=int)
    subparsers = parser.add_subparsers(dest="command", required=True)

    pause_parser = subparsers.add_parser("pause")
    pause_parser.add_argument("symbol")

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("symbol")

    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("symbol")
    migrate_parser.add_argument("--stream", default=None)
    migrate_parser.add_argument("--details", default=None)

    resync_parser = subparsers.add_parser("resync")
    resync_parser.add_argument("symbol")
    resync_parser.add_argument("--stream", default=None)
    resync_parser.add_argument("--reason", default=None)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("symbol", nargs="?")

    args = parser.parse_args(argv)
    host = args.host
    port = args.port

    try:
        if args.command == "pause":
            result = _http_post(host, port, f"/symbols/{args.symbol}/pause", {})
        elif args.command == "resume":
            result = _http_post(host, port, f"/symbols/{args.symbol}/resume", {})
        elif args.command == "migrate":
            payload = {"stream": args.stream, "details": args.details}
            result = _http_post(host, port, f"/symbols/{args.symbol}/migrate", payload)
        elif args.command == "resync":
            payload = {"stream": args.stream, "reason": args.reason}
            result = _http_post(host, port, f"/symbols/{args.symbol}/resync", payload)
        elif args.command == "status":
            suffix = f"?symbol={args.symbol}" if args.symbol else ""
            result = _http_get(host, port, f"/status{suffix}")
        else:  # pragma: no cover - defensive branch
            raise RuntimeError(f"Unknown command {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}")
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run_cli())


__all__ = [
    "StreamAdminInterface",
    "StreamAdminRequestHandler",
    "StreamAdminServer",
    "run_cli",
]
