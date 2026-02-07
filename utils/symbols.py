"""Helpers for symbol normalization."""

from __future__ import annotations


def normalize_symbol(symbol: str) -> str:
    """Normalize exchange symbol for stable comparison across sources."""
    normalized = str(symbol).strip().upper()
    if ":" in normalized:
        normalized = normalized.split(":", maxsplit=1)[0]
    return normalized

