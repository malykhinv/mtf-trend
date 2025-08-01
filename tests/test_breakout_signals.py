import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.breakout_signals import (
    Signal,
    allow_short,
    evaluate_breakout,
)


def test_long_breakout_signal():
    idx = pd.date_range("2024-01-01", periods=5, freq="1min")
    ohlcv = pd.DataFrame(
        {
            "open": [90, 95, 97, 99, 101],
            "high": [91, 96, 98, 100, 105],
            "low": [89, 94, 96, 98, 100],
            "close": [95, 97, 99, 101, 104],
            "volume": [100, 120, 110, 130, 300],
        },
        index=idx,
    )
    cluster = pd.DataFrame({"high": [102], "low": [98]})
    cvd = pd.Series([1, 2, 3, 4, 6], index=idx)
    oi = pd.Series([100, 102, 103, 105, 110], index=idx)
    delta_oi = oi.diff().fillna(0)
    volume_stats = pd.Series({"avg_volume": 120})
    funding = 0.005

    signals = evaluate_breakout(
        ohlcv,
        cluster,
        cvd,
        delta_oi,
        volume_stats,
        funding,
        avg_volume_mult=1.5,
    )
    assert signals, "Expected a breakout signal"
    sig = signals[0]
    assert isinstance(sig, Signal)
    assert sig.direction == "long"
    assert sig.entry == 102
    assert sig.stop == 98
    assert sig.tp1 == 108
    assert sig.tp2 == 114


def test_breakout_blocked_by_trend_filter():
    idx = pd.date_range("2024-01-01", periods=5, freq="1min")
    ohlcv = pd.DataFrame(
        {
            "open": [90, 95, 97, 100, 99],
            "high": [91, 96, 98, 101, 105],
            "low": [89, 94, 96, 99, 98],
            "close": [95, 97, 99, 99, 104],  # candle 4 is bearish
            "volume": [100, 120, 110, 130, 300],
        },
        index=idx,
    )
    cluster = pd.DataFrame({"high": [102], "low": [98]})
    cvd = pd.Series([1, 2, 3, 4, 6], index=idx)
    oi = pd.Series([100, 102, 103, 105, 110], index=idx)
    delta_oi = oi.diff().fillna(0)
    volume_stats = pd.Series({"avg_volume": 120})
    funding = 0.005

    # Trend filter should block this potential breakout
    signals = evaluate_breakout(
        ohlcv,
        cluster,
        cvd,
        delta_oi,
        volume_stats,
        funding,
        avg_volume_mult=1.5,
    )
    assert signals == []


def test_allow_short():
    idx = pd.date_range("2024-01-01", periods=3, freq="5min")
    df = pd.DataFrame(
        {
            "open": [100, 98, 97],
            "high": [101, 99, 98],
            "low": [99, 97, 95],
            "close": [98, 97, 95],
            "volume": [50, 60, 70],
        },
        index=idx,
    )

    assert allow_short(df)

