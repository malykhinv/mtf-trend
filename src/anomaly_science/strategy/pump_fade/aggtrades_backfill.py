"""Pump-fade-owned event-scoped aggTrades acquisition workflow."""

from __future__ import annotations

import io
import json
import os
import threading
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from anomaly_science.binance_vision_cache import (
    BINANCE_VISION_BASE_URL,
    CacheConfig,
    DEFAULT_MARKET_CACHE_ROOT,
    csv_has_header,
    download_optional_bytes,
    first_csv_from_zip,
    replace_metadata_file,
)
from anomaly_science.data.aggtrades_minute import (
    build_minute_aggtrades_features,
)

DEFAULT_EVENTS_SOURCE = Path(".output/results/pump_fade_decisions_canonical_full.parquet")
DEFAULT_AGGTRADES_SIDECAR_DIR = (
    DEFAULT_MARKET_CACHE_ROOT / "binance_vision" / "um_futures" / "aggtrades_1m_event_scoped"
)
_MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class AggTradesBackfillConfig:
    sidecar_dir: Path = DEFAULT_AGGTRADES_SIDECAR_DIR
    events_source: Path = DEFAULT_EVENTS_SOURCE
    symbols: tuple[str, ...] = ()
    max_symbols: int | None = None
    symbol_workers: int = 4
    workers: int = 3
    retries: int = 5
    timeout_seconds: float = 45.0
    connect_timeout_seconds: float = 8.0
    lookback_minutes: int = 240
    compression: str = "zstd"
    refresh: bool = False
    network_pause_seconds: float = 30.0
    network_pause_max_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not 1 <= self.symbol_workers <= 8:
            raise ValueError("symbol_workers must be between 1 and 8")
        if not 1 <= self.workers <= 16:
            raise ValueError("workers must be between 1 and 16")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")
        if self.lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be positive")
        if self.max_symbols is not None and self.max_symbols <= 0:
            raise ValueError("max_symbols must be positive when provided")
        if self.network_pause_seconds <= 0:
            raise ValueError("network_pause_seconds must be positive")
        if self.network_pause_max_seconds < self.network_pause_seconds:
            raise ValueError("network_pause_max_seconds must be >= network_pause_seconds")


@dataclass(frozen=True, slots=True)
class SymbolBackfillStats:
    symbol: str
    requested_days: int
    downloaded_days: int
    missing_days: int
    failed_days: int
    minute_rows: int
    bytes_downloaded: int
    elapsed_seconds: float


def aggtrades_archive_url(*, symbol: str, day: date) -> str:
    label = day.isoformat()
    normalized = symbol.upper()
    return (
        f"{BINANCE_VISION_BASE_URL}/data/futures/um/daily/aggTrades/{normalized}/"
        f"{normalized}-aggTrades-{label}.zip"
    )


def required_symbol_days(events: pd.DataFrame, *, lookback_minutes: int) -> dict[str, set[date]]:
    """Derive the (symbol -> required UTC days) map from an events/decisions frame.

    Uses only `event_id` (which encodes the ignition timestamp) and `snapshot_time_ms`,
    so it stays valid even if the decisions schema gains unrelated columns.
    """

    if events.empty:
        return {}
    grouped = events.groupby("event_id").agg(
        symbol=("symbol", "first"), last_snapshot_ms=("snapshot_time_ms", "max")
    )
    ignition_ms = grouped.index.to_series().str.rsplit(":", n=1).str[-1].astype("int64")
    lookback_ms = lookback_minutes * 60_000
    result: dict[str, set[date]] = {}
    for event_id, row in grouped.iterrows():
        symbol = str(row["symbol"])
        start_ms = int(ignition_ms.loc[event_id]) - lookback_ms
        end_ms = int(row["last_snapshot_ms"])
        days = result.setdefault(symbol, set())
        days.update(_utc_days(first_ms=start_ms, last_ms=end_ms))
    return result


def _utc_days(*, first_ms: int, last_ms: int) -> Iterable[date]:
    current = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).date()
    end = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
    while current <= end:
        yield current
        current += timedelta(days=1)


def read_aggtrades(zip_bytes: bytes) -> pd.DataFrame:
    import polars as pl

    csv_bytes = first_csv_from_zip(zip_bytes)
    has_header = csv_has_header(csv_bytes)
    frame = pl.read_csv(
        io.BytesIO(csv_bytes),
        has_header=has_header,
        new_columns=None
        if has_header
        else ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time", "is_buyer_maker"],
        ignore_errors=True,
    )
    frame = frame.select(
        pl.col("price").cast(pl.Float64, strict=False),
        pl.col("quantity").cast(pl.Float64, strict=False),
        pl.col("transact_time").cast(pl.Int64, strict=False),
        pl.col("is_buyer_maker").cast(pl.Boolean, strict=False),
    ).drop_nulls()
    return frame.to_pandas()


class _AggTradesDownloadPool:
    def __init__(self, config: AggTradesBackfillConfig) -> None:
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="aggtrades-http")
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._lock = threading.Lock()
        self._request_config = CacheConfig(
            out_dir=config.sidecar_dir,
            download_workers=1,
            retries=config.retries,
            timeout_seconds=config.timeout_seconds,
            connect_timeout_seconds=config.connect_timeout_seconds,
        )

    def __enter__(self) -> "_AggTradesDownloadPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
        with self._lock:
            sessions = tuple(self._sessions)
            self._sessions.clear()
        for session in sessions:
            session.close()

    def download_many(self, *, symbol: str, days: Iterable[date]) -> dict[date, bytes | None]:
        futures = {
            self._executor.submit(self._download_one, aggtrades_archive_url(symbol=symbol, day=day)): day
            for day in days
        }
        downloaded: dict[date, bytes | None] = {}
        for future in as_completed(futures):
            downloaded[futures[future]] = future.result()
        return downloaded

    def _download_one(self, url: str) -> bytes | None:
        """Fetch one archive, pausing (not failing) through network outages.

        `download_optional_bytes` already retries `config.retries` times internally
        and returns None cleanly for a genuine HTTP 404 (day/symbol never existed).
        It only raises once those internal retries are exhausted, which in practice
        means the network itself is down rather than one flaky request. Treat that
        as "pause and keep trying" rather than "give up and record a failed day" —
        a permanent failure here would leave a hole that looks identical to a real
        404 in the manifest.
        """

        pause = self._config.network_pause_seconds
        while True:
            try:
                return download_optional_bytes(url, self._request_config, session=self._session())
            except RuntimeError as exc:
                print(
                    f"[network] {_console_safe(url)} unreachable after retries, "
                    f"pausing {pause:.0f}s before trying again: {_console_safe(str(exc))}",
                    flush=True,
                )
                time.sleep(pause)
                pause = min(pause * 2.0, self._config.network_pause_max_seconds)

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is not None:
            return session
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": "anomaly-science-aggtrades-backfill/1.0"})
        self._local.session = session
        with self._lock:
            self._sessions.append(session)
        return session


def backfill_pump_fade_aggtrades(config: AggTradesBackfillConfig) -> tuple[SymbolBackfillStats, ...]:
    events = pd.read_parquet(config.events_source, columns=["event_id", "symbol", "snapshot_time_ms"])
    day_map = required_symbol_days(events, lookback_minutes=config.lookback_minutes)

    requested_symbols = {symbol.upper().strip() for symbol in config.symbols if symbol.strip()}
    symbols = sorted(day_map) if not requested_symbols else sorted(requested_symbols & set(day_map))
    if requested_symbols:
        missing = sorted(requested_symbols - set(day_map))
        if missing:
            raise ValueError(f"no events found for requested symbols: {missing}")
    if config.max_symbols is not None:
        symbols = symbols[: config.max_symbols]

    config.sidecar_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(config.sidecar_dir)
    results: list[SymbolBackfillStats] = []
    pending_by_symbol: dict[str, list[date]] = {}
    for symbol in symbols:
        required_days = sorted(day_map[symbol])
        pending_by_symbol[symbol] = _pending_days(
            symbol=symbol, required_days=required_days, manifest=manifest, refresh=config.refresh
        )
    total_pending_days = sum(len(days) for days in pending_by_symbol.values())
    days_done = 0
    start_time = time.monotonic()
    with ProcessPoolExecutor(max_workers=config.symbol_workers) as executor:
        futures = {
            executor.submit(
                _backfill_one_symbol_worker,
                symbol=symbol,
                required_days=sorted(day_map[symbol]),
                pending_days=pending_by_symbol[symbol],
                config=config,
            ): symbol
            for symbol in symbols
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            symbol, day_status, stats = future.result()
            manifest[symbol] = {**manifest.get(symbol, {}), **day_status}
            results.append(stats)
            days_done += stats.downloaded_days + stats.missing_days + stats.failed_days
            eta = _format_eta(days_done=days_done, total_days=total_pending_days, elapsed_seconds=time.monotonic() - start_time)
            print(
                f"aggTrades backfill [{completed}/{len(symbols)}] {_console_safe(symbol)} - "
                f"{stats.downloaded_days} downloaded, {stats.missing_days} missing, {stats.failed_days} failed "
                f"({days_done}/{total_pending_days} days, ETA {eta})",
                flush=True,
            )
            _write_manifest(config.sidecar_dir, manifest)
    return tuple(results)


def _format_eta(*, days_done: int, total_days: int, elapsed_seconds: float) -> str:
    remaining_days = total_days - days_done
    if days_done <= 0 or remaining_days <= 0:
        return "0s" if remaining_days <= 0 else "unknown"
    rate_days_per_second = days_done / max(elapsed_seconds, 1e-9)
    return _format_duration(remaining_days / rate_days_per_second)


def _format_duration(seconds: float) -> str:
    total_seconds = int(max(seconds, 0.0))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _console_safe(text: str) -> str:
    """Keep progress identity printable regardless of the Windows console codec.

    A handful of meme perpetuals carry CJK characters in their ticker (e.g.
    龙虾USDT). When stdout is redirected to a file, Python encodes with the
    locale codec (cp1252 on Windows), which cannot represent those glyphs, so a
    naive print of the symbol raises UnicodeEncodeError and kills the run. Mirror
    the OI backfill's `_console_safe_symbol` and degrade to an ASCII escape.
    """

    return text.encode("ascii", errors="backslashreplace").decode("ascii")


def _pending_days(*, symbol: str, required_days: list[date], manifest: dict, refresh: bool) -> list[date]:
    if refresh:
        return list(required_days)
    completed = manifest.get(symbol, {})
    return [day for day in required_days if completed.get(day.isoformat()) not in ("completed", "missing_404")]


def _backfill_one_symbol_worker(
    *,
    symbol: str,
    required_days: list[date],
    pending_days: list[date],
    config: AggTradesBackfillConfig,
) -> tuple[str, dict[str, str], SymbolBackfillStats]:
    """Top-level, picklable entry point run inside a ProcessPoolExecutor worker."""

    day_status: dict[str, str] = {}
    with _AggTradesDownloadPool(config) as pool:
        stats = _backfill_symbol(
            symbol=symbol,
            required_days=required_days,
            pending_days=pending_days,
            config=config,
            pool=pool,
            day_status=day_status,
        )
    return symbol, day_status, stats


def _backfill_symbol(
    *,
    symbol: str,
    required_days: list[date],
    pending_days: list[date],
    config: AggTradesBackfillConfig,
    pool: _AggTradesDownloadPool,
    day_status: dict[str, str],
) -> SymbolBackfillStats:
    import pyarrow.parquet as pq

    start_time = time.monotonic()
    downloaded_days = 0
    missing_days = 0
    failed_days = 0
    bytes_downloaded = 0
    minute_frames: list[pd.DataFrame] = []

    if pending_days:
        payloads = pool.download_many(symbol=symbol, days=pending_days)
        for day, payload in sorted(payloads.items()):
            if payload is None:
                missing_days += 1
                day_status[day.isoformat()] = "missing_404"
                continue
            try:
                bytes_downloaded += len(payload)
                trades = read_aggtrades(payload)
                minute_frame = build_minute_aggtrades_features(
                    price=trades["price"].to_numpy(),
                    quantity=trades["quantity"].to_numpy(),
                    transact_time_ms=trades["transact_time"].to_numpy(),
                    is_buyer_maker=trades["is_buyer_maker"].to_numpy(),
                )
                minute_frames.append(minute_frame)
                downloaded_days += 1
                day_status[day.isoformat()] = "completed"
            except (zipfile.BadZipFile, ValueError, KeyError) as exc:
                failed_days += 1
                day_status[day.isoformat()] = f"failed:{exc}"

    output_path = config.sidecar_dir / f"{symbol}.parquet"
    if minute_frames:
        combined = pd.concat(minute_frames, ignore_index=True)
        if output_path.exists() and not config.refresh:
            existing = pd.read_parquet(output_path)
            combined = pd.concat([existing, combined], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
        tmp_path = output_path.with_name(f"{output_path.name}.{os.getpid()}.aggtrades-backfill.tmp")
        try:
            import pyarrow as pa

            table = pa.Table.from_pandas(combined, preserve_index=False)
            pq.write_table(table, tmp_path, compression=config.compression, use_dictionary=True, write_statistics=True)
            replace_metadata_file(tmp_path, output_path, label=f"aggTrades minute sidecar for {symbol}")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
        minute_rows = len(combined)
    else:
        minute_rows = _existing_row_count(output_path)

    return SymbolBackfillStats(
        symbol=symbol,
        requested_days=len(required_days),
        downloaded_days=downloaded_days,
        missing_days=missing_days,
        failed_days=failed_days,
        minute_rows=minute_rows,
        bytes_downloaded=bytes_downloaded,
        elapsed_seconds=time.monotonic() - start_time,
    )


def _existing_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(pd.read_parquet(path, columns=["timestamp"]))


def _manifest_path(sidecar_dir: Path) -> Path:
    return sidecar_dir / "_metadata" / "manifest.json"


def _read_manifest(sidecar_dir: Path) -> dict:
    path = _manifest_path(sidecar_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            return {}
        return payload["symbols"]
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return {}


def _write_manifest(sidecar_dir: Path, symbols: dict) -> None:
    path = _manifest_path(sidecar_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": _MANIFEST_SCHEMA_VERSION, "symbols": symbols}
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    replace_metadata_file(tmp_path, path, label="aggTrades backfill manifest")


__all__ = [
    "AggTradesBackfillConfig",
    "DEFAULT_AGGTRADES_SIDECAR_DIR",
    "DEFAULT_EVENTS_SOURCE",
    "SymbolBackfillStats",
    "aggtrades_archive_url",
    "backfill_pump_fade_aggtrades",
    "read_aggtrades",
    "required_symbol_days",
]
