from __future__ import annotations

from dataclasses import dataclass

from .market import MarketDataContractError

CONTROL_STATUS_OK = "OK"
CONTROL_STATUS_SKIPPED = "SKIPPED"
VALID_CONTROL_STATUSES = frozenset({CONTROL_STATUS_OK, CONTROL_STATUS_SKIPPED})

PLACEBO_CONTROL_NAMES = frozenset({"random_labels", "time_shuffled_labels", "symbol_shuffled_labels"})
BASELINE_NAMES = frozenset(
    {
        "global_prior_only",
        "session_only",
        "event_time_only",
        "price_path_only",
        "always_follow_anomaly",
        "always_fade_anomaly",
        "fade_only_after_extension",
        "follow_only_early_squeeze",
        "volume_only",
        "btc_eth_only",
        "no_cvd_features_ablation",
        "no_oi_features_ablation",
        "no_liquidation_features_ablation",
        "idiosyncratic_only_subset",
        "systemic_cluster_only_subset",
    }
)


@dataclass(frozen=True, slots=True)
class PlaceboTestRow:
    control_version: str
    control_name: str
    target_horizon_minutes: int
    random_seed: int
    available_label_rows: int
    oos_prediction_rows: int
    accuracy: float
    multiclass_brier: float
    log_loss: float
    reference_real_brier: float
    brier_delta_vs_real: float
    status: str
    notes: str

    def __post_init__(self) -> None:
        _validate_common_control_fields(
            version=self.control_version,
            target_horizon_minutes=self.target_horizon_minutes,
            available_label_rows=self.available_label_rows,
            oos_prediction_rows=self.oos_prediction_rows,
            accuracy=self.accuracy,
            multiclass_brier=self.multiclass_brier,
            log_loss=self.log_loss,
            status=self.status,
            notes=self.notes,
        )
        if self.control_name not in PLACEBO_CONTROL_NAMES:
            raise MarketDataContractError(f"unknown placebo control_name: {self.control_name!r}")
        if self.random_seed < 0:
            raise MarketDataContractError("random_seed must be non-negative")
        if self.reference_real_brier < 0.0:
            raise MarketDataContractError("reference_real_brier must be non-negative")


@dataclass(frozen=True, slots=True)
class BaselineComparisonRow:
    control_version: str
    baseline_name: str
    feature_family: str
    target_horizon_minutes: int
    available_label_rows: int
    oos_prediction_rows: int
    accuracy: float
    multiclass_brier: float
    log_loss: float
    reference_real_brier: float
    brier_delta_vs_real: float
    status: str
    notes: str

    def __post_init__(self) -> None:
        _validate_common_control_fields(
            version=self.control_version,
            target_horizon_minutes=self.target_horizon_minutes,
            available_label_rows=self.available_label_rows,
            oos_prediction_rows=self.oos_prediction_rows,
            accuracy=self.accuracy,
            multiclass_brier=self.multiclass_brier,
            log_loss=self.log_loss,
            status=self.status,
            notes=self.notes,
        )
        if self.baseline_name not in BASELINE_NAMES:
            raise MarketDataContractError(f"unknown baseline_name: {self.baseline_name!r}")
        if not self.feature_family:
            raise MarketDataContractError("feature_family is required")
        if self.reference_real_brier < 0.0:
            raise MarketDataContractError("reference_real_brier must be non-negative")


def _validate_common_control_fields(
    *,
    version: str,
    target_horizon_minutes: int,
    available_label_rows: int,
    oos_prediction_rows: int,
    accuracy: float,
    multiclass_brier: float,
    log_loss: float,
    status: str,
    notes: str,
) -> None:
    if not version:
        raise MarketDataContractError("control_version is required")
    if target_horizon_minutes not in (15, 30, 60):
        raise MarketDataContractError("target_horizon_minutes must be one of 15, 30, or 60")
    if available_label_rows < 0:
        raise MarketDataContractError("available_label_rows must be non-negative")
    if oos_prediction_rows < 0:
        raise MarketDataContractError("oos_prediction_rows must be non-negative")
    if accuracy < 0.0 or accuracy > 1.0:
        raise MarketDataContractError("accuracy must be within [0, 1]")
    if multiclass_brier < 0.0:
        raise MarketDataContractError("multiclass_brier must be non-negative")
    if log_loss < 0.0:
        raise MarketDataContractError("log_loss must be non-negative")
    if status not in VALID_CONTROL_STATUSES:
        raise MarketDataContractError(f"unknown control status: {status!r}")
    if not notes:
        raise MarketDataContractError("notes is required")
