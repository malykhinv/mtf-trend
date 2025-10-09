from __future__ import annotations

from typing import Callable

from domain.models import LogLine, Signal, Wall
from .resync import ResyncReason

SubscriptionHandler = Callable[[str], None]
FocusHandler = Callable[[str], None]
DefocusHandler = Callable[[], None]
PositionEntryHandler = Callable[[str, Signal, Wall], bool]
PositionExitHandler = Callable[[str, str], bool]
StopMoveHandler = Callable[[str, float], bool]
TelegramHandler = Callable[[str], None]
LogWriter = Callable[[LogLine], None]
ResyncHandler = Callable[[ResyncReason], None]

__all__ = [
    "SubscriptionHandler",
    "FocusHandler",
    "DefocusHandler",
    "PositionEntryHandler",
    "PositionExitHandler",
    "StopMoveHandler",
    "TelegramHandler",
    "LogWriter",
    "ResyncHandler",
]
