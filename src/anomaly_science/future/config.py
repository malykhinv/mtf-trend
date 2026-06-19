from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.future.atr import ATR_1D_WINDOW_MINUTES


FUTURE_PATH_BUILDER_VERSION = "atr_normalized_future_paths_v1"
FUTURE_RETURN_HORIZONS_MINUTES = (5, 15, 30, 60, 120, 180)
FUTURE_RECLAIM_HORIZONS_MINUTES = (30, 60)


@dataclass(frozen=True, slots=True)
class FuturePathBuilderConfig:
    """Configuration for raw and ATR-normalized future path construction.

    The horizons define post-snapshot outcome windows. They are not labels,
    trade holding periods, entry rules, exits, or PnL assumptions. ATR is
    computed strictly as-of each snapshot and is only emitted when enough
    closed 1m history exists; the builder must not invent a fallback ATR.
    """

    future_return_horizons_minutes: tuple[int, ...] = FUTURE_RETURN_HORIZONS_MINUTES
    future_reclaim_horizons_minutes: tuple[int, ...] = FUTURE_RECLAIM_HORIZONS_MINUTES
    atr_window_minutes: int = ATR_1D_WINDOW_MINUTES
    double_barrier_k_continuation: float = 1.0
    double_barrier_k_fade: float = 1.0
    future_path_builder_version: str = FUTURE_PATH_BUILDER_VERSION

    def __post_init__(self) -> None:
        if self.future_return_horizons_minutes != FUTURE_RETURN_HORIZONS_MINUTES:
            raise ValueError(
                "anomaly_future_paths.csv schema requires horizons "
                f"{FUTURE_RETURN_HORIZONS_MINUTES}"
            )
        if self.future_reclaim_horizons_minutes != FUTURE_RECLAIM_HORIZONS_MINUTES:
            raise ValueError(
                "anomaly_future_paths.csv schema requires reclaim horizons "
                f"{FUTURE_RECLAIM_HORIZONS_MINUTES}"
            )
        if self.atr_window_minutes <= 0:
            raise ValueError("atr_window_minutes must be positive")
        if self.double_barrier_k_continuation <= 0:
            raise ValueError("double_barrier_k_continuation must be positive")
        if self.double_barrier_k_fade <= 0:
            raise ValueError("double_barrier_k_fade must be positive")
        if not self.future_path_builder_version:
            raise ValueError("future_path_builder_version is required")
