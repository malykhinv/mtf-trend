"""Optimal parameter ranges DTO."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptimalParameterRanges:
    lookback: list[int]
    volume_multiplier: list[float]
