"""Stage-1 фильтр монет для стратегии bee_bite."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from constants import (
    BEE_BITE_STAGE1_MIN_PUMP_PCT,
    BEE_BITE_STAGE1_MIN_RETAIN_RATIO,
    BEE_BITE_STAGE1_MIN_VOLUME_USDT,
    BEE_BITE_STAGE1_PUMP_WINDOW_BARS,
    BEE_BITE_STAGE1_VOLUME_WINDOW_BARS_15M,
)


@dataclass(slots=True)
class BeeBiteStage1Result:
    symbol: str
    passed: bool
    reason: str
    rolling_volume_usdt: float | None = None
    pump_percent: float | None = None
    retain_ratio: float | None = None
    pump_start_timestamp: int | None = None
    pump_peak_timestamp: int | None = None
    pump_base_price: float | None = None
    pump_peak_price: float | None = None
    hold_price: float | None = None
    lowest_after_pump: float | None = None


class BeeBiteStage1Selector:
    """Отбирает монеты по базовым условиям первого этапа bee_bite."""

    def __init__(
        self,
        *,
        min_volume_usdt: float = BEE_BITE_STAGE1_MIN_VOLUME_USDT,
        min_pump_pct: float = BEE_BITE_STAGE1_MIN_PUMP_PCT,
        min_retain_ratio: float = BEE_BITE_STAGE1_MIN_RETAIN_RATIO,
        pump_window_bars: int = BEE_BITE_STAGE1_PUMP_WINDOW_BARS,
        volume_window_bars: int = BEE_BITE_STAGE1_VOLUME_WINDOW_BARS_15M,
    ) -> None:
        self._min_volume_usdt = float(min_volume_usdt)
        self._min_pump_pct = float(min_pump_pct)
        self._min_retain_ratio = float(min_retain_ratio)
        self._pump_window_bars = int(pump_window_bars)
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

        min_required_rows = max(self._volume_window_bars, self._pump_window_bars + 2)
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

        suffix_min_low = np.empty_like(lows)
        current_min = float("inf")
        for idx in range(len(lows) - 1, -1, -1):
            current_min = min(current_min, float(lows[idx]))
            suffix_min_low[idx] = current_min

        saw_volume_candidate = False
        saw_pump_candidate = False
        latest_match: BeeBiteStage1Result | None = None

        for end_idx in range(self._pump_window_bars, len(prepared)):
            start_idx = end_idx - self._pump_window_bars + 1
            before_idx = start_idx - 1
            if before_idx < 0:
                continue

            rolling_volume_usdt = float(rolling_volume[end_idx])
            if not np.isfinite(rolling_volume_usdt):
                continue
            if rolling_volume_usdt < self._min_volume_usdt:
                continue
            saw_volume_candidate = True

            pump_highs = highs[start_idx : end_idx + 1]
            peak_offset = int(np.argmax(pump_highs))
            peak_idx = start_idx + peak_offset
            if peak_idx + 1 >= len(prepared):
                continue

            pump_base_price = float(lows[before_idx])
            pump_peak_price = float(pump_highs[peak_offset])
            if pump_base_price <= 0 or pump_peak_price <= pump_base_price:
                continue

            pump_percent = (pump_peak_price / pump_base_price) - 1.0
            if pump_percent < self._min_pump_pct:
                continue
            saw_pump_candidate = True

            hold_price = pump_base_price + ((pump_peak_price - pump_base_price) * self._min_retain_ratio)
            lowest_after_pump = float(suffix_min_low[peak_idx + 1])
            retain_ratio = (lowest_after_pump - pump_base_price) / max(pump_peak_price - pump_base_price, 1e-12)
            if lowest_after_pump < hold_price:
                continue

            candidate = BeeBiteStage1Result(
                symbol=symbol,
                passed=True,
                reason="passed",
                rolling_volume_usdt=rolling_volume_usdt,
                pump_percent=pump_percent,
                retain_ratio=retain_ratio,
                pump_start_timestamp=int(timestamps[start_idx]),
                pump_peak_timestamp=int(timestamps[peak_idx]),
                pump_base_price=pump_base_price,
                pump_peak_price=pump_peak_price,
                hold_price=hold_price,
                lowest_after_pump=lowest_after_pump,
            )
            if latest_match is None:
                latest_match = candidate
                continue
            if candidate.pump_peak_timestamp is not None and latest_match.pump_peak_timestamp is not None:
                if candidate.pump_peak_timestamp >= latest_match.pump_peak_timestamp:
                    latest_match = candidate

        if latest_match is not None:
            return latest_match
        if not saw_volume_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="volume_below_20m")
        if not saw_pump_candidate:
            return BeeBiteStage1Result(symbol=symbol, passed=False, reason="pump_below_20pct")
        return BeeBiteStage1Result(symbol=symbol, passed=False, reason="dumped_below_half")
