from __future__ import annotations

from dataclasses import dataclass
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
    candles_1m.to_csv(config.out_dir / "candles_1m.csv", index=False)
    _build_candles_5m(candles_1m).to_csv(config.out_dir / "candles_5m.csv", index=False)
    _build_open_interest_5m(candles_1m).to_csv(config.out_dir / "open_interest_5m.csv", index=False)
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
