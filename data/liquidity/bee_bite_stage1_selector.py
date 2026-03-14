"""Stage-1 filter and event detector for the bee_bite strategy."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from constants import (
    BEE_BITE_STAGE1_MAX_CONFIRM_DELAY_BARS_15M,
    BEE_BITE_STAGE1_MAX_INITIAL_PUMP_DURATION_BARS_15M,
    BEE_BITE_STAGE1_MIN_CONFIRM_DELAY_BARS_15M,
    BEE_BITE_STAGE1_MIN_PUMP_PCT,
    BEE_BITE_STAGE1_MIN_RETAIN_RATIO,
    BEE_BITE_STAGE1_MIN_VOLUME_RATIO,
    BEE_BITE_STAGE1_MIN_VOLUME_USDT,
    BEE_BITE_STAGE1_PUMP_START_LOOKBACK_BARS,
    BEE_BITE_STAGE1_PUMP_WINDOW_BARS,
    BEE_BITE_STAGE1_SLEEP_WINDOW_BARS_15M,
    BEE_BITE_STAGE1_VOLUME_WINDOW_BARS_15M,
)


@dataclass(slots=True)
class BeeBiteStage1Result:
    symbol: str
    passed: bool
    reason: str
    rolling_volume_usdt: float | None = None
    sleep_avg_volume_usdt: float | None = None
    post_pump_avg_volume_usdt: float | None = None
    post_pump_volume_ratio: float | None = None
    pump_percent: float | None = None
    retain_ratio: float | None = None
    sleep_start_timestamp: int | None = None
    sleep_end_timestamp: int | None = None
    pump_start_timestamp: int | None = None
    pump_peak_timestamp: int | None = None
    stage1_confirmed_timestamp: int | None = None
    pump_base_price: float | None = None
    pump_peak_price: float | None = None
    hold_base_price: float | None = None
    hold_base_timestamp: int | None = None
    hold_price: float | None = None
    lowest_after_pump: float | None = None
    lowest_after_pump_timestamp: int | None = None


@dataclass(slots=True)
class _PreparedStage1Frame:
    timestamps: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    quote_volume: np.ndarray
    rolling_volume: np.ndarray
    sleep_volume_mean: np.ndarray
    sleep_window_range_pct: np.ndarray
    cumulative_quote_volume: np.ndarray


class BeeBiteStage1Selector:
    """Select assets that match sleep -> pump -> retain-above-half stage-1 logic."""

    _EPSILON = 1e-12

    def __init__(
        self,
        *,
        min_volume_usdt: float = BEE_BITE_STAGE1_MIN_VOLUME_USDT,
        min_pump_pct: float = BEE_BITE_STAGE1_MIN_PUMP_PCT,
        min_retain_ratio: float = BEE_BITE_STAGE1_MIN_RETAIN_RATIO,
        min_volume_ratio: float = BEE_BITE_STAGE1_MIN_VOLUME_RATIO,
        sleep_window_bars: int = BEE_BITE_STAGE1_SLEEP_WINDOW_BARS_15M,
        pump_window_bars: int = BEE_BITE_STAGE1_PUMP_WINDOW_BARS,
        pump_start_lookback_bars: int = BEE_BITE_STAGE1_PUMP_START_LOOKBACK_BARS,
        volume_window_bars: int = BEE_BITE_STAGE1_VOLUME_WINDOW_BARS_15M,
        max_initial_pump_duration_bars: int = BEE_BITE_STAGE1_MAX_INITIAL_PUMP_DURATION_BARS_15M,
        min_confirm_delay_bars: int = BEE_BITE_STAGE1_MIN_CONFIRM_DELAY_BARS_15M,
        max_confirm_delay_bars: int = BEE_BITE_STAGE1_MAX_CONFIRM_DELAY_BARS_15M,
    ) -> None:
        self._min_volume_usdt = float(min_volume_usdt)
        self._min_pump_pct = float(min_pump_pct)
        self._min_retain_ratio = float(min_retain_ratio)
        self._min_volume_ratio = float(min_volume_ratio)
        self._sleep_window_bars = int(sleep_window_bars)
        self._pump_window_bars = int(pump_window_bars)
        self._pump_start_lookback_bars = int(max(pump_start_lookback_bars, pump_window_bars))
        self._volume_window_bars = int(volume_window_bars)
        self._max_initial_pump_duration_bars = int(max(max_initial_pump_duration_bars, 1))
        self._min_confirm_delay_bars = int(max(min_confirm_delay_bars, 1))
        self._max_confirm_delay_bars = int(max(max_confirm_delay_bars, self._min_confirm_delay_bars))

    def evaluate_symbol(self, *, symbol: str, frame: pd.DataFrame) -> BeeBiteStage1Result:
        prepared = self._prepare_frame(symbol=symbol, frame=frame)
        if isinstance(prepared, BeeBiteStage1Result):
            return prepared

        saw_sleep_candidate, saw_pump_candidate = self._scan_stage1_setup_flags(prepared)
        saw_retain_candidate = False
        saw_sleep_below_hold_failure = False
        saw_confirm_band_failure = False
        saw_confirm_too_early = False
        saw_confirm_too_late = False
        saw_initial_pump_too_long = False
        saw_peak_invalidated = False
        saw_volume_ratio_candidate = False
        saw_volume_24h_candidate = False
        latest_match: BeeBiteStage1Result | None = None
        latest_rolling_volume_usdt = self._safe_float(prepared.rolling_volume[-1])
        last_frame_idx = len(prepared.timestamps) - 1

        for candidate in self._iter_stage1_candidates(prepared=prepared):
            confirm_start_idx = candidate.peak_idx + self._min_confirm_delay_bars
            confirm_end_idx = min(candidate.regime_end_idx, last_frame_idx)
            if confirm_start_idx > confirm_end_idx:
                saw_confirm_too_late = True
                continue

            candidate_passed = False
            for confirm_idx in range(confirm_start_idx, confirm_end_idx + 1):
                rolling_volume_usdt = self._safe_float(prepared.rolling_volume[min(confirm_idx, last_frame_idx)])
                if confirm_idx == last_frame_idx:
                    rolling_volume_usdt = latest_rolling_volume_usdt
                evaluation = self._evaluate_candidate_at_index(
                    symbol=symbol,
                    prepared=prepared,
                    candidate=candidate,
                    confirm_idx=confirm_idx,
                    rolling_volume_usdt=rolling_volume_usdt,
                )
                if evaluation is None:
                    continue

                if evaluation.retain_ratio is not None and evaluation.retain_ratio >= self._min_retain_ratio:
                    saw_retain_candidate = True
                if evaluation.reason == "sleep_closes_not_below_hold_majority":
                    saw_sleep_below_hold_failure = True
                if evaluation.reason == "confirm_candle_outside_pump_band":
                    saw_confirm_band_failure = True
                if evaluation.reason == "confirm_before_min_hold":
                    saw_confirm_too_early = True
                if evaluation.reason == "confirm_after_max_hold":
                    saw_confirm_too_late = True
                if evaluation.reason == "initial_pump_too_long":
                    saw_initial_pump_too_long = True
                if evaluation.reason == "peak_invalidated_by_new_high":
                    saw_peak_invalidated = True
                if evaluation.post_pump_volume_ratio is not None and evaluation.post_pump_volume_ratio >= self._min_volume_ratio:
                    saw_volume_ratio_candidate = True
                if evaluation.rolling_volume_usdt is not None and evaluation.rolling_volume_usdt >= self._min_volume_usdt:
                    saw_volume_24h_candidate = True

                if evaluation.reason in {"retain_below_half", "peak_invalidated_by_new_high"}:
                    break
                if not evaluation.passed:
                    continue

                candidate_passed = True
                if latest_match is None:
                    latest_match = evaluation
                elif (
                    evaluation.stage1_confirmed_timestamp is not None
                    and latest_match.stage1_confirmed_timestamp is not None
                    and evaluation.stage1_confirmed_timestamp >= latest_match.stage1_confirmed_timestamp
                ):
                    latest_match = evaluation
                break

            if not candidate_passed and confirm_end_idx < last_frame_idx:
                saw_confirm_too_late = True

        if latest_match is not None:
            return latest_match
        if not saw_sleep_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="sleep_not_dormant")
        if not saw_pump_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="pump_below_15pct")
        if not saw_retain_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="retain_below_half")
        if saw_sleep_below_hold_failure:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="sleep_closes_not_below_hold_majority")
        if saw_initial_pump_too_long:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="initial_pump_too_long")
        if saw_peak_invalidated:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="peak_invalidated_by_new_high")
        if saw_confirm_too_early:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="confirm_before_min_hold")
        if saw_confirm_too_late:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="confirm_after_max_hold")
        if saw_confirm_band_failure:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="confirm_candle_outside_pump_band")
        if not saw_volume_ratio_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="volume_ratio_below_15x")
        if not saw_volume_24h_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="volume_below_20m")
        return BeeBiteStage1Result(symbol=symbol, passed=False, reason="retain_below_half")

    def detect_events(self, *, symbol: str, frame: pd.DataFrame) -> list[BeeBiteStage1Result]:
        prepared = self._prepare_frame(symbol=symbol, frame=frame)
        if isinstance(prepared, BeeBiteStage1Result):
            return []

        events: list[BeeBiteStage1Result] = []
        seen_keys: set[tuple[int, int]] = set()
        for candidate in self._iter_stage1_candidates(prepared=prepared):
            candidate_key = (
                int(prepared.timestamps[candidate.pump_start_idx]),
                int(prepared.timestamps[candidate.peak_idx]),
            )
            if candidate_key in seen_keys:
                continue

            confirm_start_idx = candidate.peak_idx + self._min_confirm_delay_bars
            confirm_end_idx = min(candidate.regime_end_idx, len(prepared.timestamps) - 1)
            if confirm_start_idx > confirm_end_idx:
                continue

            for confirm_idx in range(confirm_start_idx, confirm_end_idx + 1):
                rolling_volume_usdt = self._safe_float(prepared.rolling_volume[confirm_idx])
                evaluation = self._evaluate_candidate_at_index(
                    symbol=symbol,
                    prepared=prepared,
                    candidate=candidate,
                    confirm_idx=confirm_idx,
                    rolling_volume_usdt=rolling_volume_usdt,
                )
                if evaluation is None:
                    continue
                if evaluation.reason in {"retain_below_half", "peak_invalidated_by_new_high"}:
                    break
                if not evaluation.passed:
                    continue
                events.append(evaluation)
                seen_keys.add(candidate_key)
                break

        events.sort(
            key=lambda item: (
                int(item.stage1_confirmed_timestamp or 0),
                int(item.pump_peak_timestamp or 0),
                item.symbol,
            )
        )
        return events

    def _prepare_frame(self, *, symbol: str, frame: pd.DataFrame) -> _PreparedStage1Frame | BeeBiteStage1Result:
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        if frame.empty:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="empty_frame")
        if not required_columns.issubset(frame.columns):
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="missing_columns")

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        for column in ("timestamp", "open", "high", "low", "close", "volume"):
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
        prepared = prepared.reset_index(drop=True)

        min_required_rows = max(
            self._sleep_window_bars + self._pump_window_bars + 1,
            self._volume_window_bars,
        )
        if len(prepared) < min_required_rows:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="insufficient_history")

        timestamps = prepared["timestamp"].astype("int64").to_numpy()
        opens = prepared["open"].astype("float64").to_numpy()
        highs = prepared["high"].astype("float64").to_numpy()
        lows = prepared["low"].astype("float64").to_numpy()
        closes = prepared["close"].astype("float64").to_numpy()
        volumes = prepared["volume"].astype("float64").to_numpy()
        quote_volume = closes * volumes
        cumulative_quote_volume = np.cumsum(quote_volume, dtype="float64")

        rolling_volume = (
            pd.Series(quote_volume)
            .rolling(window=self._volume_window_bars, min_periods=self._volume_window_bars)
            .sum()
            .to_numpy(dtype="float64")
        )
        sleep_volume_mean = (
            pd.Series(quote_volume)
            .rolling(window=self._sleep_window_bars, min_periods=self._sleep_window_bars)
            .mean()
            .shift(1)
            .to_numpy(dtype="float64")
        )
        sleep_window_range_pct = (
            pd.Series(highs)
            .rolling(window=self._pump_window_bars, min_periods=self._pump_window_bars)
            .max()
            .div(pd.Series(lows).rolling(window=self._pump_window_bars, min_periods=self._pump_window_bars).min())
            .sub(1.0)
            .to_numpy(dtype="float64")
        )

        return _PreparedStage1Frame(
            timestamps=timestamps,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            quote_volume=quote_volume,
            rolling_volume=rolling_volume,
            sleep_volume_mean=sleep_volume_mean,
            sleep_window_range_pct=sleep_window_range_pct,
            cumulative_quote_volume=cumulative_quote_volume,
        )

    def _iter_stage1_candidates(self, *, prepared: _PreparedStage1Frame):
        last_candidate_start = len(prepared.timestamps) - self._pump_window_bars - 1
        start_idx = self._sleep_window_bars
        regime_floor_idx = self._sleep_window_bars
        while start_idx <= last_candidate_start:
            window_end_idx = start_idx + self._pump_window_bars - 1
            pump_highs = self._effective_highs(prepared)[start_idx : window_end_idx + 1]
            peak_offset = int(np.argmax(pump_highs))
            peak_idx = start_idx + peak_offset

            raw_pump_start_idx = self._resolve_pump_start_idx(
                prepared=prepared,
                peak_idx=peak_idx,
                min_start_idx=regime_floor_idx,
            )
            pump_start_idx = self._refine_pump_start_idx(
                prepared=prepared,
                raw_pump_start_idx=raw_pump_start_idx,
                peak_idx=peak_idx,
            )
            if pump_start_idx < self._sleep_window_bars:
                start_idx += 1
                continue
            if not self._is_sleep_window_valid(
                start_idx=pump_start_idx,
                sleep_window_range_pct=prepared.sleep_window_range_pct,
            ):
                start_idx += 1
                continue

            sleep_avg_volume_usdt = self._safe_float(prepared.sleep_volume_mean[pump_start_idx])
            if sleep_avg_volume_usdt is None or sleep_avg_volume_usdt <= self._EPSILON:
                start_idx += 1
                continue

            pump_base_price = float(prepared.lows[pump_start_idx])
            pump_peak_price = self._effective_high_at(prepared=prepared, idx=peak_idx)
            if pump_base_price <= self._EPSILON or pump_peak_price <= pump_base_price:
                start_idx += 1
                continue

            pump_percent = (pump_peak_price / pump_base_price) - 1.0
            if pump_percent < self._min_pump_pct:
                start_idx += 1
                continue
            if (peak_idx - pump_start_idx) > self._max_initial_pump_duration_bars:
                start_idx += 1
                continue
            if not self._has_stepped_initial_volume(prepared=prepared, pump_start_idx=pump_start_idx):
                start_idx += 1
                continue

            launch_start_idx = self._resolve_launch_start_idx(
                prepared=prepared,
                pump_start_idx=pump_start_idx,
                peak_idx=peak_idx,
            )
            if launch_start_idx > pump_start_idx:
                launch_base_price = float(prepared.lows[launch_start_idx])
                if launch_base_price > self._EPSILON:
                    launch_pump_percent = (pump_peak_price / launch_base_price) - 1.0
                    if launch_pump_percent >= self._min_pump_pct:
                        pump_start_idx = launch_start_idx
                        pump_base_price = launch_base_price
                        pump_percent = launch_pump_percent

            initial_candidate = _Stage1Candidate(
                sleep_start_idx=pump_start_idx - self._sleep_window_bars,
                sleep_end_idx=pump_start_idx - 1,
                pump_start_idx=pump_start_idx,
                peak_idx=peak_idx,
                regime_end_idx=peak_idx,
                sleep_avg_volume_usdt=sleep_avg_volume_usdt,
                pump_base_price=pump_base_price,
                pump_peak_price=pump_peak_price,
                hold_base_idx=pump_start_idx,
                hold_base_price=pump_base_price,
                pump_percent=pump_percent,
            )
            candidate = self._finalize_candidate_regime(prepared=prepared, candidate=initial_candidate)
            yield candidate

            regime_floor_idx = max(regime_floor_idx, candidate.regime_end_idx)
            start_idx = max(start_idx + 1, candidate.regime_end_idx)

    def _scan_stage1_setup_flags(self, prepared: _PreparedStage1Frame) -> tuple[bool, bool]:
        for _candidate in self._iter_stage1_candidates(prepared=prepared):
            return True, True
        return False, False

    def _resolve_pump_start_idx(
        self,
        *,
        prepared: _PreparedStage1Frame,
        peak_idx: int,
        min_start_idx: int | None = None,
    ) -> int:
        floor_idx = self._sleep_window_bars if min_start_idx is None else int(max(self._sleep_window_bars, min_start_idx))
        search_start_idx = max(floor_idx, peak_idx - self._pump_start_lookback_bars + 1)
        lows_window = prepared.lows[search_start_idx : peak_idx + 1]
        start_offset = int(np.argmin(lows_window))
        return search_start_idx + start_offset

    def _refine_pump_start_idx(
        self,
        *,
        prepared: _PreparedStage1Frame,
        raw_pump_start_idx: int,
        peak_idx: int,
    ) -> int:
        latest_valid_start_idx = raw_pump_start_idx
        latest_effective_high = self._effective_high_at(prepared=prepared, idx=peak_idx)
        latest_candidate_idx = peak_idx - self._pump_window_bars + 1
        if latest_candidate_idx < raw_pump_start_idx:
            return raw_pump_start_idx

        for candidate_start_idx in range(raw_pump_start_idx, latest_candidate_idx + 1):
            pump_base_price = float(prepared.lows[candidate_start_idx])
            if pump_base_price <= self._EPSILON:
                continue

            pump_percent = (latest_effective_high / pump_base_price) - 1.0
            if pump_percent < self._min_pump_pct:
                continue
            if not self._has_stepped_initial_volume(prepared=prepared, pump_start_idx=candidate_start_idx):
                continue

            latest_valid_start_idx = candidate_start_idx

        return latest_valid_start_idx

    def _resolve_launch_start_idx(
        self,
        *,
        prepared: _PreparedStage1Frame,
        pump_start_idx: int,
        peak_idx: int,
    ) -> int:
        if peak_idx <= pump_start_idx:
            return pump_start_idx
        if self._is_start_launch_bar(prepared=prepared, pump_start_idx=pump_start_idx):
            return pump_start_idx

        search_end_idx = min(peak_idx, pump_start_idx + 12)
        explosive_search_end_idx = min(search_end_idx, peak_idx - 2)
        for idx in range(pump_start_idx + 1, explosive_search_end_idx + 1):
            if self._is_explosive_launch_bar(
                prepared=prepared,
                pump_start_idx=pump_start_idx,
                idx=idx,
            ):
                return idx

        if (
            (peak_idx - pump_start_idx) <= self._pump_window_bars
            and self._is_explosive_launch_bar(
                prepared=prepared,
                pump_start_idx=pump_start_idx,
                idx=peak_idx,
            )
        ):
            return peak_idx

        if self._initial_move_directional_efficiency(prepared=prepared, pump_start_idx=pump_start_idx, peak_idx=peak_idx) >= 0.35:
            return pump_start_idx

        for idx in range(pump_start_idx + 1, search_end_idx + 1):
            if self._is_launch_sequence_start(
                prepared=prepared,
                pump_start_idx=pump_start_idx,
                candidate_idx=idx,
                peak_idx=peak_idx,
            ):
                return idx

        return pump_start_idx

    def _is_start_launch_bar(
        self,
        *,
        prepared: _PreparedStage1Frame,
        pump_start_idx: int,
    ) -> bool:
        history_start_idx = max(0, pump_start_idx - 6)
        if history_start_idx >= pump_start_idx:
            return False

        prev_ranges = prepared.highs[history_start_idx:pump_start_idx] - prepared.lows[history_start_idx:pump_start_idx]
        prev_bodies = np.abs(prepared.closes[history_start_idx:pump_start_idx] - prepared.opens[history_start_idx:pump_start_idx])
        prev_quote_volumes = prepared.quote_volume[history_start_idx:pump_start_idx]
        if prev_ranges.size == 0 or prev_bodies.size == 0 or prev_quote_volumes.size == 0:
            return False

        current_range = float(prepared.highs[pump_start_idx] - prepared.lows[pump_start_idx])
        current_body = float(abs(prepared.closes[pump_start_idx] - prepared.opens[pump_start_idx]))
        current_quote_volume = float(prepared.quote_volume[pump_start_idx])
        prev_range_median = float(np.median(prev_ranges))
        prev_body_median = float(np.median(prev_bodies))
        prev_quote_volume_median = float(np.median(prev_quote_volumes))
        prior_high = float(np.max(prepared.highs[history_start_idx:pump_start_idx]))
        current_close = float(prepared.closes[pump_start_idx])

        return (
            current_range >= max(prev_range_median * 3.0, self._EPSILON)
            and current_body >= max(prev_body_median * 3.0, self._EPSILON)
            and current_quote_volume >= max(prev_quote_volume_median * 4.0, self._EPSILON)
            and current_close > prior_high
            and current_body >= (current_range * 0.5)
        )

    def _initial_move_directional_efficiency(
        self,
        *,
        prepared: _PreparedStage1Frame,
        pump_start_idx: int,
        peak_idx: int,
    ) -> float:
        segment_end_idx = min(peak_idx, pump_start_idx + 6)
        closes = prepared.closes[pump_start_idx : segment_end_idx + 1]
        if closes.size < 3:
            return 1.0
        diffs = np.diff(closes)
        path = float(np.sum(np.abs(diffs)))
        if path <= self._EPSILON:
            return 1.0
        net = float(abs(closes[-1] - closes[0]))
        return net / path

    def _is_launch_sequence_start(
        self,
        *,
        prepared: _PreparedStage1Frame,
        pump_start_idx: int,
        candidate_idx: int,
        peak_idx: int,
    ) -> bool:
        history_start_idx = max(pump_start_idx, candidate_idx - 4)
        if history_start_idx >= candidate_idx:
            return False

        future_end_idx = min(peak_idx + 1, candidate_idx + 3)
        if future_end_idx <= candidate_idx:
            return False

        prev_ranges = prepared.highs[history_start_idx:candidate_idx] - prepared.lows[history_start_idx:candidate_idx]
        prev_quote_volumes = prepared.quote_volume[history_start_idx:candidate_idx]
        if prev_ranges.size == 0 or prev_quote_volumes.size == 0:
            return False

        future_ranges = prepared.highs[candidate_idx:future_end_idx] - prepared.lows[candidate_idx:future_end_idx]
        future_quote_volumes = prepared.quote_volume[candidate_idx:future_end_idx]
        if future_ranges.size == 0 or future_quote_volumes.size == 0:
            return False

        prev_range_median = float(np.median(prev_ranges))
        prev_quote_volume_median = float(np.median(prev_quote_volumes))
        future_range_mean = float(np.mean(future_ranges))
        future_quote_volume_mean = float(np.mean(future_quote_volumes))
        prior_high = float(np.max(prepared.highs[pump_start_idx:candidate_idx]))
        future_high = float(np.max(prepared.highs[candidate_idx:future_end_idx]))
        start_close = float(prepared.closes[pump_start_idx])
        max_prior_close = float(np.max(prepared.closes[pump_start_idx:candidate_idx]))
        prior_close_drift = ((max_prior_close / start_close) - 1.0) if start_close > self._EPSILON else 0.0

        return (
            future_quote_volume_mean >= max(prev_quote_volume_median * 1.75, self._EPSILON)
            and future_range_mean >= max(prev_range_median * 1.5, self._EPSILON)
            and future_high > (prior_high + self._EPSILON)
            and prior_close_drift < 0.02
        )

    def _is_explosive_launch_bar(
        self,
        *,
        prepared: _PreparedStage1Frame,
        pump_start_idx: int,
        idx: int,
    ) -> bool:
        history_start_idx = max(pump_start_idx, idx - 6)
        if history_start_idx >= idx:
            return False

        prev_ranges = prepared.highs[history_start_idx:idx] - prepared.lows[history_start_idx:idx]
        prev_bodies = np.abs(prepared.closes[history_start_idx:idx] - prepared.opens[history_start_idx:idx])
        prev_quote_volumes = prepared.quote_volume[history_start_idx:idx]
        if prev_ranges.size == 0 or prev_bodies.size == 0 or prev_quote_volumes.size == 0:
            return False

        median_range = float(np.median(prev_ranges))
        median_body = float(np.median(prev_bodies))
        median_quote_volume = float(np.median(prev_quote_volumes))
        current_range = float(prepared.highs[idx] - prepared.lows[idx])
        current_body = float(abs(prepared.closes[idx] - prepared.opens[idx]))
        current_quote_volume = float(prepared.quote_volume[idx])
        prior_high = float(np.max(prepared.highs[pump_start_idx:idx]))
        start_close = float(prepared.closes[pump_start_idx])
        max_prior_close = float(np.max(prepared.closes[pump_start_idx:idx]))
        prior_close_drift = ((max_prior_close / start_close) - 1.0) if start_close > self._EPSILON else 0.0

        return (
            current_range >= max(median_range * 6.0, self._EPSILON)
            and current_body >= max(median_body * 6.0, self._EPSILON)
            and current_quote_volume >= max(median_quote_volume * 10.0, self._EPSILON)
            and float(prepared.closes[idx]) > prior_high
            and prior_close_drift < 0.02
        )

    def _effective_highs(self, prepared: _PreparedStage1Frame) -> np.ndarray:
        body_highs = np.maximum(prepared.opens, prepared.closes)
        body_sizes = np.abs(prepared.closes - prepared.opens)
        upper_wicks = prepared.highs - body_highs
        return np.where(upper_wicks > body_sizes, body_highs, prepared.highs)

    def _effective_high_at(self, *, prepared: _PreparedStage1Frame, idx: int) -> float:
        body_high = max(float(prepared.opens[idx]), float(prepared.closes[idx]))
        body_size = abs(float(prepared.closes[idx]) - float(prepared.opens[idx]))
        upper_wick = float(prepared.highs[idx]) - body_high
        if upper_wick > body_size:
            return body_high
        return float(prepared.highs[idx])

    def _breakout_high_at(self, *, prepared: _PreparedStage1Frame, peak_idx: int, breakout_idx: int) -> float:
        if (breakout_idx - peak_idx) <= 2:
            return float(prepared.highs[breakout_idx])
        return self._effective_high_at(prepared=prepared, idx=breakout_idx)

    def _has_stepped_initial_volume(self, *, prepared: _PreparedStage1Frame, pump_start_idx: int) -> bool:
        window_end_idx = min(len(prepared.quote_volume) - 1, pump_start_idx + self._pump_window_bars - 1)
        initial_quote_volume = prepared.quote_volume[pump_start_idx : window_end_idx + 1]
        if initial_quote_volume.size < 3:
            return False

        segments = [segment for segment in np.array_split(initial_quote_volume, 3) if segment.size > 0]
        if len(segments) < 3:
            return False

        segment_means = [float(np.mean(segment)) for segment in segments]
        return (
            segment_means[0] < segment_means[1] < segment_means[2]
            and segment_means[2] >= (segment_means[0] * 1.2)
        )

    def _finalize_candidate_regime(
        self,
        *,
        prepared: _PreparedStage1Frame,
        candidate: "_Stage1Candidate",
    ) -> "_Stage1Candidate":
        current_peak_idx = int(candidate.peak_idx)
        current_peak_price = float(candidate.pump_peak_price)
        current_hold_base_idx = int(candidate.hold_base_idx)
        current_hold_base_price = float(candidate.hold_base_price)
        regime_end_idx = min(
            len(prepared.timestamps) - 1,
            current_peak_idx + self._max_confirm_delay_bars,
        )
        idx = current_peak_idx + 1

        while idx < len(prepared.timestamps):
            hold_price = current_hold_base_price + (
                (current_peak_price - current_hold_base_price) * self._min_retain_ratio
            )
            current_low = float(prepared.lows[idx])
            current_high = self._breakout_high_at(
                prepared=prepared,
                peak_idx=current_peak_idx,
                breakout_idx=idx,
            )

            if current_low < hold_price:
                regime_end_idx = idx
                break
            if current_high > (current_peak_price + self._EPSILON):
                breakout_is_significant = self._is_significant_breakout_above_main_high(
                    prepared=prepared,
                    peak_idx=current_peak_idx,
                    breakout_idx=idx,
                    main_high_price=current_peak_price,
                )
                had_long_balance_before_breakout = breakout_is_significant and (
                    (idx - current_peak_idx) >= self._min_confirm_delay_bars
                )
                if not breakout_is_significant:
                    idx += 1
                    continue
                if had_long_balance_before_breakout and current_hold_base_idx == candidate.pump_start_idx:
                    first_hold_low_idx = self._resolve_hold_base_idx_before_breakout(
                        prepared=prepared,
                        peak_idx=current_peak_idx,
                        breakout_idx=idx,
                    )
                    if first_hold_low_idx is not None:
                        current_hold_base_idx = first_hold_low_idx
                        current_hold_base_price = float(prepared.lows[first_hold_low_idx])
                current_peak_idx = idx
                current_peak_price = current_high
                regime_end_idx = min(
                    len(prepared.timestamps) - 1,
                    current_peak_idx + self._max_confirm_delay_bars,
                )
                idx += 1
                continue
            if idx >= regime_end_idx:
                break
            idx += 1
        else:
            regime_end_idx = len(prepared.timestamps) - 1

        return _Stage1Candidate(
            sleep_start_idx=candidate.sleep_start_idx,
            sleep_end_idx=candidate.sleep_end_idx,
            pump_start_idx=candidate.pump_start_idx,
            peak_idx=current_peak_idx,
            regime_end_idx=regime_end_idx,
            sleep_avg_volume_usdt=candidate.sleep_avg_volume_usdt,
            pump_base_price=candidate.pump_base_price,
            pump_peak_price=current_peak_price,
            hold_base_idx=current_hold_base_idx,
            hold_base_price=current_hold_base_price,
            pump_percent=(current_peak_price / candidate.pump_base_price) - 1.0,
        )

    def _has_upper_hold_before_breakout(
        self,
        *,
        prepared: _PreparedStage1Frame,
        peak_idx: int,
        breakout_idx: int,
        hold_price: float,
        peak_price: float,
    ) -> bool:
        bars_since_peak = breakout_idx - peak_idx
        if bars_since_peak < self._min_confirm_delay_bars:
            return False

        hold_start_idx = breakout_idx - self._min_confirm_delay_bars
        hold_lows = prepared.lows[hold_start_idx:breakout_idx]
        hold_highs = self._effective_highs(prepared)[hold_start_idx:breakout_idx]
        if hold_lows.size < self._min_confirm_delay_bars:
            return False
        if np.any(hold_lows < hold_price):
            return False
        if np.any(hold_highs > (peak_price + self._EPSILON)):
            return False
        return True

    def _resolve_hold_base_idx_before_breakout(
        self,
        *,
        prepared: _PreparedStage1Frame,
        peak_idx: int,
        breakout_idx: int,
    ) -> int | None:
        if breakout_idx <= (peak_idx + 1):
            return None
        hold_lows = prepared.lows[peak_idx + 1 : breakout_idx]
        if hold_lows.size == 0:
            return None
        hold_low_offset = int(np.argmin(hold_lows))
        return peak_idx + 1 + hold_low_offset

    def _is_significant_breakout_above_main_high(
        self,
        *,
        prepared: _PreparedStage1Frame,
        peak_idx: int,
        breakout_idx: int,
        main_high_price: float,
    ) -> bool:
        breakout_excess = self._breakout_high_at(
            prepared=prepared,
            peak_idx=peak_idx,
            breakout_idx=breakout_idx,
        ) - main_high_price
        if breakout_excess <= self._EPSILON:
            return False
        candle_sizes = prepared.highs[peak_idx : breakout_idx + 1] - prepared.lows[peak_idx : breakout_idx + 1]
        mean_candle_size = float(np.mean(candle_sizes)) if candle_sizes.size > 0 else 0.0
        bars_since_peak = breakout_idx - peak_idx
        if bars_since_peak <= 2:
            return breakout_excess >= max(mean_candle_size * 0.1, self._EPSILON)
        return breakout_excess >= max(mean_candle_size, self._EPSILON)

    def _is_sleep_window_valid(
        self,
        *,
        start_idx: int,
        sleep_window_range_pct: np.ndarray,
    ) -> bool:
        sleep_end_idx = start_idx - 1
        if sleep_end_idx < 0:
            return False
        sleep_begin_idx = max(self._pump_window_bars - 1, start_idx - self._sleep_window_bars)
        if sleep_begin_idx > sleep_end_idx:
            return False

        sleep_ranges = sleep_window_range_pct[sleep_begin_idx : sleep_end_idx + 1]
        if sleep_ranges.size == 0:
            return False
        valid_ranges = sleep_ranges[np.isfinite(sleep_ranges)]
        if valid_ranges.size == 0:
            return False
        return bool(np.nanmax(valid_ranges) < self._min_pump_pct)

    def _evaluate_candidate_at_index(
        self,
        *,
        symbol: str,
        prepared: _PreparedStage1Frame,
        candidate: "_Stage1Candidate",
        confirm_idx: int,
        rolling_volume_usdt: float | None,
    ) -> BeeBiteStage1Result | None:
        if confirm_idx <= candidate.peak_idx or confirm_idx >= len(prepared.timestamps):
            return None

        bars_since_peak = confirm_idx - candidate.peak_idx
        initial_pump_duration_bars = candidate.peak_idx - candidate.pump_start_idx
        post_peak_lows = prepared.lows[candidate.peak_idx + 1 : confirm_idx + 1]
        lowest_after_pump_offset = int(np.argmin(post_peak_lows))
        lowest_after_pump_idx = candidate.peak_idx + 1 + lowest_after_pump_offset
        lowest_after_pump = float(post_peak_lows[lowest_after_pump_offset])
        hold_price = candidate.hold_base_price + (
            (candidate.pump_peak_price - candidate.hold_base_price) * self._min_retain_ratio
        )
        sleep_closes = prepared.closes[candidate.sleep_start_idx : candidate.sleep_end_idx + 1]
        sleep_closes_below_hold_ratio = float(np.mean(sleep_closes < hold_price))
        confirm_candle_low = float(prepared.lows[confirm_idx])
        confirm_candle_close = float(prepared.closes[confirm_idx])
        retain_ratio = (lowest_after_pump - candidate.hold_base_price) / max(
            candidate.pump_peak_price - candidate.hold_base_price,
            self._EPSILON,
        )
        post_pump_avg_volume_usdt = self._mean_range(
            prepared.cumulative_quote_volume,
            start_idx=candidate.pump_start_idx,
            end_idx=confirm_idx,
        )
        post_pump_volume_ratio = post_pump_avg_volume_usdt / max(candidate.sleep_avg_volume_usdt, self._EPSILON)

        result = BeeBiteStage1Result(
            symbol=symbol,
            passed=False,
            reason="passed",
            rolling_volume_usdt=rolling_volume_usdt,
            sleep_avg_volume_usdt=candidate.sleep_avg_volume_usdt,
            post_pump_avg_volume_usdt=post_pump_avg_volume_usdt,
            post_pump_volume_ratio=post_pump_volume_ratio,
            pump_percent=candidate.pump_percent,
            retain_ratio=retain_ratio,
            sleep_start_timestamp=int(prepared.timestamps[candidate.sleep_start_idx]),
            sleep_end_timestamp=int(prepared.timestamps[candidate.sleep_end_idx]),
            pump_start_timestamp=int(prepared.timestamps[candidate.pump_start_idx]),
            pump_peak_timestamp=int(prepared.timestamps[candidate.peak_idx]),
            stage1_confirmed_timestamp=int(prepared.timestamps[confirm_idx]),
            pump_base_price=candidate.pump_base_price,
            pump_peak_price=candidate.pump_peak_price,
            hold_base_price=candidate.hold_base_price,
            hold_base_timestamp=int(prepared.timestamps[candidate.hold_base_idx]),
            hold_price=hold_price,
            lowest_after_pump=lowest_after_pump,
            lowest_after_pump_timestamp=int(prepared.timestamps[lowest_after_pump_idx]),
        )

        if lowest_after_pump < hold_price:
            result.reason = "retain_below_half"
            return result
        if initial_pump_duration_bars > self._max_initial_pump_duration_bars:
            result.reason = "initial_pump_too_long"
            return result
        if bars_since_peak < self._min_confirm_delay_bars:
            result.reason = "confirm_before_min_hold"
            return result
        if bars_since_peak > self._max_confirm_delay_bars:
            result.reason = "confirm_after_max_hold"
            return result
        if sleep_closes_below_hold_ratio <= 0.5:
            result.reason = "sleep_closes_not_below_hold_majority"
            return result
        if confirm_candle_low < hold_price or confirm_candle_close > candidate.pump_peak_price:
            result.reason = "confirm_candle_outside_pump_band"
            return result
        if post_pump_avg_volume_usdt < self._EPSILON or post_pump_volume_ratio < self._min_volume_ratio:
            result.reason = "volume_ratio_below_15x"
            return result
        if rolling_volume_usdt is None or rolling_volume_usdt < self._min_volume_usdt:
            result.reason = "volume_below_20m"
            return result

        result.passed = True
        return result

    @staticmethod
    def _mean_range(cumulative_values: np.ndarray, *, start_idx: int, end_idx: int) -> float:
        total = float(cumulative_values[end_idx])
        if start_idx > 0:
            total -= float(cumulative_values[start_idx - 1])
        count = (end_idx - start_idx) + 1
        return total / max(count, 1)

    @staticmethod
    def _safe_float(value: object) -> float | None:
        if isinstance(value, (float, int)) and np.isfinite(value):
            return float(value)
        return None


@dataclass(slots=True)
class _Stage1Candidate:
    sleep_start_idx: int
    sleep_end_idx: int
    pump_start_idx: int
    peak_idx: int
    regime_end_idx: int
    sleep_avg_volume_usdt: float
    pump_base_price: float
    pump_peak_price: float
    hold_base_idx: int
    hold_base_price: float
    pump_percent: float
