from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BeeBiteStage4PostmortemParams:
    min_rr: float
    tp3_multiplier: float
    sweep_size_multiplier: float
    stop_mode: str
    tp1_stop_mode: str
    pump_minute_filter: str
    timing_session_filter: str
    tp1_share: float
    tp2_share: float
    tp3_share: float


def _build_default_stage4_grid() -> tuple[BeeBiteStage4PostmortemParams, ...]:
    # Optimized research grid for stage-4 execution:
    # - backup year-long run showed RR=1.25 as weak on both 15m and 5m
    # - current 15m full run shows intermediate RR steps 1.75 and 2.25 do not
    #   add a distinct frontier regime versus neighboring anchors 1.50/2.00/2.50/3.00
    # - tp3 multiplier 1.0 underperformed 2.0/3.0 on both 15m and 5m
    # - two share profiles were consistently weakest on both timeframes:
    #   0.75/0.25/0.00 and 1.00/0.00/0.00
    rr_grid = (1.50, 2.00, 2.50, 3.00)
    tp3_multipliers = (2.0, 3.0)
    sweep_size_multipliers = (1.0, 2.0, 3.0, 4.0)
    stop_modes = ("sweep_low", "entry_minus_avg_body")
    tp1_stop_modes = ("entry", "last_red_low")
    pump_minute_filters = ("any", "minute_00_or_30")
    timing_session_filters = ("any", "pump_not_us_overlap_and_sweep_not_europe")
    share_profiles = (
        (0.50, 0.25, 0.25),
        (0.50, 0.50, 0.00),
        (0.75, 0.00, 0.25),
    )
    grid: list[BeeBiteStage4PostmortemParams] = []
    for min_rr in rr_grid:
        for tp3_multiplier in tp3_multipliers:
            for sweep_size_multiplier in sweep_size_multipliers:
                for stop_mode in stop_modes:
                    for tp1_stop_mode in tp1_stop_modes:
                        for pump_minute_filter in pump_minute_filters:
                            for timing_session_filter in timing_session_filters:
                                for tp1_share, tp2_share, tp3_share in share_profiles:
                                    grid.append(
                                        BeeBiteStage4PostmortemParams(
                                            min_rr=min_rr,
                                            tp3_multiplier=tp3_multiplier,
                                            sweep_size_multiplier=sweep_size_multiplier,
                                            stop_mode=stop_mode,
                                            tp1_stop_mode=tp1_stop_mode,
                                            pump_minute_filter=pump_minute_filter,
                                            timing_session_filter=timing_session_filter,
                                            tp1_share=tp1_share,
                                            tp2_share=tp2_share,
                                            tp3_share=tp3_share,
                                        )
                                    )
    return tuple(grid)


DEFAULT_BEE_BITE_STAGE4_POSTMORTEM_GRID: tuple[BeeBiteStage4PostmortemParams, ...] = _build_default_stage4_grid()


def get_bee_bite_stage4_postmortem_grid() -> tuple[BeeBiteStage4PostmortemParams, ...]:
    return DEFAULT_BEE_BITE_STAGE4_POSTMORTEM_GRID
