from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd
from joblib import dump, load

from utils.logger import LOG_PATH

# Default location where optimized thresholds are stored
DEFAULT_OUTPUT = Path("data") / "optimized_thresholds.joblib"


def analyze_trade_history(log_path: Path = LOG_PATH) -> Dict[str, float]:
    """Analyze trade history and compute optimized parameter thresholds.

    The optimizer inspects the trade log and derives thresholds based on
    historical behaviour of successful trades.  Currently the following
    metrics are supported:

    ``funding_rate``
        75th percentile of the absolute funding rate.
    ``basis``
        75th percentile of the absolute entry basis.
    ``holding_time``
        75th percentile of holding time in seconds.
    ``volume``
        75th percentile of traded quantity or volume column.
    ``liquidity``
        75th percentile of recorded liquidity values.
    """

    if not log_path.exists():
        return {}

    df = pd.read_excel(log_path)
    if df.empty:
        return {}

    if "pnl" in df.columns:
        df = df[df["pnl"] > 0]

    thresholds: Dict[str, float] = {}

    if "funding" in df.columns:
        thresholds["funding_rate"] = float(df["funding"].abs().quantile(0.75))

    if "entry_basis" in df.columns:
        thresholds["basis"] = float(df["entry_basis"].abs().quantile(0.75))

    if {"entry_time", "exit_time"}.issubset(df.columns):
        entry_times = pd.to_datetime(df["entry_time"], errors="coerce")
        exit_times = pd.to_datetime(df["exit_time"], errors="coerce")
        hold_seconds = (exit_times - entry_times).dt.total_seconds().dropna()
        if not hold_seconds.empty:
            thresholds["holding_time"] = float(hold_seconds.quantile(0.75))

    if "volume" in df.columns and not df["volume"].dropna().empty:
        thresholds["volume"] = float(df["volume"].quantile(0.75))
    elif "quantity" in df.columns and not df["quantity"].dropna().empty:
        thresholds["volume"] = float(df["quantity"].quantile(0.75))

    if "liquidity" in df.columns and not df["liquidity"].dropna().empty:
        thresholds["liquidity"] = float(df["liquidity"].quantile(0.75))

    return thresholds


def optimize_and_save(
    log_path: Path = LOG_PATH, out_path: Path = DEFAULT_OUTPUT
) -> Dict[str, float]:
    """Run optimization on trade history and persist the result using joblib."""
    thresholds = analyze_trade_history(log_path)
    if thresholds:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        dump(thresholds, out_path)
    return thresholds


async def periodic_optimization(
    min_hours: int = 24,
    max_hours: int = 48,
    log_path: Path = LOG_PATH,
    out_path: Path = DEFAULT_OUTPUT,
    on_update: Optional[Callable[[Dict[str, float]], None]] = None,
) -> None:
    """Periodically optimize parameters every ``min_hours``–``max_hours``.

    Parameters
    ----------
    min_hours, max_hours:
        Range of hours to wait between optimization runs.
    log_path, out_path:
        Locations of the trade log and output file.
    on_update:
        Optional callback invoked with the newly computed thresholds after
        each optimization run.  This allows callers to react to refreshed
        parameters.
    """

    while True:
        thresholds = optimize_and_save(log_path, out_path)
        if on_update and thresholds:
            on_update(thresholds)
        wait_hours = random.randint(min_hours, max_hours)
        await asyncio.sleep(wait_hours * 3600)


def load_thresholds(defaults: Dict[str, float], path: Path = DEFAULT_OUTPUT) -> Dict[str, float]:
    """Load optimized thresholds and merge with ``defaults``."""
    try:
        data = load(path)
        if isinstance(data, dict):
            return {**defaults, **data}
    except Exception:
        pass
    return defaults


if __name__ == "__main__":  # pragma: no cover - manual execution
    asyncio.run(periodic_optimization())
