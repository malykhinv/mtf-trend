"""Data preparation helpers for strategy/backtest pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from domain.models.trade_result import TradeResult


@dataclass(frozen=True, slots=True)
class VectorbtInputs:
    """Prepared inputs that can be directly consumed by vectorbt."""

    close: pd.Series
    entries: pd.Series
    exits: pd.Series
    equity_curve: pd.Series
    trades: pd.DataFrame


class DataPreparer:
    """Loads cached parquet data and normalizes it for backtest processing."""

    REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)

    def list_symbols(self, timeframe: Timeframe) -> list[str]:
        symbols: list[str] = []
        if not self._cache_dir.exists():
            return symbols

        for symbol_dir in self._cache_dir.iterdir():
            path = symbol_dir / timeframe.value / "data.parquet"
            if symbol_dir.is_dir() and path.exists():
                symbols.append(symbol_dir.name)
        return sorted(symbols)

    def load_symbol_data(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        path = self._cache_dir / symbol / timeframe.value / "data.parquet"
        if not path.exists():
            return pd.DataFrame()

        frame = pd.read_parquet(path)
        missing = [col for col in self.REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            return pd.DataFrame()

        normalized = frame.copy()
        normalized["symbol"] = symbol
        normalized["datetime"] = pd.to_datetime(normalized["timestamp"], unit="ms", utc=True, errors="coerce")
        normalized = normalized.dropna(subset=["datetime"])

        numeric_cols = [col for col in ("open", "high", "low", "close", "volume", "open_interest") if col in normalized.columns]
        for col in numeric_cols:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

        normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"])
        normalized = normalized.sort_values("datetime").drop_duplicates(subset=["timestamp"], keep="last")
        return normalized.reset_index(drop=True)

    @staticmethod
    def prepare_vectorbt_inputs(trades: list[TradeResult], initial_price: float = 100.0) -> VectorbtInputs:
        """Build synthetic price/signals/equity series from closed trades."""
        if not trades:
            index = pd.DatetimeIndex([], tz="UTC")
            empty_float = pd.Series([], index=index, dtype="float64")
            empty_bool = pd.Series([], index=index, dtype="bool")
            trades_frame = pd.DataFrame(columns=["entry_time", "exit_time", "pnl", "pnl_percent", "result_type"])
            return VectorbtInputs(
                close=empty_float,
                entries=empty_bool,
                exits=empty_bool,
                equity_curve=empty_float,
                trades=trades_frame,
            )

        trades_sorted = sorted(trades, key=lambda trade: (trade.entry_time, trade.exit_time))
        trade_rows = [
            {
                "entry_time": pd.Timestamp(trade.entry_time, tz="UTC"),
                "exit_time": pd.Timestamp(trade.exit_time, tz="UTC"),
                "pnl": float(trade.pnl),
                "pnl_percent": float(trade.pnl_percent.value),
                "result_type": trade.result_type.value,
            }
            for trade in trades_sorted
        ]
        trades_frame = pd.DataFrame(trade_rows)

        timeline = pd.DatetimeIndex(
            sorted(set(trades_frame["entry_time"].tolist() + trades_frame["exit_time"].tolist())),
            tz="UTC",
        )
        close = pd.Series(initial_price, index=timeline, dtype="float64")
        entries = pd.Series(False, index=timeline, dtype="bool")
        exits = pd.Series(False, index=timeline, dtype="bool")
        equity_curve = pd.Series(0.0, index=timeline, dtype="float64")

        running_price = float(initial_price)
        cumulative_pnl = 0.0
        for trade in trade_rows:
            entry_time = trade["entry_time"]
            exit_time = trade["exit_time"]
            pnl_percent = trade["pnl_percent"]
            pnl = trade["pnl"]

            entries.loc[entry_time] = True
            exits.loc[exit_time] = True
            close.loc[entry_time] = running_price

            running_price *= 1.0 + (pnl_percent / 100.0)
            close.loc[exit_time] = running_price

            cumulative_pnl += pnl
            equity_curve.loc[exit_time] = cumulative_pnl

        close = close.ffill()
        equity_curve = equity_curve.replace(0.0, pd.NA).ffill().fillna(0.0)

        return VectorbtInputs(
            close=close,
            entries=entries,
            exits=exits,
            equity_curve=equity_curve,
            trades=trades_frame,
        )
