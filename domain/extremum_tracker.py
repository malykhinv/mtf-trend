from __future__ import annotations

from collections import deque
from typing import Deque, Iterable

from .extremum import Extremum, ExtremumType


class ExtremumTracker:
    """Хранит найденные экстремумы."""

    def __init__(self, maxlen: int = 100):
        self._extremums: Deque[Extremum] = deque(maxlen=maxlen)

    def update(self, new_extremums: Iterable[Extremum]) -> None:
        self._extremums.extend(new_extremums)

    def last(self, type_: ExtremumType | None = None) -> Extremum | None:
        if type_ is None:
            return self._extremums[-1] if self._extremums else None
        for ext in reversed(self._extremums):
            if ext.type is type_:
                return ext
        return None

    @property
    def items(self) -> list[Extremum]:
        return list(self._extremums)

