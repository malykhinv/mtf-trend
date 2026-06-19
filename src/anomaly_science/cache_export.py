from __future__ import annotations

import gc
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from anomaly_science.binance_vision_cache import is_delivery_contract_symbol

ONE_MINUTE_MS = 60_000
ONE_DAY_MS = 86_400_000
FULL_UTC_DAY_1M_ROWS = 1_440


@dataclass(frozen=True, slots=True)
class CacheMvp1CsvExportConfig:
    cache_dir: Path
    out_dir: Path
    symbols: tuple[str, ...] = ()
    days: int | None = None
    expected_days: int | None = None
    fail_on_missing_utc_days: bool = False
    fail_on_missing_1m_rows: bool = False
    fail_on_missing_open_interest: bool = False
    include_delivery_contracts: bool = False
    progress_every: int = 25
    parquet_use_threads: bool = False

    def __post_init__(self) -> None:
        if self.days is not None and self.days <= 0:
            raise ValueError("days must be positive when provided")
        if self.expected_days is not None and self.expected_days <= 0:
            raise ValueError("expected_days must be positive when provided")
        if self.progress_every < 0:
            raise ValueError("progress_every must be non-negative")


def export_cache_to_mvp1_csv(config: CacheMvp1CsvExportConfig) -> Path:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    symbols, excluded_delivery_symbols = _resolve_export_symbols(config)
    if not symbols:
        raise ValueError(f"cache contains no symbol parquet files: {config.cache_dir}")

    candles_1m_path = config.out_dir / "candles_1m.csv"
    candles_5m_path = config.out_dir / "candles_5m.csv"
    open_interest_5m_path = config.out_dir / "open_interest_5m.csv"
    coverage_path = config.out_dir / "cache_export_coverage.csv"
    manifest_path = config.out_dir / "cache_export_manifest.json"
    _remove_stale_export_artifacts(
        candles_1m_path,
        candles_5m_path,
        open_interest_5m_path,
        coverage_path,
        manifest_path,
    )

    start_ms = _resolve_export_start_ms(config=config, symbols=symbols)
    coverage_frames: list[pd.DataFrame] = []
    exported_symbols: list[str] = []
    total_rows = 0

    for symbol_index, symbol in enumerate(symbols, start=1):
        candles_1m = _read_symbol_cache(
            config.cache_dir,
            symbol,
            parquet_use_threads=config.parquet_use_threads,
        )
        candles_1m = _filter_from_start_ms(candles_1m, start_ms=start_ms).sort_values(["symbol", "open_time_ms"])
        if candles_1m.empty:
            _print_cache_export_progress(
                config=config,
                symbol_index=symbol_index,
                symbol_count=len(symbols),
                symbol=symbol,
                rows_written=0,
                skipped=True,
            )
            continue

        candles_5m = _build_candles_5m(candles_1m)
        open_interest_5m = _build_open_interest_5m(candles_1m)
        coverage_frames.append(_build_cache_export_coverage(candles_1m, cache_dir=config.cache_dir))
        exported_symbols.append(symbol)
        total_rows += int(len(candles_1m))
        _append_csv(candles_1m_path, candles_1m)
        _append_csv(candles_5m_path, candles_5m)
        _append_csv(open_interest_5m_path, open_interest_5m)
        _print_cache_export_progress(
            config=config,
            symbol_index=symbol_index,
            symbol_count=len(symbols),
            symbol=symbol,
            rows_written=int(len(candles_1m)),
            skipped=False,
        )
        del candles_1m, candles_5m, open_interest_5m
        gc.collect()

    if not exported_symbols or not coverage_frames:
        raise ValueError(f"cache export produced no rows for days={config.days!r} symbols={symbols!r}")

    coverage = pd.concat(coverage_frames, ignore_index=True)
    validation = _build_cache_export_validation(config=config, coverage=coverage)
    coverage.to_csv(coverage_path, index=False)
    _write_cache_export_manifest(
        manifest_path,
        config=config,
        symbols=tuple(exported_symbols),
        row_count_1m=total_rows,
        coverage=coverage,
        validation=validation,
        artifact_paths=(candles_1m_path, candles_5m_path, open_interest_5m_path, coverage_path),
        excluded_delivery_symbols=excluded_delivery_symbols,
    )
    _raise_for_validation_failures(validation)
    return config.out_dir


def _remove_stale_export_artifacts(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _append_csv(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, mode="a", header=not path.exists(), index=False)


def _read_parquet_schema_columns(path: Path) -> tuple[str, ...]:
    return tuple(pq.ParquetFile(path).schema_arrow.names)


def _resolve_export_start_ms(*, config: CacheMvp1CsvExportConfig, symbols: tuple[str, ...]) -> int | None:
    if config.days is None:
        return None
    max_open_time_ms: int | None = None
    for symbol in symbols:
        path = config.cache_dir / f"{symbol}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"cache parquet is missing for {symbol}: {path}")
        schema_columns = set(_read_parquet_schema_columns(path))
        if "timestamp" not in schema_columns:
            raise ValueError(f"{path} is missing required cache columns: ['timestamp']")
        timestamps = pd.read_parquet(path, columns=["timestamp"], use_threads=config.parquet_use_threads)
        if timestamps.empty:
            continue
        symbol_max = int(timestamps["timestamp"].astype("int64").max())
        max_open_time_ms = symbol_max if max_open_time_ms is None else max(max_open_time_ms, symbol_max)
        del timestamps
    if max_open_time_ms is None:
        return None
    end_day_start_ms = (max_open_time_ms // ONE_DAY_MS) * ONE_DAY_MS
    return end_day_start_ms - (config.days - 1) * ONE_DAY_MS


def _print_cache_export_progress(
    *,
    config: CacheMvp1CsvExportConfig,
    symbol_index: int,
    symbol_count: int,
    symbol: str,
    rows_written: int,
    skipped: bool,
) -> None:
    if config.progress_every == 0:
        return
    if symbol_index != 1 and symbol_index != symbol_count and symbol_index % config.progress_every != 0:
        return
    status = "skipped" if skipped else f"rows={rows_written}"
    print(
        f"cache export {symbol_index}/{symbol_count} symbol={symbol} {status}",
        file=sys.stderr,
        flush=True,
    )


def discover_cache_symbols(cache_dir: Path, *, include_delivery_contracts: bool = False) -> tuple[str, ...]:
    symbols, _excluded_delivery_symbols = _discover_cache_symbols_with_exclusions(
        cache_dir,
        include_delivery_contracts=include_delivery_contracts,
    )
    return symbols


def _resolve_export_symbols(config: CacheMvp1CsvExportConfig) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if config.symbols:
        delivery_symbols = tuple(symbol for symbol in config.symbols if is_delivery_contract_symbol(symbol))
        if delivery_symbols and not config.include_delivery_contracts:
            raise ValueError(
                "explicit delivery contract symbols are excluded by default: "
                + ",".join(delivery_symbols)
                + "; pass --include-delivery-contracts only for an explicit delivery-contract experiment"
            )
        return config.symbols, ()
    return _discover_cache_symbols_with_exclusions(
        config.cache_dir,
        include_delivery_contracts=config.include_delivery_contracts,
    )


def _discover_cache_symbols_with_exclusions(
    cache_dir: Path,
    *,
    include_delivery_contracts: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not cache_dir.exists():
        raise FileNotFoundError(f"cache directory is missing: {cache_dir}")
    all_symbols = tuple(sorted(path.stem.upper() for path in cache_dir.glob("*.parquet") if path.is_file()))
    if include_delivery_contracts:
        return all_symbols, ()
    symbols = tuple(symbol for symbol in all_symbols if not is_delivery_contract_symbol(symbol))
    excluded_delivery_symbols = tuple(symbol for symbol in all_symbols if is_delivery_contract_symbol(symbol))
    return symbols, excluded_delivery_symbols


def _read_symbol_cache(cache_dir: Path, symbol: str, *, parquet_use_threads: bool = False) -> pd.DataFrame:
    path = cache_dir / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"cache parquet is missing for {symbol}: {path}")
    required = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_quote_volume",
    }
    schema_columns = set(_read_parquet_schema_columns(path))
    missing = sorted(required - schema_columns)
    if missing:
        raise ValueError(f"{path} is missing required cache columns: {missing}")
    selected_columns = sorted(required)
    if "open_interest" in schema_columns:
        selected_columns.append("open_interest")
    frame = pd.read_parquet(path, columns=selected_columns, use_threads=parquet_use_threads)
    result = pd.DataFrame(
        {
            "symbol": symbol,
            "open_time_ms": frame["timestamp"].astype("int64"),
            "available_time_ms": frame["timestamp"].astype("int64") + ONE_MINUTE_MS,
            "open": frame["open"],
            "high": frame["high"],
            "low": frame["low"],
            "close": frame["close"],
            "volume": frame["volume"],
            "quote_volume": frame["quote_volume"],
            "number_of_trades": frame["trade_count"],
            "taker_buy_quote_volume": frame["taker_buy_quote_volume"],
            "open_interest": frame["open_interest"] if "open_interest" in frame.columns else pd.NA,
        }
    )
    return result


def _filter_days(frame: pd.DataFrame, *, days: int | None) -> pd.DataFrame:
    if days is None or frame.empty:
        return frame
    end_ms = int(frame["open_time_ms"].max())
    end_day_start_ms = (end_ms // ONE_DAY_MS) * ONE_DAY_MS
    start_ms = end_day_start_ms - (days - 1) * ONE_DAY_MS
    return frame[frame["open_time_ms"] >= start_ms].copy()


def _filter_from_start_ms(frame: pd.DataFrame, *, start_ms: int | None) -> pd.DataFrame:
    if start_ms is None or frame.empty:
        return frame
    return frame[frame["open_time_ms"] >= start_ms].copy()


def _build_candles_5m(candles_1m: pd.DataFrame) -> pd.DataFrame:
    frame = candles_1m.copy()
    frame["bucket"] = (frame["open_time_ms"] // 300_000) * 300_000
    grouped = frame.groupby(["symbol", "bucket"], sort=True)
    return grouped.agg(
        open_time_ms=("bucket", "first"),
        available_time_ms=("bucket", lambda values: int(values.iloc[0]) + 300_000),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        number_of_trades=("number_of_trades", "sum"),
        taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
    ).reset_index()[[
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
    ]]


def _build_open_interest_5m(candles_1m: pd.DataFrame) -> pd.DataFrame:
    frame = candles_1m.dropna(subset=["open_interest"]).copy()
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "timestamp_ms", "available_time_ms", "open_interest", "source"])
    frame["bucket"] = (frame["open_time_ms"] // 300_000) * 300_000
    grouped = frame.groupby(["symbol", "bucket"], sort=True)
    output = grouped.agg(open_interest=("open_interest", "last")).reset_index()
    output["timestamp_ms"] = output["bucket"].astype("int64")
    output["available_time_ms"] = output["timestamp_ms"] + 300_000
    output["source"] = "binance_vision_cache"
    return output[["symbol", "timestamp_ms", "available_time_ms", "open_interest", "source"]]


def _build_cache_export_coverage(candles_1m: pd.DataFrame, *, cache_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for symbol, frame in candles_1m.groupby("symbol", sort=True):
        ordered = frame.sort_values("open_time_ms")
        timestamps = ordered["open_time_ms"].astype("int64")
        unique_timestamps = timestamps.drop_duplicates().sort_values()
        first_ms = int(unique_timestamps.iloc[0])
        last_ms = int(unique_timestamps.iloc[-1])
        expected_rows_1m_by_span = ((last_ms - first_ms) // ONE_MINUTE_MS) + 1
        duplicate_rows_1m = int(len(timestamps) - len(unique_timestamps))
        missing_rows_1m_by_span = int(expected_rows_1m_by_span - len(unique_timestamps))
        unique_diffs = unique_timestamps.diff().dropna().astype("int64")
        non_1m_step_count = int((unique_diffs != ONE_MINUTE_MS).sum())
        max_gap_minutes = 0
        first_gap_after_open_time_ms: int | None = None
        if not unique_diffs.empty:
            max_step_ms = int(unique_diffs.max())
            max_gap_minutes = max(0, (max_step_ms // ONE_MINUTE_MS) - 1)
            gap_positions = unique_diffs[unique_diffs != ONE_MINUTE_MS]
            if not gap_positions.empty:
                first_gap_index = int(gap_positions.index[0])
                first_gap_after_open_time_ms = int(unique_timestamps.loc[first_gap_index - 1])
        open_dates = pd.to_datetime(timestamps, unit="ms", utc=True).dt.date
        observed_days = set(open_dates.tolist())
        day_row_counts = open_dates.value_counts()
        partial_utc_days = int((day_row_counts != FULL_UTC_DAY_1M_ROWS).sum())
        first_date = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).date()
        last_date = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
        calendar_days = (last_date - first_date).days + 1
        rows.append(
            {
                "symbol": symbol,
                "rows_1m": int(len(ordered)),
                "unique_rows_1m": int(len(unique_timestamps)),
                "expected_rows_1m_by_span": int(expected_rows_1m_by_span),
                "missing_rows_1m_by_span": missing_rows_1m_by_span,
                "duplicate_rows_1m": duplicate_rows_1m,
                "non_1m_step_count": non_1m_step_count,
                "max_gap_minutes": max_gap_minutes,
                "first_gap_after_open_time_ms": first_gap_after_open_time_ms,
                "first_open_time_ms": first_ms,
                "last_open_time_ms": last_ms,
                "first_date": first_date.isoformat(),
                "last_date": last_date.isoformat(),
                "calendar_days": calendar_days,
                "observed_utc_days": len(observed_days),
                "missing_utc_days": calendar_days - len(observed_days),
                "partial_utc_days": partial_utc_days,
                "has_open_interest": bool(ordered["open_interest"].notna().any()),
                "has_complete_1m_span": bool(
                    missing_rows_1m_by_span == 0 and duplicate_rows_1m == 0 and non_1m_step_count == 0
                ),
                "source_path": str(cache_dir / f"{symbol}.parquet"),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "rows_1m",
            "unique_rows_1m",
            "expected_rows_1m_by_span",
            "missing_rows_1m_by_span",
            "duplicate_rows_1m",
            "non_1m_step_count",
            "max_gap_minutes",
            "first_gap_after_open_time_ms",
            "first_open_time_ms",
            "last_open_time_ms",
            "first_date",
            "last_date",
            "calendar_days",
            "observed_utc_days",
            "missing_utc_days",
            "partial_utc_days",
            "has_open_interest",
            "has_complete_1m_span",
            "source_path",
        ],
    )


def _build_cache_export_validation(
    *,
    config: CacheMvp1CsvExportConfig,
    coverage: pd.DataFrame,
) -> dict[str, Any]:
    first_ms = int(coverage["first_open_time_ms"].min())
    last_ms = int(coverage["last_open_time_ms"].max())
    first_date = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).date()
    last_date = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
    effective_calendar_days = (last_date - first_date).days + 1
    duplicate_1m_rows_total = int(coverage["duplicate_rows_1m"].sum())
    missing_rows_1m_total = int(coverage["missing_rows_1m_by_span"].sum())
    missing_utc_days_total = int(coverage["missing_utc_days"].sum())
    symbols_without_open_interest = coverage.loc[~coverage["has_open_interest"].astype(bool), "symbol"].tolist()
    failures: list[str] = []
    if config.expected_days is not None and effective_calendar_days < config.expected_days:
        failures.append(
            "effective_calendar_days_below_expected: "
            f"expected>={config.expected_days} actual={effective_calendar_days}"
        )
    if duplicate_1m_rows_total > 0:
        failures.append(f"duplicate_1m_rows_present: total={duplicate_1m_rows_total}")
    if config.fail_on_missing_utc_days and missing_utc_days_total > 0:
        failures.append(f"missing_utc_days_present: total={missing_utc_days_total}")
    if config.fail_on_missing_1m_rows and missing_rows_1m_total > 0:
        failures.append(f"missing_1m_rows_present: total={missing_rows_1m_total}")
    if config.fail_on_missing_open_interest and symbols_without_open_interest:
        failures.append(
            "symbols_without_open_interest: " + ",".join(str(symbol) for symbol in symbols_without_open_interest)
        )
    return {
        "validation_passed": not failures,
        "validation_failures": failures,
        "expected_days": config.expected_days,
        "effective_calendar_days": effective_calendar_days,
        "exported_symbol_count": int(coverage["symbol"].nunique()),
        "duplicate_1m_rows_total": duplicate_1m_rows_total,
        "missing_1m_rows_total": missing_rows_1m_total,
        "symbols_with_missing_1m_rows": coverage.loc[
            coverage["missing_rows_1m_by_span"] > 0, "symbol"
        ].tolist(),
        "missing_utc_days_total": missing_utc_days_total,
        "symbols_with_missing_utc_days": coverage.loc[coverage["missing_utc_days"] > 0, "symbol"].tolist(),
        "partial_utc_days_total": int(coverage["partial_utc_days"].sum()),
        "symbols_without_open_interest": symbols_without_open_interest,
    }


def _raise_for_validation_failures(validation: dict[str, Any]) -> None:
    failures = validation.get("validation_failures", [])
    if failures:
        raise ValueError("cache export validation failed: " + "; ".join(str(item) for item in failures))


def _write_cache_export_manifest(
    path: Path,
    *,
    config: CacheMvp1CsvExportConfig,
    symbols: tuple[str, ...],
    row_count_1m: int,
    coverage: pd.DataFrame,
    validation: dict[str, Any],
    artifact_paths: tuple[Path, ...],
    excluded_delivery_symbols: tuple[str, ...],
) -> None:
    first_ms = int(coverage["first_open_time_ms"].min())
    last_ms = int(coverage["last_open_time_ms"].max())
    first_date = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).date()
    last_date = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
    payload = {
        "source": "binance_vision_cache",
        "boundary": "mvp1_normalized_csv",
        "cache_dir": str(config.cache_dir),
        "out_dir": str(config.out_dir),
        "requested_symbols": list(config.symbols),
        "exported_symbols": list(symbols),
        "excluded_delivery_contract_symbols": list(excluded_delivery_symbols),
        "include_delivery_contracts": config.include_delivery_contracts,
        "requested_days": config.days,
        "expected_days": config.expected_days,
        "fail_on_missing_utc_days": config.fail_on_missing_utc_days,
        "fail_on_missing_1m_rows": config.fail_on_missing_1m_rows,
        "fail_on_missing_open_interest": config.fail_on_missing_open_interest,
        "effective_start_date": first_date.isoformat(),
        "effective_end_date": last_date.isoformat(),
        "effective_calendar_days": (last_date - first_date).days + 1,
        "rows_1m": int(row_count_1m),
        "symbols_with_missing_utc_days": coverage.loc[coverage["missing_utc_days"] > 0, "symbol"].tolist(),
        "missing_utc_days_total": int(coverage["missing_utc_days"].sum()),
        "validation": validation,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": [
            {
                "name": artifact_path.name,
                "path": str(artifact_path.relative_to(config.out_dir)),
                "sha256": _sha256_file(artifact_path),
                "size_bytes": artifact_path.stat().st_size,
            }
            for artifact_path in artifact_paths
        ],
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
