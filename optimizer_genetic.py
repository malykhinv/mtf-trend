from __future__ import annotations

"""Командный интерфейс для оптимизатора на генетическом алгоритме."""

import argparse
from dataclasses import asdict

import pandas as pd

from utils.ga_optimizer import optimize
from utils.ohlcv_fetcher import update_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize strategy parameters using a genetic algorithm",
    )
    parser.add_argument("--symbol", required=True, help="Trading pair symbol, e.g. BTC/USDT")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--population",
        type=int,
        default=20,
        help="Population size for the genetic algorithm",
    )
    parser.add_argument(
        "--generations",
        type=int,
        default=10,
        help="Number of generations to evolve",
    )
    args = parser.parse_args()

    csv_path = update_data(args.symbol, args.start, args.end, timeframe="1m")
    data = pd.read_csv(csv_path)
    best_genome, score = optimize(
        data, population_size=args.population, generations=args.generations
    )

    print("Best parameters:")
    for key, value in asdict(best_genome).items():
        print(f"  {key}: {value}")
    print(f"Fitness: {score:.4f}")


if __name__ == "__main__":
    main()
