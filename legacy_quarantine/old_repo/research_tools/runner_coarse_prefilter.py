"""Coarse data-loading prefilters for runner discovery.

The functions in this module are not trading rules. They may reject a fetch
window only when coarse candles prove that no exact LTF snapshot inside the
window can pass the shared decision core's known-at-decision prerequisites.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
import pandas as pd

from research_tools.pump_decision_core import DecisionSnapshot, evaluate_rolling_seed_stage


MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class MinuteCoarseFrame:
    timestamps: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    quote_volumes: np.ndarray
    number_of_trades: np.ndarray


@dataclass(frozen=True, slots=True)
class CoarsePrefilterVerdict:
    possible: bool
    reason: str
    bounds: dict[str, object]


@dataclass(frozen=True, slots=True)
class _WindowUpperStats:
    complete: bool
    quote: float = float("nan")
    trades: float = float("nan")
    high: float = float("nan")
    open_floor: float = float("nan")


def prepare_minute_coarse_frame(frame: pd.DataFrame) -> MinuteCoarseFrame | None:
    required = {"timestamp", "open", "high", "low", "quote_volume", "number_of_trades"}
    if frame.empty or not required.issubset(frame.columns):
        return None
    prepared = frame.copy()
    for column in required:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=list(required)).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    if prepared.empty:
        return None
    return MinuteCoarseFrame(
        timestamps=prepared["timestamp"].astype("int64").to_numpy(),
        opens=prepared["open"].astype(float).to_numpy(),
        highs=prepared["high"].astype(float).to_numpy(),
        lows=prepared["low"].astype(float).to_numpy(),
        quote_volumes=prepared["quote_volume"].astype(float).to_numpy(),
        number_of_trades=prepared["number_of_trades"].astype(float).to_numpy(),
    )


def coarse_minute_pair_prefilter(
    frame: MinuteCoarseFrame | None,
    *,
    pair_start_ms: int,
    htf_ms: int,
    ltf_ms: int,
    min_seed_return_pct: float,
    min_seed_quote_ratio: float,
    min_seed_trade_ratio: float,
    min_confirm_return_pct: float,
    min_confirm_quote_pace_ratio: float,
    min_confirm_trade_pace_ratio: float,
    min_confirm_candles: int,
    max_confirm_candles: int,
    baseline_quote_values: Iterable[float],
    baseline_trade_values: Iterable[float],
) -> CoarsePrefilterVerdict:
    model = "coarse_1m_seed_confirm_upper_bound_data_loading_only"
    base_bounds: dict[str, object] = {
        "coarse_prefilter_model": model,
        "coarse_prefilter_trading_signal": False,
    }
    if frame is None or len(frame.timestamps) == 0:
        return CoarsePrefilterVerdict(True, "not_checked_missing_1m", {**base_bounds, "coarse_prefilter_reason": "not_checked_missing_1m"})
    min_baseline_quote = _min_positive(baseline_quote_values)
    min_baseline_trades = _min_positive(baseline_trade_values)
    if not math.isfinite(min_baseline_quote) or not math.isfinite(min_baseline_trades):
        return CoarsePrefilterVerdict(
            True,
            "not_checked_missing_baseline",
            {**base_bounds, "coarse_prefilter_reason": "not_checked_missing_baseline"},
        )

    starts = list(range(int(pair_start_ms), int(pair_start_ms) + int(htf_ms) + 1, int(ltf_ms)))
    if not starts:
        return CoarsePrefilterVerdict(True, "not_checked_no_candidate_starts", {**base_bounds, "coarse_prefilter_reason": "not_checked_no_candidate_starts"})

    min_duration_ms = max(1, int(min_confirm_candles) * int(ltf_ms))
    max_confirm_ms = max(1, int(max_confirm_candles) * int(ltf_ms))
    best: dict[str, float] = {
        "seed_return": float("nan"),
        "seed_quote_ratio": float("nan"),
        "seed_trade_ratio": float("nan"),
        "confirm_return": float("nan"),
        "confirm_quote_pace": float("nan"),
        "confirm_trade_pace": float("nan"),
    }
    rejection_counts = {
        "impossible_minute_seed_return": 0,
        "impossible_minute_seed_quote_ratio": 0,
        "impossible_minute_seed_trade_ratio": 0,
        "impossible_minute_confirm_return": 0,
        "impossible_minute_confirm_quote_pace": 0,
        "impossible_minute_confirm_trade_pace": 0,
    }

    for seed_start in starts:
        seed = _window_upper_stats(frame, start_ms=int(seed_start), end_exclusive_ms=int(seed_start) + int(htf_ms))
        if not seed.complete:
            return CoarsePrefilterVerdict(
                True,
                "not_checked_incomplete_1m_coverage",
                {**base_bounds, "coarse_prefilter_reason": "not_checked_incomplete_1m_coverage"},
            )
        seed_return = _safe_divide(seed.high - seed.open_floor, seed.open_floor)
        seed_quote_ratio = _safe_divide(seed.quote, min_baseline_quote)
        seed_trade_ratio = _safe_divide(seed.trades, min_baseline_trades)
        _max_into(best, "seed_return", seed_return)
        _max_into(best, "seed_quote_ratio", seed_quote_ratio)
        _max_into(best, "seed_trade_ratio", seed_trade_ratio)
        if not math.isfinite(seed_return) or seed_return < float(min_seed_return_pct):
            rejection_counts["impossible_minute_seed_return"] += 1
            continue
        if not math.isfinite(seed_quote_ratio) or seed_quote_ratio < float(min_seed_quote_ratio):
            rejection_counts["impossible_minute_seed_quote_ratio"] += 1
            continue
        if not math.isfinite(seed_trade_ratio) or seed_trade_ratio < float(min_seed_trade_ratio):
            rejection_counts["impossible_minute_seed_trade_ratio"] += 1
            continue

        confirm_start = int(seed_start) + int(htf_ms)
        confirm = _window_upper_stats(frame, start_ms=confirm_start, end_exclusive_ms=confirm_start + max_confirm_ms)
        if not confirm.complete:
            return CoarsePrefilterVerdict(
                True,
                "not_checked_incomplete_1m_coverage",
                {**base_bounds, "coarse_prefilter_reason": "not_checked_incomplete_1m_coverage"},
            )
        confirm_return = _safe_divide(confirm.high - confirm.open_floor, confirm.open_floor)
        confirm_quote_pace = _safe_divide(confirm.quote, min_baseline_quote * min_duration_ms / int(htf_ms))
        confirm_trade_pace = _safe_divide(confirm.trades, min_baseline_trades * min_duration_ms / int(htf_ms))
        _max_into(best, "confirm_return", confirm_return)
        _max_into(best, "confirm_quote_pace", confirm_quote_pace)
        _max_into(best, "confirm_trade_pace", confirm_trade_pace)
        if not math.isfinite(confirm_return) or confirm_return < float(min_confirm_return_pct):
            rejection_counts["impossible_minute_confirm_return"] += 1
            continue
        if not math.isfinite(confirm_quote_pace) or confirm_quote_pace < float(min_confirm_quote_pace_ratio):
            rejection_counts["impossible_minute_confirm_quote_pace"] += 1
            continue
        if not math.isfinite(confirm_trade_pace) or confirm_trade_pace < float(min_confirm_trade_pace_ratio):
            rejection_counts["impossible_minute_confirm_trade_pace"] += 1
            continue

        return CoarsePrefilterVerdict(
            True,
            "possible_by_1m_bounds",
            {
                **base_bounds,
                "coarse_prefilter_reason": "possible_by_1m_bounds",
                "coarse_prefilter_candidate_starts_checked": int(len(starts)),
                **_best_bounds(best),
                **{f"coarse_{key}_count": int(value) for key, value in rejection_counts.items()},
            },
        )

    reason = _dominant_reason(rejection_counts)
    return CoarsePrefilterVerdict(
        False,
        reason,
        {
            **base_bounds,
            "coarse_prefilter_reason": reason,
            "coarse_prefilter_candidate_starts_checked": int(len(starts)),
            **_best_bounds(best),
            **{f"coarse_{key}_count": int(value) for key, value in rejection_counts.items()},
        },
    )


def core_seed_stage_prefilter(snapshot: DecisionSnapshot) -> CoarsePrefilterVerdict:
    """Use the shared core to decide whether confirm/entry LTF must be fetched.

    This is a data-loading guard, not a trade signal. It rejects only terminal
    seed-stage failures computed by the shared decision core from seed/context
    data already known at seed close. Dependency/unknown states remain possible.
    """

    model = "shared_core_seed_stage_terminal_reject_data_loading_only"
    verdict = evaluate_rolling_seed_stage(snapshot)
    if verdict.rejects:
        reason = _seed_stage_reason(verdict.rejects[0].reason)
    elif verdict.dependencies:
        reason = _seed_stage_reason(verdict.dependencies[0].reason or verdict.dependencies[0].name)
    else:
        reason = _seed_stage_reason(verdict.verdict)
    bounds: dict[str, object] = {
        "seed_stage_prefilter_model": model,
        "seed_stage_prefilter_trading_signal": False,
        "seed_stage_prefilter_signal_verdict": verdict.verdict,
        "seed_stage_prefilter_reason": reason,
        "seed_stage_prefilter_stage": str(verdict.rejects[0].stage) if verdict.rejects else "",
        "seed_stage_snapshot_hash": verdict.features.get("snapshot_hash", ""),
    }
    for key, value in verdict.features.items():
        if str(key).startswith(("htf_", "pregrowth_", "pre_seed_", "dormancy_", "baseline_", "prior_spike_")):
            bounds[f"seed_stage_{key}"] = value
    if verdict.verdict == "rejected" and any(str(reject.stage) == "rolling_htf_seed" for reject in verdict.rejects):
        return CoarsePrefilterVerdict(False, reason, bounds)
    if verdict.verdict == "data_dependency_not_ready":
        return CoarsePrefilterVerdict(True, reason, bounds)
    return CoarsePrefilterVerdict(True, "seed_stage_passed", {**bounds, "seed_stage_prefilter_reason": "seed_stage_passed"})


def _window_upper_stats(frame: MinuteCoarseFrame, *, start_ms: int, end_exclusive_ms: int) -> _WindowUpperStats:
    cover_start = _floor_minute(int(start_ms))
    cover_end = _ceil_minute(int(end_exclusive_ms))
    if cover_end <= cover_start:
        return _WindowUpperStats(False)
    left = int(np.searchsorted(frame.timestamps, cover_start, side="left"))
    right = int(np.searchsorted(frame.timestamps, cover_end, side="left"))
    expected = int((cover_end - cover_start) // MINUTE_MS)
    if right - left != expected:
        return _WindowUpperStats(False)
    timestamps = frame.timestamps[left:right]
    if len(timestamps) != expected:
        return _WindowUpperStats(False)
    expected_ts = cover_start + np.arange(expected, dtype=np.int64) * MINUTE_MS
    if not np.array_equal(timestamps, expected_ts):
        return _WindowUpperStats(False)
    if expected <= 0:
        return _WindowUpperStats(False)
    return _WindowUpperStats(
        True,
        quote=float(np.nansum(frame.quote_volumes[left:right])),
        trades=float(np.nansum(frame.number_of_trades[left:right])),
        high=float(np.nanmax(frame.highs[left:right])),
        open_floor=float(np.nanmin(np.minimum(frame.opens[left:right], frame.lows[left:right]))),
    )


def _floor_minute(value: int) -> int:
    return int(value) - (int(value) % MINUTE_MS)


def _ceil_minute(value: int) -> int:
    value = int(value)
    return value if value % MINUTE_MS == 0 else value + (MINUTE_MS - value % MINUTE_MS)


def _min_positive(values: Iterable[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value)) and float(value) > 0.0]
    return min(finite) if finite else float("nan")


def _safe_divide(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator == 0.0:
        return float("nan")
    return numerator / denominator


def _max_into(target: dict[str, float], key: str, value: float) -> None:
    if math.isfinite(value) and (not math.isfinite(target[key]) or value > target[key]):
        target[key] = float(value)


def _best_bounds(best: dict[str, float]) -> dict[str, float]:
    return {
        "coarse_seed_return_upper_bound": float(best["seed_return"]),
        "coarse_seed_quote_ratio_upper_bound": float(best["seed_quote_ratio"]),
        "coarse_seed_trade_ratio_upper_bound": float(best["seed_trade_ratio"]),
        "coarse_confirm_return_upper_bound": float(best["confirm_return"]),
        "coarse_confirm_quote_pace_upper_bound": float(best["confirm_quote_pace"]),
        "coarse_confirm_trade_pace_upper_bound": float(best["confirm_trade_pace"]),
    }


def _dominant_reason(counts: dict[str, int]) -> str:
    nonzero = [(count, reason) for reason, count in counts.items() if count > 0]
    if not nonzero:
        return "impossible_minute_seed_or_confirm_bounds"
    return max(nonzero)[1]


def _seed_stage_reason(value: object) -> str:
    text = str(value or "").strip()
    return text or "seed_stage_unknown"
