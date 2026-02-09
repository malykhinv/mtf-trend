"""CCXT protocol/type declarations for futures exchange client."""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class CcxtClientOptions(TypedDict):
    defaultType: str


class CcxtFuturesApi(Protocol):
    markets: dict[str, dict[str, object]]

    def load_markets(self) -> object:
        ...

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        ...


@runtime_checkable
class CcxtOpenInterestApi(Protocol):
    def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str,
        since: int,
        limit: int,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        ...
