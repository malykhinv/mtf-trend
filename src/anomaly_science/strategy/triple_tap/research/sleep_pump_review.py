"""High-recall, outcome-free discovery population for sleep → pump labeling.

The review task is deliberately narrower than a trade setup.  A row is only a
plausible transition hypothesis: the expert may keep its pump, redraw it, or
reject the event.  No level, breakout, CAP, EV, or forward-return rule enters
candidate selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.desk.labels import LabelStore
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION, LEVEL_LABEL_SCHEMA_VERSION
from anomaly_science.strategy.pump_long.research.context import DEV_END_MS
from anomaly_science.strategy.triple_tap.detect import CACHE_1M


REVIEW_DIR = Path(".output/results/triple_tap_v1/sleep_pump_review")
DEFAULT_TFS = ("3m",)
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "1h": 60, "4h": 240}

# Discovery policy: intentionally broad, with only enough structure to make a
# human review card meaningful.  These are not a trading admission rule.
SLEEP_BARS = 120
MIN_SLEEP_BARS = 48
MAX_PUMP_BARS = 96
MIN_BASE_TO_HIGH_PCT = 0.08
MAX_SLEEP_RANGE_PCT = 0.25
LOCAL_HIGH_LOOKBACK = 8
CONFIRMATION_BARS = 4
POST_BARS = 48
LEFT_CONTEXT_BARS = 120
DEDUP_MS = 12 * 3_600_000


@dataclass(frozen=True, slots=True)
class SleepPumpCandidate:
    candidate_schema_version: str
    event_id: str
    symbol: str
    tf: str
    review_start_ms: int
    review_end_ms: int
    anchor_time_ms: int
    proposal_time_ms: int
    pump_start_ms: int
    culmination_ms: int
    pump_low_price: float
    culmination_price: float
    pump_pct: float
    pump_bars: int
    pump_hours: float
    sleep_range_pct: float
    sleep_directional_drift_pct: float
    sleep_base_price: float
    base_to_high_pct: float
    pump_path_efficiency: float
    pump_retrace_share: float
    pump_single_bar_range_share: float
    pump_upper_wick_share: float
    pump_over_sleep_vol: float
    pump_over_sleep_trades: float
    score: float


def _event_id(symbol: str, tf: str, pump_start_ms: int, culmination_ms: int) -> str:
    raw = f"sleep_pump_v1|{symbol}|{tf}|{pump_start_ms}|{culmination_ms}".encode("utf-8")
    return "sleeppump_" + hashlib.blake2b(raw, digest_size=10).hexdigest()


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if np.isfinite(numerator) and np.isfinite(denominator) and denominator > 0 else np.nan


def _pump_shape_features(
    *,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    sleep_start_idx: int,
    pump_start_idx: int,
    culmination_idx: int,
    pump_low_price: float,
    culmination_price: float,
) -> tuple[float, float, float, float, float]:
    """Candidate-time shape measurements; no review or future candles enter."""

    sleep_open = float(close[sleep_start_idx])
    sleep_last = float(close[pump_start_idx - 1])
    sleep_drift = abs(sleep_last - sleep_open) / sleep_open if sleep_open > 0.0 else np.nan
    pump_close = close[pump_start_idx : culmination_idx + 1]
    pump_rise = culmination_price - pump_low_price
    path = float(np.sum(np.abs(np.diff(pump_close))))
    path_efficiency = (float(pump_close[-1]) - float(pump_close[0])) / path if path > 0.0 else 0.0
    running_high = np.maximum.accumulate(pump_close)
    retrace = float(np.max(running_high - pump_close)) / pump_rise if pump_rise > 0.0 else np.inf
    pump_high = high[pump_start_idx : culmination_idx + 1]
    pump_low = low[pump_start_idx : culmination_idx + 1]
    pump_open = open_[pump_start_idx : culmination_idx + 1]
    candle_range = pump_high - pump_low
    single_bar_range = float(np.max(candle_range) / pump_rise) if pump_rise > 0.0 else np.inf
    upper_wick = pump_high - np.maximum(pump_open, pump_close)
    valid_range = candle_range > 0.0
    upper_wick_share = float(np.max(upper_wick[valid_range] / candle_range[valid_range])) if np.any(valid_range) else 0.0
    return sleep_drift, path_efficiency, retrace, single_bar_range, upper_wick_share


@njit(cache=True)
def _proposal_indices(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    end_idx: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Find broad candidate start/high pairs in one linear compiled pass."""

    starts = np.empty(end_idx, np.int64)
    culminations = np.empty(end_idx, np.int64)
    count = 0
    first = SLEEP_BARS + MIN_SLEEP_BARS
    final = end_idx - CONFIRMATION_BARS
    for culmination_idx in range(first, final):
        local_max = high[culmination_idx]
        for j in range(culmination_idx - LOCAL_HIGH_LOOKBACK, culmination_idx):
            if high[j] > local_max:
                local_max = high[j]
        if high[culmination_idx] < local_max:
            continue
        confirmation_max = high[culmination_idx]
        for j in range(culmination_idx + 1, culmination_idx + 1 + CONFIRMATION_BARS):
            if high[j] > confirmation_max:
                confirmation_max = high[j]
        if high[culmination_idx] < confirmation_max:
            continue
        search_start = culmination_idx - MAX_PUMP_BARS
        if search_start < SLEEP_BARS:
            search_start = SLEEP_BARS
        pump_start_idx = search_start
        for j in range(search_start + 1, culmination_idx + 1):
            if low[j] < low[pump_start_idx]:
                pump_start_idx = j
        if pump_start_idx >= culmination_idx:
            continue
        sleep_start = pump_start_idx - SLEEP_BARS
        sleep_low = low[sleep_start]
        sleep_high = high[sleep_start]
        sleep_sum = 0.0
        for j in range(sleep_start, pump_start_idx):
            if low[j] < sleep_low:
                sleep_low = low[j]
            if high[j] > sleep_high:
                sleep_high = high[j]
            sleep_sum += close[j]
        sleep_mean = sleep_sum / SLEEP_BARS
        if sleep_low <= 0.0:
            continue
        if (sleep_high - sleep_low) / sleep_low > MAX_SLEEP_RANGE_PCT:
            continue
        if sleep_mean <= 0.0 or (high[culmination_idx] - sleep_mean) / sleep_mean < MIN_BASE_TO_HIGH_PCT:
            continue
        starts[count] = pump_start_idx
        culminations[count] = culmination_idx
        count += 1
    return starts[:count], culminations[:count]


def _scan_symbol_tf(
    symbol: str,
    base: dict[str, np.ndarray],
    tf: str,
    *,
    end_ms: int,
    max_per_symbol_tf: int,
) -> list[SleepPumpCandidate]:
    minutes = TF_MINUTES[tf]
    frame = base if minutes == 1 else resample_ohlcv_np(base, minutes)
    ts = frame["timestamp"]
    high = frame["high"]
    low = frame["low"]
    open_ = frame["open"]
    close = frame["close"]
    qv = frame["quote_volume"]
    tc = frame["trade_count"]
    n = len(ts)
    if n < SLEEP_BARS + MAX_PUMP_BARS + CONFIRMATION_BARS + POST_BARS:
        return []
    stop = min(int(np.searchsorted(ts, end_ms, side="left")), n - CONFIRMATION_BARS - POST_BARS)
    proposals: list[SleepPumpCandidate] = []
    start_indices, culmination_indices = _proposal_indices(high, low, close, stop)
    for pump_start_idx, culmination_idx in zip(start_indices, culmination_indices, strict=True):
        pump_start_idx = int(pump_start_idx)
        culmination_idx = int(culmination_idx)
        sleep_start = max(0, pump_start_idx - SLEEP_BARS)
        if pump_start_idx - sleep_start < MIN_SLEEP_BARS:
            continue
        sleep_low = float(np.min(low[sleep_start:pump_start_idx]))
        sleep_high = float(np.max(high[sleep_start:pump_start_idx]))
        sleep_range = (sleep_high - sleep_low) / sleep_low if sleep_low > 0 else np.nan
        if not (np.isfinite(sleep_range) and sleep_range <= MAX_SLEEP_RANGE_PCT):
            continue
        sleep_base = float(np.median(close[sleep_start:pump_start_idx]))
        culmination = float(high[culmination_idx])
        base_to_high = (culmination - sleep_base) / sleep_base if sleep_base > 0 else np.nan
        if not (np.isfinite(base_to_high) and base_to_high >= MIN_BASE_TO_HIGH_PCT):
            continue
        pump_low = float(low[pump_start_idx])
        pump_pct = (culmination - pump_low) / pump_low if pump_low > 0 else np.nan
        sleep_drift, path_efficiency, retrace_share, single_bar_range_share, upper_wick_share = _pump_shape_features(
            open_=open_,
            high=high,
            low=low,
            close=close,
            sleep_start_idx=sleep_start,
            pump_start_idx=pump_start_idx,
            culmination_idx=culmination_idx,
            pump_low_price=pump_low,
            culmination_price=culmination,
        )
        sleep_vol = float(np.mean(qv[sleep_start:pump_start_idx]))
        pump_vol = float(np.mean(qv[pump_start_idx : culmination_idx + 1]))
        sleep_trades = float(np.mean(tc[sleep_start:pump_start_idx]))
        pump_trades = float(np.mean(tc[pump_start_idx : culmination_idx + 1]))
        vol_step = _ratio(pump_vol, sleep_vol)
        trade_step = _ratio(pump_trades, sleep_trades)
        review_start = int(ts[max(0, pump_start_idx - LEFT_CONTEXT_BARS)])
        review_end = int(ts[min(n - 1, culmination_idx + POST_BARS)])
        proposals.append(
            SleepPumpCandidate(
                candidate_schema_version=ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                event_id=_event_id(symbol, tf, int(ts[pump_start_idx]), int(ts[culmination_idx])),
                symbol=symbol,
                tf=tf,
                review_start_ms=review_start,
                review_end_ms=review_end,
                anchor_time_ms=int(ts[culmination_idx]),
                proposal_time_ms=int(ts[culmination_idx + CONFIRMATION_BARS]),
                pump_start_ms=int(ts[pump_start_idx]),
                culmination_ms=int(ts[culmination_idx]),
                pump_low_price=pump_low,
                culmination_price=culmination,
                pump_pct=float(pump_pct),
                pump_bars=int(culmination_idx - pump_start_idx),
                pump_hours=float((culmination_idx - pump_start_idx) * minutes / 60),
                sleep_range_pct=float(sleep_range),
                sleep_directional_drift_pct=float(sleep_drift),
                sleep_base_price=sleep_base,
                base_to_high_pct=float(base_to_high),
                pump_path_efficiency=float(path_efficiency),
                pump_retrace_share=float(retrace_share),
                pump_single_bar_range_share=float(single_bar_range_share),
                pump_upper_wick_share=float(upper_wick_share),
                pump_over_sleep_vol=float(vol_step),
                pump_over_sleep_trades=float(trade_step),
                # Score orders review cards only; no outcome is used.
                score=float(base_to_high),
            )
        )
    proposals.sort(key=lambda row: (row.score, row.base_to_high_pct), reverse=True)
    selected: list[SleepPumpCandidate] = []
    selected_times: list[int] = []
    for proposal in proposals:
        if any(abs(proposal.culmination_ms - prior) < DEDUP_MS for prior in selected_times):
            continue
        selected.append(proposal)
        selected_times.append(proposal.culmination_ms)
        if len(selected) >= max_per_symbol_tf:
            break
    return selected


def _seed_mark(candidate: SleepPumpCandidate) -> dict:
    return {
        "event_id": candidate.event_id,
        "symbol": candidate.symbol,
        "tf": candidate.tf,
        "source_event_id": candidate.event_id,
        "source_event_ids": [candidate.event_id],
        "selected_tf": candidate.tf,
        "has_level": False,
        "has_pump_transition": True,
        "setups": [
            {
                "family": "unknown",
                "quality": "bad",
                "notes": "BOT sleep→pump hypothesis | Save = keep; redraw pump if boundaries are wrong; delete pump or No setup = reject",
                "has_level": False,
                "has_pump_transition": True,
                "has_structure_break": False,
                "level_price": None,
                "level_start_ms": None,
                "level_end_ms": None,
                "level_broken": None,
                "level_touch_times_ms": None,
                "pump_start_ms": candidate.pump_start_ms,
                "pump_start_price": candidate.pump_low_price,
                "culmination_ms": candidate.culmination_ms,
                "culmination_price": candidate.culmination_price,
                "pump_waves": [
                    {
                        "wave_ordinal": 1,
                        "start_ms": candidate.pump_start_ms,
                        "start_price": candidate.pump_low_price,
                        "culmination_ms": candidate.culmination_ms,
                        "culmination_price": candidate.culmination_price,
                    }
                ],
                "sideways_segments": [],
                "structure_break_ms": None,
                "structure_break_price": None,
                "structure_swing_low_ms": None,
                "structure_swing_low_price": None,
                "entry_ms": None,
                "entry_price": None,
                "entry_auto": None,
                "entry_source": None,
                "exit_ms": None,
                "exit_price": None,
                "sl_price": None,
                "sl_ms": None,
                "sl_hit_ms": None,
                "sl_auto": None,
                "sl_source": None,
                "zigzag_points": [],
            }
        ],
        "source": "bot_sleep_pump_candidate",
        "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION,
        "saved_at_ms": 0,
    }


def build_sleep_pump_review(
    *,
    cache_dir: Path = CACHE_1M,
    review_dir: Path = REVIEW_DIR,
    tfs: tuple[str, ...] = DEFAULT_TFS,
    end_ms: int = DEV_END_MS,
    max_events: int = 500,
    max_per_symbol_tf: int = 3,
    refresh_existing_labels: bool = False,
) -> pd.DataFrame:
    """Build a review population without overwriting expert labels.

    A feature-only refresh of a previously annotated queue is allowed only when
    every effective expert label still points to a candidate in the refreshed
    population.  This makes refreshes explicit and protects label identity.
    """

    rows: list[dict] = []
    for path in sorted(cache_dir.glob("*.parquet")):
        try:
            base = load_ohlcv_parquet(path)
            for tf in tfs:
                rows.extend(asdict(row) for row in _scan_symbol_tf(
                    path.stem,
                    base,
                    tf,
                    end_ms=end_ms,
                    max_per_symbol_tf=max_per_symbol_tf,
                ))
        except (OSError, ValueError, KeyError):
            continue
    candidates = pd.DataFrame(rows)
    if candidates.empty:
        raise ValueError("no sleep-pump review candidates")
    candidates = (
        candidates.sort_values(["score", "base_to_high_pct"], ascending=False)
        .head(max_events)
        .sort_values(["culmination_ms", "symbol", "tf"])
        .reset_index(drop=True)
    )
    labels_path = review_dir / "review_labels.jsonl"
    if labels_path.exists() and labels_path.stat().st_size:
        if not refresh_existing_labels:
            raise FileExistsError(
                f"refusing to replace annotated candidate queue without refresh_existing_labels=True: {labels_path}"
            )
        candidate_ids = set(candidates["event_id"])
        referenced_ids = {
            str(label.get("source_event_id") or "")
            for label in LabelStore(labels_path).read_effective().values()
        }
        missing = referenced_ids - candidate_ids
        if missing:
            raise ValueError(
                "refreshed candidate queue would orphan expert labels: "
                + ", ".join(sorted(missing)[:5])
            )
    seeds = [_seed_mark(SleepPumpCandidate(**row)) for row in candidates.to_dict("records")]
    review_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(review_dir / "candidates.parquet", index=False)
    marks = "".join(json.dumps(seed, ensure_ascii=False, separators=(",", ":")) + "\n" for seed in seeds)
    (review_dir / "marks.jsonl").write_text(marks, encoding="utf-8")
    labels_path.touch(exist_ok=True)
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Build high-recall sleep→pump review candidates.")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_1M)
    parser.add_argument("--review-dir", type=Path, default=REVIEW_DIR)
    parser.add_argument("--tf", action="append", choices=sorted(TF_MINUTES), default=None)
    parser.add_argument("--max-events", type=int, default=500)
    parser.add_argument("--max-per-symbol-tf", type=int, default=3)
    parser.add_argument("--refresh-existing-labels", action="store_true")
    args = parser.parse_args()
    candidates = build_sleep_pump_review(
        cache_dir=args.cache_dir,
        review_dir=args.review_dir,
        tfs=tuple(args.tf) if args.tf else DEFAULT_TFS,
        max_events=args.max_events,
        max_per_symbol_tf=args.max_per_symbol_tf,
        refresh_existing_labels=args.refresh_existing_labels,
    )
    print(f"sleep-pump review candidates={len(candidates)} -> {args.review_dir}")


if __name__ == "__main__":
    main()
