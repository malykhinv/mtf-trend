from __future__ import annotations

from datetime import date
from pathlib import Path

from anomaly_science import binance_vision_cache as cache
from anomaly_science.binance_vision_cache import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    CacheConfig,
    RunKlinesArchiveIndex,
    build_run_klines_archive_index,
    filter_symbols_by_archive_range,
    parse_monthly_kline_archive_key,
)


def test_compact_command_uses_optimized_network_defaults() -> None:
    config = CacheConfig(out_dir=Path(".cache"))

    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 45.0
    assert config.connect_timeout_seconds == DEFAULT_CONNECT_TIMEOUT_SECONDS == 8.0
    assert config.retries == DEFAULT_RETRIES == 2


def test_archive_range_filter_uses_run_level_monthly_klines_index(monkeypatch, tmp_path: Path) -> None:
    def fail_symbol_index(*, symbol: str, config: CacheConfig):  # pragma: no cover - should never run
        raise AssertionError(f"unexpected per-symbol archive index load: {symbol}")

    monkeypatch.setattr(cache, "load_or_build_archive_file_index", fail_symbol_index)

    symbols, loaded_indexes, skipped = filter_symbols_by_archive_range(
        symbols=["OLDUSDT", "AAAUSDT"],
        config=CacheConfig(out_dir=tmp_path),
        start_date=date(2025, 6, 2),
        end_date=date(2026, 6, 16),
        run_klines_index=RunKlinesArchiveIndex(
            monthly_labels_by_symbol={
                "OLDUSDT": frozenset({"2022-12"}),
                "AAAUSDT": frozenset({"2026-06"}),
            }
        ),
    )

    assert symbols == ["AAAUSDT"]
    assert sorted(loaded_indexes) == ["AAAUSDT"]
    assert loaded_indexes["AAAUSDT"].has(
        block=cache.VisionBlock(period="monthly", label="2026-06", start_date=date(2026, 6, 1), end_date=date(2026, 6, 30)),
        dataset="klines",
    )
    assert [(item.symbol, item.reason) for item in skipped] == [
        ("OLDUSDT", "no_klines_archive_in_requested_range")
    ]


def test_run_level_monthly_klines_index_is_built_from_one_root_listing(monkeypatch, tmp_path: Path) -> None:
    prefixes: list[str] = []

    def fake_list_s3_object_keys(*, prefix: str, config: CacheConfig) -> list[str]:
        prefixes.append(prefix)
        return [
            "data/futures/um/monthly/klines/AAAUSDT/1m/AAAUSDT-1m-2026-06.zip",
            "data/futures/um/monthly/klines/BBBUSDT/1m/BBBUSDT-1m-2025-12.zip",
            "data/futures/um/monthly/klines/BAD/3m/BAD-3m-2026-06.zip",
        ]

    monkeypatch.setattr(cache, "list_s3_object_keys", fake_list_s3_object_keys)

    index = build_run_klines_archive_index(config=CacheConfig(out_dir=tmp_path))

    assert prefixes == ["data/futures/um/monthly/klines/"]
    assert index.monthly_labels("AAAUSDT") == frozenset({"2026-06"})
    assert index.monthly_labels("BBBUSDT") == frozenset({"2025-12"})
    assert index.monthly_labels("BAD") == frozenset()


def test_monthly_kline_archive_key_parser_requires_matching_symbol() -> None:
    assert parse_monthly_kline_archive_key(
        "data/futures/um/monthly/klines/AAAUSDT/1m/AAAUSDT-1m-2026-06.zip"
    ) == ("AAAUSDT", "2026-06")
    assert parse_monthly_kline_archive_key(
        "data/futures/um/monthly/klines/AAAUSDT/1m/BBBUSDT-1m-2026-06.zip"
    ) is None
