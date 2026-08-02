from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from .contracts import SpotTrendContractError
from .futures_data import IS_START, OOS_START


STRUCTURAL_STOP_TIMEFRAMES_MINUTES: tuple[int, ...] = (5, 15, 30, 60)
MAX_HOLD_MINUTES = 7_200


@dataclass(frozen=True, slots=True)
class StructuralStopConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    timeframes_minutes: tuple[int, ...] = STRUCTURAL_STOP_TIMEFRAMES_MINUTES
    pivot_left_bars: int = 2
    pivot_right_bars: int = 2
    maximum_hold_minutes: int = MAX_HOLD_MINUTES

    def __post_init__(self) -> None:
        if self.timeframes_minutes != STRUCTURAL_STOP_TIMEFRAMES_MINUTES:
            raise SpotTrendContractError("structural stop TFs are frozen at 5/15/30/60 minutes")
        if (self.pivot_left_bars, self.pivot_right_bars) != (2, 2):
            raise SpotTrendContractError("structural pivots are frozen at two left/two right bars")
        if self.maximum_hold_minutes != MAX_HOLD_MINUTES:
            raise SpotTrendContractError("structural stop diagnostic horizon is frozen at five days")


def _load_minute(path: Path) -> pd.DataFrame:
    start_ms = int(pd.Timestamp(IS_START, tz="UTC").timestamp() * 1_000)
    end_ms = int(pd.Timestamp(OOS_START, tz="UTC").timestamp() * 1_000)
    return (
        pl.scan_parquet(path)
        .filter((pl.col("timestamp") >= start_ms) & (pl.col("timestamp") < end_ms))
        .select("timestamp", "open", "high", "low", "close")
        .sort("timestamp")
        .collect()
        .to_pandas()
    )


class _RangeMaximumIndex:
    """Exact range-maximum and first-greater queries over completed bar closes."""

    def __init__(self, values: np.ndarray) -> None:
        size = 1
        while size < len(values):
            size *= 2
        self._size = size
        self._length = len(values)
        self._tree = np.full(2 * size, -np.inf, dtype=float)
        self._tree[size : size + len(values)] = values
        for index in range(size - 1, 0, -1):
            self._tree[index] = max(self._tree[index * 2], self._tree[index * 2 + 1])

    def range_maximum(self, start: int, end: int) -> float:
        """Return max(values[start:end]); an empty interval is minus infinity."""

        left = max(0, start) + self._size
        right = min(self._length, end) + self._size
        maximum = -np.inf
        while left < right:
            if left % 2:
                maximum = max(maximum, self._tree[left])
                left += 1
            if right % 2:
                right -= 1
                maximum = max(maximum, self._tree[right])
            left //= 2
            right //= 2
        return float(maximum)

    def first_greater(self, start: int, threshold: float) -> int | None:
        """Return the first index >= start whose value is strictly above threshold."""

        if start >= self._length or self.range_maximum(start, self._length) <= threshold:
            return None
        return self._first_greater(node=1, left=0, right=self._size, start=start, threshold=threshold)

    def _first_greater(
        self,
        *,
        node: int,
        left: int,
        right: int,
        start: int,
        threshold: float,
    ) -> int | None:
        if right <= start or left >= self._length or self._tree[node] <= threshold:
            return None
        if right - left == 1:
            return left
        middle = (left + right) // 2
        found = self._first_greater(
            node=node * 2,
            left=left,
            right=middle,
            start=start,
            threshold=threshold,
        )
        if found is not None:
            return found
        return self._first_greater(
            node=node * 2 + 1,
            left=middle,
            right=right,
            start=start,
            threshold=threshold,
        )


def _confirmed_structural_lows(minute: pd.DataFrame, timeframe_minutes: int) -> pd.DataFrame:
    frame = minute.copy()
    frame["bar_start"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True).dt.floor(
        f"{timeframe_minutes}min"
    )
    bars = frame.groupby("bar_start", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        minute_count=("timestamp", "size"),
    )
    bars["bar_end"] = bars.index + pd.Timedelta(minutes=timeframe_minutes)
    lows = bars["low"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    bar_starts = bars.index.to_numpy()
    bar_ends = bars["bar_end"].to_numpy()
    complete = bars["minute_count"].eq(timeframe_minutes).to_numpy(dtype=bool)
    if len(bars) < 5:
        return pd.DataFrame()
    complete_centres = (
        complete[:-4]
        & complete[1:-3]
        & complete[2:-2]
        & complete[3:-1]
        & complete[4:]
    )
    low_mask = np.zeros(len(bars), dtype=bool)
    low_mask[2:-2] = (
        complete_centres
        & (lows[2:-2] < lows[1:-3])
        & (lows[2:-2] < lows[:-4])
        & (lows[2:-2] <= lows[3:-1])
        & (lows[2:-2] <= lows[4:])
    )
    high_mask = np.zeros(len(bars), dtype=bool)
    high_mask[2:-2] = (
        complete_centres
        & (highs[2:-2] > highs[1:-3])
        & (highs[2:-2] > highs[:-4])
        & (highs[2:-2] >= highs[3:-1])
        & (highs[2:-2] >= highs[4:])
    )
    low_positions = np.flatnonzero(low_mask)
    high_positions = np.flatnonzero(high_mask)
    if not len(low_positions) or not len(high_positions):
        return pd.DataFrame()
    close_index = _RangeMaximumIndex(closes)
    high_break_positions = np.array(
        [
            -1
            if (position := close_index.first_greater(int(high_position) + 3, highs[high_position])) is None
            else position
            for high_position in high_positions
        ],
        dtype=np.int64,
    )
    protected: list[dict[str, object]] = []
    active = np.zeros(len(high_positions), dtype=bool)
    latest_high_heap: list[tuple[int, int]] = []
    breaking_high_heap: list[tuple[int, int]] = []
    next_high = 0
    for low_position in low_positions:
        while next_high < len(high_positions) and high_positions[next_high] < low_position:
            active[next_high] = True
            pivot_position = int(high_positions[next_high])
            heapq.heappush(latest_high_heap, (-pivot_position, next_high))
            break_position = int(high_break_positions[next_high])
            if break_position >= 0:
                heapq.heappush(breaking_high_heap, (break_position, next_high))
            next_high += 1
        raw_confirmation_position = int(low_position) + 2
        while breaking_high_heap and breaking_high_heap[0][0] <= raw_confirmation_position:
            _, expired = heapq.heappop(breaking_high_heap)
            active[expired] = False
        while latest_high_heap and not active[latest_high_heap[0][1]]:
            heapq.heappop(latest_high_heap)
        if not latest_high_heap:
            continue
        reference_position = int(high_positions[latest_high_heap[0][1]])
        confirmation_position = close_index.first_greater(
            raw_confirmation_position + 1,
            highs[reference_position],
        )
        if confirmation_position is None:
            continue
        protected.append(
            {
                "pivot_time": pd.Timestamp(bar_starts[low_position]),
                "pivot_price": float(lows[low_position]),
                "confirmation_time": pd.Timestamp(bar_ends[confirmation_position]),
                "broken_high_time": pd.Timestamp(bar_starts[reference_position]),
                "broken_high_price": float(highs[reference_position]),
            }
        )
    if not protected:
        return pd.DataFrame()
    output = pd.DataFrame(protected).sort_values(["confirmation_time", "pivot_time"], kind="stable")
    output = output.loc[
        output.groupby(["confirmation_time", "broken_high_time"])["pivot_price"].idxmin()
    ]
    return output.sort_values(["confirmation_time", "pivot_time"], kind="stable").reset_index(drop=True)


def _exact_path(
    minute: pd.DataFrame,
    timestamps: np.ndarray,
    snapshot_ms: int,
    minutes: int,
) -> pd.DataFrame | None:
    start = int(np.searchsorted(timestamps, snapshot_ms, side="left"))
    end = start + minutes
    if start >= len(timestamps) or end > len(timestamps):
        return None
    selected = minute.iloc[start:end]
    expected = np.arange(snapshot_ms, snapshot_ms + minutes * 60_000, 60_000, dtype=np.int64)
    if not np.array_equal(selected["timestamp"].to_numpy(dtype=np.int64), expected):
        return None
    return selected


def _simulate_policy(
    event: pd.Series,
    path: pd.DataFrame,
    pivots: pd.DataFrame,
    timeframe_minutes: int,
) -> dict[str, Any] | None:
    snapshot = pd.Timestamp(event["snapshot_time"])
    entry = float(event["snapshot_close"])
    available = pivots.loc[
        pivots["confirmation_time"].le(snapshot) & pivots["pivot_price"].lt(entry)
    ]
    if available.empty:
        return None
    initial_pivot = available.iloc[-1]
    stop = float(initial_pivot["pivot_price"])
    initial_stop = stop
    updates: list[dict[str, object]] = [
        {
            "time_ms": int(snapshot.timestamp() * 1_000),
            "price": stop,
            "pivot_time_ms": int(pd.Timestamp(initial_pivot["pivot_time"]).timestamp() * 1_000),
            "confirmation_time_ms": int(pd.Timestamp(initial_pivot["confirmation_time"]).timestamp() * 1_000),
            "kind": "initial",
            "broken_high_price": float(initial_pivot["broken_high_price"]),
        }
    ]
    future_pivots = pivots.loc[pivots["confirmation_time"].gt(snapshot)].sort_values("confirmation_time")
    path_timestamps = path["timestamp"].to_numpy(dtype=np.int64)
    path_lows = path["low"].to_numpy(dtype=float)
    path_opens = path["open"].to_numpy(dtype=float)
    exit_position: int | None = None
    exit_price = np.nan
    segment_start = 0
    for pivot in future_pivots.to_dict("records"):
        confirmation = pd.Timestamp(pivot["confirmation_time"])
        confirmation_ms = int(confirmation.timestamp() * 1_000)
        update_position = int(np.searchsorted(path_timestamps, confirmation_ms, side="left"))
        update_position = min(update_position, len(path))
        hits = np.flatnonzero(path_lows[segment_start:update_position] <= stop)
        if len(hits):
            exit_position = segment_start + int(hits[0])
            exit_price = path_opens[exit_position] if path_opens[exit_position] < stop else stop
            break
        if update_position >= len(path):
            segment_start = len(path)
            break
        candidate = float(pivot["pivot_price"])
        if candidate > stop and candidate < path_opens[update_position]:
            stop = candidate
            updates.append(
                {
                    "time_ms": confirmation_ms,
                    "price": stop,
                    "pivot_time_ms": int(pd.Timestamp(pivot["pivot_time"]).timestamp() * 1_000),
                    "confirmation_time_ms": confirmation_ms,
                    "kind": "trail",
                    "broken_high_price": float(pivot["broken_high_price"]),
                }
            )
        segment_start = update_position
    if exit_position is None and segment_start < len(path):
        hits = np.flatnonzero(path_lows[segment_start:] <= stop)
        if len(hits):
            exit_position = segment_start + int(hits[0])
            exit_price = path_opens[exit_position] if path_opens[exit_position] < stop else stop
    if exit_position is None:
        exit_position = len(path) - 1
        exit_price = float(path.iloc[-1]["close"])
        exit_reason = "time_5d"
    else:
        exit_reason = "structural_stop"
    traded = path.iloc[: exit_position + 1]
    exit_time_ms = int(traded.iloc[-1]["timestamp"] + 60_000)
    risk = entry - initial_stop
    if risk <= 0:
        return None
    post_exit = path.iloc[exit_position + 1 :]
    recovered_entry = bool(len(post_exit) and post_exit["high"].max() > entry)
    recovered_level = bool(len(post_exit) and post_exit["high"].max() > float(event["breakout_threshold"]))
    return {
        "trade_id": f"{event['event_id']}:{timeframe_minutes}m",
        "event_id": str(event["event_id"]),
        "symbol": str(event["symbol"]),
        "tf": f"{timeframe_minutes}m" if timeframe_minutes < 60 else "1h",
        "stop_timeframe_minutes": timeframe_minutes,
        "fill_time_ms": int(snapshot.timestamp() * 1_000),
        "exit_time_ms": exit_time_ms,
        "entry_price": entry,
        "exit_price": exit_price,
        "level": float(event["breakout_threshold"]),
        "initial_stop": initial_stop,
        "stop": stop,
        "mfe_price": float(traded["high"].max()),
        "mae_price": float(traded["low"].min()),
        "initial_risk_pct": risk / entry,
        "return_pct": exit_price / entry - 1.0,
        "net_r": (exit_price - entry) / risk,
        "outcome": "win" if exit_price > entry else "loss",
        "exit_reason": exit_reason,
        "stop_update_count": len(updates) - 1,
        "stop_updates": updates,
        "stopped_then_recovered_entry": bool(exit_reason == "structural_stop" and recovered_entry),
        "stopped_then_recovered_level": bool(exit_reason == "structural_stop" and recovered_level),
        "five_day_close_return": float(path.iloc[-1]["close"] / entry - 1.0),
        "daily_close_confirmed": bool(event["daily_close_confirmed"]),
        "crossing_minute_of_day": int(event["crossing_minute_of_day"]),
        "trigger_horizon": int(event["trigger_horizon"]),
    }


def build_structural_stop_ledger(
    causal_crossings: pd.DataFrame,
    *,
    config: StructuralStopConfig = StructuralStopConfig(),
) -> list[dict[str, Any]]:
    """Evaluate four causal swing-low trailing policies on the same five-day paths."""

    trades: list[dict[str, Any]] = []
    for symbol, symbol_events in causal_crossings.groupby("symbol", sort=True):
        path = config.source_dir / f"{symbol}.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        minute = _load_minute(path)
        timestamps = minute["timestamp"].to_numpy(dtype=np.int64)
        pivots_by_tf = {
            timeframe: _confirmed_structural_lows(minute, timeframe)
            for timeframe in config.timeframes_minutes
        }
        for _, event in symbol_events.sort_values("snapshot_time", kind="stable").iterrows():
            snapshot_ms = int(pd.Timestamp(event["snapshot_time"]).timestamp() * 1_000)
            future = _exact_path(minute, timestamps, snapshot_ms, config.maximum_hold_minutes)
            if future is None:
                continue
            for timeframe in config.timeframes_minutes:
                pivots = pivots_by_tf[timeframe]
                if pivots.empty:
                    continue
                trade = _simulate_policy(event, future, pivots, timeframe)
                if trade is not None:
                    trades.append(trade)
    if not trades:
        raise SpotTrendContractError("structural stop policies produced no resolved trades")
    return trades


def write_structural_stop_ledger(
    trades: list[dict[str, Any]],
    output_dir: Path,
    *,
    artifact_prefix: str = "structural_protected_stop",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{artifact_prefix}_trades.json").write_text(
        json.dumps(trades, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    flat = pd.DataFrame([{key: value for key, value in trade.items() if key != "stop_updates"} for trade in trades])
    flat.to_parquet(output_dir / f"{artifact_prefix}_trades.parquet", index=False)
    summary = flat.groupby("tf", sort=True).agg(
        trades=("trade_id", "size"),
        win_rate=("outcome", lambda values: (values == "win").mean()),
        stop_hit_rate=("exit_reason", lambda values: (values == "structural_stop").mean()),
        mean_initial_risk_pct=("initial_risk_pct", "mean"),
        median_initial_risk_pct=("initial_risk_pct", "median"),
        mean_net_r=("net_r", "mean"),
        mean_return=("return_pct", "mean"),
        stopped_then_recovered_entry_share=("stopped_then_recovered_entry", "mean"),
        stopped_then_recovered_level_share=("stopped_then_recovered_level", "mean"),
        mean_stop_updates=("stop_update_count", "mean"),
    ).reset_index()
    summary.to_csv(output_dir / f"{artifact_prefix}_policy_audit.csv", index=False)
