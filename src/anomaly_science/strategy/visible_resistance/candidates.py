"""Causal candidate sampling for manual visible-resistance annotation.

Candidate selection deliberately does not contain a resistance detector.  It
uses only activity, realised movement, liquidity, and fixed end-of-day
snapshots.  The human label is therefore free to say that no visible boundary
exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.annotation.boundary.contracts import BOUNDARY_CANDIDATE_SCHEMA_VERSION

BOUNDARY_CANDIDATE_POLICY_VERSION = "causal_activity_snapshot_v1"


@dataclass(frozen=True, slots=True)
class BoundaryCandidateConfig:
    analysis_start: date = date(2025, 1, 1)
    analysis_end: date = date(2025, 12, 31)
    rolling_days: int = 20
    maximum_liquid_symbols: int = 50
    symbol_cooldown_days: int = 5
    review_lookback_days: int = 21
    maximum_events: int = 360

    def __post_init__(self) -> None:
        if self.analysis_start != date(2025, 1, 1) or self.analysis_end != date(2025, 12, 31):
            raise ValueError("visible-resistance IS candidate contract is calendar year 2025")
        if self.rolling_days < 10:
            raise ValueError("rolling_days must be at least 10")
        if self.maximum_liquid_symbols < 10:
            raise ValueError("maximum_liquid_symbols must be at least 10")
        if self.maximum_events <= 0:
            raise ValueError("maximum_events must be positive")


def _event_id(symbol: str, snapshot_time_ms: int, stratum: str) -> str:
    raw = f"{symbol}|{snapshot_time_ms}|{stratum}|{BOUNDARY_CANDIDATE_POLICY_VERSION}".encode()
    return "visible_resistance_" + hashlib.blake2b(raw, digest_size=10).hexdigest()


def _eligible_symbols(master: pd.DataFrame) -> set[str]:
    required = {
        "symbol",
        "quote_asset",
        "is_stablecoin",
        "is_leveraged_token",
    }
    missing = required.difference(master.columns)
    if missing:
        raise ValueError(f"futures master missing columns: {sorted(missing)}")
    eligible = master.loc[
        master["quote_asset"].eq("USDT")
        & ~master["is_stablecoin"].fillna(True).astype(bool)
        & ~master["is_leveraged_token"].fillna(True).astype(bool),
        "symbol",
    ]
    return set(eligible.astype(str))


def _causal_scores(daily: pd.DataFrame, config: BoundaryCandidateConfig) -> pd.DataFrame:
    required = {
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "quote_volume",
        "available_time_ms",
        "complete_daily_bar",
    }
    missing = required.difference(daily.columns)
    if missing:
        raise ValueError(f"daily futures data missing columns: {sorted(missing)}")
    frame = daily.loc[daily["complete_daily_bar"].fillna(False)].copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.loc[
        frame["date"].between(
            pd.Timestamp(config.analysis_start, tz="UTC"),
            pd.Timestamp(config.analysis_end, tz="UTC"),
        )
    ].sort_values(["symbol", "date"], kind="stable")
    if frame.empty:
        raise ValueError("no complete daily futures bars inside calendar IS 2025")

    group = frame.groupby("symbol", sort=False, group_keys=False)
    frame["history_days"] = group.cumcount()
    frame["prior_median_quote_volume"] = group["quote_volume"].transform(
        lambda values: values.shift(1).rolling(config.rolling_days, min_periods=config.rolling_days).median()
    )
    frame["prior_median_range_pct"] = group.apply(
        lambda part: (part["high"] / part["low"] - 1.0)
        .shift(1)
        .rolling(config.rolling_days, min_periods=config.rolling_days)
        .median(),
        include_groups=False,
    ).reset_index(level=0, drop=True)
    frame["positive_move_20d"] = group["close"].pct_change(config.rolling_days, fill_method=None)
    frame["activity_ratio"] = frame["quote_volume"] / frame["prior_median_quote_volume"]
    frame["daily_range_pct"] = frame["high"] / frame["low"] - 1.0
    frame["range_expansion"] = frame["daily_range_pct"] / frame["prior_median_range_pct"]
    finite = (
        np.isfinite(frame["activity_ratio"])
        & np.isfinite(frame["range_expansion"])
        & np.isfinite(frame["positive_move_20d"])
        & frame["prior_median_quote_volume"].gt(0)
        & frame["prior_median_range_pct"].gt(0)
    )
    frame = frame.loc[finite].copy()
    frame["liquidity_rank"] = frame.groupby("date")["prior_median_quote_volume"].rank(
        method="first", ascending=False
    )
    frame = frame.loc[frame["liquidity_rank"].le(config.maximum_liquid_symbols)].copy()
    frame["activity_rank"] = frame.groupby("date")["activity_ratio"].rank(pct=True)
    frame["movement_rank"] = frame.groupby("date")["positive_move_20d"].rank(pct=True)
    frame["range_rank"] = frame.groupby("date")["range_expansion"].rank(pct=True)
    frame["sampling_score"] = (
        0.45 * frame["activity_rank"]
        + 0.35 * frame["movement_rank"]
        + 0.20 * frame["range_rank"]
    )
    return frame.sort_values(["date", "sampling_score", "symbol"], ascending=[True, False, True], kind="stable")


def _select_causal_panel(scored: pd.DataFrame, config: BoundaryCandidateConfig) -> pd.DataFrame:
    selected: list[dict[str, Any]] = []
    last_selected: dict[str, pd.Timestamp] = {}
    cooldown = pd.Timedelta(days=config.symbol_cooldown_days)

    for event_date, day in scored.groupby("date", sort=True):
        if len(selected) >= config.maximum_events:
            break
        used_today: set[str] = set()

        def admissible(row: pd.Series) -> bool:
            symbol = str(row["symbol"])
            previous = last_selected.get(symbol)
            return symbol not in used_today and (previous is None or event_date - previous >= cooldown)

        salient = next((row for _, row in day.iterrows() if admissible(row)), None)
        ordered_control = day.assign(
            _neutral_distance=(day["sampling_score"] - day["sampling_score"].median()).abs()
        ).sort_values(["_neutral_distance", "symbol"], kind="stable")
        control = next((row for _, row in ordered_control.iterrows() if admissible(row)), None)

        for stratum, row in (("salient", salient), ("neutral_control", control)):
            if row is None or len(selected) >= config.maximum_events:
                continue
            symbol = str(row["symbol"])
            used_today.add(symbol)
            last_selected[symbol] = event_date
            record = row.to_dict()
            record["sampling_stratum"] = stratum
            selected.append(record)

    if not selected:
        raise ValueError("causal boundary candidate sampler produced no rows")
    return pd.DataFrame(selected)


def build_boundary_review_candidates(
    *,
    daily_path: Path,
    master_path: Path,
    output_dir: Path,
    config: BoundaryCandidateConfig = BoundaryCandidateConfig(),
) -> pd.DataFrame:
    """Materialise an append-only manual-review queue without detecting levels."""

    daily = pd.read_parquet(daily_path)
    master = pd.read_parquet(master_path)
    daily = daily.loc[daily["symbol"].astype(str).isin(_eligible_symbols(master))]
    scored = _causal_scores(daily, config)
    selected = _select_causal_panel(scored, config)

    rows: list[dict[str, Any]] = []
    lookback_ms = config.review_lookback_days * 86_400_000
    for raw in selected.to_dict("records"):
        snapshot_ms = int(raw["available_time_ms"])
        symbol = str(raw["symbol"])
        stratum = str(raw["sampling_stratum"])
        rows.append(
            {
                "candidate_schema_version": BOUNDARY_CANDIDATE_SCHEMA_VERSION,
                "candidate_policy_version": BOUNDARY_CANDIDATE_POLICY_VERSION,
                "event_id": _event_id(symbol, snapshot_ms, stratum),
                "symbol": symbol,
                "tf": "1h",
                "date": pd.Timestamp(raw["date"]),
                "snapshot_time_ms": snapshot_ms,
                "feature_cutoff_time_ms": snapshot_ms,
                "review_start_ms": snapshot_ms - lookback_ms,
                "review_end_ms": snapshot_ms - 1,
                "sampling_stratum": stratum,
                "sampling_score": float(raw["sampling_score"]),
                "activity_ratio": float(raw["activity_ratio"]),
                "positive_move_20d": float(raw["positive_move_20d"]),
                "range_expansion": float(raw["range_expansion"]),
                "liquidity_rank": int(raw["liquidity_rank"]),
            }
        )
    frame = pd.DataFrame(rows).sort_values(["snapshot_time_ms", "sampling_stratum", "symbol"], kind="stable")
    if frame["event_id"].duplicated().any():
        raise ValueError("visible-resistance event ids must be unique")
    if frame["review_end_ms"].ge(frame["snapshot_time_ms"]).any():
        raise ValueError("boundary review window must end strictly before snapshot")

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "candidates.parquet"
    labels_path = output_dir / "labels.jsonl"
    if labels_path.exists() and labels_path.stat().st_size:
        raise FileExistsError(f"refusing to overwrite expert boundary labels: {labels_path}")
    frame.to_parquet(candidates_path, index=False)
    labels_path.touch(exist_ok=True)
    actual_start = pd.to_datetime(daily["date"], utc=True).min()
    actual_end = pd.to_datetime(daily["date"], utc=True).max()
    manifest = {
        "candidate_schema_version": BOUNDARY_CANDIDATE_SCHEMA_VERSION,
        "candidate_policy_version": BOUNDARY_CANDIDATE_POLICY_VERSION,
        "declared_is_start": config.analysis_start.isoformat(),
        "declared_is_end": config.analysis_end.isoformat(),
        "available_cache_start": actual_start.isoformat(),
        "available_cache_end": actual_end.isoformat(),
        "full_calendar_year_available": bool(
            actual_start <= pd.Timestamp(config.analysis_start, tz="UTC")
            and actual_end >= pd.Timestamp(config.analysis_end, tz="UTC")
        ),
        "candidate_count": len(frame),
        "sampling_strata": frame["sampling_stratum"].value_counts().sort_index().to_dict(),
        "config": {key: str(value) if isinstance(value, date) else value for key, value in asdict(config).items()},
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return frame.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build causal visible-resistance annotation candidates.")
    parser.add_argument(
        "--daily-path",
        type=Path,
        default=Path(".output/market/binance_vision/um_futures/daily_local_is_v1/futures_daily_is.parquet"),
    )
    parser.add_argument(
        "--master-path",
        type=Path,
        default=Path(".output/research/binance_usdm_trend_is_2025_v1/data_inferred_futures_master.parquet"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".output/research/visible_resistance_is_2025_v1"),
    )
    parser.add_argument("--max-events", type=int, default=BoundaryCandidateConfig.maximum_events)
    args = parser.parse_args()
    frame = build_boundary_review_candidates(
        daily_path=args.daily_path,
        master_path=args.master_path,
        output_dir=args.output_dir,
        config=BoundaryCandidateConfig(maximum_events=args.max_events),
    )
    print(f"visible-resistance candidates={len(frame)} -> {args.output_dir}", flush=True)


if __name__ == "__main__":
    main()
