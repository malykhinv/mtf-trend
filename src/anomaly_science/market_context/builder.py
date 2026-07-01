from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.market_context.config import ReferenceMarketContextConfig, ReferenceMarketSpec


class ReferenceMarketContextError(ValueError):
    """Raised when causal reference-market features cannot be constructed."""


def build_reference_market_features(
    market: pd.DataFrame,
    *,
    reference: ReferenceMarketSpec,
    config: ReferenceMarketContextConfig,
) -> pd.DataFrame:
    required = {
        "timestamp", "high", "low", "close", "quote_volume",
        "taker_buy_quote_volume", "open_interest", "oi_available",
    }
    missing = sorted(required - set(market.columns))
    if missing:
        raise ReferenceMarketContextError(f"reference market columns missing: {missing}")
    work = market.loc[:, sorted(required)].copy()
    work = work.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    if work["timestamp"].duplicated().any() or not work["timestamp"].is_monotonic_increasing:
        raise ReferenceMarketContextError("reference timestamps must be unique and increasing")
    timestamp = pd.to_numeric(work["timestamp"], errors="raise").to_numpy(dtype=np.int64)
    close = pd.to_numeric(work["close"], errors="raise").to_numpy(dtype=float)
    high = pd.to_numeric(work["high"], errors="raise").to_numpy(dtype=float)
    low = pd.to_numeric(work["low"], errors="raise").to_numpy(dtype=float)
    quote = pd.to_numeric(work["quote_volume"], errors="raise").to_numpy(dtype=float)
    taker = pd.to_numeric(work["taker_buy_quote_volume"], errors="raise").to_numpy(dtype=float)
    oi = pd.to_numeric(work["open_interest"], errors="coerce").to_numpy(dtype=float)
    oi_available = _explicit_bool(work["oi_available"], "oi_available").to_numpy(dtype=bool)
    if not (
        np.isfinite(close).all() and np.isfinite(high).all() and np.isfinite(low).all()
        and np.isfinite(quote).all() and np.isfinite(taker).all()
    ):
        raise ReferenceMarketContextError("reference OHLC/activity contains non-finite values")
    if np.any(close <= 0.0) or np.any(high < low) or np.any(quote < 0.0) or np.any(taker < 0.0):
        raise ReferenceMarketContextError("reference market values violate price/activity constraints")
    prefix = reference.alias
    result = pd.DataFrame(
        {
            "snapshot_time_ms": timestamp + config.interval_ms,
            f"has_{prefix}_context": True,
        }
    )
    for lag in config.return_lags_minutes:
        result[f"{prefix}_return_{lag}m"] = _causal_fractional_lag(
            close, timestamp, lag, config.interval_ms
        )
    for lag in config.oi_lags_minutes:
        values = _causal_fractional_lag(oi, timestamp, lag, config.interval_ms)
        valid = oi_available & _lagged_bool(oi_available, timestamp, lag, config.interval_ms)
        result[f"{prefix}_oi_change_{lag}m"] = np.where(valid, values, np.nan)
    for window in config.taker_windows_minutes:
        quote_sum = _block_rolling_sum(quote, timestamp, window, config.interval_ms)
        taker_sum = _block_rolling_sum(taker, timestamp, window, config.interval_ms)
        imbalance = np.full(len(quote_sum), np.nan, dtype=float)
        np.divide(2.0 * taker_sum, quote_sum, out=imbalance, where=quote_sum > 0.0)
        imbalance[quote_sum > 0.0] -= 1.0
        result[f"{prefix}_taker_imbalance_{window}m"] = imbalance
    previous_close = np.concatenate(([close[0]], close[:-1]))
    block_starts = np.concatenate(
        (np.asarray([True]), np.diff(timestamp) != config.interval_ms)
    )
    previous_close[block_starts] = close[block_starts]
    true_range = np.maximum.reduce(
        (high - low, np.abs(high - previous_close), np.abs(low - previous_close))
    )
    range_sum = _block_rolling_sum(
        true_range, timestamp, config.range_window_minutes, config.interval_ms
    )
    result[f"{prefix}_range_activity_{config.range_window_minutes}m"] = range_sum / close
    activity = _block_rolling_sum(
        quote, timestamp, config.activity_window_minutes, config.interval_ms
    )
    prior_mean = _block_prior_rolling_mean(
        quote,
        timestamp,
        window=config.activity_baseline_minutes,
        exclude_recent=config.activity_window_minutes,
        interval_ms=config.interval_ms,
    )
    denominator = prior_mean * config.activity_window_minutes
    result[f"{prefix}_quote_activity_{config.activity_window_minutes}m_vs_24h"] = np.where(
        denominator > 0.0, activity / denominator, np.nan
    )
    result["market_context_schema_version"] = config.schema_version
    return result


def attach_reference_market_context(
    rows: pd.DataFrame,
    reference_features: tuple[pd.DataFrame, ...],
    *,
    config: ReferenceMarketContextConfig,
    snapshot_time_column: str = "snapshot_time_ms",
) -> pd.DataFrame:
    if snapshot_time_column not in rows:
        raise ReferenceMarketContextError(f"rows missing {snapshot_time_column}")
    joined = rows.copy()
    original_count = len(joined)
    for features, reference in zip(reference_features, config.references, strict=True):
        if features["snapshot_time_ms"].duplicated().any():
            raise ReferenceMarketContextError(f"duplicate feature timestamps for {reference.symbol}")
        schemas = set(features["market_context_schema_version"].dropna().astype(str).unique())
        if schemas != {config.schema_version}:
            raise ReferenceMarketContextError(
                f"market-context schema mismatch for {reference.symbol}: {sorted(schemas)}"
            )
        right = features.drop(columns=["market_context_schema_version"]).rename(
            columns={"snapshot_time_ms": snapshot_time_column}
        )
        joined = joined.merge(
            right,
            on=snapshot_time_column,
            how="left",
            validate="many_to_one",
            sort=False,
            suffixes=("", f"_{reference.alias}"),
        )
        flag = f"has_{reference.alias}_context"
        joined[flag] = joined[flag].eq(True)
    if len(joined) != original_count:
        raise ReferenceMarketContextError("market-context join changed row count")
    joined["market_context_schema_version"] = config.schema_version
    return joined


def reference_market_feature_names(config: ReferenceMarketContextConfig) -> tuple[str, ...]:
    names: list[str] = []
    for reference in config.references:
        prefix = reference.alias
        names.extend(f"{prefix}_return_{lag}m" for lag in config.return_lags_minutes)
        names.extend(f"{prefix}_oi_change_{lag}m" for lag in config.oi_lags_minutes)
        names.extend(f"{prefix}_taker_imbalance_{window}m" for window in config.taker_windows_minutes)
        names.extend(
            (
                f"{prefix}_range_activity_{config.range_window_minutes}m",
                f"{prefix}_quote_activity_{config.activity_window_minutes}m_vs_24h",
            )
        )
    return tuple(names)


def _causal_fractional_lag(values: np.ndarray, timestamps: np.ndarray, lag: int, interval_ms: int) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    if lag >= len(values):
        return result
    valid = timestamps[lag:] - timestamps[:-lag] == lag * interval_ms
    previous = values[:-lag]
    current = values[lag:]
    finite = valid & np.isfinite(previous) & np.isfinite(current) & (previous != 0.0)
    target = result[lag:]
    target[finite] = current[finite] / previous[finite] - 1.0
    return result


def _lagged_bool(values: np.ndarray, timestamps: np.ndarray, lag: int, interval_ms: int) -> np.ndarray:
    result = np.zeros(len(values), dtype=bool)
    if lag < len(values):
        result[lag:] = values[:-lag] & (
            timestamps[lag:] - timestamps[:-lag] == lag * interval_ms
        )
    return result


def _block_rolling_sum(values: np.ndarray, timestamps: np.ndarray, window: int, interval_ms: int) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    for start, stop in _contiguous_slices(timestamps, interval_ms):
        result[start:stop] = (
            pd.Series(values[start:stop]).rolling(window, min_periods=window).sum().to_numpy()
        )
    return result


def _block_prior_rolling_mean(
    values: np.ndarray,
    timestamps: np.ndarray,
    *,
    window: int,
    exclude_recent: int,
    interval_ms: int,
) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    for start, stop in _contiguous_slices(timestamps, interval_ms):
        result[start:stop] = (
            pd.Series(values[start:stop])
            .shift(exclude_recent)
            .rolling(window, min_periods=window)
            .mean()
            .to_numpy()
        )
    return result


def _contiguous_slices(timestamps: np.ndarray, interval_ms: int) -> list[tuple[int, int]]:
    if not len(timestamps):
        return []
    breaks = np.flatnonzero(np.diff(timestamps) != interval_ms) + 1
    boundaries = np.concatenate(([0], breaks, [len(timestamps)]))
    return [(int(a), int(b)) for a, b in zip(boundaries[:-1], boundaries[1:])]


def _explicit_bool(series: pd.Series, name: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.astype(bool)
    normalized = series.astype(str).str.lower()
    if not normalized.isin({"true", "false"}).all():
        raise ReferenceMarketContextError(f"{name} must contain explicit booleans")
    return normalized == "true"


__all__ = [
    "ReferenceMarketContextError",
    "attach_reference_market_context",
    "build_reference_market_features",
    "reference_market_feature_names",
]
