# Patch log

## prediction: add horizon-free binary weekly walk-forward core

Status: APPLIED_IN_CURRENT_TREE; FULL_DATA_DEVELOPMENT_RUN_COMPLETE_NEGATIVE.

Changes:
- Adds a generic typed binary probability Core with strict causal input validation.
- Uses actual offline label resolution (`resolution_time < weekly freeze`) instead of a fixed-horizon purge for horizon-free targets.
- Uses chronological event-group-exclusive 60/20/20 fit, validation, and isotonic-calibration splits; test-week groups are excluded from train.
- Freezes one CatBoost model and one isotonic calibrator per ISO week, with no intra-week update and no unsupported-split fallback.
- Writes OOS probabilities, frozen weekly baselines, model/calibrator files, metrics, reliability, Wilson bounds, within-week permutation controls, temporal audits, pre-registered gates, metadata, and manifest.
- Registers pump-fade t0 features, population, dates, model settings, and twelve gates in `research/pump_fade_t0_probability.json`.

Validation:
- `python -m compileall -q main.py src tests zip_project.py`
- `PYTHONPATH=src pytest -q`: 347 passed in 270.08s after archived positioning ingestion/as-of additions.
- `git diff --check`: passed (line-ending conversion warnings only).

Runtime/science follow-up:
- Vectorized segment-tree construction and contiguous-block boundaries, replaced repeated prefix medians with an exact incremental sorted median, and used exact-equal Polars rolling medians for finite blocks.
- A real full-symbol baseline/optimized comparison was exact for every state/label value and dtype; wall time fell from about 13.0s to 6.8s for that symbol. The 796-symbol full build then completed in 13m56s.
- Added a registered causal new-high state lattice and separate ordinal probability family with Bonferroni-controlled permutation gates.
- Added regularized equal-support-bin Beta-shrunk isotonic V2 after direct isotonic V1 exposed unsupported endpoint probabilities.
- T0 and every state ordinal failed the registered high-probability gates; details are in `EXPERIMENT_LOG.md`.
- Added a generic exact-population paired OOS comparator with ISO-week block bootstrap, cluster sign-flips, minimum incremental effects, and familywise gates.
- Ran a strategy-owned nine-feature OI increment on t0/ordinals 1-2. All incremental CI crossed zero and every paired gate family failed; OI is not admitted to the production probability model.
- Added generic Core BTC/ETH point-in-time context artifacts with exact closed-minute joins and future-tail invariance tests. A 24-feature paired increment on t0/ordinals 1-2 failed all incremental/high-probability families and is not admitted.
- Added cached daily Binance reference-metrics ingestion with explicit coverage, +5m publication lag, backward as-of positioning joins, and 38 registered log-ratio/change/spread coordinates. All 766 BTC/ETH archives were acquired, but the paired positioning family failed and is excluded.

## science: harden pump-fade evidence protocol

Status: APPLIED_IN_CURRENT_TREE_AFTER_RUNTIME_COMPLETION.

Intent:
- Make research evidence governance explicit: `EXPERIMENT_LOG.md` is evidence, `RESEARCH_STATE.md` is summary, `METHODOLOGY_GAP_LEDGER.md` is platform capability, and `PATCH_LOG.md` is patch state.
- Treat future-only pump-fade fields as registered `label_only_columns` and forbid label/schema/filter/label-only columns as archetype model features.
- Replace pump-fade magic missing sentinels with `NaN` plus explicit availability/context flags.
- Split prediction train/validation/calibration by whole `(symbol,event_id)` groups, not rows.
- Use one bounded runtime thread-count source for CatBoost; no CLI thread-count overrides.
- Make OI paired runs write non-evidential `FAILED_PARTIAL` summaries instead of crashing after partial artifacts.
- Recompute random/matched-control RR acceptability after re-anchoring.
- Limit simulation candle/funding grouping to eligible decision symbols for i5/16GB memory discipline.
- Add class-wise calibrated probability Brier/ECE metrics.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- `PYTHONPATH=src pytest -q tests/test_runtime.py`
- Full CLI/import pytest still needs the project venv because this sandbox lacks `polars`.

## decision: make RR acceptability side-specific

Status: APPLIED_IN_CURRENT_TREE.

Changes:
- Adds side-specific decision timing fields: `RR_long_acceptable`, `RR_short_acceptable`, `selected_RR`, and `selected_RR_acceptable`.
- Keeps `is_RR_still_acceptable` only as a legacy alias for any side passing min RR, not as the simulation gate.
- Makes trade simulation require the RR gate for the selected side: long trades require `RR_long_acceptable`, short trades require `RR_short_acceptable`.
- Adds regression tests proving that opposite-side RR cannot make a selected long/short trade simulatable.
- Updates the Core methodology to document the side-specific RR invariant.

Validation in this environment:
- `python -m compileall -q main.py src tests zip_project.py`
- Focused pytest collection still needs the project venv because this sandbox lacks `polars`.

## audit: separate smoke and evidence forensic gates

Status: PROPOSED.

Intent:
- Add an explicit `--forensic-evidence-mode smoke|development|evidence` gate for `run-research`.
- Keep `FAIL` as a hard run blocker in every mode.
- Allow `WARN` to complete smoke/development runs while marking artifacts non-evidential, and block evidence mode on any `WARN`.
- Persist forensic evidence mode/status/claim eligibility in `research_run_summary.csv`, `strategy_run_config.csv`, aliases, and reproducibility config.

Validation:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest tests/test_forensic_audit.py tests/test_research_run.py -q`

## pump-fade: require finite registered OI model features

Status: APPLIED_IN_CURRENT_TREE.

Intent:
- Split raw OI stream availability from registered OI model-feature availability in paired pump-fade OI experiments.
- Require `oi_available=true` plus finite values for every registered `PUMP_FADE_OI_MODEL_FEATURES` column before either baseline or OI arm is trained.
- Write a `FAILED_PARTIAL` summary if one paired arm fails, so a successful baseline arm cannot be interpreted as evidence.

Validation:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest tests/test_archetype_discovery.py -q`

## research: make experiment ledger the evidence source of truth

Status: APPLIED_IN_CURRENT_TREE.

Intent:
- Replace the stale empty experiment log with explicit development/smoke/failed-partial records for the current pump-fade and OI work.
- Make `research/EXPERIMENT_LOG.md` authoritative for evidence status, with `research/RESEARCH_STATE.md` limited to summary.
- Prevent the 30-symbol OI smoke, full failed-partial OI run, and discovered archetype thresholds from being read as tradeable edge evidence.

Validation:
- `python -m compileall -q main.py src tests zip_project.py`

## architecture: add dataset store phase cache v1

Status: PROPOSED.

Intent:
- Add an explicit `build-research-dataset` command that materializes reusable research dataset phases behind an explicit `--max-phase` boundary.
- Start with safe dataset phases only: input, data_audit, events, state, future, feature_catalog, and feature_matrix. Prediction, controls, EV, simulation, and metrics remain experiment outputs.
- Preserve `run-research` behavior; this patch only creates the reusable dataset-store contract needed before `run-research --dataset`.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src tests main.py`
- `.venv\Scripts\python.exe -m pytest tests\test_research_dataset_store.py tests\test_cli_contract.py -q`

## perf: stream feature cross-section metrics

Status: APPLIED.

Intent:
- Replace per-snapshot SQLite `SELECT` calls in feature cross-section materialization with one ordered cursor grouped by `snapshot_time_ms`.
- Preserve point-in-time cross-section semantics and the same temporary SQLite storage boundary.
- Target the remaining 9d `feature_matrix` bottleneck that persisted after per-row CVD/BTC helper optimizations.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\features tests\test_feature_matrix.py`
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py -q`
- `.venv\Scripts\python.exe -m pytest -q`

## perf: index feature CVD and BTC correlation

Status: APPLIED.

Intent:
- Add `_CandleSeries` prefix indexes for event/window CVD and one-minute return correlations.
- Replace per-row event CVD scans, small CVD window slices, and BTC correlation return-list construction with indexed point-in-time queries.
- Keep generic fallback behavior for non-indexed sequences.
- Preserve feature semantics: all inputs remain as-of `snapshot_time_ms`.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\features tests\test_feature_matrix.py`
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py -q`
- Synthetic `_build_state_feature_row` profile with BTC context improved from about `8.1s` to `2.9s` for `9059` rows.
- `.venv\Scripts\python.exe -m pytest -q`
- 9d all-symbol `run-research broad_anomaly_v1_h30 --days 9` diagnostic still had `feature_matrix` running after more than `1800s` following `feature_catalog`; the run was stopped and deleted. The next bottleneck is likely outside the optimized per-row CVD/BTC context helpers, such as cross-section materialization, CSV parsing, or artifact writing.

## perf: avoid feature candle history copies

Status: APPLIED.

Intent:
- Stop `_price_speed_atr` from copying the full as-of candle history for every indexed feature row.
- Add direct `_CandleSeries` previous/current lookup and event quote-volume prefix-sum helpers.
- Use the prefix-sum helper for liquidation cumulative intensity denominator.
- Preserve point-in-time feature semantics and generic Sequence fallbacks.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\features tests\test_feature_matrix.py`
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py -q`
- `.venv\Scripts\python.exe -m pytest -q`

## perf: speed indexed feature market context

Status: APPLIED.

Intent:
- Add indexed `_CandleSeries` helpers for window returns and one-minute return windows.
- Use those helpers in BTC-relative return and BTC-correlation feature hot paths instead of per-row dict/set/sorted alignment when both series are already indexed.
- Preserve point-in-time market-context semantics and the existing generic Sequence fallback.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\features tests\test_feature_matrix.py`
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py -q`
- `.venv\Scripts\python.exe -m pytest -q`

## perf: lazy future barrier indexes

Status: APPLIED.

Intent:
- Stop building double-barrier range indexes eagerly for every symbol in `future`.
- Build the barrier index lazily only after a cheap horizon range precheck proves a same-candle double-barrier hit is possible.
- Replace generic sparse-table build/query callbacks with specialized max/min sparse helpers for future high/low windows.
- Preserve raw future-path semantics, strict time contract, ATR handling, pessimistic double-barrier resolution, and canonical CSV schemas.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py`
- `.venv\Scripts\python.exe -m pytest tests\test_future_paths.py -q`
- `.venv\Scripts\python.exe -m pytest -q`
- 9d all-symbol `run-research broad_anomaly_v1_h30 --days 9` diagnostic reached `future` PASS in `1370.058049s`; the run was stopped after `feature_catalog` and deleted after recording timings.

## perf: slim future CSV hot path parsing

Status: APPLIED.

Intent:
- Make the direct grouped-CSV `future` path read `strategy_state_1m.csv` through a typed `_FutureStateProjection` containing only the fields consumed by future-path construction.
- Keep exact state artifact schema validation, explicit temporal checks, positive price checks, canonical output schemas, and raw future-path semantics unchanged.
- Remove `pandas.isna` from the direct CSV hot path for future candles and projected state rows.
- Target the remaining traced 9d `future` bottleneck after range indexing did not show an immediate enough speedup in a stopped diagnostic run.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py`
- `.venv\Scripts\python.exe -m pytest tests\test_future_paths.py -q`
- `.venv\Scripts\python.exe -m pytest -q`

## perf: index future horizon queries

Status: APPLIED.

Intent:
- Replace per-state scanning of up-to-180 future candles with per-symbol range indexes for future high/low, exact horizon close, first new high, and double-barrier existence checks.
- Keep raw future-path semantics, strict time contract, ATR handling, and canonical CSV schemas unchanged.
- Target the active traced bottleneck where 9d `future` still took about `2059s` after direct CSV generation.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py`
- `.venv\Scripts\python.exe -m pytest tests/test_future_paths.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_future_paths.py tests/test_labels.py tests/test_atlas.py tests/test_research_run.py tests/test_artifact_schemas.py -q`

## perf: stream future CSV values directly

Status: APPLIED.

Intent:
- Add a direct grouped-CSV future generator for `run-mvp1-future` that emits canonical CSV value rows without allocating `FuturePathRow` objects on the multi-million-row CLI path.
- Keep the existing typed `FuturePathRow` API for in-memory builders, loaders, and tests.
- Add a regression test proving direct CSV values match typed builder serialization on the same fixture.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py tests\test_research_run.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m pytest tests/test_future_paths.py tests/test_research_run.py tests/test_artifact_schemas.py -q`

## perf: write large stage rows without per-row dicts

Status: APPLIED.

Intent:
- Replace `csv.DictWriter` + per-row dictionaries with `csv.writer` + direct slot value lists for state, labels, feature matrix, and EV decision timing writers.
- Keep strict schema validation before writing, canonical column order, and hardlink-first aliases unchanged.
- Reduce Python allocation overhead on multi-million-row artifacts across the single-command research pipeline.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\state src\anomaly_science\labels src\anomaly_science\features src\anomaly_science\decision src\anomaly_science\future tests\test_state_builder.py tests\test_labels.py tests\test_feature_matrix.py tests\test_decision_expected_value.py`
- `.venv\Scripts\python.exe -m pytest tests/test_state_builder.py tests/test_labels.py tests/test_feature_matrix.py tests/test_decision_expected_value.py tests/test_future_paths.py tests/test_research_run.py tests/test_artifact_schemas.py -q`

## perf: write future rows without per-row dicts

Status: APPLIED.

Intent:
- Replace `csv.DictWriter` + per-row future artifact dictionaries with `csv.writer` + direct slot value lists for `strategy_future_paths.csv`.
- Preserve strict schema validation, canonical column order, hardlink-first aliasing, and all future-path semantics.
- Reduce the hottest remaining overhead in the 9d traced run where `future` took about `3092s`.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py tests\test_research_run.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m pytest tests/test_future_paths.py tests/test_research_run.py tests/test_artifact_schemas.py -q`

## perf: reduce future row allocation pressure

Status: APPLIED.

Intent:
- Stop `_build_state_future_path` from allocating a per-row tuple slice of up-to-180 future candles.
- Pass future-window start/end indexes into `_future_metrics` and scan the existing per-symbol candle tuple in place.
- Replace per-row horizon metric dictionaries with fixed-position tuple metrics for the canonical 5/15/30/60/120/180m horizon set.
- Preserve future path semantics, ATR handling, barrier logic, and row schemas.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m pytest tests/test_future_paths.py tests/test_research_run.py -q`

## perf: serialize EV decision rows directly

Status: APPLIED.

Intent:
- Write large `strategy_decision_timing.csv` artifacts from `ExpectedValueRow` slots directly after one schema-field validation.
- Avoid building a second full `asdict()` payload list for multi-million-row EV outputs.
- Preserve hardlink-first `anomaly_decision_timing.csv` alias behavior and EV semantics.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\decision tests\test_decision_expected_value.py tests\test_trade_simulation.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m pytest tests/test_decision_expected_value.py tests/test_trade_simulation.py tests/test_research_run.py -q`

## perf: reuse simulation inputs inside runner

Status: APPLIED.

Intent:
- Stop `run-mvp1-trade-simulation` from reading `candles_1m.csv` twice and decision timing three times.
- Normalize candles, funding rates, and decision rows once in the runner, then pass the same typed inputs to base simulation and random-entry control builders.
- Keep simulation semantics, pessimistic fills, costs, controls, and audit rows unchanged.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\simulation tests\test_trade_simulation.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m pytest tests/test_trade_simulation.py tests/test_research_run.py -q`

## perf: stream EV and simulation artifact loaders

Status: APPLIED.

Intent:
- Replace `pandas.read_csv` + `iterrows()` in EV/simulation artifact loaders with strict `csv.DictReader` streaming.
- Preserve exact schema-boundary failures and row-index error context while avoiding full-frame materialization for large decision/simulation artifacts.
- Remove the pandas import from those builder modules; no strategy, EV, or simulation semantics changed.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\decision src\anomaly_science\simulation tests\test_decision_expected_value.py tests\test_trade_simulation.py`
- `.venv\Scripts\python.exe -m pytest tests/test_decision_expected_value.py tests/test_trade_simulation.py -q`

## perf: trace run-research stage timings

Status: APPLIED.

Intent:
- Add root `strategy_stage_timings.csv` with `anomaly_stage_timings.csv` as a hardlink-first compatibility alias.
- Write timing rows incrementally after each `run-research` stage, including FAIL rows before re-raising exceptions.
- Keep the next long proof diagnosable without changing strategy, methodology, labels, EV, or simulation behavior.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\research src\anomaly_science\contracts tests\test_research_run.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m pytest tests/test_artifact_schemas.py tests/test_research_run.py -q`

## perf: bound feature-matrix cross-section cache

Status: APPLIED.

Intent:
- Keep `strategy_state_1m.csv` and final `strategy_feature_matrix.csv` in the same row order for downstream row-aligned atlas/labels/prediction stages.
- Move cross-section rank materialization out of an unbounded in-memory dict and into a temporary SQLite lookup owned by `run-mvp1-feature-matrix`.
- Stream CLI feature rows by grouped symbol CSV boundaries, holding only the current symbol candle series plus BTC context instead of the full all-symbol 1m market in Python objects.
- Serialize feature rows directly from dataclass slots after one schema validation instead of running `dataclasses.asdict()` and set checks for every multi-million-row output row.
- Delete the temporary lookup before the feature stage exits and preserve strict artifact parsing plus Core/strategy separation.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src tests`
- `.venv\Scripts\python.exe -m pytest tests/test_feature_matrix.py`
- `.venv\Scripts\python.exe -m pytest`
- `.venv\Scripts\python.exe main.py run-research broad_anomaly_v1_h30 --days 2` completed with forensic `11` PASS / `8` WARN / `0` FAIL rows; local proof artifacts were deleted after validation.

## perf: serialize future rows directly

Status: APPLIED.

Intent:
- Reduce `run-mvp1-future` runtime on multi-million-row outputs by removing per-row `dataclasses.asdict()` and schema-set checks from `strategy_future_paths.csv` writing.
- Validate future artifact schema columns once, map CSV columns to `FuturePathRow` slots once, and serialize rows directly during streaming write.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m pytest tests/test_future_paths.py tests/test_artifact_schemas.py`

## perf: serialize state and label rows directly

Status: APPLIED.

Intent:
- Reduce `run-mvp1-state` and `run-mvp1-labels` runtime on multi-million-row outputs by removing per-row `dataclasses.asdict()` and schema-set checks from canonical artifact writers.
- Validate state/label artifact schema columns once, map CSV columns to dataclass slots once, and serialize rows directly during streaming writes.
- Stream state rows out of each event builder instead of accumulating a per-event list before yielding rows to the canonical writer.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\state src\anomaly_science\labels tests\test_state_builder.py tests\test_labels.py`
- `.venv\Scripts\python.exe -m pytest tests/test_state_builder.py tests/test_labels.py tests/test_artifact_schemas.py`

## perf: release run-research stage memory

Status: APPLIED.

Intent:
- Add an explicit memory barrier between `run-research` stages so one long-lived Python process does not carry large temporary stage structures into later stages.
- Keep the single-command pipeline unchanged; strategy/methodology/simulation boundaries are unchanged.
- Reduce retained memory pressure after cache export, data audit, events, state, future, feature matrix, atlas, labels, prediction, controls, EV, simulation, and rejection funnel.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\research tests\test_research_run.py`
- `.venv\Scripts\python.exe -m pytest tests\test_research_run.py -q`

## perf: stream feature-matrix state input

Status: APPLIED.

Intent:
- Stop `run-mvp1-feature-matrix` from materializing full `strategy_state_1m.csv` as a tuple before writing features.
- Build a compact per-snapshot state summary for cross-section fields, then stream state rows from CSV during feature writing.
- Keep feature semantics unchanged: cross-section return/event-alive context still uses only as-of state rows.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\features tests\test_feature_matrix.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py tests\test_research_run.py -q`

## perf: stream forensic alias comparison

Status: APPLIED.

Intent:
- Stop independent forensic alias consistency checks from materializing both sides of large CSV alias pairs.
- Use a same-file hardlink fast path and stream row comparison only when aliases are separate files.
- Keep forensic semantics unchanged: header drift, row-count mismatch, and first row mismatch still fail explicitly.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_forensic_audit.py tests\test_artifact_schemas.py -q`

## perf: compact rejection funnel for large research runs

Status: APPLIED.

Intent:
- Stop `strategy_rejection_funnel.csv` from duplicating every included `state/future/labels/prediction/decision/simulation` row as a second large artifact.
- Keep explicit per-stage lineage by writing stage summary rows with `row_count` and explicit aggregate exclusion reasons.
- Preserve forensic requirements: every required funnel stage is present, excluded rows always carry `reason_code`, and summary rows are auditable.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_rejection_funnel.py tests\test_forensic_audit.py -q`

## perf: stream future stage by symbol

Status: APPLIED.

Intent:
- Stop `run-mvp1-future` from building a full-market candle index before writing future rows.
- Stream grouped `candles_1m.csv` and grouped `strategy_state_1m.csv` one symbol at a time while preserving row order inside each state group.
- Keep future semantics unchanged: labels still use only candles strictly after `snapshot_time_ms`, and ATR is computed from closed as-of history.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future tests\test_future_paths.py`
- `.venv\Scripts\python.exe -m pytest tests\test_future_paths.py -q`

## perf: avoid duplicate events trigger-frame conversions

Status: APPLIED.

Intent:
- Add a strategy-owned pandas trigger boundary so Core can reuse the already-loaded event input frame without pandas->polars->pandas roundtrips.
- Release source frame references after data-quality/universe checks before trigger generation.
- Keep Core/strategy separation: Core calls a BaseStrategy method; concrete strategies own their trigger conversion path.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\strategy src\anomaly_science\events tests\test_events_detector.py`
- `.venv\Scripts\python.exe -m pytest tests\test_events_detector.py tests\test_strategy_contract.py -q`

## perf: stream state-stage CSV input boundary

Status: APPLIED.

Intent:
- Stop `run-mvp1-state` from loading the full `candles_1m.csv` input into a pandas DataFrame before state writing.
- Parse `strategy_events.csv` through a strict streaming CSV boundary instead of a pandas frame.
- Process state candles by explicit symbol groups so the CLI keeps only one symbol's 1m candle history in memory instead of building a full-market candle index.
- Keep state semantics unchanged: state rows remain causal and use only candles with `available_time_ms <= state_time_ms`.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\state src\anomaly_science\research\run.py tests\test_state_builder.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m pytest tests\test_state_builder.py tests\test_research_run.py -q`

## perf: stream run-research holdout input filtering

Status: APPLIED.

Intent:
- Make `run-research` holdout-lock filtering stream input CSV rows instead of loading full exported input artifacts into pandas DataFrames.
- Make research date-range and summary time-bound scans streaming as well.
- Keep the same governance contract: default IS mode still removes final holdout rows before downstream stages; frozen holdout remains explicit.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\research\run.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m pytest tests\test_research_run.py -q`

## perf: stream strict CSV artifact writer

Status: APPLIED.

Intent:
- Make the shared strict CSV artifact writer validate and write rows incrementally instead of normalizing the full input into memory first.
- Keep strict schema behavior unchanged: missing and extra columns still fail with row index context.
- Keep compatibility aliases hardlink-first when schemas are identical; materialize rows only if a future non-identical alias schema requires a second pass.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\artifacts\writer.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m pytest tests\test_artifact_schemas.py -q`

## perf: index feature-matrix liquidation windows

Status: APPLIED.

Intent:
- Remove repeated full-list liquidation scans from feature-matrix per-state rows and cross-section liquidation percentiles.
- Keep the same causal contract: liquidation rows are usable only when `available_time_ms <= snapshot_time_ms`, and event windows stay bounded by `event_time_ms`.
- Keep the change inside Core feature infrastructure; strategy thresholds, labels, training, EV, and simulation are unchanged.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py -q`

## perf: hardlink artifact aliases and chunk atlas inputs

Status: APPLIED.

Intent:
- Stop duplicating large canonical/compatibility CSV artifacts when the schemas are identical: `anomaly_*` aliases are now hardlink-first and copy-only fallback.
- Keep canonical `strategy_*` artifacts as the Core contract; aliases remain compatibility files and do not change methodology semantics.
- Reduce atlas input pressure by reading strict state/future/feature CSV boundaries in bounded chunks and enforcing row alignment per chunk.
- Clean generated run artifacts after large local proofs: `.output/results` and `tmp/*` were removed; `.output/market` cache was preserved as source data.

Validation:
- `.venv\Scripts\python.exe -m compileall -q main.py src tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest -q`
- Targeted hardlink regression: identical `strategy_protocol_audit.csv` / `anomaly_protocol_audit.csv` aliases share the same filesystem file when hardlinks are supported.
- Disk after cleanup: C: free space recovered from about `1.5GB` to about `91.8GB`; remaining large project data is the preserved `.output\market` source cache.
- Atlas 9d proof is not yet claimed after cleanup; downstream 9d atlas/prediction/EV/simulation remains the next validation target.

## perf: stream 9d labels artifact generation

Status: APPLIED.

Intent:
- Remove the 9d labels bottleneck caused by materializing all state/future inputs and label rows in memory before writing.
- Keep labels inside the Core artifact/methodology layer: no strategy thresholds, training, EV, simulation, or live behavior changed.
- Parse only the labels-owned strict artifact fields: state is used for join/temporal checks, future paths are used for ATR-normalized scenario labels.
- Preserve canonical `strategy_outcome_labels.csv` as primary and copy identical `anomaly_outcome_labels.csv` only as compatibility alias.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future\builder.py src\anomaly_science\future\__init__.py src\anomaly_science\labels\builder.py src\anomaly_science\labels\run.py src\anomaly_science\labels\__init__.py tests\test_labels.py`
- `.venv\Scripts\python.exe -m pytest tests\test_labels.py tests\test_future_paths.py tests\test_artifact_schemas.py -q`
- First `100,000` streaming label input rows plus label assignment completed in `12.65s` (`~7,903.7 rows/s`).
- `.venv\Scripts\python.exe main.py run-mvp1-labels --state tmp\state_9d_streaming_perf_h30\strategy_state_1m.csv --future tmp\future_9d_streaming_state_h30_final\strategy_future_paths.csv --out tmp\labels_9d_hotpath_final` completed in about `25m23s`, wrote `7,308,031` `strategy_outcome_labels.csv` rows, and stage audit had `12` PASS / `0` FAIL rows.

## perf: index feature matrix as-of hot paths

Status: APPLIED.

Intent:
- Keep feature-matrix computation inside Core artifact/feature infrastructure without changing strategy, training, EV, or simulation contracts.
- Replace repeated per-row Open Interest filtering/sorting with an explicit as-of `OpenInterest5m` series index.
- Replace repeated rolling volume/quote-volume sample standard deviation over 1440 candles with exact prefix-sum moments.
- Preserve exact feature semantics: no fallback, no threshold changes, no strategy-specific branches.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py -q`
- 9d probe improved first `100,000` feature rows from about `954.64s` to about `716.42s` total elapsed, including unchanged startup state/candle indexing.
- After exact rolling median caching, `.venv\Scripts\python.exe main.py run-mvp1-feature-matrix --input .output\results\research_runs\20260620T134600533596Z_broad_anomaly_v1_h30\input --state tmp\state_9d_streaming_perf_h30\strategy_state_1m.csv --out tmp\feature_matrix_9d_hotpath_median_final` completed in about `2h05m`, wrote `7,308,031` `strategy_feature_matrix.csv` rows, and stage audit had `13` PASS / `0` FAIL rows.

## perf: stream feature matrix artifact writes

Status: APPLIED.

Intent:
- Remove the extra multi-million-row `strategy_feature_matrix.csv` list materialization before artifact writing.
- Keep feature computation inside Core artifact infrastructure; strategy definitions, training methodology, EV, and simulation black-box contracts are unchanged.
- Reuse the strict direct CSV candle/state iterators for the feature-matrix CLI boundary.
- Preserve canonical `strategy_feature_matrix.csv` as primary and copy identical `anomaly_feature_matrix.csv` only as compatibility alias.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_feature_matrix.py tests\test_future_paths.py -q`
- 9d probe wrote `1,486,861` feature rows before manual stop, proving streaming output works; full 9d feature-matrix runtime is still a bottleneck and remains the next optimization target.

## perf: stream state and future artifacts for compact proofs

Status: APPLIED.

Intent:
- Keep compact all-symbol research executable without materializing multi-million-row state/future artifacts as Python lists before writing.
- Stream `strategy_state_1m.csv` and `strategy_future_paths.csv` through strict canonical schemas, then copy identical `anomaly_*` aliases for compatibility.
- Treat zero ATR from flat as-of history as explicit missing ATR fields instead of crashing or falling back to a proxy value.
- Finish canonical audit payload cleanup so default protocol/data-quality rows name `strategy_*` primary artifacts.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_future_paths.py tests\test_state_builder.py tests\test_contracts.py tests\test_data_audit.py tests\test_events_detector.py -q`
- `.venv\Scripts\python.exe main.py run-mvp1-future --input .output\results\research_runs\20260620T134600533596Z_broad_anomaly_v1_h30\input --state tmp\state_9d_streaming_perf_h30\strategy_state_1m.csv --out tmp\future_9d_streaming_state_h30_final` completed in about `58m`.
- The 9d future proof wrote `7,308,031` `strategy_future_paths.csv` rows and `strategy_protocol_audit.csv` had `11` PASS / `0` FAIL rows.

## methodology: keep WFA proof rows in short IS runs

Status: APPLIED.

Intent:
- Keep `run-research <strategy> --days N` compact without requiring extra holdout flags for short proof runs.
- Preserve at least one final holdout day while keeping up to 8 non-holdout research days when the exported window allows it.
- Reduce short-window forensic WARNs caused by empty OOS/EV/simulation proof rows.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_research_run.py -q`

## audit: name canonical artifacts in protocol rows

Status: APPLIED.

Intent:
- Align stage protocol audits with the documented canonical artifact boundary: `strategy_*` is primary, `anomaly_*` is only a compatibility alias.
- Keep alias writers/loaders intact while removing stale alias names from schema-boundary and output audit messages.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future\run.py src\anomaly_science\features\matrix.py src\anomaly_science\atlas\run.py src\anomaly_science\labels\run.py src\anomaly_science\prediction\run.py src\anomaly_science\controls\run.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m pytest tests\test_artifact_schemas.py tests\test_atlas.py tests\test_future_paths.py tests\test_feature_matrix.py tests\test_labels.py tests\test_prediction.py tests\test_controls.py -q`
- `.venv\Scripts\python.exe -m pytest tests\test_artifact_schemas.py tests\test_state_builder.py tests\test_trade_simulation.py tests\test_research_run.py -q`

## perf: vectorize atlas aggregation for compact run-research

Status: APPLIED.

Intent:
- Make `run-mvp1-atlas` consume strict canonical CSV artifacts directly instead of materializing every joined row as Python dataclasses before grouping.
- Preserve the atlas temporal contract and descriptive-only methodology: state/features remain as-of, future columns remain response summaries only.
- Keep `run-research <strategy> --days N` simple: strategy and optional days at the CLI, output and stage wiring under the hood.

Validation:
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\atlas\builder.py src\anomaly_science\atlas\run.py`
- `.venv\Scripts\python.exe -m pytest tests\test_atlas.py -q`
- `.venv\Scripts\python.exe main.py run-research broad_anomaly_v1_h30 --days 2` completed at `.output\results\research_runs\20260620T115928580603Z_broad_anomaly_v1_h30` with forensic `FAIL=0`, `WARN=8`
- Atlas in the full all-symbol 2d run completed in about `8m47s`; previous standalone 2d atlas measurement was `1582.80s`.

## perf: make compact run-research stages executable through labels

Status: APPLIED.

Intent:
- Keep `run-research <strategy> --days N` compact: output is automatic, and short default IS windows auto-scale holdout so at least one non-holdout day remains.
- Remove all-symbol hot-path stalls in data audit, events, state, future, feature matrix, and labels without changing trigger thresholds, labels, EV, or methodology.
- Reuse active strategy `H_max` for the state window and avoid state rows beyond the selected strategy horizon.
- Preserve canonical `strategy_*` artifacts and copy identical `anomaly_*` aliases after strict canonical schema validation instead of serializing huge CSVs twice.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_future_paths.py tests\test_feature_matrix.py tests\test_labels.py tests\test_atlas.py tests\test_artifact_schemas.py -q`
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\future\builder.py src\anomaly_science\future\run.py src\anomaly_science\features\matrix.py src\anomaly_science\labels\builder.py src\anomaly_science\atlas\builder.py src\anomaly_science\artifacts\writer.py`
- `run-mvp1-feature-matrix` on the 2d all-symbol run input/state: `710.30s` after removing duplicate state load and hot history copies.
- `run-mvp1-labels` on the 2d all-symbol state/future artifacts: `221.16s`.
- `run-mvp1-atlas` on the 2d all-symbol state/future/feature artifacts: `1582.80s`; atlas still needs streaming aggregation before claiming the full compact all-symbol `run-research --days 2` proof.

## perf: vectorize point-in-time universe audit

Status: APPLIED.

Intent:
- Remove per-source-row `iterrows()` scans from `build_symbol_universe_by_day`.
- Keep point-in-time universe behavior unchanged: dated dataset presence, string symbol identity, first/last seen timestamps, explicit missing days, and cross-section eligibility remain the same.
- Make `run-mvp1-data-audit` viable after all-symbol cache export instead of stalling in universe construction.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_data_audit.py -q`
- `.venv\Scripts\python.exe -m compileall src main.py tests zip_project.py`
- `Measure-Command { .venv\Scripts\python.exe main.py run-mvp1-data-audit --input .output\results\research_runs\20260620T032748623164Z_broad_anomaly_v1_h30\input --out tmp\data_audit_2d_perf }` -> `577.49s`

## perf: push cache export day filter into parquet reads

Status: APPLIED.

Intent:
- Avoid reading each full 380d symbol parquet when exporting a short `--days` window from the local cache.
- Keep the global window scan over `timestamp` only, then pass `filters=[("timestamp", ">=", start_ms)]` into the per-symbol parquet read.
- Preserve the existing post-read filter as a contract guard, so behavior stays identical while IO drops sharply for small windows.

Validation:
- `.venv\Scripts\python.exe -m pytest tests\test_cache_export.py -q`
- `.venv\Scripts\python.exe -m compileall -q src\anomaly_science\cache_export.py tests\test_cache_export.py`
- `Measure-Command { .venv\Scripts\python.exe main.py export-cache-mvp1-csv --cache-dir .output\market\binance_vision\um_futures\enriched_1m --out tmp\cache_export_2d_perf --days 2 --expected-days 2 --fail-on-missing-utc-days --progress-every 200 }` -> `186.99s`

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

Superseded by the structural-execution/market-mechanics patch: post-pump OI and
liquidation streams are optional enrichment with explicit missingness. This
historical bullet is not the current contract; post-anomaly extension streams
remain required.

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
## research: paired pump-fade OI incremental protocol

Status: APPLIED; full development run in progress.

Changes:
- Adds an explicit population equality gate so `oi_available=true` can define the experiment population without becoming a model feature.
- Runs no-OI and with-OI archetype discovery on identical event rows, with blind and calendar-block shuffled-label controls in both arms.
- Persists later-period OOS probabilities and applies a 2,000-draw paired group bootstrap to the AUC delta.
- Parallelizes the independent per-symbol pump builder deterministically and excludes dated delivery contracts from the perpetual universe.
- Records OI coverage and excluded delivery symbols in dataset metadata.

Validation:
- 30-symbol smoke: 1,093 event rows, 100% OI coverage; AUC 0.6050 -> 0.6351; paired 95% interval -0.0038..+0.0632; no accepted archetype categories.
- `.venv\Scripts\python.exe -m pytest -q`: 288 passed before the final delivery-universe regression, followed by focused delivery/parallel tests: 2 passed.
## decision: mark nature-based EV as proxy utility

Status: PROPOSED.

Changes:
- Adds explicit utility interpretation fields to decision rows: `utility_model_kind=nature_proxy`, `utility_evidence_status=NON_FINAL`, and `utility_evidence_claim_allowed=false`.
- Records the same utility boundary in EV metrics and run config so it survives artifact-only review.
- Adds stage and independent forensic audit checks that fail if a decision artifact tries to claim final utility proof from nature-class EV.
- Updates methodology/docs/tests without changing EV math, thresholds, model fitting, simulation entry/exit logic, or runtime-heavy stages.

Validation in this environment:
- `git apply --check` against the Patch 1-4 working tree.
- `python -m compileall -q main.py src tests zip_project.py`
- Full pytest still needs the project venv because this sandbox lacks project runtime dependencies such as `polars`.

## pump-fade: add archetype threshold stability audit

Status: PROPOSED.

Changes:
- Adds `anomaly_archetype_threshold_stability.csv` with a lightweight `strategy_archetype_threshold_stability.csv` alias.
- Audits each selected frozen archetype condition against original, rounded, nearby, and discovery-only coarse-quantile threshold variants.
- Measures discovery and later verification support, fade rate, matched blind rate, lift, cluster edge lower bound, and membership Jaccard against the original rule.
- Keeps the audit diagnostic-only: no threshold, rule, model, EV, simulation, or evidence status is changed by stability rows.

Validation in this environment:
- `git apply --check` against the Patch 1-5 working tree.
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest tests/test_artifact_schemas.py::test_mvp1_artifact_schemas_are_fixed -q`
- Full archetype pytest collection still needs the project venv because this sandbox lacks `polars`.

## controls: add matched random market-time baselines

Status: PROPOSED.

Changes:
- Adds a bounded same-symbol/session/volatility random market-time control to trade simulation metrics.
- Keeps the existing signal-time shuffle control separate instead of treating all random entries as one baseline.
- Samples market-time controls from the candle stream with deterministic bounded candidate pools, avoiding a full cross join over all symbols and minutes.
- Records matched-market-time control counts, total net PnL, and delta-vs-control metrics per declared structural execution variant.
- Updates forensic required simulation-control metrics, methodology docs, and regression tests.

Validation in this environment:
- `git apply --check` against the Patch 1-6 working tree.
- `python -m compileall -q main.py src tests zip_project.py`
- Focused pytest still needs the project venv if runtime dependencies are absent.

## labels: add realized barrier outcome artifacts

Status: PROPOSED.

Changes:
- Adds `strategy_barrier_outcomes.csv` with `anomaly_barrier_outcomes.csv` alias.
- Builds labels-only realized structural target-first / stop-first / timeout outcomes from existing decision rows and 1m candles during the simulation stage.
- Resolves same-candle target/stop collisions as `stop_loss_first` and records `intracandle_collision` explicitly.
- Keeps the artifact out of feature generation, EV selection, and trade admission; current nature-proxy utility remains `NON_FINAL`.
- Updates canonical schemas, run config, protocol audit, methodology docs, and regression tests.

Validation in this environment:
- `git apply --check` against the Patch 1-7 working tree.
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest tests/test_artifact_schemas.py::test_mvp1_artifact_schemas_are_fixed -q`
- Full simulation pytest collection still needs the project venv because this sandbox lacks `polars`.

## prediction: support registered state-lattice supervised anchors

Status: PROPOSED.

Changes:
- Adds `registered_state_lattice_v1` supervised anchor policy with bounded offsets `0,5,10,15,30,60`; `t0_only_v1` remains an explicit ablation/baseline.
- Loads only registered anchor rows from state/label/feature artifacts and records policy id/offsets in run config, prediction metrics, and model metadata.
- Adds event-anchor-normalized sample weights so multiple lattice anchors do not multiply an event's split weight.
- Excludes same `(symbol,event_id)` rows from train if that event appears in the OOS week, preventing cross-week event leakage.
- Updates methodology, artifact schema, CLI arguments, and regression tests without enabling full per-minute supervised datasets.

Validation in this environment:
- `git apply --check` against the Patch 1-8 working tree.
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest tests/test_artifact_schemas.py::test_mvp1_artifact_schemas_are_fixed -q`
- Full prediction pytest collection still needs the project venv because this sandbox lacks `polars`.

## perf: reuse prepared population in paired OI experiment

Status: PROPOSED.

Changes:
- Splits archetype preparation into reusable row/split preparation and feature-matrix preparation.
- Lets paired pump-fade OI experiments read and symbol-limit the input once, enforce the finite-OI population gate once, and share the prepared discovery/verification rows across both arms.
- Keeps model behavior separate: the baseline arm still excludes all OI model features, while the OI arm builds its own feature matrix with the registered OI features.
- Records `input_reuse_contract=paired_oi_shared_population_split_v1` in child run metadata and `preparation_contract` plus shared row counts in `oi_incremental_summary.json`.
- Preserves the paired row-identity assertions and FAILED_PARTIAL behavior when preparation or one arm fails.

Validation in this environment:
- `git apply --check` against the Patch 1-9v3 working tree.
- `python -m compileall -q main.py src tests zip_project.py`
- Focused archetype pytest still needs the project venv because this sandbox lacks `polars`.

## science: separate online pump-fade states from offline labels

Status: APPLIED.

Changes:
- Introduces the immutable `pump_fade_online_state_v1` schema with no future-derived columns.
- Moves outcomes, censoring, final event peak/end, and nature labels into a separate offline-label table.
- Writes online, labels, and an audited one-to-one supervised research view as distinct Parquet artifacts.
- Adds future-tail invariance coverage proving that online prefixes remain unchanged while offline labels may change.
- Registers all active explicit missingness flags in the typed strategy feature catalog.
- Closes Phase 0 consistency defects found by the full suite: archetype assignment status, simulation audit registration, active-strategy layout assertions, and strict simulation fixtures.

Validation:
- `python -m compileall -q main.py src tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest -q` (`318 passed`)

## science: add registered interaction atlas and causal resolved-event memory

Status: APPLIED_AND_DEVELOPMENT_TESTED.

Changes:
- Extends generic Core regime inference from single frozen bins to explicitly registered pairs/triples while retaining all single hypotheses.
- Adds cheap support/effect pruning, a complete `regime_screening.csv`, joint BY-FDR, T0/ordinal 1/ordinal 2 family execution, and Bonferroni correction across those variants.
- Adds strategy-owned `pump_fade_event_memory_v1` with a strict prior-resolution embargo, relative price structure, bounded three-event slots, aggregate 6h-48h recurrence state, explicit availability flags, and 48h recurrence-chain IDs.
- Bumps the online schema to `pump_fade_online_state_v2` and fixes `last_prior_size` so an unresolved prior event cannot expose its offline-finalized peak.
- Adds generic probability `split_group_column` support so recurrent event chains remain isolated across fit, validation, calibration, and weekly OOS boundaries while event IDs remain prediction/weighting groups.
- Adds paired baseline/event-memory weekly WFA and auditable family artifacts.

Development results:
- No interaction passed every registered gate. `efficient_path_x_upper_wick` was rejected for insufficient month/symbol support despite a large point effect.
- Event memory failed incremental paired gates at T0 and degraded ordinals 1-2. No variant emitted calibrated probability at or above 0.70.
- These are negative/mixed dirty-worktree development results, not pristine evidence or a production admission.

Validation:
- Full lifecycle rebuild: 157,360 online rows, 39,283 events, 105,624 state rows; temporal and finite-value audits pass.
- `.venv\Scripts\python.exe -m pytest -q` (`357 passed`).
- `python -m compileall -q src tests` and `git diff --check` pass.

## science: add event-scoped same-symbol positioning evidence

Status: APPLIED_AND_NEGATIVE_DEVELOPMENT_RESULT.

Changes:
- Adds generic Core event-scoped Binance positioning acquisition with exact symbol/date scope, bounded concurrency, atomic per-symbol checkpoints, strict resume validation, coverage, metadata, and manifest artifacts.
- Adds same-symbol backward as-of attachment at both snapshot and ignition with a +5m publication lag, 10m maximum age, fixed 15m/60m changes, ignition-relative changes, and explicit complete-case status.
- Treats non-positive ratios as invalid log inputs, removes only the affected rows, and records the count rather than rejecting a whole day or silently coercing values.
- Registers a finite 19-feature pump-fade symbol-positioning family and reuses the generic paired context probability runner for T0 and ordinals 1-2.

Development result:
- 20,998/20,998 requested symbol-day archives succeeded; 587,091 causal metric rows retained; 1,869 invalid ratio rows audited.
- Complete-case coverage exceeds 99.6%.
- Delta AUC: T0 +0.00332, ordinal 1 -0.00045, ordinal 2 +0.00241. All effects failed registered minima/inference gates; no `p>=0.70` rows.
- Family rejected; post-hoc ratio, lag, stage, and symbol mining forbidden.

## science: add causal pump-fade CVD path evidence

Status: APPLIED_AND_NEGATIVE_DEVELOPMENT_RESULT.

Changes:
- Adds strategy-owned `pump_fade_cvd_path_v1` from closed event-minute taker-buy
  quote volume, total quote volume, and close price only.
- Registers 11 fixed path, acceleration, drawdown, confirmation, efficiency, and
  price/flow divergence coordinates with explicit source availability.
- Bumps the online state to `pump_fade_online_state_v3` and feature schema to
  `pump_fade_market_mechanics_v4`.
- Reuses the generic paired complete-case probability runner for T0 and state
  ordinals 1-2; baseline and augmented arms retain identical rows and freezes.

Development result:
- Lifecycle v3 preserved all 157,360 decision rows, 39,283 events, and labels
  from v2; CVD source coverage is 100% and full-family coverage is 82.83%.
- Delta AUC: T0 -0.00099, ordinal 1 -0.00048, ordinal 2 +0.00055. Every
  interval crossed zero, all paired gates failed, and no `p>=0.70` row existed.
- Family rejected; post-hoc CVD window, coordinate, and stage mining on this OOS
  period is forbidden.

Validation:
- Full lifecycle rebuild: 157,360 online rows and 39,283 events with v2-identical
  keys and labels; temporal audit passed.
- 749 lifecycle/experiment manifest entries passed size and SHA-256 checks.
- `python -m compileall -q main.py src tests` and `git diff --check` pass.
- `.venv\Scripts\python.exe -m pytest -q` (`370 passed`).

## science: add causal same-symbol perp-crowding evidence

Status: APPLIED_AND_MIXED_DEVELOPMENT_RESULT_NOT_ADMITTED.

Changes:
- Adds generic Core event-scoped acquisition for official Binance Vision 1m
  premium-index klines and monthly funding-rate records with atomic symbol
  checkpoints, exact resume scope, coverage, metadata, and manifests.
- Adds bounded same-symbol backward as-of joins with premium close availability,
  conservative funding publication lag, fixed strategy-declared windows, and
  explicit partial/complete-case flags.
- Registers a frozen 16-coordinate pump-fade premium/funding family and reuses
  the paired T0/ordinal 1/ordinal 2 weekly WFA runner.

Development result:
- 27,218/27,847 archives succeeded, 629 prelisting archives were explicitly
  missing, failures were zero; complete-case coverage exceeded 93%.
- T0 delta AUC +0.00934 had positive CI and corrected p=0.004; log-loss and
  Brier gains passed their incremental gates. Admission still failed because
  AUC delta missed +0.010, absolute AUC was 0.597, and no `p>=0.70` row existed.
- The unchanged family is reserved for new forward confirmation; post-hoc
  premium/funding mining on this OOS period is forbidden.

Validation:
- Full archive: 693 symbols, 27,847 requested archives, zero hard failures;
  2,082 archive manifest entries verified.
- Projection/experiment audit: row identity and temporal joins pass; all 2,775
  acquisition/projection/experiment manifest entries verified.
- `python -m compileall -q main.py src tests` and `git diff --check` pass.
- `.venv\Scripts\python.exe -m pytest -q` (`376 passed`).

## science: add broad cross-fitted phenotype discovery

Status: APPLIED_AND_MIXED_DEVELOPMENT_RESULT_NO_HIGH_PROBABILITY_PHENOTYPE.

Changes:
- Adds generic Core cross-fitted phenotype discovery with recurrence-chain-safe
  search/calibration/verification segments and resolution-purged temporal folds.
- Adds explicit search-fitted numeric imputation plus missing indicators,
  search-frozen categorical levels, direct CatBoost leaf rules, and interpretable
  pooled-OOF-risk surrogate rules.
- Persists every screened leaf, distinct frozen rules, Beta-shrunk calibration
  probabilities, later verification/BY-FDR, assignments, coverage, 19 complete
  label-permutation controls, transforms, protocol, and manifest.
- Registers strategy-owned `pump_fade_broad_phenotype_surface_v1` spanning 288
  causal mechanics, memory, CVD, OI, reference/symbol positioning, and
  premium/funding features. Adds CLI progress milestones for future long runs.

Development result:
- 29,151 leaves -> 917 fold candidates -> 17 distinct recurrent rules.
- Every one of 19 null searches produced zero frozen rules.
- Fourteen rules passed later development verification and cover 39.1% of
  verification events, but frozen probabilities maxed at 0.6759 and none passed
  the registered 0.70 high-probability gate.
- The architecture now finds many stable groups; high-probability prediction is
  still unproven and thresholds/rules may not be retuned on verification.

## science: retain governed phenotype follow-up memory

Status: APPLIED.

Changes:
- Persists every frozen phenotype in a follow-up registry instead of collapsing
  research history into pass/discard semantics.
- Separates unchanged scientific status from forward recalibration, stability,
  EV, and archival research dispositions.
- Records failure mode, evidence snapshot, mechanism features, priority, and the
  next admissible study.
- Explicitly forbids rule/threshold/gate tuning on the already viewed periods;
  independent confirmation requires new forward data.

## science: add generic causal coarse-regime atlas

Status: APPLIED.

Changes:
- Adds a Core-owned frozen-bin regime atlas between descriptive paths and CatBoost search.
- Keeps pump-fade ownership limited to percentile, ATR-normalized, and time axis declarations.
- Uses discovery-only threshold fitting and candidate selection, same-symbol/month/activity controls, ISO-week bootstrap with baseline re-estimation, within-stratum label permutations, BY-FDR, and month/symbol stability gates.
- Writes discovery, stability, controls, frozen-spec, holdout-access, metadata, and manifest artifacts; dirty runs are explicitly non-evidential.
- Adds strict signal/null/future-tail/config/artifact/CLI tests.

Development diagnostic:
- Full historical dirty-worktree V3 run: 21,746 discovery rows, 17,520 verification rows, 3 discovery candidates, 1 statistically development-replicated coarse regime.
- The surviving regime is discovery-frozen `rel_vol_phase` 99+ (`>318.9769`): verification matched fade rate 0.6139 vs 0.4068, delta +0.2071; re-estimated week/month/symbol bootstrap lower bounds are +0.1443/+0.1480/+0.1114; shuffled p=0.001, BY q=0.0055, positive in 5/5 eligible months and 35/44 eligible symbols.
- Artifact status remains `UNFROZEN_DIRTY_DEVELOPMENT`; this is not calibrated prediction, pristine evidence, EV, or trading proof.

Validation:
- `python -m compileall -q main.py src tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest -q` (`327 passed`)

## science: add pump-fade missingness and rejection audit artifacts

Status: APPLIED.

Changes:
- Writes canonical `strategy_data_quality.csv`, `feature_missingness_report.csv`, and `dataset_rejection_summary.csv` artifacts on every pump-fade dataset build.
- Records each declared feature's family, missing fraction, stream requirement, availability flag, and explicit status.
- Preserves diagnostics on fail-closed runs while removing stale production datasets.
- Encodes unavailable OI positioning states as `NaN` with `oi_available=false`, never as a false/zero market state.

Validation:
- `python -m compileall -q main.py src tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest -q` (`318 passed`)

## science: add IS-only blind drawdown recovery Stage 0

Status: APPLIED, descriptive evidence only.

Changes:
- Adds a generic causal recovery-path index with strict future timing and
  censor-aware break-even measurements.
- Adds the frozen session-anchored drawdown-ladder protocol, physical IS-only
  reader, bounded parallel builder, separate candidate/outcome artifacts,
  equal-notional ladder states, and Kaplan-Meier summaries.
- Adds fail-closed temporal audits proving that no 2026 row is consumed.
- Adds CLI coverage, future-tail mutation tests, session activation tests, and
  censoring tests.

Full IS diagnostic:
- 796 perpetual symbols, 347,866 individual levels, 214,992 ladder states, and
  106,922 parent symbol/session events.
- Temporal audit PASS; this run is not a trade simulation and makes no edge or
  PnL claim.

## science: add mirrored and prior non-drawdown ladder controls

Status: APPLIED; naive unconditional long edge rejected on IS.

Changes:
- Extends the generic recovery index to signed short recovery without changing
  frozen long artifacts.
- Adds the mechanically identical rally/short mirror, exact-stratum cluster
  bootstrap, frozen primary states, Holm correction, and month stability.
- Adds strictly prior same-symbol/session/minute non-drawdown controls matched
  on causal volatility, quote volume, and return with fail-closed calipers.
- Audits missing activity without imputation, control-outcome resolution before
  each signal, unique pair keys, feature timing, future timing, and zero OOS
  access.

Full IS result:
- Mirror: 238,585 ladder states; temporal audit PASS. Short recovery was higher
  but its adverse tail was worse; no short edge claim.
- Prior control: 162,548 / 214,992 matched pairs (75.61%); temporal audit PASS.
  The eligible 3→6% state underperformed matched ordinary market time by 10.56
  percentage points in recovery and 2.48 percentage points in signed 48-hour
  return. Deeper primary states failed coverage and remain unidentified.

## science: preregister full-context drawdown recovery prediction

Status: APPLIED; IS feature build and weekly prediction still pending.

Changes:
- Adds a typed Stage 1 feature contract covering the complete causal picture:
  geometry, path, EMA, activity/order flow, OI/liquidations, BTC/relative state,
  market breadth, sessions, concurrency, and strictly resolved event memory.
- Adds bounded IS-only feature and breadth builders, explicit missingness,
  immutable output directories, and temporal/future-mutation audits.
- Freezes paired weekly CatBoost arms: a structural geometry control and the
  complete causal model, with absolute and incremental probability gates.
- Keeps 2026 physically unread and explicitly forbids PnL work until calibrated
  causal separation passes.

## performance: parallelize independent weekly probability fits

Status: APPLIED; numerical protocol unchanged.

Changes:
- Adds bounded thread-level concurrency across independent calendar-week model
  freezes while leaving CatBoost parameters, seed, train eligibility, splits,
  calibration, and prediction order unchanged.
- Records the execution-only weekly job count in probability artifacts.
- Adds a deterministic regression proving sequential and parallel predictions,
  metadata, feature importance, and model ordering are identical.

## science: attest frozen drawdown Stage 1 paired comparison

Status: APPLIED; paired result not opened before implementation.

Changes:
- Adds an immutable runner for the pre-registered full-causal versus structural
  probability comparison.
- Requires both inputs to be frozen, pristine-holdout-free, protocol-matched,
  and temporally audited before comparison.
- Persists paired metrics, ISO-week bootstrap/sign-flip inference, frozen gates,
  input hashes, code revision, report, and artifact manifest.

## performance: preserve exact paired inference with weekly sufficient statistics

Status: APPLIED; comparison result remained unopened.

Changes:
- Replaces repeated 168k-row AUC sorting in every bootstrap draw with exact
  precomputed week-by-week positive/negative concordance matrices.
- Computes bootstrap log-loss and Brier deltas from exact weekly sums and
  sign-flip AUC from all four baseline/augmented week-pair combinations.
- Preserves the frozen random seed, 2,000 bootstrap draws, 999 sign flips,
  metric definitions, and gates; synthetic old/new inference agrees to floating
  precision while runtime falls by orders of magnitude.

## audit fix: guarantee reliability coverage for every probability gate

Status: APPLIED before reading Stage 1 gate results; predictions unchanged.

Root cause:
- The Stage 1 high-probability gate was frozen at 0.92, but the generic
  reliability table's explicit display thresholds ended at 0.90. The original
  gate lookup therefore had no 0.92 row and would fail mechanically regardless
  of model quality.

Changes:
- Core reliability now always includes the configured gate threshold in
  addition to display thresholds.
- Adds a regression with a gate threshold absent from the display list.
- Adds an immutable gate-amendment runner that reuses frozen predictions,
  metrics, null tests, and weekly metadata, changes no model output, and records
  all source hashes and the exact correction reason.

## science: add post-gate drawdown win/loss diagnostics

Status: APPLIED as explanatory analysis only; no filter authority.

Changes:
- Compares recovery wins and failures across every available numeric model
  feature using finite support, missingness, means, and standardized differences.
- Aggregates full-model feature importance by feature and causal family across
  all frozen weeks.
- Reports recovery/failure shares and full-versus-structural AUC, log loss, and
  Brier by symbol, week, month, session, and exact ladder state.
- Marks all outputs as post-gate associations that cannot rescue the failed
  incremental hypothesis or become IS trading filters.

Frozen IS result:
- Full arm passed all 12 corrected absolute probability gates, but the
  structural control was better on AUC, log loss, and Brier.
- Full-minus-structural AUC was -0.03188 (95% CI -0.04942 to -0.00262),
  log-loss improvement -0.00989, and Brier improvement -0.000566.
- All nine incremental gates failed; status is
  `IS_CAUSAL_CONTEXT_INCREMENTAL_HYPOTHESIS_REJECTED`.
- Post-gate diagnostics confirm broad symbol/time degradation rather than one
  removable bad coin. No execution or PnL stage was opened.

## science: preregister post-selection structural protection EV

Status: APPLIED; EV return result deliberately unopened.

Changes:
- Adds a typed causal join from frozen weekly structural OOF probabilities to
  complete pre-2026 48-hour return paths.
- Freezes HOLD-at-0.92 versus immediate protective EXIT and unconditional-hold
  comparison on three already registered stressed states.
- Applies a 12-hypothesis selection adjustment across viewed arms, endpoints,
  and states, with 20,000 ISO-week and symbol cluster bootstrap draws.
- Adds fail-closed checks for join identity, target identity, model freeze time,
  feature/future timing, complete horizons, finite paths, duplicate decisions,
  and any implied access to 2026.
- Adds future-mutation, threshold-boundary, OOS-boundary, protocol, and CLI
  regressions. Build and analysis remain separate commands so the protocol can
  be committed before evidence is opened.

Pre-result schema audit amendment:
- The first build stopped before writing EV rows because Stage 0 and Stage 1
  both used the generic name `feature_cutoff_time_ms` for different causal
  boundaries. Stage 0 means session/order-activation cutoff; Stage 1 means the
  fill-snapshot feature cutoff used by prediction.
- The join now names and audits both meanings explicitly: the prediction cutoff
  must equal the Stage-1 cutoff, while both the Stage-1 cutoff and Stage-0
  trigger cutoff must be no later than the snapshot. No result, threshold,
  state, endpoint, or gate was opened or changed.

Frozen IS result:
- 27,560 joined EV rows; all temporal/OOS audits PASS and no 2026 row read.
- Primary 3→6 policy mean at 25 bps was -1.4436%, worse than unconditional
  48-hour hold at -1.2718% by 0.1718 percentage points.
- Week- and symbol-cluster adjusted lower bounds were negative for absolute EV
  and improvement; 0/5 months were positive.
- CVaR5 improved, but mean EV, stability, and concentration gates failed.
- Final status `IS_STRUCTURAL_PROTECTION_EV_GATES_FAIL`; no execution or
  portfolio simulation authorized and same-sample retuning forbidden.

## science: preregister directly aligned continuation nature

Status: APPLIED; continuation label distribution and model results unopened.

Changes:
- Adds a causal primary-state dataset whose target is whether terminal 48-hour
  HOLD outperforms EXIT at the already observed snapshot close.
- Uses full-horizon resolution timestamps rather than the old early recovery
  resolution, preserving strict weekly training eligibility.
- Freezes a structural-primary and complete-causal-challenger CatBoost design,
  all probability/calibration/null gates, paired comparison, and selection
  adjustment before opening the target distribution.
- Adds fail-closed identity, cutoff, future-start, horizon, finite-label,
  duplicate, output immutability, clean-revision, and 2026-boundary checks.
- Adds target-mutation, OOS-boundary, model-contract, protocol, and CLI tests.

Frozen IS result:
- 24,567 complete primary-state rows; 19,821 OOS predictions; 22/22 frozen
  weeks per arm; every timing/OOS audit PASS.
- Structural AUC 0.4816, log-loss gain -0.01336, Brier gain -0.00629, ECE
  0.0849; full causal was worse on every overall metric.
- Both arms had AUC below 0.5 in all five months, and high-probability cohorts
  failed reliability/support gates.
- Sparse symbol-level exceptions have no filter authority. Final status
  `IS_CONTINUATION_NATURE_PREDICTION_REJECTED`; later EV stages forbidden.
