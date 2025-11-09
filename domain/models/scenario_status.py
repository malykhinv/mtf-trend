from __future__ import annotations

from enum import Enum


class ScenarioStatus(str, Enum):
    IDLE = "idle"
    MONITORING = "monitoring"
    ACTIVE = "active"
    COOLDOWN = "cooldown"


__all__ = ["ScenarioStatus"]
