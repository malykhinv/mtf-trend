"""Exact IS future residual paths for the pre-registered stage-1 test."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import polars as pl

from anomaly_science.strategy.residual_absorption.response_memory import ResponseMemoryRecord
from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
    ResponseOutcomeSpec,
)
from anomaly_science.strategy.residual_absorption.stage1_response_build import (
    central_factor_order_statistics,
    exact_leave_one_out_median,
)

STAGE1_OUTCOME_BUILD_VERSION = "residual_response_outcome_build_v1"


class Stage1OutcomeBuildError(ValueError):
    """Raised when outcome construction cannot preserve event-frozen inputs."""


def build_event_core_memberships(
    cross_section_path: str | Path,
    *,
    event_times: np.ndarray,
    output_path: str | Path,
    reference_symbols: tuple[str, ...],
) -> Path:
    target = Path(output_path)
    if target.is_file():
        return target
    memberships = (
        pl.scan_parquet(cross_section_path)
        .filter(
            pl.col("snapshot_time_ms").is_in(event_times.tolist())
            & pl.col("core_eligible")
            & ~pl.col("symbol").is_in(list(reference_symbols))
        )
        .select("snapshot_time_ms", "symbol")
        .sort("snapshot_time_ms", "symbol")
        .collect(engine="streaming")
    )
    if memberships.is_empty():
        raise Stage1OutcomeBuildError("no event-time core memberships were found")
    memberships.write_parquet(target, compression="zstd")
    return target


def build_event_factor_path_statistics(
    *,
    memberships_path: str | Path,
    symbol_price_dir: str | Path,
    output_dir: str | Path,
    spec: ResponseOutcomeSpec = ResponseOutcomeSpec(),
    progress: Callable[[str], None] | None = print,
) -> tuple[Path, Path]:
    root = Path(output_dir)
    contribution_path = root / "event_core_path_contributions_is.parquet"
    stats_path = root / "event_factor_path_order_statistics_is.parquet"
    if contribution_path.is_file() and stats_path.is_file():
        return contribution_path, stats_path
    memberships = pd.read_parquet(memberships_path)
    offsets = np.arange(
        spec.path_interval_minutes,
        max(spec.horizons_minutes) + spec.path_interval_minutes,
        spec.path_interval_minutes,
        dtype=np.int64,
    ) * 60_000
    contributions: list[pd.DataFrame] = []
    groups = tuple(memberships.groupby("symbol", sort=True))
    for completed, (symbol, group) in enumerate(groups, start=1):
        price_path = Path(symbol_price_dir) / f"{symbol}.parquet"
        if not price_path.is_file():
            continue
        prices = pd.read_parquet(price_path, columns=["snapshot_time_ms", "close"])
        series = prices.set_index("snapshot_time_ms")["close"]
        event_times = group["snapshot_time_ms"].to_numpy(dtype=np.int64)
        event_prices = series.reindex(event_times).to_numpy(dtype=float)
        path_times = event_times[:, None] + offsets[None, :]
        future_prices = series.reindex(path_times.ravel()).to_numpy(dtype=float).reshape(path_times.shape)
        returns = future_prices / event_prices[:, None] - 1.0
        valid = np.isfinite(returns)
        event_grid = np.broadcast_to(event_times[:, None], path_times.shape)
        contributions.append(
            pd.DataFrame(
                {
                    "event_snapshot_time_ms": event_grid[valid],
                    "path_time_ms": path_times[valid],
                    "symbol": str(symbol),
                    "factor_member_return": returns[valid],
                }
            )
        )
        if progress and (completed % 50 == 0 or completed == len(groups)):
            progress(f"stage1 factor paths {completed}/{len(groups)}")
    if not contributions:
        raise Stage1OutcomeBuildError("event factor path build produced no contributions")
    contribution_frame = pd.concat(contributions, ignore_index=True)
    contribution_frame.to_parquet(contribution_path, index=False, compression="zstd")
    grouped = (
        pl.scan_parquet(contribution_path)
        .group_by("event_snapshot_time_ms", "path_time_ms")
        .agg(pl.col("factor_member_return").sort().alias("factor_returns"))
        .sort("event_snapshot_time_ms", "path_time_ms")
        .collect(engine="streaming")
    )
    rows: list[dict[str, object]] = []
    for event_time, path_time, values in grouped.iter_rows():
        if len(values) < spec.minimum_factor_symbols:
            continue
        rows.append(
            {
                "event_snapshot_time_ms": int(event_time),
                "path_time_ms": int(path_time),
                **central_factor_order_statistics(values),
            }
        )
    pd.DataFrame(rows).to_parquet(stats_path, index=False, compression="zstd")
    return contribution_path, stats_path


def build_symbol_outcome_records(
    profile_frame: pd.DataFrame,
    *,
    prices: pd.Series,
    factor_path_stats: dict[int, pd.DataFrame],
    core_event_times: set[int],
    spec: ResponseOutcomeSpec = ResponseOutcomeSpec(),
) -> list[ResponseMemoryRecord]:
    """Resolve one symbol with event-time membership and frozen profile beta."""

    offsets_minutes = np.arange(
        spec.path_interval_minutes,
        max(spec.horizons_minutes) + spec.path_interval_minutes,
        spec.path_interval_minutes,
        dtype=np.int64,
    )
    records: list[ResponseMemoryRecord] = []
    for raw in profile_frame.sort_values("snapshot_time_ms").itertuples(index=False):
        event_time = int(raw.snapshot_time_ms)
        resolution_time = event_time + max(spec.horizons_minutes) * 60_000
        initial = _optional_float(raw.direction_adjusted_underreaction_15m)
        beta = _optional_float(raw.beta_15m)
        if (
            beta is None
            or initial is None
            or resolution_time >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms
            or event_time not in prices.index
            or event_time not in factor_path_stats
        ):
            records.append(_unresolved(raw, initial))
            continue
        path_times = event_time + offsets_minutes * 60_000
        future_prices = prices.reindex(path_times).to_numpy(dtype=float)
        event_price = float(prices.loc[event_time])
        stats = factor_path_stats[event_time].reindex(path_times)
        if (
            not np.all(np.isfinite(future_prices))
            or stats.isna().any(axis=None)
            or event_price <= 0.0
        ):
            records.append(_unresolved(raw, initial))
            continue
        symbol_returns = future_prices / event_price - 1.0
        is_member = event_time in core_event_times
        factor_returns = np.asarray(
            [
                exact_leave_one_out_median(
                    symbol_return=float(symbol_return),
                    symbol_is_factor_member=is_member,
                    factor_symbol_count=int(stat.factor_symbol_count),
                    lower_center=float(stat.factor_lower_center_15m),
                    factor_median=float(stat.factor_median_15m),
                    upper_center=float(stat.factor_upper_center_15m),
                )
                for symbol_return, stat in zip(
                    symbol_returns,
                    stats.itertuples(index=False),
                    strict=True,
                )
            ],
            dtype=float,
        )
        catchup = int(raw.impulse_direction) * (symbol_returns - beta * factor_returns)
        terminal = -initial + catchup
        horizon_values = {
            horizon: float(catchup[horizon // spec.path_interval_minutes - 1])
            for horizon in spec.horizons_minutes
        }
        half_times = offsets_minutes[(initial > 0.0) & (catchup >= 0.5 * initial)]
        zero_crossed = bool(
            np.any(terminal >= 0.0) if -initial < 0.0 else np.any(terminal <= 0.0)
        )
        records.append(
            ResponseMemoryRecord(
                event_id=str(raw.event_id),
                symbol=str(raw.symbol),
                event_snapshot_time_ms=event_time,
                impulse_direction=int(raw.impulse_direction),
                session_seq=int(raw.session_seq),
                initial_direction_adjusted_underreaction_15m=initial,
                resolution_time_ms=resolution_time,
                catchup_residual_change_15m=horizon_values[15],
                catchup_residual_change_30m=horizon_values[30],
                catchup_residual_change_60m=horizon_values[60],
                catchup_residual_change_120m=horizon_values[120],
                terminal_direction_adjusted_residual_120m=float(terminal[-1]),
                maximum_catchup_residual_change_120m=float(np.max(catchup)),
                zero_crossed_expected_response_120m=zero_crossed,
                time_to_half_catchup_minutes=(
                    float(half_times[0]) if len(half_times) else None
                ),
            )
        )
    return records


def build_stage1_outcomes(
    *,
    stage1_dir: str | Path,
    spec: ResponseOutcomeSpec = ResponseOutcomeSpec(),
    progress: Callable[[str], None] | None = print,
) -> Path:
    root = Path(stage1_dir)
    cross_path = root / "cross_section_5m_is.parquet"
    event_path = root / "market_impulse_events_is.parquet"
    profile_path = root / "residual_response_profiles_is.parquet"
    price_dir = root / "symbol_5m"
    for path in (cross_path, event_path, profile_path, price_dir):
        if not path.exists():
            raise FileNotFoundError(path)
    events = pd.read_parquet(event_path)
    memberships_path = build_event_core_memberships(
        cross_path,
        event_times=events["snapshot_time_ms"].to_numpy(dtype=np.int64),
        output_path=root / "event_core_memberships_is.parquet",
        reference_symbols=spec.reference_symbols,
    )
    _, stats_path = build_event_factor_path_statistics(
        memberships_path=memberships_path,
        symbol_price_dir=price_dir,
        output_dir=root,
        spec=spec,
        progress=progress,
    )
    memberships = pd.read_parquet(memberships_path)
    core_by_symbol = {
        str(symbol): set(group["snapshot_time_ms"].astype("int64"))
        for symbol, group in memberships.groupby("symbol")
    }
    factor_frame = pd.read_parquet(stats_path)
    factor_by_event = {
        int(event_time): group.set_index("path_time_ms").drop(
            columns="event_snapshot_time_ms"
        )
        for event_time, group in factor_frame.groupby("event_snapshot_time_ms")
    }
    profiles = pd.read_parquet(profile_path)
    records: list[ResponseMemoryRecord] = []
    groups = tuple(profiles.groupby("symbol", sort=True))
    for completed, (symbol, group) in enumerate(groups, start=1):
        price_path = price_dir / f"{symbol}.parquet"
        if not price_path.is_file():
            records.extend(_unresolved(raw, _optional_float(raw.direction_adjusted_underreaction_15m)) for raw in group.itertuples(index=False))
            continue
        price_frame = pd.read_parquet(price_path, columns=["snapshot_time_ms", "close"])
        records.extend(
            build_symbol_outcome_records(
                group,
                prices=price_frame.set_index("snapshot_time_ms")["close"],
                factor_path_stats=factor_by_event,
                core_event_times=core_by_symbol.get(str(symbol), set()),
                spec=spec,
            )
        )
        if progress and (completed % 50 == 0 or completed == len(groups)):
            progress(f"stage1 response outcomes {completed}/{len(groups)}")
    records.sort(key=lambda item: (item.event_snapshot_time_ms, item.symbol))
    output_path = root / "response_outcomes_is.parquet"
    pd.DataFrame([asdict(row) for row in records]).to_parquet(
        output_path, index=False, compression="zstd"
    )
    _write_outcome_audit(root, records, len(profiles))
    return output_path


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def _unresolved(raw: object, initial: float | None) -> ResponseMemoryRecord:
    return ResponseMemoryRecord(
        event_id=str(raw.event_id),
        symbol=str(raw.symbol),
        event_snapshot_time_ms=int(raw.snapshot_time_ms),
        impulse_direction=int(raw.impulse_direction),
        session_seq=int(raw.session_seq),
        initial_direction_adjusted_underreaction_15m=initial,
        resolution_time_ms=None,
        catchup_residual_change_15m=None,
        catchup_residual_change_30m=None,
        catchup_residual_change_60m=None,
        catchup_residual_change_120m=None,
        terminal_direction_adjusted_residual_120m=None,
        maximum_catchup_residual_change_120m=None,
        zero_crossed_expected_response_120m=None,
        time_to_half_catchup_minutes=None,
    )


def _write_outcome_audit(root: Path, records: list[ResponseMemoryRecord], expected: int) -> Path:
    resolved = [row for row in records if row.resolution_time_ms is not None]
    checks = {
        "one_record_per_profile": len(records) == expected,
        "record_keys_unique": len({(row.event_id, row.symbol) for row in records}) == len(records),
        "future_starts_after_snapshot": all(
            row.resolution_time_ms is None or row.resolution_time_ms > row.event_snapshot_time_ms
            for row in records
        ),
        "resolved_labels_end_inside_is": all(
            row.resolution_time_ms is None
            or row.resolution_time_ms < RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms
            for row in records
        ),
    }
    audit = {
        "audit_version": "residual_response_outcome_audit_v1",
        "build_version": STAGE1_OUTCOME_BUILD_VERSION,
        "status": "PASS" if records and all(checks.values()) else "FAIL",
        "checks": checks,
        "record_count": len(records),
        "resolved_count": len(resolved),
        "unresolved_count": len(records) - len(resolved),
        "note": "Outcome artifacts are labels and are not imported by online feature code.",
    }
    path = root / "response_outcome_audit.json"
    path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    if audit["status"] != "PASS":
        raise Stage1OutcomeBuildError(f"outcome audit failed: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact stage-1 IS response outcomes.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(build_stage1_outcomes(stage1_dir=args.stage1_dir))


__all__ = [
    "STAGE1_OUTCOME_BUILD_VERSION",
    "Stage1OutcomeBuildError",
    "build_event_core_memberships",
    "build_event_factor_path_statistics",
    "build_stage1_outcomes",
    "build_symbol_outcome_records",
]


if __name__ == "__main__":
    main()
