from dataclasses import dataclass


@dataclass(frozen=True)
class PumpWindow:
    high: float
    low: float
    start_ts: int
    end_ts: int
