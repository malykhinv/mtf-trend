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
from utils.plotting import plot_equity
from utils.market_analysis import (
    load_btc_eth_candles,
    has_consecutive_move,
    price_above_ema,
)


@dataclass
class Trade:
    """Container describing a completed trade."""

    symbol: str
    direction: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    tp1: float
    tp2: float
    remaining: float
    trail: float
    exit_time: pd.Timestamp
    exit: float
    pnl: float
    rr: float


@dataclass
class Position:
    """State of an open trade."""

    symbol: str
    direction: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    tp1: float
    tp2: float
    remaining: float = 1.0
    trail: float | None = None
    peak: float | None = None
    tp1_hit: bool = False


def load_data(symbol: str, data_dir: str | Path) -> pd.DataFrame:
    """Load OHLCV data for ``symbol`` from ``data_dir``."""
    path = Path(data_dir) / f"{symbol}.csv"
    if not path.exists():
        alt = Path(data_dir) / f"{symbol}_1m.csv"
        if alt.exists():
            path = alt
        else:
            raise FileNotFoundError(
                f"No data for {symbol} at {path} or {alt}"
            )
    return pd.read_csv(path, parse_dates=["timestamp"])


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute helper columns used during the backtest."""

    df = df.copy()
    change = df["close"].diff().fillna(0)
    direction = np.where(change > 0, 1, np.where(change < 0, -1, 0))
    df["cvd"] = (direction * df["volume"].astype(float)).cumsum()
    df["oi"] = df.get("open_interest", pd.Series(0, index=df.index)).astype(float)
    df["delta_oi"] = df["oi"].diff().fillna(0)
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
    equity_path: str | Path | None = None,
    avg_volume_mult: float = 1.5,
    delta_volume_mult: float = 2.0,
    tp1_rr: float = 1.5,
    tp2_rr: float = 3.0,
) -> dict:
    """Run a simple breakout backtest and return summary statistics.

    Parameters
    ----------
    avg_volume_mult:
        Multiplier applied to the rolling average of volume.
    delta_volume_mult:
        Multiplier applied to the standard deviation of volume changes.
    tp1_rr, tp2_rr:
        Risk-reward multiples for the first and second take profit levels.
    """

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
    open_trade: Optional[Position] = None
    trades: List[Trade] = []
    rr_list: List[float] = []
    wins = 0

    def record_exit(pos: Position, price: float, size: float, ts: pd.Timestamp) -> None:
        nonlocal equity, peak_equity, max_drawdown, rr_list, wins, trades
        pnl = (price - pos.entry) * size if pos.direction == "long" else (pos.entry - price) * size
        risk_unit = abs(pos.entry - pos.stop)
        rr = ((price - pos.entry) if pos.direction == "long" else (pos.entry - price)) / risk_unit if risk_unit else 0.0
        equity += pnl
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity)
        rr_list.append(rr)
        if pnl > 0:
            wins += 1
        trades.append(
            Trade(
                pos.symbol,
                pos.direction,
                pos.entry_time,
                pos.entry,
                pos.stop,
                pos.tp1,
                pos.tp2,
                pos.remaining,
                pos.trail if pos.trail is not None else pos.stop,
                ts,
                price,
                pnl,
                rr,
            )
        )

    for i, row in df.iterrows():
        # Manage open trade
        if open_trade is not None:
            active_stop = open_trade.trail if open_trade.trail is not None else open_trade.stop
            if open_trade.direction == "long":
                if row["low"] <= active_stop:
                    size = open_trade.remaining
                    open_trade.remaining = 0.0
                    record_exit(open_trade, active_stop, size, row["timestamp"])
                    open_trade = None
                    continue
                if not open_trade.tp1_hit and row["high"] >= open_trade.tp1:
                    size = open_trade.remaining / 2
                    open_trade.remaining -= size
                    open_trade.tp1_hit = True
                    open_trade.peak = open_trade.tp1
                    open_trade.trail = open_trade.stop
                    record_exit(open_trade, open_trade.tp1, size, row["timestamp"])
                if open_trade.tp1_hit:
                    open_trade.peak = max(open_trade.peak or open_trade.tp1, row["high"])
                    new_trail = open_trade.peak * (1 - 0.002)
                    if open_trade.trail is None or new_trail > open_trade.trail:
                        open_trade.trail = new_trail
                    active_stop = open_trade.trail
                    if row["low"] <= active_stop:
                        size = open_trade.remaining
                        open_trade.remaining = 0.0
                        record_exit(open_trade, active_stop, size, row["timestamp"])
                        open_trade = None
                        continue
                    if row["high"] >= open_trade.tp2:
                        size = open_trade.remaining
                        open_trade.remaining = 0.0
                        record_exit(open_trade, open_trade.tp2, size, row["timestamp"])
                        open_trade = None
                        continue
            else:  # short
                if row["high"] >= active_stop:
                    size = open_trade.remaining
                    open_trade.remaining = 0.0
                    record_exit(open_trade, active_stop, size, row["timestamp"])
                    open_trade = None
                    continue
                if not open_trade.tp1_hit and row["low"] <= open_trade.tp1:
                    size = open_trade.remaining / 2
                    open_trade.remaining -= size
                    open_trade.tp1_hit = True
                    open_trade.peak = open_trade.tp1
                    open_trade.trail = open_trade.stop
                    record_exit(open_trade, open_trade.tp1, size, row["timestamp"])
                if open_trade.tp1_hit:
                    open_trade.peak = min(open_trade.peak or open_trade.tp1, row["low"])
                    new_trail = open_trade.peak * (1 + 0.002)
                    if open_trade.trail is None or new_trail < open_trade.trail:
                        open_trade.trail = new_trail
                    active_stop = open_trade.trail
                    if row["high"] >= active_stop:
                        size = open_trade.remaining
                        open_trade.remaining = 0.0
                        record_exit(open_trade, active_stop, size, row["timestamp"])
                        open_trade = None
                        continue
                    if row["low"] <= open_trade.tp2:
                        size = open_trade.remaining
                        open_trade.remaining = 0.0
                        record_exit(open_trade, open_trade.tp2, size, row["timestamp"])
                        open_trade = None
                        continue

        if open_trade is not None:
            continue

        # No open trade -> look for new signal
        window = df.iloc[: i + 1]
        clusters = find_tight_range_clusters(
            window[["timestamp", "high", "low", "close"]],
            atr_multiplier=0.5,
            min_bars=10,
            max_bars=30,
        )
        if clusters.empty:
            continue
        level = clusters.iloc[[-1]][["high", "low"]]
        cvd_series = window["cvd"]
        delta_oi_series = window["delta_oi"]
        volume_stats = pd.Series({"avg_volume": window["avg_volume"].iloc[-1]})
        funding = float(window["funding"].iloc[-1])
        signals = evaluate_breakout(
            window[["open", "high", "low", "close", "volume"]],
            level,
            cvd_series,
            delta_oi_series,
            volume_stats,
            funding,
            avg_volume_mult=avg_volume_mult,
            delta_volume_mult=delta_volume_mult,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
        )
        if not signals:
            continue
        sig = signals[0]

        # Evaluate broader market trend using recent BTC candles
        candles = load_btc_eth_candles()
        btc = candles.get("BTC/USDT")
        btc_down = has_consecutive_move(btc, "down")
        btc_up = has_consecutive_move(btc, "up")
        btc_above = price_above_ema(btc)

        allow_long = not (btc_down or not btc_above)
        allow_short = not btc_up

        if sig.direction == "long" and not allow_long:
            continue
        if sig.direction == "short" and not allow_short:
            continue
        open_trade = Position(
            symbol=symbol,
            direction=sig.direction,
            entry_time=row["timestamp"],
            entry=sig.entry,
            stop=sig.stop,
            tp1=sig.tp1,
            tp2=sig.tp2,
        )

    trades_df = pd.DataFrame([asdict(t) for t in trades])
    trades_df.to_csv(trades_path, index=False)

    if not trades_df.empty:
        fig = plot_equity(trades_df, price=df)
        equity_path = Path(equity_path) if equity_path is not None else Path("data") / "equity.png"
        equity_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(equity_path)

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
    parser.add_argument(
        "--avg-volume-mult",
        type=float,
        default=1.5,
        help="Average volume multiplier",
    )
    parser.add_argument(
        "--delta-volume-mult",
        type=float,
        default=2.0,
        help="Volume delta standard deviation multiplier",
    )
    parser.add_argument(
        "--tp1-rr",
        type=float,
        default=1.5,
        help="Risk-reward multiple for first take-profit",
    )
    parser.add_argument(
        "--tp2-rr",
        type=float,
        default=3.0,
        help="Risk-reward multiple for second take-profit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = run_backtest(
        args.symbol,
        args.start,
        args.end,
        data_dir=args.data_dir,
        avg_volume_mult=args.avg_volume_mult,
        delta_volume_mult=args.delta_volume_mult,
        tp1_rr=args.tp1_rr,
        tp2_rr=args.tp2_rr,
    )
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
