from __future__ import annotations

import argparse
import csv
import json
import io
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal
from xml.etree import ElementTree

import requests

BINANCE_VISION_BASE_URL = "https://data.binance.vision"
S3_LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DEFAULT_MARKET_CACHE_ROOT = Path(".output/market")
DEFAULT_MARKET_CACHE_DIR = DEFAULT_MARKET_CACHE_ROOT / "binance_vision" / "um_futures" / "enriched_1m"
ONE_MINUTE_MS = 60_000
ONE_DAY_MS = 86_400_000

OUTPUT_COLUMNS = [
    "timestamp",
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
]

KLINE_RAW_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]

METRICS_FALLBACK_COLUMNS = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]

LIQUIDATION_FALLBACK_COLUMNS = [
    "time",
    "symbol",
    "side",
    "price",
    "orig_qty",
    "avg_price",
    "status",
    "last_filled_qty",
    "accumulated_filled_qty",
    "trade_time",
]

TIME_COLUMN_CANDIDATES = (
    "timestamp",
    "time",
    "open_time",
    "create_time",
    "createTime",
    "trade_time",
    "tradeTime",
    "T",
    "E",
)

OI_COLUMN_CANDIDATES = (
    "sum_open_interest",
    "sumOpenInterest",
    "open_interest",
    "openInterest",
    "openInterestAmount",
)

LIQ_SIDE_CANDIDATES = ("side", "S")
LIQ_QTY_CANDIDATES = (
    "accumulated_filled_qty",
    "accumulatedFilledQty",
    "orig_qty",
    "origQty",
    "quantity",
    "qty",
    "executedQty",
    "last_filled_qty",
    "lastFilledQty",
    "z",
    "q",
    "l",
)



ProgressCallback = Callable[[str, str, int, int, int], None]

@dataclass(frozen=True, slots=True)
class CacheConfig:
    out_dir: Path
    days: int = 380
    end_date: date | None = None
    symbols: tuple[str, ...] = ()
    max_symbols: int | None = None
    download_workers: int = 3
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0
    retries: int = 3
    overwrite: bool = False
    compression: str = "zstd"
    oi_join_strategy: Literal["backward", "nearest"] = "backward"
    request_sleep_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.days <= 0:
            raise ValueError("days must be positive")
        if not 1 <= self.download_workers <= 3:
            raise ValueError("download_workers must be between 1 and 3 to keep RAM bounded")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")
        if self.max_symbols is not None and self.max_symbols <= 0:
            raise ValueError("max_symbols must be positive when provided")
        if self.oi_join_strategy not in {"backward", "nearest"}:
            raise ValueError("oi_join_strategy must be 'backward' or 'nearest'")


@dataclass(frozen=True, slots=True)
class VisionBlock:
    period: Literal["monthly", "daily"]
    label: str
    start_date: date
    end_date: date

    @property
    def is_monthly(self) -> bool:
        return self.period == "monthly"


@dataclass(frozen=True, slots=True)
class DownloadedBlockFiles:
    klines_zip: bytes | None
    metrics_zip: bytes | None
    liquidations_zip: bytes | None
    missing_required_klines: bool


@dataclass(frozen=True, slots=True)
class SymbolStats:
    symbol: str
    rows_written: int
    blocks_written: int
    missing_kline_blocks: int
    missing_metric_blocks: int
    missing_liquidation_blocks: int
    output_path: Path | None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build-binance-vision-cache",
        description=(
            "Build per-symbol 1m Parquet cache from Binance Vision USD-M Futures klines, "
            "metrics/open-interest, and liquidationSnapshot archives."
        ),
    )
    parser.add_argument("--days", type=int, default=380, help="Inclusive lookback window in calendar days. Default: 380.")
    parser.add_argument(
        "--end-date",
        default="",
        help="Inclusive UTC end date YYYY-MM-DD. Default: yesterday UTC, because daily archives lag by one day.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated symbols. If empty, discover every archived USD-M Futures symbol from Binance Vision.",
    )
    parser.add_argument(
        "--symbols-file",
        default="",
        help="Optional text file with one symbol per line. Combined with --symbols when provided.",
    )
    parser.add_argument("--max-symbols", type=int, default=None, help="Optional cap for smoke tests.")
    parser.add_argument(
        "--download-workers",
        type=int,
        default=3,
        help="Concurrent downloads inside one symbol/block. Hard-capped to 1..3 for RAM safety.",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-request read timeout in seconds.")
    parser.add_argument("--connect-timeout", type=float, default=10.0, help="Per-request connect timeout in seconds.")
    parser.add_argument("--retries", type=int, default=3, help="Retries per file download.")
    parser.add_argument("--overwrite", action="store_true", help="Rebuild symbols even if {symbol}.parquet already exists.")
    parser.add_argument(
        "--oi-join-strategy",
        choices=("backward", "nearest"),
        default="backward",
        help=(
            "How to align sparse metrics/OI to 1m candles. Default backward is research-safe; "
            "nearest follows the literal nearest-sample rule but can leak future OI."
        ),
    )
    parser.add_argument(
        "--request-sleep",
        type=float,
        default=0.0,
        help="Optional sleep after each block to be gentle to the public archive.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = CacheConfig(
        out_dir=DEFAULT_MARKET_CACHE_DIR,
        days=args.days,
        end_date=parse_optional_date(args.end_date),
        symbols=tuple(read_symbols_arg(args.symbols, args.symbols_file)),
        max_symbols=args.max_symbols,
        download_workers=args.download_workers,
        timeout_seconds=args.timeout,
        connect_timeout_seconds=args.connect_timeout,
        retries=args.retries,
        overwrite=args.overwrite,
        oi_join_strategy=args.oi_join_strategy,
        request_sleep_seconds=args.request_sleep,
    )
    build_binance_vision_cache(cfg)
    return 0


def build_binance_vision_cache(config: CacheConfig) -> list[SymbolStats]:
    # Heavy imports are local so the rest of anomaly_science stays usable without cache-building deps installed.
    try:
        from tqdm import tqdm
    except ImportError as exc:  # pragma: no cover - exercised only in missing optional dependency envs.
        raise RuntimeError("tqdm is required: pip install tqdm") from exc

    end = config.end_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start = end - timedelta(days=config.days - 1)
    blocks = plan_blocks(start, end)

    symbols = list(config.symbols) if config.symbols else discover_um_futures_symbols(config)
    symbols = sorted(dict.fromkeys(symbol.upper().strip() for symbol in symbols if symbol.strip()))
    if config.max_symbols is not None:
        symbols = symbols[: config.max_symbols]
    if not symbols:
        raise RuntimeError("No symbols to process. Discovery returned empty set and no --symbols were provided.")

    config.out_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir = cache_metadata_dir(config.out_dir)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    stats: list[SymbolStats] = []

    started_at = time.monotonic()
    completed_block_units = 0
    estimated_block_units = max(1, len(symbols) * max(1, len(blocks)))

    progress = tqdm(symbols, desc="Binance Vision cache", unit="symbol", mininterval=1.0)
    for symbol_index, symbol in enumerate(progress, start=1):

        def on_block_progress(
            progress_symbol: str,
            block_label: str,
            symbol_blocks_done: int,
            symbol_blocks_total: int,
            rows_written: int,
        ) -> None:
            nonlocal completed_block_units, estimated_block_units
            completed_block_units += 1
            estimated_block_units = max(
                estimated_block_units,
                (symbol_index - 1) * max(1, len(blocks)) + symbol_blocks_total,
                completed_block_units,
            )
            progress.set_postfix(
                symbol=progress_symbol,
                block=f"{symbol_blocks_done}/{symbol_blocks_total}",
                current=block_label,
                blocks=f"{completed_block_units}/{estimated_block_units}",
                rows=rows_written,
                disk=human_bytes(directory_size(DEFAULT_MARKET_CACHE_ROOT)),
                eta=format_eta(started_at, completed_block_units, estimated_block_units),
                refresh=True,
            )

        progress.set_postfix(
            symbol=symbol,
            block=f"0/{len(blocks)}",
            disk=human_bytes(directory_size(DEFAULT_MARKET_CACHE_ROOT)),
            eta=format_eta(started_at, completed_block_units, estimated_block_units),
            refresh=True,
        )
        symbol_stats = build_symbol_cache(
            symbol=symbol,
            blocks=blocks,
            config=config,
            start_date=start,
            end_date=end,
            progress_callback=on_block_progress,
        )
        stats.append(symbol_stats)
        write_stats(metadata_dir / "cache_stats.csv", stats)
        write_manifest(metadata_dir / "manifest.json", config=config, stats=stats, start_date=start, end_date=end)
    return stats


def build_symbol_cache(
    *,
    symbol: str,
    blocks: list[VisionBlock],
    config: CacheConfig,
    start_date: date,
    end_date: date,
    progress_callback: ProgressCallback | None = None,
) -> SymbolStats:
    import pyarrow.parquet as pq

    final_path = config.out_dir / f"{symbol}.parquet"
    tmp_path = config.out_dir / f".{symbol}.parquet.tmp"
    if final_path.exists() and not config.overwrite:
        return SymbolStats(
            symbol=symbol,
            rows_written=-1,
            blocks_written=0,
            missing_kline_blocks=0,
            missing_metric_blocks=0,
            missing_liquidation_blocks=0,
            output_path=final_path,
        )
    if tmp_path.exists():
        tmp_path.unlink()

    writer: pq.ParquetWriter | None = None
    rows_written = 0
    blocks_written = 0
    missing_kline_blocks = 0
    missing_metric_blocks = 0
    missing_liquidation_blocks = 0
    last_oi: float | None = None
    symbol_blocks_done = 0
    symbol_blocks_total = max(1, len(blocks))

    def notify_block(label: str) -> None:
        nonlocal symbol_blocks_done, symbol_blocks_total
        symbol_blocks_done += 1
        symbol_blocks_total = max(symbol_blocks_total, symbol_blocks_done)
        if progress_callback is not None:
            progress_callback(symbol, label, symbol_blocks_done, symbol_blocks_total, rows_written)

    try:
        for block in blocks:
            files = download_block_files(symbol=symbol, block=block, config=config)
            if files.missing_required_klines and block.is_monthly:
                notify_block(f"{block.period}:{block.label}:missing-monthly")
                fallback_days = daily_blocks(block.start_date, min(block.end_date, end_date))
                symbol_blocks_total += len(fallback_days)
                for daily_block in fallback_days:
                    daily_files = download_block_files(symbol=symbol, block=daily_block, config=config)
                    if daily_files.missing_required_klines:
                        missing_kline_blocks += 1
                        notify_block(f"{daily_block.period}:{daily_block.label}:missing")
                        continue
                    frame, last_oi = process_block(
                        files=daily_files,
                        start_date=max(daily_block.start_date, start_date),
                        end_date=min(daily_block.end_date, end_date),
                        last_oi=last_oi,
                        oi_join_strategy=config.oi_join_strategy,
                    )
                    if daily_files.metrics_zip is None:
                        missing_metric_blocks += 1
                    if daily_files.liquidations_zip is None:
                        missing_liquidation_blocks += 1
                    writer = append_parquet_frame(writer=writer, path=tmp_path, frame=frame, compression=config.compression)
                    rows_written += frame.height
                    blocks_written += int(frame.height > 0)
                    notify_block(f"{daily_block.period}:{daily_block.label}")
                    maybe_sleep(config.request_sleep_seconds)
                continue

            if files.missing_required_klines:
                missing_kline_blocks += 1
                notify_block(f"{block.period}:{block.label}:missing")
                maybe_sleep(config.request_sleep_seconds)
                continue

            frame, last_oi = process_block(
                files=files,
                start_date=max(block.start_date, start_date),
                end_date=min(block.end_date, end_date),
                last_oi=last_oi,
                oi_join_strategy=config.oi_join_strategy,
            )
            if files.metrics_zip is None:
                missing_metric_blocks += 1
            if files.liquidations_zip is None:
                missing_liquidation_blocks += 1
            writer = append_parquet_frame(writer=writer, path=tmp_path, frame=frame, compression=config.compression)
            rows_written += frame.height
            blocks_written += int(frame.height > 0)
            notify_block(f"{block.period}:{block.label}")
            maybe_sleep(config.request_sleep_seconds)
    finally:
        if writer is not None:
            writer.close()

    if rows_written <= 0:
        if tmp_path.exists():
            tmp_path.unlink()
        return SymbolStats(
            symbol=symbol,
            rows_written=0,
            blocks_written=0,
            missing_kline_blocks=missing_kline_blocks,
            missing_metric_blocks=missing_metric_blocks,
            missing_liquidation_blocks=missing_liquidation_blocks,
            output_path=None,
        )

    os.replace(tmp_path, final_path)
    return SymbolStats(
        symbol=symbol,
        rows_written=rows_written,
        blocks_written=blocks_written,
        missing_kline_blocks=missing_kline_blocks,
        missing_metric_blocks=missing_metric_blocks,
        missing_liquidation_blocks=missing_liquidation_blocks,
        output_path=final_path,
    )


def append_parquet_frame(*, writer, path: Path, frame, compression: str):
    import pyarrow.parquet as pq

    if frame.height == 0:
        return writer
    table = frame.to_arrow()
    if writer is None:
        writer = pq.ParquetWriter(
            where=path,
            schema=table.schema,
            compression=compression,
            use_dictionary=True,
            write_statistics=True,
        )
    writer.write_table(table)
    return writer


def process_block(
    *,
    files: DownloadedBlockFiles,
    start_date: date,
    end_date: date,
    last_oi: float | None,
    oi_join_strategy: Literal["backward", "nearest"],
):
    import polars as pl

    if files.klines_zip is None:
        raise ValueError("klines zip is required")

    start_ms = utc_date_ms(start_date)
    end_exclusive_ms = utc_date_ms(end_date + timedelta(days=1))

    candles = read_klines(files.klines_zip).filter(
        (pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_exclusive_ms)
    )
    if candles.height == 0:
        return empty_output_frame(), last_oi

    metrics = read_metrics(files.metrics_zip) if files.metrics_zip is not None else empty_oi_frame()
    liquidations = read_liquidations(files.liquidations_zip) if files.liquidations_zip is not None else empty_liquidation_frame()

    if metrics.height > 0:
        metrics = metrics.filter(pl.col("timestamp") < end_exclusive_ms).sort("timestamp")
    candles = candles.sort("timestamp")

    if metrics.height > 0:
        joined = candles.join_asof(metrics, on="timestamp", strategy=oi_join_strategy)
    else:
        joined = candles.with_columns(pl.lit(None, dtype=pl.Float64).alias("open_interest"))

    fill_oi = 0.0 if last_oi is None else float(last_oi)
    joined = joined.with_columns(pl.col("open_interest").fill_null(strategy="forward").fill_null(fill_oi))

    if liquidations.height > 0:
        joined = joined.join(liquidations, on="timestamp", how="left")
    else:
        joined = joined.with_columns(
            pl.lit(0.0, dtype=pl.Float64).alias("long_liquidations_vol"),
            pl.lit(0.0, dtype=pl.Float64).alias("short_liquidations_vol"),
        )

    frame = (
        joined.with_columns(
            pl.col("long_liquidations_vol").fill_null(0.0),
            pl.col("short_liquidations_vol").fill_null(0.0),
        )
        .select(OUTPUT_COLUMNS)
        .with_columns(
            pl.col("timestamp").cast(pl.Int64),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
            pl.col("taker_buy_base_volume").cast(pl.Float64),
            pl.col("taker_buy_quote_volume").cast(pl.Float64),
            pl.col("open_interest").cast(pl.Float64),
            pl.col("long_liquidations_vol").cast(pl.Float64),
            pl.col("short_liquidations_vol").cast(pl.Float64),
        )
    )
    next_last_oi = float(frame.select(pl.col("open_interest").last()).item()) if frame.height > 0 else last_oi
    return frame, next_last_oi


def read_klines(zip_bytes: bytes):
    import polars as pl

    csv_bytes = first_csv_from_zip(zip_bytes)
    has_header = csv_has_header(csv_bytes)
    frame = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=has_header,
        new_columns=None if has_header else KLINE_RAW_COLUMNS,
        ignore_errors=True,
    )
    rename_map = {name: KLINE_RAW_COLUMNS[index] for index, name in enumerate(frame.columns[: len(KLINE_RAW_COLUMNS)])}
    if has_header:
        rename_map = {column: normalize_column_name(column) for column in frame.columns}
    frame = frame.rename(rename_map)
    if "open_time" not in frame.columns:
        raise ValueError("kline CSV has no open_time column")
    taker_buy_base_column = first_existing_column(
        frame.columns,
        (
            "taker_buy_base_volume",
            "taker_buy_volume",
            "taker_buy_base_asset_volume",
        ),
    )
    taker_buy_quote_column = first_existing_column(
        frame.columns,
        (
            "taker_buy_quote_volume",
            "taker_buy_quote_asset_volume",
        ),
    )
    if taker_buy_base_column is None:
        raise ValueError(f"kline CSV has no taker buy base volume column: {frame.columns}")
    if taker_buy_quote_column is None:
        raise ValueError(f"kline CSV has no taker buy quote volume column: {frame.columns}")
    selected = frame.select(
        normalize_timestamp_expr("open_time").alias("timestamp"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
        pl.col(taker_buy_base_column).cast(pl.Float64).alias("taker_buy_base_volume"),
        pl.col(taker_buy_quote_column).cast(pl.Float64).alias("taker_buy_quote_volume"),
    )
    return selected.unique(subset=["timestamp"], keep="last").sort("timestamp")


def read_metrics(zip_bytes: bytes | None):
    import polars as pl

    if zip_bytes is None:
        return empty_oi_frame()
    csv_bytes = first_csv_from_zip(zip_bytes)
    has_header = csv_has_header(csv_bytes)
    frame = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=has_header,
        new_columns=None if has_header else METRICS_FALLBACK_COLUMNS,
        ignore_errors=True,
    )
    frame = frame.rename({column: normalize_column_name(column) for column in frame.columns})
    time_column = first_existing_column(frame.columns, TIME_COLUMN_CANDIDATES)
    oi_column = first_existing_column(frame.columns, OI_COLUMN_CANDIDATES)
    if time_column is None or oi_column is None:
        return empty_oi_frame()
    return (
        frame.select(
            normalize_timestamp_expr(time_column).alias("timestamp"),
            pl.col(oi_column).cast(pl.Float64).alias("open_interest"),
        )
        .drop_nulls(subset=["timestamp"])
        .unique(subset=["timestamp"], keep="last")
        .sort("timestamp")
    )


def read_liquidations(zip_bytes: bytes | None):
    import polars as pl

    if zip_bytes is None:
        return empty_liquidation_frame()
    csv_bytes = first_csv_from_zip(zip_bytes)
    if len(csv_bytes.strip()) == 0:
        return empty_liquidation_frame()
    has_header = csv_has_header(csv_bytes)
    frame = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=has_header,
        new_columns=None if has_header else LIQUIDATION_FALLBACK_COLUMNS,
        ignore_errors=True,
    )
    if frame.height == 0:
        return empty_liquidation_frame()
    frame = frame.rename({column: normalize_column_name(column) for column in frame.columns})
    time_column = first_existing_column(frame.columns, TIME_COLUMN_CANDIDATES)
    side_column = first_existing_column(frame.columns, LIQ_SIDE_CANDIDATES)
    qty_column = first_existing_column(frame.columns, LIQ_QTY_CANDIDATES)
    if time_column is None or side_column is None or qty_column is None:
        return empty_liquidation_frame()

    normalized = frame.select(
        ((normalize_timestamp_expr(time_column) // ONE_MINUTE_MS) * ONE_MINUTE_MS).alias("timestamp"),
        pl.col(side_column).cast(pl.Utf8).str.to_uppercase().alias("side"),
        pl.col(qty_column).cast(pl.Float64).fill_null(0.0).alias("qty"),
    ).drop_nulls(subset=["timestamp", "side"])

    if normalized.height == 0:
        return empty_liquidation_frame()

    return (
        normalized.with_columns(
            pl.when(pl.col("side") == "SELL").then(pl.col("qty")).otherwise(0.0).alias("long_liquidations_vol"),
            pl.when(pl.col("side") == "BUY").then(pl.col("qty")).otherwise(0.0).alias("short_liquidations_vol"),
        )
        .group_by("timestamp")
        .agg(
            pl.col("long_liquidations_vol").sum(),
            pl.col("short_liquidations_vol").sum(),
        )
        .sort("timestamp")
    )


def empty_output_frame():
    import polars as pl

    return pl.DataFrame(
        schema={
            "timestamp": pl.Int64,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "taker_buy_base_volume": pl.Float64,
            "taker_buy_quote_volume": pl.Float64,
            "open_interest": pl.Float64,
            "long_liquidations_vol": pl.Float64,
            "short_liquidations_vol": pl.Float64,
        }
    )


def empty_oi_frame():
    import polars as pl

    return pl.DataFrame(schema={"timestamp": pl.Int64, "open_interest": pl.Float64})


def empty_liquidation_frame():
    import polars as pl

    return pl.DataFrame(
        schema={
            "timestamp": pl.Int64,
            "long_liquidations_vol": pl.Float64,
            "short_liquidations_vol": pl.Float64,
        }
    )


def normalize_timestamp_expr(column: str):
    import polars as pl

    expr = pl.col(column)
    # Numeric milliseconds are the normal futures format. Microsecond timestamps are normalized defensively.
    numeric = expr.cast(pl.Int64, strict=False)
    parsed = expr.cast(pl.Utf8).str.strptime(pl.Datetime(time_unit="ms", time_zone="UTC"), strict=False).dt.timestamp("ms")
    timestamp = pl.coalesce([numeric, parsed])
    return pl.when(timestamp > 10_000_000_000_000).then(timestamp // 1000).otherwise(timestamp).cast(pl.Int64)


def first_csv_from_zip(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            raise ValueError("zip archive contains no CSV-like file")
        with archive.open(names[0]) as csv_file:
            return csv_file.read()


def csv_has_header(csv_bytes: bytes) -> bool:
    sample = csv_bytes[:4096].decode("utf-8-sig", errors="ignore")
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    first_cell = first_line.split(",", 1)[0].strip().strip('"')
    return not bool(re.fullmatch(r"-?\d+(\.\d+)?", first_cell))


def normalize_column_name(name: str) -> str:
    clean = name.strip().strip('"').strip("'")
    replacements = {
        "Open time": "open_time",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Close time": "close_time",
        "Quote asset volume": "quote_volume",
        "Number of trades": "trade_count",
        "Taker buy base asset volume": "taker_buy_base_volume",
        "Taker buy quote asset volume": "taker_buy_quote_volume",
        "Ignore": "ignore",
    }
    if clean in replacements:
        return replacements[clean]
    snake = re.sub(r"[^0-9a-zA-Z]+", "_", clean).strip("_")
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake).lower()
    camel_aliases = {
        "sum_open_interest": "sum_open_interest",
        "sum_open_interest_value": "sum_open_interest_value",
        "open_interest": "open_interest",
        "create_time": "create_time",
        "trade_time": "trade_time",
        "orig_qty": "orig_qty",
        "last_filled_qty": "last_filled_qty",
        "accumulated_filled_qty": "accumulated_filled_qty",
    }
    return camel_aliases.get(snake, snake)


def first_existing_column(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    column_set = set(columns)
    normalized_candidates = [normalize_column_name(candidate) for candidate in candidates]
    for candidate in list(candidates) + normalized_candidates:
        if candidate in column_set:
            return candidate
    return None


def download_block_files(*, symbol: str, block: VisionBlock, config: CacheConfig) -> DownloadedBlockFiles:
    urls = {
        "klines_zip": make_archive_url(symbol=symbol, block=block, dataset="klines"),
        "metrics_zip": make_archive_url(symbol=symbol, block=block, dataset="metrics"),
        "liquidations_zip": make_archive_url(symbol=symbol, block=block, dataset="liquidationSnapshot"),
    }
    results: dict[str, bytes | None] = {name: None for name in urls}
    with ThreadPoolExecutor(max_workers=config.download_workers) as executor:
        future_to_name = {
            executor.submit(download_optional_bytes, url, config): name for name, url in urls.items()
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            results[name] = future.result()

    return DownloadedBlockFiles(
        klines_zip=results["klines_zip"],
        metrics_zip=results["metrics_zip"],
        liquidations_zip=results["liquidations_zip"],
        missing_required_klines=results["klines_zip"] is None,
    )


def download_optional_bytes(url: str, config: CacheConfig) -> bytes | None:
    timeout = (config.connect_timeout_seconds, config.timeout_seconds)
    last_error: Exception | None = None
    for attempt in range(config.retries + 1):
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= config.retries:
                raise RuntimeError(f"failed to download {url}: {exc}") from exc
            time.sleep(min(2.0 * (attempt + 1), 10.0))
    if last_error is not None:
        raise RuntimeError(f"failed to download {url}: {last_error}") from last_error
    return None


def make_archive_url(*, symbol: str, block: VisionBlock, dataset: Literal["klines", "metrics", "liquidationSnapshot"]) -> str:
    if dataset == "klines":
        if block.period == "monthly":
            path = f"data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{block.label}.zip"
        else:
            path = f"data/futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{block.label}.zip"
    elif dataset == "metrics":
        path = f"data/futures/um/{block.period}/metrics/{symbol}/{symbol}-metrics-{block.label}.zip"
    else:
        path = f"data/futures/um/{block.period}/liquidationSnapshot/{symbol}/{symbol}-liquidationSnapshot-{block.label}.zip"
    return f"{BINANCE_VISION_BASE_URL}/{path}"


def discover_um_futures_symbols(config: CacheConfig) -> list[str]:
    prefixes = set()
    for root_prefix in ("data/futures/um/monthly/klines/", "data/futures/um/daily/klines/"):
        prefixes.update(list_s3_common_prefixes(root_prefix, config))
    symbols = []
    for prefix in prefixes:
        trimmed = prefix.rstrip("/")
        symbol = trimmed.rsplit("/", 1)[-1]
        if symbol and symbol != "klines":
            symbols.append(symbol)
    return sorted(set(symbols))


def list_s3_common_prefixes(prefix: str, config: CacheConfig) -> list[str]:
    common_prefixes: list[str] = []
    continuation_token: str | None = None
    timeout = (config.connect_timeout_seconds, config.timeout_seconds)

    try:
        while True:
            params: dict[str, str] = {"delimiter": "/", "prefix": prefix, "max-keys": "1000"}
            if continuation_token:
                params["continuation-token"] = continuation_token
            response = requests.get(S3_LIST_URL, params=params, timeout=timeout)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            namespace_match = re.match(r"\{.*\}", root.tag)
            ns = namespace_match.group(0) if namespace_match else ""
            common_prefixes.extend(
                element.text or "" for element in root.findall(f".//{ns}CommonPrefixes/{ns}Prefix")
            )
            is_truncated = (root.findtext(f"{ns}IsTruncated") or "false").lower() == "true"
            continuation_token = root.findtext(f"{ns}NextContinuationToken")
            if not is_truncated or not continuation_token:
                break
    except (requests.RequestException, ElementTree.ParseError):
        common_prefixes = list_common_prefixes_from_index_page(prefix=prefix, config=config)
    return [item for item in common_prefixes if item]


def list_common_prefixes_from_index_page(*, prefix: str, config: CacheConfig) -> list[str]:
    timeout = (config.connect_timeout_seconds, config.timeout_seconds)
    response = requests.get(BINANCE_VISION_BASE_URL, params={"prefix": prefix}, timeout=timeout)
    response.raise_for_status()
    text = response.text
    escaped_prefix = re.escape(prefix)
    href_matches = re.findall(rf"{escaped_prefix}([^/'\"]+)/", text)
    plain_matches = re.findall(r"(?:^|[>\s])([A-Z0-9_]+/)", text)
    prefixes = {f"{prefix}{match}/" for match in href_matches}
    prefixes.update(f"{prefix}{match}" for match in plain_matches if match != "../")
    return sorted(prefixes)


def plan_blocks(start: date, end: date) -> list[VisionBlock]:
    if end < start:
        raise ValueError("end date must be >= start date")
    blocks: list[VisionBlock] = []
    cursor = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)
    while cursor < end_month:
        month_end = month_last_day(cursor)
        blocks.append(VisionBlock(period="monthly", label=cursor.strftime("%Y-%m"), start_date=cursor, end_date=month_end))
        cursor = add_month(cursor)
    for day_block in daily_blocks(max(start, end_month), end):
        blocks.append(day_block)
    return blocks


def daily_blocks(start: date, end: date) -> list[VisionBlock]:
    if end < start:
        return []
    blocks: list[VisionBlock] = []
    cursor = start
    while cursor <= end:
        blocks.append(VisionBlock(period="daily", label=cursor.isoformat(), start_date=cursor, end_date=cursor))
        cursor += timedelta(days=1)
    return blocks


def add_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def month_last_day(value: date) -> date:
    return add_month(date(value.year, value.month, 1)) - timedelta(days=1)


def utc_date_ms(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1000)


def parse_optional_date(value: str) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def read_symbols_arg(symbols_arg: str, symbols_file: str) -> list[str]:
    symbols: list[str] = []
    if symbols_arg:
        symbols.extend(item.strip() for item in symbols_arg.split(",") if item.strip())
    if symbols_file:
        path = Path(symbols_file)
        symbols.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return symbols


def cache_metadata_dir(out_dir: Path) -> Path:
    return out_dir.parent / "metadata"


def write_stats(path: Path, stats: list[SymbolStats]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp_path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(
            file_obj,
            fieldnames=[
                "symbol",
                "rows_written",
                "blocks_written",
                "missing_kline_blocks",
                "missing_metric_blocks",
                "missing_liquidation_blocks",
                "output_path",
            ],
        )
        writer.writeheader()
        for item in stats:
            writer.writerow(
                {
                    "symbol": item.symbol,
                    "rows_written": item.rows_written,
                    "blocks_written": item.blocks_written,
                    "missing_kline_blocks": item.missing_kline_blocks,
                    "missing_metric_blocks": item.missing_metric_blocks,
                    "missing_liquidation_blocks": item.missing_liquidation_blocks,
                    "output_path": str(item.output_path) if item.output_path is not None else "",
                }
            )
    os.replace(tmp_path, path)


def write_manifest(
    path: Path,
    *,
    config: CacheConfig,
    stats: list[SymbolStats],
    start_date: date,
    end_date: date,
) -> None:
    payload = {
        "source": "binance_vision",
        "market": "um_futures",
        "dataset": "enriched_1m",
        "data_dir": str(config.out_dir),
        "metadata_dir": str(path.parent),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": config.days,
        "symbols_requested": list(config.symbols),
        "symbols_seen": [item.symbol for item in stats],
        "symbols_written": [item.symbol for item in stats if item.rows_written > 0],
        "symbols_skipped_existing": [item.symbol for item in stats if item.rows_written == -1],
        "rows_written": sum(max(0, item.rows_written) for item in stats),
        "blocks_written": sum(item.blocks_written for item in stats),
        "compression": config.compression,
        "oi_join_strategy": config.oi_join_strategy,
        "output_columns": OUTPUT_COLUMNS,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _, files in os.walk(path):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    return total


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def format_eta(started_at: float, completed_units: int, total_units: int) -> str:
    if completed_units <= 0:
        return "calculating"
    remaining_units = max(0, total_units - completed_units)
    elapsed = max(0.0, time.monotonic() - started_at)
    seconds_per_unit = elapsed / completed_units
    return format_duration(remaining_units * seconds_per_unit)


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def maybe_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
