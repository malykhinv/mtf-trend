"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SymbolFetchResult:
    success: bool
    message: str
    added_rows: int = 0
    quality_metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def ok(cls, added_rows: int) -> "SymbolFetchResult":
        """Создаёт успешный результат загрузки символа."""
        return cls(success=True, message="ok", added_rows=added_rows, quality_metadata={})

    @classmethod
    def error(cls, message: str) -> "SymbolFetchResult":
        """Создаёт результат загрузки с ошибкой."""
        return cls(success=False, message=message, added_rows=0, quality_metadata={})

