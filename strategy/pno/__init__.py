from __future__ import annotations

from typing import TYPE_CHECKING

from strategy.pno.config import (
    PNO_BACKTEST_TIMEFRAME_PAIRS,
    PNO_DEFAULT_ENTRY_TIMEFRAME,
    PNO_DEFAULT_BACKTEST_TIMEFRAME_PAIR,
    PNO_DEFAULT_LIVE_TIMEFRAME_PAIR,
    PNO_DEFAULT_LEVELS_TIMEFRAME,
    PNO_LIVE_TIMEFRAME_PAIRS,
    PNO_SUPPORTED_ENTRY_TIMEFRAMES,
    PNO_SUPPORTED_LEVELS_TIMEFRAMES,
    PNO_SUPPORTED_TIMEFRAME_PAIRS,
    PnoParams,
    build_pno_grid,
    resolve_pno_default_timeframe_pair,
    validate_pno_params,
    validate_pno_timeframe_pair,
    with_pno_risk,
)

if TYPE_CHECKING:
    from strategy.pno.pno_strategy import PnoStrategy


def __getattr__(name: str) -> object:
    if name == "PnoStrategy":
        from strategy.pno.pno_strategy import PnoStrategy

        return PnoStrategy
    raise AttributeError(name)


__all__ = [
    "PNO_BACKTEST_TIMEFRAME_PAIRS",
    "PNO_DEFAULT_ENTRY_TIMEFRAME",
    "PNO_DEFAULT_BACKTEST_TIMEFRAME_PAIR",
    "PNO_DEFAULT_LIVE_TIMEFRAME_PAIR",
    "PNO_DEFAULT_LEVELS_TIMEFRAME",
    "PNO_LIVE_TIMEFRAME_PAIRS",
    "PNO_SUPPORTED_ENTRY_TIMEFRAMES",
    "PNO_SUPPORTED_LEVELS_TIMEFRAMES",
    "PNO_SUPPORTED_TIMEFRAME_PAIRS",
    "PnoParams",
    "PnoStrategy",
    "build_pno_grid",
    "resolve_pno_default_timeframe_pair",
    "validate_pno_params",
    "validate_pno_timeframe_pair",
    "with_pno_risk",
]
