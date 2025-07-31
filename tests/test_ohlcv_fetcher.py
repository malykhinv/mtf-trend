from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils import ohlcv_fetcher as of


def test_update_data_saves_with_timeframe(tmp_path, monkeypatch):
    def fake_fetch(exchange, symbol, timeframe, since, limit, max_retries):
        return [
            [since, 1, 1, 1, 1, 1],
            [since + 60_000, 1, 1, 1, 1, 1],
        ]

    monkeypatch.setattr(of, "_safe_fetch", fake_fetch)

    start = pd.Timestamp("2023-01-01")
    end = start + pd.Timedelta(minutes=1)
    path = of.update_data(
        "TEST/USDT", start, end, timeframe="1m", limit=2, data_dir=tmp_path
    )

    assert path == Path(tmp_path) / "TESTUSDT_1m.csv"
    df = pd.read_csv(path)
    assert len(df) == 2


def test_fetch_timeframes_creates_files(tmp_path, monkeypatch):
    def fake_update_data(symbol, start, end, timeframe, **kwargs):
        file_path = Path(kwargs.get("data_dir", tmp_path)) / f"{symbol.replace('/', '')}_{timeframe}.csv"
        Path(kwargs.get("data_dir", tmp_path)).mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {"timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        ).to_csv(file_path, index=False)
        return file_path

    monkeypatch.setattr(of, "update_data", fake_update_data)

    paths = of.fetch_timeframes(
        "TEST/USDT", ["1m", "5m"], limit=2, data_dir=tmp_path
    )

    names = sorted(p.name for p in paths)
    assert names == ["TESTUSDT_1m.csv", "TESTUSDT_5m.csv"]
