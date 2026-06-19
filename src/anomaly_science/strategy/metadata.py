from __future__ import annotations

from typing import Iterable, Mapping

from anomaly_science.contracts.audit import RunConfigRow
from anomaly_science.strategy.base import BaseStrategy
from anomaly_science.strategy.registry import get_strategy


DEFAULT_RESEARCH_STRATEGY_NAME = "broad_anomaly_v1_h30"


def format_required_data_streams(streams: Mapping[str, bool]) -> str:
    return ";".join(f"{name}={str(required).lower()}" for name, required in sorted(streams.items()))


def active_strategy_h_max_minutes(strategy_names: Iterable[str]) -> int:
    names = tuple(strategy_names)
    if not names:
        raise ValueError("active_strategy_names must not be empty")
    horizons = tuple(get_strategy(name).metadata.horizon_minutes for name in names)
    return max(horizons)


def strategy_metadata_run_config_rows(strategy: BaseStrategy | None = None, *, strategy_name: str | None = None) -> list[RunConfigRow]:
    if strategy is None:
        strategy = get_strategy(strategy_name or DEFAULT_RESEARCH_STRATEGY_NAME)
    metadata = strategy.metadata
    return [
        RunConfigRow(key="strategy_name", value=metadata.strategy_name, source="strategy_registry"),
        RunConfigRow(key="strategy_version", value=metadata.strategy_version, source="strategy_registry"),
        RunConfigRow(key="strategy_contract_version", value=metadata.strategy_contract_version, source="strategy_registry"),
        RunConfigRow(key="strategy_family", value=metadata.strategy_family, source="strategy_registry"),
        RunConfigRow(key="strategy_horizon_minutes", value=str(metadata.horizon_minutes), source="strategy_registry"),
        RunConfigRow(key="take_profit_atr_1440", value=str(metadata.take_profit_atr_1440), source="strategy_registry"),
        RunConfigRow(key="stop_loss_atr_1440", value=str(metadata.stop_loss_atr_1440), source="strategy_registry"),
        RunConfigRow(key="feature_schema_version", value=metadata.feature_schema_version, source="strategy_registry"),
        RunConfigRow(key="label_schema_version", value=metadata.label_schema_version, source="strategy_registry"),
        RunConfigRow(
            key="required_data_streams",
            value=format_required_data_streams(strategy.required_data_streams),
            source="strategy_registry",
        ),
    ]
