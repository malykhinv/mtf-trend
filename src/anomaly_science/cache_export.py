from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


@dataclass(frozen=True, slots=True)
class CacheMvp1CsvExportConfig:
    cache_dir: Path
    out_dir: Path
    symbols: tuple[str, ...] = ()
    days: int | None = None


def export_cache_to_mvp1_csv(config: CacheMvp1CsvExportConfig) -> Path:
    if config.days is not None and config.days <= 0:
        raise ValueError("days must be positive when provided")
    config.out_dir.mkdir(parents=True, exist_ok=True)
    symbols = config.symbols or discover_cache_symbols(config.cache_dir)
    if not symbols:
        raise ValueError(f"cache contains no symbol parquet files: {config.cache_dir}")
    frames = [_read_symbol_cache(config.cache_dir, symbol) for symbol in symbols]
    candles_1m = pd.concat(frames, ignore_index=True)
    candles_1m = _filter_days(candles_1m, days=config.days).sort_values(["symbol", "open_time_ms"])
    if candles_1m.empty:
        raise ValueError(f"cache export produced no rows for days={config.days!r} symbols={symbols!r}")
    candles_5m = _build_candles_5m(candles_1m)
    open_interest_5m = _build_open_interest_5m(candles_1m)
    candles_1m_path = config.out_dir / "candles_1m.csv"
    candles_5m_path = config.out_dir / "candles_5m.csv"
    open_interest_5m_path = config.out_dir / "open_interest_5m.csv"
    coverage_path = config.out_dir / "cache_export_coverage.csv"
    manifest_path = config.out_dir / "cache_export_manifest.json"
    candles_1m.to_csv(candles_1m_path, index=False)
    candles_5m.to_csv(candles_5m_path, index=False)
    open_interest_5m.to_csv(open_interest_5m_path, index=False)
    coverage = _build_cache_export_coverage(candles_1m, cache_dir=config.cache_dir)
    coverage.to_csv(coverage_path, index=False)
    _write_cache_export_manifest(
        manifest_path,
        config=config,
        symbols=tuple(symbols),
        candles_1m=candles_1m,
        coverage=coverage,
        artifact_paths=(candles_1m_path, candles_5m_path, open_interest_5m_path, coverage_path),
    )
    return config.out_dir


def discover_cache_symbols(cache_dir: Path) -> tuple[str, ...]:
    if not cache_dir.exists():
        raise FileNotFoundError(f"cache directory is missing: {cache_dir}")
    return tuple(sorted(path.stem.upper() for path in cache_dir.glob("*.parquet") if path.is_file()))


def _read_symbol_cache(cache_dir: Path, symbol: str) -> pd.DataFrame:
    path = cache_dir / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"cache parquet is missing for {symbol}: {path}")
    frame = pd.read_parquet(path)
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
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path} is missing required cache columns: {missing}")
    result = pd.DataFrame(
        {
            "symbol": symbol,
            "open_time_ms": frame["timestamp"].astype("int64"),
            "available_time_ms": frame["timestamp"].astype("int64") + 60_000,
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
    end_day_start_ms = (end_ms // 86_400_000) * 86_400_000
    start_ms = end_day_start_ms - (days - 1) * 86_400_000
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
        first_ms = int(ordered["open_time_ms"].iloc[0])
        last_ms = int(ordered["open_time_ms"].iloc[-1])
        observed_days = {
            datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date()
            for value in ordered["open_time_ms"].tolist()
        }
        first_date = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).date()
        last_date = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).date()
        calendar_days = (last_date - first_date).days + 1
        rows.append(
            {
                "symbol": symbol,
                "rows_1m": int(len(ordered)),
                "first_open_time_ms": first_ms,
                "last_open_time_ms": last_ms,
                "first_date": first_date.isoformat(),
                "last_date": last_date.isoformat(),
                "calendar_days": calendar_days,
                "observed_utc_days": len(observed_days),
                "missing_utc_days": calendar_days - len(observed_days),
                "has_open_interest": bool(ordered["open_interest"].notna().any()),
                "source_path": str(cache_dir / f"{symbol}.parquet"),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "rows_1m",
            "first_open_time_ms",
            "last_open_time_ms",
            "first_date",
            "last_date",
            "calendar_days",
            "observed_utc_days",
            "missing_utc_days",
            "has_open_interest",
            "source_path",
        ],
    )


def _write_cache_export_manifest(
    path: Path,
    *,
    config: CacheMvp1CsvExportConfig,
    symbols: tuple[str, ...],
    candles_1m: pd.DataFrame,
    coverage: pd.DataFrame,
    artifact_paths: tuple[Path, ...],
) -> None:
    timestamps = pd.to_datetime(candles_1m["open_time_ms"].astype("int64"), unit="ms", utc=True)
    payload = {
        "source": "binance_vision_cache",
        "boundary": "mvp1_normalized_csv",
        "cache_dir": str(config.cache_dir),
        "out_dir": str(config.out_dir),
        "requested_symbols": list(config.symbols),
        "exported_symbols": list(symbols),
        "requested_days": config.days,
        "effective_start_date": timestamps.min().date().isoformat(),
        "effective_end_date": timestamps.max().date().isoformat(),
        "effective_calendar_days": (timestamps.max().date() - timestamps.min().date()).days + 1,
        "rows_1m": int(len(candles_1m)),
        "symbols_with_missing_utc_days": coverage.loc[coverage["missing_utc_days"] > 0, "symbol"].tolist(),
        "missing_utc_days_total": int(coverage["missing_utc_days"].sum()),
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
