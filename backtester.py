from __future__ import annotations

"""Simple backtesting engine for breakout strategy.

The script loads OHLCV data from CSV files, detects tight range clusters and
applies the breakout signal evaluation from :mod:`utils.breakout_signals` on
a bar-by-bar basis.  Trades are executed using a very small rule set and the
resulting statistics and trades are written to ``trades.csv``.

Usage
-----

    python backtester.py --symbol ETHUSDT --start 2024-05-01 --end 2024-07-01

"""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

import argparse
import pandas as pd
import numpy as np

from utils.range_clusters import find_tight_range_clusters
from utils.breakout_signals import evaluate_breakout, Signal


@dataclass
class Trade:
    """Container describing a completed trade."""

    symbol: str
    direction: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    tp: float
    exit_time: pd.Timestamp
    exit: float
    pnl: float
    rr: float


def load_data(symbol: str, data_dir: str | Path) -> pd.DataFrame:
    """Load OHLCV data for ``symbol`` from ``data_dir``."""

    path = Path(data_dir) / f"{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No data for {symbol} at {path}")
    return pd.read_csv(path, parse_dates=["timestamp"])


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute helper columns used during the backtest."""

    df = df.copy()
    change = df["close"].diff().fillna(0)
    direction = np.where(change > 0, 1, np.where(change < 0, -1, 0))
    df["cvd"] = (direction * df["volume"].astype(float)).cumsum()
    df["oi"] = df.get("open_interest", pd.Series(0, index=df.index)).astype(float)
    df["avg_volume"] = df["volume"].rolling(20).mean()
    if "funding" not in df.columns:
        df["funding"] = 0.0
    return df


def run_backtest(
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    *,
    data_dir: str | Path = "data/raw_data",
    trades_path: str | Path = "trades.csv",
) -> dict:
    """Run a simple breakout backtest and return summary statistics."""

    df = load_data(symbol, data_dir)
    if start:
        df = df[df["timestamp"] >= pd.to_datetime(start)]
    if end:
        df = df[df["timestamp"] <= pd.to_datetime(end)]
    df = df.reset_index(drop=True)
    df = compute_indicators(df)

    equity = 10000.0
    peak_equity = equity
    max_drawdown = 0.0
    open_trade: Optional[Trade] = None
    trades: List[Trade] = []
    rr_list: List[float] = []
    wins = 0

    for i, row in df.iterrows():
        # First manage any open trade using current bar
        if open_trade is not None:
            closed = False
            if open_trade.direction == "long":
                if row["low"] <= open_trade.stop:
                    exit_price = open_trade.stop
                    closed = True
                elif row["high"] >= open_trade.tp:
                    exit_price = open_trade.tp
                    closed = True
            else:  # short
                if row["high"] >= open_trade.stop:
                    exit_price = open_trade.stop
                    closed = True
                elif row["low"] <= open_trade.tp:
                    exit_price = open_trade.tp
                    closed = True
            if closed:
                pnl = exit_price - open_trade.entry if open_trade.direction == "long" else open_trade.entry - exit_price
                risk = abs(open_trade.entry - open_trade.stop)
                rr = pnl / risk if risk else 0.0
                equity += pnl
                peak_equity = max(peak_equity, equity)
                max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity)
                rr_list.append(rr)
                if pnl > 0:
                    wins += 1
                trades.append(
                    Trade(
                        symbol,
                        open_trade.direction,
                        open_trade.entry_time,
                        open_trade.entry,
                        open_trade.stop,
                        open_trade.tp,
                        row["timestamp"],
                        exit_price,
                        pnl,
                        rr,
                    )
                )
                open_trade = None
                continue  # move to next bar

        # No open trade -> look for new signal
        window = df.iloc[: i + 1]
        clusters = find_tight_range_clusters(
            window[["timestamp", "high", "low", "close"]],
            atr_multiplier=2.0,
            min_bars=5,
        )
        if clusters.empty:
            continue
        level = clusters.iloc[[-1]][["high", "low"]]
        cvd_series = window["cvd"]
        oi_series = window["oi"]
        volume_stats = pd.Series({"avg_volume": window["avg_volume"].iloc[-1]})
        funding = float(window["funding"].iloc[-1])
        signals = evaluate_breakout(
            window[["open", "high", "low", "close", "volume"]],
            level,
            cvd_series,
            oi_series,
            volume_stats,
            funding,
        )
        if not signals:
            continue
        sig = signals[0]
        open_trade = Trade(
            symbol=symbol,
            direction=sig.direction,
            entry_time=row["timestamp"],
            entry=sig.entry,
            stop=sig.stop,
            tp=sig.tp1,
            exit_time=row["timestamp"],
            exit=sig.entry,
            pnl=0.0,
            rr=0.0,
        )
        # Immediately check if trade would have hit stop or target in this bar
        if sig.direction == "long":
            if row["low"] <= sig.stop:
                exit_price = sig.stop
            elif row["high"] >= sig.tp1:
                exit_price = sig.tp1
            else:
                continue
            pnl = exit_price - sig.entry
            risk = abs(sig.entry - sig.stop)
            rr = pnl / risk if risk else 0.0
        else:  # short
            if row["high"] >= sig.stop:
                exit_price = sig.stop
            elif row["low"] <= sig.tp1:
                exit_price = sig.tp1
            else:
                continue
            pnl = sig.entry - exit_price
            risk = abs(sig.stop - sig.entry)
            rr = pnl / risk if risk else 0.0
        equity += pnl
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity)
        rr_list.append(rr)
        if pnl > 0:
            wins += 1
        trades.append(
            Trade(
                symbol,
                sig.direction,
                row["timestamp"],
                sig.entry,
                sig.stop,
                sig.tp1,
                row["timestamp"],
                exit_price,
                pnl,
                rr,
            )
        )
        open_trade = None

    trades_df = pd.DataFrame([asdict(t) for t in trades])
    trades_df.to_csv(trades_path, index=False)

    winrate = wins / len(trades) if trades else 0.0
    avg_rr = sum(rr_list) / len(rr_list) if rr_list else 0.0

    return {
        "equity": equity,
        "pnl": equity - 10000.0,
        "max_drawdown": max_drawdown,
        "winrate": winrate,
        "avg_rr": avg_rr,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple breakout backtester")
    parser.add_argument("--symbol", required=True, help="Symbol, e.g. ETHUSDT")
    parser.add_argument("--start", required=False, help="Start date (inclusive)")
    parser.add_argument("--end", required=False, help="End date (inclusive)")
    parser.add_argument(
        "--data-dir",
        default="data/raw_data",
        help="Directory containing CSV data",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = run_backtest(args.symbol, args.start, args.end, data_dir=args.data_dir)
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
