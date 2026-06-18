from __future__ import annotations

from datetime import date
from pathlib import Path

from anomaly_science import binance_vision_cache as cache
from anomaly_science.binance_vision_cache import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    ArchiveFileIndex,
    CacheConfig,
    filter_symbols_by_archive_range,
)


def test_compact_command_uses_optimized_network_defaults() -> None:
    config = CacheConfig(out_dir=Path(".cache"))

    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 45.0
    assert config.connect_timeout_seconds == DEFAULT_CONNECT_TIMEOUT_SECONDS == 8.0
    assert config.retries == DEFAULT_RETRIES == 2


def test_archive_range_filter_reports_progress(monkeypatch, tmp_path: Path) -> None:
    indexes = {
        "AAAUSDT": ArchiveFileIndex(
            symbol="AAAUSDT",
            labels={
                ("monthly", "klines"): frozenset({"2026-06"}),
                ("monthly", "metrics"): frozenset(),
                ("monthly", "liquidationSnapshot"): frozenset(),
                ("daily", "klines"): frozenset(),
                ("daily", "metrics"): frozenset(),
                ("daily", "liquidationSnapshot"): frozenset(),
            },
        )
    }
    progress_calls: list[list[str]] = []

    def fake_load_or_build_archive_file_index(*, symbol: str, config: CacheConfig) -> ArchiveFileIndex:
        return indexes[symbol]

    def fake_progress(items: list[str]):
        progress_calls.append(list(items))
        return iter(items)

    monkeypatch.setattr(cache, "load_or_build_archive_file_index", fake_load_or_build_archive_file_index)

    symbols, loaded_indexes, skipped = filter_symbols_by_archive_range(
        symbols=["AAAUSDT"],
        config=CacheConfig(out_dir=tmp_path),
        start_date=date(2025, 6, 2),
        end_date=date(2026, 6, 16),
        progress_factory=fake_progress,
    )

    assert symbols == ["AAAUSDT"]
    assert sorted(loaded_indexes) == ["AAAUSDT"]
    assert skipped == []
    assert progress_calls == [["AAAUSDT"]]
