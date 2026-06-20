# Patch log

## docs: canonicalize low-level CLI artifact help

Status: APPLIED.

Intent:
- Fix low-level CLI help that still named `anomaly_*` inputs as primary artifacts after canonical `strategy_*` contracts became the documented boundary.
- Keep `anomaly_*` compatibility aliases intact, but stop advertising them as the preferred CLI path.
- Replace stale `RESEARCH_STATE` Git-head `UNKNOWN` text with the checked local head before this patch.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_cli_contract.py tests\test_atlas.py -q`
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\cli.py tests\test_cli_contract.py`
- `.venv\Scripts\python.exe -m pytest -q`

## methodology: validate full 380d cache export proof

Status: APPLIED.

Intent:
- Add `validate-cache-export-proof` so the full cache/export proof can be verified without rewriting multi-GB normalized CSVs.
- Classify missing 1m rows as settlement transitions only when a `{symbol}SETTLED` sibling parquet has timestamps inside the gap.
- Record the local full 380d proof in `research/validation/cache_export_380d_validation.json`.
- Promote the data-source boundary ledger row after validation proved 380 global days, 796 exported perpetual symbols, 344,895,197 1m rows, no missing UTC days, no duplicate rows, and no unclassified 1m gaps.

Validation:
- `.venv\Scripts\python.exe main.py validate-cache-export-proof --manifest tmp\mvp1_input_380d\cache_export_manifest.json --coverage tmp\mvp1_input_380d\cache_export_coverage.csv --out research\validation\cache_export_380d_validation.json --expected-days 380 --allow-settlement-transition-gaps`
- `.venv\Scripts\python.exe -m pytest tests\test_cache_validation.py tests\test_cache_export.py -q`
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\cache_validation.py src\anomaly_science\cli.py tests\test_cache_validation.py`
- `.venv\Scripts\python.exe -m compileall -q main.py src tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe main.py run-research broad_anomaly_v1_h30 --cache-dir tmp\codex_smoke_cache --research-mode frozen_holdout --protocol-freeze-id smoke_20260620_fixture`

## methodology: exclude delivery contracts from cache export discovery

Status: APPLIED.

Intent:
- Keep the MVP1 perpetual export aligned with the Binance Vision cache builder universe by excluding fixed-date delivery-contract parquet files such as `BTCUSDT_250627` during automatic symbol discovery.
- Preserve explicitness: requested delivery symbols fail with a clear error unless `--include-delivery-contracts` is passed for a dedicated delivery-contract experiment.
- Record excluded delivery-contract files in `cache_export_manifest.json` instead of silently ignoring them.
- Add regression coverage for discovery, explicit-symbol rejection, manifest reporting, and the CLI opt-in flag.

Validation:
- `python -m pytest tests/test_cache_export.py tests/test_binance_vision_cache_delivery_symbols.py -q`
- `python -m compileall -q src/anomaly_science/cache_export.py src/anomaly_science/cli.py tests/test_cache_export.py`

## methodology: harden cache export proof gates

Status: APPLIED.

Intent:
- Add explicit cache export validation gates for expected global calendar span, missing UTC days, missing 1m rows, duplicate 1m rows, and optional missing open interest.
- Extend `cache_export_coverage.csv` with per-symbol unique/expected/missing/duplicate 1m row diagnostics, gap diagnostics, partial UTC day count, and complete-span flag.
- Extend `cache_export_manifest.json` with validation config, validation summary, and failure reasons while still writing proof artifacts before raising validation errors.
- Document the 380d proof command while keeping the data-source boundary `PARTIAL` until the real local all-symbol proof run is recorded.

Validation:
- `python -m pytest tests/test_cache_export.py -q`
- `python -m pytest tests/test_cache_export.py tests/test_binance_vision_cache.py tests/test_binance_vision_cache_delivery_symbols.py tests/test_binance_vision_cache_startup.py -q`
- `python -m compileall -q src/anomaly_science/cache_export.py src/anomaly_science/cli.py tests/test_cache_export.py`

## docs: synchronize executable strategy status

Status: APPLIED.

Intent:
- Align README, COMMANDS, and RESEARCH_STATE with the current executable anomaly registry.
- Document `broad_anomaly_v1_h15/h30/h60`, `post_anomaly_extension_v1_h60/h120/h180`, and `post_pump_distribution_v1_h60/h120/h180` as executable offline research variants.
- Remove stale current-state notes that still described post-extension/post-pump variants as specified-only or pending.
- Keep the data-source boundary explicitly PARTIAL until full 380d all-symbol cache/export proof artifacts are recorded.

Validation:
- Documentation-only patch; no runtime behavior changed.
- `git apply --check` against the uploaded snapshot.

## methodology: prove cache export boundary

Status: APPLIED.

Intent:
- Make the Binance Vision cache to MVP1 CSV export write explicit coverage and manifest proof artifacts.
- Record exported symbols, effective date range, row counts, missing UTC days, and hashes for normalized input files.
- Keep `run-research <strategy> [--days]` compact: output is automatic, and omitted `--days` still means full available cache period.
- Leave the data-source ledger row `PARTIAL` until a real full 380d all-symbol local cache/export validation is run and recorded.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_cache_export.py tests\test_research_run.py -q`
- `.venv\Scripts\python.exe -m compileall -q src main.py tests zip_project.py`

## methodology: neutralize core contract names

Status: APPLIED.

Intent:
- Promote strategy-neutral Core contract names: `StrategyEvent`, `StrategyState1mRow`, `StrategyFeatureMatrixRow`, and `StrategyOutcomeLabelRow`.
- Add strategy-neutral loader/builder entrypoints for Core stages while keeping `Anomaly*` and `load_anomaly_*` compatibility aliases for documented anomaly artifacts.
- Move Strategy/Core separation ledger row to implemented without changing canonical/alias artifact schemas.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src main.py tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest tests\test_contracts.py tests\test_state_builder.py tests\test_future_paths.py tests\test_labels.py tests\test_feature_matrix.py tests\test_prediction.py tests\test_decision_expected_value.py tests\test_atlas.py -q`
- `.venv\Scripts\python.exe -m pytest -q`

## methodology: complete final forensic protocol audit coverage

Status: APPLIED.

Intent:
- Add independent forensic checks for pre-trigger data-quality mask enforcement, point-in-time universe eligibility, and full feature-catalog coverage.
- Require `strategy_feature_catalog.csv` to cover feature-matrix columns and model metadata feature references while forbidding future/raw model features.
- Add missing `ATR_1d_asof_t` audit-only catalog row so feature matrix schema and catalog agree.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_forensic_audit.py tests\test_feature_catalog.py -q`
- `.venv\Scripts\python.exe -m pytest tests\test_research_run.py tests\test_feature_matrix.py tests\test_prediction.py tests\test_artifact_schemas.py -q`
- `.venv\Scripts\python.exe -m pytest -q`

## strategy: complete broad anomaly trigger breadth

Status: APPLIED.

Intent:
- Add causal executable trigger-family components for `fast_burst`, `grind_pump`, `breakout`, `pump_inside_noise`, `session_activity_burst`, and `market_wide_impulse`.
- Bump `DETECTOR_VERSION` to `broad_anomaly_detector_v2` because event selection and event IDs changed.
- Keep components as audit metadata only: no future labels, no EV/PnL logic, no trade-direction rule.
- Update anomaly strategy docs and methodology ledger so implemented tags match code.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_events_detector.py -q`
- `.venv\Scripts\python.exe -m pytest tests\test_strategy_contract.py -q`
- `.venv\Scripts\python.exe -m pytest -q`

## methodology: audit market context feature coverage

Status: APPLIED.

Intent:
- Add independent forensic verification that required cross-sectional, BTC-relative, systemic-cluster, and market-shock context features are cataloged and materialized.
- Validate market-context feature families, normalization types, source artifacts, no-future-data flags, feature-matrix columns, percentile/correlation ranges, systemic regime values, and market shock IDs.
- Make rejection funnel write explicit skipped placeholders for empty stages so small smoke runs remain auditable without hard-failing on absent downstream rows.
- Register `calibration_breakdowns_written` in the methodology-v2 audit catalog.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_forensic_audit.py tests\test_feature_catalog.py tests\test_feature_matrix.py tests\test_research_run.py tests\test_prediction.py tests\test_contracts.py tests\test_rejection_funnel.py`

## methodology: audit required controls completeness

Status: APPLIED.

Intent:
- Add independent forensic verification for required placebo, baseline, anomaly ablation/subset, always-no-trade, and random-entry controls.
- Make simulation control delta metrics appear even when no simulated trade rows exist.
- Promote controls ledger rows only after code, artifacts, tests, and forensic proof agree.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_forensic_audit.py tests\test_controls.py tests\test_trade_simulation.py tests\test_research_run.py`

## methodology: strengthen simulation forensic audit

Status: APPLIED.

Intent:
- Add independent artifact-level forensic checks for EV/simulation alignment.
- Verify simulation execution model, entry basis, cost model, pessimistic side-aware prices, fee costs, barrier resolution, and no same-symbol overlapping positions from CSV artifacts.
- Add PASS/FAIL regression tests for simulation forensic assumptions.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_forensic_audit.py tests\test_trade_simulation.py tests\test_research_run.py`

## methodology: align EV and simulation execution reference

Status: APPLIED.

Intent:
- Add a shared EV/simulation execution reference contract.
- Store `execution_reference_model`, entry price basis, and cost model in EV and trade-simulation artifacts.
- Make simulation validate the decision-row execution/cost model before simulating.
- Record the shared model in EV/simulation run configs and protocol audit rows.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_decision_expected_value.py tests\test_trade_simulation.py tests\test_artifact_schemas.py tests\test_research_run.py tests\test_forensic_audit.py`

## methodology: add explicit sample weight policy

Status: APPLIED.

Intent:
- Add `sample_weight_policy="uniform_v1"` to prediction config as an explicit ML protocol contract.
- Pass validated fit-split sample weights into CatBoost instead of relying on implicit library defaults.
- Record sample weight policy and weight sums in model metadata, model diagnostics, run config, and protocol audit.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_prediction.py tests\test_artifact_schemas.py`

## methodology: enforce feature catalog membership before prediction

Status: APPLIED.

Intent:
- Make `build_default_feature_catalog()` a hard gate for every numeric/bool model feature used by prediction.
- Add the current prediction state feature set to the feature catalog.
- Remove top-level audit imports from stage runners so feature/config modules can be imported without protocol-audit cycles.
- Restore explicit Core horizon imports in future/label configs.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_prediction.py tests\test_feature_catalog.py tests\test_horizon_contract.py`

## methodology: harden point-in-time universe

Status: PROPOSED; patch generated after `methodology: enforce data quality mask before trigger`.

Intent:
- Add anti-survivorship fields to `symbol_universe_by_day.csv`: first/last seen timestamps, source symbol status, listing/delisting confidence, and explicit `eligible_for_cross_section`.
- Materialize missing days between a symbol's first and last observed data as non-tradable rows with explicit exclusion reasons.
- Use `eligible_for_cross_section` for market-relative features so missing/inferred universe rows do not enter cross-sectional ranks.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_harden_point_in_time_universe.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_data_audit.py tests/test_feature_matrix.py tests/test_contracts.py` must be run in the project venv because this sandbox lacks `polars`.

## methodology: enforce data quality mask before trigger

Status: PROPOSED; patch generated after `methodology: add full research run manifest`.

Intent:
- Add shared `DataQualityMask` for row-level 1m candle exclusions before `strategy.generate_triggers`.
- Exclude maskable bad candles, duplicate symbol/time rows, impossible close returns, technical-noise rows, and post-gap warm-up rows from detector candidates and detector baseline context.
- Keep dataset/schema/source failures as blocking FAIL rather than silently masking them.
- Write explicit mask exclusion counts into `strategy_data_quality.csv` and event protocol audit.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_enforce_data_quality_mask_before_trigger.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_data_audit.py tests/test_events_detector.py` must be run in the project venv because this sandbox lacks `polars`.

## methodology: add full research run manifest

Status: PROPOSED; patch generated after `methodology: enforce real holdout lock`.

Intent:
- Write a root-level `strategy_run_config.csv` / `anomaly_run_config.csv` for `run-research`, not only per-stage run configs.
- Include strategy identity, target horizon, active `H_max`, research/holdout mode, protocol freeze id, forensic audit status/counts, data snapshot hash, config hash, dependency versions, and methodology ledger status.
- Write a root-level `artifact_manifest.json` covering run CSV/JSON artifacts.
- Make forensic audit verify the root research run manifest exists and contains required reproducibility keys.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_add_full_research_run_manifest.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_forensic_audit.py tests/test_research_run.py` must be run in the project venv because this sandbox lacks `polars`.


## methodology: enforce real holdout lock

Status: PROPOSED; patch generated after `methodology: hard-gate run-research on forensic audit`.

Intent:
- Add explicit `is` and `frozen_holdout` research modes to `run-research`.
- Make default `is` mode exclude the final locked holdout from downstream input before audit/events/state/features/prediction.
- Require `protocol_freeze_id` for `frozen_holdout` and record an approved holdout access row.
- Add holdout mode fields to run summary and governance run config.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_enforce_real_holdout_lock.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_holdout_governance.py tests/test_research_run.py` must be run in the project venv because this sandbox lacks `polars`.


## methodology: hard-gate run-research on forensic audit

Status: PROPOSED; patch generated after `methodology: add independent forensic protocol audit`.

Intent:
- Run the independent artifact-driven forensic audit after the `run-research` simulation stage.
- Write `stages/forensic_audit/strategy_protocol_audit.csv` and the anomaly compatibility alias.
- Add forensic audit status, FAIL count, and WARN count to `research_run_summary.csv`.
- Exit non-zero if the forensic audit emits any `FAIL` row, while still writing the summary first.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_hard_gate_run_research_on_forensic_audit.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_forensic_audit.py tests/test_research_run.py` must be run in the project venv because this sandbox lacks `polars`.

## methodology: add independent forensic protocol audit

Status: PROPOSED; patch generated against uploaded snapshot `project_20260619_124016.zip`.

Intent:
- Add an artifact-driven forensic audit layer that does not trust stage-local PASS rows as proof.
- Re-read written CSV artifacts and independently verify schema columns, temporal contract, model metadata purge/H_max, OOS prediction cutoff, prediction/model horizon identity, canonical/alias consistency, and existing stage audit FAIL rows.
- Add tests for PASS, future leak FAIL, purge FAIL, and alias drift FAIL cases.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_add_independent_forensic_protocol_audit.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_forensic_audit.py` was attempted in this sandbox but collection requires missing third-party dependency `polars`; run it in the project venv.


## methodology: clean up strategy executable horizon status

Status: SUPERSEDED by executable post-anomaly extension and post-pump distribution registry patches.

Changes:
- Add `strategy_implementation_status.csv` as a registry truth table covering executable and specified-only anomaly variants.
- Keep `strategy_registry.csv` executable-only: currently `broad_anomaly_v1_h15/h30/h60`.
- Mark `post_anomaly_extension_v1_h60/h120/h180` and `post_pump_distribution_v1_h60/h120/h180` as specified-only until explicit implementation patches.
- Add the missing specified-only `post_anomaly_extension_v1_h180` metadata row so docs, defaults, and registry-status output agree.
- Add strategy registry tests for the executable/specified-only split and update docs/state/ledger.

Validation:
- `git apply --check --whitespace=error methodology_cleanup_strategy_executable_horizon_status.patch`
- `python -m compileall -q main.py src tests zip_project.py`

# Patch log


## methodology: audit horizon consistency

Status: PROPOSED; patch generated against uploaded snapshot `project_20260619_111337.zip` after applying H2 v2, H3, H4 v2, H5, and H6.

Intent:
- Add explicit methodology audit checks for horizon consistency.
- Make prediction protocol audit verify the Core horizon whitelist, registry strategy/horizon compatibility, target label column, active `H_max`, purge horizon, OOS prediction artifact identity, and model metadata horizon identity.
- Add regression tests for passing horizon audit rows and FAIL rows when label column, H_max, purge, prediction, or model metadata drift.
- Keep this patch limited to audit/metadata verification; no trigger, label threshold, ML, EV, simulation, or live behavior changes.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_audit_horizon_consistency.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_horizon_contract.py tests/test_prediction.py tests/test_contracts.py` was not run in this sandbox because collection requires missing third-party dependency `polars`; run it in the project venv.


## methodology: add prediction artifact horizon identity

Status: PROPOSED; patch generated against uploaded snapshot `project_20260619_111337.zip` after applying H2 v2, H3, H4 v2, and H5.

Intent:
- Make prediction artifacts self-describing about their strategy/horizon identity.
- Add `target_label_column` and `active_h_max_minutes` to OOS prediction rows and frozen model metadata.
- Add strategy name/version/contract identity to OOS prediction rows and frozen model metadata.
- Add Core helpers that map supported horizons to strict label columns such as `scenario_30m`, rejecting arbitrary horizons before artifact construction.
- Make EV verify that prediction `target_label_column` matches the configured target horizon instead of relying only on numeric horizon filtering.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_add_prediction_artifact_horizon_identity.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_horizon_contract.py tests/test_prediction.py tests/test_decision_expected_value.py` was attempted in this sandbox but collection requires missing third-party dependency `polars`; run it in the project venv.


## methodology: align CLI horizon validation

Status: PROPOSED; patch generated against uploaded snapshot `project_20260619_111337.zip` after applying H2 v2, H3, and H4 v2.

Intent:
- Make low-level prediction/control/EV/simulation CLI commands use the Core `SUPPORTED_RESEARCH_HORIZONS` whitelist instead of local `(15, 30, 60)` choices.
- Add optional `--strategy-name` to low-level target-horizon commands so CLI can express an explicit executable strategy variant instead of hardcoding broad anomaly from the horizon.
- Preserve backward-compatible low-level defaults by resolving omitted `--strategy-name` to `broad_anomaly_v1_h{horizon}`.
- Validate the resolved strategy/horizon pair through the registry before downstream file IO, so `h32`, `broad_anomaly_v1_h180`, and specified-but-not-implemented variants fail at the boundary.
- Update README/COMMANDS examples to show explicit strategy+horizon arguments.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_align_cli_horizon_validation.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_horizon_contract.py` was attempted in this sandbox but collection requires missing third-party dependency `polars`; run it in the project venv.

## methodology: enforce registry horizon compatibility

Status: PROPOSED; patch generated against uploaded snapshot `project_20260619_111337.zip` after applying H2 v2 and H3.

Intent:
- Add registry-level `validate_strategy_horizon(strategy_name, horizon_minutes)`.
- Reject arbitrary horizons such as h11/h32 at the registry boundary.
- Reject mismatched executable pairs such as `broad_anomaly_v1_h30` with target h60.
- Reject unknown strategy names such as `broad_anomaly_v1_h180`.
- Reject specified-but-not-implemented variants such as `post_pump_distribution_v1_h120`.
- Make prediction, controls, EV, and simulation configs validate the executable strategy/horizon pair before running downstream stages.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_enforce_registry_horizon_compatibility.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_horizon_contract.py tests/test_strategy_contract.py tests/test_strategy_registry.py` was attempted in this sandbox but collection requires missing third-party dependency `polars`; run it in the project venv.


## methodology: add StrategyMetadata horizon semantics

Status: PROPOSED; patch generated against uploaded snapshot `project_20260619_111337.zip` after applying `methodology_add_core_supported_horizon_constants_v2.patch`.

Intent:
- Extend `StrategyMetadata` with semantic `allowed_horizons` and `default_horizon_minutes` while keeping `horizon_minutes` as the selected run/variant horizon.
- Validate selected/default/allowed horizons against the Core supported horizon whitelist.
- Export allowed/default horizons in strategy registry and run-config artifacts.
- Add contract tests for arbitrary horizons, selected horizon outside allowed set, and default horizon outside allowed set.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_add_strategy_metadata_horizon_semantics.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_strategy_contract.py tests/test_strategy_registry.py tests/test_horizon_contract.py` was attempted in this sandbox but collection requires missing third-party dependency `polars`; run it in the project venv.

## methodology: add Core supported horizon constants

Status: PROPOSED; patch regenerated from uploaded snapshot `project_20260619_111337.zip`; GitHub head not checked in this environment.

Intent:
- Add a single Core horizon whitelist and validator in `anomaly_science.contracts.horizons`.
- Keep `15/30/60/120/180` as the only supported research label/prediction horizons.
- Keep `5m` only as a raw future-path diagnostic horizon, not as a label/prediction horizon.
- Make labels, future config, prediction config, controls config, EV config, simulation config, and row contracts reuse the Core horizon contract instead of local literal lists.
- Add regression tests rejecting arbitrary horizons such as h11/h32.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_add_core_supported_horizon_constants_v2.patch`
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_horizon_contract.py` was attempted in this sandbox but collection requires missing third-party dependency `polars`; run it in the project venv.

## methodology: document horizon ownership contract

Status: PROPOSED; patch generated from uploaded snapshot `project_20260619_103805.zip`; GitHub head not checked in this environment.

Intent:
- Make horizon ownership explicit in Core methodology: Core supports the fixed research horizon set, Strategy selects semantic variants from that set, and Registry enforces compatibility.
- Document the current Core-supported horizon set: 15/30/60/120/180.
- Clarify that arbitrary horizons such as h11/h32 require a Core contract/schema patch and cannot be introduced through Strategy Spec or CLI alone.
- Clarify anomaly horizon validity: broad anomaly is limited to h15/h30/h60, while post-extension/post-pump h60/h120/h180 remain specified-only until implemented.
- Update the methodology gap ledger and research state without changing code, thresholds, labels, triggers, ML, EV, simulation, or live behavior.

Validation for this proposed patch:
- `git apply --check --whitespace=error /mnt/data/out/methodology_document_horizon_ownership_contract.patch`
- `python -m compileall -q main.py src tests zip_project.py`

## methodology: integrate holdout freeze into run-research

Status: APPLIED in current local branch after direct code inspection of head `db17b76a`.

Intent:
- Make compact `run-research <strategy> [--days]` create holdout governance automatically.
- Derive research start/end dates from exported `input/candles_1m.csv`.
- Write protocol freeze ledger and empty `holdout_access_log.csv` before downstream research stages read data.
- Record governance location and research date range in `research_run_summary.csv`.

Validation for this patch:
- `.venv\Scripts\python.exe -m pytest tests\test_research_run.py tests\test_holdout_governance.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m compileall src main.py tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest`

## methodology: canonicalize strategy-neutral artifacts

Status: APPLIED in current local branch after direct code inspection of head `f61c11e`.

Intent:
- Make strategy-owned run outputs write canonical `strategy_*` artifacts first.
- Keep `anomaly_*` files as compatibility aliases with the same strict schemas.
- Make `run-research` pass canonical strategy artifact paths between stages.
- Extend schema/writer tests to prevent returning to anomaly-first output.

Validation for this patch:
- `.venv\Scripts\python.exe -m pytest tests\test_artifact_schemas.py tests\test_data_audit.py tests\test_events_detector.py tests\test_state_builder.py tests\test_future_paths.py tests\test_feature_catalog.py tests\test_feature_matrix.py tests\test_atlas.py tests\test_labels.py tests\test_prediction.py tests\test_controls.py tests\test_decision_expected_value.py tests\test_trade_simulation.py tests\test_holdout_governance.py tests\test_strategy_registry.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m compileall src main.py tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest`

## strategy: add 180m horizon support

Status: APPLIED in current local branch after direct code inspection of head `eb280f4a`.

Intent:
- Add 180m future-path fields to contracts, artifact schemas, builder output, and loader boundaries.
- Add 180m descriptive outcome labels and dynamic label run-config horizon recording.
- Allow 180m target dispatch in prediction, controls, and EV/decision code.
- Keep post-pump and post-extension strategy variants marked as not implemented until their own registry patches.

Validation for this patch:
- `.venv\Scripts\python.exe -m pytest tests\test_future_paths.py tests\test_labels.py tests\test_prediction.py tests\test_controls.py tests\test_decision_expected_value.py`
- `.venv\Scripts\python.exe -m pytest tests\test_future_paths.py tests\test_labels.py tests\test_prediction.py tests\test_controls.py tests\test_decision_expected_value.py tests\test_artifact_schemas.py tests\test_contracts.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m compileall src main.py tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest`

## methodology: enforce active-horizon H_max purge

Status: APPLIED in current local branch after direct code inspection of head `be91a0ca`.

Intent:
- Remove the manual/default purge horizon assumption from prediction and controls configs.
- Compute purge as `H_max = max(horizon_minutes)` across active strategy variants in the run.
- Keep weekly walk-forward train rows constrained by `train_snapshot_time + H_max <= weekly_model_freeze_time`.
- Record the computed H_max in run config and protocol audit messages.

Validation for this patch:
- `.venv\Scripts\python.exe -m pytest tests\test_prediction.py tests\test_controls.py`
- `.venv\Scripts\python.exe -m compileall src main.py tests zip_project.py`

## refactor: quarantine legacy code and bootstrap anomaly science core

Status: APPLIED in uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Move the old repository tree into `legacy_quarantine/old_repo` as reference-only code.
- Add a clean `src/anomaly_science` bootstrap.
- Add tests preventing accidental imports from legacy roots.

Validation recorded for uploaded snapshot:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`

## feat: add data source boundary and MVP data quality gates

Status: APPLIED in uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add explicit normalized CSV data-source boundary.
- Add MVP1 data-quality checks without proxy fallbacks.
- Add point-in-time universe-by-day skeleton from dated source rows.
- Add `run-mvp1-data-audit` CLI that writes data quality, universe, protocol audit, run config, and manifest artifacts.

Validation recorded for uploaded snapshot:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit`

## feat: build MVP1 online anomaly state

Status: APPLIED in uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add strict `anomaly_events.csv` boundary.
- Build `anomaly_state_1m.csv` from closed 1m candles using only data available as-of each state time.
- Keep future paths, labels, ML, PnL, and trade simulation out of the state layer.

Validation recorded for uploaded snapshot:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/anomaly_events.csv --out tmp/mvp1_state`

## feat: build MVP1 raw future paths

Status: APPLIED in uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add strict `anomaly_state_1m.csv` boundary.
- Build `anomaly_future_paths.csv` from closed 1m candles using only candles strictly after each snapshot.
- Preserve structural-break fields as explicit nulls until structural features exist; do not proxy them from running lows.
- Keep scenario labels, ML, PnL, trade simulation, shadow live, and production live out of this layer.

Validation recorded for uploaded snapshot:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/anomaly_state_1m.csv --out tmp/mvp1_future`

## feat: add MVP1 anomaly nature atlas

Status: PROPOSED; patch generated from uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add atlas module and `run-mvp1-atlas` CLI.
- Read `anomaly_state_1m.csv` and `anomaly_future_paths.csv` through strict schema boundaries.
- Join state/future rows one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`.
- Write descriptive atlas artifacts without ML, calibrated labels, PnL, trade simulation, or decision logic.
- Keep atlas grouping bins derived from state plus feature-matrix as-of fields; use future fields only for descriptive response summaries.

Validation for this proposed patch:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/check_patch7/events`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/check_patch7/events/anomaly_events.csv --out tmp/check_patch7/state`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/check_patch7/state/anomaly_state_1m.csv --out tmp/check_patch7/future`
- `python main.py run-mvp1-feature-matrix --input tests/fixtures/minimal_market_data --state tmp/check_patch7/state/anomaly_state_1m.csv --out tmp/check_patch7/feature_matrix`
- `python main.py run-mvp1-atlas --state tmp/check_patch7/state/anomaly_state_1m.csv --future tmp/check_patch7/future/anomaly_future_paths.csv --features tmp/check_patch7/feature_matrix/anomaly_feature_matrix.csv --out tmp/check_patch7/atlas`

## feat: add MVP1 outcome label kernel

Status: PROPOSED; patch generated on top of Patch 7 applied to uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add `run-mvp1-labels` CLI.
- Build `anomaly_outcome_labels.csv` from strict `anomaly_state_1m.csv` and `anomaly_future_paths.csv` boundaries.
- Join state/future rows one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`.
- Assign descriptive future-nature scenarios for 15m, 30m, and 60m horizons from raw future path fields only.
- Keep `missing_future` explicit as a data condition, not collapsed into `unclear` or `static_or_chop`.
- Keep labels out of trading: no ML, calibrated probabilities, PnL, EV, entry/exit, trade simulation, shadow live, or production live.

Validation for this proposed patch:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/check_patch8/events`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/check_patch8/events/anomaly_events.csv --out tmp/check_patch8/state`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/check_patch8/state/anomaly_state_1m.csv --out tmp/check_patch8/future`
- `python main.py run-mvp1-labels --state tmp/check_patch8/state/anomaly_state_1m.csv --future tmp/check_patch8/future/anomaly_future_paths.csv --out tmp/check_patch8/labels`

## feat: add MVP1 walk-forward calibrated prediction

Status: PROPOSED; patch generated on top of Patch 8 applied to uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add `run-mvp1-prediction` CLI.
- Read `anomaly_state_1m.csv` and `anomaly_outcome_labels.csv` through strict schema boundaries.
- Join state/label rows one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`.
- Run daily prequential walk-forward prediction with purge: `train_snapshot_time_ms + H_max <= test_day_start_ms`.
- Use weekly CatBoost+Isotonic over state plus required feature-matrix fields; labels are used only as train targets and OOS evaluation targets.
- Write `anomaly_oos_predictions.csv`, `anomaly_calibration.csv`, and `anomaly_prediction_metrics.csv`.
- Keep this layer out of trading: no thresholds, EV, PnL, entry/exit, trade simulation, shadow live, or production live.

Validation for this proposed patch:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/check_patch9/events`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/check_patch9/events/anomaly_events.csv --out tmp/check_patch9/state`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/check_patch9/state/anomaly_state_1m.csv --out tmp/check_patch9/future`
- `python main.py run-mvp1-labels --state tmp/check_patch9/state/anomaly_state_1m.csv --future tmp/check_patch9/future/anomaly_future_paths.csv --out tmp/check_patch9/labels`
- `python main.py run-mvp1-prediction --state tmp/check_patch9/state/anomaly_state_1m.csv --labels tmp/check_patch9/labels/anomaly_outcome_labels.csv --out tmp/check_patch9/prediction`

## feat: add MVP1 placebo/control tests

Status: PROPOSED; patch generated on top of Patch 9 applied to uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add `run-mvp1-controls` CLI.
- Read `anomaly_state_1m.csv` and `anomaly_outcome_labels.csv` through strict schema boundaries reused from the prediction layer.
- Run negative placebo checks: deterministic random-label, time-shuffled-label, and symbol-shuffled-label controls.
- Run simple non-trading baselines: global-prior-only, session-only, event-time-only, and price-path-only.
- Explicitly defer `volume_only` until real volume columns exist in `anomaly_state_1m.csv`; do not proxy volume from unrelated fields.
- Write `anomaly_placebo_tests.csv` and `anomaly_baseline_comparison.csv`.
- Keep this layer out of trading: no thresholds, EV, PnL, entry/exit, trade simulation, shadow live, or production live.

Validation for this proposed patch:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/check_patch10/events`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/check_patch10/events/anomaly_events.csv --out tmp/check_patch10/state`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/check_patch10/state/anomaly_state_1m.csv --out tmp/check_patch10/future`
- `python main.py run-mvp1-labels --state tmp/check_patch10/state/anomaly_state_1m.csv --future tmp/check_patch10/future/anomaly_future_paths.csv --out tmp/check_patch10/labels`
- `python main.py run-mvp1-prediction --state tmp/check_patch10/state/anomaly_state_1m.csv --labels tmp/check_patch10/labels/anomaly_outcome_labels.csv --out tmp/check_patch10/prediction`
- `python main.py run-mvp1-controls --state tmp/check_patch10/state/anomaly_state_1m.csv --labels tmp/check_patch10/labels/anomaly_outcome_labels.csv --out tmp/check_patch10/controls`

## perf: remove Binance Vision cache per-block IO amplification

Status: PROPOSED; patch generated from uploaded snapshot `project_20260618_115220.zip`; GitHub head not checked in this environment.

Intent:
- Fix the corrupt previous patch by regenerating a clean unified diff from the current uploaded project snapshot.
- Stop rewriting the full Binance Vision block ledger on every processed block; append block records and keep last-record-wins resume semantics.
- Sample disk usage in progress output instead of recursively walking `.output/market` on every progress tick.
- Avoid expanding a missing monthly archive into daily fallback probes when the S3 archive file index already proves no daily kline archives exist.
- Keep tqdm output shorter and terminal-width aware.
- Exclude top-level `tmp/` generated run artifacts from `zip_project.py` output so project zips stay small.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_zip_project.py tests/test_binance_vision_cache.py`

## perf: prune Binance Vision cache work by requested date range

Status: PROPOSED; patch generated on top of `perf: remove Binance Vision cache per-block IO amplification`; GitHub head not checked in this environment.

Intent:
- Skip symbols whose Binance Vision kline archives do not intersect the requested cache date range.
- Reuse the preloaded archive file index during per-symbol processing instead of rebuilding it after filtering.
- Write `metadata/skipped_symbols.csv` and record skipped no-klines symbols in `manifest.json`.
- Add a `symbol_completion.csv` ledger so a completed final per-symbol parquet can safely replace block parts on subsequent runs.
- Remove `{out_dir}_parts/{symbol}` after final parquet compaction succeeds to keep SSD usage bounded.
- Preserve block-level resume while a symbol is still in progress; parts are deleted only after final parquet and completion ledger are written.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_binance_vision_cache.py tests/test_zip_project.py`


## perf: exclude Binance delivery contracts from perpetual cache

Status: PROPOSED; patch generated on top of `perf: prune Binance Vision cache work by requested date range`; GitHub head not checked in this environment.

Intent:
- Exclude Binance delivery/fixed-date symbols matching `*_YYMMDD` from the USD-M perpetual cache unconditionally.
- Keep the cache command compact: no `--include-delivery-contracts` or other delivery-specific flag is introduced.
- Record excluded delivery contracts in `metadata/skipped_symbols.csv` with `reason=delivery_contract_excluded`.
- Apply delivery filtering before date-range archive probing so old fixed-date contracts do not trigger needless S3 listing/download work.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_binance_vision_cache.py tests/test_zip_project.py`


## perf: make Binance Vision cache startup visible

Status: PROPOSED; patch generated on top of `perf: exclude Binance delivery contracts from perpetual cache`; GitHub head not checked in this environment.

Intent:
- Emit immediate startup stage logs before symbol discovery, delivery filtering, and archive range preflight.
- Show a dedicated `Binance Vision preflight` progress bar while archive file indexes are checked for requested-date overlap.
- Move compact-command network defaults to the optimized values previously used manually: `timeout=45`, `connect-timeout=8`, `retries=2`.
- Record effective network defaults in `manifest.json` for reproducibility.
- Keep the launch command compact; no new CLI flags are introduced.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_binance_vision_cache.py tests/test_binance_vision_cache_delivery_symbols.py tests/test_binance_vision_cache_startup.py tests/test_zip_project.py`

## perf: replace per-symbol Binance Vision preflight with run-level klines index

Status: PROPOSED; patch generated on top of `perf: make Binance Vision cache startup visible`; GitHub head not checked in this environment.

Intent:
- Replace the slow per-symbol archive range preflight with one run-level monthly klines index scan.
- Stop calling `load_or_build_archive_file_index()` once per symbol during preflight.
- Preload partial monthly-kline indexes for eligible symbols, using them only for kline availability.
- Keep optional metrics/liquidation datasets as direct probes when a partial index does not know those datasets.
- Prevent missing monthly klines from exploding into daily fallback probes when the run-level monthly index already proves the monthly kline archive is absent.
- Keep the compact cache command unchanged.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_binance_vision_cache.py tests/test_binance_vision_cache_delivery_symbols.py tests/test_binance_vision_cache_startup.py tests/test_zip_project.py`
## perf: replace Binance Vision root archive scan with scoped preflight

Status: PROPOSED; patch generated from uploaded snapshot `project_20260618_124949.zip`; GitHub branch `codex/pno-anomaly-continuation-lab` head checked as `e1a4e19a14a6a735a650aaec50f0f41f65da57eb`, but uploaded snapshot contains newer local changes not present at that head.

Intent:
- Stop building the run-level kline preflight by recursively listing the whole `data/futures/um/monthly/klines/` S3 tree.
- Build a scoped kline index only for requested symbols and requested monthly labels, with visible `Binance Vision archive index` progress.
- Probe current-month daily klines only for symbols that have no monthly archive in the requested window, preserving daily-only newly listed symbols without exploding probes for active symbols.
- Cache the scoped index only when its symbol/month/day scope exactly matches the current run.
- Make the top-level `main.py build-binance-vision-cache` CLI use the optimized network defaults: timeout 45s, connect-timeout 8s, retries 2.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `PYTHONPATH=. pytest -q tests/test_binance_vision_cache_startup.py tests/test_binance_vision_cache.py tests/test_binance_vision_cache_delivery_symbols.py tests/test_zip_project.py`
- Full `PYTHONPATH=. pytest -q` was attempted, but this container lacks `pyarrow`/`fastparquet`, so `tests/test_cache_export.py::test_export_cache_to_mvp1_csv_writes_explicit_boundary` fails before exercising this patch.
- GitHub combined status for `e1a4e19a14a6a735a650aaec50f0f41f65da57eb`: no status checks returned.

## methodology: add implementation gap ledger

Status: APPLIED.

Intent:
- Add `research/METHODOLOGY_GAP_LEDGER.md` as the explicit source of truth for what is implemented, partial, missing, or out of scope in the offline research methodology.
- Define the "100% without live" completion criteria without including shadow live, production live, exchange execution, or portfolio infrastructure.
- Record Core methodology gaps and anomaly strategy gaps separately so future patches do not mix strategy-specific work into Core.
- Update README and research state so completeness claims must reference the ledger.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`


## strategy: complete broad anomaly trigger component accounting

Status: APPLIED.

Changes:
- Adds strict `trigger_component` and `trigger_components` fields to `AnomalyEvent` and the canonical/alias events artifact schema.
- Persists deterministic causal component tags for the current broad detector: `one_shot_spike`, `range_expansion`, `quote_volume_spike`, `base_volume_spike`, `trade_count_spike`, and derived `volume_only_anomaly`.
- Carries component tags through the BaseStrategy trigger frame and state artifact loader boundary.
- Adds tests for one-shot, volume-only, artifact persistence, trigger-frame exposure, and schema/header updates.

Validation in this environment:
- `python -m compileall src main.py tests zip_project.py`
- Targeted pytest collection is blocked here by missing runtime dependency `polars`.
## features: add structural state and relaxed geometry features

Status: APPLIED.

Changes:
- Adds causal confirmed structural high/low levels and timestamps to `AnomalyState1mRow` and the canonical/alias state artifact schema.
- Computes structural levels only from closed as-of event-window candles using left/right local swing confirmation; missing structure remains null instead of being proxied from running high/low.
- Adds the required relaxed anomaly geometry fields to `AnomalyFeatureMatrixRow`, `strategy_feature_matrix.csv`, and `anomaly_feature_matrix.csv`.
- Catalogs raw shelf levels as audit-only and exposes ATR-normalized / percentile / relative geometry coordinates as model features.
- Adds regression tests for structural no-leakage timing, feature matrix materialization, artifact schema roundtrip, and catalog declaration.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- `pytest -q tests/test_state_builder.py tests/test_feature_matrix.py tests/test_artifact_schemas.py -k 'not run_mvp1_state_cli_writes_state_artifacts and not run_mvp1_feature_matrix_writes_artifacts'`
- `pytest -q tests/test_feature_catalog.py -k 'not run_mvp1_features_writes_catalog_and_audit'`

Full CLI/audit pytest still needs the project venv because this sandbox lacks `polars`.

## strategy: implement post-pump distribution variants

Status: APPLIED.

Changes:
- Makes `post_pump_distribution_v1_h60/h120/h180` executable registry variants.
- Adds `PostPumpDistributionStrategy` with causal trigger semantics: `daily_return_asof_t > 0.30`, same-minute `trade_count_market_percentile_asof_t > 0.99`, and one trigger per symbol/UTC-day.
- Adds explicit post-pump audit fields to the canonical/alias events artifact schema and loader boundary.
- Routes `run-mvp1-events` and `run-research` through the selected registry strategy instead of hard-coding broad anomaly.
- Keeps OI and liquidations required for post-pump variants before trigger generation.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- Full pytest still needs the project venv because this sandbox lacks `polars`.

## strategy: implement post-anomaly extension variants

Status: APPLIED.

Changes:
- Makes `post_anomaly_extension_v1_h60/h120/h180` executable registry variants.
- Adds `PostAnomalyExtensionStrategy` with causal late-extension semantics: source broad anomaly seed from closed candles, direction-aware as-of extension from seed open, and only the first qualifying extension row per source event.
- Keeps `event_start_time` anchored to the source broad anomaly and writes the actual late extension trigger as `state_time` / `event_detection_time`.
- Requires OI and liquidations before trigger generation for post-extension variants.
- Updates registry/status/docs/ledger/tests.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- Full pytest still needs the project venv because this sandbox may lack project runtime dependencies.

## methodology: add artifact-driven rejection funnel

Status: APPLIED.

Changes:
- Adds canonical `strategy_rejection_funnel.csv` with `anomaly_rejection_funnel.csv` alias.
- Builds the funnel from already-written artifacts after simulation inside `run-research`, without rerunning strategy logic, models, or thresholds.
- Records included/excluded lineage across data quality, point-in-time universe, events, state, future paths, labels, prediction, decision, and simulation.
- Requires explicit `reason_code` for excluded rows and adds independent forensic completeness checks.
- Updates methodology ledger/docs/tests.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- `pytest -q tests/test_rejection_funnel.py tests/test_artifact_schemas.py tests/test_forensic_audit.py`

Full pytest still needs the project venv because this sandbox may lack project runtime dependencies.


## methodology: add calibration breakdown audit

Status: APPLIED.

Changes:
- Adds canonical `strategy_calibration_breakdown.csv` with `anomaly_calibration_breakdown.csv` alias.
- Writes OOS calibration reliability slices by UTC session, ISO week, UTC month, symbol, systemic cluster regime, market-shock group, alpha-decay bucket, and minutes-since-trigger bucket.
- Computes row count, mean confidence, empirical accuracy, multiclass Brier, log loss, and expected calibration error for every slice.
- Adds independent forensic completeness checks so research interpretation fails when required calibration breakdowns are absent or malformed.
- Updates schema/docs/ledger/tests without changing model fitting, calibration method, thresholds, EV, or simulation logic.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- `pytest -q tests/test_artifact_schemas.py`

Prediction/forensic pytest collection still needs the project venv because this sandbox lacks `polars`.

## methodology: expand atlas multi-horizon geometry slices

Status: APPLIED.

Changes:
- Expands atlas output from fixed 30m to configured 15/30/60/120/180m ATR-normalized descriptive outcomes.
- Adds explicit `outcome_horizon_minutes` to context split and market-shock atlas artifacts.
- Adds session, speed, market-context, and relaxed shelf/sweep/consolidation geometry contexts.
- Adds geometry response surfaces without feeding atlas output into prediction, EV, decision, or simulation.
- Updates methodology/spec/docs/ledger/tests.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- `pytest -q tests/test_atlas.py tests/test_artifact_schemas.py -k 'not cli'`

Full CLI pytest still needs the project venv because this sandbox lacks `polars`.
