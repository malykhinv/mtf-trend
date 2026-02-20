"""Оценка уровней (rolling-экстремумы + score-фильтр качества)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class LevelDetectorConfig:
    """Параметры оценки уровня."""

    tolerance_atr_mult: float = 0.5
    min_level_score: float = 2.0


class LevelDetector:
    """Строит кандидаты уровней и оставляет только качественные по score."""

    def __init__(self, config: LevelDetectorConfig | None = None) -> None:
        self._config = config or LevelDetectorConfig()

    @staticmethod
    def _safe_mean(values: list[float]) -> float:
        if not values:
            return 0.0
        return float(np.mean(values))

    def _score_level(
        self,
        *,
        highs: np.ndarray,
        lows: np.ndarray,
        level: float,
        atr: float,
        is_resistance: bool,
    ) -> tuple[int, float, float, int, float, float, list[int]]:
        atr_safe = max(float(atr), 1e-12)
        tolerance = self._config.tolerance_atr_mult * atr_safe

        touches: list[int] = []
        for idx in range(len(highs)):
            probe_price = highs[idx] if is_resistance else lows[idx]
            if abs(float(probe_price) - float(level)) <= tolerance:
                touches.append(idx)

        touch_count = len(touches)
        min_bars_between_touches = 0
        if touch_count >= 2:
            min_bars_between_touches = min(touches[idx] - touches[idx - 1] for idx in range(1, touch_count))

        penetration_atr_values: list[float] = []
        penetration_pct_values: list[float] = []
        for touch_idx in touches:
            if is_resistance:
                penetration = max(0.0, float(highs[touch_idx]) - float(level))
            else:
                penetration = max(0.0, float(level) - float(lows[touch_idx]))
            penetration_atr_values.append(penetration / atr_safe)
            penetration_pct_values.append(penetration / max(abs(float(level)), 1e-12))
        max_penetration_atr = max(penetration_atr_values, default=0.0)
        max_penetration_pct = max(penetration_pct_values, default=0.0)

        reactions: list[float] = []
        for touch_position, touch_idx in enumerate(touches):
            next_touch_idx = touches[touch_position + 1] if touch_position + 1 < touch_count else len(highs)
            if next_touch_idx - touch_idx <= 1:
                continue
            window_slice = slice(touch_idx + 1, next_touch_idx)
            if is_resistance:
                rebound = max(0.0, (float(level) - float(np.min(lows[window_slice]))) / atr_safe)
            else:
                rebound = max(0.0, (float(np.max(highs[window_slice])) - float(level)) / atr_safe)
            reactions.append(rebound)
        reaction_strength = self._safe_mean(reactions)

        penetrations_atr: list[float] = []
        if is_resistance:
            for probe_high in highs:
                deep_penetration = max(0.0, (float(probe_high) - float(level) - tolerance) / atr_safe)
                penetrations_atr.append(deep_penetration)
        else:
            for probe_low in lows:
                deep_penetration = max(0.0, (float(level) - float(probe_low) - tolerance) / atr_safe)
                penetrations_atr.append(deep_penetration)
        avg_deep_penetration = self._safe_mean(penetrations_atr)
        cleanliness = 1.0 / (1.0 + avg_deep_penetration)

        level_score = float(touch_count) + reaction_strength + cleanliness
        return (
            touch_count,
            reaction_strength,
            level_score,
            min_bars_between_touches,
            max_penetration_atr,
            max_penetration_pct,
            touches,
        )

    def detect(self, *, higher_base: pd.DataFrame, lookback: int) -> pd.DataFrame:
        """Возвращает уровни-кандидаты с метриками и score-фильтром."""
        if len(higher_base) < lookback + 2:
            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "level_high",
                    "level_low",
                    "level_start_time",
                    "touch_count",
                    "reaction_strength",
                    "level_score",
                    "resistance_touch_count",
                    "resistance_min_bars_between_touches",
                    "resistance_max_penetration_atr",
                    "resistance_max_penetration_pct",
                    "resistance_touch_timestamps_ms",
                    "support_touch_count",
                    "support_min_bars_between_touches",
                    "support_max_penetration_atr",
                    "support_max_penetration_pct",
                    "support_touch_timestamps_ms",
                ],
            )

        frame = higher_base.copy().sort_values("timestamp").reset_index(drop=True)

        highs = frame["high"].to_numpy(dtype="float64", copy=False)
        lows = frame["low"].to_numpy(dtype="float64", copy=False)
        close = frame["close"].to_numpy(dtype="float64", copy=False)
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum.reduce(
            [
                highs - lows,
                np.abs(highs - prev_close),
                np.abs(lows - prev_close),
            ],
        )
        atr = pd.Series(tr).rolling(window=max(2, int(lookback)), min_periods=max(2, int(lookback))).mean().to_numpy()

        frame["level_high"] = frame["high"].rolling(window=lookback).max().shift(1)
        frame["level_low"] = frame["low"].rolling(window=lookback).min().shift(1)
        level_start_times: list[float] = []

        touch_counts: list[float] = []
        reaction_strengths: list[float] = []
        level_scores: list[float] = []
        resistance_touch_counts: list[float] = []
        resistance_min_bars_between_touches_values: list[float] = []
        resistance_max_penetration_atr_values: list[float] = []
        resistance_max_penetration_pct_values: list[float] = []
        resistance_touch_timestamps_values: list[list[int]] = []
        support_touch_counts: list[float] = []
        support_min_bars_between_touches_values: list[float] = []
        support_max_penetration_atr_values: list[float] = []
        support_max_penetration_pct_values: list[float] = []
        support_touch_timestamps_values: list[list[int]] = []

        for idx in range(len(frame)):
            candidate_high = frame.at[idx, "level_high"]
            candidate_low = frame.at[idx, "level_low"]
            atr_value = atr[idx]
            if pd.isna(candidate_high) or pd.isna(candidate_low) or pd.isna(atr_value) or idx < lookback:
                level_start_times.append(np.nan)
                touch_counts.append(np.nan)
                reaction_strengths.append(np.nan)
                level_scores.append(np.nan)
                resistance_touch_counts.append(np.nan)
                resistance_min_bars_between_touches_values.append(np.nan)
                resistance_max_penetration_atr_values.append(np.nan)
                resistance_max_penetration_pct_values.append(np.nan)
                resistance_touch_timestamps_values.append([])
                support_touch_counts.append(np.nan)
                support_min_bars_between_touches_values.append(np.nan)
                support_max_penetration_atr_values.append(np.nan)
                support_max_penetration_pct_values.append(np.nan)
                support_touch_timestamps_values.append([])
                continue

            window_slice = slice(idx - lookback, idx)
            window_timestamps = frame["timestamp"].iloc[window_slice].to_numpy(dtype="int64", copy=False)
            level_start_times.append(float(window_timestamps[0]))
            (
                high_touch_count,
                high_reaction,
                high_score,
                high_min_bars_between_touches,
                high_max_penetration_atr,
                high_max_penetration_pct,
                high_touch_indices,
            ) = self._score_level(
                highs=highs[window_slice],
                lows=lows[window_slice],
                level=float(candidate_high),
                atr=float(atr_value),
                is_resistance=True,
            )
            (
                low_touch_count,
                low_reaction,
                low_score,
                low_min_bars_between_touches,
                low_max_penetration_atr,
                low_max_penetration_pct,
                low_touch_indices,
            ) = self._score_level(
                highs=highs[window_slice],
                lows=lows[window_slice],
                level=float(candidate_low),
                atr=float(atr_value),
                is_resistance=False,
            )

            merged_touch_count = float(high_touch_count + low_touch_count)
            merged_reaction = (high_reaction + low_reaction) / 2.0
            merged_score = (high_score + low_score) / 2.0

            if merged_score < self._config.min_level_score:
                frame.at[idx, "level_high"] = np.nan
                frame.at[idx, "level_low"] = np.nan

            touch_counts.append(merged_touch_count)
            reaction_strengths.append(merged_reaction)
            level_scores.append(merged_score)
            resistance_touch_counts.append(float(high_touch_count))
            resistance_min_bars_between_touches_values.append(float(high_min_bars_between_touches))
            resistance_max_penetration_atr_values.append(float(high_max_penetration_atr))
            resistance_max_penetration_pct_values.append(float(high_max_penetration_pct))
            resistance_touch_timestamps_values.append([int(window_timestamps[touch_idx]) for touch_idx in high_touch_indices])
            support_touch_counts.append(float(low_touch_count))
            support_min_bars_between_touches_values.append(float(low_min_bars_between_touches))
            support_max_penetration_atr_values.append(float(low_max_penetration_atr))
            support_max_penetration_pct_values.append(float(low_max_penetration_pct))
            support_touch_timestamps_values.append([int(window_timestamps[touch_idx]) for touch_idx in low_touch_indices])

        frame["level_start_time"] = level_start_times
        frame["touch_count"] = touch_counts
        frame["reaction_strength"] = reaction_strengths
        frame["level_score"] = level_scores
        frame["resistance_touch_count"] = resistance_touch_counts
        frame["resistance_min_bars_between_touches"] = resistance_min_bars_between_touches_values
        frame["resistance_max_penetration_atr"] = resistance_max_penetration_atr_values
        frame["resistance_max_penetration_pct"] = resistance_max_penetration_pct_values
        frame["resistance_touch_timestamps_ms"] = resistance_touch_timestamps_values
        frame["support_touch_count"] = support_touch_counts
        frame["support_min_bars_between_touches"] = support_min_bars_between_touches_values
        frame["support_max_penetration_atr"] = support_max_penetration_atr_values
        frame["support_max_penetration_pct"] = support_max_penetration_pct_values
        frame["support_touch_timestamps_ms"] = support_touch_timestamps_values

        frame = frame.dropna(
            subset=[
                "level_high",
                "level_low",
                "level_start_time",
                "touch_count",
                "reaction_strength",
                "level_score",
                "resistance_touch_count",
                "resistance_min_bars_between_touches",
                "resistance_max_penetration_atr",
                "resistance_max_penetration_pct",
                "support_touch_count",
                "support_min_bars_between_touches",
                "support_max_penetration_atr",
                "support_max_penetration_pct",
            ]
        )
        return frame[
            [
                "timestamp",
                "level_high",
                "level_low",
                "level_start_time",
                "touch_count",
                "reaction_strength",
                "level_score",
                "resistance_touch_count",
                "resistance_min_bars_between_touches",
                "resistance_max_penetration_atr",
                "resistance_max_penetration_pct",
                "resistance_touch_timestamps_ms",
                "support_touch_count",
                "support_min_bars_between_touches",
                "support_max_penetration_atr",
                "support_max_penetration_pct",
                "support_touch_timestamps_ms",
            ]
        ].reset_index(drop=True)
