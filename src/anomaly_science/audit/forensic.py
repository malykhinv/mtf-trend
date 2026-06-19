from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping

from anomaly_science.contracts.artifacts import MVP1_ARTIFACT_SCHEMAS, STRATEGY_ARTIFACT_ALIASES
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow
from anomaly_science.contracts.execution import EV_EXECUTION_REFERENCE_MODEL, ROUND_TRIP_COST_MODEL, SIMULATION_ENTRY_PRICE_BASIS


_CANONICAL_AUDIT_ARTIFACT = "strategy_protocol_audit.csv"
_REQUIRED_PLACEBO_CONTROLS = frozenset(("random_labels", "time_shuffled_labels", "symbol_shuffled_labels"))
_REQUIRED_BASELINE_CONTROLS = frozenset(
    (
        "global_prior_only",
        "session_only",
        "event_time_only",
        "price_path_only",
        "volume_only",
        "btc_eth_only",
        "always_follow_anomaly",
        "always_fade_anomaly",
        "fade_only_after_extension",
        "follow_only_early_squeeze",
        "no_cvd_features_ablation",
        "no_oi_features_ablation",
        "no_liquidation_features_ablation",
        "idiosyncratic_only_subset",
        "systemic_cluster_only_subset",
    )
)
_REQUIRED_CALIBRATION_BREAKDOWNS = frozenset(
    (
        "session_utc",
        "test_week",
        "test_month",
        "symbol",
        "systemic_cluster_regime",
        "market_shock_group",
        "alpha_decay_bucket",
        "minutes_since_trigger_bucket",
    )
)

_REQUIRED_MARKET_CONTEXT_FEATURES: dict[str, tuple[str, str]] = {
    "volume_market_percentile": ("cross_sectional_market_relative", "market_relative"),
    "quote_volume_market_percentile": ("cross_sectional_market_relative", "market_relative"),
    "return_1m_market_percentile": ("cross_sectional_market_relative", "market_relative"),
    "return_from_event_market_percentile": ("cross_sectional_market_relative", "market_relative"),
    "oi_growth_market_percentile": ("open_interest", "market_relative"),
    "liq_intensity_market_percentile": ("liquidation", "market_relative"),
    "range_expansion_market_percentile": ("cross_sectional_market_relative", "market_relative"),
    "cross_section_available": ("cross_sectional_market_relative", "boolean_flag"),
    "cross_section_symbol_count": ("cross_sectional_market_relative", "point_in_time_id"),
    "corr_with_btc_15m": ("market_context", "btc_relative"),
    "corr_with_btc_30m": ("market_context", "btc_relative"),
    "corr_with_btc_60m": ("market_context", "btc_relative"),
    "symbol_return_minus_btc_return_5m": ("market_context", "btc_relative"),
    "symbol_return_minus_btc_return_15m": ("market_context", "btc_relative"),
    "idiosyncratic_momentum_score": ("market_context", "dimensionless_ratio"),
    "simultaneous_anomalies_count_1m": ("signal_clustering_systemic_beta", "point_in_time_id"),
    "simultaneous_anomalies_share_1m": ("signal_clustering_systemic_beta", "dimensionless_ratio"),
    "systemic_cluster_regime": ("signal_clustering_systemic_beta", "categorical_bucket"),
    "market_shock_id": ("signal_clustering_systemic_beta", "point_in_time_id"),
}


_REQUIRED_SIMULATION_CONTROL_METRICS = frozenset(
    (
        "always_no_trade_baseline_net_pnl",
        "random_entry_time_control_rows",
        "random_entry_time_control_total_net_pnl",
        "delta_vs_always_no_trade_net_pnl",
        "delta_vs_random_entry_time_net_pnl",
    )
)


class ForensicAuditError(ValueError):
    """Raised when forensic audit inputs are invalid before artifact checks run."""


def build_independent_forensic_audit_rows(root_dir: str | Path) -> list[ProtocolAuditRow]:
    """Build independent protocol-audit rows by reading already-written artifacts.

    This layer is intentionally artifact-driven. It does not trust stage-local
    PASS rows as proof of methodology correctness; it re-reads CSV artifacts and
    checks schema, temporal, purge, horizon-identity, and alias consistency from
    their explicit fields.
    """
    root = Path(root_dir)
    if not root.exists():
        raise ForensicAuditError(f"forensic audit root does not exist: {root}")
    if not root.is_dir():
        raise ForensicAuditError(f"forensic audit root must be a directory: {root}")

    found = _index_known_csv_artifacts(root)
    rows: list[ProtocolAuditRow] = []
    rows.append(_schema_columns_row(found))
    rows.append(_root_run_manifest_completeness_row(root))
    rows.append(_temporal_contract_row(found))
    rows.append(_model_metadata_purge_row(found))
    rows.append(_prediction_oos_cutoff_row(found))
    rows.append(_prediction_model_horizon_identity_row(found))
    rows.append(_canonical_alias_consistency_row(root))
    rows.append(_simulation_decision_contract_alignment_row(found))
    rows.append(_simulation_pessimistic_prices_and_costs_row(found))
    rows.append(_simulation_no_parallel_symbol_positions_row(found))
    rows.append(_required_controls_completeness_row(found))
    rows.append(_calibration_breakdown_completeness_row(found))
    rows.append(_rejection_funnel_completeness_row(found))
    rows.append(_market_context_feature_coverage_row(found))
    rows.append(_stage_audit_fail_summary_row(found))
    rows.append(_forensic_gate_row(rows))
    return rows


def _index_known_csv_artifacts(root: Path) -> dict[str, tuple[Path, ...]]:
    indexed: dict[str, list[Path]] = {}
    for path in root.rglob("*.csv"):
        if path.name not in MVP1_ARTIFACT_SCHEMAS:
            continue
        indexed.setdefault(path.name, []).append(path)
    return {name: tuple(sorted(paths)) for name, paths in indexed.items()}


def _schema_columns_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for name, paths in sorted(found.items()):
        schema = MVP1_ARTIFACT_SCHEMAS[name]
        expected = list(schema.required_columns)
        for path in paths:
            checked += 1
            header = _read_csv_header(path)
            missing = [column for column in expected if column not in header]
            extra = [column for column in header if column not in expected]
            if missing or extra:
                failures.append(f"{path}: missing={missing}, extra={extra}")
    if failures:
        return _row(
            "forensic_artifact_schema_columns_verified",
            AuditStatus.FAIL,
            f"{len(failures)} schema mismatch(es): " + "; ".join(failures[:5]),
        )
    if checked == 0:
        return _row(
            "forensic_artifact_schema_columns_verified",
            AuditStatus.WARN,
            "no known CSV artifacts were found for independent schema verification",
        )
    return _row(
        "forensic_artifact_schema_columns_verified",
        AuditStatus.PASS,
        f"independently verified required/extra columns for {checked} known CSV artifact(s)",
    )



def _root_run_manifest_completeness_row(root: Path) -> ProtocolAuditRow:
    run_config_path = root / "strategy_run_config.csv"
    manifest_path = root / "artifact_manifest.json"
    if not run_config_path.exists():
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            f"missing root research run manifest: {run_config_path}",
            artifact="strategy_run_config.csv",
        )
    if not manifest_path.exists():
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            f"missing root artifact manifest: {manifest_path}",
            artifact="artifact_manifest.json",
        )
    required_keys = {
        "strategy_name",
        "strategy_version",
        "strategy_contract_version",
        "strategy_family",
        "target_horizon_minutes",
        "active_h_max_minutes",
        "research_mode",
        "holdout_days",
        "protocol_freeze_id",
        "research_start_date",
        "research_end_date",
        "forensic_audit_status",
        "data_snapshot_hash",
        "config_hash",
        "dependency_versions",
        "artifact_manifest_path",
        "methodology_gap_ledger_status",
    }
    rows = tuple(_read_csv_rows(run_config_path))
    keys = {row.get("key", "") for row in rows}
    missing = sorted(required_keys - keys)
    if missing:
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            "root strategy_run_config.csv is missing required reproducibility keys: " + ", ".join(missing),
            artifact="strategy_run_config.csv",
        )
    empty_required_values = sorted(
        row.get("key", "") for row in rows if row.get("key", "") in required_keys and row.get("value", "") == ""
    )
    if empty_required_values:
        return _row(
            "forensic_root_research_run_manifest_complete",
            AuditStatus.FAIL,
            "root strategy_run_config.csv has empty required values: " + ", ".join(empty_required_values),
            artifact="strategy_run_config.csv",
        )
    return _row(
        "forensic_root_research_run_manifest_complete",
        AuditStatus.PASS,
        f"root research manifest has {len(required_keys)} required reproducibility key(s) and artifact_manifest.json is present",
        artifact="strategy_run_config.csv",
    )

def _temporal_contract_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for artifact_name in (
        "strategy_state_1m.csv",
        "strategy_feature_matrix.csv",
        "strategy_future_paths.csv",
        "strategy_outcome_labels.csv",
        "strategy_oos_predictions.csv",
        "strategy_decision_timing.csv",
        "strategy_trade_simulation.csv",
    ):
        for path in found.get(artifact_name, ()):  # canonical only is enough for forensic proof
            for row_index, row in enumerate(_read_csv_rows(path), start=2):
                if "snapshot_time_ms" not in row or "feature_cutoff_time_ms" not in row:
                    continue
                checked += 1
                snapshot = _to_int(row.get("snapshot_time_ms"))
                cutoff = _to_int(row.get("feature_cutoff_time_ms"))
                future_start = _to_int(row.get("future_start_time_ms")) if "future_start_time_ms" in row else None
                if snapshot is None or cutoff is None:
                    failures.append(f"{path}:{row_index} has non-integer snapshot/feature_cutoff")
                    continue
                if cutoff > snapshot:
                    failures.append(f"{path}:{row_index} feature_cutoff_time_ms={cutoff} > snapshot_time_ms={snapshot}")
                if future_start is not None and future_start <= snapshot:
                    failures.append(f"{path}:{row_index} future_start_time_ms={future_start} <= snapshot_time_ms={snapshot}")
    if failures:
        return _row(
            "forensic_temporal_contract_verified_from_artifacts",
            AuditStatus.FAIL,
            f"{len(failures)} temporal violation(s): " + "; ".join(failures[:5]),
        )
    if checked == 0:
        return _row(
            "forensic_temporal_contract_verified_from_artifacts",
            AuditStatus.WARN,
            "no temporal rows were available for independent artifact verification",
        )
    return _row(
        "forensic_temporal_contract_verified_from_artifacts",
        AuditStatus.PASS,
        f"verified feature_cutoff_time_ms <= snapshot_time_ms and future_start_time_ms > snapshot_time_ms for {checked} row(s)",
    )


def _model_metadata_purge_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for path in found.get("strategy_model_metadata.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            train_cutoff = _to_int(row.get("train_cutoff_time_ms"))
            freeze = _to_int(row.get("weekly_model_freeze_time_ms"))
            h_max = _to_int(row.get("active_h_max_minutes"))
            if train_cutoff is None or freeze is None or h_max is None:
                failures.append(f"{path}:{row_index} has non-integer train_cutoff/freeze/active_h_max")
                continue
            checked += 1
            required_cutoff = train_cutoff + h_max * 60_000
            if required_cutoff > freeze:
                failures.append(
                    f"{path}:{row_index} train_cutoff_time_ms + active_h_max_minutes exceeds weekly_model_freeze_time_ms "
                    f"({train_cutoff} + {h_max}m > {freeze})"
                )
    if failures:
        return _row(
            "forensic_model_metadata_purge_hmax_verified",
            AuditStatus.FAIL,
            f"{len(failures)} purge violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_model_metadata.csv",
        )
    if checked == 0:
        return _row(
            "forensic_model_metadata_purge_hmax_verified",
            AuditStatus.WARN,
            "no strategy_model_metadata.csv rows were available for independent purge verification",
            artifact="strategy_model_metadata.csv",
        )
    return _row(
        "forensic_model_metadata_purge_hmax_verified",
        AuditStatus.PASS,
        f"verified train_cutoff_time_ms + active_h_max_minutes <= weekly_model_freeze_time_ms for {checked} frozen model row(s)",
        artifact="strategy_model_metadata.csv",
    )


def _prediction_oos_cutoff_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for path in found.get("strategy_oos_predictions.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            snapshot = _to_int(row.get("snapshot_time_ms"))
            train_cutoff = _to_int(row.get("train_cutoff_time_ms"))
            if snapshot is None or train_cutoff is None:
                failures.append(f"{path}:{row_index} has non-integer snapshot/train_cutoff")
                continue
            checked += 1
            if train_cutoff >= snapshot:
                failures.append(f"{path}:{row_index} train_cutoff_time_ms={train_cutoff} >= snapshot_time_ms={snapshot}")
    if failures:
        return _row(
            "forensic_prediction_rows_are_oos_after_train_cutoff",
            AuditStatus.FAIL,
            f"{len(failures)} OOS cutoff violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_oos_predictions.csv",
        )
    if checked == 0:
        return _row(
            "forensic_prediction_rows_are_oos_after_train_cutoff",
            AuditStatus.WARN,
            "no prediction rows were available for independent OOS cutoff verification",
            artifact="strategy_oos_predictions.csv",
        )
    return _row(
        "forensic_prediction_rows_are_oos_after_train_cutoff",
        AuditStatus.PASS,
        f"verified train_cutoff_time_ms < snapshot_time_ms for {checked} OOS prediction row(s)",
        artifact="strategy_oos_predictions.csv",
    )


def _prediction_model_horizon_identity_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    metadata_by_key: dict[str, dict[str, str]] = {}
    duplicate_keys: list[str] = []
    failures: list[str] = []
    for path in found.get("strategy_model_metadata.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            key = row.get("model_key", "")
            if not key:
                failures.append(f"{path}:{row_index} has empty model_key")
                continue
            if key in metadata_by_key:
                duplicate_keys.append(key)
            metadata_by_key[key] = row
    if duplicate_keys:
        failures.append("duplicate model metadata keys: " + ", ".join(sorted(set(duplicate_keys))[:5]))

    checked = 0
    for path in found.get("strategy_oos_predictions.csv", ()):
        for row_index, prediction in enumerate(_read_csv_rows(path), start=2):
            key = prediction.get("model_key", "")
            metadata = metadata_by_key.get(key)
            if metadata is None:
                failures.append(f"{path}:{row_index} model_key={key!r} missing from strategy_model_metadata.csv")
                continue
            checked += 1
            for field_name in (
                "strategy_name",
                "strategy_version",
                "strategy_contract_version",
                "target_horizon_minutes",
                "target_label_column",
                "active_h_max_minutes",
            ):
                if prediction.get(field_name) != metadata.get(field_name):
                    failures.append(
                        f"{path}:{row_index} {field_name}={prediction.get(field_name)!r} differs from model metadata {metadata.get(field_name)!r}"
                    )
    if failures:
        return _row(
            "forensic_prediction_model_horizon_identity_consistent",
            AuditStatus.FAIL,
            f"{len(failures)} identity mismatch(es): " + "; ".join(failures[:5]),
            artifact="strategy_oos_predictions.csv",
        )
    if checked == 0:
        return _row(
            "forensic_prediction_model_horizon_identity_consistent",
            AuditStatus.WARN,
            "no joined prediction/model rows were available for independent horizon identity verification",
            artifact="strategy_oos_predictions.csv",
        )
    return _row(
        "forensic_prediction_model_horizon_identity_consistent",
        AuditStatus.PASS,
        f"verified prediction rows match frozen model metadata identity for {checked} row(s)",
        artifact="strategy_oos_predictions.csv",
    )


def _canonical_alias_consistency_row(root: Path) -> ProtocolAuditRow:
    failures: list[str] = []
    checked = 0
    for anomaly_name, strategy_name in sorted(STRATEGY_ARTIFACT_ALIASES.items()):
        anomaly_by_parent = {path.parent: path for path in root.rglob(anomaly_name)}
        strategy_by_parent = {path.parent: path for path in root.rglob(strategy_name)}
        if not anomaly_by_parent and not strategy_by_parent:
            continue
        checked += 1
        missing_strategy = sorted(set(anomaly_by_parent) - set(strategy_by_parent))
        missing_anomaly = sorted(set(strategy_by_parent) - set(anomaly_by_parent))
        if missing_strategy:
            failures.append(f"{anomaly_name}/{strategy_name} missing strategy aliases in: " + ", ".join(str(path) for path in missing_strategy[:5]))
        if missing_anomaly:
            failures.append(f"{anomaly_name}/{strategy_name} missing anomaly aliases in: " + ", ".join(str(path) for path in missing_anomaly[:5]))
        for parent in sorted(set(anomaly_by_parent) & set(strategy_by_parent)):
            anomaly_path = anomaly_by_parent[parent]
            strategy_path = strategy_by_parent[parent]
            if anomaly_path.read_bytes() != strategy_path.read_bytes():
                failures.append(f"{anomaly_path} content differs from {strategy_path}")
                continue
    if failures:
        return _row(
            "forensic_canonical_alias_artifacts_match",
            AuditStatus.FAIL,
            f"{len(failures)} alias mismatch(es): " + "; ".join(failures[:5]),
        )
    if checked == 0:
        return _row(
            "forensic_canonical_alias_artifacts_match",
            AuditStatus.WARN,
            "no canonical/alias artifact pairs were found for independent comparison",
        )
    return _row(
        "forensic_canonical_alias_artifacts_match",
        AuditStatus.PASS,
        f"verified {checked} canonical strategy_* artifact family alias pair(s)",
    )


def _simulation_decision_contract_alignment_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    decisions: dict[tuple[str, str, str, str], dict[str, str]] = {}
    duplicate_keys: list[str] = []
    failures: list[str] = []
    for path in found.get("strategy_decision_timing.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            key = _decision_simulation_key(row)
            if key is None:
                failures.append(f"{path}:{row_index} has incomplete decision/simulation key")
                continue
            if key in decisions:
                duplicate_keys.append("|".join(key))
            decisions[key] = row
    if duplicate_keys:
        failures.append("duplicate decision keys: " + ", ".join(sorted(set(duplicate_keys))[:5]))

    checked = 0
    for path in found.get("strategy_trade_simulation.csv", ()):
        for row_index, simulation in enumerate(_read_csv_rows(path), start=2):
            key = _decision_simulation_key(simulation)
            if key is None:
                failures.append(f"{path}:{row_index} has incomplete decision/simulation key")
                continue
            decision = decisions.get(key)
            if decision is None:
                failures.append(f"{path}:{row_index} has no matching strategy_decision_timing.csv row for key {'|'.join(key)}")
                continue
            checked += 1
            field_pairs = (
                ("execution_reference_model", "execution_reference_model"),
                ("cost_model", "cost_model"),
                ("target_horizon_minutes", "target_horizon_minutes"),
                ("decision_action", "best_action"),
            )
            for simulation_field, decision_field in field_pairs:
                if simulation.get(simulation_field) != decision.get(decision_field):
                    failures.append(
                        f"{path}:{row_index} {simulation_field}={simulation.get(simulation_field)!r} "
                        f"differs from decision {decision_field}={decision.get(decision_field)!r}"
                    )
            numeric_pairs = (
                ("fee_bps", "fee_bps"),
                ("slippage_bps", "slippage_bps"),
                ("stop_distance", "stop_distance"),
                ("target_distance", "target_distance"),
                ("ATR_1d_asof_t", "ATR_1d_asof_t"),
            )
            for simulation_field, decision_field in numeric_pairs:
                simulation_value = _to_float(simulation.get(simulation_field))
                decision_value = _to_float(decision.get(decision_field))
                if simulation_value is None or decision_value is None:
                    failures.append(f"{path}:{row_index} has non-numeric {simulation_field}/{decision_field}")
                elif not _float_equal(simulation_value, decision_value):
                    failures.append(
                        f"{path}:{row_index} {simulation_field}={simulation_value} differs from decision {decision_field}={decision_value}"
                    )
    if failures:
        return _row(
            "forensic_simulation_decision_contract_alignment_verified",
            AuditStatus.FAIL,
            f"{len(failures)} simulation/decision contract mismatch(es): " + "; ".join(failures[:5]),
            artifact="strategy_decision_timing.csv;strategy_trade_simulation.csv",
        )
    if checked == 0:
        return _row(
            "forensic_simulation_decision_contract_alignment_verified",
            AuditStatus.WARN,
            "no joined decision/simulation rows were available for independent contract verification",
            artifact="strategy_decision_timing.csv;strategy_trade_simulation.csv",
        )
    return _row(
        "forensic_simulation_decision_contract_alignment_verified",
        AuditStatus.PASS,
        f"verified EV decision/simulation execution, cost, horizon, action, and distance contracts for {checked} row(s)",
        artifact="strategy_decision_timing.csv;strategy_trade_simulation.csv",
    )


def _simulation_pessimistic_prices_and_costs_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    for path in found.get("strategy_trade_simulation.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            checked += 1
            side = row.get("simulated_side")
            if row.get("execution_reference_model") != EV_EXECUTION_REFERENCE_MODEL:
                failures.append(f"{path}:{row_index} has unexpected execution_reference_model={row.get('execution_reference_model')!r}")
            if row.get("entry_price_basis") != SIMULATION_ENTRY_PRICE_BASIS:
                failures.append(f"{path}:{row_index} has unexpected entry_price_basis={row.get('entry_price_basis')!r}")
            if row.get("cost_model") != ROUND_TRIP_COST_MODEL:
                failures.append(f"{path}:{row_index} has unexpected cost_model={row.get('cost_model')!r}")
            if side not in {"long", "short"} or row.get("decision_action") != side:
                failures.append(f"{path}:{row_index} has invalid side/action pair {row.get('decision_action')!r}/{side!r}")
                continue

            values = {
                name: _to_float(row.get(name))
                for name in (
                    "entry_reference_open",
                    "entry_price",
                    "stop_distance",
                    "target_distance",
                    "stop_price",
                    "target_price",
                    "fee_bps",
                    "total_cost",
                    "exit_price",
                )
            }
            if any(value is None for value in values.values()):
                failures.append(f"{path}:{row_index} has non-numeric simulation price/cost field(s)")
                continue
            entry_open = values["entry_reference_open"]
            entry_price = values["entry_price"]
            stop_distance = values["stop_distance"]
            target_distance = values["target_distance"]
            stop_price = values["stop_price"]
            target_price = values["target_price"]
            fee_bps = values["fee_bps"]
            total_cost = values["total_cost"]
            exit_price = values["exit_price"]
            assert entry_open is not None
            assert entry_price is not None
            assert stop_distance is not None
            assert target_distance is not None
            assert stop_price is not None
            assert target_price is not None
            assert fee_bps is not None
            assert total_cost is not None
            assert exit_price is not None

            snapshot = _to_int(row.get("snapshot_time_ms"))
            entry_time = _to_int(row.get("entry_reference_time_ms"))
            exit_time = _to_int(row.get("exit_time_ms"))
            if snapshot is None or entry_time is None or exit_time is None or entry_time <= snapshot or exit_time < entry_time:
                failures.append(f"{path}:{row_index} violates simulation entry/exit time ordering")
            if stop_distance <= 0.0 or target_distance <= 0.0:
                failures.append(f"{path}:{row_index} has non-positive stop/target distance")
            if side == "long":
                if entry_price < entry_open:
                    failures.append(f"{path}:{row_index} long entry_price is below entry_reference_open")
                if target_price <= entry_price or stop_price >= entry_price:
                    failures.append(f"{path}:{row_index} long stop/target geometry is invalid")
            else:
                if entry_price > entry_open:
                    failures.append(f"{path}:{row_index} short entry_price is above entry_reference_open")
                if target_price >= entry_price or stop_price <= entry_price:
                    failures.append(f"{path}:{row_index} short stop/target geometry is invalid")

            expected_total_cost = (entry_price + exit_price) * (fee_bps / 10_000.0)
            if not _float_equal(total_cost, expected_total_cost):
                failures.append(f"{path}:{row_index} total_cost={total_cost} differs from fee model {expected_total_cost}")
            _append_exit_resolution_failures(failures, path=path, row_index=row_index, row=row, side=side, exit_price=exit_price, stop_price=stop_price, target_price=target_price)
    if failures:
        return _row(
            "forensic_simulation_pessimistic_prices_and_costs_verified",
            AuditStatus.FAIL,
            f"{len(failures)} pessimistic simulation violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_trade_simulation.csv",
        )
    if checked == 0:
        return _row(
            "forensic_simulation_pessimistic_prices_and_costs_verified",
            AuditStatus.WARN,
            "no strategy_trade_simulation.csv rows were available for independent price/cost verification",
            artifact="strategy_trade_simulation.csv",
        )
    return _row(
        "forensic_simulation_pessimistic_prices_and_costs_verified",
        AuditStatus.PASS,
        f"verified next-open entry basis, pessimistic side-aware prices, fee costs, and barrier resolution for {checked} simulation row(s)",
        artifact="strategy_trade_simulation.csv",
    )


def _simulation_no_parallel_symbol_positions_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    failures: list[str] = []
    positions: dict[tuple[str, str, str], list[tuple[int, int, Path, int]]] = {}
    for path in found.get("strategy_trade_simulation.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            entry_time = _to_int(row.get("entry_reference_time_ms"))
            exit_time = _to_int(row.get("exit_time_ms"))
            key = (row.get("strategy_name", ""), row.get("strategy_version", ""), row.get("symbol", ""))
            if entry_time is None or exit_time is None or not all(key):
                failures.append(f"{path}:{row_index} has incomplete position interval key")
                continue
            checked += 1
            positions.setdefault(key, []).append((entry_time, exit_time, path, row_index))
    for key, intervals in sorted(positions.items()):
        previous: tuple[int, int, Path, int] | None = None
        for interval in sorted(intervals):
            if previous is not None and interval[0] <= previous[1]:
                failures.append(
                    f"{interval[2]}:{interval[3]} overlaps previous same strategy/symbol position "
                    f"{previous[2]}:{previous[3]} for {'|'.join(key)}"
                )
            previous = interval
    if failures:
        return _row(
            "forensic_simulation_no_parallel_symbol_positions_verified",
            AuditStatus.FAIL,
            f"{len(failures)} parallel-position violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_trade_simulation.csv",
        )
    if checked == 0:
        return _row(
            "forensic_simulation_no_parallel_symbol_positions_verified",
            AuditStatus.WARN,
            "no strategy_trade_simulation.csv rows were available for independent no-parallel-position verification",
            artifact="strategy_trade_simulation.csv",
        )
    return _row(
        "forensic_simulation_no_parallel_symbol_positions_verified",
        AuditStatus.PASS,
        f"verified no overlapping positions per strategy/version/symbol across {checked} simulation row(s)",
        artifact="strategy_trade_simulation.csv",
    )


def _required_controls_completeness_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    failures: list[str] = []
    checked_artifacts = 0
    placebo_names: set[str] = set()
    baseline_names: set[str] = set()
    simulation_metric_names: set[str] = set()

    for path in found.get("strategy_placebo_tests.csv", ()):
        checked_artifacts += 1
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            name = row.get("control_name", "")
            if name:
                placebo_names.add(name)
            _append_control_status_failure(failures, path=path, row_index=row_index, row=row, status_field="status")

    for path in found.get("strategy_baseline_comparison.csv", ()):
        checked_artifacts += 1
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            name = row.get("baseline_name", "")
            if name:
                baseline_names.add(name)
            _append_control_status_failure(failures, path=path, row_index=row_index, row=row, status_field="status")

    for path in found.get("strategy_trade_simulation_metrics.csv", ()):
        checked_artifacts += 1
        for row in _read_csv_rows(path):
            metric_name = row.get("metric_name", "")
            if metric_name:
                simulation_metric_names.add(metric_name)

    missing_placebo = sorted(_REQUIRED_PLACEBO_CONTROLS - placebo_names)
    missing_baselines = sorted(_REQUIRED_BASELINE_CONTROLS - baseline_names)
    missing_simulation_metrics = sorted(_REQUIRED_SIMULATION_CONTROL_METRICS - simulation_metric_names)
    if missing_placebo:
        failures.append("missing placebo controls: " + ", ".join(missing_placebo))
    if missing_baselines:
        failures.append("missing baseline/ablation controls: " + ", ".join(missing_baselines))
    if missing_simulation_metrics:
        failures.append("missing simulation control metrics: " + ", ".join(missing_simulation_metrics))

    if failures:
        return _row(
            "forensic_required_controls_complete",
            AuditStatus.FAIL,
            f"{len(failures)} required control completeness violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_placebo_tests.csv;strategy_baseline_comparison.csv;strategy_trade_simulation_metrics.csv",
        )
    if checked_artifacts == 0:
        return _row(
            "forensic_required_controls_complete",
            AuditStatus.WARN,
            "no control artifacts were available for independent required-control verification",
            artifact="strategy_placebo_tests.csv;strategy_baseline_comparison.csv;strategy_trade_simulation_metrics.csv",
        )
    return _row(
        "forensic_required_controls_complete",
        AuditStatus.PASS,
        "verified required placebo, baseline, ablation, subset, always-no-trade, and random-entry controls from artifacts",
        artifact="strategy_placebo_tests.csv;strategy_baseline_comparison.csv;strategy_trade_simulation_metrics.csv",
    )


def _append_control_status_failure(
    failures: list[str],
    *,
    path: Path,
    row_index: int,
    row: Mapping[str, str],
    status_field: str,
) -> None:
    status = row.get(status_field, "")
    if status not in {"OK", "SKIPPED"}:
        failures.append(f"{path}:{row_index} has invalid control status={status!r}")


def _decision_simulation_key(row: Mapping[str, str]) -> tuple[str, str, str, str] | None:
    event_id = row.get("event_id", "")
    symbol = row.get("symbol", "")
    snapshot_time_ms = row.get("snapshot_time_ms", "")
    feature_cutoff_time_ms = row.get("feature_cutoff_time_ms", "")
    if not event_id or not symbol or not snapshot_time_ms or not feature_cutoff_time_ms:
        return None
    return (event_id, symbol, snapshot_time_ms, feature_cutoff_time_ms)


def _append_exit_resolution_failures(
    failures: list[str],
    *,
    path: Path,
    row_index: int,
    row: Mapping[str, str],
    side: str,
    exit_price: float,
    stop_price: float,
    target_price: float,
) -> None:
    exit_reason = row.get("exit_reason")
    barrier_resolution = row.get("barrier_resolution")
    if exit_reason == "stop_loss":
        if barrier_resolution not in {"single_barrier", "stop_loss_first"}:
            failures.append(f"{path}:{row_index} stop_loss has invalid barrier_resolution={barrier_resolution!r}")
        if side == "long" and exit_price > stop_price:
            failures.append(f"{path}:{row_index} long stop_loss exit is better than stop_price")
        if side == "short" and exit_price < stop_price:
            failures.append(f"{path}:{row_index} short stop_loss exit is better than stop_price")
    elif exit_reason == "target_hit":
        if barrier_resolution != "single_barrier":
            failures.append(f"{path}:{row_index} target_hit has invalid barrier_resolution={barrier_resolution!r}")
        if side == "long" and exit_price > target_price:
            failures.append(f"{path}:{row_index} long target_hit exit is better than target_price")
        if side == "short" and exit_price < target_price:
            failures.append(f"{path}:{row_index} short target_hit exit is better than target_price")
    elif exit_reason == "horizon_close":
        if barrier_resolution != "horizon_close":
            failures.append(f"{path}:{row_index} horizon_close has invalid barrier_resolution={barrier_resolution!r}")
    else:
        failures.append(f"{path}:{row_index} has invalid exit_reason={exit_reason!r}")



def _calibration_breakdown_completeness_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    paths = found.get("strategy_calibration_breakdown.csv", ())
    if not paths:
        return _row(
            "forensic_calibration_breakdowns_complete",
            AuditStatus.FAIL,
            "missing strategy_calibration_breakdown.csv; calibration is only auditable overall, not by regime/symbol/session",
            artifact="strategy_calibration_breakdown.csv",
        )

    checked = 0
    failures: list[str] = []
    breakdowns: set[str] = set()
    for path in paths:
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            checked += 1
            name = row.get("breakdown_name", "")
            if name:
                breakdowns.add(name)
            row_count = _to_int(row.get("row_count"))
            if row_count is None or row_count <= 0:
                failures.append(f"{path}:{row_index} has non-positive row_count={row.get('row_count')!r}")
            for field_name in ("mean_confidence", "empirical_accuracy", "multiclass_brier", "log_loss", "expected_calibration_error"):
                value = _to_float(row.get(field_name))
                if value is None or value < 0.0:
                    failures.append(f"{path}:{row_index} has invalid {field_name}={row.get(field_name)!r}")
            if not row.get("breakdown_value", ""):
                failures.append(f"{path}:{row_index} has empty breakdown_value")
            if not row.get("confidence_bucket", ""):
                failures.append(f"{path}:{row_index} has empty confidence_bucket")
    if checked == 0:
        return _row(
            "forensic_calibration_breakdowns_complete",
            AuditStatus.WARN,
            "strategy_calibration_breakdown.csv exists but contains no rows; calibration breakdown proof requires non-empty OOS predictions",
            artifact="strategy_calibration_breakdown.csv",
        )
    missing = sorted(_REQUIRED_CALIBRATION_BREAKDOWNS - breakdowns)
    if missing:
        failures.append("missing calibration breakdowns: " + ", ".join(missing))
    if failures:
        return _row(
            "forensic_calibration_breakdowns_complete",
            AuditStatus.FAIL,
            f"{len(failures)} calibration-breakdown violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_calibration_breakdown.csv",
        )
    return _row(
        "forensic_calibration_breakdowns_complete",
        AuditStatus.PASS,
        f"verified {len(breakdowns)} required calibration breakdown(s) across {checked} row(s)",
        artifact="strategy_calibration_breakdown.csv",
    )

def _rejection_funnel_completeness_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    paths = found.get("strategy_rejection_funnel.csv", ())
    if not paths:
        return _row(
            "forensic_rejection_funnel_complete",
            AuditStatus.FAIL,
            "missing strategy_rejection_funnel.csv; row lineage and explicit exclusion reasons are not auditable",
            artifact="strategy_rejection_funnel.csv",
        )

    checked = 0
    failures: list[str] = []
    stages: set[str] = set()
    excluded_reason_rows = 0
    required_base_stages = {
        "data_quality",
        "point_in_time_universe",
        "events",
        "state",
        "future_path",
        "labels",
        "prediction",
        "decision",
        "simulation",
    }
    for path in paths:
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            checked += 1
            stage = row.get("stage", "")
            if stage and not stage.startswith("summary:"):
                stages.add(stage)
            status = row.get("status", "")
            reason = row.get("reason_code", "")
            if status not in {"INCLUDED", "EXCLUDED", "SKIPPED"}:
                failures.append(f"{path}:{row_index} has invalid status={status!r}")
            if status == "EXCLUDED":
                if not reason:
                    failures.append(f"{path}:{row_index} excluded row has empty reason_code")
                else:
                    excluded_reason_rows += 1
            if not row.get("row_key", ""):
                failures.append(f"{path}:{row_index} has empty row_key")
            if not row.get("source_artifact", ""):
                failures.append(f"{path}:{row_index} has empty source_artifact")
    missing_stages = sorted(required_base_stages - stages)
    if missing_stages:
        failures.append("missing funnel stages: " + ", ".join(missing_stages))
    if failures:
        return _row(
            "forensic_rejection_funnel_complete",
            AuditStatus.FAIL,
            f"{len(failures)} rejection-funnel violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_rejection_funnel.csv",
        )
    if checked == 0:
        return _row(
            "forensic_rejection_funnel_complete",
            AuditStatus.FAIL,
            "strategy_rejection_funnel.csv exists but contains no rows",
            artifact="strategy_rejection_funnel.csv",
        )
    return _row(
        "forensic_rejection_funnel_complete",
        AuditStatus.PASS,
        f"verified strategy_rejection_funnel.csv coverage for {len(stages)} stage(s), {checked} row(s), and {excluded_reason_rows} explicit exclusion row(s)",
        artifact="strategy_rejection_funnel.csv",
    )


def _market_context_feature_coverage_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    catalog_paths = found.get("strategy_feature_catalog.csv", ())
    matrix_paths = found.get("strategy_feature_matrix.csv", ())
    if not catalog_paths:
        return _row(
            "forensic_market_context_feature_coverage_verified",
            AuditStatus.FAIL,
            "missing strategy_feature_catalog.csv; market context feature contract is not independently auditable",
            artifact="strategy_feature_catalog.csv;strategy_feature_matrix.csv",
        )
    if not matrix_paths:
        return _row(
            "forensic_market_context_feature_coverage_verified",
            AuditStatus.FAIL,
            "missing strategy_feature_matrix.csv; market context feature materialization is not independently auditable",
            artifact="strategy_feature_catalog.csv;strategy_feature_matrix.csv",
        )

    failures: list[str] = []
    catalog_by_name: dict[str, dict[str, str]] = {}
    for path in catalog_paths:
        seen_in_path: set[str] = set()
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            feature_name = row.get("feature_name", "")
            if feature_name in seen_in_path:
                failures.append(f"{path}:{row_index} duplicate feature catalog row for {feature_name!r}")
            if feature_name:
                seen_in_path.add(feature_name)
                catalog_by_name[feature_name] = row
    for feature_name, (expected_family, expected_normalization) in sorted(_REQUIRED_MARKET_CONTEXT_FEATURES.items()):
        row = catalog_by_name.get(feature_name)
        if row is None:
            failures.append(f"missing market-context feature catalog row: {feature_name}")
            continue
        if row.get("feature_family") != expected_family:
            failures.append(f"{feature_name} feature_family={row.get('feature_family')!r}, expected {expected_family!r}")
        if row.get("normalization_type") != expected_normalization:
            failures.append(f"{feature_name} normalization_type={row.get('normalization_type')!r}, expected {expected_normalization!r}")
        if row.get("source_artifact") != "anomaly_feature_matrix.csv":
            failures.append(f"{feature_name} source_artifact={row.get('source_artifact')!r}, expected 'anomaly_feature_matrix.csv'")
        if row.get("uses_future_data") not in {"False", "false", "0", ""}:
            failures.append(f"{feature_name} uses_future_data must be false")

    checked_rows = 0
    for path in matrix_paths:
        header = _read_csv_header(path)
        missing_columns = sorted(set(_REQUIRED_MARKET_CONTEXT_FEATURES) - set(header))
        if missing_columns:
            failures.append(f"{path} missing market-context feature matrix columns: " + ", ".join(missing_columns))
            continue
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            checked_rows += 1
            _append_market_context_matrix_failures(failures, path=path, row_index=row_index, row=row)
    if failures:
        return _row(
            "forensic_market_context_feature_coverage_verified",
            AuditStatus.FAIL,
            f"{len(failures)} market-context feature violation(s): " + "; ".join(failures[:5]),
            artifact="strategy_feature_catalog.csv;strategy_feature_matrix.csv",
        )
    if checked_rows == 0:
        return _row(
            "forensic_market_context_feature_coverage_verified",
            AuditStatus.WARN,
            "market-context catalog exists but no feature-matrix rows were available for value checks",
            artifact="strategy_feature_catalog.csv;strategy_feature_matrix.csv",
        )
    return _row(
        "forensic_market_context_feature_coverage_verified",
        AuditStatus.PASS,
        f"verified required market-relative, BTC-relative, and systemic-cluster feature coverage across {checked_rows} feature-matrix row(s)",
        artifact="strategy_feature_catalog.csv;strategy_feature_matrix.csv",
    )


def _append_market_context_matrix_failures(failures: list[str], *, path: Path, row_index: int, row: Mapping[str, str]) -> None:
    for field_name in (
        "volume_market_percentile",
        "quote_volume_market_percentile",
        "return_1m_market_percentile",
        "return_from_event_market_percentile",
        "oi_growth_market_percentile",
        "liq_intensity_market_percentile",
        "range_expansion_market_percentile",
        "simultaneous_anomalies_share_1m",
    ):
        value = _to_float(row.get(field_name))
        if value is not None and not 0.0 <= value <= 1.0:
            failures.append(f"{path}:{row_index} {field_name}={value} outside [0, 1]")
    for field_name in ("corr_with_btc_15m", "corr_with_btc_30m", "corr_with_btc_60m"):
        value = _to_float(row.get(field_name))
        if value is not None and not -1.0 <= value <= 1.0:
            failures.append(f"{path}:{row_index} {field_name}={value} outside [-1, 1]")
    for field_name in ("cross_section_symbol_count", "simultaneous_anomalies_count_1m"):
        value = _to_int(row.get(field_name))
        if value is None or value < 0:
            failures.append(f"{path}:{row_index} {field_name} must be a non-negative integer")
    cross_section_available = _to_bool(row.get("cross_section_available"))
    if cross_section_available is None:
        failures.append(f"{path}:{row_index} cross_section_available must be boolean")
    elif not cross_section_available:
        for field_name in (
            "volume_market_percentile",
            "quote_volume_market_percentile",
            "return_1m_market_percentile",
            "oi_growth_market_percentile",
            "liq_intensity_market_percentile",
            "range_expansion_market_percentile",
        ):
            if row.get(field_name) not in (None, ""):
                failures.append(f"{path}:{row_index} {field_name} must be empty when cross_section_available=false")
    if row.get("systemic_cluster_regime") not in {"unknown", "idiosyncratic", "moderate_cluster", "systemic_beta_shock"}:
        failures.append(f"{path}:{row_index} invalid systemic_cluster_regime={row.get('systemic_cluster_regime')!r}")
    if not row.get("market_shock_id", ""):
        failures.append(f"{path}:{row_index} market_shock_id is required")


def _stage_audit_fail_summary_row(found: Mapping[str, tuple[Path, ...]]) -> ProtocolAuditRow:
    checked = 0
    fail_rows: list[str] = []
    for path in found.get("strategy_protocol_audit.csv", ()):
        for row_index, row in enumerate(_read_csv_rows(path), start=2):
            checked += 1
            if row.get("status") == AuditStatus.FAIL.value:
                fail_rows.append(f"{path}:{row_index} {row.get('check_name', '')}")
    if fail_rows:
        return _row(
            "forensic_stage_protocol_audit_has_no_fail_rows",
            AuditStatus.FAIL,
            f"stage protocol audit contains {len(fail_rows)} FAIL row(s): " + "; ".join(fail_rows[:5]),
            artifact="strategy_protocol_audit.csv",
        )
    if checked == 0:
        return _row(
            "forensic_stage_protocol_audit_has_no_fail_rows",
            AuditStatus.WARN,
            "no stage strategy_protocol_audit.csv rows were found for FAIL summary",
            artifact="strategy_protocol_audit.csv",
        )
    return _row(
        "forensic_stage_protocol_audit_has_no_fail_rows",
        AuditStatus.PASS,
        f"scanned {checked} stage protocol audit row(s) and found no FAIL statuses",
        artifact="strategy_protocol_audit.csv",
    )


def _forensic_gate_row(rows: Iterable[ProtocolAuditRow]) -> ProtocolAuditRow:
    items = tuple(rows)
    fail_count = sum(1 for row in items if row.status is AuditStatus.FAIL)
    warn_count = sum(1 for row in items if row.status is AuditStatus.WARN)
    if fail_count:
        return _row(
            "forensic_protocol_interpretation_gate",
            AuditStatus.FAIL,
            f"independent forensic audit found {fail_count} FAIL row(s); downstream results must not be interpreted",
        )
    if warn_count:
        return _row(
            "forensic_protocol_interpretation_gate",
            AuditStatus.WARN,
            f"independent forensic audit found {warn_count} WARN row(s); artifacts are incomplete for full forensic proof",
        )
    return _row(
        "forensic_protocol_interpretation_gate",
        AuditStatus.PASS,
        "independent forensic audit found no FAIL or WARN rows",
    )


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.reader(file_obj)
        try:
            return [str(column) for column in next(reader)]
        except StopIteration:
            return []


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _to_bool(value: object) -> bool | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _float_equal(left: float, right: float) -> bool:
    tolerance = max(1e-9, abs(right) * 1e-9)
    return abs(left - right) <= tolerance


def _row(check_name: str, status: AuditStatus, message: str, artifact: str = _CANONICAL_AUDIT_ARTIFACT) -> ProtocolAuditRow:
    return ProtocolAuditRow(check_name=check_name, status=status, message=message, artifact=artifact)
