"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class VectorbtInputs:
    close: pd.Series
    entries: pd.Series
    exits: pd.Series
    equity_curve: pd.Series
    positions: pd.DataFrame
