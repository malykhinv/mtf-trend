from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.market_context.config import (
    ReferenceMarketSpec,
    ReferencePositioningContextConfig,
)
from anomaly_science.market_context.metrics_archive import (
    REFERENCE_METRICS_COLUMNS,
    REFERENCE_METRICS_SCHEMA_VERSION,
)


POSITIONING_RATIO_ALIASES = {
    "toptrader_account_long_short_ratio": "toptrader_account",
    "toptrader_position_long_short_ratio": "toptrader_position",
    "global_account_long_short_ratio": "global_account",
    "taker_long_short_volume_ratio": "taker",
}


def build_reference_positioning_features(
    metrics: pd.DataFrame,
    *,
    reference: ReferenceMarketSpec,
    config: ReferencePositioningContextConfig,
) -> pd.DataFrame:
    required = {
        "source_time_ms", "available_time_ms", "symbol", "metrics_schema_version",
        *REFERENCE_METRICS_COLUMNS,
    }
    missing = sorted(required - set(metrics.columns))
    if missing:
        raise ValueError(f"reference positioning metrics missing columns: {missing}")
    work = metrics.sort_values("available_time_ms", kind="mergesort").reset_index(drop=True)
    if set(work["symbol"].astype(str).unique()) != {reference.symbol}:
        raise ValueError(f"positioning symbol mismatch for {reference.symbol}")
    if set(work["metrics_schema_version"].astype(str).unique()) != {
        REFERENCE_METRICS_SCHEMA_VERSION
    }:
        raise ValueError("positioning metrics schema mismatch")
    if work["available_time_ms"].duplicated().any():
        raise ValueError("positioning available times must be unique")
    source = pd.to_numeric(work["source_time_ms"], errors="raise").to_numpy(dtype=np.int64)
    available = pd.to_numeric(work["available_time_ms"], errors="raise").to_numpy(dtype=np.int64)
    if np.any(available <= source):
        raise ValueError("positioning metrics must have positive publication lag")
    result = pd.DataFrame(
        {
            "source_time_ms": source,
            "available_time_ms": available,
            "positioning_context_schema_version": config.schema_version,
        }
    )
    logs: dict[str, np.ndarray] = {}
    for source_name, alias in POSITIONING_RATIO_ALIASES.items():
        values = pd.to_numeric(work[source_name], errors="raise").to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.any(values <= 0.0):
            raise ValueError(f"positioning ratio {source_name} must be finite and positive")
        logs[alias] = np.log(values)
        result[f"{reference.alias}_{alias}_log_ratio"] = logs[alias]
        for lag in config.change_lags_minutes:
            periods = lag // config.metrics_interval_minutes
            result[f"{reference.alias}_{alias}_log_change_{lag}m"] = _causal_change(
                logs[alias], available, periods, lag * 60_000
            )
    prefix = reference.alias
    result[f"{prefix}_toptrader_account_minus_global"] = (
        logs["toptrader_account"] - logs["global_account"]
    )
    result[f"{prefix}_toptrader_position_minus_global"] = (
        logs["toptrader_position"] - logs["global_account"]
    )
    result[f"{prefix}_taker_minus_global"] = logs["taker"] - logs["global_account"]
    return result


def attach_reference_positioning_context(
    rows: pd.DataFrame,
    feature_frames: tuple[pd.DataFrame, ...],
    *,
    config: ReferencePositioningContextConfig,
    snapshot_time_column: str = "snapshot_time_ms",
) -> pd.DataFrame:
    if snapshot_time_column not in rows:
        raise ValueError(f"rows missing {snapshot_time_column}")
    joined = rows.copy()
    joined["__original_order"] = np.arange(len(joined), dtype=np.int64)
    for features, reference in zip(feature_frames, config.references, strict=True):
        schemas = set(features["positioning_context_schema_version"].astype(str).unique())
        if schemas != {config.schema_version}:
            raise ValueError(f"positioning feature schema mismatch for {reference.symbol}")
        renamed = features.drop(columns=["positioning_context_schema_version"]).rename(
            columns={
                "source_time_ms": f"{reference.alias}_metrics_source_time_ms",
                "available_time_ms": f"{reference.alias}_metrics_available_time_ms",
            }
        )
        joined = pd.merge_asof(
            joined.sort_values(snapshot_time_column, kind="mergesort"),
            renamed.sort_values(f"{reference.alias}_metrics_available_time_ms", kind="mergesort"),
            left_on=snapshot_time_column,
            right_on=f"{reference.alias}_metrics_available_time_ms",
            direction="backward",
            tolerance=config.max_age_minutes * 60_000,
            allow_exact_matches=True,
        )
        available_column = f"{reference.alias}_metrics_available_time_ms"
        source_column = f"{reference.alias}_metrics_source_time_ms"
        flag = f"has_{reference.alias}_positioning_context"
        joined[flag] = joined[available_column].notna()
        joined[f"{reference.alias}_positioning_age_minutes"] = (
            joined[snapshot_time_column] - joined[available_column]
        ) / 60_000.0
        valid = joined[flag]
        if (joined.loc[valid, available_column] > joined.loc[valid, snapshot_time_column]).any():
            raise AssertionError("positioning as-of join used future availability")
        if (joined.loc[valid, source_column] >= joined.loc[valid, available_column]).any():
            raise AssertionError("positioning publication lag is not positive")
    joined["positioning_context_schema_version"] = config.schema_version
    return joined.sort_values("__original_order", kind="mergesort").drop(
        columns=["__original_order"]
    ).reset_index(drop=True)


def reference_positioning_feature_names(
    config: ReferencePositioningContextConfig,
) -> tuple[str, ...]:
    names: list[str] = []
    for reference in config.references:
        for alias in POSITIONING_RATIO_ALIASES.values():
            names.append(f"{reference.alias}_{alias}_log_ratio")
            names.extend(
                f"{reference.alias}_{alias}_log_change_{lag}m"
                for lag in config.change_lags_minutes
            )
        names.extend(
            (
                f"{reference.alias}_toptrader_account_minus_global",
                f"{reference.alias}_toptrader_position_minus_global",
                f"{reference.alias}_taker_minus_global",
            )
        )
    return tuple(names)


def _causal_change(
    values: np.ndarray,
    available_times: np.ndarray,
    periods: int,
    expected_delta_ms: int,
) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    if periods < len(values):
        valid = (
            available_times[periods:] - available_times[:-periods]
            == expected_delta_ms
        )
        target = result[periods:]
        target[valid] = values[periods:][valid] - values[:-periods][valid]
    return result


__all__ = [
    "attach_reference_positioning_context",
    "build_reference_positioning_features",
    "POSITIONING_RATIO_ALIASES",
    "reference_positioning_feature_names",
]
