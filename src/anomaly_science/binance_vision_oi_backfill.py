from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests

from anomaly_science.binance_vision_cache import (
    BINANCE_VISION_BASE_URL,
    DEFAULT_MARKET_CACHE_DIR,
    CacheConfig,
    download_optional_bytes,
    is_delivery_contract_symbol,
    normalize_output_arrow_table,
    read_metrics,
    replace_metadata_file,
)


@dataclass(frozen=True, slots=True)
class OiBackfillConfig:
    cache_dir: Path = DEFAULT_MARKET_CACHE_DIR
    symbols: tuple[str, ...] = ()
    max_symbols: int | None = None
    workers: int = 16
    retries: int = 5
    timeout_seconds: float = 45.0
    connect_timeout_seconds: float = 8.0
    max_staleness_minutes: int = 10
    compression: str = "zstd"
    refresh: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 32:
            raise ValueError("workers must be between 1 and 32")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")
        if self.max_staleness_minutes <= 0:
            raise ValueError("max_staleness_minutes must be positive")
        if self.max_symbols is not None and self.max_symbols <= 0:
            raise ValueError("max_symbols must be positive when provided")


@dataclass(frozen=True, slots=True)
class OiBackfillStats:
    symbol: str
    status: str
    candle_rows: int
    requested_days: int
    archive_days: int
    metric_samples: int
    oi_rows_before: int
    oi_rows_after: int
    first_candle_time: str
    last_candle_time: str
    source_digest_sha256: str
    output_path: str


class _MetricsDownloadPool:
    def __init__(self, config: OiBackfillConfig) -> None:
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="oi-http")
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._lock = threading.Lock()
        self._request_config = CacheConfig(
            out_dir=config.cache_dir,
            download_workers=1,
            retries=config.retries,
            timeout_seconds=config.timeout_seconds,
            connect_timeout_seconds=config.connect_timeout_seconds,
        )

    def __enter__(self) -> "_MetricsDownloadPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
        with self._lock:
            sessions = tuple(self._sessions)
            self._sessions.clear()
        for session in sessions:
            session.close()

    def download_many(self, *, symbol: str, days: Iterable[date]) -> dict[date, bytes]:
        futures = {
            self._executor.submit(self._download_one, metrics_archive_url(symbol=symbol, day=day)): day
            for day in days
        }
        downloaded: dict[date, bytes] = {}
        for future in as_completed(futures):
            payload = future.result()
            if payload is not None:
                downloaded[futures[future]] = payload
        return downloaded

    def _download_one(self, url: str) -> bytes | None:
        return download_optional_bytes(url, self._request_config, session=self._session())

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is not None:
            return session
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": "anomaly-science-oi-backfill/1.0"})
        self._local.session = session
        with self._lock:
            self._sessions.append(session)
        return session


def metrics_archive_url(*, symbol: str, day: date) -> str:
    label = day.isoformat()
    normalized = symbol.upper()
    return (
        f"{BINANCE_VISION_BASE_URL}/data/futures/um/daily/metrics/{normalized}/"
        f"{normalized}-metrics-{label}.zip"
    )


def backfill_binance_vision_open_interest(config: OiBackfillConfig) -> tuple[OiBackfillStats, ...]:
    paths = _resolve_symbol_paths(config)
    results: list[OiBackfillStats] = []
    with _MetricsDownloadPool(config) as pool:
        for index, path in enumerate(paths, start=1):
            symbol = path.stem.upper()
            print(f"OI backfill [{index}/{len(paths)}] {symbol}", flush=True)
            completed = _read_completed_if_current(path=path, config=config)
            if completed is not None:
                results.append(completed)
                continue
            stats = _backfill_symbol(path=path, symbol=symbol, config=config, pool=pool)
            _write_symbol_proof(path=proof_path(config.cache_dir, symbol), stats=stats, parquet_path=path)
            results.append(stats)
    _write_manifest(config=config, stats=results)
    return tuple(results)


def _resolve_symbol_paths(config: OiBackfillConfig) -> tuple[Path, ...]:
    requested = {symbol.upper().strip() for symbol in config.symbols if symbol.strip()}
    paths = tuple(
        path
        for path in sorted(config.cache_dir.glob("*.parquet"), key=lambda item: item.stem.upper())
        if not is_delivery_contract_symbol(path.stem)
        and (not requested or path.stem.upper() in requested)
    )
    if requested:
        found = {path.stem.upper() for path in paths}
        missing = sorted(requested - found)
        if missing:
            raise FileNotFoundError("cache parquet not found for symbols: " + ",".join(missing))
    return paths[: config.max_symbols] if config.max_symbols is not None else paths


def _backfill_symbol(
    *,
    path: Path,
    symbol: str,
    config: OiBackfillConfig,
    pool: _MetricsDownloadPool,
) -> OiBackfillStats:
    import polars as pl
    import pyarrow.parquet as pq

    candles = pl.read_parquet(path).sort("timestamp")
    if candles.height == 0:
        raise ValueError(f"empty cache parquet: {path}")
    timestamps = candles.get_column("timestamp")
    first_ms = int(timestamps[0])
    last_ms = int(timestamps[-1])
    requested_days = tuple(_utc_days(first_ms=first_ms, last_ms=last_ms))
    payloads = pool.download_many(symbol=symbol, days=requested_days)

    digest = hashlib.sha256()
    metric_frames = []
    for day, payload in sorted(payloads.items()):
        digest.update(day.isoformat().encode("ascii"))
        digest.update(hashlib.sha256(payload).digest())
        metric_frames.append(read_metrics(payload))
    metrics = (
        pl.concat(metric_frames, how="vertical_relaxed")
        .unique(subset=["timestamp"], keep="last")
        .sort("timestamp")
        if metric_frames
        else pl.DataFrame(schema={"timestamp": pl.Int64, "open_interest": pl.Float64})
    )
    oi_rows_before = (
        int(candles.get_column("open_interest").is_not_null().sum())
        if "open_interest" in candles.columns
        else 0
    )
    enriched = merge_causal_open_interest(
        candles=candles,
        metrics=metrics,
        max_staleness_minutes=config.max_staleness_minutes,
    )
    oi_rows_after = int(enriched.get_column("open_interest").is_not_null().sum())

    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.oi-backfill.tmp")
    try:
        table = normalize_output_arrow_table(enriched.to_arrow())
        pq.write_table(
            table,
            tmp_path,
            compression=config.compression,
            use_dictionary=True,
            write_statistics=True,
        )
        replace_metadata_file(tmp_path, path, label=f"OI-enriched parquet for {symbol}")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return OiBackfillStats(
        symbol=symbol,
        status="completed" if payloads else "no_metrics_archives",
        candle_rows=candles.height,
        requested_days=len(requested_days),
        archive_days=len(payloads),
        metric_samples=metrics.height,
        oi_rows_before=oi_rows_before,
        oi_rows_after=oi_rows_after,
        first_candle_time=_iso_time(first_ms),
        last_candle_time=_iso_time(last_ms),
        source_digest_sha256=digest.hexdigest(),
        output_path=str(path),
    )


def merge_causal_open_interest(*, candles, metrics, max_staleness_minutes: int):
    import polars as pl

    base = candles.drop([name for name in ("open_interest", "oi_available", "missing_oi_flag") if name in candles.columns])
    if metrics.height == 0:
        return base.with_columns(
            pl.lit(None, dtype=pl.Float32).alias("open_interest"),
            pl.lit(False).alias("oi_available"),
            pl.lit(True).alias("missing_oi_flag"),
        )
    samples = metrics.select(
        pl.col("timestamp").cast(pl.Int64).alias("oi_sample_timestamp"),
        pl.col("open_interest").cast(pl.Float64),
    ).sort("oi_sample_timestamp")
    joined = base.sort("timestamp").join_asof(
        samples,
        left_on="timestamp",
        right_on="oi_sample_timestamp",
        strategy="backward",
    )
    max_age_ms = max_staleness_minutes * 60_000
    candle_day = pl.from_epoch(pl.col("timestamp"), time_unit="ms").dt.date()
    sample_day = pl.from_epoch(pl.col("oi_sample_timestamp"), time_unit="ms").dt.date()
    valid = (
        pl.col("oi_sample_timestamp").is_not_null()
        & (pl.col("timestamp") >= pl.col("oi_sample_timestamp"))
        & ((pl.col("timestamp") - pl.col("oi_sample_timestamp")) <= max_age_ms)
        & (candle_day == sample_day)
    )
    return (
        joined.with_columns(
            pl.when(valid).then(pl.col("open_interest")).otherwise(None).cast(pl.Float32).alias("open_interest")
        )
        .with_columns(
            pl.col("open_interest").is_not_null().alias("oi_available"),
            pl.col("open_interest").is_null().alias("missing_oi_flag"),
        )
        .drop("oi_sample_timestamp")
    )


def _utc_days(*, first_ms: int, last_ms: int) -> Iterable[date]:
    current = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).date()
    end = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
    while current <= end:
        yield current
        current += timedelta(days=1)


def proof_path(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / "_metadata" / "oi_backfill" / f"{symbol.upper()}.json"


def _write_symbol_proof(*, path: Path, stats: OiBackfillStats, parquet_path: Path) -> None:
    stat = parquet_path.stat()
    payload = {
        "schema_version": 1,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "parquet_size": stat.st_size,
        "parquet_mtime_ns": stat.st_mtime_ns,
        "stats": asdict(stats),
    }
    _atomic_json(path, payload, label=f"OI proof for {stats.symbol}")


def _read_completed_if_current(*, path: Path, config: OiBackfillConfig) -> OiBackfillStats | None:
    if config.refresh:
        return None
    metadata_path = proof_path(config.cache_dir, path.stem)
    if not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        stat = path.stat()
        if payload.get("schema_version") != 1:
            return None
        if payload.get("parquet_size") != stat.st_size or payload.get("parquet_mtime_ns") != stat.st_mtime_ns:
            return None
        return OiBackfillStats(**payload["stats"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_manifest(*, config: OiBackfillConfig, stats: list[OiBackfillStats]) -> None:
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "Binance Vision USD-M daily metrics",
        "join_contract": (
            "latest sample with oi_sample_timestamp <= candle timestamp; "
            "same UTC day; bounded staleness"
        ),
        "max_staleness_minutes": config.max_staleness_minutes,
        "liquidation_snapshot_status": "unavailable_in_binance_vision_usd_m_for_requested_period",
        "symbols": len(stats),
        "archive_days": sum(item.archive_days for item in stats),
        "metric_samples": sum(item.metric_samples for item in stats),
        "oi_rows_after": sum(item.oi_rows_after for item in stats),
        "results": [asdict(item) for item in stats],
    }
    _atomic_json(
        config.cache_dir / "_metadata" / "oi_backfill_manifest.json",
        payload,
        label="OI backfill manifest",
    )


def _atomic_json(path: Path, payload: dict[str, object], *, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    replace_metadata_file(tmp_path, path, label=label)


def _iso_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
