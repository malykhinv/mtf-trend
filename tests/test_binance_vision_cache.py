from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

pl = pytest.importorskip("polars")

from anomaly_science.binance_vision_cache import CacheConfig, DownloadedBlockFiles, process_block, read_metrics


def _zip_csv(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def _klines_zip() -> bytes:
    return _zip_csv(
        "klines.csv",
        "\n".join(
            [
                "open_time,open,high,low,close,volume,close_time,quote_volume,trade_count,taker_buy_base_volume,taker_buy_quote_volume,ignore",
                "1704067200000,10,11,9,10.5,100,1704067259999,1000,10,40,400,0",
                "1704067260000,10.5,11,10,10.8,110,1704067319999,1100,11,44,440,0",
            ]
        ),
    )


def test_cache_config_rejects_nearest_oi_join_strategy() -> None:
    with pytest.raises(ValueError, match="nearest can leak future OI"):
        CacheConfig(out_dir=Path(".cache"), oi_join_strategy="nearest")  # type: ignore[arg-type]


def test_process_block_never_uses_future_open_interest_sample() -> None:
    future_metrics_zip = _zip_csv(
        "metrics.csv",
        "create_time,symbol,sum_open_interest\n1704067500000,AAAUSDT,999\n",  # 2024-01-01 00:05:00 UTC
    )
    files = DownloadedBlockFiles(
        klines_zip=_klines_zip(),
        metrics_zip=future_metrics_zip,
        liquidations_zip=None,
        missing_required_klines=False,
    )

    frame, next_last_oi = process_block(
        files=files,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        last_oi=None,
        oi_join_strategy="backward",
    )

    assert frame["open_interest"].to_list() == [None, None]
    assert frame["oi_available"].to_list() == [False, False]
    assert frame["missing_oi_flag"].to_list() == [True, True]
    assert next_last_oi is None


def test_process_block_marks_missing_oi_even_when_previous_oi_is_carried_forward() -> None:
    files = DownloadedBlockFiles(
        klines_zip=_klines_zip(),
        metrics_zip=None,
        liquidations_zip=None,
        missing_required_klines=False,
    )

    frame, next_last_oi = process_block(
        files=files,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        last_oi=123.0,
        oi_join_strategy="backward",
    )

    assert frame["open_interest"].to_list() == [123.0, 123.0]
    assert frame["oi_available"].to_list() == [True, True]
    assert frame["missing_oi_flag"].to_list() == [True, True]
    assert frame["liquidation_available"].to_list() == [False, False]
    assert frame["missing_liquidation_flag"].to_list() == [True, True]
    assert next_last_oi == 123.0


def test_process_block_outputs_memory_bounded_schema() -> None:
    metrics_zip = _zip_csv(
        "metrics.csv",
        "create_time,symbol,sum_open_interest\n1704067200000,AAAUSDT,100\n",
    )
    files = DownloadedBlockFiles(
        klines_zip=_klines_zip(),
        metrics_zip=metrics_zip,
        liquidations_zip=None,
        missing_required_klines=False,
    )

    frame, _ = process_block(
        files=files,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        last_oi=None,
        oi_join_strategy="backward",
    )

    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
        "open_interest",
        "long_liquidations_vol",
        "short_liquidations_vol",
    ):
        assert frame.schema[column] == pl.Float32
    for column in ("oi_available", "missing_oi_flag", "liquidation_available", "missing_liquidation_flag"):
        assert frame.schema[column] == pl.Boolean


def test_present_metrics_with_unknown_schema_fail_instead_of_silent_empty_fallback() -> None:
    bad_metrics_zip = _zip_csv("metrics.csv", "bad_time,bad_value\n1,2\n")

    with pytest.raises(ValueError, match="unsupported schema"):
        read_metrics(bad_metrics_zip)
