"""Single pump-long backtest runner over the Core path simulator.

A hypothesis supplies a filtered events-with-context frame (see
:mod:`.context`) and a small set of mechanical knobs; this module runs the
registered :func:`simulate_long_path` per event and returns a metrics-ready
result. There is exactly one simulation loop in the codebase (Core's), one
universe builder, and one metrics implementation - hypotheses never re-roll
any of them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import STATUS_FILLED, simulate_long_path
from anomaly_science.strategy.pump_long.research.metrics import TradeSetSummary, summarize
from anomaly_science.strategy.pump_long.spec import (
    PUMP_LONG_EXECUTION_SPEC,
    PumpLongExecutionSpec,
)

# Target rule: which prior-resistance column becomes the (0.98x) take-profit.
TARGET_COLUMNS = {"near": "near", "far": "far", "level": "level"}


@dataclass(frozen=True, slots=True)
class BacktestResult:
    net: np.ndarray
    r_multiple: np.ndarray
    months: np.ndarray
    weeks: np.ndarray
    target_hit: np.ndarray

    def summary(self, *, risk: float = 0.03) -> TradeSetSummary:
        return summarize(
            self.net, r_multiples=self.r_multiple, months=self.months, weeks=self.weeks, risk=risk
        )


def run_backtest(
    events: pd.DataFrame,
    symbol_cache: dict[str, tuple[np.ndarray, ...]],
    *,
    spec: PumpLongExecutionSpec = PUMP_LONG_EXECUTION_SPEC,
    target: str | None = "near",
    target_undercut: float = 0.98,
    stage_switch_r: float | None = 2.0,
    swing_reversal_atr: float | None = 2.5,
    stop_column: str = "base",
) -> BacktestResult:
    """Run the long strategy over ``events`` and collect per-trade outcomes.

    ``target`` is one of ``near`` / ``far`` / ``level`` (a resistance column on
    ``events``) or ``None`` for a pure trailing exit. ``stage_switch_r`` and
    ``swing_reversal_atr`` select the stage-dependent structural exit (the
    winning config). Costs come from ``spec`` - pass a frictionless spec to
    inspect gross mechanics.
    """

    target_col = TARGET_COLUMNS.get(target) if target else None
    net: list[float] = []
    r_mult: list[float] = []
    months: list[str] = []
    weeks: list[str] = []
    hits: list[bool] = []
    for row in events.itertuples():
        arrays = symbol_cache.get(row.symbol)
        if arrays is None:
            continue
        ts, open_, high, low, close, atr = arrays
        snap0 = int(row.snap0)
        d0 = int(np.searchsorted(ts, snap0))
        entry_index = d0 + 1
        if entry_index >= len(ts) or int(ts[d0]) != snap0:
            continue
        base = float(getattr(row, stop_column))
        take_profit = None
        if target_col is not None:
            level = float(getattr(row, target_col))
            if np.isfinite(level):
                candidate = level * target_undercut
                if candidate > float(open_[entry_index]):
                    take_profit = candidate
        result = simulate_long_path(
            open_=open_, high=high, low=low, close=close,
            entry_index=entry_index, initial_stop_price=base, spec=spec,
            take_profit_price=take_profit, swing_reversal_atr=swing_reversal_atr,
            stage_switch_r=stage_switch_r, atr=atr,
        )
        if result.status != STATUS_FILLED:
            continue
        net.append(result.net_return)
        r_mult.append(result.net_r)
        months.append(row.mo)
        weeks.append(row.week)
        hits.append(take_profit is not None and result.exit_price >= take_profit * (1 - 1e-9))
    return BacktestResult(
        net=np.asarray(net),
        r_multiple=np.asarray(r_mult),
        months=np.asarray(months),
        weeks=np.asarray(weeks),
        target_hit=np.asarray(hits, dtype=bool),
    )


__all__ = ["BacktestResult", "run_backtest"]
