from __future__ import annotations

from anomaly_science.regimes import (
    CausalRegimeAtlasConfig,
    RegimeAxisSpec,
    RegimeInteractionComponent,
    RegimeInteractionSpec,
)


PUMP_FADE_REGIME_AXES: tuple[RegimeAxisSpec, ...] = (
    RegimeAxisSpec(
        feature_name="rel_vol_phase",
        kind="quantile",
        cuts=(0.90, 0.95, 0.98, 0.99),
        candidate_bins=(1, 2, 3, 4),
    ),
    RegimeAxisSpec(
        feature_name="atr_mult",
        kind="fixed",
        cuts=(1.0, 2.0, 3.0, 5.0),
        candidate_bins=(1, 2, 3, 4),
    ),
    RegimeAxisSpec(
        feature_name="pump_elapsed_min",
        kind="fixed",
        cuts=(15.0, 30.0, 60.0, 120.0),
        candidate_bins=(0, 1, 2, 3, 4),
    ),
    RegimeAxisSpec("decision_index", "fixed", (1.5, 2.5, 3.5, 5.5, 8.5), (0, 1, 2, 3, 4, 5)),
    RegimeAxisSpec("verticality", "quantile", (0.20, 0.80, 0.95), (0, 1, 2, 3)),
    RegimeAxisSpec("current_upper_wick_fraction", "quantile", (0.50, 0.80, 0.95), (0, 1, 2, 3)),
    RegimeAxisSpec("taker_decline", "quantile", (0.20, 0.80), (0, 1, 2)),
    RegimeAxisSpec("event_path_efficiency", "quantile", (0.20, 0.50, 0.80), (0, 1, 2, 3)),
    RegimeAxisSpec("close_drawdown_from_high", "fixed", (0.005, 0.015, 0.03), (0, 1, 2, 3)),
    RegimeAxisSpec("range_contraction", "quantile", (0.20, 0.80), (0, 1, 2)),
    RegimeAxisSpec("n_prior_48h", "fixed", (0.5, 1.5, 2.5), (0, 1, 2, 3)),
    RegimeAxisSpec("n_prior_fades_48h", "fixed", (0.5, 1.5), (1, 2)),
    RegimeAxisSpec("recency_weighted_prior_fade_rate_48h", "fixed", (0.25, 0.75), (0, 1, 2)),
    RegimeAxisSpec("prior_1_current_vs_peak", "fixed", (-0.02, 0.0, 0.02), (0, 1, 2, 3)),
    RegimeAxisSpec("pre_return_60m", "quantile", (0.20, 0.80), (0, 1, 2)),
    RegimeAxisSpec("pump_size", "fixed", (0.05, 0.10, 0.20), (1, 2, 3)),
)


def _interaction(
    interaction_id: str,
    components: tuple[tuple[str, int], ...],
    rationale: str,
) -> RegimeInteractionSpec:
    return RegimeInteractionSpec(
        interaction_id=interaction_id,
        components=tuple(
            RegimeInteractionComponent(feature_name=name, bin_index=bin_index)
            for name, bin_index in components
        ),
        rationale=rationale,
    )


PUMP_FADE_REGIME_INTERACTIONS: tuple[RegimeInteractionSpec, ...] = (
    _interaction("extreme_activity_x_first_rehigh", (("rel_vol_phase", 4), ("decision_index", 0)), "Extreme participation may exhaust at the first admissible re-high."),
    _interaction("extreme_activity_x_second_rehigh", (("rel_vol_phase", 4), ("decision_index", 1)), "Extreme participation may remain informative at the second re-high."),
    _interaction("extreme_activity_x_taker_decline", (("rel_vol_phase", 4), ("taker_decline", 2)), "Extreme activity combined with weakening aggressive buying is an exhaustion mechanism."),
    _interaction("extreme_activity_x_range_contraction", (("rel_vol_phase", 4), ("range_contraction", 2)), "Participation without continuing range expansion may indicate absorption."),
    _interaction("high_verticality_x_upper_wick", (("verticality", 3), ("current_upper_wick_fraction", 3)), "A vertical move rejected near the candle high is a structural fade mechanism."),
    _interaction("high_verticality_x_taker_decline", (("verticality", 3), ("taker_decline", 2)), "Fast price expansion with declining taker pressure may be exhaustion."),
    _interaction("efficient_path_x_upper_wick", (("event_path_efficiency", 3), ("current_upper_wick_fraction", 3)), "A one-directional path followed by rejection may separate exhaustion from chop."),
    _interaction("drawdown_x_taker_decline", (("close_drawdown_from_high", 2), ("taker_decline", 2)), "Loss of the high together with flow decay may precede a fade."),
    _interaction("drawdown_x_range_contraction", (("close_drawdown_from_high", 2), ("range_contraction", 2)), "Price rejection and contracting range may mark failed continuation."),
    _interaction("repeat_event_x_extreme_activity", (("n_prior_48h", 2), ("rel_vol_phase", 4)), "Repeated pumps may respond differently to a new extreme activity shock."),
    _interaction("prior_fade_x_below_prior_peak", (("n_prior_fades_48h", 1), ("prior_1_current_vs_peak", 1)), "A new pump below the latest resolved peak may encounter persistent structural supply."),
    _interaction("multiple_prior_fades_x_below_peak", (("n_prior_fades_48h", 2), ("prior_1_current_vs_peak", 1)), "Repeated resolved fades below the latest peak may indicate recurrent exhaustion."),
    _interaction("prior_fade_dominance_x_taker_decline", (("recency_weighted_prior_fade_rate_48h", 2), ("taker_decline", 2)), "Recent fade-dominant memory may matter only when current buying weakens."),
    _interaction("prior_fade_dominance_x_upper_wick", (("recency_weighted_prior_fade_rate_48h", 2), ("current_upper_wick_fraction", 3)), "Past fade recurrence plus current rejection is a cross-event structural hypothesis."),
    _interaction("large_pump_x_upper_wick", (("pump_size", 3), ("current_upper_wick_fraction", 3)), "Large displacement with strong rejection may identify exhaustion."),
    _interaction("positive_pretrend_x_taker_decline", (("pre_return_60m", 2), ("taker_decline", 2)), "A preconditioned rise may fade when current aggressive buying decays."),
    _interaction("extreme_activity_x_verticality_x_wick", (("rel_vol_phase", 4), ("verticality", 3), ("current_upper_wick_fraction", 3)), "Extreme activity, verticality, and rejection jointly represent blow-off exhaustion."),
    _interaction("extreme_activity_x_drawdown_x_flow_decay", (("rel_vol_phase", 4), ("close_drawdown_from_high", 2), ("taker_decline", 2)), "Extreme activity followed by structural loss and flow decay may isolate early fade."),
    _interaction("prior_fade_x_below_peak_x_wick", (("n_prior_fades_48h", 1), ("prior_1_current_vs_peak", 1), ("current_upper_wick_fraction", 3)), "Prior resolved fade structure may become relevant when the new pump rejects below it."),
    _interaction("repeat_extreme_x_flow_decay", (("n_prior_48h", 2), ("rel_vol_phase", 4), ("taker_decline", 2)), "Repeated pumps with extreme activity and weakening flow may express crowd exhaustion."),
)


def pump_fade_regime_atlas_config(
    *,
    discovery_start_ms: int,
    discovery_end_ms: int,
    verification_start_ms: int,
    verification_end_ms: int,
    protocol_freeze_id: str,
) -> CausalRegimeAtlasConfig:
    """Return the strategy declaration consumed by the generic Core atlas."""

    return CausalRegimeAtlasConfig(
        discovery_start_ms=discovery_start_ms,
        discovery_end_ms=discovery_end_ms,
        verification_start_ms=verification_start_ms,
        verification_end_ms=verification_end_ms,
        axes=PUMP_FADE_REGIME_AXES,
        interactions=PUMP_FADE_REGIME_INTERACTIONS,
        protocol_freeze_id=protocol_freeze_id,
    )


__all__ = [
    "PUMP_FADE_REGIME_AXES",
    "PUMP_FADE_REGIME_INTERACTIONS",
    "pump_fade_regime_atlas_config",
]
