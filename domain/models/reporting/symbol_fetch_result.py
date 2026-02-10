"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SymbolFetchResult:
    success: bool
    message: str
    added_rows: int = 0

    @classmethod
    def ok(cls, added_rows: int) -> "SymbolFetchResult":
        return cls(success=True, message="ok", added_rows=added_rows)

    @classmethod
    def error(cls, message: str) -> "SymbolFetchResult":
        return cls(success=False, message=message, added_rows=0)

