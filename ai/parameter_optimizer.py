from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Dict

import pandas as pd
from joblib import dump, load

from utils.logger import LOG_PATH

# Default location where optimized thresholds are stored
DEFAULT_OUTPUT = Path("data") / "optimized_thresholds.joblib"


def analyze_trade_history(log_path: Path = LOG_PATH) -> Dict[str, float]:
    """Analyze trade history and compute new parameter thresholds.

    This simplistic optimizer looks at historical funding rates and
    derives a new threshold based on the 75th percentile of absolute
    funding values from profitable trades.
    """
    if not log_path.exists():
        return {}
    df = pd.read_excel(log_path)
    if df.empty or "funding" not in df.columns:
        return {}
    if "pnl" in df.columns:
        df = df[df["pnl"] > 0]
    funding_threshold = df["funding"].abs().quantile(0.75)
    return {"funding_rate": float(funding_threshold)}


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
) -> None:
    """Periodically optimize parameters every 24–48 hours."""
    while True:
        optimize_and_save(log_path, out_path)
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
