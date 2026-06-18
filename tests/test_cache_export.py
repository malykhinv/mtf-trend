from __future__ import annotations

import pandas as pd

from anomaly_science.cache_export import CacheMvp1CsvExportConfig, export_cache_to_mvp1_csv


def test_export_cache_to_mvp1_csv_writes_explicit_boundary(tmp_path):
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "mvp1"
    cache_dir.mkdir()
    pd.DataFrame(
        {
            "timestamp": [1704067200000, 1704067260000, 1704067320000, 1704067380000, 1704067440000],
            "open": [1, 2, 3, 4, 5],
            "high": [2, 3, 4, 5, 6],
            "low": [0.5, 1.5, 2.5, 3.5, 4.5],
            "close": [1.5, 2.5, 3.5, 4.5, 5.5],
            "volume": [10, 11, 12, 13, 14],
            "quote_volume": [100, 110, 120, 130, 140],
            "trade_count": [1, 2, 3, 4, 5],
            "taker_buy_quote_volume": [50, 55, 60, 65, 70],
            "open_interest": [1000, 1001, 1002, 1003, 1004],
        }
    ).to_parquet(cache_dir / "BTCUSDT.parquet")

    export_cache_to_mvp1_csv(CacheMvp1CsvExportConfig(cache_dir=cache_dir, out_dir=out_dir, symbols=("BTCUSDT",)))

    candles_1m = pd.read_csv(out_dir / "candles_1m.csv")
    candles_5m = pd.read_csv(out_dir / "candles_5m.csv")
    oi_5m = pd.read_csv(out_dir / "open_interest_5m.csv")
    assert list(candles_1m.columns) == [
        "symbol",
        "open_time_ms",
        "available_time_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "number_of_trades",
        "taker_buy_quote_volume",
        "open_interest",
    ]
    assert candles_1m["symbol"].unique().tolist() == ["BTCUSDT"]
    assert len(candles_5m) == 1
    assert oi_5m.iloc[0]["source"] == "binance_vision_cache"
