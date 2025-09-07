from __future__ import annotations

from typing import Dict

from domain.models.state import SymbolState


class SymbolRegistry:
    """In memory storage of ``SymbolState`` objects."""

    def __init__(self) -> None:
        self._states: Dict[str, SymbolState] = {}

    def has(self, symbol: str) -> bool:
        return symbol in self._states

    def get(self, symbol: str) -> SymbolState:
        return self._states[symbol]

    def put(self, state: SymbolState) -> None:
        self._states[state.symbol] = state

    def update(self, symbol: str, new_state: SymbolState) -> None:
        self._states[symbol] = new_state

    def all_symbols(self) -> tuple[str, ...]:
        return tuple(self._states.keys())
