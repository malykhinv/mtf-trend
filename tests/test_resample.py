from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.data.resample import resample_ohlcv

_MIN_MS = 60_000


def _synth_1m(n: int, start_ms: int) -> pd.DataFrame:
    ts = start_ms + np.arange(n) * _MIN_MS
    i = np.arange(n, dtype=float)
    return pd.DataFrame({
        "timestamp": ts,
        "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i,
        "volume": np.ones(n), "quote_volume": np.full(n, 10.0), "trade_count": np.full(n, 5.0),
        "taker_buy_base_volume": np.full(n, 0.6), "taker_buy_quote_volume": np.full(n, 6.0),
        "open_interest": 1000.0 + i,
        "long_liquidations_vol": np.zeros(n), "short_liquidations_vol": np.zeros(n),
        "oi_available": np.ones(n, bool), "missing_oi_flag": np.zeros(n, bool),
        "liquidation_available": np.ones(n, bool), "missing_liquidation_flag": np.zeros(n, bool),
    })


def test_resample_aligns_and_aggregates_ohlcv() -> None:
    # 30 aligned 1m bars starting on a :00 boundary -> two full 15m bars.
    start = pd.Timestamp("2025-01-01T00:00:00Z").value // 10**6
    out = resample_ohlcv(_synth_1m(30, start), "15min")

    assert len(out) == 2
    assert list(out["n_1m_bars"]) == [15, 15]
    # bar 0 spans minutes 0..14
    assert out.loc[0, "timestamp"] == start
    assert out.loc[0, "open"] == 100.0          # first
    assert out.loc[0, "high"] == 101.0 + 14      # max
    assert out.loc[0, "low"] == 99.0             # min
    assert out.loc[0, "close"] == 100.5 + 14     # last
    assert out.loc[0, "quote_volume"] == 15 * 10.0   # sum
    assert out.loc[0, "trade_count"] == 15 * 5.0     # sum
    assert out.loc[0, "open_interest"] == 1000.0 + 14  # last snapshot
    # bar 1 opens at the next :15 boundary
    assert out.loc[1, "timestamp"] == start + 15 * _MIN_MS
    assert out.loc[1, "open"] == 100.0 + 15


def test_partial_bucket_is_kept_with_bar_count() -> None:
    start = pd.Timestamp("2025-01-01T00:00:00Z").value // 10**6
    out = resample_ohlcv(_synth_1m(20, start), "15min")  # 15 + 5

    assert list(out["n_1m_bars"]) == [15, 5]
    assert out.loc[1, "high"] == 101.0 + 19  # aggregates only the present bars
