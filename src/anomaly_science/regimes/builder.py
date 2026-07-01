from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

import numpy as np
import pandas as pd

from anomaly_science.regimes.config import CausalRegimeAtlasConfig, RegimeAxisSpec
from anomaly_science.regimes.contracts import (
    CausalRegimeAtlasResult,
    FrozenRegimeBin,
    FrozenRegimeInteraction,
    RegimeControlRow,
    RegimeEvidenceRow,
    RegimeScreeningRow,
    RegimeStabilityRow,
)


class RegimeAtlasInputError(ValueError):
    """Raised when a supervised frame cannot support causal regime inference."""


@dataclass(frozen=True, slots=True)
class _FrozenAxis:
    spec: RegimeAxisSpec
    edges: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _MatchedEffect:
    event_count: int
    matched_count: int
    matched_coverage: float
    label_rate: float
    matched_rate: float
    delta: float
    rows: pd.DataFrame


@dataclass(frozen=True, slots=True)
class _FrozenHypothesis:
    descriptor: FrozenRegimeBin
    kind: str
    component_features: tuple[str, ...]
    component_bins: tuple[int, ...]
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class _CandidateEvaluation:
    hypothesis: _FrozenHypothesis
    discovery: _MatchedEffect
    discovery_ci: tuple[float, float]
    discovery_month_ci: tuple[float, float]
    discovery_symbol_ci: tuple[float, float]
    verification: _MatchedEffect
    verification_ci: tuple[float, float]
    verification_month_ci: tuple[float, float]
    verification_symbol_ci: tuple[float, float]
    shuffled_p_value: float
    stability_rows: tuple[RegimeStabilityRow, ...]
    positive_month_fraction: float
    eligible_month_count: int
    positive_symbol_fraction: float
    eligible_symbol_count: int


def build_causal_regime_atlas(
    frame: pd.DataFrame,
    config: CausalRegimeAtlasConfig,
) -> CausalRegimeAtlasResult:
    """Discover frozen coarse regimes and evaluate them on later verification data.

    Axis thresholds and activity matching bins are fitted on discovery only.
    Candidate selection uses discovery only. Verification contributes effect
    estimation, clustered uncertainty, shuffled-label p-values, FDR, and
    stability diagnostics; it never changes a threshold or candidate rule.
    """

    prepared = _prepare_frame(frame, config)
    discovery = prepared.loc[
        (prepared["__snapshot_ms"] >= config.discovery_start_ms)
        & (prepared["__snapshot_ms"] < config.discovery_end_ms)
    ].copy()
    verification = prepared.loc[
        (prepared["__snapshot_ms"] >= config.verification_start_ms)
        & (prepared["__snapshot_ms"] < config.verification_end_ms)
    ].copy()
    if discovery.empty or verification.empty:
        raise RegimeAtlasInputError("both discovery and verification windows require rows")

    frozen_axes = tuple(_freeze_axis(discovery, axis) for axis in config.axes)
    activity_edges = _quantile_edges(
        discovery[config.activity_match_column],
        tuple(index / config.activity_match_quantiles for index in range(1, config.activity_match_quantiles)),
        name=config.activity_match_column,
        allow_collapsed=True,
    )
    discovery = _add_frozen_bins(discovery, frozen_axes, activity_edges, config)
    verification = _add_frozen_bins(verification, frozen_axes, activity_edges, config)
    discovery_row_count = len(discovery)
    verification_row_count = len(verification)

    frozen_bins = tuple(
        frozen_bin
        for axis in frozen_axes
        for frozen_bin in _axis_bins(axis)
    )
    frozen_interactions = tuple(
        FrozenRegimeInteraction(
            regime_id=interaction.interaction_id,
            component_features="|".join(
                component.feature_name for component in interaction.components
            ),
            component_bins="|".join(
                str(component.bin_index) for component in interaction.components
            ),
            rationale=interaction.rationale,
        )
        for interaction in config.interactions
    )
    hypotheses = tuple(
        _FrozenHypothesis(
            descriptor=frozen_bin,
            kind="single",
            component_features=(frozen_bin.feature_name,),
            component_bins=(frozen_bin.bin_index,),
        )
        for frozen_bin in frozen_bins
        if _is_candidate_bin(frozen_bin, frozen_axes)
    ) + tuple(
        _FrozenHypothesis(
            descriptor=FrozenRegimeBin(
                regime_id=interaction.interaction_id,
                feature_name="&".join(
                    component.feature_name for component in interaction.components
                ),
                bin_index=-1,
                lower_bound=None,
                upper_bound=None,
            ),
            kind="interaction",
            component_features=tuple(
                component.feature_name for component in interaction.components
            ),
            component_bins=tuple(
                component.bin_index for component in interaction.components
            ),
            rationale=interaction.rationale,
        )
        for interaction in config.interactions
    )
    candidates: list[
        tuple[
            _FrozenHypothesis,
            _MatchedEffect,
            tuple[float, float],
            tuple[float, float],
            tuple[float, float],
        ]
    ] = []
    screening_rows: list[RegimeScreeningRow] = []
    for hypothesis in hypotheses:
        frozen_bin = hypothesis.descriptor
        mask = _hypothesis_mask(discovery, hypothesis)
        effect = _matched_effect(discovery, mask, config=config)
        # Cheap, deterministic support/effect pruning precedes all bootstrap work.
        cheap_reasons = _discovery_support_reasons(effect, config)
        if cheap_reasons:
            screening_rows.append(
                _screening_row(
                    hypothesis,
                    effect,
                    status="PRUNED_BEFORE_BOOTSTRAP",
                    rejection_reasons=cheap_reasons,
                )
            )
            continue
        discovery_intervals = _clustered_intervals(
            discovery,
            mask,
            config=config,
            seed_prefix=(frozen_bin.regime_id, "discovery"),
        )
        ci = discovery_intervals["week"]
        if (
            math.isfinite(ci[0])
            and ci[0] > 0.0
            and discovery_intervals["month"][0] > 0.0
            and discovery_intervals["symbol"][0] > 0.0
        ):
            screening_rows.append(
                _screening_row(
                    hypothesis,
                    effect,
                    intervals=discovery_intervals,
                    status="SELECTED_FOR_VERIFICATION",
                )
            )
            candidates.append(
                (
                    hypothesis,
                    effect,
                    ci,
                    discovery_intervals["month"],
                    discovery_intervals["symbol"],
                )
            )
        else:
            ci_reasons = []
            for dimension in ("week", "month", "symbol"):
                lower = discovery_intervals[dimension][0]
                if not math.isfinite(lower) or lower <= 0.0:
                    ci_reasons.append(f"{dimension}_clustered_ci")
            screening_rows.append(
                _screening_row(
                    hypothesis,
                    effect,
                    intervals=discovery_intervals,
                    status="REJECTED_DISCOVERY_UNCERTAINTY",
                    rejection_reasons=ci_reasons,
                )
            )

    evaluations: list[_CandidateEvaluation] = []
    for (
        hypothesis,
        discovery_effect,
        discovery_ci,
        discovery_month_ci,
        discovery_symbol_ci,
    ) in candidates:
        frozen_bin = hypothesis.descriptor
        verification_mask = _hypothesis_mask(verification, hypothesis)
        verification_effect = _matched_effect(
            verification,
            verification_mask,
            config=config,
        )
        verification_intervals = _clustered_intervals(
            verification,
            verification_mask,
            config=config,
            seed_prefix=(frozen_bin.regime_id, "verification"),
        )
        verification_ci = verification_intervals["week"]
        p_value = _shuffled_p_value(
            verification,
            verification_mask,
            observed_delta=verification_effect.delta,
            config=config,
            seed=_stable_seed(config.random_seed, frozen_bin.regime_id, "shuffle"),
        )
        (
            stability_rows,
            positive_month_fraction,
            eligible_month_count,
            positive_symbol_fraction,
            eligible_symbol_count,
        ) = _stability(verification_effect.rows, frozen_bin.regime_id, config)
        evaluations.append(
            _CandidateEvaluation(
                hypothesis=hypothesis,
                    discovery=discovery_effect,
                    discovery_ci=discovery_ci,
                    discovery_month_ci=discovery_month_ci,
                    discovery_symbol_ci=discovery_symbol_ci,
                    verification=verification_effect,
                    verification_ci=verification_ci,
                    verification_month_ci=verification_intervals["month"],
                    verification_symbol_ci=verification_intervals["symbol"],
                shuffled_p_value=p_value,
                stability_rows=stability_rows,
                positive_month_fraction=positive_month_fraction,
                eligible_month_count=eligible_month_count,
                positive_symbol_fraction=positive_symbol_fraction,
                eligible_symbol_count=eligible_symbol_count,
            )
        )

    q_values = _benjamini_yekutieli(
        [evaluation.shuffled_p_value for evaluation in evaluations]
    )
    evidence_rows: list[RegimeEvidenceRow] = []
    stability_rows: list[RegimeStabilityRow] = []
    for evaluation, q_value in zip(evaluations, q_values, strict=True):
        verification_effect = evaluation.verification
        rejection_reasons = _rejection_reasons(evaluation, q_value, config)
        replicated = not rejection_reasons
        hypothesis = evaluation.hypothesis
        frozen_bin = hypothesis.descriptor
        evidence_rows.append(
            RegimeEvidenceRow(
                regime_id=frozen_bin.regime_id,
                protocol_freeze_id=config.protocol_freeze_id,
                evidence_scope="development_replication_not_pristine",
                pristine_claim_allowed=False,
                feature_name=frozen_bin.feature_name,
                bin_index=frozen_bin.bin_index,
                lower_bound=frozen_bin.lower_bound,
                upper_bound=frozen_bin.upper_bound,
                discovery_event_count=evaluation.discovery.event_count,
                discovery_matched_count=evaluation.discovery.matched_count,
                discovery_matched_coverage=evaluation.discovery.matched_coverage,
                discovery_label_rate=evaluation.discovery.label_rate,
                discovery_matched_rate=evaluation.discovery.matched_rate,
                discovery_delta=evaluation.discovery.delta,
                discovery_ci_lower_95=evaluation.discovery_ci[0],
                discovery_ci_upper_95=evaluation.discovery_ci[1],
                discovery_month_ci_lower_95=evaluation.discovery_month_ci[0],
                discovery_month_ci_upper_95=evaluation.discovery_month_ci[1],
                discovery_symbol_ci_lower_95=evaluation.discovery_symbol_ci[0],
                discovery_symbol_ci_upper_95=evaluation.discovery_symbol_ci[1],
                verification_event_count=verification_effect.event_count,
                verification_matched_count=verification_effect.matched_count,
                verification_matched_coverage=verification_effect.matched_coverage,
                verification_label_rate=verification_effect.label_rate,
                verification_matched_rate=verification_effect.matched_rate,
                verification_delta=verification_effect.delta,
                verification_ci_lower_95=evaluation.verification_ci[0],
                verification_ci_upper_95=evaluation.verification_ci[1],
                verification_month_ci_lower_95=evaluation.verification_month_ci[0],
                verification_month_ci_upper_95=evaluation.verification_month_ci[1],
                verification_symbol_ci_lower_95=evaluation.verification_symbol_ci[0],
                verification_symbol_ci_upper_95=evaluation.verification_symbol_ci[1],
                verification_lift=(
                    verification_effect.label_rate / verification_effect.matched_rate
                    if verification_effect.matched_rate > 0.0
                    else math.inf
                ),
                shuffled_p_value=evaluation.shuffled_p_value,
                fdr_q_value=q_value,
                positive_month_fraction=evaluation.positive_month_fraction,
                eligible_month_count=evaluation.eligible_month_count,
                positive_symbol_fraction=evaluation.positive_symbol_fraction,
                eligible_symbol_count=evaluation.eligible_symbol_count,
                status="DEVELOPMENT_REPLICATED" if replicated else "REJECTED",
                rejection_reasons=";".join(rejection_reasons),
                hypothesis_kind=hypothesis.kind,
                component_features="|".join(hypothesis.component_features),
                component_bins="|".join(str(value) for value in hypothesis.component_bins),
            )
        )
        stability_rows.extend(evaluation.stability_rows)

    replicated_count = sum(
        row.status == "DEVELOPMENT_REPLICATED" for row in evidence_rows
    )
    minimum_p = min((row.shuffled_p_value for row in evidence_rows), default=1.0)
    controls = (
        RegimeControlRow(
            control_name="within_matched_stratum_shuffled_labels",
            iterations=config.shuffled_iterations,
            candidate_count=len(candidates),
            replicated_regime_count=replicated_count,
            minimum_p_value=minimum_p,
        notes=(
            "Labels are permuted inside symbol/calendar-month/activity strata; "
                "axis thresholds remain frozen from discovery and BY-FDR is applied "
                "jointly across discovery-selected singles and interactions."
            ),
        ),
    )
    return CausalRegimeAtlasResult(
        frozen_bins=frozen_bins,
        frozen_interactions=frozen_interactions,
        evidence_rows=tuple(evidence_rows),
        stability_rows=tuple(stability_rows),
        control_rows=controls,
        screening_rows=tuple(screening_rows),
        discovery_row_count=discovery_row_count,
        verification_row_count=verification_row_count,
    )


def _prepare_frame(
    frame: pd.DataFrame,
    config: CausalRegimeAtlasConfig,
) -> pd.DataFrame:
    required = {
        config.group_column,
        config.symbol_column,
        config.snapshot_time_column,
        config.feature_cutoff_time_column,
        config.future_start_time_column,
        config.label_column,
        config.label_available_column,
        config.row_filter_column,
        config.activity_match_column,
        *(axis.feature_name for axis in config.axes),
    }
    if config.population_exact_column:
        required.add(config.population_exact_column)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RegimeAtlasInputError(f"regime atlas input is missing columns: {missing}")
    work = frame.copy()
    snapshot = pd.to_numeric(work[config.snapshot_time_column], errors="raise")
    cutoff = pd.to_numeric(work[config.feature_cutoff_time_column], errors="raise")
    future_start = pd.to_numeric(work[config.future_start_time_column], errors="coerce")
    if (cutoff > snapshot).any():
        raise RegimeAtlasInputError("feature_cutoff_time exceeds snapshot_time")
    available = work[config.label_available_column].astype(bool)
    if future_start.loc[available].isna().any() or (
        future_start.loc[available] <= snapshot.loc[available]
    ).any():
        raise RegimeAtlasInputError("available labels must start strictly after snapshot_time")
    work = work.loc[
        available
        & (work[config.row_filter_column] == config.row_filter_value)
    ].copy()
    if config.population_exact_column:
        work = work.loc[
            work[config.population_exact_column] == config.population_exact_value
        ].copy()
    if work.empty:
        raise RegimeAtlasInputError("regime atlas population is empty after causal filters")
    if work[config.group_column].astype(str).duplicated().any():
        raise RegimeAtlasInputError(
            "regime atlas requires one causally selected row per event group"
        )
    labels = pd.to_numeric(work[config.label_column], errors="raise")
    if not set(labels.astype(int).unique()) <= {0, 1}:
        raise RegimeAtlasInputError("regime atlas labels must be binary")
    work["__label"] = labels.astype(np.int8)
    work["__snapshot_ms"] = pd.to_numeric(
        work[config.snapshot_time_column], errors="raise"
    ).astype(np.int64)
    times = pd.to_datetime(work["__snapshot_ms"], unit="ms", utc=True)
    work["__month"] = times.dt.tz_localize(None).dt.to_period("M").astype(str)
    work["__week"] = times.dt.tz_localize(None).dt.to_period("W").astype(str)
    for column in {config.activity_match_column, *(axis.feature_name for axis in config.axes)}:
        values = pd.to_numeric(work[column], errors="coerce")
        work[column] = values
    return work.reset_index(drop=True)


def _freeze_axis(discovery: pd.DataFrame, spec: RegimeAxisSpec) -> _FrozenAxis:
    if spec.kind == "fixed":
        edges = spec.cuts
    else:
        edges = _quantile_edges(
            discovery[spec.feature_name],
            spec.cuts,
            name=spec.feature_name,
            allow_collapsed=False,
        )
    return _FrozenAxis(spec=spec, edges=edges)


def _quantile_edges(
    values: pd.Series,
    quantiles: tuple[float, ...],
    *,
    name: str,
    allow_collapsed: bool,
) -> tuple[float, ...]:
    finite = values[np.isfinite(values.to_numpy(dtype=float))].to_numpy(dtype=float)
    if len(finite) == 0:
        raise RegimeAtlasInputError(f"{name} has no finite discovery values")
    raw = tuple(float(value) for value in np.quantile(finite, quantiles))
    edges = tuple(dict.fromkeys(raw))
    if not allow_collapsed and len(edges) != len(raw):
        raise RegimeAtlasInputError(
            f"{name} discovery quantile cuts collapse; axis is not identifiable"
        )
    return edges


def _add_frozen_bins(
    frame: pd.DataFrame,
    axes: tuple[_FrozenAxis, ...],
    activity_edges: tuple[float, ...],
    config: CausalRegimeAtlasConfig,
) -> pd.DataFrame:
    result = frame.copy()
    activity = result[config.activity_match_column].to_numpy(dtype=float)
    result["__activity_bin"] = np.where(
        np.isfinite(activity), np.digitize(activity, activity_edges), -1
    ).astype(np.int16)
    for axis in axes:
        values = result[axis.spec.feature_name].to_numpy(dtype=float)
        result[f"__bin_{axis.spec.feature_name}"] = np.where(
            np.isfinite(values), np.digitize(values, axis.edges), -1
        ).astype(np.int16)
    return result


def _axis_bins(axis: _FrozenAxis) -> tuple[FrozenRegimeBin, ...]:
    result: list[FrozenRegimeBin] = []
    for index in range(len(axis.edges) + 1):
        lower = None if index == 0 else axis.edges[index - 1]
        upper = None if index == len(axis.edges) else axis.edges[index]
        result.append(
            FrozenRegimeBin(
                regime_id=f"{axis.spec.feature_name}:bin_{index}",
                feature_name=axis.spec.feature_name,
                bin_index=index,
                lower_bound=lower,
                upper_bound=upper,
            )
        )
    return tuple(result)


def _is_candidate_bin(
    frozen_bin: FrozenRegimeBin,
    axes: tuple[_FrozenAxis, ...],
) -> bool:
    axis = next(
        item for item in axes if item.spec.feature_name == frozen_bin.feature_name
    )
    return (
        not axis.spec.candidate_bins
        or frozen_bin.bin_index in axis.spec.candidate_bins
    )


def _hypothesis_mask(
    frame: pd.DataFrame,
    hypothesis: _FrozenHypothesis,
) -> np.ndarray:
    mask = np.ones(len(frame), dtype=bool)
    for feature_name, bin_index in zip(
        hypothesis.component_features,
        hypothesis.component_bins,
        strict=True,
    ):
        mask &= (
            frame[f"__bin_{feature_name}"].to_numpy(dtype=np.int16) == bin_index
        )
    return mask


def _discovery_support_reasons(
    effect: _MatchedEffect,
    config: CausalRegimeAtlasConfig,
) -> list[str]:
    reasons: list[str] = []
    if effect.event_count < config.min_discovery_events:
        reasons.append("discovery_support")
    if effect.matched_count < config.min_discovery_events:
        reasons.append("matched_support")
    if effect.matched_coverage < config.min_matched_coverage_fraction:
        reasons.append("matched_coverage")
    if not math.isfinite(effect.delta) or effect.delta < config.min_discovery_delta:
        reasons.append("discovery_effect")
    return reasons


def _screening_row(
    hypothesis: _FrozenHypothesis,
    effect: _MatchedEffect,
    *,
    status: str,
    rejection_reasons: list[str] | None = None,
    intervals: dict[str, tuple[float, float]] | None = None,
) -> RegimeScreeningRow:
    intervals = intervals or {}
    return RegimeScreeningRow(
        regime_id=hypothesis.descriptor.regime_id,
        hypothesis_kind=hypothesis.kind,
        component_features="|".join(hypothesis.component_features),
        component_bins="|".join(str(value) for value in hypothesis.component_bins),
        discovery_event_count=effect.event_count,
        discovery_matched_count=effect.matched_count,
        discovery_matched_coverage=effect.matched_coverage,
        discovery_delta=effect.delta,
        discovery_week_ci_lower_95=intervals.get("week", (math.nan, math.nan))[0],
        discovery_month_ci_lower_95=intervals.get("month", (math.nan, math.nan))[0],
        discovery_symbol_ci_lower_95=intervals.get("symbol", (math.nan, math.nan))[0],
        status=status,
        rejection_reasons=";".join(rejection_reasons or []),
    )


def _matched_effect(
    frame: pd.DataFrame,
    signal_mask: pd.Series | np.ndarray,
    *,
    config: CausalRegimeAtlasConfig,
    labels: np.ndarray | None = None,
) -> _MatchedEffect:
    signal = np.asarray(signal_mask, dtype=bool).copy()
    signal &= frame["__activity_bin"].to_numpy(dtype=np.int16) >= 0
    event_count = int(signal.sum())
    if event_count == 0:
        return _empty_effect()
    work = frame[[config.symbol_column, "__month", "__activity_bin"]].copy()
    work["__label_value"] = (
        frame["__label"].to_numpy(dtype=float)
        if labels is None
        else np.asarray(labels, dtype=float)
    )
    work["__signal"] = signal
    strata = [config.symbol_column, "__month", "__activity_bin"]
    baseline = work.loc[~work["__signal"]].groupby(strata, sort=False)[
        "__label_value"
    ].agg(["mean", "count"])
    selected = work.loc[work["__signal"]].copy()
    selected["__row_index"] = selected.index
    selected = selected.join(baseline, on=strata)
    selected = selected.loc[
        selected["count"].fillna(0) >= config.min_matched_stratum_controls
    ].copy()
    matched_count = len(selected)
    if matched_count == 0:
        return _MatchedEffect(
            event_count=event_count,
            matched_count=0,
            matched_coverage=0.0,
            label_rate=float("nan"),
            matched_rate=float("nan"),
            delta=float("nan"),
            rows=pd.DataFrame(),
        )
    source = frame.loc[selected["__row_index"].to_numpy(dtype=np.int64)]
    matched_rows = pd.DataFrame(
        {
            "__label": selected["__label_value"].to_numpy(dtype=float),
            "__matched": selected["mean"].to_numpy(dtype=float),
            "__residual": (
                selected["__label_value"] - selected["mean"]
            ).to_numpy(dtype=float),
            "__month": source["__month"].to_numpy(),
            "__week": source["__week"].to_numpy(),
            "__symbol": source[config.symbol_column].astype(str).to_numpy(),
        }
    )
    return _MatchedEffect(
        event_count=event_count,
        matched_count=matched_count,
        matched_coverage=matched_count / event_count,
        label_rate=float(matched_rows["__label"].mean()),
        matched_rate=float(matched_rows["__matched"].mean()),
        delta=float(matched_rows["__residual"].mean()),
        rows=matched_rows,
    )


def _empty_effect() -> _MatchedEffect:
    return _MatchedEffect(
        event_count=0,
        matched_count=0,
        matched_coverage=0.0,
        label_rate=float("nan"),
        matched_rate=float("nan"),
        delta=float("nan"),
        rows=pd.DataFrame(),
    )


def _clustered_intervals(
    frame: pd.DataFrame,
    signal_mask: pd.Series | np.ndarray,
    *,
    config: CausalRegimeAtlasConfig,
    seed_prefix: tuple[str, str],
) -> dict[str, tuple[float, float]]:
    return {
        dimension: _clustered_interval(
            frame,
            signal_mask,
            cluster_column=column,
            config=config,
            iterations=config.bootstrap_iterations,
            seed=_stable_seed(config.random_seed, *seed_prefix, dimension),
        )
        for dimension, column in (
            ("week", "__week"),
            ("month", "__month"),
            ("symbol", config.symbol_column),
        )
    }


def _clustered_interval(
    frame: pd.DataFrame,
    signal_mask: pd.Series | np.ndarray,
    *,
    cluster_column: str,
    config: CausalRegimeAtlasConfig,
    iterations: int,
    seed: int,
) -> tuple[float, float]:
    if frame.empty:
        return float("nan"), float("nan")
    signal = np.asarray(signal_mask, dtype=bool).copy()
    signal &= frame["__activity_bin"].to_numpy(dtype=np.int16) >= 0
    work = pd.DataFrame(
        {
            "__cluster": frame[cluster_column].astype(str).to_numpy(),
            "__symbol": frame[config.symbol_column].astype(str).to_numpy(),
            "__month": frame["__month"].astype(str).to_numpy(),
            "__activity": frame["__activity_bin"].to_numpy(dtype=np.int16),
            "__signal": signal,
            "__label": frame["__label"].to_numpy(dtype=float),
        }
    )
    grouped = work.groupby(
        ["__cluster", "__symbol", "__month", "__activity", "__signal"],
        sort=False,
    )["__label"].agg(["sum", "count"]).reset_index()
    cluster_codes, clusters = pd.factorize(grouped["__cluster"], sort=False)
    stratum_keys = pd.MultiIndex.from_frame(
        grouped[["__symbol", "__month", "__activity"]]
    )
    stratum_codes, strata = pd.factorize(stratum_keys, sort=False)
    cluster_count = len(clusters)
    stratum_count = len(strata)
    if cluster_count < 2:
        return float("nan"), float("nan")
    counts = grouped["count"].to_numpy(dtype=float)
    sums = grouped["sum"].to_numpy(dtype=float)
    is_signal = grouped["__signal"].to_numpy(dtype=bool)
    rng = np.random.default_rng(seed)
    samples: list[float] = []
    for _ in range(iterations):
        chosen = rng.integers(0, cluster_count, size=cluster_count)
        cluster_weights = np.bincount(chosen, minlength=cluster_count).astype(float)
        weights = cluster_weights[cluster_codes]
        signal_counts = np.bincount(
            stratum_codes[is_signal],
            weights=counts[is_signal] * weights[is_signal],
            minlength=stratum_count,
        )
        signal_sums = np.bincount(
            stratum_codes[is_signal],
            weights=sums[is_signal] * weights[is_signal],
            minlength=stratum_count,
        )
        baseline_counts = np.bincount(
            stratum_codes[~is_signal],
            weights=counts[~is_signal] * weights[~is_signal],
            minlength=stratum_count,
        )
        baseline_sums = np.bincount(
            stratum_codes[~is_signal],
            weights=sums[~is_signal] * weights[~is_signal],
            minlength=stratum_count,
        )
        eligible = (
            (signal_counts > 0)
            & (baseline_counts >= config.min_matched_stratum_controls)
        )
        matched_signal_count = float(signal_counts[eligible].sum())
        if matched_signal_count <= 0.0:
            continue
        matched_baseline = baseline_sums[eligible] / baseline_counts[eligible]
        delta = (
            signal_sums[eligible]
            - signal_counts[eligible] * matched_baseline
        ).sum() / matched_signal_count
        if math.isfinite(float(delta)):
            samples.append(float(delta))
    if len(samples) < max(100, iterations // 2):
        return float("nan"), float("nan")
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def _shuffled_p_value(
    frame: pd.DataFrame,
    signal_mask: pd.Series | np.ndarray,
    *,
    observed_delta: float,
    config: CausalRegimeAtlasConfig,
    seed: int,
) -> float:
    if not math.isfinite(observed_delta):
        return 1.0
    rng = np.random.default_rng(seed)
    original = frame["__label"].to_numpy(dtype=np.int8)
    strata = [config.symbol_column, "__month", "__activity_bin"]
    group_indices = tuple(frame.groupby(strata, sort=False).indices.values())
    null_deltas = np.empty(config.shuffled_iterations, dtype=float)
    for iteration in range(config.shuffled_iterations):
        shuffled = original.copy()
        for indices in group_indices:
            values = np.asarray(indices, dtype=np.int64)
            shuffled[values] = rng.permutation(shuffled[values])
        effect = _matched_effect(
            frame,
            signal_mask,
            config=config,
            labels=shuffled,
        )
        null_deltas[iteration] = effect.delta
    finite = null_deltas[np.isfinite(null_deltas)]
    if len(finite) < max(10, config.shuffled_iterations // 2):
        return 1.0
    return float((1 + np.sum(finite >= observed_delta)) / (len(finite) + 1))


def _stability(
    rows: pd.DataFrame,
    regime_id: str,
    config: CausalRegimeAtlasConfig,
) -> tuple[tuple[RegimeStabilityRow, ...], float, int, float, int]:
    if rows.empty:
        return (), 0.0, 0, 0.0, 0
    output: list[RegimeStabilityRow] = []
    summaries: dict[str, list[tuple[int, float]]] = {}
    for dimension, column in (("month", "__month"), ("symbol", "__symbol")):
        dimension_rows: list[tuple[int, float]] = []
        minimum_events = (
            config.min_month_stability_events
            if dimension == "month"
            else config.min_symbol_stability_events
        )
        for value, group in rows.groupby(column, sort=True):
            count = len(group)
            delta = float(group["__residual"].mean())
            eligible = count >= minimum_events
            output.append(
                RegimeStabilityRow(
                    regime_id=regime_id,
                    dimension=dimension,
                    value=str(value),
                    event_count=count,
                    label_rate=float(group["__label"].mean()),
                    matched_rate=float(group["__matched"].mean()),
                    delta=delta,
                    eligible=eligible,
                )
            )
            if eligible:
                dimension_rows.append((count, delta))
        summaries[dimension] = dimension_rows
    month = summaries["month"]
    symbol = summaries["symbol"]
    return (
        tuple(output),
        float(np.mean([delta > 0.0 for _, delta in month])) if month else 0.0,
        len(month),
        float(np.mean([delta > 0.0 for _, delta in symbol])) if symbol else 0.0,
        len(symbol),
    )


def _benjamini_yekutieli(p_values: list[float]) -> list[float]:
    count = len(p_values)
    if count == 0:
        return []
    harmonic = sum(1.0 / index for index in range(1, count + 1))
    order = np.argsort(np.asarray(p_values, dtype=float))
    adjusted = np.ones(count, dtype=float)
    running = 1.0
    for reverse_rank in range(count - 1, -1, -1):
        original_index = int(order[reverse_rank])
        rank = reverse_rank + 1
        value = min(1.0, float(p_values[original_index]) * count * harmonic / rank)
        running = min(running, value)
        adjusted[original_index] = running
    return adjusted.tolist()


def _rejection_reasons(
    evaluation: _CandidateEvaluation,
    q_value: float,
    config: CausalRegimeAtlasConfig,
) -> list[str]:
    effect = evaluation.verification
    reasons: list[str] = []
    if effect.event_count < config.min_verification_events:
        reasons.append("verification_support")
    if effect.matched_count < config.min_verification_events:
        reasons.append("matched_support")
    if effect.matched_coverage < config.min_matched_coverage_fraction:
        reasons.append("matched_coverage")
    if not math.isfinite(effect.delta) or effect.delta < config.min_verification_delta:
        reasons.append("verification_effect")
    if not math.isfinite(evaluation.verification_ci[0]) or evaluation.verification_ci[0] <= 0.0:
        reasons.append("week_clustered_ci")
    if (
        not math.isfinite(evaluation.verification_month_ci[0])
        or evaluation.verification_month_ci[0] <= 0.0
    ):
        reasons.append("month_clustered_ci")
    if (
        not math.isfinite(evaluation.verification_symbol_ci[0])
        or evaluation.verification_symbol_ci[0] <= 0.0
    ):
        reasons.append("symbol_clustered_ci")
    if q_value > config.fdr_alpha:
        reasons.append("fdr")
    if evaluation.eligible_month_count < config.min_eligible_months:
        reasons.append("month_support")
    elif evaluation.positive_month_fraction < config.min_positive_stability_fraction:
        reasons.append("month_stability")
    if evaluation.eligible_symbol_count < config.min_eligible_symbols:
        reasons.append("symbol_support")
    elif evaluation.positive_symbol_fraction < config.min_positive_stability_fraction:
        reasons.append("symbol_stability")
    return reasons


def _stable_seed(base: int, *parts: str) -> int:
    payload = "\x1f".join((str(base), *parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


__all__ = ["RegimeAtlasInputError", "build_causal_regime_atlas"]
