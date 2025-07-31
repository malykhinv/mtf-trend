import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backtester import run_backtest


def create_sample_data(path: Path) -> None:
    idx = pd.date_range("2024-05-01", periods=22, freq="1H")
    data = {
        "timestamp": idx,
        "open": [100] * 18 + [100, 100.5, 101.0, 108.0],
        "high": [101] * 20 + [110, 118],
        "low": [99] * 20 + [100, 107],
        "close": [100] * 18 + [100.5, 101.0, 108.0, 117.0],
        "volume": [100] * 20 + [300, 150],
        "open_interest": list(range(100, 120)) + [150, 160],
        "funding": [0.0] * 22,
    }
    df = pd.DataFrame(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def test_backtester_produces_trade(tmp_path):
    data_dir = tmp_path / "data" / "raw_data"
    csv_path = data_dir / "ETHUSDT.csv"
    create_sample_data(csv_path)

    trades_path = tmp_path / "trades.csv"
    equity_path = tmp_path / "equity.png"
    stats = run_backtest(
        "ETHUSDT", data_dir=data_dir, trades_path=trades_path, equity_path=equity_path
    )
    trades = pd.read_csv(trades_path)

    # two exit events: partial at tp1 and trailing stop for the rest
    assert len(trades) == 2
    # first exit is partial with half position remaining and stop as trail
    assert trades.loc[0, "remaining"] == 0.5
    assert trades.loc[0, "trail"] == trades.loc[0, "stop"]
    assert trades.loc[0, "pnl"] == 1.5
    # final exit closes remaining position and moves trailing stop higher
    assert trades.loc[1, "remaining"] == 0.0
    assert trades.loc[1, "trail"] > trades.loc[0, "trail"]
    # reported pnl equals sum of individual trade pnls
    assert abs(trades["pnl"].sum() - stats["pnl"]) < 1e-6
    assert equity_path.exists()
