"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptimalParameterRanges:
    bite_lookback: list[int]
    bite_volume_mult: list[float]
    bite_min_rr: list[float]
    bite_tp2_mult: list[float]
