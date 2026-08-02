"""Scalable, exact IS build of leave-one-out residual response profiles."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd
import polars as pl

from anomaly_science.strategy.residual_absorption.market_impulse import MarketImpulseEvent
from anomaly_science.strategy.residual_absorption.residual_response import (
    ResidualResponseProfileRow,
)
from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
    ResidualResponseSpec,
)

FACTOR_ORDER_STATS_SCHEMA_VERSION = "leave_one_out_factor_order_stats_v1"
STAGE1_RESPONSE_BUILD_VERSION = "residual_response_profile_build_v1"


class Stage1ResponseBuildError(ValueError):
    """Raised when scalable response construction violates its registered contract."""


@dataclass(frozen=True, slots=True)
class Stage1ResponseBuildResult:
    profile_path: Path
    factor_order_stats_path: Path
    audit_path: Path
    profile_count: int


def central_factor_order_statistics(values: Iterable[float]) -> dict[str, float | int]:
    """Return the central order statistics sufficient for an exact LOO median."""

    ordered = np.sort(np.asarray(tuple(values), dtype=float))
    ordered = ordered[np.isfinite(ordered)]
    count = len(ordered)
    if count < 3:
        raise Stage1ResponseBuildError("exact leave-one-out factor requires at least three values")
    if count % 2:
        middle = count // 2
        lower = ordered[middle - 1]
        center = ordered[middle]
        upper = ordered[middle + 1]
    else:
        middle = count // 2
        lower = ordered[middle - 1]
        upper = ordered[middle]
        center = (lower + upper) / 2.0
    return {
        "factor_symbol_count": count,
        "factor_lower_center_15m": float(lower),
        "factor_median_15m": float(center),
        "factor_upper_center_15m": float(upper),
    }


def exact_leave_one_out_median(
    *,
    symbol_return: float,
    symbol_is_factor_member: bool,
    factor_symbol_count: int,
    lower_center: float,
    factor_median: float,
    upper_center: float,
) -> float:
    """Remove the symbol from the factor median exactly, including even samples."""

    if not symbol_is_factor_member:
        return factor_median
    if factor_symbol_count < 3 or not all(
        math.isfinite(value)
        for value in (symbol_return, lower_center, factor_median, upper_center)
    ):
        return float("nan")
    if factor_symbol_count % 2:
        if symbol_return < factor_median:
            return float((factor_median + upper_center) / 2.0)
        if symbol_return > factor_median:
            return float((lower_center + factor_median) / 2.0)
        return float((lower_center + upper_center) / 2.0)
    return float(upper_center if symbol_return <= lower_center else lower_center)


def build_factor_order_statistics(
    cross_section_path: str | Path,
    *,
    output_path: str | Path,
    reference_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
) -> Path:
    """Scan the cross-section once and persist sufficient LOO median statistics."""

    target = Path(output_path)
    if target.is_file():
        return target
    grouped = (
        pl.scan_parquet(cross_section_path)
        .filter(
            pl.col("core_eligible")
            & ~pl.col("symbol").is_in(list(reference_symbols))
            & pl.col("return_15m").is_finite()
        )
        .group_by("snapshot_time_ms")
        .agg(pl.col("return_15m").sort().alias("factor_returns_15m"))
        .sort("snapshot_time_ms")
        .collect(engine="streaming")
    )
    rows: list[dict[str, object]] = []
    for snapshot, values in grouped.iter_rows():
        stats = central_factor_order_statistics(values)
        rows.append(
            {
                "schema_version": FACTOR_ORDER_STATS_SCHEMA_VERSION,
                "snapshot_time_ms": int(snapshot),
                **stats,
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(target, index=False, compression="zstd")
    return target


def partition_response_symbol_inputs(
    cross_section_path: str | Path,
    *,
    output_dir: str | Path,
) -> Path:
    """Write only registered response inputs, physically partitioned by symbol."""

    target = Path(output_dir)
    completion = target / "_SUCCESS.json"
    if completion.is_file():
        return target
    if target.exists():
        raise Stage1ResponseBuildError(
            f"incomplete response partition exists; inspect before removal: {target}"
        )
    columns = (
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "utc_day",
        "session_seq",
        "return_15m",
        "core_eligible",
        "candidate_eligible",
    )
    target.mkdir(parents=True)
    pl.scan_parquet(cross_section_path).select(columns).sink_parquet(
        pl.PartitionBy(target, key="symbol", include_key=True),
        compression="zstd",
        engine="streaming",
    )
    files = tuple(target.rglob("*.parquet"))
    if not files:
        raise Stage1ResponseBuildError("symbol response partition produced no files")
    completion.write_text(
        json.dumps({"partition_count": len(files), "columns": columns}, indent=2),
        encoding="utf-8",
    )
    return target


def build_symbol_residual_profiles(
    symbol_frame: pd.DataFrame,
    *,
    factor_order_stats: pd.DataFrame,
    events: list[MarketImpulseEvent],
    spec: ResidualResponseSpec = ResidualResponseSpec(),
) -> list[ResidualResponseProfileRow]:
    """Build one symbol's profiles using exact LOO factors and prior day statistics."""

    if symbol_frame.empty or not events:
        return []
    required = {
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "utc_day",
        "session_seq",
        "return_15m",
        "core_eligible",
        "candidate_eligible",
    }
    missing = sorted(required.difference(symbol_frame.columns))
    if missing:
        raise Stage1ResponseBuildError(f"symbol response inputs missing columns: {missing}")
    symbols = symbol_frame["symbol"].astype(str).unique()
    if len(symbols) != 1:
        raise Stage1ResponseBuildError("a symbol response partition must contain one symbol")
    symbol = str(symbols[0])
    if symbol in spec.reference_symbols:
        return []
    frame = symbol_frame.merge(
        factor_order_stats,
        on="snapshot_time_ms",
        how="left",
        validate="many_to_one",
    ).sort_values("snapshot_time_ms", kind="mergesort")
    if bool(frame["snapshot_time_ms"].duplicated().any()):
        raise Stage1ResponseBuildError(f"duplicate snapshots in response partition: {symbol}")
    if bool((frame["feature_cutoff_time_ms"] > frame["snapshot_time_ms"]).any()):
        raise Stage1ResponseBuildError(f"feature cutoff exceeds snapshot for {symbol}")
    frame["leave_one_out_factor_return"] = _exact_loo_factor_array(frame)
    daily = _daily_sufficient_statistics(frame)
    event_by_time = {event.snapshot_time_ms: event for event in events}
    current = frame.loc[
        frame["candidate_eligible"].astype(bool)
        & frame["snapshot_time_ms"].isin(event_by_time)
    ]
    output: list[ResidualResponseProfileRow] = []
    for raw in current.itertuples(index=False):
        event = event_by_time[int(raw.snapshot_time_ms)]
        long_fit = _prior_window_fit(
            daily,
            session_seq=event.session_seq,
            utc_day=event.utc_day,
            limit=spec.beta_history_same_type_sessions,
            minimum=spec.minimum_beta_observations,
        )
        short_fit = _prior_window_fit(
            daily,
            session_seq=event.session_seq,
            utc_day=event.utc_day,
            limit=spec.beta_short_history_sessions,
            minimum=spec.minimum_short_beta_observations,
        )
        beta, alpha, correlation, count = long_fit
        short_beta, _, _, short_count = short_fit
        current_factor = float(raw.leave_one_out_factor_return)
        current_return = float(raw.return_15m)
        if not (math.isfinite(current_factor) and math.isfinite(current_return)):
            continue
        expected = None if beta is None or alpha is None else alpha + beta * current_factor
        residual = None if expected is None else current_return - expected
        adjusted = None if residual is None else event.direction * residual
        output.append(
            ResidualResponseProfileRow(
                schema_version=spec.schema_version,
                event_id=event.event_id,
                symbol=symbol,
                snapshot_time_ms=event.snapshot_time_ms,
                feature_cutoff_time_ms=min(
                    event.snapshot_time_ms,
                    max(event.feature_cutoff_time_ms, int(raw.feature_cutoff_time_ms)),
                ),
                session_seq=event.session_seq,
                session_name=event.session_name,
                impulse_direction=event.direction,
                history_observation_count=count,
                short_history_observation_count=short_count,
                beta_15m=beta,
                beta_short_15m=short_beta,
                beta_recent_minus_long=(
                    None if beta is None or short_beta is None else short_beta - beta
                ),
                alpha_15m=alpha,
                factor_correlation_15m=correlation,
                factor_r_squared_15m=None if correlation is None else correlation**2,
                current_symbol_return_15m=current_return,
                current_leave_one_out_factor_return_15m=current_factor,
                expected_symbol_return_15m=expected,
                response_residual_15m=residual,
                direction_adjusted_residual_15m=adjusted,
                direction_adjusted_underreaction_15m=None if adjusted is None else -adjusted,
                factor_excludes_symbol=True,
            )
        )
    return output


def build_stage1_response_profiles(
    *,
    stage1_dir: str | Path,
    spec: ResidualResponseSpec = ResidualResponseSpec(),
    progress: Callable[[str], None] | None = print,
) -> Stage1ResponseBuildResult:
    """Build and audit the complete IS residual-profile atlas."""

    root = Path(stage1_dir)
    cross_path = root / "cross_section_5m_is.parquet"
    event_path = root / "market_impulse_events_is.parquet"
    for path in (cross_path, event_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    stats_path = build_factor_order_statistics(
        cross_path,
        output_path=root / "factor_order_statistics_is.parquet",
        reference_symbols=spec.reference_symbols,
    )
    partition_dir = partition_response_symbol_inputs(
        cross_path,
        output_dir=root / "response_symbol_inputs",
    )
    factor_stats = pd.read_parquet(stats_path)
    events = _read_events(event_path)
    partitions = sorted(partition_dir.rglob("*.parquet"))
    profiles: list[ResidualResponseProfileRow] = []
    observed_symbols: set[str] = set()
    for completed, path in enumerate(partitions, start=1):
        symbol_frame = pd.read_parquet(path)
        partition_symbols = set(symbol_frame["symbol"].astype(str))
        overlap = observed_symbols.intersection(partition_symbols)
        if overlap:
            raise Stage1ResponseBuildError(f"symbol split across partitions: {sorted(overlap)}")
        observed_symbols.update(partition_symbols)
        profiles.extend(
            build_symbol_residual_profiles(
                symbol_frame,
                factor_order_stats=factor_stats,
                events=events,
                spec=spec,
            )
        )
        if progress and (completed % 50 == 0 or completed == len(partitions)):
            progress(f"stage1 residual profiles {completed}/{len(partitions)}")
    profiles.sort(key=lambda item: (item.snapshot_time_ms, item.symbol))
    profile_path = root / "residual_response_profiles_is.parquet"
    profile_frame = pd.DataFrame([asdict(row) for row in profiles])
    profile_frame.to_parquet(profile_path, index=False, compression="zstd")
    audit_path = _write_response_audit(
        root=root,
        profiles=profiles,
        event_count=len(events),
        partition_count=len(partitions),
    )
    return Stage1ResponseBuildResult(
        profile_path=profile_path,
        factor_order_stats_path=stats_path,
        audit_path=audit_path,
        profile_count=len(profiles),
    )


def _daily_sufficient_statistics(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame.loc[
        np.isfinite(frame["leave_one_out_factor_return"].to_numpy(dtype=float))
        & np.isfinite(frame["return_15m"].to_numpy(dtype=float))
    ].copy()
    valid["x2"] = valid["leave_one_out_factor_return"] ** 2
    valid["y2"] = valid["return_15m"] ** 2
    valid["xy"] = valid["leave_one_out_factor_return"] * valid["return_15m"]
    return (
        valid.groupby(["session_seq", "utc_day"], as_index=False)
        .agg(
            count=("return_15m", "size"),
            sum_x=("leave_one_out_factor_return", "sum"),
            sum_y=("return_15m", "sum"),
            sum_x2=("x2", "sum"),
            sum_y2=("y2", "sum"),
            sum_xy=("xy", "sum"),
        )
        .sort_values(["session_seq", "utc_day"])
    )


def _exact_loo_factor_array(frame: pd.DataFrame) -> np.ndarray:
    returns = frame["return_15m"].to_numpy(dtype=float)
    counts = frame["factor_symbol_count"].to_numpy(dtype=float)
    lower = frame["factor_lower_center_15m"].to_numpy(dtype=float)
    median = frame["factor_median_15m"].to_numpy(dtype=float)
    upper = frame["factor_upper_center_15m"].to_numpy(dtype=float)
    members = frame["core_eligible"].to_numpy(dtype=bool)
    output = median.copy()
    valid = (
        np.isfinite(returns)
        & np.isfinite(counts)
        & np.isfinite(lower)
        & np.isfinite(median)
        & np.isfinite(upper)
        & (counts >= 3)
    )
    output[~valid] = np.nan
    odd = valid & members & np.equal(np.remainder(counts, 2.0), 1.0)
    even = valid & members & ~odd
    below = odd & (returns < median)
    above = odd & (returns > median)
    equal = odd & ~(below | above)
    output[below] = (median[below] + upper[below]) / 2.0
    output[above] = (lower[above] + median[above]) / 2.0
    output[equal] = (lower[equal] + upper[equal]) / 2.0
    lower_half = even & (returns <= lower)
    upper_half = even & ~lower_half
    output[lower_half] = upper[lower_half]
    output[upper_half] = lower[upper_half]
    return output


def _prior_window_fit(
    daily: pd.DataFrame,
    *,
    session_seq: int,
    utc_day: int,
    limit: int,
    minimum: int,
) -> tuple[float | None, float | None, float | None, int]:
    eligible = daily.loc[
        daily["session_seq"].eq(session_seq) & daily["utc_day"].lt(utc_day)
    ].tail(limit)
    if eligible.empty:
        return None, None, None, 0
    totals = eligible[["count", "sum_x", "sum_y", "sum_x2", "sum_y2", "sum_xy"]].sum()
    count = int(totals["count"])
    if count < minimum:
        return None, None, None, count
    sum_x = float(totals["sum_x"])
    sum_y = float(totals["sum_y"])
    centered_x2 = float(totals["sum_x2"]) - sum_x**2 / count
    centered_y2 = float(totals["sum_y2"]) - sum_y**2 / count
    covariance = float(totals["sum_xy"]) - sum_x * sum_y / count
    if centered_x2 <= 0.0 or centered_y2 <= 0.0:
        return None, None, None, count
    beta = covariance / centered_x2
    alpha = (sum_y - beta * sum_x) / count
    correlation = covariance / math.sqrt(centered_x2 * centered_y2)
    return float(beta), float(alpha), float(np.clip(correlation, -1.0, 1.0)), count


def _read_events(path: Path) -> list[MarketImpulseEvent]:
    events: list[MarketImpulseEvent] = []
    for raw in pd.read_parquet(path).itertuples(index=False):
        events.append(
            MarketImpulseEvent(
                event_id=str(raw.event_id),
                schema_version=str(raw.schema_version),
                snapshot_time_ms=int(raw.snapshot_time_ms),
                feature_cutoff_time_ms=int(raw.feature_cutoff_time_ms),
                utc_day=int(raw.utc_day),
                session_seq=int(raw.session_seq),
                session_name=str(raw.session_name),
                direction=int(raw.direction),
                factor_return_15m=float(raw.factor_return_15m),
                factor_robust_z_15m=float(raw.factor_robust_z_15m),
                directional_breadth_15m=float(raw.directional_breadth_15m),
                cross_section_symbol_count=int(raw.cross_section_symbol_count),
                reference_support_count=int(raw.reference_support_count),
                reference_confirmed=bool(raw.reference_confirmed),
            )
        )
    return events


def _write_response_audit(
    *,
    root: Path,
    profiles: list[ResidualResponseProfileRow],
    event_count: int,
    partition_count: int,
) -> Path:
    fitted = [row for row in profiles if row.beta_15m is not None]
    checks = {
        "profiles_are_is_only": all(
            row.snapshot_time_ms < RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms
            for row in profiles
        ),
        "feature_cutoff_not_after_snapshot": all(
            row.feature_cutoff_time_ms <= row.snapshot_time_ms for row in profiles
        ),
        "factor_is_leave_one_out": all(row.factor_excludes_symbol for row in profiles),
        "registered_references_excluded": all(
            row.symbol not in {"BTCUSDT", "ETHUSDT"} for row in profiles
        ),
        "profile_keys_unique": len({(row.event_id, row.symbol) for row in profiles})
        == len(profiles),
    }
    audit = {
        "audit_version": "residual_response_profile_audit_v1",
        "build_version": STAGE1_RESPONSE_BUILD_VERSION,
        "status": "PASS" if profiles and all(checks.values()) else "FAIL",
        "checks": checks,
        "event_count": event_count,
        "partition_count": partition_count,
        "profile_count": len(profiles),
        "fitted_profile_count": len(fitted),
        "unfitted_profile_count": len(profiles) - len(fitted),
        "minimum_history_observations": min(
            (row.history_observation_count for row in profiles), default=None
        ),
        "maximum_history_observations": max(
            (row.history_observation_count for row in profiles), default=None
        ),
        "note": "Outcome fields are absent; this artifact contains event-time features only.",
    }
    path = root / "residual_response_profile_audit.json"
    path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    if audit["status"] != "PASS":
        raise Stage1ResponseBuildError(f"residual profile audit failed: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the IS residual response profile atlas.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(build_stage1_response_profiles(stage1_dir=args.stage1_dir))


__all__ = [
    "FACTOR_ORDER_STATS_SCHEMA_VERSION",
    "STAGE1_RESPONSE_BUILD_VERSION",
    "Stage1ResponseBuildError",
    "Stage1ResponseBuildResult",
    "build_factor_order_statistics",
    "build_stage1_response_profiles",
    "build_symbol_residual_profiles",
    "central_factor_order_statistics",
    "exact_leave_one_out_median",
    "partition_response_symbol_inputs",
]


if __name__ == "__main__":
    main()
