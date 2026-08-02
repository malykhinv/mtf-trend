"""Daily anomaly-hold cohort for continuation-versus-fade research.

This is a deterministic monitoring population, not a trading signal.  An event
is admitted only after the following daily candle has closed, so its condition
is explicit and reproducible:

* a 20-day sleep-volume baseline is followed by a two-day, upward volume pump;
* each pump day has quote volume >= 3x the median of the preceding 20 closed
  days, and the two-day close-to-close rise is >= 20%;
* one to three consecutive daily candles form a compact top consolidation:
  they remain near the anomaly high, make a shallow retrace, and do not extend
  into a fresh directional leg.

The hold is a cohort-entry condition, not the end of the chart.  Every cohort
member retains a fixed post-hold observation interval.  That interval is where
we later look for 1h/15m/10m BRK/CAP structures that precede a break to a new
range.  The interval's eventual outcome is stored separately from the
candidate features, so it cannot leak into setup selection or scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION
from anomaly_science.validation.governance import run_mvp1_holdout_governance


DAY_MS = 24 * 60 * 60 * 1_000
DEFAULT_CACHE_DIR = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEFAULT_OUTPUT_DIR = Path(".output/results/daily_anomaly_hold_continuation_v5_is")


@dataclass(frozen=True, slots=True)
class DailyAnomalyHoldPolicy:
    version: str = "daily_anomaly_hold_continuation_v5_is_2026-07-15"
    sleep_history_days: int = 20
    pump_candles: int = 2
    min_pump_candle_close_to_close_pct: float = 0.05
    min_pump_total_close_to_close_pct: float = 0.20
    min_pump_volume_over_sleep_median: float = 3.0
    min_hold_close_position: float = 0.80
    max_hold_close_position: float = 1.15
    min_hold_low_position: float = 0.50
    max_hold_low_position: float = 0.95
    max_hold_high_position: float = 1.15
    max_hold_upper_wick_share: float = 0.30
    max_hold_candles: int = 3
    daily_context_days: int = 60
    intraday_context_days: int = 5
    post_hold_observation_days: int = 14
    final_holdout_days: int = 60
    continuation_tfs: tuple[str, ...] = ("1d", "1h", "15m", "10m")


POLICY = DailyAnomalyHoldPolicy()


def _event_id(symbol: str, anomaly_day_ms: int) -> str:
    raw = f"{POLICY.version}|{symbol}|{anomaly_day_ms}".encode("utf-8")
    return "dailyhold_" + hashlib.blake2b(raw, digest_size=10).hexdigest()


def _load_daily_bars(path: Path) -> tuple[dict[str, np.ndarray], str]:
    """Load one cache schema explicitly and aggregate it to closed UTC days.

    Historical cache versions expose either native ``quote_volume`` or base
    ``volume``.  The latter is normalised candle-by-candle to quote notional,
    preserving a comparable daily volume-ratio criterion without dropping old
    symbols from the scan.
    """

    source = ds.dataset(path)
    names = set(source.schema.names)
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - names
    if missing:
        raise ValueError(f"{path}: missing daily-anomaly OHLC columns {sorted(missing)}")
    if "quote_volume" in names:
        volume_name, volume_basis = "quote_volume", "quote_volume"
    elif "volume" in names:
        volume_name, volume_basis = "volume", "volume_x_close"
    else:
        raise ValueError(f"{path}: requires quote_volume or volume")
    table = source.to_table(columns=["timestamp", "open", "high", "low", "close", volume_name])
    raw = {name: table.column(name).to_numpy(zero_copy_only=False).astype(np.float64) for name in table.column_names}
    raw["timestamp"] = raw["timestamp"].astype(np.int64)
    quote_volume = raw[volume_name] if volume_name == "quote_volume" else raw[volume_name] * raw["close"]
    bucket = raw["timestamp"] // DAY_MS
    starts = np.concatenate(([0], np.nonzero(np.diff(bucket))[0] + 1))
    ends = np.append(starts[1:], len(raw["timestamp"])) - 1
    return {
        "timestamp": bucket[starts] * DAY_MS,
        "open": raw["open"][starts],
        "high": np.maximum.reduceat(raw["high"], starts),
        "low": np.minimum.reduceat(raw["low"], starts),
        "close": raw["close"][ends],
        "quote_volume": np.add.reduceat(quote_volume, starts),
    }, volume_basis


def _continuation_outcome(
    *,
    timestamp: np.ndarray,
    close: np.ndarray,
    anomaly_high: float,
    hold_floor: float,
    first_future_index: int,
    final_future_index: int,
) -> tuple[str, int | None]:
    """Label the first observed competing continuation outcome.

    A daily close above the anomaly high is a durable new-range exit.  A close
    below the hold floor is a fade.  If neither happens by the declared
    horizon, the event is deliberately left unresolved rather than forcing a
    binary label.  This helper is called only for retrospective outcome fields;
    none of its result participates in cohort admission or candidate scoring.
    """

    if first_future_index > final_future_index:
        return "incomplete", None
    for index in range(first_future_index, final_future_index + 1):
        if float(close[index]) > anomaly_high:
            return "new_range", int(timestamp[index] + DAY_MS)
        if float(close[index]) < hold_floor:
            return "fade", int(timestamp[index] + DAY_MS)
    return "unresolved", None


def _top_hold_candle(
    *,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    anomaly_low: float,
    anomaly_high: float,
    policy: DailyAnomalyHoldPolicy,
) -> tuple[bool, float, float, float, float]:
    """Assess one post-pump candle against the declared top-hold contract.

    A close alone is insufficient.  A valid hold must be a compact pause after
    the anomaly: its close stays near the top, its low makes a bounded retrace
    (at least 5%, but no more than 50%, of the anomaly range), and its high
    never creates a new directional leg.  All positions are measured from the
    anomaly low in units of the anomaly range.
    """

    anomaly_range = anomaly_high - anomaly_low
    candle_range = high_price - low_price
    if anomaly_range <= 0.0 or candle_range <= 0.0:
        return False, np.nan, np.nan, np.nan, np.nan
    close_position = (close_price - anomaly_low) / anomaly_range
    low_position = (low_price - anomaly_low) / anomaly_range
    high_position = (high_price - anomaly_low) / anomaly_range
    upper_wick_share = (high_price - max(open_price, close_price)) / candle_range
    accepted = (
        close_position >= policy.min_hold_close_position
        and close_position <= policy.max_hold_close_position
        and low_position >= policy.min_hold_low_position
        and low_position <= policy.max_hold_low_position
        and high_position <= policy.max_hold_high_position
        and upper_wick_share <= policy.max_hold_upper_wick_share
    )
    return (
        accepted,
        float(close_position),
        float(low_position),
        float(high_position),
        float(upper_wick_share),
    )


def _sleep_to_volume_pump(
    *,
    close: np.ndarray,
    volume: np.ndarray,
    pump_start_idx: int,
    policy: DailyAnomalyHoldPolicy,
) -> tuple[bool, float, float, tuple[float, ...], tuple[float, ...]]:
    """Recognise a causal two-day ``sleep -> volume pump`` transition.

    The sleep baseline is fixed before the first pump candle.  Therefore the
    second pump candle can confirm the proposal, but no later hold/outcome data
    can alter either the volume or directional admission decision.
    """

    history_start = pump_start_idx - policy.sleep_history_days
    pump_end_idx = pump_start_idx + policy.pump_candles - 1
    if history_start < 0 or pump_end_idx >= len(close):
        return False, np.nan, np.nan, (), ()
    sleep_median_volume = float(np.median(volume[history_start:pump_start_idx]))
    if not np.isfinite(sleep_median_volume) or sleep_median_volume <= 0.0:
        return False, np.nan, np.nan, (), ()
    pre_pump_volume_ratio = float(volume[pump_start_idx - 1] / sleep_median_volume)
    pre_pump_return = float(close[pump_start_idx - 1] / close[pump_start_idx - 2] - 1.0)
    candle_returns = tuple(
        float(close[idx] / close[idx - 1] - 1.0)
        for idx in range(pump_start_idx, pump_end_idx + 1)
        if close[idx - 1] > 0.0
    )
    volume_ratios = tuple(
        float(volume[idx] / sleep_median_volume)
        for idx in range(pump_start_idx, pump_end_idx + 1)
    )
    if len(candle_returns) != policy.pump_candles:
        return False, pre_pump_volume_ratio, pre_pump_return, candle_returns, volume_ratios
    total_return = float(close[pump_end_idx] / close[pump_start_idx - 1] - 1.0)
    # Do not slide a two-day window into the middle of an already active pump.
    # The previous day may have ordinary volume, but an upward anomalous-volume
    # day marks an earlier causal onset and this suffix is not a new event.
    already_in_active_pump = (
        pre_pump_volume_ratio >= policy.min_pump_volume_over_sleep_median
        and pre_pump_return >= policy.min_pump_candle_close_to_close_pct
    )
    accepted = (
        not already_in_active_pump
        and all(value >= policy.min_pump_candle_close_to_close_pct for value in candle_returns)
        and total_return >= policy.min_pump_total_close_to_close_pct
        and all(value >= policy.min_pump_volume_over_sleep_median for value in volume_ratios)
    )
    return accepted, pre_pump_volume_ratio, pre_pump_return, candle_returns, volume_ratios


def _event_rows(symbol: str, daily: dict[str, np.ndarray], policy: DailyAnomalyHoldPolicy = POLICY) -> list[dict]:
    """Return multi-timeframe cohort views with a fixed post-hold horizon."""

    ts = np.asarray(daily["timestamp"], dtype=np.int64)
    open_price = np.asarray(daily["open"], dtype=np.float64)
    high = np.asarray(daily["high"], dtype=np.float64)
    low = np.asarray(daily["low"], dtype=np.float64)
    close = np.asarray(daily["close"], dtype=np.float64)
    volume = np.asarray(daily["quote_volume"], dtype=np.float64)
    rows: list[dict] = []
    for pump_start_idx in range(policy.sleep_history_days, len(ts) - policy.pump_candles):
        pump_end_idx = pump_start_idx + policy.pump_candles - 1
        pump_low = float(np.min(low[pump_start_idx : pump_end_idx + 1]))
        pump_high = float(np.max(high[pump_start_idx : pump_end_idx + 1]))
        if pump_low <= 0.0 or pump_high <= pump_low:
            continue
        (
            accepted_pump,
            pre_pump_volume_ratio,
            pre_pump_return,
            pump_returns,
            pump_volume_ratios,
        ) = _sleep_to_volume_pump(
            close=close,
            volume=volume,
            pump_start_idx=pump_start_idx,
            policy=policy,
        )
        if not accepted_pump:
            continue
        total_pump_return = float(close[pump_end_idx] / close[pump_start_idx - 1] - 1.0)

        hold_floor = pump_low + policy.min_hold_close_position * (pump_high - pump_low)
        hold_end_idx: int | None = None
        hold_close_positions: list[float] = []
        hold_low_positions: list[float] = []
        hold_high_positions: list[float] = []
        hold_upper_wick_shares: list[float] = []
        for offset in range(1, policy.max_hold_candles + 1):
            hold_idx = pump_end_idx + offset
            if hold_idx >= len(ts):
                break
            accepted, close_position, low_position, high_position, upper_wick_share = _top_hold_candle(
                open_price=float(open_price[hold_idx]),
                high_price=float(high[hold_idx]),
                low_price=float(low[hold_idx]),
                close_price=float(close[hold_idx]),
                anomaly_low=pump_low,
                anomaly_high=pump_high,
                policy=policy,
            )
            if not accepted:
                break
            hold_end_idx = hold_idx
            hold_close_positions.append(close_position)
            hold_low_positions.append(low_position)
            hold_high_positions.append(high_position)
            hold_upper_wick_shares.append(upper_wick_share)
        if hold_end_idx is None:
            continue

        next_day_close = float(close[pump_end_idx + 1])
        hold_position_pct = hold_close_positions[0]

        pump_start_ms = int(ts[pump_start_idx])
        pump_end_ms = int(ts[pump_end_idx])
        confirmation_end_ms = int(ts[hold_end_idx] + DAY_MS)
        first_future_idx = hold_end_idx + 1
        final_future_idx = min(len(ts) - 1, first_future_idx + policy.post_hold_observation_days - 1)
        review_end_ms = int(ts[final_future_idx] + DAY_MS) if first_future_idx <= final_future_idx else confirmation_end_ms
        outcome, outcome_time_ms = _continuation_outcome(
            timestamp=ts,
            close=close,
            anomaly_high=pump_high,
            hold_floor=hold_floor,
            first_future_index=first_future_idx,
            final_future_index=final_future_idx,
        )
        complete_horizon = final_future_idx - first_future_idx + 1 == policy.post_hold_observation_days
        common = {
            "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
            "annotation_group_id": _event_id(symbol, pump_start_ms),
            "symbol": symbol,
            "review_end_ms": review_end_ms,
            "anchor_time_ms": pump_start_ms,
            # Point-in-time cutoff: the two-day pump and hold-day close are
            # known when this cohort member enters the continuation study.
            "feature_cutoff_time_ms": confirmation_end_ms,
            "proposal_time_ms": confirmation_end_ms,
            "pump_start_ms": pump_start_ms,
            "culmination_ms": pump_end_ms,
            "pump_low_price": pump_low,
            "culmination_price": pump_high,
            "pump_pct": total_pump_return,
            "pump_bars": policy.pump_candles,
            "pump_hours": float(24 * policy.pump_candles),
            "pump_over_sleep_vol": float(min(pump_volume_ratios)),
            "score": float(total_pump_return * np.log1p(min(pump_volume_ratios))),
            "daily_pump_start_ms": pump_start_ms,
            "daily_pump_end_ms": pump_end_ms,
            "daily_pump_candle_count": policy.pump_candles,
            "daily_pump_total_close_to_close_pct": total_pump_return,
            "daily_pump_first_close_to_close_pct": pump_returns[0],
            "daily_pump_second_close_to_close_pct": pump_returns[1],
            "daily_pump_first_volume_ratio": pump_volume_ratios[0],
            "daily_pump_second_volume_ratio": pump_volume_ratios[1],
            "daily_pre_pump_volume_ratio": pre_pump_volume_ratio,
            "daily_pre_pump_close_to_close_pct": pre_pump_return,
            "daily_anomaly_low": pump_low,
            "daily_anomaly_high": pump_high,
            "daily_hold_floor": float(hold_floor),
            "daily_next_close": next_day_close,
            "daily_next_close_position_pct": float(hold_position_pct),
            "daily_hold_candle_count": len(hold_close_positions),
            "daily_hold_end_ms": confirmation_end_ms,
            "daily_hold_min_close_position": float(min(hold_close_positions)),
            "daily_hold_min_low_position": float(min(hold_low_positions)),
            "daily_hold_max_low_position": float(max(hold_low_positions)),
            "daily_hold_max_high_position": float(max(hold_high_positions)),
            "daily_hold_max_upper_wick_share": float(max(hold_upper_wick_shares)),
            "daily_hold_policy_version": policy.version,
            "post_hold_observation_start_ms": confirmation_end_ms,
            "post_hold_observation_end_ms": review_end_ms,
            "post_hold_observation_days": policy.post_hold_observation_days,
            "post_hold_observation_complete": complete_horizon,
            "continuation_search_tfs": list(policy.continuation_tfs[1:]),
            # Retrospective labels: visible for cohort analysis only.  They are
            # explicitly separate from the causal fields above.
            "outcome_label": outcome,
            "outcome_time_ms": outcome_time_ms,
            "outcome_label_horizon_days": policy.post_hold_observation_days,
            "read_only": True,
        }
        for tf in policy.continuation_tfs:
            context_days = policy.daily_context_days if tf == "1d" else policy.intraday_context_days
            row = dict(common)
            row.update(
                {
                    "event_id": _event_id(symbol, pump_start_ms) + f"_{tf}",
                    "tf": tf,
                    "review_start_ms": int(ts[max(0, pump_start_idx - context_days)]),
                }
            )
            rows.append(row)
    return rows


def build_daily_anomaly_hold_candidates(
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    policy: DailyAnomalyHoldPolicy = POLICY,
) -> pd.DataFrame:
    """Build the view-only continuation cohort without outcome-based filtering."""

    rows: list[dict] = []
    full_start_ms: int | None = None
    full_end_ms: int | None = None
    for path in sorted(cache_dir.glob("*.parquet")):
        daily, volume_basis = _load_daily_bars(path)
        if len(daily["timestamp"]):
            full_start_ms = int(daily["timestamp"][0]) if full_start_ms is None else min(full_start_ms, int(daily["timestamp"][0]))
            full_end_ms = int(daily["timestamp"][-1]) if full_end_ms is None else max(full_end_ms, int(daily["timestamp"][-1]))
        for row in _event_rows(path.stem, daily, policy):
            row["daily_volume_basis"] = volume_basis
            rows.append(row)
    candidates = pd.DataFrame(rows)
    if candidates.empty:
        raise ValueError("daily anomaly-hold scanner found no events under the configured policy")
    if full_start_ms is None or full_end_ms is None:
        raise ValueError("daily anomaly-hold scanner found no market calendar")
    holdout_start_ms = full_end_ms - (policy.final_holdout_days - 1) * DAY_MS
    # The desk never receives a chart that reaches into the final holdout.  A
    # candidate's review window is itself an information read, so proposal time
    # alone is insufficient to protect the locked period.
    candidates = candidates.loc[candidates["review_end_ms"] <= holdout_start_ms].copy()
    if candidates.empty:
        raise ValueError("all daily anomaly-hold candidates overlap the final locked holdout")
    candidates["research_partition"] = "is_discovery"
    candidates["final_holdout_start_ms"] = holdout_start_ms
    candidates["final_holdout_excluded"] = True
    candidates = candidates.sort_values(["daily_pump_start_ms", "symbol", "tf"]).reset_index(drop=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_date = datetime.fromtimestamp(full_start_ms / 1_000, UTC).date()
    end_date = datetime.fromtimestamp(full_end_ms / 1_000, UTC).date()
    governance_dir = run_mvp1_holdout_governance(
        out_dir=output_dir / "governance",
        start_date=start_date,
        end_date=end_date,
        protocol_freeze_id=policy.version,
        holdout_days=policy.final_holdout_days,
        research_mode="is",
    )
    (output_dir / "research_partition.json").write_text(json.dumps({
        "policy_version": policy.version, "research_partition": "is_discovery",
        "final_holdout_start_ms": holdout_start_ms, "final_holdout_days": policy.final_holdout_days,
        "governance_dir": str(governance_dir), "candidate_review_end_max_ms": int(candidates["review_end_ms"].max()),
    }, indent=2, sort_keys=True), encoding="utf-8")
    candidates.to_parquet(output_dir / "candidates.parquet", index=False)
    # The desk is view-only for this workflow, but it requires a durable label
    # store path for its shared transport contract.
    (output_dir / "view_state.jsonl").touch(exist_ok=True)
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Build daily anomaly-hold continuation cohort views.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    candidates = build_daily_anomaly_hold_candidates(cache_dir=args.cache_dir, output_dir=args.output_dir)
    print(f"daily anomaly-hold cohort events={len(candidates) // len(POLICY.continuation_tfs)} -> {args.output_dir}")


if __name__ == "__main__":
    main()
