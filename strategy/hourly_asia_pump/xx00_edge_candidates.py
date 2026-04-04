from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class XX00LongLaunchCandidate:
    candidate_id: str
    label: str
    status: str
    cohort_id: str
    rule_id: str
    min_m0_return_pct: float
    min_m0_close_pos: float
    min_m0_volume_ratio: float
    require_break_prev240: bool
    rr_target: float
    entry_delay_minutes: int
    stop_style: str


XX00_ASIA_1M_LAUNCH_CORE = XX00LongLaunchCandidate(
    candidate_id="C09_xx00_asia_1m_launch_core",
    label="XX:00 Asia 1m Launch Core",
    status="validated_asia_core",
    cohort_id="long_union",
    rule_id="long_launch_r010_c65_v04_p0_rr20",
    min_m0_return_pct=0.01,
    min_m0_close_pos=0.65,
    min_m0_volume_ratio=4.0,
    require_break_prev240=False,
    rr_target=2.0,
    entry_delay_minutes=1,
    stop_style="m0_low",
)


XX00_EDGE_CANDIDATES = (
    XX00_ASIA_1M_LAUNCH_CORE,
)
