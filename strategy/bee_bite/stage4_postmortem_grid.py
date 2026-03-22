from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BeeBiteStage4PostmortemParams:
    min_rr: float
    tp3_multiplier: float
    tp1_share: float
    tp2_share: float
    tp3_share: float


def _build_default_stage4_grid() -> tuple[BeeBiteStage4PostmortemParams, ...]:
    # Optimized research grid for stage-4 execution:
    # - skip RR <= 1.0 as structurally weak
    # - keep denser search in the currently relevant center
    # - keep only practical partial-take profiles
    rr_grid = (1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 3.00)
    tp3_multipliers = (1.0, 2.0, 3.0)
    share_profiles = (
        (0.50, 0.25, 0.25),
        (0.50, 0.50, 0.00),
        (0.75, 0.25, 0.00),
        (0.75, 0.00, 0.25),
        (1.00, 0.00, 0.00),
    )
    grid: list[BeeBiteStage4PostmortemParams] = []
    for min_rr in rr_grid:
        for tp3_multiplier in tp3_multipliers:
            for tp1_share, tp2_share, tp3_share in share_profiles:
                grid.append(
                    BeeBiteStage4PostmortemParams(
                        min_rr=min_rr,
                        tp3_multiplier=tp3_multiplier,
                        tp1_share=tp1_share,
                        tp2_share=tp2_share,
                        tp3_share=tp3_share,
                    )
                )
    return tuple(grid)


DEFAULT_BEE_BITE_STAGE4_POSTMORTEM_GRID: tuple[BeeBiteStage4PostmortemParams, ...] = _build_default_stage4_grid()


def get_bee_bite_stage4_postmortem_grid() -> tuple[BeeBiteStage4PostmortemParams, ...]:
    return DEFAULT_BEE_BITE_STAGE4_POSTMORTEM_GRID
