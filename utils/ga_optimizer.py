from __future__ import annotations

"""Simple genetic algorithm optimizer for strategy parameters.

The optimizer evolves a population of parameter sets (a *genome*) to
maximize a fitness metric on a training subset of data.  The best genome
is then evaluated on the hold-out test set.  The evaluation metric is:

    Sharpe Ratio * Win Rate * (Profit / Drawdown)

The strategy used for evaluation is a minimal breakout-style system that
relies on exponential moving averages, volume spikes and cumulative
volume delta (CVD).  Stop-loss and two take-profit levels are derived
from the recent price range (``cluster_width``) and user defined
risk-reward multiples.
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
    candle_depth: int   # lookback for volume averages
    sl: float           # stop-loss multiple of the range
    tp1_rr: float       # risk-reward for first take profit
    tp2_rr: float       # risk-reward for second take profit
    ema_short: int      # short EMA period
    ema_long: int       # long EMA period
    delta_volume_spike: float  # volume spike multiplier
    cvd_ema: int        # smoothing period for CVD


# Parameter boundaries used for random initialization and mutation
BOUNDS = {
    "cluster_width": (5, 50),
    "candle_depth": (5, 50),
    "sl": (0.5, 3.0),
    "tp1_rr": (0.5, 3.0),
    "tp2_rr": (1.0, 6.0),
    "ema_short": (5, 50),
    "ema_long": (10, 200),
    "delta_volume_spike": (1.0, 5.0),
    "cvd_ema": (1, 20),
}


def random_genome() -> Genome:
    """Return a randomly initialized :class:`Genome`."""

    while True:
        g = Genome(
            cluster_width=random.randint(*BOUNDS["cluster_width"]),
            candle_depth=random.randint(*BOUNDS["candle_depth"]),
            sl=random.uniform(*BOUNDS["sl"]),
            tp1_rr=random.uniform(*BOUNDS["tp1_rr"]),
            tp2_rr=random.uniform(*BOUNDS["tp2_rr"]),
            ema_short=random.randint(*BOUNDS["ema_short"]),
            ema_long=random.randint(*BOUNDS["ema_long"]),
            delta_volume_spike=random.uniform(*BOUNDS["delta_volume_spike"]),
            cvd_ema=random.randint(*BOUNDS["cvd_ema"]),
        )
        if g.ema_short < g.ema_long and g.tp1_rr < g.tp2_rr:
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
    if g.ema_short >= g.ema_long:
        g.ema_short, g.ema_long = min(g.ema_short, g.ema_long - 1), max(
            g.ema_short + 1, g.ema_long
        )
    if g.tp1_rr >= g.tp2_rr:
        g.tp1_rr, g.tp2_rr = sorted((g.tp1_rr, g.tp2_rr))
    return g


def crossover(a: Genome, b: Genome) -> Genome:
    """Single-point crossover between ``a`` and ``b``."""

    keys = list(BOUNDS.keys())
    point = random.randint(1, len(keys) - 1)
    child_data = {}
    for i, key in enumerate(keys):
        child_data[key] = getattr(a, key) if i < point else getattr(b, key)
    child = Genome(**child_data)
    if child.ema_short >= child.ema_long:
        child.ema_short = max(BOUNDS["ema_short"][0], child.ema_long - 1)
    if child.tp1_rr >= child.tp2_rr:
        child.tp1_rr, child.tp2_rr = sorted((child.tp1_rr, child.tp2_rr))
    return child


def evaluate(genome: Genome, data: pd.DataFrame) -> float:
    """Return the fitness value for ``genome`` on ``data``."""

    if data.empty:
        return 0.0

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(data.columns):
        missing = ", ".join(sorted(required - set(data.columns)))
        raise ValueError(f"Data must contain columns: {missing}")

    df = data.copy()
    df["ema_short"] = df["close"].ewm(span=genome.ema_short).mean()
    df["ema_long"] = df["close"].ewm(span=genome.ema_long).mean()
    df["range"] = (
        df["close"].rolling(genome.cluster_width).max()
        - df["close"].rolling(genome.cluster_width).min()
    )
    df["avg_volume"] = df["volume"].rolling(genome.candle_depth).mean()
    df["volume_spike"] = df["volume"] > (genome.delta_volume_spike * df["avg_volume"])

    change = df["close"].diff().fillna(0)
    direction_arr = np.where(change > 0, 1, np.where(change < 0, -1, 0))
    df["cvd"] = (direction_arr * df["volume"].astype(float)).cumsum()
    df["cvd_ema"] = df["cvd"].ewm(span=genome.cvd_ema).mean()
    df["cvd_delta"] = df["cvd_ema"].diff()

    df.dropna(inplace=True)

    returns: List[float] = []
    in_trade = False
    entry = stop = tp1 = tp2 = 0.0
    trade_dir = 1  # 1 long, -1 short
    hit_tp1 = False

    def trade_return(entry_price: float, exit_price: float, dir_: int) -> float:
        return (exit_price / entry_price) - 1.0 if dir_ == 1 else (entry_price / exit_price) - 1.0

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        row = df.iloc[i]

        if not in_trade:
            cross_up = prev["ema_short"] <= prev["ema_long"] and row["ema_short"] > row["ema_long"]
            cross_down = prev["ema_short"] >= prev["ema_long"] and row["ema_short"] < row["ema_long"]
            vol_ok = bool(row["volume_spike"])
            cvd_up = row["cvd_delta"] > 0
            cvd_down = row["cvd_delta"] < 0

            rng = row["range"] or 1e-6

            if cross_up and vol_ok and cvd_up:
                entry = row["close"]
                risk = genome.sl * rng
                stop = entry - risk
                tp1 = entry + genome.tp1_rr * risk
                tp2 = entry + genome.tp2_rr * risk
                in_trade = True
                trade_dir = 1
                hit_tp1 = False
            elif cross_down and vol_ok and cvd_down:
                entry = row["close"]
                risk = genome.sl * rng
                stop = entry + risk
                tp1 = entry - genome.tp1_rr * risk
                tp2 = entry - genome.tp2_rr * risk
                in_trade = True
                trade_dir = -1
                hit_tp1 = False
        else:
            price = row["close"]
            if trade_dir == 1:  # long
                if not hit_tp1:
                    if price <= stop:
                        returns.append(trade_return(entry, stop, trade_dir))
                        in_trade = False
                    elif price >= tp1:
                        hit_tp1 = True
                        stop = entry
                else:
                    if price <= stop:
                        returns.append(trade_return(entry, stop, trade_dir))
                        in_trade = False
                    elif price >= tp2:
                        returns.append(trade_return(entry, tp2, trade_dir))
                        in_trade = False
            else:  # short
                if not hit_tp1:
                    if price >= stop:
                        returns.append(trade_return(entry, stop, trade_dir))
                        in_trade = False
                    elif price <= tp1:
                        hit_tp1 = True
                        stop = entry
                else:
                    if price >= stop:
                        returns.append(trade_return(entry, stop, trade_dir))
                        in_trade = False
                    elif price <= tp2:
                        returns.append(trade_return(entry, tp2, trade_dir))
                        in_trade = False

    if not returns:
        return 0.0

    returns_arr = np.array(returns)
    sharpe = (
        returns_arr.mean() / returns_arr.std(ddof=1) * np.sqrt(len(returns_arr))
        if returns_arr.std(ddof=1)
        else 0.0
    )
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

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(data.columns):
        missing = ", ".join(sorted(required - set(data.columns)))
        raise ValueError(f"Data must contain columns: {missing}")

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
