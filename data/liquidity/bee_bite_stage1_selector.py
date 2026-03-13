"""Stage-1 filter for the bee_bite strategy."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from constants import (
    BEE_BITE_STAGE1_MIN_POST_PUMP_HOLD_BARS,
    BEE_BITE_STAGE1_MIN_PUMP_PCT,
    BEE_BITE_STAGE1_MIN_RETAIN_RATIO,
    BEE_BITE_STAGE1_MIN_VOLUME_RATIO,
    BEE_BITE_STAGE1_MIN_VOLUME_USDT,
    BEE_BITE_STAGE1_PUMP_WINDOW_BARS,
    BEE_BITE_STAGE1_SLEEP_WINDOW_BARS_15M,
    BEE_BITE_STAGE1_SWEEP_RATIO,
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
    pump_start_timestamp: int | None = None
    pump_peak_timestamp: int | None = None
    correction_start_timestamp: int | None = None
    correction_end_timestamp: int | None = None
    pump_base_price: float | None = None
    pump_peak_price: float | None = None
    hold_price: float | None = None
    lowest_after_pump: float | None = None
    hold_bars: int | None = None
    sweep_count: int | None = None


class BeeBiteStage1Selector:
    """Select assets that match sleep -> pump -> upper-hold stage-1 logic."""

    _EPSILON = 1e-12

    def __init__(
        self,
        *,
        min_volume_usdt: float = BEE_BITE_STAGE1_MIN_VOLUME_USDT,
        min_pump_pct: float = BEE_BITE_STAGE1_MIN_PUMP_PCT,
        min_retain_ratio: float = BEE_BITE_STAGE1_MIN_RETAIN_RATIO,
        min_volume_ratio: float = BEE_BITE_STAGE1_MIN_VOLUME_RATIO,
        sleep_window_bars: int = BEE_BITE_STAGE1_SLEEP_WINDOW_BARS_15M,
        min_hold_bars: int = BEE_BITE_STAGE1_MIN_POST_PUMP_HOLD_BARS,
        pump_window_bars: int = BEE_BITE_STAGE1_PUMP_WINDOW_BARS,
        sweep_ratio: float = BEE_BITE_STAGE1_SWEEP_RATIO,
        volume_window_bars: int = BEE_BITE_STAGE1_VOLUME_WINDOW_BARS_15M,
    ) -> None:
        self._min_volume_usdt = float(min_volume_usdt)
        self._min_pump_pct = float(min_pump_pct)
        self._min_retain_ratio = float(min_retain_ratio)
        self._min_volume_ratio = float(min_volume_ratio)
        self._sleep_window_bars = int(sleep_window_bars)
        self._min_hold_bars = int(min_hold_bars)
        self._pump_window_bars = int(pump_window_bars)
        self._sweep_ratio = float(sweep_ratio)
        self._volume_window_bars = int(volume_window_bars)

    def evaluate_symbol(self, *, symbol: str, frame: pd.DataFrame) -> BeeBiteStage1Result:
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
            self._sleep_window_bars + self._pump_window_bars + self._min_hold_bars + 1,
            self._volume_window_bars,
        )
        if len(prepared) < min_required_rows:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="insufficient_history")

        timestamps = prepared["timestamp"].astype("int64").to_numpy()
        highs = prepared["high"].astype("float64").to_numpy()
        lows = prepared["low"].astype("float64").to_numpy()
        closes = prepared["close"].astype("float64").to_numpy()
        volumes = prepared["volume"].astype("float64").to_numpy()
        quote_volume = closes * volumes

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

        saw_sleep_candidate = False
        saw_pump_candidate = False
        saw_volume_ratio_candidate = False
        saw_hold_candidate = False
        saw_volume_24h_candidate = False
        saw_upper_half_failure = False
        latest_match: BeeBiteStage1Result | None = None

        last_candidate_start = len(prepared) - self._pump_window_bars - self._min_hold_bars
        for start_idx in range(self._sleep_window_bars, last_candidate_start + 1):
            if not self._is_sleep_window_valid(
                start_idx=start_idx,
                sleep_window_range_pct=sleep_window_range_pct,
            ):
                continue
            saw_sleep_candidate = True

            sleep_avg_volume_usdt = float(sleep_volume_mean[start_idx])
            if not np.isfinite(sleep_avg_volume_usdt) or sleep_avg_volume_usdt <= self._EPSILON:
                continue

            window_end_idx = start_idx + self._pump_window_bars - 1
            pump_highs = highs[start_idx : window_end_idx + 1]
            peak_offset = int(np.argmax(pump_highs))
            peak_idx = start_idx + peak_offset

            pump_base_offset = int(np.argmin(lows[start_idx : peak_idx + 1]))
            pump_start_idx = start_idx + pump_base_offset
            pump_base_price = float(lows[pump_start_idx])
            pump_peak_price = float(highs[peak_idx])
            if pump_base_price <= self._EPSILON or pump_peak_price <= pump_base_price:
                continue

            pump_percent = (pump_peak_price / pump_base_price) - 1.0
            if pump_percent < self._min_pump_pct:
                continue
            saw_pump_candidate = True

            candidate = self._evaluate_hold_after_peak(
                symbol=symbol,
                timestamps=timestamps,
                highs=highs,
                lows=lows,
                quote_volume=quote_volume,
                rolling_volume=rolling_volume,
                sleep_avg_volume_usdt=sleep_avg_volume_usdt,
                pump_start_idx=pump_start_idx,
                peak_idx=peak_idx,
                pump_base_price=pump_base_price,
                pump_peak_price=pump_peak_price,
                pump_percent=pump_percent,
            )
            if candidate is None:
                continue

            if candidate.post_pump_volume_ratio is not None and candidate.post_pump_volume_ratio >= self._min_volume_ratio:
                saw_volume_ratio_candidate = True
            if candidate.hold_bars is not None and candidate.hold_bars >= self._min_hold_bars:
                saw_hold_candidate = True
            if candidate.rolling_volume_usdt is not None and candidate.rolling_volume_usdt >= self._min_volume_usdt:
                saw_volume_24h_candidate = True
            if candidate.reason == "hold_not_in_upper_half":
                saw_upper_half_failure = True

            if candidate.passed:
                if latest_match is None:
                    latest_match = candidate
                elif (
                    candidate.pump_peak_timestamp is not None
                    and latest_match.pump_peak_timestamp is not None
                    and candidate.pump_peak_timestamp >= latest_match.pump_peak_timestamp
                ):
                    latest_match = candidate

        if latest_match is not None:
            return latest_match
        if not saw_sleep_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="sleep_not_dormant")
        if not saw_pump_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="pump_below_15pct")
        if saw_upper_half_failure:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="hold_not_in_upper_half")
        if not saw_hold_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="hold_under_4h")
        if not saw_volume_ratio_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="volume_ratio_below_15x")
        if not saw_volume_24h_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="volume_below_20m")
        return BeeBiteStage1Result(symbol=symbol, passed=False, reason="hold_not_in_upper_half")

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

    def _evaluate_hold_after_peak(
        self,
        *,
        symbol: str,
        timestamps: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        quote_volume: np.ndarray,
        rolling_volume: np.ndarray,
        sleep_avg_volume_usdt: float,
        pump_start_idx: int,
        peak_idx: int,
        pump_base_price: float,
        pump_peak_price: float,
        pump_percent: float,
    ) -> BeeBiteStage1Result | None:
        current_high_idx = peak_idx
        current_high_price = pump_peak_price
        correction_low_idx: int | None = None
        sweep_count = 0

        for idx in range(peak_idx + 1, len(highs)):
            if correction_low_idx is None or lows[idx] < lows[correction_low_idx]:
                correction_low_idx = idx

            lowest_after_pump = float(lows[correction_low_idx])
            hold_price = pump_base_price + ((current_high_price - pump_base_price) * self._min_retain_ratio)
            retain_ratio = (lowest_after_pump - pump_base_price) / max(current_high_price - pump_base_price, self._EPSILON)

            if lowest_after_pump < hold_price:
                return BeeBiteStage1Result(
                    symbol=symbol,
                    passed=False,
                    reason="hold_not_in_upper_half",
                    pump_percent=pump_percent,
                    retain_ratio=retain_ratio,
                    pump_start_timestamp=int(timestamps[pump_start_idx]),
                    pump_peak_timestamp=int(timestamps[current_high_idx]),
                    correction_start_timestamp=int(timestamps[current_high_idx + 1]) if current_high_idx + 1 < len(timestamps) else None,
                    correction_end_timestamp=int(timestamps[idx]),
                    pump_base_price=pump_base_price,
                    pump_peak_price=current_high_price,
                    hold_price=hold_price,
                    lowest_after_pump=lowest_after_pump,
                    hold_bars=idx - current_high_idx,
                    sweep_count=sweep_count,
                )

            if highs[idx] > current_high_price:
                correction_low_price = lowest_after_pump
                sweep_limit = self._sweep_ratio * max(current_high_price - correction_low_price, 0.0)
                high_extension = float(highs[idx] - current_high_price)
                if high_extension > sweep_limit + self._EPSILON:
                    current_high_idx = idx
                    current_high_price = float(highs[idx])
                    correction_low_idx = None
                    continue
                sweep_count += 1

            hold_bars = idx - current_high_idx
            if hold_bars < self._min_hold_bars:
                continue

            post_pump_avg_volume_usdt = float(np.mean(quote_volume[pump_start_idx : idx + 1]))
            if post_pump_avg_volume_usdt < self._EPSILON:
                return BeeBiteStage1Result(symbol=symbol, passed=False, reason="volume_ratio_below_15x")
            post_pump_volume_ratio = post_pump_avg_volume_usdt / max(sleep_avg_volume_usdt, self._EPSILON)
            if post_pump_volume_ratio < self._min_volume_ratio:
                return BeeBiteStage1Result(
                    symbol=symbol,
                    passed=False,
                    reason="volume_ratio_below_15x",
                    sleep_avg_volume_usdt=sleep_avg_volume_usdt,
                    post_pump_avg_volume_usdt=post_pump_avg_volume_usdt,
                    post_pump_volume_ratio=post_pump_volume_ratio,
                    pump_percent=pump_percent,
                    retain_ratio=retain_ratio,
                    pump_start_timestamp=int(timestamps[pump_start_idx]),
                    pump_peak_timestamp=int(timestamps[current_high_idx]),
                    correction_start_timestamp=int(timestamps[current_high_idx + 1]) if current_high_idx + 1 < len(timestamps) else None,
                    correction_end_timestamp=int(timestamps[idx]),
                    pump_base_price=pump_base_price,
                    pump_peak_price=current_high_price,
                    hold_price=hold_price,
                    lowest_after_pump=lowest_after_pump,
                    hold_bars=hold_bars,
                    sweep_count=sweep_count,
                )

            rolling_volume_usdt = float(rolling_volume[idx])
            if not np.isfinite(rolling_volume_usdt) or rolling_volume_usdt < self._min_volume_usdt:
                return BeeBiteStage1Result(
                    symbol=symbol,
                    passed=False,
                    reason="volume_below_20m",
                    rolling_volume_usdt=rolling_volume_usdt if np.isfinite(rolling_volume_usdt) else None,
                    sleep_avg_volume_usdt=sleep_avg_volume_usdt,
                    post_pump_avg_volume_usdt=post_pump_avg_volume_usdt,
                    post_pump_volume_ratio=post_pump_volume_ratio,
                    pump_percent=pump_percent,
                    retain_ratio=retain_ratio,
                    pump_start_timestamp=int(timestamps[pump_start_idx]),
                    pump_peak_timestamp=int(timestamps[current_high_idx]),
                    correction_start_timestamp=int(timestamps[current_high_idx + 1]) if current_high_idx + 1 < len(timestamps) else None,
                    correction_end_timestamp=int(timestamps[idx]),
                    pump_base_price=pump_base_price,
                    pump_peak_price=current_high_price,
                    hold_price=hold_price,
                    lowest_after_pump=lowest_after_pump,
                    hold_bars=hold_bars,
                    sweep_count=sweep_count,
                )

            return BeeBiteStage1Result(
                symbol=symbol,
                passed=True,
                reason="passed",
                rolling_volume_usdt=rolling_volume_usdt,
                sleep_avg_volume_usdt=sleep_avg_volume_usdt,
                post_pump_avg_volume_usdt=post_pump_avg_volume_usdt,
                post_pump_volume_ratio=post_pump_volume_ratio,
                pump_percent=pump_percent,
                retain_ratio=retain_ratio,
                pump_start_timestamp=int(timestamps[pump_start_idx]),
                pump_peak_timestamp=int(timestamps[current_high_idx]),
                correction_start_timestamp=int(timestamps[current_high_idx + 1]) if current_high_idx + 1 < len(timestamps) else None,
                correction_end_timestamp=int(timestamps[idx]),
                pump_base_price=pump_base_price,
                pump_peak_price=current_high_price,
                hold_price=hold_price,
                lowest_after_pump=lowest_after_pump,
                hold_bars=hold_bars,
                sweep_count=sweep_count,
            )

        return None
