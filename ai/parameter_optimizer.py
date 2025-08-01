from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd
from joblib import dump, load
from sklearn.linear_model import LinearRegression

from utils.logger import LOG_PATH

# Default location where optimized thresholds are stored
DEFAULT_OUTPUT = Path("data") / "optimized_thresholds.joblib"


def analyze_trade_history(log_path: Path = LOG_PATH) -> Dict[str, float]:
    """Analyze trade history using linear regression to derive thresholds.

    A lightweight :class:`~sklearn.linear_model.LinearRegression` model is
    trained on historical trades to evaluate which features contribute most to
    profitability.  Thresholds for ``funding_rate``, ``basis``,
    ``holding_time``, ``volume`` and ``liquidity`` are then derived from the
    median values of trades that the model predicts to be profitable.
    """

    if not log_path.exists():
        return {}

    df = pd.read_excel(log_path)
    if df.empty or "pnl" not in df.columns:
        return {}

    features: Dict[str, pd.Series] = {}

    if "funding" in df.columns:
        features["funding_rate"] = df["funding"].abs()

    if "entry_basis" in df.columns:
        features["basis"] = df["entry_basis"].abs()

    if {"entry_time", "exit_time"}.issubset(df.columns):
        entry_times = pd.to_datetime(df["entry_time"], errors="coerce")
        exit_times = pd.to_datetime(df["exit_time"], errors="coerce")
        hold_seconds = (exit_times - entry_times).dt.total_seconds()
        features["holding_time"] = hold_seconds

    if "volume" in df.columns:
        features["volume"] = df["volume"]
    elif "quantity" in df.columns:
        features["volume"] = df["quantity"]

    if "liquidity" in df.columns:
        features["liquidity"] = df["liquidity"]

    if not features:
        return {}

    X = pd.DataFrame(features)
    X["pnl"] = df["pnl"]
    X = X.dropna()
    y = X.pop("pnl")

    if X.empty:
        return {}

    model = LinearRegression()
    model.fit(X, y)
    preds = model.predict(X)

    profitable = X[preds > 0]
    if profitable.empty:
        profitable = X

    thresholds: Dict[str, float] = {
        col: float(profitable[col].median()) for col in profitable.columns
    }

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
