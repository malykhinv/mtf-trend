"""Scalable IS-only 1m -> 5m/session/cross-section build for stage 1."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import polars as pl

from anomaly_science.market_context.sessions import (
    MS_PER_DAY,
    MS_PER_MINUTE,
    UTC_SESSION_BY_SEQ,
    block_seq_for_ms,
    utc_day_for_ms,
)
from anomaly_science.strategy.residual_absorption.data import read_is_symbol_minutes
from anomaly_science.strategy.residual_absorption.market_impulse import (
    detect_market_impulse_events,
    finalize_market_factor_aggregates,
)
from anomaly_science.strategy.residual_absorption.spec import ResidualAbsorptionResearchSpec

STAGE1_SYMBOL_5M_SCHEMA_VERSION = "residual_absorption_symbol_5m_is_v1"
STAGE1_CROSS_SECTION_SCHEMA_VERSION = "residual_absorption_cross_section_is_v1"
FIVE_MINUTES_MS = 5 * MS_PER_MINUTE


class Stage1BuildError(ValueError):
    """Raised when the physical IS stage-1 build cannot satisfy its contract."""


@dataclass(frozen=True, slots=True)
class Stage1IsBuildConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    output_dir: Path = Path(".output/research/residual_absorption/stage1_is")
    quote_suffix: str = "USDT"
    workers: int = 4
    max_symbols: int | None = None
    overwrite: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 8:
            raise Stage1BuildError("workers must be between 1 and 8")
        if self.max_symbols is not None and self.max_symbols <= 0:
            raise Stage1BuildError("max_symbols must be positive when provided")
        if not self.quote_suffix:
            raise Stage1BuildError("quote_suffix is required")


@dataclass(frozen=True, slots=True)
class Symbol5mBuildStats:
    symbol: str
    minute_rows: int
    five_minute_rows: int
    first_snapshot_time_ms: int | None
    last_snapshot_time_ms: int | None
    output_path: str
    status: str


@dataclass(frozen=True, slots=True)
class Stage1IsBuildResult:
    symbol_stats: tuple[Symbol5mBuildStats, ...]
    cross_section_path: Path
    factor_aggregates_path: Path
    manifest_path: Path


def build_symbol_5m_is(
    source_path: str | Path,
    *,
    output_path: str | Path,
    spec: ResidualAbsorptionResearchSpec = ResidualAbsorptionResearchSpec(),
    overwrite: bool = False,
) -> Symbol5mBuildStats:
    """Aggregate one symbol using a physical Parquet IS predicate before load."""

    source = Path(source_path)
    target = Path(output_path)
    symbol = source.stem.upper()
    if target.exists() and not overwrite:
        existing = pd.read_parquet(
            target,
            columns=["snapshot_time_ms"],
        )
        return Symbol5mBuildStats(
            symbol=symbol,
            minute_rows=0,
            five_minute_rows=len(existing),
            first_snapshot_time_ms=(
                None if existing.empty else int(existing["snapshot_time_ms"].min())
            ),
            last_snapshot_time_ms=(
                None if existing.empty else int(existing["snapshot_time_ms"].max())
            ),
            output_path=str(target),
            status="reused",
        )
    columns = ("close", "quote_volume")
    minute = read_is_symbol_minutes(source, columns=columns)
    if minute.empty:
        return Symbol5mBuildStats(symbol, 0, 0, None, None, str(target), "empty")
    minute["bucket_open_time_ms"] = (
        minute["timestamp"].astype("int64") // FIVE_MINUTES_MS
    ) * FIVE_MINUTES_MS
    five = (
        minute.groupby("bucket_open_time_ms", sort=True)
        .agg(
            close=("close", "last"),
            quote_volume_5m=("quote_volume", "sum"),
            observed_minutes=("timestamp", "nunique"),
        )
        .reset_index()
    )
    five = five.loc[five["observed_minutes"].eq(5)].copy()
    if five.empty:
        return Symbol5mBuildStats(symbol, len(minute), 0, None, None, str(target), "empty")
    five["snapshot_time_ms"] = five["bucket_open_time_ms"] + FIVE_MINUTES_MS
    five["feature_cutoff_time_ms"] = five["snapshot_time_ms"]
    close_by_snapshot = pd.Series(
        five["close"].to_numpy(dtype=float),
        index=five["snapshot_time_ms"].to_numpy(dtype=np.int64),
    )
    for window in spec.market_impulse.factor_return_windows_minutes:
        prior = five["snapshot_time_ms"].sub(window * MS_PER_MINUTE).map(close_by_snapshot)
        five[f"return_{window}m"] = five["close"] / prior - 1.0

    bucket_time = five["bucket_open_time_ms"].to_numpy(dtype=np.int64)
    five["utc_day"] = utc_day_for_ms(bucket_time)
    five["session_seq"] = block_seq_for_ms(bucket_time)
    start_hour = five["session_seq"].map(
        {seq: block.start_hour for seq, block in UTC_SESSION_BY_SEQ.items()}
    )
    five["session_start_time_ms"] = (
        five["utc_day"] * MS_PER_DAY + start_hour * 60 * MS_PER_MINUTE
    )
    five["session_elapsed_5m_bars"] = (
        (five["bucket_open_time_ms"] - five["session_start_time_ms"]) // FIVE_MINUTES_MS + 1
    ).astype("int16")
    session_keys = ["utc_day", "session_seq"]
    grouped = five.groupby(session_keys, sort=True)
    five["current_session_quote_volume"] = grouped["quote_volume_5m"].cumsum()
    five["current_session_observed_5m_bars"] = grouped.cumcount() + 1
    five["current_session_coverage"] = (
        five["current_session_observed_5m_bars"] / five["session_elapsed_5m_bars"]
    ).clip(0.0, 1.0)
    session_summary = grouped.agg(
        session_quote_volume=("quote_volume_5m", "sum"),
        observed_5m_bars=("bucket_open_time_ms", "size"),
    ).reset_index()
    session_summary["expected_5m_bars"] = session_summary["session_seq"].map(
        {seq: block.duration_minutes // 5 for seq, block in UTC_SESSION_BY_SEQ.items()}
    )
    session_summary["qualified"] = (
        session_summary["observed_5m_bars"] / session_summary["expected_5m_bars"]
    ).ge(spec.universe.minimum_completed_session_coverage)
    total_baseline = _prior_session_baseline(
        session_summary,
        value_column="session_quote_volume",
        history_sessions=spec.universe.history_same_type_sessions,
    )
    five = five.merge(total_baseline, on=session_keys, how="left", validate="many_to_one")

    elapsed_summary = five[
        ["utc_day", "session_seq", "session_elapsed_5m_bars", "current_session_quote_volume"]
    ].merge(
        session_summary[["utc_day", "session_seq", "qualified"]],
        on=session_keys,
        how="left",
        validate="many_to_one",
    )
    elapsed_baseline = _prior_elapsed_baseline(
        elapsed_summary,
        history_sessions=spec.universe.history_same_type_sessions,
    )
    five = five.merge(
        elapsed_baseline,
        on=["utc_day", "session_seq", "session_elapsed_5m_bars"],
        how="left",
        validate="one_to_one",
    )
    five["current_activity_ratio"] = (
        five["current_session_quote_volume"]
        / five["prior_elapsed_quote_volume_median"].replace(0.0, np.nan)
    )
    time_index = pd.to_datetime(five["snapshot_time_ms"], unit="ms", utc=True)
    five["trailing_24h_quote_volume"] = (
        pd.Series(five["quote_volume_5m"].to_numpy(dtype=float), index=time_index)
        .rolling("24h", min_periods=1)
        .sum()
        .to_numpy()
    )
    five["symbol"] = symbol
    five["schema_version"] = STAGE1_SYMBOL_5M_SCHEMA_VERSION
    output_columns = [
        "schema_version",
        "symbol",
        "bucket_open_time_ms",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "close",
        "quote_volume_5m",
        "return_5m",
        "return_15m",
        "return_30m",
        "utc_day",
        "session_seq",
        "session_elapsed_5m_bars",
        "current_session_quote_volume",
        "current_session_coverage",
        "prior_session_quote_volume_median",
        "prior_session_quote_volume_mad_ratio",
        "prior_session_history_count",
        "prior_elapsed_quote_volume_median",
        "prior_elapsed_history_count",
        "current_activity_ratio",
        "trailing_24h_quote_volume",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    five.loc[:, output_columns].to_parquet(target, index=False, compression="zstd")
    return Symbol5mBuildStats(
        symbol=symbol,
        minute_rows=len(minute),
        five_minute_rows=len(five),
        first_snapshot_time_ms=int(five["snapshot_time_ms"].min()),
        last_snapshot_time_ms=int(five["snapshot_time_ms"].max()),
        output_path=str(target),
        status="built",
    )


def build_stage1_is(
    config: Stage1IsBuildConfig = Stage1IsBuildConfig(),
    *,
    spec: ResidualAbsorptionResearchSpec = ResidualAbsorptionResearchSpec(),
    progress: Callable[[str], None] | None = print,
) -> Stage1IsBuildResult:
    if not config.source_dir.is_dir():
        raise FileNotFoundError(config.source_dir)
    paths = sorted(
        path
        for path in config.source_dir.glob("*.parquet")
        if path.stem.upper().endswith(config.quote_suffix)
    )
    if config.max_symbols is not None:
        references = {f"{symbol}.parquet" for symbol in spec.market_impulse.reference_symbols}
        selected = paths[: config.max_symbols]
        selected_names = {path.name for path in selected}
        selected.extend(path for path in paths if path.name in references - selected_names)
        paths = sorted(set(selected))
    if not paths:
        raise Stage1BuildError("no source symbols matched the requested universe")
    symbol_dir = config.output_dir / "symbol_5m"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    stats: list[Symbol5mBuildStats] = []
    if config.workers == 1:
        for completed, path in enumerate(paths, start=1):
            stats.append(
                build_symbol_5m_is(
                    path,
                    output_path=symbol_dir / path.name,
                    spec=spec,
                    overwrite=config.overwrite,
                )
            )
            if progress and (completed % 25 == 0 or completed == len(paths)):
                progress(f"stage1 symbol 5m {completed}/{len(paths)}")
    else:
        with ProcessPoolExecutor(max_workers=config.workers) as executor:
            futures = {
                executor.submit(
                    build_symbol_5m_is,
                    path,
                    output_path=symbol_dir / path.name,
                    spec=spec,
                    overwrite=config.overwrite,
                ): path
                for path in paths
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                stats.append(future.result())
                if progress and (completed % 25 == 0 or completed == len(futures)):
                    progress(f"stage1 symbol 5m {completed}/{len(futures)}")
    cross_section_path, factor_path = build_cross_section_stage1(
        symbol_dir=symbol_dir,
        output_dir=config.output_dir,
        spec=spec,
    )
    manifest_path = config.output_dir / "stage1_is_manifest.json"
    manifest = {
        "protocol_version": spec.protocol_version,
        "stage": "stage1_is_inputs",
        "research_partition": "is",
        "is_max_input_time_ms_exclusive": spec.research_split.is_max_input_time_ms_exclusive,
        "symbol_count": len(stats),
        "symbol_stats": [asdict(row) for row in sorted(stats, key=lambda item: item.symbol)],
        "cross_section_path": str(cross_section_path),
        "factor_aggregates_path": str(factor_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return Stage1IsBuildResult(
        symbol_stats=tuple(sorted(stats, key=lambda item: item.symbol)),
        cross_section_path=cross_section_path,
        factor_aggregates_path=factor_path,
        manifest_path=manifest_path,
    )


def build_cross_section_stage1(
    *,
    symbol_dir: str | Path,
    output_dir: str | Path,
    spec: ResidualAbsorptionResearchSpec = ResidualAbsorptionResearchSpec(),
) -> tuple[Path, Path]:
    source_glob = str(Path(symbol_dir) / "*.parquet")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    lf = pl.scan_parquet(source_glob)
    core_pool = (
        pl.col("prior_session_history_count").ge(spec.universe.minimum_core_history_sessions)
        & pl.col("prior_session_quote_volume_median").is_not_null()
    )
    lf = lf.with_columns(
        pl.when(core_pool)
        .then(pl.col("prior_session_quote_volume_median"))
        .otherwise(None)
        .rank(method="ordinal", descending=True)
        .over("snapshot_time_ms")
        .alias("core_liquidity_rank"),
        pl.col("current_session_quote_volume")
        .rank(method="ordinal", descending=True)
        .over("snapshot_time_ms")
        .alias("current_liquidity_rank"),
    ).with_columns(
        pl.col("core_liquidity_rank").le(spec.universe.core_top_n).fill_null(False).alias("core_eligible"),
    )
    expansion_pool = (
        pl.col("prior_elapsed_history_count").ge(spec.universe.minimum_expansion_history_sessions)
        & pl.col("current_session_coverage").ge(spec.universe.minimum_current_coverage)
        & pl.col("session_elapsed_5m_bars").ge(
            int(np.ceil(spec.universe.minimum_current_elapsed_minutes / 5))
        )
        & pl.col("current_activity_ratio").ge(spec.universe.expansion_minimum_activity_ratio)
        & pl.col("current_liquidity_rank").le(spec.universe.expansion_liquidity_rank_ceiling)
    )
    lf = lf.with_columns(
        pl.when(expansion_pool)
        .then(pl.col("current_activity_ratio"))
        .otherwise(None)
        .rank(method="ordinal", descending=True)
        .over("snapshot_time_ms")
        .alias("activity_expansion_rank")
    ).with_columns(
        pl.col("activity_expansion_rank")
        .le(spec.universe.expansion_top_n)
        .fill_null(False)
        .alias("activity_expansion_eligible")
    ).with_columns(
        (pl.col("core_eligible") | pl.col("activity_expansion_eligible")).alias("candidate_eligible"),
        pl.lit(STAGE1_CROSS_SECTION_SCHEMA_VERSION).alias("cross_section_schema_version"),
    )
    cross_section_path = output / "cross_section_5m_is.parquet"
    lf.sink_parquet(cross_section_path, compression="zstd", engine="streaming")

    references = set(spec.market_impulse.reference_symbols)
    alt = lf.filter(
        pl.col("core_eligible")
        & ~pl.col("symbol").is_in(list(references))
        & pl.col("return_15m").is_not_null()
    )
    factor = alt.group_by("snapshot_time_ms").agg(
        pl.col("feature_cutoff_time_ms").max(),
        pl.col("utc_day").first(),
        pl.col("session_seq").first(),
        pl.len().alias("cross_section_symbol_count"),
        pl.col("return_5m").median().alias("alt_median_return_5m"),
        pl.col("return_15m").median().alias("alt_median_return_15m"),
        pl.col("return_30m").median().alias("alt_median_return_30m"),
        (pl.col("return_15m") > 0.0).mean().alias("directional_breadth_up_15m"),
        (pl.col("return_15m") < 0.0).mean().alias("directional_breadth_down_15m"),
    )
    for symbol, alias in (("BTCUSDT", "btc_return_15m"), ("ETHUSDT", "eth_return_15m")):
        reference = lf.filter(pl.col("symbol").eq(symbol)).select(
            "snapshot_time_ms",
            pl.col("return_15m").alias(alias),
        )
        factor = factor.join(reference, on="snapshot_time_ms", how="left")
    factor_path = output / "market_factor_aggregates_is.parquet"
    factor.sort("snapshot_time_ms").sink_parquet(
        factor_path,
        compression="zstd",
        engine="streaming",
    )
    return cross_section_path, factor_path


def finalize_and_audit_stage1_is(
    *,
    output_dir: str | Path,
    source_dir: str | Path,
    spec: ResidualAbsorptionResearchSpec = ResidualAbsorptionResearchSpec(),
) -> Path:
    """Materialize causal factor/events and write the independent stage-1 input audit."""

    output = Path(output_dir)
    factor_path = output / "market_factor_aggregates_is.parquet"
    cross_path = output / "cross_section_5m_is.parquet"
    manifest_path = output / "stage1_is_manifest.json"
    for path in (factor_path, cross_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    aggregates = pd.read_parquet(factor_path)
    snapshots = finalize_market_factor_aggregates(aggregates, spec=spec.market_impulse)
    events = detect_market_impulse_events(snapshots, spec=spec.market_impulse)
    snapshot_path = output / "market_factor_snapshots_is.parquet"
    event_path = output / "market_impulse_events_is.parquet"
    pd.DataFrame([asdict(row) for row in snapshots]).to_parquet(
        snapshot_path,
        index=False,
        compression="zstd",
    )
    pd.DataFrame([asdict(row) for row in events]).to_parquet(
        event_path,
        index=False,
        compression="zstd",
    )

    lf = pl.scan_parquet(cross_path)
    scalar = lf.select(
        pl.len().alias("row_count"),
        pl.col("symbol").n_unique().alias("symbol_count"),
        pl.col("snapshot_time_ms").min().alias("first_snapshot_time_ms"),
        pl.col("snapshot_time_ms").max().alias("last_snapshot_time_ms"),
        (pl.col("feature_cutoff_time_ms") > pl.col("snapshot_time_ms"))
        .sum()
        .alias("temporal_violation_count"),
    ).collect().row(0, named=True)
    per_snapshot = lf.group_by("snapshot_time_ms").agg(
        pl.col("core_eligible").sum().alias("core_count"),
        pl.col("activity_expansion_eligible").sum().alias("expansion_count"),
    )
    maxima = per_snapshot.select(
        pl.col("core_count").max().alias("maximum_core_count"),
        pl.col("expansion_count").max().alias("maximum_expansion_count"),
    ).collect().row(0, named=True)
    prefix_checks = _point_in_time_prefix_checks(
        source_dir=Path(source_dir),
        symbol_dir=output / "symbol_5m",
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"),
    )
    last_snapshot = int(scalar["last_snapshot_time_ms"])
    checks = {
        "is_timestamp_boundary": last_snapshot < spec.research_split.oos_start_time_ms,
        "feature_cutoff_not_after_snapshot": int(scalar["temporal_violation_count"]) == 0,
        "core_universe_limit": int(maxima["maximum_core_count"] or 0) <= spec.universe.core_top_n,
        "expansion_universe_limit": int(maxima["maximum_expansion_count"] or 0) <= spec.universe.expansion_top_n,
        "point_in_time_prefix_recomputation": all(item["passed"] for item in prefix_checks),
    }
    audit = {
        "audit_version": "residual_absorption_stage1_input_audit_v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "cross_section": {**scalar, **maxima},
        "factor_snapshot_count": len(snapshots),
        "market_impulse_event_count": len(events),
        "reference_confirmed_event_count": sum(event.reference_confirmed for event in events),
        "prefix_recomputations": prefix_checks,
        "test_suite_evidence": {
            "future_tail_mutation": "tests/test_residual_absorption_is_build.py",
            "physical_oos_reader_guard": "tests/test_residual_absorption_stage1.py",
            "status_is_not_inferred_by_runtime_audit": True,
        },
        "note": "Runtime PASS depends only on checks executed against this artifact.",
    }
    audit_path = output / "stage1_input_lookahead_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    if audit["status"] != "PASS":
        raise Stage1BuildError(f"stage-1 input lookahead audit failed: {audit_path}")
    return audit_path


def _point_in_time_prefix_checks(
    *,
    source_dir: Path,
    symbol_dir: Path,
    symbols: tuple[str, ...],
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for symbol in symbols:
        raw_path = source_dir / f"{symbol}.parquet"
        built_path = symbol_dir / f"{symbol}.parquet"
        if not raw_path.is_file() or not built_path.is_file():
            continue
        built = pd.read_parquet(
            built_path,
            columns=[
                "bucket_open_time_ms",
                "snapshot_time_ms",
                "close",
                "quote_volume_5m",
            ],
        )
        if built.empty:
            continue
        raw = read_is_symbol_minutes(raw_path, columns=("close", "quote_volume"))
        for position in sorted({len(built) // 10, len(built) // 2, 9 * len(built) // 10}):
            row = built.iloc[min(position, len(built) - 1)]
            prefix = raw.loc[
                raw["timestamp"].ge(int(row["bucket_open_time_ms"]))
                & raw["timestamp"].lt(int(row["snapshot_time_ms"]))
            ].sort_values("timestamp")
            close_matches = len(prefix) == 5 and np.isclose(
                float(prefix.iloc[-1]["close"]),
                float(row["close"]),
                rtol=1e-7,
                atol=1e-9,
            )
            quote_matches = len(prefix) == 5 and np.isclose(
                float(prefix["quote_volume"].sum()),
                float(row["quote_volume_5m"]),
                rtol=1e-6,
                atol=1e-4,
            )
            checks.append(
                {
                    "symbol": symbol,
                    "snapshot_time_ms": int(row["snapshot_time_ms"]),
                    "source_minute_count": len(prefix),
                    "close_matches": bool(close_matches),
                    "quote_volume_matches": bool(quote_matches),
                    "passed": bool(close_matches and quote_matches),
                }
            )
    if not checks:
        raise Stage1BuildError("point-in-time audit found no deterministic symbols to verify")
    return checks


def _prior_session_baseline(
    sessions: pd.DataFrame,
    *,
    value_column: str,
    history_sessions: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for seq, group in sessions.groupby("session_seq", sort=True):
        history: list[float] = []
        for raw in group.sort_values("utc_day").itertuples(index=False):
            values = np.asarray(history[-history_sessions:], dtype=float)
            median = float(np.median(values)) if len(values) else float("nan")
            mad_ratio = float("nan")
            if len(values) and median > 0.0:
                mad_ratio = float(np.median(np.abs(values - median)) / median)
            rows.append(
                {
                    "utc_day": int(raw.utc_day),
                    "session_seq": int(seq),
                    "prior_session_quote_volume_median": median,
                    "prior_session_quote_volume_mad_ratio": mad_ratio,
                    "prior_session_history_count": len(values),
                }
            )
            if bool(raw.qualified):
                history.append(float(getattr(raw, value_column)))
    return pd.DataFrame(rows)


def _prior_elapsed_baseline(
    rows: pd.DataFrame,
    *,
    history_sessions: int,
) -> pd.DataFrame:
    output: list[dict[str, object]] = []
    for (seq, elapsed), group in rows.groupby(
        ["session_seq", "session_elapsed_5m_bars"],
        sort=True,
    ):
        history: list[float] = []
        for raw in group.sort_values("utc_day").itertuples(index=False):
            values = np.asarray(history[-history_sessions:], dtype=float)
            output.append(
                {
                    "utc_day": int(raw.utc_day),
                    "session_seq": int(seq),
                    "session_elapsed_5m_bars": int(elapsed),
                    "prior_elapsed_quote_volume_median": (
                        float(np.median(values)) if len(values) else float("nan")
                    ),
                    "prior_elapsed_history_count": len(values),
                }
            )
            if bool(raw.qualified):
                history.append(float(raw.current_session_quote_volume))
    return pd.DataFrame(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build residual-absorption stage-1 IS inputs.")
    defaults = Stage1IsBuildConfig()
    parser.add_argument("--source", type=Path, default=defaults.source_dir)
    parser.add_argument("--out", type=Path, default=defaults.output_dir)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args()
    if args.finalize_only:
        print(finalize_and_audit_stage1_is(output_dir=args.out, source_dir=args.source))
    else:
        result = build_stage1_is(
            Stage1IsBuildConfig(
                source_dir=args.source,
                output_dir=args.out,
                workers=args.workers,
                max_symbols=args.max_symbols,
                overwrite=args.overwrite,
            )
        )
        print(result.manifest_path)


__all__ = [
    "STAGE1_CROSS_SECTION_SCHEMA_VERSION",
    "STAGE1_SYMBOL_5M_SCHEMA_VERSION",
    "Stage1BuildError",
    "Stage1IsBuildConfig",
    "Stage1IsBuildResult",
    "Symbol5mBuildStats",
    "build_cross_section_stage1",
    "build_stage1_is",
    "build_symbol_5m_is",
    "finalize_and_audit_stage1_is",
]


if __name__ == "__main__":
    main()
