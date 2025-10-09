from __future__ import annotations

import sys
import threading
from datetime import timedelta
from pathlib import Path
from types import MethodType, ModuleType

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

if "websockets" not in sys.modules:
    websockets_stub = ModuleType("websockets")
    websockets_stub.connect = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    sys.modules["websockets"] = websockets_stub
if "websockets.client" not in sys.modules:
    client_stub = ModuleType("websockets.client")
    class _DummyProtocol:  # noqa: D401 - simple placeholder for protocol class
        """Placeholder used in tests to satisfy imports."""

    client_stub.WebSocketClientProtocol = _DummyProtocol
    sys.modules["websockets.client"] = client_stub

from data.exchanges.binance import BinanceExchangeData
from data.exchanges.base import ResyncReason
from domain.models import Exchange, OrderBookLevel, OrderBookSnapshot
from utils.timez import get_current_time


class _DummyBuffer:
    def __init__(self) -> None:
        self.snapshots: list[object] = []
        self.resyncs: list[tuple[ResyncReason, str | None]] = []
        self.data: list[object] = []

    def push_snapshot(self, payload: object) -> None:
        self.snapshots.append(payload)

    def push_resync(self, reason: ResyncReason, details: str | None = None) -> None:
        self.resyncs.append((reason, details))

    def push_data(self, payload: object) -> None:
        self.data.append(payload)


class _DummyLogger:
    def log(self, message: str) -> None:  # noqa: D401
        """Ignore log messages in tests."""

    def log_resync(self, reason: ResyncReason, details: str) -> None:  # noqa: D401
        """Ignore resync log messages in tests."""


def _make_exchange() -> BinanceExchangeData:
    exchange = object.__new__(BinanceExchangeData)
    exchange.symbol = "BTCUSDT"
    exchange._logger = _DummyLogger()  # type: ignore[attr-defined]
    exchange._lock = threading.Lock()  # type: ignore[attr-defined]
    exchange._depth_last_update = None  # type: ignore[attr-defined]
    exchange._depth_allow_skip = False  # type: ignore[attr-defined]
    exchange._depth_buffered_messages = []  # type: ignore[attr-defined]
    return exchange


def _make_snapshot(last_update_id: int) -> OrderBookSnapshot:
    now = get_current_time()
    level = OrderBookLevel(
        price=100.0,
        quantity=1.0,
        notional=100.0,
        first_seen_at=now,
        last_update_at=now,
        min_quantity_seen=1.0,
        max_quantity_seen=1.0,
    )
    return OrderBookSnapshot(
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        last_update_id=last_update_id,
        bids=(level,),
        asks=(level,),
        received_at=now,
    )


def test_first_diff_gap_after_snapshot_triggers_resync() -> None:
    exchange = _make_exchange()
    buffer = _DummyBuffer()
    snapshot = _make_snapshot(last_update_id=100)

    exchange._apply_depth_snapshot(snapshot, buffer)
    assert exchange._depth_last_update == 100  # type: ignore[attr-defined]
    assert exchange._depth_allow_skip is True  # type: ignore[attr-defined]

    resync_calls: list[tuple[ResyncReason, str]] = []

    def fake_trigger(
        self: BinanceExchangeData,
        stream_buffer: object,
        *,
        reason: ResyncReason,
        details: str,
    ) -> None:
        resync_calls.append((reason, details))

    exchange._trigger_depth_resync = MethodType(fake_trigger, exchange)  # type: ignore[attr-defined]

    event_time = get_current_time() + timedelta(milliseconds=100)
    gap_message = {
        "e": "depthUpdate",
        "U": 150,
        "u": 151,
        "pu": 149,
        "E": int(event_time.timestamp() * 1000),
    }

    exchange._handle_depth_message(gap_message, buffer)

    assert len(resync_calls) == 1
    reason, details = resync_calls[0]
    assert reason == ResyncReason.SEQUENCE_GAP
    assert isinstance(details, str)
    assert exchange._depth_allow_skip is False  # type: ignore[attr-defined]
