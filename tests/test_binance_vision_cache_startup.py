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
    VisionBlock,
    build_run_klines_archive_index,
    filter_symbols_by_archive_range,
    parse_monthly_kline_archive_key,
)


def test_compact_command_uses_optimized_network_defaults() -> None:
    config = CacheConfig(out_dir=Path(".cache"))

    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 45.0
    assert config.connect_timeout_seconds == DEFAULT_CONNECT_TIMEOUT_SECONDS == 8.0
    assert config.retries == DEFAULT_RETRIES == 2
    assert config.use_archive_file_index


def test_archive_file_index_flag_is_backward_compatible_no_op() -> None:
    args = cache.build_arg_parser().parse_args(["--archive-file-index"])
    config = CacheConfig(
        out_dir=Path(".cache"),
        use_archive_file_index=not bool(args.no_archive_file_index),
    )

    assert config.use_archive_file_index


def test_direct_archive_probing_is_explicit_opt_out() -> None:
    args = cache.build_arg_parser().parse_args(["--no-archive-file-index"])
    config = CacheConfig(
        out_dir=Path(".cache"),
        use_archive_file_index=not bool(args.no_archive_file_index),
    )

    assert not config.use_archive_file_index


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


def test_run_level_klines_index_is_scoped_to_requested_symbols_and_months(monkeypatch, tmp_path: Path) -> None:
    prefixes: list[str] = []

    def fake_list_s3_object_keys(*, prefix: str, config: CacheConfig) -> list[str]:
        prefixes.append(prefix)
        if "AAAUSDT" in prefix:
            return [
                "data/futures/um/monthly/klines/AAAUSDT/1m/AAAUSDT-1m-2026-06.zip",
                "data/futures/um/monthly/klines/AAAUSDT/1m/AAAUSDT-1m-2022-01.zip",
            ]
        if "BBBUSDT" in prefix:
            return ["data/futures/um/monthly/klines/BBBUSDT/1m/BBBUSDT-1m-2025-12.zip"]
        return ["data/futures/um/monthly/klines/BAD/3m/BAD-3m-2026-06.zip"]

    monkeypatch.setattr(cache, "list_s3_object_keys", fake_list_s3_object_keys)

    index = build_run_klines_archive_index(
        config=CacheConfig(out_dir=tmp_path),
        symbols=("AAAUSDT", "BBBUSDT", "BAD"),
        monthly_labels=("2026-06",),
    )

    assert sorted(prefixes) == [
        "data/futures/um/monthly/klines/AAAUSDT/1m/",
        "data/futures/um/monthly/klines/BAD/1m/",
        "data/futures/um/monthly/klines/BBBUSDT/1m/",
    ]
    assert index.monthly_labels("AAAUSDT") == frozenset({"2026-06"})
    assert index.monthly_labels("BBBUSDT") == frozenset()
    assert index.monthly_labels("BAD") == frozenset()


def test_monthly_kline_archive_key_parser_requires_matching_symbol() -> None:
    assert parse_monthly_kline_archive_key(
        "data/futures/um/monthly/klines/AAAUSDT/1m/AAAUSDT-1m-2026-06.zip"
    ) == ("AAAUSDT", "2026-06")
    assert parse_monthly_kline_archive_key(
        "data/futures/um/monthly/klines/AAAUSDT/1m/BBBUSDT-1m-2026-06.zip"
    ) is None


def test_scoped_klines_index_can_keep_daily_only_current_month_symbol(monkeypatch, tmp_path: Path) -> None:
    def fake_list_s3_object_keys(*, prefix: str, config: CacheConfig) -> list[str]:
        return []

    def fake_archive_url_exists(url: str, *, config: CacheConfig) -> bool:
        return url.endswith("NEWUSDT-1m-2026-06-16.zip")

    monkeypatch.setattr(cache, "list_s3_object_keys", fake_list_s3_object_keys)
    monkeypatch.setattr(cache, "archive_url_exists", fake_archive_url_exists)

    index = build_run_klines_archive_index(
        config=CacheConfig(out_dir=tmp_path),
        symbols=("NEWUSDT",),
        monthly_labels=("2026-05",),
        daily_labels=("2026-06-15", "2026-06-16"),
    )

    symbol_index = cache.archive_index_from_run_klines_index(symbol="NEWUSDT", run_index=index)

    assert index.monthly_labels("NEWUSDT") == frozenset()
    assert index.daily_labels("NEWUSDT") == frozenset({"2026-06-16"})
    assert symbol_index.has(
        block=VisionBlock(period="daily", label="2026-06-16", start_date=date(2026, 6, 16), end_date=date(2026, 6, 16)),
        dataset="klines",
    )
    assert not symbol_index.has(
        block=VisionBlock(period="daily", label="2026-06-15", start_date=date(2026, 6, 15), end_date=date(2026, 6, 15)),
        dataset="klines",
    )


def test_project_cli_enables_archive_file_index_by_default() -> None:
    from anomaly_science import cli

    args = cli.build_parser().parse_args(["build-binance-vision-cache"])
    config = CacheConfig(
        out_dir=Path(".cache"),
        use_archive_file_index=not bool(args.no_archive_file_index),
    )

    assert config.use_archive_file_index


def test_project_cli_can_disable_archive_file_index_for_debugging() -> None:
    from anomaly_science import cli

    args = cli.build_parser().parse_args(["build-binance-vision-cache", "--no-archive-file-index"])
    config = CacheConfig(
        out_dir=Path(".cache"),
        use_archive_file_index=not bool(args.no_archive_file_index),
    )

    assert not config.use_archive_file_index

def test_cached_scoped_klines_index_can_be_reused_without_preflight(tmp_path: Path) -> None:
    from anomaly_science.binance_vision_cache import (
        load_cached_run_klines_archive_index_if_scope_matches,
        run_klines_archive_index_path,
        write_run_klines_archive_index,
    )

    config = CacheConfig(out_dir=tmp_path / "enriched_1m")
    index = RunKlinesArchiveIndex(monthly_labels_by_symbol={"AAAUSDT": frozenset({"2026-05"})})
    write_run_klines_archive_index(
        run_klines_archive_index_path(config.out_dir),
        index,
        symbols=("AAAUSDT",),
        monthly_labels=("2026-05",),
        daily_labels=("2026-06-16",),
    )

    cached = load_cached_run_klines_archive_index_if_scope_matches(
        config=config,
        symbols=("AAAUSDT",),
        blocks=[
            VisionBlock(period="monthly", label="2026-05", start_date=date(2026, 5, 1), end_date=date(2026, 5, 31)),
            VisionBlock(period="daily", label="2026-06-16", start_date=date(2026, 6, 16), end_date=date(2026, 6, 16)),
        ],
    )

    assert cached is not None
    assert cached.monthly_labels("AAAUSDT") == frozenset({"2026-05"})


def test_missing_monthly_daily_fallback_is_explicit_opt_in() -> None:
    from anomaly_science import cli

    default_args = cli.build_parser().parse_args(["build-binance-vision-cache"])
    opt_in_args = cli.build_parser().parse_args(["build-binance-vision-cache", "--daily-fallback-for-missing-monthly"])

    assert not default_args.daily_fallback_for_missing_monthly
    assert opt_in_args.daily_fallback_for_missing_monthly
