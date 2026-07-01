from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class RegimeAtlasConfigError(ValueError):
    """Raised when a causal regime-atlas protocol is not fully registered."""


@dataclass(frozen=True, slots=True)
class RegimeAxisSpec:
    """One strategy-declared axis consumed by the generic Core atlas."""

    feature_name: str
    kind: str
    cuts: tuple[float, ...]
    candidate_bins: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.feature_name:
            raise RegimeAtlasConfigError("regime axis feature_name is required")
        if self.kind not in {"fixed", "quantile"}:
            raise RegimeAtlasConfigError("regime axis kind must be fixed or quantile")
        if not self.cuts:
            raise RegimeAtlasConfigError("regime axis cuts must not be empty")
        if any(left >= right for left, right in zip(self.cuts, self.cuts[1:])):
            raise RegimeAtlasConfigError("regime axis cuts must increase strictly")
        if self.kind == "quantile" and (
            self.cuts[0] <= 0.0 or self.cuts[-1] >= 1.0
        ):
            raise RegimeAtlasConfigError("quantile cuts must lie strictly inside (0, 1)")
        bin_count = len(self.cuts) + 1
        if self.candidate_bins and any(
            index < 0 or index >= bin_count for index in self.candidate_bins
        ):
            raise RegimeAtlasConfigError("candidate_bins contains an invalid bin index")


@dataclass(frozen=True, slots=True)
class RegimeInteractionComponent:
    feature_name: str
    bin_index: int

    def __post_init__(self) -> None:
        if not self.feature_name:
            raise RegimeAtlasConfigError("interaction component feature_name is required")
        if type(self.bin_index) is not int or self.bin_index < 0:
            raise RegimeAtlasConfigError("interaction component bin_index must be non-negative int")


@dataclass(frozen=True, slots=True)
class RegimeInteractionSpec:
    """One explicitly registered pair or triple of frozen axis bins."""

    interaction_id: str
    components: tuple[RegimeInteractionComponent, ...]
    rationale: str

    def __post_init__(self) -> None:
        if not self.interaction_id:
            raise RegimeAtlasConfigError("interaction_id is required")
        if len(self.components) not in (2, 3):
            raise RegimeAtlasConfigError("registered interactions must contain two or three components")
        names = tuple(component.feature_name for component in self.components)
        if len(names) != len(set(names)):
            raise RegimeAtlasConfigError("interaction component features must be unique")
        if not self.rationale:
            raise RegimeAtlasConfigError("interaction rationale is required")


@dataclass(frozen=True, slots=True)
class CausalRegimeAtlasConfig:
    discovery_start_ms: int
    discovery_end_ms: int
    verification_start_ms: int
    verification_end_ms: int
    axes: tuple[RegimeAxisSpec, ...]
    protocol_freeze_id: str
    interactions: tuple[RegimeInteractionSpec, ...] = ()
    group_column: str = "group"
    symbol_column: str = "symbol"
    snapshot_time_column: str = "snapshot_time_ms"
    feature_cutoff_time_column: str = "feature_cutoff_time_ms"
    future_start_time_column: str = "nature_future_start_time_ms"
    label_column: str = "nature_y"
    label_available_column: str = "nature_label_available"
    row_filter_column: str = "is_nature_anchor"
    row_filter_value: bool = True
    population_exact_column: str = ""
    population_exact_value: str | int | float | bool | None = None
    activity_match_column: str = "rel_vol_phase"
    activity_match_quantiles: int = 5
    min_matched_stratum_controls: int = 2
    min_matched_coverage_fraction: float = 0.70
    min_discovery_events: int = 80
    min_verification_events: int = 50
    min_discovery_delta: float = 0.05
    min_verification_delta: float = 0.03
    bootstrap_iterations: int = 1000
    shuffled_iterations: int = 99
    fdr_alpha: float = 0.05
    stability_frequency: str = "M"
    min_month_stability_events: int = 10
    min_symbol_stability_events: int = 2
    min_eligible_months: int = 3
    min_eligible_symbols: int = 20
    min_positive_stability_fraction: float = 0.60
    random_seed: int = 20260629

    def __post_init__(self) -> None:
        times = (
            self.discovery_start_ms,
            self.discovery_end_ms,
            self.verification_start_ms,
            self.verification_end_ms,
        )
        if any(type(value) is not int or value < 0 for value in times):
            raise RegimeAtlasConfigError("regime atlas times must be non-negative int milliseconds")
        if not (
            self.discovery_start_ms
            < self.discovery_end_ms
            <= self.verification_start_ms
            < self.verification_end_ms
        ):
            raise RegimeAtlasConfigError("discovery and verification windows must be ordered and disjoint")
        if not self.axes:
            raise RegimeAtlasConfigError("at least one regime axis is required")
        names = [axis.feature_name for axis in self.axes]
        if len(names) != len(set(names)):
            raise RegimeAtlasConfigError("regime axis feature names must be unique")
        axis_by_name = {axis.feature_name: axis for axis in self.axes}
        interaction_ids = [item.interaction_id for item in self.interactions]
        if len(interaction_ids) != len(set(interaction_ids)):
            raise RegimeAtlasConfigError("interaction IDs must be unique")
        if len(self.interactions) > 50:
            raise RegimeAtlasConfigError("at most 50 interactions may be registered")
        for interaction in self.interactions:
            for component in interaction.components:
                axis = axis_by_name.get(component.feature_name)
                if axis is None:
                    raise RegimeAtlasConfigError(
                        f"interaction {interaction.interaction_id!r} uses an undeclared axis"
                    )
                if component.bin_index >= len(axis.cuts) + 1:
                    raise RegimeAtlasConfigError(
                        f"interaction {interaction.interaction_id!r} has an invalid bin"
                    )
        if not self.protocol_freeze_id:
            raise RegimeAtlasConfigError("protocol_freeze_id is required")
        if bool(self.population_exact_column) != (self.population_exact_value is not None):
            raise RegimeAtlasConfigError(
                "population_exact_column and population_exact_value must be set together"
            )
        for name in (
            "group_column",
            "symbol_column",
            "snapshot_time_column",
            "feature_cutoff_time_column",
            "future_start_time_column",
            "label_column",
            "label_available_column",
            "row_filter_column",
            "activity_match_column",
        ):
            if not getattr(self, name):
                raise RegimeAtlasConfigError(f"{name} is required")
        if self.activity_match_quantiles < 2:
            raise RegimeAtlasConfigError("activity_match_quantiles must be at least two")
        if self.min_matched_stratum_controls <= 0:
            raise RegimeAtlasConfigError("min_matched_stratum_controls must be positive")
        if not 0.0 < self.min_matched_coverage_fraction <= 1.0:
            raise RegimeAtlasConfigError("min_matched_coverage_fraction must lie in (0, 1]")
        if self.min_discovery_events <= 0 or self.min_verification_events <= 0:
            raise RegimeAtlasConfigError("minimum event counts must be positive")
        if self.min_discovery_delta < 0.0 or self.min_verification_delta < 0.0:
            raise RegimeAtlasConfigError("minimum deltas must be non-negative")
        if self.bootstrap_iterations < 200:
            raise RegimeAtlasConfigError("bootstrap_iterations must be at least 200")
        if self.shuffled_iterations < 19:
            raise RegimeAtlasConfigError("shuffled_iterations must be at least 19")
        if not 0.0 < self.fdr_alpha < 1.0:
            raise RegimeAtlasConfigError("fdr_alpha must lie in (0, 1)")
        if self.stability_frequency != "M":
            raise RegimeAtlasConfigError(
                "stability_frequency must be M for the registered month audit"
            )
        for name in (
            "min_month_stability_events",
            "min_symbol_stability_events",
            "min_eligible_months",
            "min_eligible_symbols",
        ):
            if getattr(self, name) <= 0:
                raise RegimeAtlasConfigError(f"{name} must be positive")
        if not 0.0 <= self.min_positive_stability_fraction <= 1.0:
            raise RegimeAtlasConfigError(
                "min_positive_stability_fraction must lie in [0, 1]"
            )


def load_causal_regime_atlas_config(path: Path) -> CausalRegimeAtlasConfig:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    axes_raw = raw.pop("axes", None)
    if not isinstance(axes_raw, list):
        raise RegimeAtlasConfigError("axes must be a JSON list")
    axes: list[RegimeAxisSpec] = []
    for item in axes_raw:
        if not isinstance(item, dict):
            raise RegimeAtlasConfigError("each axis must be a JSON object")
        axis = dict(item)
        try:
            axes.append(
                RegimeAxisSpec(
                    feature_name=str(axis.pop("feature_name")),
                    kind=str(axis.pop("kind")),
                    cuts=tuple(float(value) for value in axis.pop("cuts")),
                    candidate_bins=tuple(
                        int(value) for value in axis.pop("candidate_bins", [])
                    ),
                )
            )
        except KeyError as exc:
            raise RegimeAtlasConfigError(f"axis is missing field: {exc.args[0]}") from exc
        if axis:
            raise RegimeAtlasConfigError(f"unknown axis fields: {sorted(axis)}")
    interactions_raw = raw.pop("interactions", [])
    if not isinstance(interactions_raw, list):
        raise RegimeAtlasConfigError("interactions must be a JSON list")
    interactions: list[RegimeInteractionSpec] = []
    for item in interactions_raw:
        if not isinstance(item, dict):
            raise RegimeAtlasConfigError("each interaction must be a JSON object")
        interaction = dict(item)
        components_raw = interaction.pop("components", None)
        if not isinstance(components_raw, list):
            raise RegimeAtlasConfigError("interaction components must be a JSON list")
        components: list[RegimeInteractionComponent] = []
        for component_raw in components_raw:
            if not isinstance(component_raw, dict):
                raise RegimeAtlasConfigError("each interaction component must be an object")
            component = dict(component_raw)
            try:
                components.append(
                    RegimeInteractionComponent(
                        feature_name=str(component.pop("feature_name")),
                        bin_index=int(component.pop("bin_index")),
                    )
                )
            except KeyError as exc:
                raise RegimeAtlasConfigError(
                    f"interaction component is missing field: {exc.args[0]}"
                ) from exc
            if component:
                raise RegimeAtlasConfigError(
                    f"unknown interaction component fields: {sorted(component)}"
                )
        try:
            interactions.append(
                RegimeInteractionSpec(
                    interaction_id=str(interaction.pop("interaction_id")),
                    components=tuple(components),
                    rationale=str(interaction.pop("rationale")),
                )
            )
        except KeyError as exc:
            raise RegimeAtlasConfigError(
                f"interaction is missing field: {exc.args[0]}"
            ) from exc
        if interaction:
            raise RegimeAtlasConfigError(f"unknown interaction fields: {sorted(interaction)}")
    time_values: dict[str, int] = {}
    for prefix in ("discovery_start", "discovery_end", "verification_start", "verification_end"):
        utc_key = f"{prefix}_utc"
        ms_key = f"{prefix}_ms"
        if utc_key in raw and ms_key in raw:
            raise RegimeAtlasConfigError(f"use only one of {utc_key} and {ms_key}")
        if utc_key in raw:
            time_values[ms_key] = _parse_utc_ms(raw.pop(utc_key), field_name=utc_key)
        elif ms_key in raw:
            time_values[ms_key] = int(raw.pop(ms_key))
        else:
            raise RegimeAtlasConfigError(f"{utc_key} or {ms_key} is required")
    allowed = set(CausalRegimeAtlasConfig.__dataclass_fields__) - {
        "axes",
        *time_values,
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise RegimeAtlasConfigError(f"unknown regime atlas config fields: {unknown}")
    return CausalRegimeAtlasConfig(
        **time_values,
        axes=tuple(axes),
        interactions=tuple(interactions),
        **raw,
    )


def _parse_utc_ms(value: object, *, field_name: str) -> int:
    if not isinstance(value, str) or not value:
        raise RegimeAtlasConfigError(f"{field_name} must be a non-empty ISO UTC string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RegimeAtlasConfigError(f"{field_name} must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


__all__ = [
    "CausalRegimeAtlasConfig",
    "RegimeAtlasConfigError",
    "RegimeAxisSpec",
    "RegimeInteractionComponent",
    "RegimeInteractionSpec",
    "load_causal_regime_atlas_config",
]
