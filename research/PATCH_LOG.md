# Patch log

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

Status: PROPOSED

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

Status: PROPOSED; patch generated from uploaded snapshot `project_20260619_084329.zip`; GitHub head was not available inside the uploaded archive, so this patch is against the snapshot contents only.

Intent:
- Add `research/METHODOLOGY_GAP_LEDGER.md` as the explicit source of truth for what is implemented, partial, missing, or out of scope in the offline research methodology.
- Define the "100% without live" completion criteria without including shadow live, production live, exchange execution, or portfolio infrastructure.
- Record Core methodology gaps and anomaly strategy gaps separately so future patches do not mix strategy-specific work into Core.
- Update README and research state so completeness claims must reference the ledger.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
