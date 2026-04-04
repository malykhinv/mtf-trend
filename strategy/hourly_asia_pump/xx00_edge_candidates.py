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
    status="historical_candidate_invalidated_online",
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
    readiness="invalidated_by_online_watchlist_backtest",
    notes=(
        "Historical best-known Asia XX:00 long from the post-hoc 5m-universe phase. "
        "The 2026-04-05 fully online watchlist backtest did not confirm it as a live-ready edge. "
        "Keep only as research history, not as a validated production candidate."
    ),
)


XX00_EUROPE_1M_LAUNCH_OVERLAY = XX00LongLaunchCandidate(
    candidate_id="C10_xx00_europe_1m_launch_overlay",
    label="XX:00 Europe 1m Launch Overlay",
    status="exploratory_not_confirmed_online",
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
    readiness="exploratory_only",
    notes=(
        "Europe still has some current-positive online combos, but the fully online "
        "2026-04-05 backtest did not find an old-selected rule that survived honestly on current. "
        "Keep as research only."
    ),
)


XX00_VALIDATED_EDGE_CANDIDATES = ()


XX00_EXPLORATORY_EDGE_CANDIDATES = (
    XX00_ASIA_1M_LAUNCH_CORE,
    XX00_EUROPE_1M_LAUNCH_OVERLAY,
)


XX00_EDGE_CANDIDATES = (
    *XX00_VALIDATED_EDGE_CANDIDATES,
    *XX00_EXPLORATORY_EDGE_CANDIDATES,
)
