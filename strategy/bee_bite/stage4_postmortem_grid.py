from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BeeBiteStage4PostmortemParams:
    min_rr: float


def _build_default_stage4_grid() -> tuple[BeeBiteStage4PostmortemParams, ...]:
    # Full first-pass research grid for stage-4 execution filtering.
    return tuple(
        BeeBiteStage4PostmortemParams(min_rr=round(step * 0.25, 2))
        for step in range(1, 13)
    )


DEFAULT_BEE_BITE_STAGE4_POSTMORTEM_GRID: tuple[BeeBiteStage4PostmortemParams, ...] = _build_default_stage4_grid()


def get_bee_bite_stage4_postmortem_grid() -> tuple[BeeBiteStage4PostmortemParams, ...]:
    return DEFAULT_BEE_BITE_STAGE4_POSTMORTEM_GRID
