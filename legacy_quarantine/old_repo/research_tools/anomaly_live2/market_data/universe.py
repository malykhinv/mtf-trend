"""Startup universe selection for anomaly live2.

The selector is intentionally fed by already-received ticker WS state. It does
not perform REST discovery and does not run inside the signal decision path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..clock import utc_now_ms
from ..state import SymbolState, SymbolStateStore


@dataclass(frozen=True, slots=True)
class Live2UniverseMember:
    rank: int
    symbol: str
    market_id: str
    quote_volume_24h: float | None
    trade_count_24h: int | None
    last_price: float | None
    price_change_pct_24h: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "symbol": self.symbol,
            "market_id": self.market_id,
            "quote_volume_24h": self.quote_volume_24h,
            "trade_count_24h": self.trade_count_24h,
            "last_price": self.last_price,
            "price_change_pct_24h": self.price_change_pct_24h,
        }


@dataclass(frozen=True, slots=True)
class Live2UniverseSelection:
    mode: str
    selected_at_ms: int
    selected_symbols: tuple[str, ...]
    members: tuple[Live2UniverseMember, ...]
    considered_symbols: int
    eligible_symbols: int
    rejected_non_usdt: int
    rejected_missing_ticker: int
    rejected_below_quote_volume: int
    rejected_below_trade_count: int
    max_symbols: int
    min_quote_volume_24h: float
    min_trade_count_24h: int
    source: str = "ticker_ws_state"

    def rank_by_symbol(self) -> dict[str, int]:
        return {member.symbol: member.rank for member in self.members}

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source,
            "selected_at_ms": self.selected_at_ms,
            "selected_symbols": len(self.selected_symbols),
            "considered_symbols": self.considered_symbols,
            "eligible_symbols": self.eligible_symbols,
            "rejected_non_usdt": self.rejected_non_usdt,
            "rejected_missing_ticker": self.rejected_missing_ticker,
            "rejected_below_quote_volume": self.rejected_below_quote_volume,
            "rejected_below_trade_count": self.rejected_below_trade_count,
            "max_symbols": self.max_symbols,
            "min_quote_volume_24h": self.min_quote_volume_24h,
            "min_trade_count_24h": self.min_trade_count_24h,
            "symbols": list(self.selected_symbols),
            "top": [member.as_dict() for member in self.members[:20]],
        }


class Live2UniverseSelector:
    """Select the startup aggTrade universe from ticker WS state.

    Explicit symbols win and are not liquidity-pruned. Auto selection is based
    on the ticker snapshot already present in SymbolStateStore. This keeps REST
    and disk/cache access out of the hot signal path.
    """

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        explicit_symbols: tuple[str, ...],
        max_symbols: int,
        min_quote_volume_24h: float,
        min_trade_count_24h: int,
    ) -> None:
        if max_symbols <= 0:
            raise ValueError("max_symbols must be > 0")
        if min_quote_volume_24h < 0:
            raise ValueError("min_quote_volume_24h must be >= 0")
        if min_trade_count_24h < 0:
            raise ValueError("min_trade_count_24h must be >= 0")
        self.state_store = state_store
        self.explicit_symbols = tuple(dict.fromkeys(symbol.strip() for symbol in explicit_symbols if symbol.strip()))
        self.max_symbols = int(max_symbols)
        self.min_quote_volume_24h = float(min_quote_volume_24h)
        self.min_trade_count_24h = int(min_trade_count_24h)

    def select(self) -> Live2UniverseSelection:
        states = self.state_store.snapshot()
        selected_at_ms = utc_now_ms()
        if self.explicit_symbols:
            state_by_symbol = {state.symbol: state for state in states}
            members: list[Live2UniverseMember] = []
            for rank, symbol in enumerate(self.explicit_symbols, start=1):
                state = state_by_symbol.get(symbol)
                members.append(_member_from_state(rank=rank, symbol=symbol, state=state))
            return Live2UniverseSelection(
                mode="explicit_symbols",
                selected_at_ms=selected_at_ms,
                selected_symbols=self.explicit_symbols,
                members=tuple(members),
                considered_symbols=len(self.explicit_symbols),
                eligible_symbols=len(self.explicit_symbols),
                rejected_non_usdt=0,
                rejected_missing_ticker=0,
                rejected_below_quote_volume=0,
                rejected_below_trade_count=0,
                max_symbols=self.max_symbols,
                min_quote_volume_24h=self.min_quote_volume_24h,
                min_trade_count_24h=self.min_trade_count_24h,
            )

        considered = 0
        eligible: list[SymbolState] = []
        rejected_non_usdt = 0
        rejected_missing_ticker = 0
        rejected_below_quote_volume = 0
        rejected_below_trade_count = 0
        for state in states:
            considered += 1
            if not state.symbol.endswith("/USDT:USDT"):
                rejected_non_usdt += 1
                continue
            if state.ticker_status != "ok" or state.ticker_quote_volume_24h is None:
                rejected_missing_ticker += 1
                continue
            if state.ticker_quote_volume_24h < self.min_quote_volume_24h:
                rejected_below_quote_volume += 1
                continue
            trade_count = state.ticker_trade_count_24h
            if trade_count is None or trade_count < self.min_trade_count_24h:
                rejected_below_trade_count += 1
                continue
            eligible.append(state)

        eligible.sort(
            key=lambda state: (
                -(state.ticker_quote_volume_24h or 0.0),
                -(state.ticker_trade_count_24h or 0),
                state.symbol,
            )
        )
        selected_states = tuple(eligible[: self.max_symbols])
        members = tuple(
            _member_from_state(rank=rank, symbol=state.symbol, state=state)
            for rank, state in enumerate(selected_states, start=1)
        )
        return Live2UniverseSelection(
            mode="auto_ticker_liquidity",
            selected_at_ms=selected_at_ms,
            selected_symbols=tuple(member.symbol for member in members),
            members=members,
            considered_symbols=considered,
            eligible_symbols=len(eligible),
            rejected_non_usdt=rejected_non_usdt,
            rejected_missing_ticker=rejected_missing_ticker,
            rejected_below_quote_volume=rejected_below_quote_volume,
            rejected_below_trade_count=rejected_below_trade_count,
            max_symbols=self.max_symbols,
            min_quote_volume_24h=self.min_quote_volume_24h,
            min_trade_count_24h=self.min_trade_count_24h,
        )


def _member_from_state(*, rank: int, symbol: str, state: SymbolState | None) -> Live2UniverseMember:
    if state is None:
        return Live2UniverseMember(
            rank=rank,
            symbol=symbol,
            market_id="",
            quote_volume_24h=None,
            trade_count_24h=None,
            last_price=None,
            price_change_pct_24h=None,
        )
    return Live2UniverseMember(
        rank=rank,
        symbol=symbol,
        market_id=state.ticker_market_id,
        quote_volume_24h=state.ticker_quote_volume_24h,
        trade_count_24h=state.ticker_trade_count_24h,
        last_price=state.ticker_last_price,
        price_change_pct_24h=state.ticker_price_change_pct_24h,
    )
