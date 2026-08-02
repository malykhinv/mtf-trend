"""Phase-0 REGISTRATION for the level-setup research (structure_break first).

Every definition, gate, feature, entry/exit and sweep grid here is FROZEN BEFORE
the broad IS population's outcomes are inspected — the anti-mining discipline of
the roadmap (docs/strategies/level_setup_research_roadmap.md). Researcher degrees
of freedom (zigzag threshold, staleness cap, detection TF, exit grids) are
declared a priori as GRIDS, not tuned against outcomes.

Scope now: structure_break. breakout / cap register the same way later.
Nothing here reads outcomes; it is pure declaration.
"""
from __future__ import annotations

from dataclasses import dataclass, field

LEVEL_SETUP_REGISTRY_VERSION = "level_setup_registry_v1_2026-07-11"

# IS boundary is owned by pump_long.research.context.DEV_END_MS; all phases 0-6
# stay strictly below it. Forward outcome windows are censored at DEV_END_MS.


# --------------------------------------------------------------------------- #
# structure_break — setup definition + validity gates (FROZEN)                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StructureBreakDefinition:
    """main high -> series of DESCENDING swing highs -> long on break of the
    LAST (lowest) swing high. All swings causal (confirmed before the break)."""

    # Detection timeframes to consider (soft prior: 3m clearer). A priori set.
    detection_tfs: tuple[str, ...] = ("3m", "5m", "10m", "15m")
    # Swing granularity = zigzag reversal threshold in ATR units. SWEPT, not tuned.
    zigzag_atr_grid: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0)
    # A pivot is confirmed only after this many bars close on the far side of it
    # (causal — the swing must be knowable before the entry bar).
    pivot_confirmation_bars: int = 2
    # Minimum number of descending swing highs after the main high.
    min_descending_highs: int = 2
    # Each swing high must be <= previous * (1 + tol) (descending, small tolerance).
    descending_tolerance: float = 0.0
    # Last swing high must sit below the main high (headroom must exist).
    require_headroom: bool = True
    # Staleness: the break must occur within this many detection-TF bars of the
    # main high, else the structure is stale. A priori cap.
    max_bars_main_high_to_break: int = 200
    # ATR window for the causal ATR used by swings/trail.
    atr_window: int = 30


STRUCTURE_BREAK_DEFINITION = StructureBreakDefinition()


# --------------------------------------------------------------------------- #
# Candidate feature list (FROZEN) + expected sign vs a GOOD (profitable) setup  #
# sign: +1 higher=better, -1 higher=worse, 0 unknown/two-sided. Used in Phase 3 #
# only to compare discovered signs against pre-registered expectations.         #
# --------------------------------------------------------------------------- #
STRUCTURE_BREAK_FEATURES: dict[str, int] = {
    # pump impulse
    "pump_pct": 0,
    "pump_hours": 0,
    "pump_verticality": -1,          # steeper pump -> worse (user rule, IS-confirmed)
    "pump_path_eff": +1,
    "pump_maxbar_pct": -1,
    # descending-swing series (the defining structure)
    "n_swings": -1,                  # more swings = messier
    "desc_highs_slope": 0,
    "desc_highs_resid": -1,          # ragged descent = worse
    "swing_leg_eff_mean": +1,        # clean legs = better
    "swing_depth_mean": -1,          # deep swings = worse
    "swing_depth_trend": 0,
    "swing_vol_trend": -1,           # volume drying across swings = accumulation
    "swing_trades_trend": -1,
    "seller_pressure": -1,           # down-leg vs up-leg pressure weakening = better
    "swing_wick_share_trend": 0,
    "internal_higher_lows": +1,      # early reversal inside the descent
    "last_swing_to_main_high": +1,   # headroom to run (IS strongest)
    # approach (last swing low -> break)
    "approach_eff": +1,
    "approach_slope": +1,
    "approach_vol_ramp_rel": +1,     # normalized; honestly testable only on stalls
    "approach_vol_ramp_1m_rel": +1,  # user's key metric (needs stall population)
    "approach_trades_ramp_1m_rel": +1,
    # break bar (causal for close entry)
    "break_close_over_swing": -1,    # chasing an already-extended break = worse
    "break_upper_wick": -1,
    "break_vol_ratio": +1,
    # context
    "wave_number": -1,               # later wave = worse (user rule; candle-counted)
    "prior_pumps_48h": +1,           # hot coin runs (runner-portrait; opposite scale)
    "price_vs_daily_ema": +1,
    "price_vs_daily_vwap": +1,
    "hour_of_day": 0,
    "day_of_week": 0,
    # geometry
    "stop_dist_pct": -1,
}


# --------------------------------------------------------------------------- #
# Entry modes (FROZEN). All long, fill = next TF open + slip; no fake limits.   #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EntryModes:
    # close: first TF bar closing above the last swing high -> enter next open.
    # break: first TF bar whose high exceeds it -> enter next open (<= close).
    # antic: first approach bar with a 1m volume/trade surge while price still
    #        below the level -> enter next open, tighter micro-swing stop.
    modes: tuple[str, ...] = ("close", "break", "antic")
    # anticipation surge grid (multiple over pump-baseline 1m volume) and window.
    antic_surge_mult_grid: tuple[float, ...] = (1.25, 1.5, 2.0, 3.0)
    antic_surge_window_1m: tuple[int, ...] = (10, 15, 30)
    antic_tight_stop_bars: int = 3
    # NOTE: anticipation is validatable ONLY on the broad population that includes
    # STALLS (failed approaches). On the all-break manual set it is a selection leak.


ENTRY_MODES = EntryModes()


# --------------------------------------------------------------------------- #
# Exit policies (FROZEN) — all through the frozen core simulate_long_path.      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ExitPolicies:
    # default: structural swing-low trail (confirmation threshold in ATR).
    swing_reversal_atr_grid: tuple[float, ...] = (1.0, 1.5, 2.0)
    # close-into-impulse: exit when a bar/leg exceeds k*ATR within m bars of entry.
    impulse_atr_grid: tuple[float, ...] = (2.0, 3.0, 4.0)
    impulse_window_bars: tuple[int, ...] = (3, 5)
    # alternative fixed targets, tested separately.
    n_r_grid: tuple[float, ...] = (1.0, 2.0, 3.0)          # plus "trail-only" (no target)
    round_number_target: bool = True
    resistance_target: bool = True                          # e.g. pump high (cap)
    # partial close fraction at a target, remainder trailed.
    partial_close_grid: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)


EXIT_POLICIES = ExitPolicies()


# --------------------------------------------------------------------------- #
# Outcome, objective, dedup, controls (FROZEN)                                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class OutcomeAndObjective:
    # Outcome via frozen core (costs built in): net_r, fwd_mfe_r, fwd_mae_r.
    outcome_metric: str = "frozen_core_net_r"
    forward_censor_at_dev_end: bool = True
    # Multi-criteria objective (balance, not winrate-at-any-cost).
    objective_terms: tuple[str, ...] = (
        "trades_per_day", "winrate", "realized_r_median", "positive_days_frac",
        "independence_from_tops",
    )
    trades_per_day_floor: float = 3.0
    # independence-from-tops: result must stay positive after removing top-k.
    independence_top_k_grid: tuple[int, ...] = (1, 3, 5)


OUTCOME_AND_OBJECTIVE = OutcomeAndObjective()


@dataclass(frozen=True, slots=True)
class ValidationProtocol:
    dedup_rule: str = "one_pump_event_per_symbol_is_one_trade"
    overlapping_same_symbol_not_independent: bool = True
    cv: str = "weekly_group_kfold_within_is"
    controls: tuple[str, ...] = ("blind_base_rate", "shuffled_label")
    is_only_until_final_freeze: bool = True


VALIDATION_PROTOCOL = ValidationProtocol()


__all__ = [
    "LEVEL_SETUP_REGISTRY_VERSION",
    "STRUCTURE_BREAK_DEFINITION",
    "STRUCTURE_BREAK_FEATURES",
    "ENTRY_MODES",
    "EXIT_POLICIES",
    "OUTCOME_AND_OBJECTIVE",
    "VALIDATION_PROTOCOL",
]
