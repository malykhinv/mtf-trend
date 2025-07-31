from __future__ import annotations

"""Simple genetic algorithm optimizer for strategy parameters.

The optimizer evolves a population of parameter sets (a *genome*) to
maximize a fitness metric on a training subset of data.  The best genome
is then evaluated on the hold-out test set.  The evaluation metric is:

    Sharpe Ratio * Win Rate * (Profit / Drawdown)

The strategy used for evaluation is a very small moving-average crossover
system with stop-loss and take-profit levels derived from recent price
range (``cluster_width``).
"""

from dataclasses import dataclass
import random
from typing import List, Tuple

import numpy as np
import pandas as pd


@dataclass
class Genome:
    """Container for strategy parameters."""

    cluster_width: int  # lookback for recent range calculation
    sl_ratio: float     # stop-loss multiple of the range
    tp_ratio: float     # take-profit multiple of the range
    ema_fast: int       # fast EMA period
    ema_slow: int       # slow EMA period


# Parameter boundaries used for random initialization and mutation
BOUNDS = {
    "cluster_width": (5, 50),
    "sl_ratio": (0.5, 3.0),
    "tp_ratio": (0.5, 5.0),
    "ema_fast": (5, 50),
    "ema_slow": (10, 200),
}


def random_genome() -> Genome:
    """Return a randomly initialized :class:`Genome`."""

    while True:
        g = Genome(
            cluster_width=random.randint(*BOUNDS["cluster_width"]),
            sl_ratio=random.uniform(*BOUNDS["sl_ratio"]),
            tp_ratio=random.uniform(*BOUNDS["tp_ratio"]),
            ema_fast=random.randint(*BOUNDS["ema_fast"]),
            ema_slow=random.randint(*BOUNDS["ema_slow"]),
        )
        if g.ema_fast < g.ema_slow:
            return g


def mutate(genome: Genome, rate: float = 0.1) -> Genome:
    """Randomly mutate ``genome`` with the given ``rate``."""

    data = genome.__dict__.copy()
    for key, (low, high) in BOUNDS.items():
        if random.random() < rate:
            if isinstance(low, int) and isinstance(high, int):
                data[key] = random.randint(low, high)
            else:
                data[key] = random.uniform(low, high)
    g = Genome(**data)
    if g.ema_fast >= g.ema_slow:
        g.ema_fast, g.ema_slow = min(g.ema_fast, g.ema_slow - 1), max(
            g.ema_fast + 1, g.ema_slow
        )
    return g


def crossover(a: Genome, b: Genome) -> Genome:
    """Single-point crossover between ``a`` and ``b``."""

    keys = list(BOUNDS.keys())
    point = random.randint(1, len(keys) - 1)
    child_data = {}
    for i, key in enumerate(keys):
        child_data[key] = getattr(a, key) if i < point else getattr(b, key)
    child = Genome(**child_data)
    if child.ema_fast >= child.ema_slow:
        child.ema_fast = max(BOUNDS["ema_fast"][0], child.ema_slow - 1)
    return child


def evaluate(genome: Genome, data: pd.DataFrame) -> float:
    """Return the fitness value for ``genome`` on ``data``."""

    if data.empty:
        return 0.0

    df = data.copy()
    df["ema_fast"] = df["close"].ewm(span=genome.ema_fast).mean()
    df["ema_slow"] = df["close"].ewm(span=genome.ema_slow).mean()
    df["range"] = (
        df["close"].rolling(genome.cluster_width).max()
        - df["close"].rolling(genome.cluster_width).min()
    )
    df.dropna(inplace=True)

    returns: List[float] = []
    in_trade = False
    entry_price = 0.0
    stop = 0.0
    target = 0.0

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        row = df.iloc[i]

        if not in_trade:
            cross_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
            if cross_up:
                entry_price = row["close"]
                rng = row["range"] or 1e-6
                stop = entry_price - genome.sl_ratio * rng
                target = entry_price + genome.tp_ratio * rng
                in_trade = True
        else:
            price = row["close"]
            exit_trade = False
            if price <= stop or price >= target:
                exit_price = price
                exit_trade = True
            else:
                cross_down = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]
                if cross_down:
                    exit_price = price
                    exit_trade = True
            if exit_trade:
                ret = (exit_price / entry_price) - 1.0
                returns.append(ret)
                in_trade = False

    if not returns:
        return 0.0

    returns_arr = np.array(returns)
    sharpe = returns_arr.mean() / returns_arr.std(ddof=1) * np.sqrt(len(returns_arr)) if returns_arr.std(ddof=1) else 0.0
    winrate = (returns_arr > 0).mean()
    equity = returns_arr.cumsum()
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak).min()
    max_dd = abs(drawdown) if drawdown < 0 else 1e-9
    profit = returns_arr.sum()
    profit_dd = profit / max_dd if max_dd else 0.0
    return sharpe * winrate * profit_dd


def optimize(
    data: pd.DataFrame,
    population_size: int = 20,
    generations: int = 10,
) -> Tuple[Genome, float]:
    """Run a genetic algorithm to optimize strategy parameters.

    Parameters
    ----------
    data:
        Price history with at least a ``close`` column.
    population_size:
        Number of genomes in the population.
    generations:
        Number of evolutionary generations.

    Returns
    -------
    Tuple[Genome, float]
        The best genome found and its fitness on the test set.
    """

    if "close" not in data.columns:
        raise ValueError("Data must contain a 'close' column")

    split = int(len(data) * 0.7)
    train, test = data.iloc[:split], data.iloc[split:]

    population = [random_genome() for _ in range(population_size)]

    for _ in range(generations):
        scored = [(evaluate(g, train), g) for g in population]
        scored.sort(key=lambda x: x[0], reverse=True)
        elites = [g for _, g in scored[:2]]
        parents = [g for _, g in scored[: population_size // 2]]
        children: List[Genome] = elites.copy()
        while len(children) < population_size:
            a, b = random.sample(parents, 2)
            child = crossover(a, b)
            child = mutate(child)
            children.append(child)
        population = children

    final_scored = [(evaluate(g, train), g) for g in population]
    final_scored.sort(key=lambda x: x[0], reverse=True)
    best_genome = final_scored[0][1]
    test_score = evaluate(best_genome, test)
    return best_genome, test_score


__all__ = ["Genome", "optimize"]
