from __future__ import annotations

import math

import numpy as np
import pandas as pd

from anomaly_science.market_context.config import EventScopedPositioningContextConfig
from anomaly_science.market_context.metrics_archive import (
    REFERENCE_METRICS_COLUMNS,
    REFERENCE_METRICS_SCHEMA_VERSION,
)
from anomaly_science.market_context.positioning import POSITIONING_RATIO_ALIASES


EVENT_POSITIONING_PREFIX = "symbol_pos"


def event_positioning_feature_names(
    config: EventScopedPositioningContextConfig,
) -> tuple[str, ...]:
    names: list[str] = []
    for alias in POSITIONING_RATIO_ALIASES.values():
        names.append(f"{EVENT_POSITIONING_PREFIX}_{alias}_log_ratio")
        names.extend(
            f"{EVENT_POSITIONING_PREFIX}_{alias}_log_change_{lag}m"
            for lag in config.change_lags_minutes
        )
        names.append(f"{EVENT_POSITIONING_PREFIX}_{alias}_log_change_since_ignition")
    names.extend(
        (
            f"{EVENT_POSITIONING_PREFIX}_toptrader_account_minus_global",
            f"{EVENT_POSITIONING_PREFIX}_toptrader_position_minus_global",
            f"{EVENT_POSITIONING_PREFIX}_taker_minus_global",
        )
    )
    return tuple(names)


def build_event_positioning_features(
    metrics: pd.DataFrame,
    *,
    symbol: str,
    config: EventScopedPositioningContextConfig,
) -> pd.DataFrame:
    required = {
        "source_time_ms",
        "available_time_ms",
        "symbol",
        "metrics_schema_version",
        *REFERENCE_METRICS_COLUMNS,
    }
    missing = sorted(required - set(metrics.columns))
    if missing:
        raise ValueError(f"event positioning metrics missing columns: {missing}")
    if metrics.empty:
        return _empty_feature_frame(config)
    work = metrics.sort_values("available_time_ms", kind="mergesort").reset_index(drop=True)
    if set(work["symbol"].astype(str).unique()) != {symbol}:
        raise ValueError(f"event positioning symbol mismatch for {symbol}")
    if set(work["metrics_schema_version"].astype(str).unique()) != {
        REFERENCE_METRICS_SCHEMA_VERSION
    }:
        raise ValueError("event positioning raw metrics schema mismatch")
    if work["available_time_ms"].duplicated().any():
        raise ValueError(f"event positioning availability times duplicate for {symbol}")
    source = pd.to_numeric(work["source_time_ms"], errors="raise").to_numpy(dtype=np.int64)
    available = pd.to_numeric(work["available_time_ms"], errors="raise").to_numpy(
        dtype=np.int64
    )
    expected_publication_ms = config.publication_lag_minutes * 60_000
    if np.any(available - source != expected_publication_ms):
        raise ValueError("event positioning publication lag differs from protocol")
    result = pd.DataFrame(
        {
            "symbol": symbol,
            "metrics_source_time_ms": source,
            "metrics_available_time_ms": available,
        }
    )
    logs: dict[str, np.ndarray] = {}
    for source_name, alias in POSITIONING_RATIO_ALIASES.items():
        values = pd.to_numeric(work[source_name], errors="raise").to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.any(values <= 0.0):
            raise ValueError(f"event positioning ratio {source_name} must be finite/positive")
        logs[alias] = np.log(values)
        result[f"{EVENT_POSITIONING_PREFIX}_{alias}_log_ratio"] = logs[alias]
        for lag in config.change_lags_minutes:
            periods = lag // config.metrics_interval_minutes
            result[f"{EVENT_POSITIONING_PREFIX}_{alias}_log_change_{lag}m"] = (
                _causal_change(logs[alias], available, periods, lag * 60_000)
            )
    result[f"{EVENT_POSITIONING_PREFIX}_toptrader_account_minus_global"] = (
        logs["toptrader_account"] - logs["global_account"]
    )
    result[f"{EVENT_POSITIONING_PREFIX}_toptrader_position_minus_global"] = (
        logs["toptrader_position"] - logs["global_account"]
    )
    result[f"{EVENT_POSITIONING_PREFIX}_taker_minus_global"] = (
        logs["taker"] - logs["global_account"]
    )
    result["event_positioning_context_schema_version"] = config.schema_version
    return result


def attach_event_positioning_context(
    rows: pd.DataFrame,
    feature_frames: tuple[pd.DataFrame, ...],
    *,
    config: EventScopedPositioningContextConfig,
    symbol_column: str = "symbol",
    snapshot_time_column: str = "snapshot_time_ms",
    anchor_time_column: str = "ignition_time_ms",
) -> pd.DataFrame:
    required = {symbol_column, snapshot_time_column, anchor_time_column}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"event positioning rows missing columns: {missing}")
    feature_names = event_positioning_feature_names(config)
    levels = tuple(
        f"{EVENT_POSITIONING_PREFIX}_{alias}_log_ratio"
        for alias in POSITIONING_RATIO_ALIASES.values()
    )
    features_by_symbol: dict[str, pd.DataFrame] = {}
    for frame in feature_frames:
        if frame.empty:
            continue
        schemas = set(
            frame["event_positioning_context_schema_version"].dropna().astype(str).unique()
        )
        if schemas != {config.schema_version}:
            raise ValueError("event positioning feature schema mismatch")
        symbols = tuple(frame["symbol"].astype(str).unique())
        if len(symbols) != 1 or symbols[0] in features_by_symbol:
            raise ValueError("event positioning feature frames must be one per unique symbol")
        features_by_symbol[symbols[0]] = frame

    output_parts: list[pd.DataFrame] = []
    indexed = rows.copy()
    indexed["__original_order"] = np.arange(len(indexed), dtype=np.int64)
    for raw_symbol, group in indexed.groupby(symbol_column, sort=False):
        symbol = str(raw_symbol)
        features = features_by_symbol.get(symbol)
        if features is None or features.empty:
            output_parts.append(_missing_context(group, feature_names, config))
            continue
        right = features.drop(columns=["event_positioning_context_schema_version"])
        current = pd.merge_asof(
            group.sort_values(snapshot_time_column, kind="mergesort"),
            right.sort_values("metrics_available_time_ms", kind="mergesort"),
            left_on=snapshot_time_column,
            right_on="metrics_available_time_ms",
            direction="backward",
            tolerance=config.max_age_minutes * 60_000,
            allow_exact_matches=True,
            suffixes=("", "_metrics"),
        )
        anchor_right = right.loc[:, ["metrics_available_time_ms", *levels]].rename(
            columns={
                "metrics_available_time_ms": "ignition_metrics_available_time_ms",
                **{name: f"{name}_at_ignition" for name in levels},
            }
        )
        current = pd.merge_asof(
            current.sort_values(anchor_time_column, kind="mergesort"),
            anchor_right.sort_values(
                "ignition_metrics_available_time_ms", kind="mergesort"
            ),
            left_on=anchor_time_column,
            right_on="ignition_metrics_available_time_ms",
            direction="backward",
            tolerance=config.max_age_minutes * 60_000,
            allow_exact_matches=True,
        )
        for alias, level_name in zip(
            POSITIONING_RATIO_ALIASES.values(), levels, strict=True
        ):
            current[
                f"{EVENT_POSITIONING_PREFIX}_{alias}_log_change_since_ignition"
            ] = current[level_name] - current[f"{level_name}_at_ignition"]
        current["has_symbol_positioning_context"] = current[
            "metrics_available_time_ms"
        ].notna()
        current["has_symbol_positioning_at_ignition"] = current[
            "ignition_metrics_available_time_ms"
        ].notna()
        current["symbol_positioning_age_minutes"] = (
            current[snapshot_time_column] - current["metrics_available_time_ms"]
        ) / 60_000.0
        current["symbol_positioning_ignition_age_minutes"] = (
            current[anchor_time_column]
            - current["ignition_metrics_available_time_ms"]
        ) / 60_000.0
        current["symbol_positioning_complete"] = (
            current.loc[:, list(feature_names)]
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .notna()
            .all(axis=1)
        )
        _audit_join_times(
            current,
            snapshot_time_column=snapshot_time_column,
            anchor_time_column=anchor_time_column,
        )
        current["event_positioning_context_schema_version"] = config.schema_version
        current = current.drop(
            columns=[f"{name}_at_ignition" for name in levels]
        )
        output_parts.append(current)
    joined = pd.concat(output_parts, ignore_index=True)
    joined = joined.sort_values("__original_order", kind="mergesort").drop(
        columns=["__original_order"]
    ).reset_index(drop=True)
    if len(joined) != len(rows):
        raise AssertionError("event positioning join changed row count")
    return joined


def _missing_context(
    group: pd.DataFrame,
    feature_names: tuple[str, ...],
    config: EventScopedPositioningContextConfig,
) -> pd.DataFrame:
    result = group.copy()
    for name in feature_names:
        result[name] = math.nan
    for name in (
        "metrics_source_time_ms",
        "metrics_available_time_ms",
        "ignition_metrics_available_time_ms",
        "symbol_positioning_age_minutes",
        "symbol_positioning_ignition_age_minutes",
    ):
        result[name] = math.nan
    result["has_symbol_positioning_context"] = False
    result["has_symbol_positioning_at_ignition"] = False
    result["symbol_positioning_complete"] = False
    result["event_positioning_context_schema_version"] = config.schema_version
    return result


def _audit_join_times(
    frame: pd.DataFrame,
    *,
    snapshot_time_column: str,
    anchor_time_column: str,
) -> None:
    current = frame["metrics_available_time_ms"].notna()
    ignition = frame["ignition_metrics_available_time_ms"].notna()
    if (
        frame.loc[current, "metrics_available_time_ms"]
        > frame.loc[current, snapshot_time_column]
    ).any():
        raise AssertionError("event positioning current join used future availability")
    if (
        frame.loc[ignition, "ignition_metrics_available_time_ms"]
        > frame.loc[ignition, anchor_time_column]
    ).any():
        raise AssertionError("event positioning ignition join used future availability")
    if (
        frame.loc[current, "metrics_source_time_ms"]
        >= frame.loc[current, "metrics_available_time_ms"]
    ).any():
        raise AssertionError("event positioning join lacks positive publication lag")


def _causal_change(
    values: np.ndarray,
    available_times: np.ndarray,
    periods: int,
    expected_delta_ms: int,
) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    if periods < len(values):
        valid = available_times[periods:] - available_times[:-periods] == expected_delta_ms
        target = result[periods:]
        target[valid] = values[periods:][valid] - values[:-periods][valid]
    return result


def _empty_feature_frame(
    config: EventScopedPositioningContextConfig,
) -> pd.DataFrame:
    return pd.DataFrame(
        columns=(
            "symbol",
            "metrics_source_time_ms",
            "metrics_available_time_ms",
            *tuple(
                name
                for name in event_positioning_feature_names(config)
                if not name.endswith("_log_change_since_ignition")
            ),
            "event_positioning_context_schema_version",
        )
    )


__all__ = [
    "EVENT_POSITIONING_PREFIX",
    "attach_event_positioning_context",
    "build_event_positioning_features",
    "event_positioning_feature_names",
]
