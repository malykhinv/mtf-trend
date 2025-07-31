import pandas as pd

from utils.breakout_signals import evaluate_breakout, Signal


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
    volume_stats = pd.Series({"avg_volume": 120})
    funding = 0.005

    signals = evaluate_breakout(ohlcv, cluster, cvd, oi, volume_stats, funding)
    assert signals, "Expected a breakout signal"
    sig = signals[0]
    assert isinstance(sig, Signal)
    assert sig.direction == "long"
    assert sig.entry == 102
    assert sig.stop == 98
    assert sig.tp1 == 106
    assert sig.tp2 == 110
