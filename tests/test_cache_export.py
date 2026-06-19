from __future__ import annotations

import json

import pandas as pd

from anomaly_science.cli import build_parser
from anomaly_science.cache_export import CacheMvp1CsvExportConfig, discover_cache_symbols, export_cache_to_mvp1_csv


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
    coverage = pd.read_csv(out_dir / "cache_export_coverage.csv")
    manifest = json.loads((out_dir / "cache_export_manifest.json").read_text(encoding="utf-8"))
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
    assert coverage.loc[0, "symbol"] == "BTCUSDT"
    assert coverage.loc[0, "rows_1m"] == 5
    assert coverage.loc[0, "missing_utc_days"] == 0
    assert bool(coverage.loc[0, "has_open_interest"])
    assert manifest["boundary"] == "mvp1_normalized_csv"
    assert manifest["requested_days"] is None
    assert manifest["effective_start_date"] == "2024-01-01"
    assert manifest["effective_end_date"] == "2024-01-01"
    assert {item["name"] for item in manifest["artifacts"]} == {
        "candles_1m.csv",
        "candles_5m.csv",
        "open_interest_5m.csv",
        "cache_export_coverage.csv",
    }


def test_export_cache_to_mvp1_csv_discovers_symbols_and_filters_days(tmp_path):
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "mvp1"
    cache_dir.mkdir()
    pd.DataFrame(
        {
            "timestamp": [1704067200000, 1704153600000],
            "open": [1, 2],
            "high": [2, 3],
            "low": [0.5, 1.5],
            "close": [1.5, 2.5],
            "volume": [10, 11],
            "quote_volume": [100, 110],
            "trade_count": [1, 2],
            "taker_buy_quote_volume": [50, 55],
        }
    ).to_parquet(cache_dir / "ETHUSDT.parquet")

    assert discover_cache_symbols(cache_dir) == ("ETHUSDT",)

    export_cache_to_mvp1_csv(CacheMvp1CsvExportConfig(cache_dir=cache_dir, out_dir=out_dir, days=1))

    candles_1m = pd.read_csv(out_dir / "candles_1m.csv")
    assert candles_1m["symbol"].unique().tolist() == ["ETHUSDT"]
    assert candles_1m["open_time_ms"].tolist() == [1704153600000]
    manifest = json.loads((out_dir / "cache_export_manifest.json").read_text(encoding="utf-8"))
    assert manifest["requested_days"] == 1
    assert manifest["effective_start_date"] == "2024-01-02"


def test_export_cache_cli_discovers_symbols_and_accepts_optional_days() -> None:
    args = build_parser().parse_args(
        ["export-cache-mvp1-csv", "--cache-dir", ".cache", "--out", "tmp/mvp1", "--days", "7"]
    )

    assert args.command == "export-cache-mvp1-csv"
    assert args.symbols == ""
    assert args.days == 7
