from __future__ import annotations

from typing import Protocol, Sequence

from domain.bar import Bar
from domain.extremum import Extremum


class Plotter(Protocol):
    def plot(self, bars: Sequence[Bar], extremums: Sequence[Extremum], path: str) -> None:
        """Отобразить свечи и экстремумы и сохранить по указанному пути."""

