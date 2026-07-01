from __future__ import annotations

import math

import numpy as np
import pandas as pd

from anomaly_science.market_context.config import EventScopedPerpCrowdingContextConfig


PERP_CROWDING_ARCHIVE_SCHEMA_VERSION = "binance_event_perp_crowding_archive_v1"
PERP_CROWDING_FAMILY = "pump_fade_perp_crowding_v1"


def perp_crowding_feature_names(
    config: EventScopedPerpCrowdingContextConfig,
) -> tuple[str, ...]:
    return (
        "symbol_premium_index",
        *(f"symbol_premium_change_{lag}m" for lag in config.premium_change_lags_minutes),
        *(f"symbol_premium_mean_{window}m" for window in config.premium_stat_windows_minutes),
        "symbol_premium_std_60m",
        "symbol_premium_max_60m",
        f"symbol_premium_zscore_{config.premium_zscore_window_minutes}m",
        "symbol_premium_change_since_ignition",
        "symbol_funding_rate",
        "symbol_funding_rate_change",
        f"symbol_funding_rate_mean_{config.funding_mean_observations}",
        "symbol_minutes_since_funding",
        "symbol_premium_minus_funding_rate",
        "symbol_premium_positive_funding_positive",
    )


def build_perp_crowding_features(
    premium: pd.DataFrame,
    funding: pd.DataFrame,
    *,
    symbol: str,
    config: EventScopedPerpCrowdingContextConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    premium_required = {
        "symbol", "premium_source_time_ms", "premium_available_time_ms",
        "premium_close", "archive_schema_version",
    }
    funding_required = {
        "symbol", "funding_source_time_ms", "funding_available_time_ms",
        "funding_rate", "funding_interval_hours", "archive_schema_version",
    }
    for frame, required, label in (
        (premium, premium_required, "premium"),
        (funding, funding_required, "funding"),
    ):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"perp crowding {label} archive missing columns: {missing}")
        if not frame.empty and set(frame["symbol"].astype(str).unique()) != {symbol}:
            raise ValueError(f"perp crowding {label} symbol mismatch for {symbol}")
        schemas = set(frame["archive_schema_version"].dropna().astype(str).unique())
        if schemas and schemas != {PERP_CROWDING_ARCHIVE_SCHEMA_VERSION}:
            raise ValueError(f"perp crowding {label} archive schema mismatch")

    premium_features = _build_premium_features(premium, symbol=symbol, config=config)
    funding_features = _build_funding_features(funding, symbol=symbol, config=config)
    return premium_features, funding_features


def attach_perp_crowding_context(
    rows: pd.DataFrame,
    feature_frames: tuple[tuple[str, pd.DataFrame, pd.DataFrame], ...],
    *,
    config: EventScopedPerpCrowdingContextConfig,
    symbol_column: str = "symbol",
    snapshot_time_column: str = "snapshot_time_ms",
    anchor_time_column: str = "ignition_time_ms",
) -> pd.DataFrame:
    required = {symbol_column, snapshot_time_column, anchor_time_column}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"perp crowding rows missing columns: {missing}")
    by_symbol: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for symbol, premium, funding in feature_frames:
        observed_symbols = set(premium["symbol"].astype(str).unique()) | set(
            funding["symbol"].astype(str).unique()
        )
        if observed_symbols and observed_symbols != {symbol}:
            raise ValueError("perp crowding feature-frame symbol differs from explicit key")
        if symbol in by_symbol:
            raise ValueError(f"duplicate perp crowding feature frames for {symbol}")
        by_symbol[symbol] = (premium, funding)

    feature_names = perp_crowding_feature_names(config)
    indexed = rows.copy()
    indexed["__original_order"] = np.arange(len(indexed), dtype=np.int64)
    parts: list[pd.DataFrame] = []
    for raw_symbol, group in indexed.groupby(symbol_column, sort=False):
        symbol = str(raw_symbol)
        frames = by_symbol.get(symbol)
        if frames is None:
            parts.append(_missing_context(group, feature_names, config))
            continue
        premium, funding = frames
        current = group.sort_values(snapshot_time_column, kind="mergesort").copy()
        if premium.empty:
            for name in (
                "premium_source_time_ms", "premium_available_time_ms",
                "symbol_premium_index", "ignition_premium_available_time_ms",
                "symbol_premium_index_at_ignition",
                *(f"symbol_premium_change_{lag}m" for lag in config.premium_change_lags_minutes),
                *(f"symbol_premium_mean_{window}m" for window in config.premium_stat_windows_minutes),
                "symbol_premium_std_60m", "symbol_premium_max_60m",
                f"symbol_premium_zscore_{config.premium_zscore_window_minutes}m",
            ):
                current[name] = math.nan
        else:
            current = pd.merge_asof(
                current,
                premium.sort_values("premium_available_time_ms", kind="mergesort"),
                left_on=snapshot_time_column,
                right_on="premium_available_time_ms",
                direction="backward",
                tolerance=config.premium_max_age_minutes * 60_000,
                allow_exact_matches=True,
                suffixes=("", "_premium"),
            )
            ignition = premium.loc[:, ["premium_available_time_ms", "symbol_premium_index"]].rename(
                columns={
                    "premium_available_time_ms": "ignition_premium_available_time_ms",
                    "symbol_premium_index": "symbol_premium_index_at_ignition",
                }
            )
            current = pd.merge_asof(
                current.sort_values(anchor_time_column, kind="mergesort"),
                ignition.sort_values("ignition_premium_available_time_ms", kind="mergesort"),
                left_on=anchor_time_column,
                right_on="ignition_premium_available_time_ms",
                direction="backward",
                tolerance=config.premium_max_age_minutes * 60_000,
                allow_exact_matches=True,
            )
        current["symbol_premium_change_since_ignition"] = (
            current["symbol_premium_index"]
            - current["symbol_premium_index_at_ignition"]
        )
        if funding.empty:
            for name in (
                "funding_source_time_ms", "funding_available_time_ms",
                "symbol_funding_rate", "symbol_funding_rate_change",
                f"symbol_funding_rate_mean_{config.funding_mean_observations}",
            ):
                current[name] = math.nan
        else:
            current = pd.merge_asof(
                current.sort_values(snapshot_time_column, kind="mergesort"),
                funding.sort_values("funding_available_time_ms", kind="mergesort"),
                left_on=snapshot_time_column,
                right_on="funding_available_time_ms",
                direction="backward",
                tolerance=config.funding_max_age_minutes * 60_000,
                allow_exact_matches=True,
                suffixes=("", "_funding"),
            )
        current["symbol_minutes_since_funding"] = (
            current[snapshot_time_column] - current["funding_available_time_ms"]
        ) / 60_000.0
        current["symbol_premium_minus_funding_rate"] = (
            current["symbol_premium_index"] - current["symbol_funding_rate"]
        )
        current["symbol_premium_positive_funding_positive"] = (
            (current["symbol_premium_index"] > 0.0)
            & (current["symbol_funding_rate"] > 0.0)
        ).where(
            current["symbol_premium_index"].notna()
            & current["symbol_funding_rate"].notna()
        )
        numeric = current.loc[:, list(feature_names)].apply(pd.to_numeric, errors="coerce")
        current["perp_crowding_complete"] = (
            numeric.replace([np.inf, -np.inf], np.nan).notna().all(axis=1)
        )
        current["has_symbol_premium_context"] = current["premium_available_time_ms"].notna()
        current["has_symbol_funding_context"] = current["funding_available_time_ms"].notna()
        _audit_join_times(
            current,
            snapshot_time_column=snapshot_time_column,
            anchor_time_column=anchor_time_column,
        )
        current["perp_crowding_context_schema_version"] = config.schema_version
        current = current.drop(
            columns=[
                "symbol_premium_index_at_ignition",
                *[
                    name
                    for name in (
                        "symbol_premium",
                        "symbol_funding",
                        "perp_crowding_context_schema_version_funding",
                    )
                    if name in current.columns
                ],
            ]
        )
        parts.append(current)
    joined = pd.concat(parts, ignore_index=True).sort_values(
        "__original_order", kind="mergesort"
    ).drop(columns="__original_order").reset_index(drop=True)
    if len(joined) != len(rows):
        raise AssertionError("perp crowding join changed row count")
    return joined


def _build_premium_features(
    premium: pd.DataFrame,
    *,
    symbol: str,
    config: EventScopedPerpCrowdingContextConfig,
) -> pd.DataFrame:
    if premium.empty:
        return pd.DataFrame(columns=["symbol", "premium_available_time_ms"])
    work = premium.sort_values("premium_available_time_ms", kind="mergesort").drop_duplicates(
        "premium_available_time_ms", keep="last"
    ).reset_index(drop=True)
    available = pd.to_numeric(work["premium_available_time_ms"], errors="raise").to_numpy(np.int64)
    source = pd.to_numeric(work["premium_source_time_ms"], errors="raise").to_numpy(np.int64)
    values = pd.to_numeric(work["premium_close"], errors="raise").to_numpy(float)
    if not np.isfinite(values).all() or np.any(available <= source):
        raise ValueError("premium archive values/times are invalid")
    result = pd.DataFrame(
        {
            "symbol": symbol,
            "premium_source_time_ms": source,
            "premium_available_time_ms": available,
            "symbol_premium_index": values,
        }
    )
    positions = {int(value): index for index, value in enumerate(available)}
    for lag in config.premium_change_lags_minutes:
        changes = np.full(len(values), np.nan)
        delta = lag * 60_000
        for index, timestamp in enumerate(available):
            prior = positions.get(int(timestamp - delta))
            if prior is not None:
                changes[index] = values[index] - values[prior]
        result[f"symbol_premium_change_{lag}m"] = changes
    series = pd.Series(values, index=pd.to_datetime(available, unit="ms", utc=True))
    for window in config.premium_stat_windows_minutes:
        rolling = series.rolling(f"{window}min", min_periods=window)
        result[f"symbol_premium_mean_{window}m"] = rolling.mean().to_numpy()
        if window == 60:
            result["symbol_premium_std_60m"] = rolling.std(ddof=0).to_numpy()
            result["symbol_premium_max_60m"] = rolling.max().to_numpy()
    zrolling = series.rolling(
        f"{config.premium_zscore_window_minutes}min",
        min_periods=config.premium_zscore_min_periods,
    )
    zmean = zrolling.mean().to_numpy()
    zstd = zrolling.std(ddof=0).to_numpy()
    result[f"symbol_premium_zscore_{config.premium_zscore_window_minutes}m"] = np.divide(
        values - zmean,
        zstd,
        out=np.full(len(values), np.nan),
        where=zstd > 0.0,
    )
    result["perp_crowding_context_schema_version"] = config.schema_version
    return result


def _build_funding_features(
    funding: pd.DataFrame,
    *,
    symbol: str,
    config: EventScopedPerpCrowdingContextConfig,
) -> pd.DataFrame:
    if funding.empty:
        return pd.DataFrame(columns=["symbol", "funding_available_time_ms"])
    work = funding.sort_values("funding_available_time_ms", kind="mergesort").drop_duplicates(
        "funding_available_time_ms", keep="last"
    ).reset_index(drop=True)
    source = pd.to_numeric(work["funding_source_time_ms"], errors="raise").to_numpy(np.int64)
    available = pd.to_numeric(work["funding_available_time_ms"], errors="raise").to_numpy(np.int64)
    rate = pd.to_numeric(work["funding_rate"], errors="raise").to_numpy(float)
    if not np.isfinite(rate).all() or np.any(
        available - source != config.funding_publication_lag_minutes * 60_000
    ):
        raise ValueError("funding archive values/publication lag are invalid")
    series = pd.Series(rate)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "funding_source_time_ms": source,
            "funding_available_time_ms": available,
            "symbol_funding_rate": rate,
            "symbol_funding_rate_change": series.diff().to_numpy(),
            f"symbol_funding_rate_mean_{config.funding_mean_observations}": series.rolling(
                config.funding_mean_observations,
                min_periods=config.funding_mean_observations,
            ).mean().to_numpy(),
            "perp_crowding_context_schema_version": config.schema_version,
        }
    )


def _missing_context(
    group: pd.DataFrame,
    feature_names: tuple[str, ...],
    config: EventScopedPerpCrowdingContextConfig,
) -> pd.DataFrame:
    result = group.copy()
    for name in feature_names:
        result[name] = math.nan
    for name in (
        "premium_source_time_ms", "premium_available_time_ms",
        "ignition_premium_available_time_ms", "funding_source_time_ms",
        "funding_available_time_ms",
    ):
        result[name] = math.nan
    result["has_symbol_premium_context"] = False
    result["has_symbol_funding_context"] = False
    result["perp_crowding_complete"] = False
    result["perp_crowding_context_schema_version"] = config.schema_version
    return result


def _audit_join_times(
    frame: pd.DataFrame,
    *,
    snapshot_time_column: str,
    anchor_time_column: str,
) -> None:
    premium = frame["premium_available_time_ms"].notna()
    ignition = frame["ignition_premium_available_time_ms"].notna()
    funding = frame["funding_available_time_ms"].notna()
    if (frame.loc[premium, "premium_available_time_ms"] > frame.loc[premium, snapshot_time_column]).any():
        raise AssertionError("perp premium join used future availability")
    if (frame.loc[ignition, "ignition_premium_available_time_ms"] > frame.loc[ignition, anchor_time_column]).any():
        raise AssertionError("ignition premium join used future availability")
    if (frame.loc[funding, "funding_available_time_ms"] > frame.loc[funding, snapshot_time_column]).any():
        raise AssertionError("funding join used future availability")


__all__ = [
    "PERP_CROWDING_ARCHIVE_SCHEMA_VERSION",
    "PERP_CROWDING_FAMILY",
    "attach_perp_crowding_context",
    "build_perp_crowding_features",
    "perp_crowding_feature_names",
]
