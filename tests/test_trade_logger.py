import datetime as dt
from pathlib import Path

import pandas as pd

from utils.trade_logger import append_trade, daily_summary


def test_append_and_daily_summary(tmp_path):
    trades_path = tmp_path / "trades.csv"
    trade1 = {
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry_time": pd.Timestamp("2024-01-01T00:00:00Z"),
        "entry": 100.0,
        "stop": 95.0,
        "tp": 110.0,
        "exit_time": pd.Timestamp("2024-01-01T01:00:00Z"),
        "exit": 110.0,
        "pnl": 10.0,
        "rr": 2.0,
    }
    trade2 = {
        "symbol": "BTC/USDT",
        "direction": "short",
        "entry_time": pd.Timestamp("2024-01-01T02:00:00Z"),
        "entry": 120.0,
        "stop": 125.0,
        "tp": 110.0,
        "exit_time": pd.Timestamp("2024-01-01T03:00:00Z"),
        "exit": 125.0,
        "pnl": -5.0,
        "rr": -1.0,
    }
    append_trade(trade1, trades_path)
    append_trade(trade2, trades_path)

    summary = daily_summary(trades_path, date=dt.date(2024, 1, 1))
    assert summary["winrate"] == 0.5
    assert summary["avg_rr"] == 0.5
    assert summary["equity_change"] == 5.0
