import sys
import pathlib

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from domain.services.notification import NotificationService
from domain.models.trading import PositionPlan
from domain.models.enums import Side


class DummyClient:
    def __init__(self) -> None:
        self.sent: str | None = None

    def send(self, text: str) -> None:
        self.sent = text


def _plan() -> PositionPlan:
    return PositionPlan(
        symbol="BTCUSDT",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit1=110.0,
        take_profit2=120.0,
        trail_start=0.0,
        trail_distance=0.0,
        quantity=1.0,
        tp1_qty=0.5,
        tp2_qty=0.25,
        tail_qty=0.25,
        window_high=105.0,
    )


def test_notify_order_open_with_price():
    client = DummyClient()
    notifier = NotificationService(client)
    plan = _plan()
    notifier.notify_order_open(plan, Side.LONG, actual_price=101.0)
    assert client.sent is not None
    assert "@ 101.0000" in client.sent
    assert "(1.00%)" in client.sent


def test_notify_order_open_without_price():
    client = DummyClient()
    notifier = NotificationService(client)
    plan = _plan()
    notifier.notify_order_open(plan, Side.LONG)
    assert client.sent is not None
    assert "(0.00%)" not in client.sent
