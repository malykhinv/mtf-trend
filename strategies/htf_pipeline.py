from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from config.config import AppConfig, AtrConfig, VolumeConfig
from domain.models.candle import Candle
from domain.models.pump import Pump
from indicators import VolumeCheckResult, compute_atr, compute_volume_zscore
from strategies.pullback import (
    PullbackValidationResult,
    validate_pullback_on_H,
)
from strategies.pump_detection import detect_pump_candle_on_H

_DEFAULT_MAX_PULLBACK_FRACTION = 0.40


def _find_candle_index(candles: Sequence[Candle], target: Candle) -> int | None:
    for index, candle in enumerate(candles):
        if candle == target:
            return index
    return None


@dataclass(frozen=True)
class HtfPipelineResult:
    atr_values: list[float]
    volume: VolumeCheckResult
    pump: Pump | None
    pullback: PullbackValidationResult
    ready_for_l_analysis: bool
    analysis_start_index: int | None


@dataclass
class HtfPipeline:
    atr_config: AtrConfig
    volume_config: VolumeConfig
    max_pullback_fraction: float = _DEFAULT_MAX_PULLBACK_FRACTION

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        max_pullback_fraction: float | None = None,
    ) -> HtfPipeline:
        fraction = (
            max_pullback_fraction
            if max_pullback_fraction is not None
            else _DEFAULT_MAX_PULLBACK_FRACTION
        )
        return cls(
            atr_config=config.atr,
            volume_config=config.volume,
            max_pullback_fraction=fraction,
        )

    def run(
        self,
        candles: Sequence[Candle],
        existing_pump: Pump | None = None,
    ) -> HtfPipelineResult:
        atr_values = compute_atr(candles, self.atr_config)
        volume_result = compute_volume_zscore(candles, self.volume_config)

        pump_candidate = detect_pump_candle_on_H(candles, atr_values, self.atr_config)
        if pump_candidate is None:
            pump_candidate = existing_pump

        pump_index = None
        if pump_candidate is not None:
            pump_index = _find_candle_index(candles, pump_candidate.candle)
        pump = pump_candidate if pump_index is not None else None

        if pump is not None and pump_index is not None:
            candles_after_pump = candles[pump_index + 1 :]
        else:
            candles_after_pump = []

        pullback_result = PullbackValidationResult(False, None, None)
        if pump is not None:
            if candles_after_pump:
                pullback_result = validate_pullback_on_H(
                    candles_after_pump,
                    pump,
                    self.max_pullback_fraction,
                )
            else:
                pullback_result = PullbackValidationResult(False, None, None)

        ready = (
            volume_result.allowed
            and not volume_result.skipped
            and pump is not None
            and pullback_result.is_valid
        )

        analysis_start_index = pump_index + 1 if pump_index is not None else None

        return HtfPipelineResult(
            atr_values=atr_values,
            volume=volume_result,
            pump=pump,
            pullback=pullback_result,
            ready_for_l_analysis=ready,
            analysis_start_index=analysis_start_index,
        )


__all__ = ["HtfPipeline", "HtfPipelineResult"]
