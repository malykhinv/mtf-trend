from __future__ import annotations

from dataclasses import dataclass


FUTURE_PATH_BUILDER_VERSION = "raw_future_paths_v1"
FUTURE_RETURN_HORIZONS_MINUTES = (5, 15, 30, 60)
FUTURE_RECLAIM_HORIZONS_MINUTES = (30, 60)


@dataclass(frozen=True, slots=True)
class FuturePathBuilderConfig:
    """Configuration for MVP1 raw future path construction.

    The horizons define raw post-snapshot outcome windows. They are not labels,
    trade holding periods, entry rules, exits, or PnL assumptions.
    """

    future_return_horizons_minutes: tuple[int, ...] = FUTURE_RETURN_HORIZONS_MINUTES
    future_reclaim_horizons_minutes: tuple[int, ...] = FUTURE_RECLAIM_HORIZONS_MINUTES
    future_path_builder_version: str = FUTURE_PATH_BUILDER_VERSION

    def __post_init__(self) -> None:
        if self.future_return_horizons_minutes != FUTURE_RETURN_HORIZONS_MINUTES:
            raise ValueError(
                "MVP1 anomaly_future_paths.csv schema requires horizons "
                f"{FUTURE_RETURN_HORIZONS_MINUTES}"
            )
        if self.future_reclaim_horizons_minutes != FUTURE_RECLAIM_HORIZONS_MINUTES:
            raise ValueError(
                "MVP1 anomaly_future_paths.csv schema requires reclaim horizons "
                f"{FUTURE_RECLAIM_HORIZONS_MINUTES}"
            )
        if not self.future_path_builder_version:
            raise ValueError("future_path_builder_version is required")
