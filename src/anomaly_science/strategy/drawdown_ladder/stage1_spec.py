"""Frozen Stage-1 prediction contract for drawdown-ladder recovery nature."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from anomaly_science.probability import (
    BinaryProbabilityGates,
    BinaryWeeklyWalkForwardConfig,
    PairedProbabilityComparisonConfig,
)


def _utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class DrawdownLadderStage1Spec:
    protocol_version: str = "drawdown_ladder_recovery_prediction_v1"
    protocol_freeze_id: str = "drawdown_ladder_recovery_prediction_20260802_v1"
    feature_schema_version: str = "drawdown_ladder_causal_features_v1"
    label_schema_version: str = "drawdown_ladder_recovery_25bps_48h_v1"
    research_partition: str = "is"
    development_start_ms: int = _utc_ms("2025-06-03T00:00:00Z")
    internal_wfa_start_ms: int = _utc_ms("2025-08-04T00:00:00Z")
    internal_wfa_end_ms: int = _utc_ms("2026-01-01T00:00:00Z")
    recovery_cost_bps: int = 25
    recovery_horizon_minutes: int = 48 * 60
    return_lags_minutes: tuple[int, ...] = (
        1,
        3,
        5,
        15,
        30,
        60,
        120,
        240,
        720,
        1440,
    )
    path_windows_minutes: tuple[int, ...] = (5, 15, 30, 60, 120, 240, 720, 1440)
    ema_periods_minutes: tuple[int, ...] = (5, 9, 21, 50, 100, 200, 400, 800, 1440)
    activity_windows_minutes: tuple[int, ...] = (1, 5, 15, 30, 60, 120, 240, 720, 1440)
    oi_lags_minutes: tuple[int, ...] = (5, 15, 30, 60, 120, 240, 720)
    liquidation_windows_minutes: tuple[int, ...] = (1, 5, 15, 60, 240)
    reference_return_lags_minutes: tuple[int, ...] = (5, 15, 60, 240, 1440)
    reference_correlation_windows_minutes: tuple[int, ...] = (60, 240, 1440)
    breadth_return_lags_minutes: tuple[int, ...] = (5, 15, 60)
    prior_reaction_windows: tuple[int, ...] = (5, 20, 50)
    catboost_iterations: int = 300
    catboost_depth: int = 6
    catboost_learning_rate: float = 0.03
    catboost_l2_leaf_reg: float = 8.0
    catboost_early_stopping_rounds: int = 40
    catboost_thread_count: int = 4
    calibration_method: str = "quantile_binned_beta_isotonic_v2"
    null_permutations: int = 999

    def __post_init__(self) -> None:
        if self.research_partition != "is":
            raise ValueError("Stage 1 is physically locked to IS")
        if not self.internal_wfa_start_ms < self.internal_wfa_end_ms:
            raise ValueError("internal WFA dates must increase")
        if not self.development_start_ms < self.internal_wfa_start_ms:
            raise ValueError("Stage-1 development must precede internal WFA")
        for name in (
            "return_lags_minutes",
            "path_windows_minutes",
            "ema_periods_minutes",
            "activity_windows_minutes",
            "oi_lags_minutes",
            "liquidation_windows_minutes",
            "reference_return_lags_minutes",
            "reference_correlation_windows_minutes",
            "breadth_return_lags_minutes",
            "prior_reaction_windows",
        ):
            values = getattr(self, name)
            if not values or tuple(sorted(set(values))) != values or any(value <= 0 for value in values):
                raise ValueError(f"{name} must be positive, sorted, and unique")
        if self.recovery_horizon_minutes != 48 * 60 or self.recovery_cost_bps != 25:
            raise ValueError("Stage-1 primary target is frozen to 25 bps within 48h")
        if self.catboost_thread_count > 8:
            raise ValueError("Stage-1 CatBoost threads are intentionally bounded")


@dataclass(frozen=True, slots=True)
class DrawdownLadderFeatureDefinition:
    name: str
    family: str
    dtype: str = "float64"
    model_feature: bool = True
    nullable: bool = True
    causal_source: str = "minute data available at or before snapshot"


def build_stage1_feature_catalog(
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> tuple[DrawdownLadderFeatureDefinition, ...]:
    rows: list[DrawdownLadderFeatureDefinition] = [
        DrawdownLadderFeatureDefinition("feature_schema_version", "identity", "string", False, False),
        DrawdownLadderFeatureDefinition("label_schema_version", "identity", "string", False, False),
        DrawdownLadderFeatureDefinition("is_nature_anchor", "identity", "bool", False, False),
        DrawdownLadderFeatureDefinition("candidate_id", "identity", "string", False, False),
        DrawdownLadderFeatureDefinition("parent_event_id", "identity", "string", False, False),
        DrawdownLadderFeatureDefinition("symbol", "identity", "string", False, False),
        DrawdownLadderFeatureDefinition("snapshot_time_ms", "timing", "int64", False, False),
        DrawdownLadderFeatureDefinition("feature_cutoff_time_ms", "timing", "int64", False, False),
        DrawdownLadderFeatureDefinition("future_start_time_ms", "timing", "int64", False, False),
        DrawdownLadderFeatureDefinition("label_resolution_time_ms", "label_only", "int64", False, False),
        DrawdownLadderFeatureDefinition("recovery_25bps_48h", "label_only", "bool", False, False),
        DrawdownLadderFeatureDefinition(
            "recovery_time_or_horizon_minutes", "label_only", "int64", False, False
        ),
        DrawdownLadderFeatureDefinition("label_available", "label_only", "bool", False, False),
    ]
    for name, family, dtype in (
        ("grid_step_pct", "ladder_geometry", "int64"),
        ("filled_level_count", "ladder_geometry", "int64"),
        ("deepest_filled_level_pct", "ladder_geometry", "int64"),
        ("average_entry_to_anchor", "ladder_geometry", "float64"),
        ("snapshot_close_to_anchor", "ladder_geometry", "float64"),
        ("snapshot_close_to_entry", "ladder_geometry", "float64"),
        ("fill_overshoot_pct", "ladder_geometry", "float64"),
        ("same_bar_max_drawdown_pct", "ladder_geometry", "float64"),
        ("fill_bar_range_pct", "ladder_geometry", "float64"),
        ("fill_bar_body_return", "ladder_geometry", "float64"),
        ("fill_bar_close_location", "ladder_geometry", "float64"),
        ("fill_bar_rebound_from_low", "ladder_geometry", "float64"),
        ("minutes_from_order_activation", "timing", "int64"),
        ("session_progress_fraction", "timing", "float64"),
        ("session_seq", "timing", "int64"),
        ("session_name", "timing", "string"),
        ("utc_hour", "timing", "int64"),
        ("utc_weekday", "timing", "int64"),
        ("contract_age_days", "universe", "float64"),
        ("history_gap_count_1440m", "data_quality", "float64"),
        ("oi_available", "availability", "bool"),
        ("liquidation_available", "availability", "bool"),
    ):
        rows.append(DrawdownLadderFeatureDefinition(name, family, dtype, True, True))
    for lag in spec.return_lags_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"return_{lag}m", "price_path"),
                DrawdownLadderFeatureDefinition(f"downside_return_{lag}m", "price_path"),
                DrawdownLadderFeatureDefinition(f"return_acceleration_{lag}m", "price_path"),
            )
        )
    for window in spec.path_windows_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"realized_vol_{window}m", "volatility"),
                DrawdownLadderFeatureDefinition(f"downside_vol_{window}m", "volatility"),
                DrawdownLadderFeatureDefinition(f"range_activity_{window}m", "volatility"),
                DrawdownLadderFeatureDefinition(f"close_to_high_{window}m", "structure"),
                DrawdownLadderFeatureDefinition(f"close_to_low_{window}m", "structure"),
                DrawdownLadderFeatureDefinition(f"efficiency_ratio_{window}m", "price_path"),
            )
        )
    for period in spec.ema_periods_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"close_to_ema_{period}m", "trend"),
                DrawdownLadderFeatureDefinition(f"ema_slope_{period}m", "trend"),
            )
        )
    rows.extend(
        (
            DrawdownLadderFeatureDefinition("ema_fan_bullish_fraction", "trend"),
            DrawdownLadderFeatureDefinition("ema_fan_bearish_fraction", "trend"),
            DrawdownLadderFeatureDefinition("ema_fan_dispersion", "trend"),
        )
    )
    for window in spec.activity_windows_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"quote_volume_{window}m", "activity"),
                DrawdownLadderFeatureDefinition(f"quote_activity_{window}m_vs_24h", "activity"),
                DrawdownLadderFeatureDefinition(f"trade_count_{window}m", "activity"),
                DrawdownLadderFeatureDefinition(f"average_trade_notional_{window}m", "activity"),
                DrawdownLadderFeatureDefinition(f"taker_imbalance_{window}m", "order_flow"),
            )
        )
    rows.extend(
        (
            DrawdownLadderFeatureDefinition("quote_activity_60m_vs_prior_sessions", "session_activity"),
            DrawdownLadderFeatureDefinition("trade_activity_60m_vs_prior_sessions", "session_activity"),
            DrawdownLadderFeatureDefinition("price_volume_correlation_60m", "order_flow"),
            DrawdownLadderFeatureDefinition("return_taker_correlation_60m", "order_flow"),
        )
    )
    for lag in spec.oi_lags_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"oi_change_{lag}m", "open_interest"),
                DrawdownLadderFeatureDefinition(f"price_oi_interaction_{lag}m", "open_interest"),
            )
        )
    for window in spec.liquidation_windows_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"long_liquidation_intensity_{window}m", "liquidation"),
                DrawdownLadderFeatureDefinition(f"short_liquidation_intensity_{window}m", "liquidation"),
                DrawdownLadderFeatureDefinition(f"liquidation_imbalance_{window}m", "liquidation"),
            )
        )
    for lag in spec.reference_return_lags_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"btc_return_{lag}m", "reference_market"),
                DrawdownLadderFeatureDefinition(f"coin_minus_btc_return_{lag}m", "relative_market"),
            )
        )
    for window in spec.reference_correlation_windows_minutes:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"btc_correlation_{window}m", "relative_market"),
                DrawdownLadderFeatureDefinition(f"btc_beta_{window}m", "relative_market"),
                DrawdownLadderFeatureDefinition(f"btc_residual_return_{window}m", "relative_market"),
            )
        )
    rows.extend(
        (
            DrawdownLadderFeatureDefinition("btc_oi_change_15m", "reference_market"),
            DrawdownLadderFeatureDefinition("btc_oi_change_60m", "reference_market"),
            DrawdownLadderFeatureDefinition("btc_oi_change_240m", "reference_market"),
            DrawdownLadderFeatureDefinition("btc_taker_imbalance_15m", "reference_market"),
            DrawdownLadderFeatureDefinition("btc_taker_imbalance_60m", "reference_market"),
            DrawdownLadderFeatureDefinition("btc_range_activity_60m", "reference_market"),
            DrawdownLadderFeatureDefinition("btc_quote_activity_60m_vs_24h", "reference_market"),
        )
    )
    for lag in spec.breadth_return_lags_minutes:
        for suffix in (
            "mean_return",
            "return_dispersion",
            "share_negative",
            "share_down_1pct",
            "share_down_3pct",
            "share_up_1pct",
            "share_up_3pct",
        ):
            rows.append(DrawdownLadderFeatureDefinition(f"market_{suffix}_{lag}m", "market_breadth"))
    rows.extend(
        (
            DrawdownLadderFeatureDefinition("market_symbol_count", "market_breadth"),
            DrawdownLadderFeatureDefinition("market_mean_quote_activity_60m", "market_breadth"),
            DrawdownLadderFeatureDefinition("market_share_quote_activity_gt_3x", "market_breadth"),
            DrawdownLadderFeatureDefinition("market_share_quote_activity_gt_10x", "market_breadth"),
        )
    )
    for window in spec.prior_reaction_windows:
        rows.extend(
            (
                DrawdownLadderFeatureDefinition(f"prior_symbol_recovery_rate_{window}", "event_memory"),
                DrawdownLadderFeatureDefinition(f"prior_exact_state_recovery_rate_{window}", "event_memory"),
                DrawdownLadderFeatureDefinition(f"prior_symbol_median_recovery_minutes_{window}", "event_memory"),
            )
        )
    rows.extend(
        (
            DrawdownLadderFeatureDefinition("prior_symbol_resolved_count", "event_memory"),
            DrawdownLadderFeatureDefinition("prior_exact_state_resolved_count", "event_memory"),
            DrawdownLadderFeatureDefinition("simultaneous_ladder_signal_count", "market_breadth"),
            DrawdownLadderFeatureDefinition("simultaneous_ladder_parent_count", "market_breadth"),
        )
    )
    names = [row.name for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("Stage-1 feature catalog contains duplicate names")
    return tuple(rows)


def write_stage1_protocol(
    path: str | Path,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    catalog = build_stage1_feature_catalog(spec)
    payload = {
        "spec": asdict(spec),
        "feature_count": sum(row.model_feature for row in catalog),
        "feature_catalog": [asdict(row) for row in catalog],
        "target": {
            "population": "all complete equal-notional long ladder states",
            "positive_class": "25bps cost-adjusted recovery within 48h",
            "group_exclusion": "parent_event_id",
            "sample_weight": "inverse states per parent event",
            "training_eligibility": "label_resolution_time_ms < weekly_model_freeze_time_ms",
        },
        "model_comparison": {
            "primary_arm": "full_causal",
            "negative_control_arm": "structural_baseline",
            "primary_claim_requires": (
                "full arm passes absolute probability gates and paired incremental "
                "gates versus the structural baseline"
            ),
            "full_causal_config": asdict(
                build_stage1_probability_config(arm="full_causal", spec=spec)
            ),
            "structural_baseline_config": asdict(
                build_stage1_probability_config(arm="structural_baseline", spec=spec)
            ),
            "paired_config": asdict(build_stage1_paired_comparison_config(spec)),
        },
        "oos_2026_accessed": False,
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)
    return target


_STRUCTURAL_BASELINE_FEATURES = (
    "grid_step_pct",
    "filled_level_count",
    "deepest_filled_level_pct",
    "average_entry_to_anchor",
    "snapshot_close_to_anchor",
    "snapshot_close_to_entry",
    "fill_overshoot_pct",
    "same_bar_max_drawdown_pct",
    "fill_bar_range_pct",
    "fill_bar_body_return",
    "fill_bar_close_location",
    "fill_bar_rebound_from_low",
    "minutes_from_order_activation",
    "session_progress_fraction",
    "session_seq",
    "session_name",
    "utc_hour",
    "utc_weekday",
    "contract_age_days",
    "history_gap_count_1440m",
)


def build_stage1_probability_config(
    *,
    arm: str,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> BinaryWeeklyWalkForwardConfig:
    """Freeze either the structural baseline or the full causal feature arm."""

    if arm not in {"structural_baseline", "full_causal"}:
        raise ValueError("Stage-1 probability arm must be structural_baseline or full_causal")
    catalog = build_stage1_feature_catalog(spec)
    catalog_by_name = {row.name: row for row in catalog}
    selected = (
        _STRUCTURAL_BASELINE_FEATURES
        if arm == "structural_baseline"
        else tuple(row.name for row in catalog if row.model_feature)
    )
    categorical = tuple(
        name for name in selected if catalog_by_name[name].dtype == "string"
    )
    numeric = tuple(name for name in selected if name not in categorical)
    label_only = tuple(row.name for row in catalog if row.family == "label_only")
    return BinaryWeeklyWalkForwardConfig(
        protocol_freeze_id=f"{spec.protocol_freeze_id}:{arm}",
        strategy_name=f"drawdown_ladder_recovery:{arm}",
        target_name="recovery_25bps_48h",
        development_start_ms=spec.development_start_ms,
        oos_start_ms=spec.internal_wfa_start_ms,
        oos_end_ms=spec.internal_wfa_end_ms,
        numeric_features=numeric,
        categorical_features=categorical,
        breakdown_columns=(
            "parent_event_id",
            "grid_step_pct",
            "deepest_filled_level_pct",
            "session_name",
        ),
        required_true_columns=("label_available",),
        group_column="candidate_id",
        weight_group_column="parent_event_id",
        split_group_column="parent_event_id",
        symbol_column="symbol",
        snapshot_time_column="snapshot_time_ms",
        feature_cutoff_time_column="feature_cutoff_time_ms",
        future_start_time_column="future_start_time_ms",
        resolution_time_column="label_resolution_time_ms",
        label_column="recovery_25bps_48h",
        label_available_column="label_available",
        row_filter_column="is_nature_anchor",
        required_label_schema_column="label_schema_version",
        required_label_schema_value=spec.label_schema_version,
        required_input_schema_column="feature_schema_version",
        required_input_schema_value=spec.feature_schema_version,
        label_only_columns=label_only,
        min_train_rows=5_000,
        min_split_rows=500,
        min_class_rows_per_split=50,
        catboost_iterations=spec.catboost_iterations,
        catboost_depth=spec.catboost_depth,
        catboost_learning_rate=spec.catboost_learning_rate,
        catboost_l2_leaf_reg=spec.catboost_l2_leaf_reg,
        catboost_early_stopping_rounds=spec.catboost_early_stopping_rounds,
        catboost_thread_count=spec.catboost_thread_count,
        calibration_method=spec.calibration_method,
        null_permutations=spec.null_permutations,
        gates=BinaryProbabilityGates(
            min_oos_rows=20_000,
            max_skipped_oos_week_fraction=0.0,
            min_auc=0.60,
            min_log_loss_improvement=0.005,
            min_brier_improvement=0.002,
            max_ece=0.05,
            max_auc_permutation_p_value=0.05,
            max_log_loss_permutation_p_value=0.05,
            high_probability_threshold=0.92,
            min_high_probability_rows=1_000,
            min_high_probability_observed_rate=0.92,
            min_high_probability_wilson_lower_95=0.90,
            max_high_probability_calibration_gap=0.05,
        ),
    )


def build_stage1_paired_comparison_config(
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> PairedProbabilityComparisonConfig:
    """Freeze the incremental full-context test against structural geometry."""

    return PairedProbabilityComparisonConfig(
        protocol_freeze_id=f"{spec.protocol_freeze_id}:full_minus_structural",
        bootstrap_iterations=2_000,
        sign_flip_iterations=999,
        alpha=0.05 / 3.0,
        min_auc_delta=0.01,
        min_log_loss_improvement=0.002,
        min_brier_improvement=0.001,
    )


def write_stage1_probability_configs(
    directory: str | Path,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
) -> tuple[Path, Path, Path]:
    """Persist both model arms and their frozen paired-inference contract."""

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    payloads = (
        (
            root / "structural_baseline_probability_config.json",
            asdict(build_stage1_probability_config(arm="structural_baseline", spec=spec)),
        ),
        (
            root / "full_causal_probability_config.json",
            asdict(build_stage1_probability_config(arm="full_causal", spec=spec)),
        ),
        (
            root / "full_minus_structural_comparison_config.json",
            asdict(build_stage1_paired_comparison_config(spec)),
        ),
    )
    paths: list[Path] = []
    for path, payload in payloads:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
        paths.append(path)
    return paths[0], paths[1], paths[2]


__all__ = [
    "DrawdownLadderFeatureDefinition",
    "DrawdownLadderStage1Spec",
    "build_stage1_paired_comparison_config",
    "build_stage1_probability_config",
    "build_stage1_feature_catalog",
    "write_stage1_probability_configs",
    "write_stage1_protocol",
]
