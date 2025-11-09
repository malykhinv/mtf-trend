from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Sequence

from config.config import VolumeConfig
from domain.models.candle import Candle


@dataclass(frozen=True)
class VolumeCheckResult:
    z_score: float | None
    allowed: bool
    skipped: bool


def compute_volume_zscore(candles: Sequence[Candle], config: VolumeConfig) -> VolumeCheckResult:
    if config.n_vol_h <= 0:
        raise ValueError("N_VOL_H must be positive")
    if len(candles) < config.n_vol_h:
        return VolumeCheckResult(z_score=None, allowed=False, skipped=True)

    window = candles[-config.n_vol_h :]
    volumes = [candle.volume for candle in window]
    mean_volume = fmean(volumes)
    std_volume = pstdev(volumes)
    if math.isclose(std_volume, 0.0, abs_tol=1e-12):
        return VolumeCheckResult(z_score=None, allowed=False, skipped=True)

    latest_volume = volumes[-1]
    z_score = (latest_volume - mean_volume) / std_volume
    allowed = z_score >= config.z_min
    return VolumeCheckResult(z_score=z_score, allowed=allowed, skipped=False)


__all__ = ["VolumeCheckResult", "compute_volume_zscore"]
