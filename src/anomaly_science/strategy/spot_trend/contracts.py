from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal


DAY_COUNT = 365
DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20, 30, 60, 90, 150, 250, 360)
MODEL_SEEDS: tuple[int, ...] = (17, 43, 91)
FEATURE_SCHEMA_VERSION = "spot_trend_features_v1"
STRATEGY_VERSION = "binance_spot_trend_v1"


class SpotTrendContractError(ValueError):
    """Raised when a scientific or point-in-time contract is violated."""


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    quote_asset: str = "USDT"
    minimum_history_bars: int = 365
    liquidity_window: int = 30
    minimum_median_quote_volume: float = 2_000_000.0
    entry_rank: int = 20
    retention_rank: int = 30
    maximum_members: int = 20

    def __post_init__(self) -> None:
        if self.minimum_history_bars < 2:
            raise SpotTrendContractError("minimum_history_bars must be >= 2")
        if not 0 < self.entry_rank <= self.retention_rank:
            raise SpotTrendContractError("entry_rank must be positive and <= retention_rank")
        if self.maximum_members > self.entry_rank:
            raise SpotTrendContractError("maximum_members cannot exceed entry_rank")
        if self.minimum_median_quote_volume <= 0:
            raise SpotTrendContractError("minimum_median_quote_volume must be positive")


@dataclass(frozen=True, slots=True)
class CatBoostConfig:
    target_horizon_days: int = 20
    training_lookback_days: int = 1_095
    validation_days: int = 180
    purge_days: int = 20
    refit_frequency: Literal["weekly"] = "weekly"
    seeds: tuple[int, ...] = MODEL_SEEDS
    iterations: int = 1_500
    learning_rate: float = 0.03
    depth: int = 6
    l2_leaf_reg: float = 10.0
    random_strength: float = 1.0
    bagging_temperature: float = 1.0
    od_wait: int = 100

    def __post_init__(self) -> None:
        if self.refit_frequency != "weekly":
            raise SpotTrendContractError("heavy ML must use frozen weekly walk-forward")
        if self.target_horizon_days <= 0:
            raise SpotTrendContractError("target_horizon_days must be positive")
        if self.purge_days < self.target_horizon_days:
            raise SpotTrendContractError("purge_days must cover the target horizon")
        if self.validation_days <= self.purge_days:
            raise SpotTrendContractError("validation_days must exceed purge_days")
        if not self.seeds or len(set(self.seeds)) != len(self.seeds):
            raise SpotTrendContractError("seeds must be a non-empty unique tuple")

    def model_parameters(self, seed: int) -> dict[str, object]:
        if seed not in self.seeds:
            raise SpotTrendContractError(f"unregistered CatBoost seed: {seed}")
        return {
            "loss_function": "RMSE",
            "eval_metric": "MAE",
            "iterations": self.iterations,
            "learning_rate": self.learning_rate,
            "depth": self.depth,
            "l2_leaf_reg": self.l2_leaf_reg,
            "random_strength": self.random_strength,
            "bagging_temperature": self.bagging_temperature,
            "has_time": True,
            "use_best_model": True,
            "od_type": "Iter",
            "od_wait": self.od_wait,
            "allow_writing_files": False,
            "random_seed": seed,
            "verbose": False,
        }


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    base_slot_weight: float = 0.05
    asset_volatility_target: float = 0.25
    asset_volatility_window: int = 90
    covariance_window: int = 60
    ewma_lambda: float = 0.94
    covariance_diagonal_shrinkage: float = 0.5
    portfolio_volatility_target: float = 0.15
    maximum_asset_weight: float = 0.05
    maximum_gross_exposure: float = 1.0
    absolute_rebalance_band: float = 0.0025
    relative_rebalance_band: float = 0.20

    def __post_init__(self) -> None:
        if len(self.horizons) != 9 or tuple(sorted(set(self.horizons))) != self.horizons:
            raise SpotTrendContractError("horizons must contain nine unique increasing values")
        if self.base_slot_weight != 1.0 / 20.0:
            raise SpotTrendContractError("base_slot_weight is frozen at 1/20")
        if not 0 < self.maximum_asset_weight <= self.maximum_gross_exposure <= 1.0:
            raise SpotTrendContractError("long-only exposure caps must be in (0, 1]")
        if not 0 < self.ewma_lambda < 1:
            raise SpotTrendContractError("ewma_lambda must be in (0, 1)")
        if not 0 <= self.covariance_diagonal_shrinkage <= 1:
            raise SpotTrendContractError("covariance shrinkage must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    one_way_cost_bps: int = 25
    participation_cap: float = 0.001
    delisting_penalty: float = 0.02
    initial_capital: float = 1_000_000.0

    def __post_init__(self) -> None:
        if self.one_way_cost_bps not in {10, 25, 50}:
            raise SpotTrendContractError("one_way_cost_bps must be 10, 25, or 50")
        if self.participation_cap not in {0.001, 0.0005}:
            raise SpotTrendContractError("participation_cap must be 0.001 or 0.0005")
        if self.delisting_penalty < 0 or self.initial_capital <= 0:
            raise SpotTrendContractError("simulation capital/penalty is invalid")


@dataclass(frozen=True, slots=True)
class AdmissionConfig:
    baseline_min_sharpe: float = 0.8
    baseline_max_drawdown: float = 0.25
    required_positive_halfyears: int = 3
    evaluated_halfyears: int = 4
    maximum_top_three_trade_profit_share: float = 0.30
    overlay_min_sharpe_improvement: float = 0.15
    overlay_max_drawdown_degradation: float = 0.02
    overlay_max_turnover_ratio: float = 1.25
    negative_control_min_sharpe_advantage: float = 0.10
    minimum_daily_rank_ic: float = 0.0
    minimum_mae_improvement: float = 0.0
    minimum_top_bottom_target_spread: float = 0.0
    minimum_top_quintile_expected_target: float = 0.0


@dataclass(frozen=True, slots=True)
class HoldoutPolicy:
    candidate_start: date = date(2025, 3, 20)
    candidate_end: date = date(2026, 6, 30)
    protocol_freeze_date: date = date(2026, 7, 20)
    forward_start: date = date(2026, 7, 21)
    candidate_was_unseen: bool | None = None

    @property
    def candidate_status(self) -> str:
        if self.candidate_was_unseen is True:
            return "locked_blind_holdout"
        if self.candidate_was_unseen is False:
            return "historical_oos_previously_inspected"
        return "historical_candidate_until_unseen_attestation"


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    model: CatBoostConfig = field(default_factory=CatBoostConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    admission: AdmissionConfig = field(default_factory=AdmissionConfig)
    holdout: HoldoutPolicy = field(default_factory=HoldoutPolicy)
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    strategy_version: str = STRATEGY_VERSION
    negative_control_seed: int = 20_250_319
