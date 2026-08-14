from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anomaly_science.contracts.horizons import validate_supported_research_horizon
from anomaly_science.strategy.anomaly_config import BroadAnomalyDetectorConfig
from anomaly_science.strategy.anomaly import (
    ANOMALY_STRATEGY_DEFAULTS,
    BroadAnomalyStrategy,
    PostAnomalyExtensionStrategy,
    PostPumpDistributionStrategy,
    make_broad_anomaly_strategy,
    make_post_anomaly_extension_strategy,
    make_post_pump_distribution_strategy,
)
from anomaly_science.contracts.strategy import BaseStrategy

if TYPE_CHECKING:
    from anomaly_science.annotation.app import AnnotationStrategyApp


class StrategyRegistryError(ValueError):
    """Raised when a requested strategy is not registered cleanly."""


@dataclass(frozen=True, slots=True)
class StrategyRegistryEntry:
    strategy_name: str
    strategy_family: str
    strategy_contract_version: str
    factory: Callable[[], BaseStrategy]


@dataclass(frozen=True, slots=True)
class StrategyImplementationStatus:
    strategy_name: str
    strategy_family: str
    strategy_contract_version: str
    horizon_minutes: int
    allowed_horizons: tuple[int, ...]
    default_horizon_minutes: int
    implementation_status: str
    executable: bool
    registry_error: str


BROAD_ANOMALY_VARIANTS: tuple[str, ...] = (
    "broad_anomaly_v1_h15",
    "broad_anomaly_v1_h30",
    "broad_anomaly_v1_h60",
)

POST_ANOMALY_EXTENSION_VARIANTS: tuple[str, ...] = (
    "post_anomaly_extension_v1_h60",
    "post_anomaly_extension_v1_h120",
    "post_anomaly_extension_v1_h180",
)

POST_PUMP_DISTRIBUTION_VARIANTS: tuple[str, ...] = (
    "post_pump_distribution_v1_h60",
    "post_pump_distribution_v1_h120",
    "post_pump_distribution_v1_h180",
)

EXECUTABLE_STRATEGY_NAMES: tuple[str, ...] = (
    BROAD_ANOMALY_VARIANTS + POST_ANOMALY_EXTENSION_VARIANTS + POST_PUMP_DISTRIBUTION_VARIANTS
)
SPECIFIED_NOT_IMPLEMENTED_STRATEGY_NAMES: tuple[str, ...] = tuple(
    strategy_name for strategy_name in ANOMALY_STRATEGY_DEFAULTS if strategy_name not in EXECUTABLE_STRATEGY_NAMES
)


def available_strategies() -> tuple[StrategyRegistryEntry, ...]:
    """Return executable strategy variants only.

    Specified-only variants are intentionally excluded. They can appear in the
    implementation-status artifact but must never be instantiated through a
    default or broad-anomaly fallback factory.
    """
    rows: list[StrategyRegistryEntry] = []
    for strategy_name in BROAD_ANOMALY_VARIANTS:
        rows.append(
            StrategyRegistryEntry(
                strategy_name=strategy_name,
                strategy_family="anomaly",
                strategy_contract_version="base_strategy_v2_structural_execution",
                factory=lambda strategy_name=strategy_name: make_broad_anomaly_strategy(strategy_name=strategy_name),
            )
        )
    for strategy_name in POST_ANOMALY_EXTENSION_VARIANTS:
        rows.append(
            StrategyRegistryEntry(
                strategy_name=strategy_name,
                strategy_family="anomaly",
                strategy_contract_version="base_strategy_v2_structural_execution",
                factory=lambda strategy_name=strategy_name: make_post_anomaly_extension_strategy(strategy_name=strategy_name),
            )
        )
    for strategy_name in POST_PUMP_DISTRIBUTION_VARIANTS:
        rows.append(
            StrategyRegistryEntry(
                strategy_name=strategy_name,
                strategy_family="anomaly",
                strategy_contract_version="base_strategy_v2_structural_execution",
                factory=lambda strategy_name=strategy_name: make_post_pump_distribution_strategy(strategy_name=strategy_name),
            )
        )
    return tuple(rows)


def executable_strategy_names() -> tuple[str, ...]:
    return EXECUTABLE_STRATEGY_NAMES


def specified_not_implemented_strategy_names() -> tuple[str, ...]:
    return SPECIFIED_NOT_IMPLEMENTED_STRATEGY_NAMES


def strategy_implementation_statuses() -> tuple[StrategyImplementationStatus, ...]:
    rows: list[StrategyImplementationStatus] = []
    for strategy_name, defaults in ANOMALY_STRATEGY_DEFAULTS.items():
        executable = strategy_name in EXECUTABLE_STRATEGY_NAMES
        rows.append(
            StrategyImplementationStatus(
                strategy_name=strategy_name,
                strategy_family="anomaly",
                strategy_contract_version="base_strategy_v2_structural_execution",
                horizon_minutes=int(defaults["horizon_minutes"]),
                allowed_horizons=tuple(int(horizon) for horizon in defaults["allowed_horizons"]),
                default_horizon_minutes=int(defaults["default_horizon_minutes"]),
                implementation_status="implemented" if executable else "specified_not_implemented",
                executable=executable,
                registry_error="" if executable else "strategy variant is specified but not implemented yet",
            )
        )
    return tuple(rows)


def get_strategy(
    strategy_name: str,
    *,
    detector_config: object | None = None,
) -> BaseStrategy:
    for entry in available_strategies():
        if entry.strategy_name == strategy_name:
            if detector_config is not None:
                if not isinstance(detector_config, BroadAnomalyDetectorConfig):
                    raise StrategyRegistryError(
                        "detector_config does not match the broad-anomaly strategy contract"
                    )
                if strategy_name not in BROAD_ANOMALY_VARIANTS:
                    raise StrategyRegistryError(
                        "detector_config is only supported by broad-anomaly strategy variants"
                    )
                return make_broad_anomaly_strategy(
                    strategy_name=strategy_name,
                    config=detector_config,
                )
            return entry.factory()
    if strategy_name in SPECIFIED_NOT_IMPLEMENTED_STRATEGY_NAMES:
        raise StrategyRegistryError(f"strategy variant is specified but not implemented yet: {strategy_name!r}")
    raise StrategyRegistryError(f"unknown strategy_name: {strategy_name!r}")


def validate_strategy_horizon(strategy_name: str, horizon_minutes: int) -> None:
    """Validate an executable strategy/horizon pair at the registry boundary.

    Core owns the technical supported-horizon whitelist. Strategy metadata owns
    the semantic allowed horizons for a concrete hypothesis. The registry is the
    enforcement point that prevents arbitrary horizons, specified-but-not-
    implemented strategies, and mismatched selected horizons from reaching ML,
    controls, EV, or simulation.
    """
    if not strategy_name:
        raise StrategyRegistryError("strategy_name is required")
    try:
        validate_supported_research_horizon(horizon_minutes, field_name="horizon_minutes")
    except ValueError as exc:
        raise StrategyRegistryError(str(exc)) from exc

    strategy = get_strategy(strategy_name)
    metadata = strategy.metadata
    if horizon_minutes != metadata.horizon_minutes:
        raise StrategyRegistryError(
            "strategy/horizon mismatch: "
            f"{strategy_name!r} selects h{metadata.horizon_minutes}, but h{horizon_minutes} was requested"
        )
    if horizon_minutes not in metadata.allowed_horizons:
        raise StrategyRegistryError(
            "strategy horizon is not semantically allowed: "
            f"{strategy_name!r} requested h{horizon_minutes}, allowed={metadata.allowed_horizons}"
        )


def get_broad_anomaly_strategy(
    config: BroadAnomalyDetectorConfig | None = None,
    *,
    strategy_name: str = "broad_anomaly_v1_h30",
) -> BroadAnomalyStrategy:
    return make_broad_anomaly_strategy(strategy_name=strategy_name, config=config)


def get_post_pump_distribution_strategy(*, strategy_name: str = "post_pump_distribution_v1_h120") -> PostPumpDistributionStrategy:
    return make_post_pump_distribution_strategy(strategy_name=strategy_name)


def get_post_anomaly_extension_strategy(*, strategy_name: str = "post_anomaly_extension_v1_h120") -> PostAnomalyExtensionStrategy:
    return make_post_anomaly_extension_strategy(strategy_name=strategy_name)


def annotation_iteration_hooks() -> dict[str, Callable[[Path], dict[str, Any]]]:
    from anomaly_science.strategy.triple_tap.annotation_iteration import run_trade_report_iteration

    return {"triple_tap_manual_pump_review": run_trade_report_iteration}


def annotation_apps(project_root: Path) -> tuple[AnnotationStrategyApp, ...]:
    """Declare strategy-owned annotation artifacts without coupling Core to strategies."""

    from anomaly_science.annotation.app import AnnotationStrategyApp
    from anomaly_science.strategy.pump_wave_short.lifecycle_review import PUMP_LIFECYCLE_REVIEW_QUESTIONS
    from anomaly_science.strategy.session_reclaim.desk_review import SESSION_RECLAIM_REVIEW_QUESTIONS

    cache_dir = project_root / ".output" / "market" / "binance_vision" / "um_futures" / "enriched_1m"
    session_root = project_root / ".output" / "results" / "session_break"
    knife_root = project_root / ".output" / "results" / "knife_catch"
    triple_root = project_root / ".output" / "results" / "triple_tap_v1" / "manual_pump_review"
    pump_wave_root = project_root / ".output" / "results" / "pump_wave_short_v1" / "lifecycle_review"
    session_reclaim_root = project_root / ".output" / "research" / "session_reclaim_short" / "desk_review_is"
    top_congestion_root = project_root / ".output" / "research" / "top_congestion" / "universe_is"

    apps = (
        AnnotationStrategyApp(
            strategy_id="s8_top_congestion_break",
            title="S8 - Pump-top congestion breakout (LONG)",
            candidates_path=top_congestion_root / "events.parquet",
            labels_path=top_congestion_root / "review_labels.jsonl",
            cache_dir=cache_dir,
            marks_path=top_congestion_root / "marks.jsonl",
        ),
        AnnotationStrategyApp(
            strategy_id="triple_tap_manual_pump_review",
            title="Triple-tap pump review",
            candidates_path=triple_root / "pump_review_candidates.parquet",
            labels_path=triple_root / "pump_level_labels.jsonl",
            cache_dir=cache_dir,
        ),
        AnnotationStrategyApp(
            strategy_id="s1_reclaim_held_level",
            title="S1 - Reclaim held-level short",
            candidates_path=session_root / "reclaim_review" / "candidates.parquet",
            labels_path=session_root / "reclaim_review" / "review_comments.jsonl",
            cache_dir=cache_dir,
        ),
        AnnotationStrategyApp(
            strategy_id="s2_return_to_range",
            title="S2 - Return to distant prior range",
            candidates_path=session_root / "return_range_review" / "candidates.parquet",
            labels_path=session_root / "return_range_review" / "review_comments.jsonl",
            cache_dir=cache_dir,
        ),
        AnnotationStrategyApp(
            strategy_id="s3_session_streak",
            title="S3 - Session-streak momentum",
            candidates_path=session_root / "streak_review" / "candidates.parquet",
            labels_path=session_root / "streak_review" / "review_comments.jsonl",
            cache_dir=cache_dir,
        ),
        AnnotationStrategyApp(
            strategy_id="s4_pump_fade",
            title="S4 - Anomaly fade short",
            candidates_path=session_root / "pump_fade_review" / "candidates.parquet",
            labels_path=session_root / "pump_fade_review" / "review_comments.jsonl",
            cache_dir=cache_dir,
        ),
        AnnotationStrategyApp(
            strategy_id="s5_session_pump",
            title="S5 - Inter-session pump anomaly",
            candidates_path=session_root / "session_pump_review" / "candidates.parquet",
            labels_path=session_root / "session_pump_review" / "review_labels.jsonl",
            cache_dir=cache_dir,
            marks_path=session_root / "session_pump_review" / "marks.jsonl",
        ),
        AnnotationStrategyApp(
            strategy_id="s6_knife_catch",
            title="S6 - Knife-catch dump",
            candidates_path=knife_root / "dump_review" / "candidates.parquet",
            labels_path=knife_root / "dump_review" / "review_labels.jsonl",
            cache_dir=cache_dir,
            marks_path=knife_root / "dump_review" / "marks.jsonl",
        ),
        AnnotationStrategyApp(
            strategy_id="s7_dump_trades",
            title="S7 - Dump trades excluding crash week",
            candidates_path=knife_root / "dump_trade_review" / "candidates.parquet",
            labels_path=knife_root / "dump_trade_review" / "review_labels.jsonl",
            cache_dir=cache_dir,
            marks_path=knife_root / "dump_trade_review" / "marks.jsonl",
        ),
        AnnotationStrategyApp(
            strategy_id="pump_lifecycle_review",
            title="Sleep → pump waves → dump review",
            candidates_path=pump_wave_root / "candidates.parquet",
            labels_path=pump_wave_root / "review_labels.jsonl",
            cache_dir=cache_dir,
            marks_path=pump_wave_root / "marks.jsonl",
            review_questions=PUMP_LIFECYCLE_REVIEW_QUESTIONS,
        ),
        AnnotationStrategyApp(
            strategy_id="session_reclaim_mechanics_review",
            title="Session failed-break reclaim short · complete mechanics",
            candidates_path=session_reclaim_root / "candidates.parquet",
            labels_path=session_reclaim_root / "review_labels.jsonl",
            cache_dir=cache_dir,
            review_questions=SESSION_RECLAIM_REVIEW_QUESTIONS,
        ),
    )
    return tuple(app for app in apps if app.candidates_path.exists() and app.cache_dir.exists())
