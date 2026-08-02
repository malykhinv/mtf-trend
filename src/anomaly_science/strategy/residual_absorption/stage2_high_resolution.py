"""Causal event-scoped aggTrades features for residual absorption research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from anomaly_science.data.aggtrades_minute import AGGTRADES_MINUTE_FEATURES
from anomaly_science.strategy.residual_absorption.spec import (
    HighResolutionAggTradesSpec,
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
)

DEFAULT_AGGTRADES_DIR = Path(
    ".output/market/binance_vision/um_futures/aggtrades_1m_event_scoped"
)
_MINUTE_MS = 60_000
_WEIGHTED_COLUMNS = (
    "trade_notional_p50",
    "trade_notional_p75",
    "trade_notional_p90",
    "trade_notional_p95",
    "trade_notional_p99",
    "top1pct_notional_share",
    "notional_gini",
    "same_side_run_mean",
    "side_sign_entropy",
    "side_flip_rate",
    "inter_arrival_ms_mean",
    "inter_arrival_ms_std",
    "inter_arrival_ms_p90",
    "buy_impact_per_notional",
    "sell_impact_per_notional",
)


def build_enrichment_request_manifest(
    profiles: pd.DataFrame,
    *,
    spec: HighResolutionAggTradesSpec | None = None,
) -> pd.DataFrame:
    """Freeze requests from coarse selections without consulting archive coverage."""

    active = spec or HighResolutionAggTradesSpec()
    required = {
        "event_id",
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "schema_version",
    }
    missing = sorted(required.difference(profiles.columns))
    if missing:
        raise ValueError(f"high-resolution manifest profiles missing columns: {missing}")
    event_id = profiles["event_id"].astype(str)
    symbol = profiles["symbol"].astype(str)
    snapshot = pd.to_numeric(profiles["snapshot_time_ms"], errors="raise").astype("int64")
    cutoff = pd.to_numeric(profiles["feature_cutoff_time_ms"], errors="raise").astype("int64")
    schema = profiles["schema_version"].astype(str)
    if bool(event_id.eq("").any() or symbol.eq("").any() or schema.eq("").any()):
        raise ValueError("event id, symbol, and selection schema must be non-empty")
    if bool(cutoff.gt(snapshot).any()):
        raise ValueError("high-resolution selection cutoff exceeds snapshot")
    start = snapshot - max(active.windows_minutes) * _MINUTE_MS
    if bool(start.ge(snapshot).any()):
        raise ValueError("high-resolution request interval must be positive")
    frame = pd.DataFrame(
        {
            "request_id": "aggtrades:" + event_id + ":" + symbol,
            "request_version": "event_scoped_high_resolution_v1",
            "event_id": event_id,
            "symbol": symbol,
            "selection_snapshot_time_ms": snapshot,
            "selection_feature_cutoff_time_ms": cutoff,
            "selection_granularity_ms": active.selection_granularity_ms,
            "selection_schema_version": schema,
            "source": active.source,
            "enrichment_granularity_ms": active.enrichment_granularity_ms,
            "requested_start_time_ms": start,
            "requested_end_time_ms_exclusive": snapshot,
        }
    )
    return frame.sort_values(
        ["selection_snapshot_time_ms", "symbol"], kind="mergesort"
    ).reset_index(drop=True)


def compute_symbol_aggtrades_features(
    minutes: pd.DataFrame,
    *,
    events: pd.DataFrame,
    symbol: str,
    spec: HighResolutionAggTradesSpec | None = None,
) -> pd.DataFrame:
    """Aggregate only closed minutes preceding each coarse selection snapshot."""

    active = spec or HighResolutionAggTradesSpec()
    required_events = {"event_id", "snapshot_time_ms", "impulse_direction"}
    missing_events = sorted(required_events.difference(events.columns))
    if missing_events:
        raise ValueError(f"aggTrades events missing columns: {missing_events}")
    required_minutes = {"timestamp", *AGGTRADES_MINUTE_FEATURES}
    missing_minutes = sorted(required_minutes.difference(minutes.columns))
    if missing_minutes:
        raise ValueError(f"aggTrades minute source missing columns: {missing_minutes}")

    ordered = events.sort_values("snapshot_time_ms", kind="mergesort").reset_index(drop=True)
    event_count = len(ordered)
    snapshots = ordered["snapshot_time_ms"].to_numpy(dtype=np.int64)
    offsets = np.arange(1, max(active.windows_minutes) + 1, dtype=np.int64)
    grid = pd.DataFrame(
        {
            "event_row": np.repeat(np.arange(event_count), len(offsets)),
            "minute_offset": np.tile(offsets, event_count),
            "timestamp": (snapshots[:, None] - offsets[None, :] * _MINUTE_MS).ravel(),
        }
    )
    source = minutes.copy()
    source["timestamp"] = pd.to_numeric(source["timestamp"], errors="raise").astype("int64")
    source = source.drop_duplicates("timestamp", keep="last")
    expanded = grid.merge(source, on="timestamp", how="left", validate="many_to_one")
    result = pd.DataFrame(
        {
            "schema_version": active.schema_version,
            "event_id": ordered["event_id"].astype(str),
            "symbol": symbol,
            "snapshot_time_ms": snapshots,
            "feature_cutoff_time_ms": snapshots,
            "impulse_direction": ordered["impulse_direction"].to_numpy(dtype=np.int8),
        }
    )
    for window in active.windows_minutes:
        result = pd.concat(
            [
                result,
                _aggregate_window(
                    expanded,
                    window_minutes=window,
                    event_count=event_count,
                    direction=result["impulse_direction"].to_numpy(dtype=float),
                ).reset_index(drop=True),
            ],
            axis=1,
        )

    result["aggtrades_trade_intensity_acceleration_5v15"] = _safe_ratio(
        result["aggtrades_5m_trade_count"] / 5.0,
        result["aggtrades_15m_trade_count"] / 15.0,
    )
    result["aggtrades_large_trade_rate_change_5v15"] = (
        result["aggtrades_5m_large_trades_per_1000"]
        - result["aggtrades_15m_large_trades_per_1000"]
    )
    result["aggtrades_run_persistence_acceleration_5v15"] = _safe_ratio(
        result["aggtrades_5m_same_side_run_mean"],
        result["aggtrades_15m_same_side_run_mean"],
    )
    result["aggtrades_primary_complete"] = result[
        "aggtrades_15m_coverage"
    ].ge(active.primary_minimum_coverage)
    return result


def _aggregate_window(
    expanded: pd.DataFrame,
    *,
    window_minutes: int,
    event_count: int,
    direction: np.ndarray,
) -> pd.DataFrame:
    prefix = f"aggtrades_{window_minutes}m_"
    work = expanded.loc[
        expanded["minute_offset"].le(window_minutes)
        & expanded["aggtrades_trade_count"].notna()
    ].copy()
    group = work.groupby("event_row", sort=False)
    index = pd.RangeIndex(event_count)
    minute_count = group.size().reindex(index, fill_value=0)
    total_trades = pd.to_numeric(
        group["aggtrades_trade_count"].sum().reindex(index, fill_value=0.0),
        errors="coerce",
    ).astype(float)
    minute_mean = group["aggtrades_trade_count"].mean().reindex(index)
    minute_std = group["aggtrades_trade_count"].std(ddof=0).reindex(index)
    frame = pd.DataFrame(index=index)
    frame[f"{prefix}minute_count"] = minute_count
    frame[f"{prefix}coverage"] = minute_count / window_minutes
    frame[f"{prefix}missing"] = minute_count.ne(window_minutes)
    frame[f"{prefix}trade_count"] = total_trades
    frame[f"{prefix}trade_count_minute_cv"] = _safe_ratio(minute_std, minute_mean)

    for column in _WEIGHTED_COLUMNS:
        frame[f"{prefix}{column}"] = _weighted_event_mean(
            work, column=column, event_count=event_count
        )
    frame[f"{prefix}same_side_run_max"] = group["same_side_run_max"].max().reindex(index)
    large_count = pd.to_numeric(
        group["large_trade_count"].sum().reindex(index, fill_value=0.0),
        errors="coerce",
    ).astype(float)
    frame[f"{prefix}large_trade_count"] = large_count
    frame[f"{prefix}large_trades_per_1000"] = _safe_ratio(
        1_000.0 * large_count, total_trades
    )
    frame[f"{prefix}p95_to_p50"] = _safe_ratio(
        frame[f"{prefix}trade_notional_p95"], frame[f"{prefix}trade_notional_p50"]
    )
    frame[f"{prefix}p99_to_p50"] = _safe_ratio(
        frame[f"{prefix}trade_notional_p99"], frame[f"{prefix}trade_notional_p50"]
    )
    frame[f"{prefix}inter_arrival_cv"] = _safe_ratio(
        frame[f"{prefix}inter_arrival_ms_std"],
        frame[f"{prefix}inter_arrival_ms_mean"],
    )
    buy = frame[f"{prefix}buy_impact_per_notional"].to_numpy(dtype=float)
    sell = frame[f"{prefix}sell_impact_per_notional"].to_numpy(dtype=float)
    aligned = np.where(direction > 0.0, buy, sell)
    adverse = np.where(direction > 0.0, sell, buy)
    frame[f"{prefix}impulse_aligned_impact_per_notional"] = aligned
    frame[f"{prefix}adverse_impact_per_notional"] = adverse
    frame[f"{prefix}impact_asymmetry"] = np.divide(
        aligned - adverse,
        np.abs(aligned) + np.abs(adverse),
        out=np.full(event_count, np.nan),
        where=np.isfinite(aligned)
        & np.isfinite(adverse)
        & ((np.abs(aligned) + np.abs(adverse)) > 0.0),
    )
    return frame


def _weighted_event_mean(
    frame: pd.DataFrame,
    *,
    column: str,
    event_count: int,
) -> pd.Series:
    value = pd.to_numeric(frame[column], errors="coerce")
    weight = pd.to_numeric(frame["aggtrades_trade_count"], errors="coerce")
    valid = value.notna() & weight.gt(0.0)
    numerator = (value[valid] * weight[valid]).groupby(frame.loc[valid, "event_row"]).sum()
    denominator = weight[valid].groupby(frame.loc[valid, "event_row"]).sum()
    return (numerator / denominator).reindex(range(event_count))


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator_float = pd.to_numeric(numerator, errors="coerce").astype(float)
    denominator_float = pd.to_numeric(denominator, errors="coerce").astype(float)
    valid = numerator_float.notna() & denominator_float.notna() & denominator_float.ne(0.0)
    output = pd.Series(np.nan, index=numerator_float.index, dtype=float)
    output.loc[valid] = numerator_float.loc[valid] / denominator_float.loc[valid]
    return output


def build_stage2_high_resolution_features(
    *,
    stage1_dir: str | Path,
    source_dir: str | Path = DEFAULT_AGGTRADES_DIR,
    progress: Callable[[str], None] | None = print,
    spec: HighResolutionAggTradesSpec | None = None,
) -> Path:
    """Build the locked request manifest and coverage-preserving feature matrix."""

    active = spec or HighResolutionAggTradesSpec()
    root = Path(stage1_dir)
    source_root = Path(source_dir)
    profiles = pd.read_parquet(root / "residual_response_profiles_is.parquet")
    if bool(
        (profiles["snapshot_time_ms"] >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms).any()
    ):
        raise ValueError("high-resolution build received OOS profiles")
    manifest = build_enrichment_request_manifest(profiles, spec=active)
    manifest_path = root / "stage2_high_resolution_requests_is.parquet"
    manifest.to_parquet(manifest_path, index=False, compression="zstd")

    rows: list[pd.DataFrame] = []
    groups = tuple(profiles.groupby("symbol", sort=True))
    columns = ["timestamp", *AGGTRADES_MINUTE_FEATURES]
    for completed, (symbol, events) in enumerate(groups, start=1):
        source = source_root / f"{symbol}.parquet"
        if source.is_file():
            minimum = int(events["snapshot_time_ms"].min()) - max(active.windows_minutes) * _MINUTE_MS
            maximum = int(events["snapshot_time_ms"].max())
            minutes = pd.read_parquet(
                source,
                columns=columns,
                filters=[("timestamp", ">=", minimum), ("timestamp", "<", maximum)],
            )
        else:
            minutes = pd.DataFrame(columns=columns)
        rows.append(
            compute_symbol_aggtrades_features(
                minutes, events=events, symbol=str(symbol), spec=active
            )
        )
        if progress and (completed % 50 == 0 or completed == len(groups)):
            progress(f"stage2 aggTrades {completed}/{len(groups)}")

    frame = pd.concat(rows, ignore_index=True).sort_values(
        ["snapshot_time_ms", "symbol"], kind="mergesort"
    )
    output = root / "stage2_high_resolution_features_is.parquet"
    frame.to_parquet(output, index=False, compression="zstd")
    covered = frame["aggtrades_primary_complete"].astype(bool)
    joined = profiles[["event_id", "symbol", "session_name", "impulse_direction"]].merge(
        frame[["event_id", "symbol", "aggtrades_primary_complete"]],
        on=["event_id", "symbol"],
        how="left",
        validate="one_to_one",
    )
    month = pd.to_datetime(frame["snapshot_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    audit = {
        "audit_version": "stage2_high_resolution_audit_v1",
        "status": "PASS",
        "profile_row_count": len(profiles),
        "request_row_count": len(manifest),
        "feature_row_count": len(frame),
        "request_key_unique": not bool(manifest.duplicated(["event_id", "symbol"]).any()),
        "feature_key_unique": not bool(frame.duplicated(["event_id", "symbol"]).any()),
        "full_15m_coverage_rows": int(covered.sum()),
        "full_15m_coverage_share": float(covered.mean()),
        "partial_15m_coverage_rows": int(
            frame["aggtrades_15m_coverage"].between(0.0, 1.0, inclusive="neither").sum()
        ),
        "zero_15m_coverage_rows": int(frame["aggtrades_15m_coverage"].eq(0.0).sum()),
        "covered_by_session": joined.groupby("session_name")["aggtrades_primary_complete"].agg(["count", "sum", "mean"]).to_dict(orient="index"),
        "covered_by_impulse_direction": joined.groupby("impulse_direction")["aggtrades_primary_complete"].agg(["count", "sum", "mean"]).to_dict(orient="index"),
        "covered_by_month": pd.DataFrame({"month": month, "covered": covered}).groupby("month")["covered"].agg(["count", "sum", "mean"]).to_dict(orient="index"),
        "oos_profile_rows": int((profiles["snapshot_time_ms"] >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms).sum()),
        "oos_feature_rows": int((frame["snapshot_time_ms"] >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms).sum()),
        "maximum_feature_cutoff_minus_snapshot_ms": int((frame["feature_cutoff_time_ms"] - frame["snapshot_time_ms"]).max()),
        "maximum_request_end_minus_snapshot_ms": int((manifest["requested_end_time_ms_exclusive"] - manifest["selection_snapshot_time_ms"]).max()),
    }
    invariants = (
        len(profiles) == len(manifest) == len(frame)
        and audit["request_key_unique"]
        and audit["feature_key_unique"]
        and audit["oos_profile_rows"] == 0
        and audit["oos_feature_rows"] == 0
        and audit["maximum_feature_cutoff_minus_snapshot_ms"] <= 0
        and audit["maximum_request_end_minus_snapshot_ms"] <= 0
    )
    audit["status"] = "PASS" if invariants else "FAIL"
    (root / "stage2_high_resolution_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8"
    )
    if not invariants:
        raise ValueError("stage2 high-resolution audit failed")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build event-scoped aggTrades features.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_AGGTRADES_DIR)
    args = parser.parse_args()
    print(build_stage2_high_resolution_features(stage1_dir=args.stage1_dir, source_dir=args.source))


__all__ = [
    "build_enrichment_request_manifest",
    "build_stage2_high_resolution_features",
    "compute_symbol_aggtrades_features",
]


if __name__ == "__main__":
    main()
