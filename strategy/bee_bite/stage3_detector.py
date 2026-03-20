"""Stage-3 detector for bee_bite: lower sweep and reclaim back into the box."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from data.liquidity.bee_bite_stage1_selector import BeeBiteStage1Result
from strategy.bee_bite.stage2_detector import BeeBiteStage2LiquidityZone, BeeBiteStage2Result
from strategy.bee_bite.stage3_rules import (
    is_move_pct_smaller_than_range_pct,
    resolve_below_range_span,
    resolve_half_hold_price,
)


@dataclass(slots=True, frozen=True)
class BeeBiteStage3Result:
    symbol: str
    passed: bool
    reason: str
    analysis_start_timestamp: int | None = None
    analysis_end_timestamp: int | None = None
    active_lower_liquidity_zone: BeeBiteStage2LiquidityZone | None = None
    break_timestamp: int | None = None
    reclaim_timestamp: int | None = None
    invalidation_timestamp: int | None = None
    break_idx: int | None = None
    reclaim_idx: int | None = None
    invalidation_idx: int | None = None
    lowest_break_idx: int | None = None
    lowest_break_price: float | None = None
    lowest_break_timestamp: int | None = None
    below_range_high_price: float | None = None
    under_range_span: float | None = None
    under_range_span_pct: float | None = None
    range_size_pct: float | None = None
    bars_under_range: int | None = None
    box_low: float | None = None
    box_high: float | None = None
    hold_price: float | None = None
    reference_box_start_timestamp: int | None = None
    reference_box_end_timestamp: int | None = None


@dataclass(slots=True)
class _PreparedStage3Frame:
    timestamps: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray


class BeeBiteStage3Detector:
    """Detect a valid stage-3 lower sweep followed by reclaim into the stage-2 box."""

    _EPSILON = 1e-12
    _MIN_LOWER_ZONE_TO_BOX_RATIO = 0.5

    def detect(
        self,
        *,
        symbol: str,
        frame: pd.DataFrame,
        stage1: BeeBiteStage1Result,
        stage2: BeeBiteStage2Result,
        analysis_end_timestamp: int | None = None,
    ) -> BeeBiteStage3Result:
        if not stage1.passed:
            return BeeBiteStage3Result(symbol=symbol, passed=False, reason="stage1_not_passed")
        if not stage2.passed:
            return BeeBiteStage3Result(symbol=symbol, passed=False, reason="stage2_not_passed")
        if stage2.box_low is None or stage2.box_high is None or stage2.box_end_timestamp is None:
            return BeeBiteStage3Result(symbol=symbol, passed=False, reason="stage2_box_missing")
        reference_box_start_timestamp = int(stage2.box_start_timestamp) if stage2.box_start_timestamp is not None else None
        reference_box_end_timestamp = int(stage2.box_end_timestamp)

        prepared = self._prepare_frame(frame=frame)
        if prepared is None:
            return BeeBiteStage3Result(symbol=symbol, passed=False, reason="frame_invalid")

        box_end_idx = self._resolve_index_by_timestamp(prepared.timestamps, int(stage2.box_end_timestamp))
        if box_end_idx is None:
            return BeeBiteStage3Result(symbol=symbol, passed=False, reason="box_end_not_in_frame")

        lower_zone = self._resolve_active_lower_liquidity_zone(stage2=stage2)
        if lower_zone is None:
            fallback_timestamp = int(prepared.timestamps[min(box_end_idx, len(prepared.timestamps) - 1)])
            return BeeBiteStage3Result(
                symbol=symbol,
                passed=False,
                reason="no_active_lower_liquidity_zone",
                analysis_start_timestamp=fallback_timestamp,
                analysis_end_timestamp=fallback_timestamp,
                box_low=float(stage2.box_low),
                box_high=float(stage2.box_high),
                reference_box_start_timestamp=reference_box_start_timestamp,
                reference_box_end_timestamp=reference_box_end_timestamp,
            )

        scan_start_idx = self._resolve_stage3_ready_idx(
            prepared=prepared,
            stage1=stage1,
            stage2=stage2,
            lower_zone=lower_zone,
        )
        if scan_start_idx is None:
            return BeeBiteStage3Result(
                symbol=symbol,
                passed=False,
                reason="lower_zone_too_short_for_box",
                analysis_start_timestamp=int(prepared.timestamps[box_end_idx]),
                analysis_end_timestamp=int(prepared.timestamps[box_end_idx]),
                active_lower_liquidity_zone=lower_zone,
                box_low=float(stage2.box_low),
                box_high=float(stage2.box_high),
                reference_box_start_timestamp=reference_box_start_timestamp,
                reference_box_end_timestamp=reference_box_end_timestamp,
            )

        scan_end_idx = len(prepared.timestamps) - 1
        if analysis_end_timestamp is not None:
            resolved_end_idx = self._resolve_index_by_timestamp(prepared.timestamps, int(analysis_end_timestamp))
            if resolved_end_idx is not None:
                scan_end_idx = min(scan_end_idx, resolved_end_idx)
        if scan_end_idx < scan_start_idx:
            safe_start_idx = min(scan_start_idx, len(prepared.timestamps) - 1)
            safe_end_idx = max(0, min(scan_end_idx, len(prepared.timestamps) - 1))
            return BeeBiteStage3Result(
                symbol=symbol,
                passed=False,
                reason="no_data_after_stage3_ready",
                analysis_start_timestamp=int(prepared.timestamps[safe_start_idx]),
                analysis_end_timestamp=int(prepared.timestamps[safe_end_idx]),
                active_lower_liquidity_zone=lower_zone,
                box_low=float(stage2.box_low),
                box_high=float(stage2.box_high),
                reference_box_start_timestamp=reference_box_start_timestamp,
                reference_box_end_timestamp=reference_box_end_timestamp,
            )

        boundary = float(stage2.box_low)
        range_high = float(stage2.box_high)
        hold_price = (
            resolve_half_hold_price(
                high_pump=stage1.pump_peak_price,
                low_before_pump=stage1.hold_base_price if stage1.hold_base_price is not None else stage1.pump_base_price,
            )
        )
        range_size_pct = ((range_high - boundary) / max(boundary, self._EPSILON)) if range_high > boundary else None
        break_threshold = min(boundary, float(lower_zone.high))

        break_idx: int | None = None
        lowest_break: float | None = None
        lowest_break_idx: int | None = None
        below_range_high: float | None = None

        for idx in range(scan_start_idx, scan_end_idx + 1):
            low = float(prepared.lows[idx])
            high = float(prepared.highs[idx])
            close = float(prepared.closes[idx])
            timestamp = int(prepared.timestamps[idx])

            if break_idx is None:
                if low >= boundary or low >= break_threshold:
                    continue
                move_size = max(boundary - low, 0.0)
                if not is_move_pct_smaller_than_range_pct(
                    move_size=move_size,
                    reclaim_boundary=boundary,
                    range_high=range_high,
                ):
                    return BeeBiteStage3Result(
                        symbol=symbol,
                        passed=False,
                        reason="break_too_deep",
                        analysis_start_timestamp=int(prepared.timestamps[scan_start_idx]),
                        analysis_end_timestamp=int(prepared.timestamps[scan_end_idx]),
                        active_lower_liquidity_zone=lower_zone,
                        break_timestamp=timestamp,
                        break_idx=idx,
                        lowest_break_price=low,
                        box_low=boundary,
                        box_high=range_high,
                        hold_price=hold_price,
                        range_size_pct=range_size_pct,
                        reference_box_start_timestamp=reference_box_start_timestamp,
                        reference_box_end_timestamp=reference_box_end_timestamp,
                    )
                break_idx = idx
                lowest_break = low
                lowest_break_idx = idx
                below_range_high = min(high, boundary)

            if break_idx is None:
                continue

            if lowest_break is None or low < lowest_break:
                lowest_break = low
                lowest_break_idx = idx
            below_range_high = max(float(below_range_high if below_range_high is not None else min(high, boundary)), min(high, boundary))

            if hold_price is not None and close < hold_price:
                return BeeBiteStage3Result(
                    symbol=symbol,
                    passed=False,
                    reason="close_below_hold",
                    analysis_start_timestamp=int(prepared.timestamps[scan_start_idx]),
                    analysis_end_timestamp=int(prepared.timestamps[scan_end_idx]),
                    active_lower_liquidity_zone=lower_zone,
                    break_timestamp=int(prepared.timestamps[break_idx]),
                    invalidation_timestamp=timestamp,
                    break_idx=break_idx,
                    invalidation_idx=idx,
                    lowest_break_idx=lowest_break_idx,
                    lowest_break_price=lowest_break,
                    lowest_break_timestamp=int(prepared.timestamps[lowest_break_idx]) if lowest_break_idx is not None else None,
                    below_range_high_price=below_range_high,
                    box_low=boundary,
                    box_high=range_high,
                    hold_price=hold_price,
                    range_size_pct=range_size_pct,
                    reference_box_start_timestamp=reference_box_start_timestamp,
                    reference_box_end_timestamp=reference_box_end_timestamp,
                )

            under_range_span = resolve_below_range_span(
                lowest_break=lowest_break,
                below_range_high=below_range_high,
            )
            under_range_ok = under_range_span is not None and is_move_pct_smaller_than_range_pct(
                move_size=under_range_span,
                reclaim_boundary=boundary,
                range_high=range_high,
            )
            if not under_range_ok:
                return BeeBiteStage3Result(
                    symbol=symbol,
                    passed=False,
                    reason="under_range_span_too_wide",
                    analysis_start_timestamp=int(prepared.timestamps[scan_start_idx]),
                    analysis_end_timestamp=int(prepared.timestamps[scan_end_idx]),
                    active_lower_liquidity_zone=lower_zone,
                    break_timestamp=int(prepared.timestamps[break_idx]),
                    invalidation_timestamp=timestamp,
                    break_idx=break_idx,
                    invalidation_idx=idx,
                    lowest_break_idx=lowest_break_idx,
                    lowest_break_price=lowest_break,
                    lowest_break_timestamp=int(prepared.timestamps[lowest_break_idx]) if lowest_break_idx is not None else None,
                    below_range_high_price=below_range_high,
                    under_range_span=under_range_span,
                    under_range_span_pct=under_range_span / max(boundary, self._EPSILON),
                    range_size_pct=range_size_pct,
                    box_low=boundary,
                    box_high=range_high,
                    hold_price=hold_price,
                    reference_box_start_timestamp=reference_box_start_timestamp,
                    reference_box_end_timestamp=reference_box_end_timestamp,
                )

            if close > boundary:
                bars_under_range = max(idx - break_idx + 1, 1)
                return BeeBiteStage3Result(
                    symbol=symbol,
                    passed=True,
                    reason="stage3_confirmed",
                    analysis_start_timestamp=int(prepared.timestamps[scan_start_idx]),
                    analysis_end_timestamp=int(prepared.timestamps[scan_end_idx]),
                    active_lower_liquidity_zone=lower_zone,
                    break_timestamp=int(prepared.timestamps[break_idx]),
                    reclaim_timestamp=timestamp,
                    break_idx=break_idx,
                    reclaim_idx=idx,
                    lowest_break_idx=lowest_break_idx,
                    lowest_break_price=lowest_break,
                    lowest_break_timestamp=int(prepared.timestamps[lowest_break_idx]) if lowest_break_idx is not None else None,
                    below_range_high_price=below_range_high,
                    under_range_span=under_range_span,
                    under_range_span_pct=(under_range_span / max(boundary, self._EPSILON)) if under_range_span is not None else None,
                    range_size_pct=range_size_pct,
                    bars_under_range=bars_under_range,
                    box_low=boundary,
                    box_high=range_high,
                    hold_price=hold_price,
                    reference_box_start_timestamp=reference_box_start_timestamp,
                    reference_box_end_timestamp=reference_box_end_timestamp,
                )

        return BeeBiteStage3Result(
            symbol=symbol,
            passed=False,
            reason="reclaim_not_found",
            analysis_start_timestamp=int(prepared.timestamps[scan_start_idx]),
            analysis_end_timestamp=int(prepared.timestamps[scan_end_idx]),
            active_lower_liquidity_zone=lower_zone,
            break_timestamp=int(prepared.timestamps[break_idx]) if break_idx is not None else None,
            break_idx=break_idx,
            lowest_break_idx=lowest_break_idx,
            lowest_break_price=lowest_break,
            lowest_break_timestamp=int(prepared.timestamps[lowest_break_idx]) if lowest_break_idx is not None else None,
            below_range_high_price=below_range_high,
            under_range_span=resolve_below_range_span(lowest_break=lowest_break, below_range_high=below_range_high),
            box_low=boundary,
            box_high=range_high,
            hold_price=hold_price,
            range_size_pct=range_size_pct,
            reference_box_start_timestamp=reference_box_start_timestamp,
            reference_box_end_timestamp=reference_box_end_timestamp,
        )

    @staticmethod
    def _resolve_active_lower_liquidity_zone(*, stage2: BeeBiteStage2Result) -> BeeBiteStage2LiquidityZone | None:
        lower_zones = [zone for zone in stage2.liquidity_zones if zone.side == "lower"]
        if not lower_zones:
            return None
        box_low = float(stage2.box_low or 0.0)
        candidates = [zone for zone in lower_zones if zone.high <= (box_low + 1e-12)]
        if not candidates:
            candidates = lower_zones
        return max(
            candidates,
            key=lambda zone: (
                zone.high,
                zone.last_touch_timestamp,
                zone.touch_count,
                zone.end_timestamp,
            ),
        )

    def _resolve_stage3_ready_idx(
        self,
        *,
        prepared: _PreparedStage3Frame,
        stage1: BeeBiteStage1Result,
        stage2: BeeBiteStage2Result,
        lower_zone: BeeBiteStage2LiquidityZone,
    ) -> int | None:
        if stage2.box_start_timestamp is None or stage2.box_end_timestamp is None:
            return None
        box_start_idx = self._resolve_index_by_timestamp(prepared.timestamps, int(stage2.box_start_timestamp))
        box_end_idx = self._resolve_index_by_timestamp(prepared.timestamps, int(stage2.box_end_timestamp))
        if box_start_idx is None or box_end_idx is None or box_end_idx < box_start_idx:
            return None

        box_bars = box_end_idx - box_start_idx + 1
        zone_bars = lower_zone.end_idx - lower_zone.start_idx + 1
        required_zone_bars = max(1, int(math.ceil(box_bars * self._MIN_LOWER_ZONE_TO_BOX_RATIO)))
        if zone_bars < required_zone_bars:
            return None

        ready_idx = lower_zone.start_idx + required_zone_bars
        if stage1.stage1_confirmed_timestamp is not None:
            confirmed_idx = self._resolve_index_by_timestamp(prepared.timestamps, int(stage1.stage1_confirmed_timestamp))
            if confirmed_idx is not None:
                ready_idx = max(ready_idx, confirmed_idx + 1)
        return min(ready_idx, len(prepared.timestamps) - 1)

    @staticmethod
    def _prepare_frame(*, frame: pd.DataFrame) -> _PreparedStage3Frame | None:
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        if frame.empty or not required_columns.issubset(frame.columns):
            return None
        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        for column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
        if prepared.empty:
            return None
        return _PreparedStage3Frame(
            timestamps=prepared["timestamp"].astype("int64").to_numpy(),
            opens=prepared["open"].astype("float64").to_numpy(),
            highs=prepared["high"].astype("float64").to_numpy(),
            lows=prepared["low"].astype("float64").to_numpy(),
            closes=prepared["close"].astype("float64").to_numpy(),
            volumes=prepared["volume"].astype("float64").to_numpy(),
        )

    @staticmethod
    def _resolve_index_by_timestamp(timestamps: np.ndarray, timestamp: int) -> int | None:
        matches = np.where(timestamps == int(timestamp))[0]
        if matches.size == 0:
            return None
        return int(matches[0])
