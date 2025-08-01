from __future__ import annotations

"""Helper utilities for trade logging and daily summaries."""

from pathlib import Path
from typing import Dict, Any
import datetime as dt

import pandas as pd
import requests


def append_trade(trade: Dict[str, Any], trades_path: str | Path = "trades.csv") -> None:
    """Append ``trade`` information to ``trades.csv``.

    Parameters
    ----------
    trade:
        Dictionary containing trade details. Expected keys include
        ``symbol``, ``direction``, ``entry_time``, ``entry``, ``stop``,
        ``tp1``, ``tp2``, ``exit_time``, ``pnl``, ``rr`` and ``result``.
    trades_path:
        CSV file to append to. Will be created with headers if it does not
        yet exist.
    """

    path = Path(trades_path)
    columns = [
        "symbol",
        "direction",
        "entry_time",
        "entry",
        "stop",
        "tp1",
        "tp2",
        "exit_time",
        "pnl",
        "rr",
        "result",
    ]
    df = pd.DataFrame([trade], columns=columns)
    header = not path.exists()
    df.to_csv(path, mode="a", header=header, index=False)


def daily_summary(trades_path: str | Path = "trades.csv", date: dt.date | None = None) -> Dict[str, float]:
    """Return winrate, average RR and equity change for ``date``.

    Parameters
    ----------
    trades_path:
        Path to ``trades.csv``.
    date:
        Day to summarise. Defaults to today.
    """

    path = Path(trades_path)
    if not path.exists():
        return {"winrate": 0.0, "avg_rr": 0.0, "equity_change": 0.0}

    df = pd.read_csv(path, parse_dates=["entry_time", "exit_time"])
    if df.empty:
        return {"winrate": 0.0, "avg_rr": 0.0, "equity_change": 0.0}

    if date is None:
        date = dt.date.today()
    mask = df["exit_time"].dt.date == date
    day_trades = df.loc[mask]
    if day_trades.empty:
        return {"winrate": 0.0, "avg_rr": 0.0, "equity_change": 0.0}

    wins = (day_trades["pnl"] > 0).sum()
    total = len(day_trades)
    winrate = wins / total if total else 0.0
    avg_rr = day_trades["rr"].mean() if total else 0.0
    equity_change = day_trades["pnl"].sum()
    return {"winrate": winrate, "avg_rr": avg_rr, "equity_change": equity_change}


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    """Send ``text`` via Telegram bot API."""

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except requests.RequestException:
        # Best effort: log/ignore network issues silently
        pass
