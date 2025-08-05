from __future__ import annotations

from typing import Iterable, TypeVar, List


T = TypeVar("T")


def chunked(iterable: Iterable[T], size: int) -> List[List[T]]:
    """Разбить последовательность на чанки."""
    chunk: List[T] = []
    result: List[List[T]] = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) == size:
            result.append(chunk)
            chunk = []
    if chunk:
        result.append(chunk)
    return result

