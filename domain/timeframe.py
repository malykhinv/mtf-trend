from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Timeframe:
    name: str
    minutes: int

