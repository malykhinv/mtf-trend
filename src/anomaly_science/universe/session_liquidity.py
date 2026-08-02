"""Causal session-aware liquidity universe built at an explicit snapshot."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from anomaly_science.contracts.time import SnapshotTiming, validate_timestamp_ms
from anomaly_science.market_context.sessions import (
    MS_PER_DAY,
    MS_PER_MINUTE,
    UTC_SESSION_BY_SEQ,
    UTC_SESSION_CALENDAR_VERSION,
    block_seq_for_ms,
    session_instance_for_ms,
    utc_day_for_ms,
)

SESSION_LIQUIDITY_UNIVERSE_SCHEMA_VERSION = "session_liquidity_universe_v1"
_DAY_MS = 24 * 60 * MS_PER_MINUTE


@dataclass(frozen=True, slots=True)
class SessionLiquidityUniverseConfig:
    history_same_type_sessions: int = 20
    minimum_core_history_sessions: int = 5
    minimum_expansion_history_sessions: int = 3
    minimum_completed_session_coverage: float = 0.8
    minimum_current_elapsed_minutes: int = 5
    minimum_current_coverage: float = 0.8
    core_top_n: int = 100
    expansion_top_n: int = 25
    expansion_minimum_activity_ratio: float = 3.0
    expansion_liquidity_rank_ceiling: int = 200

    def __post_init__(self) -> None:
        if self.history_same_type_sessions <= 0:
            raise ValueError("history_same_type_sessions must be positive")
        if not 1 <= self.minimum_expansion_history_sessions <= self.history_same_type_sessions:
            raise ValueError("minimum_expansion_history_sessions is outside the history window")
        if not 1 <= self.minimum_core_history_sessions <= self.history_same_type_sessions:
            raise ValueError("minimum_core_history_sessions is outside the history window")
        if not 0.0 < self.minimum_completed_session_coverage <= 1.0:
            raise ValueError("minimum_completed_session_coverage must be in (0, 1]")
        if not 0.0 < self.minimum_current_coverage <= 1.0:
            raise ValueError("minimum_current_coverage must be in (0, 1]")
        if self.minimum_current_elapsed_minutes <= 0:
            raise ValueError("minimum_current_elapsed_minutes must be positive")
        if min(self.core_top_n, self.expansion_top_n, self.expansion_liquidity_rank_ceiling) <= 0:
            raise ValueError("universe rank limits must be positive")
        if self.expansion_minimum_activity_ratio <= 1.0:
            raise ValueError("expansion_minimum_activity_ratio must be greater than one")


@dataclass(frozen=True, slots=True)
class SessionLiquidityUniverseRow:
    schema_version: str
    session_calendar_version: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    symbol: str
    utc_day: int
    session_seq: int
    session_name: str
    session_elapsed_minutes: int
    history_session_count: int
    prior_session_quote_volume_median: float | None
    prior_session_quote_volume_mad_ratio: float | None
    prior_elapsed_quote_volume_median: float | None
    current_partial_quote_volume: float
    current_activity_ratio: float | None
    trailing_24h_quote_volume: float
    current_session_coverage: float
    core_liquidity_rank: int | None
    current_liquidity_rank: int | None
    activity_expansion_rank: int | None
    core_eligible: bool
    activity_expansion_eligible: bool
    eligibility_channel: str
    latest_input_available_time_ms: int | None

    def __post_init__(self) -> None:
        SnapshotTiming(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
        )
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.session_seq not in UTC_SESSION_BY_SEQ:
            raise ValueError(f"unknown session_seq: {self.session_seq}")
        if self.session_elapsed_minutes < 0 or self.history_session_count < 0:
            raise ValueError("session counts must be non-negative")
        if not 0.0 <= self.current_session_coverage <= 1.0:
            raise ValueError("current_session_coverage must be in [0, 1]")
        expected_channel = _eligibility_channel(
            core=self.core_eligible,
            expansion=self.activity_expansion_eligible,
        )
        if self.eligibility_channel != expected_channel:
            raise ValueError("eligibility_channel disagrees with eligibility flags")


def session_universe_rows_to_frame(rows: list[SessionLiquidityUniverseRow]) -> pd.DataFrame:
    return pd.DataFrame([asdict(row) for row in rows])


def build_session_liquidity_universe(
    candles_1m: pd.DataFrame,
    *,
    snapshot_time_ms: int,
    config: SessionLiquidityUniverseConfig = SessionLiquidityUniverseConfig(),
) -> list[SessionLiquidityUniverseRow]:
    """Build one session-aware universe snapshot from data available as of time."""

    validate_timestamp_ms(snapshot_time_ms, field_name="snapshot_time_ms")
    required = {"symbol", "open_time_ms", "available_time_ms", "quote_volume"}
    missing = sorted(required.difference(candles_1m.columns))
    if missing:
        raise ValueError(f"candles_1m is missing required columns: {missing}")
    if candles_1m.empty:
        return []

    frame = candles_1m.loc[:, sorted(required)].copy()
    frame["symbol"] = frame["symbol"].astype(str)
    for column in ("open_time_ms", "available_time_ms", "quote_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if bool((frame["quote_volume"] < 0).any()):
        raise ValueError("quote_volume must be non-negative")
    if bool(frame.duplicated(["symbol", "open_time_ms"]).any()):
        raise ValueError("candles_1m must be unique by symbol and open_time_ms")

    visible = frame.loc[
        frame["available_time_ms"].le(snapshot_time_ms)
        & frame["open_time_ms"].lt(snapshot_time_ms)
    ].copy()
    if visible.empty:
        return []

    open_ms = visible["open_time_ms"].to_numpy(dtype=np.int64)
    visible["utc_day"] = utc_day_for_ms(open_ms)
    visible["session_seq"] = block_seq_for_ms(open_ms)
    start_hour = visible["session_seq"].map(
        {seq: block.start_hour for seq, block in UTC_SESSION_BY_SEQ.items()}
    )
    end_hour = visible["session_seq"].map(
        {seq: block.end_hour for seq, block in UTC_SESSION_BY_SEQ.items()}
    )
    visible["session_start_ms"] = visible["utc_day"] * MS_PER_DAY + start_hour * 60 * MS_PER_MINUTE
    visible["session_end_ms"] = visible["utc_day"] * MS_PER_DAY + end_hour * 60 * MS_PER_MINUTE
    visible["minute_offset"] = (
        (visible["open_time_ms"] - visible["session_start_ms"]) // MS_PER_MINUTE
    ).astype("int64")

    current = session_instance_for_ms(snapshot_time_ms)
    elapsed = current.elapsed_minutes_at(snapshot_time_ms)
    seq = current.block.seq
    historical_bars = visible.loc[
        visible["session_seq"].eq(seq)
        & visible["session_end_ms"].le(snapshot_time_ms)
    ].copy()
    historical_sessions = _qualified_historical_sessions(
        historical_bars,
        duration_minutes=current.block.duration_minutes,
        config=config,
    )
    selected_history = _latest_history_sessions(
        historical_sessions,
        limit=config.history_same_type_sessions,
    )
    history_summary = _history_summary(selected_history)
    elapsed_history = _elapsed_history_summary(
        historical_bars,
        selected_history=selected_history,
        elapsed_minutes=elapsed,
    )

    current_bars = visible.loc[
        visible["utc_day"].eq(current.utc_day)
        & visible["session_seq"].eq(seq)
        & visible["open_time_ms"].ge(current.start_time_ms)
        & visible["open_time_ms"].lt(snapshot_time_ms)
    ]
    current_summary = _current_summary(current_bars, elapsed_minutes=elapsed)
    trailing = (
        visible.loc[visible["open_time_ms"].ge(snapshot_time_ms - _DAY_MS)]
        .groupby("symbol", sort=False)
        .agg(
            trailing_24h_quote_volume=("quote_volume", "sum"),
            trailing_latest_available_time_ms=("available_time_ms", "max"),
        )
    )

    combined = history_summary.join(elapsed_history, how="outer")
    combined = combined.join(current_summary, how="outer")
    combined = combined.join(trailing, how="outer").fillna(
        {
            "history_session_count": 0,
            "current_partial_quote_volume": 0.0,
            "current_observed_minutes": 0,
            "trailing_24h_quote_volume": 0.0,
        }
    )
    if combined.empty:
        return []
    defaults: dict[str, float] = {
        "history_session_count": 0.0,
        "prior_session_quote_volume_median": float("nan"),
        "prior_session_quote_volume_mad_ratio": float("nan"),
        "prior_elapsed_quote_volume_median": float("nan"),
        "current_partial_quote_volume": 0.0,
        "current_observed_minutes": 0.0,
        "trailing_24h_quote_volume": 0.0,
        "history_latest_available_time_ms": float("nan"),
        "current_latest_available_time_ms": float("nan"),
        "trailing_latest_available_time_ms": float("nan"),
    }
    for column, default in defaults.items():
        if column not in combined.columns:
            combined[column] = default
    combined[["history_session_count", "current_partial_quote_volume", "current_observed_minutes", "trailing_24h_quote_volume"]] = combined[
        ["history_session_count", "current_partial_quote_volume", "current_observed_minutes", "trailing_24h_quote_volume"]
    ].fillna(0.0)
    combined["latest_input_available_time_ms"] = combined[
        [
            "history_latest_available_time_ms",
            "current_latest_available_time_ms",
            "trailing_latest_available_time_ms",
        ]
    ].max(axis=1, skipna=True)
    combined["history_session_count"] = combined["history_session_count"].astype("int64")
    combined["current_session_coverage"] = (
        combined["current_observed_minutes"] / max(elapsed, 1)
    ).clip(0.0, 1.0)
    combined["current_activity_ratio"] = (
        combined["current_partial_quote_volume"]
        / combined["prior_elapsed_quote_volume_median"].replace(0.0, np.nan)
    )

    core_pool = combined.loc[
        combined["history_session_count"].ge(config.minimum_core_history_sessions)
        & combined["prior_session_quote_volume_median"].notna()
    ].copy()
    core_pool = core_pool.sort_values(
        ["prior_session_quote_volume_median"],
        ascending=[False],
        kind="mergesort",
    )
    core_pool = core_pool.sort_index(kind="mergesort").sort_values(
        "prior_session_quote_volume_median",
        ascending=False,
        kind="mergesort",
    )
    core_rank = pd.Series(
        np.arange(1, len(core_pool) + 1, dtype=np.int64),
        index=core_pool.index,
        name="core_liquidity_rank",
    )

    current_pool = combined.sort_index(kind="mergesort").sort_values(
        "current_partial_quote_volume",
        ascending=False,
        kind="mergesort",
    )
    current_rank = pd.Series(
        np.arange(1, len(current_pool) + 1, dtype=np.int64),
        index=current_pool.index,
        name="current_liquidity_rank",
    )
    combined = combined.join(core_rank).join(current_rank)
    expansion_pool = combined.loc[
        combined["history_session_count"].ge(config.minimum_expansion_history_sessions)
        & combined["current_session_coverage"].ge(config.minimum_current_coverage)
        & combined["current_activity_ratio"].ge(config.expansion_minimum_activity_ratio)
        & combined["current_liquidity_rank"].le(config.expansion_liquidity_rank_ceiling)
        & (elapsed >= config.minimum_current_elapsed_minutes)
    ].copy()
    expansion_pool = expansion_pool.sort_index(kind="mergesort").sort_values(
        ["current_activity_ratio", "current_partial_quote_volume"],
        ascending=[False, False],
        kind="mergesort",
    )
    expansion_rank = pd.Series(
        np.arange(1, len(expansion_pool) + 1, dtype=np.int64),
        index=expansion_pool.index,
        name="activity_expansion_rank",
    )
    combined = combined.join(expansion_rank)

    rows: list[SessionLiquidityUniverseRow] = []
    for symbol, raw in combined.sort_index().iterrows():
        core_value = _optional_rank(raw.get("core_liquidity_rank"))
        current_value = _optional_rank(raw.get("current_liquidity_rank"))
        expansion_value = _optional_rank(raw.get("activity_expansion_rank"))
        core_eligible = core_value is not None and core_value <= config.core_top_n
        expansion_eligible = expansion_value is not None and expansion_value <= config.expansion_top_n
        latest_available = raw.get("latest_input_available_time_ms")
        rows.append(
            SessionLiquidityUniverseRow(
                schema_version=SESSION_LIQUIDITY_UNIVERSE_SCHEMA_VERSION,
                session_calendar_version=UTC_SESSION_CALENDAR_VERSION,
                snapshot_time_ms=snapshot_time_ms,
                feature_cutoff_time_ms=snapshot_time_ms,
                symbol=str(symbol),
                utc_day=current.utc_day,
                session_seq=seq,
                session_name=current.block.name,
                session_elapsed_minutes=elapsed,
                history_session_count=int(raw["history_session_count"]),
                prior_session_quote_volume_median=_optional_float(raw.get("prior_session_quote_volume_median")),
                prior_session_quote_volume_mad_ratio=_optional_float(raw.get("prior_session_quote_volume_mad_ratio")),
                prior_elapsed_quote_volume_median=_optional_float(raw.get("prior_elapsed_quote_volume_median")),
                current_partial_quote_volume=float(raw["current_partial_quote_volume"]),
                current_activity_ratio=_optional_float(raw.get("current_activity_ratio")),
                trailing_24h_quote_volume=float(raw["trailing_24h_quote_volume"]),
                current_session_coverage=float(raw["current_session_coverage"]),
                core_liquidity_rank=core_value,
                current_liquidity_rank=current_value,
                activity_expansion_rank=expansion_value,
                core_eligible=core_eligible,
                activity_expansion_eligible=expansion_eligible,
                eligibility_channel=_eligibility_channel(core=core_eligible, expansion=expansion_eligible),
                latest_input_available_time_ms=_optional_int(latest_available),
            )
        )
    return rows


def _qualified_historical_sessions(
    bars: pd.DataFrame,
    *,
    duration_minutes: int,
    config: SessionLiquidityUniverseConfig,
) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "utc_day",
                "quote_volume",
                "observed_minutes",
                "coverage",
                "latest_input_available_time_ms",
            ]
        )
    grouped = bars.groupby(["symbol", "utc_day"], sort=False).agg(
        quote_volume=("quote_volume", "sum"),
        observed_minutes=("open_time_ms", "nunique"),
        latest_input_available_time_ms=("available_time_ms", "max"),
    ).reset_index()
    grouped["coverage"] = grouped["observed_minutes"] / duration_minutes
    return grouped.loc[
        grouped["coverage"].ge(config.minimum_completed_session_coverage)
    ].copy()


def _latest_history_sessions(frame: pd.DataFrame, *, limit: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return (
        frame.sort_values(["symbol", "utc_day"], ascending=[True, False], kind="mergesort")
        .groupby("symbol", sort=False)
        .head(limit)
        .copy()
    )


def _history_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(index=pd.Index([], name="symbol"))

    def mad_ratio(values: pd.Series) -> float:
        median = float(values.median())
        if median <= 0.0:
            return float("nan")
        return float((values - median).abs().median() / median)

    grouped = frame.groupby("symbol", sort=False)
    result = grouped.agg(
        history_session_count=("utc_day", "size"),
        prior_session_quote_volume_median=("quote_volume", "median"),
        history_latest_available_time_ms=("latest_input_available_time_ms", "max"),
    )
    result["prior_session_quote_volume_mad_ratio"] = grouped["quote_volume"].apply(mad_ratio)
    return result


def _elapsed_history_summary(
    bars: pd.DataFrame,
    *,
    selected_history: pd.DataFrame,
    elapsed_minutes: int,
) -> pd.DataFrame:
    if bars.empty or selected_history.empty or elapsed_minutes <= 0:
        return pd.DataFrame(index=pd.Index([], name="symbol"))
    keys = selected_history[["symbol", "utc_day"]]
    selected_bars = bars.merge(keys, on=["symbol", "utc_day"], how="inner")
    selected_bars = selected_bars.loc[selected_bars["minute_offset"].lt(elapsed_minutes)]
    per_session = selected_bars.groupby(["symbol", "utc_day"], sort=False)["quote_volume"].sum()
    return per_session.groupby("symbol", sort=False).median().to_frame(
        "prior_elapsed_quote_volume_median"
    )


def _current_summary(frame: pd.DataFrame, *, elapsed_minutes: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(index=pd.Index([], name="symbol"))
    result = frame.groupby("symbol", sort=False).agg(
        current_partial_quote_volume=("quote_volume", "sum"),
        current_observed_minutes=("open_time_ms", "nunique"),
        current_latest_available_time_ms=("available_time_ms", "max"),
    )
    if elapsed_minutes == 0:
        result["current_observed_minutes"] = 0
    return result


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def _optional_int(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _optional_rank(value: object) -> int | None:
    return _optional_int(value)


def _eligibility_channel(*, core: bool, expansion: bool) -> str:
    if core and expansion:
        return "core_and_activity_expansion"
    if core:
        return "core"
    if expansion:
        return "activity_expansion"
    return "excluded"


__all__ = [
    "SESSION_LIQUIDITY_UNIVERSE_SCHEMA_VERSION",
    "SessionLiquidityUniverseConfig",
    "SessionLiquidityUniverseRow",
    "build_session_liquidity_universe",
    "session_universe_rows_to_frame",
]
