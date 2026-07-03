"""Pump-long research harness: universe construction, backtest, metrics.

Everything strategy-specific that used to live in throwaway ``tmp/*.py`` scripts
now lives here, once, on top of the Core-owned path simulator
(``anomaly_science.simulation.numpy_path``). A hypothesis is expressed as a
*universe config* (:mod:`.hypotheses`), never as a new hand-rolled simulation
loop. See ``docs/strategies/pump_long.md`` for the findings these produce.
"""

from anomaly_science.strategy.pump_long.research.backtest import (
    BacktestResult,
    run_backtest,
)
from anomaly_science.strategy.pump_long.research.context import (
    build_event_table,
    build_symbol_cache,
    load_symbol_arrays,
    prior_fade_context,
)
from anomaly_science.strategy.pump_long.research.metrics import (
    equity_curve,
    ev_excluding_best_month,
    max_drawdown,
    profit_factor,
    summarize,
    top_removed_to_negative,
)

__all__ = [
    "BacktestResult",
    "build_event_table",
    "build_symbol_cache",
    "equity_curve",
    "ev_excluding_best_month",
    "load_symbol_arrays",
    "max_drawdown",
    "prior_fade_context",
    "profit_factor",
    "run_backtest",
    "summarize",
    "top_removed_to_negative",
]
