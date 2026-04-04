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
    session_id: str = "asia"
    best_exit_template_id: str = "fixed_rr20"
    best_exit_label: str = "Fixed 2R"
    alternate_exit_template_id: str | None = None
    alternate_exit_label: str | None = None
    entry_signal_bar: str = "m0_close"
    entry_fill_bar: str = "m1_open"
    readiness: str = "research_only"
    notes: str = ""


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
    session_id="asia",
    best_exit_template_id="fixed_rr30",
    best_exit_label="Fixed 3R",
    entry_signal_bar="m0_close",
    entry_fill_bar="m1_open",
    readiness="validated_research_core",
    notes=(
        "Best-known Asia XX:00 long. Entry rule passed bias checks, "
        "and dedicated exit research now favors Fixed 3R over the original 2R. "
        "Still needs a fully online watchlist builder before live deployment."
    ),
)


XX00_EUROPE_1M_LAUNCH_OVERLAY = XX00LongLaunchCandidate(
    candidate_id="C10_xx00_europe_1m_launch_overlay",
    label="XX:00 Europe 1m Launch Overlay",
    status="exploratory_best_ready",
    cohort_id="europe__long_union",
    rule_id="long_launch_r010_c65_v04_p0_rr20",
    min_m0_return_pct=0.01,
    min_m0_close_pos=0.65,
    min_m0_volume_ratio=4.0,
    require_break_prev240=False,
    rr_target=2.0,
    entry_delay_minutes=1,
    stop_style="m0_low",
    session_id="europe",
    best_exit_template_id="fixed_rr30",
    best_exit_label="Fixed 3R",
    alternate_exit_template_id="fixed_rr20",
    alternate_exit_label="Fixed 2R",
    entry_signal_bar="m0_close",
    entry_fill_bar="m1_open",
    readiness="exploratory_overlay",
    notes=(
        "Best-known Europe transfer of the Asia XX:00 launch rule. "
        "Promising on session-separated research, but still exploratory because "
        "the event universe comes from post-hoc 5m selection and has not passed "
        "a clean untouched-holdout deployment check."
    ),
)


XX00_VALIDATED_EDGE_CANDIDATES = (
    XX00_ASIA_1M_LAUNCH_CORE,
)


XX00_EXPLORATORY_EDGE_CANDIDATES = (
    XX00_EUROPE_1M_LAUNCH_OVERLAY,
)


XX00_EDGE_CANDIDATES = (
    *XX00_VALIDATED_EDGE_CANDIDATES,
    *XX00_EXPLORATORY_EDGE_CANDIDATES,
)
