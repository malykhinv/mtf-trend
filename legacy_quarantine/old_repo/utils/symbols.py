"""Вспомогательные функции для нормализации символов."""

from __future__ import annotations


def normalize_symbol(symbol: str) -> str:
    """Нормализует биржевой символ для стабильного сравнения между источниками."""
    normalized = str(symbol).strip().upper()
    if ":" in normalized:
        normalized = normalized.split(":", maxsplit=1)[0]
    return normalized

