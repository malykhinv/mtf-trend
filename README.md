# anomaly_science

Clean scientific rebuild of anomaly-driven crypto research.

Legacy code is quarantined under `legacy_quarantine/old_repo` and is reference-only. New code must be implemented under `src/anomaly_science` without importing legacy modules.

Current target:

```text
MVP 1 — data quality, broad anomaly detector, online 1m state, future paths, protocol audit.
MVP 2 atlas slice — descriptive anomaly nature atlas over MVP1 state/future artifacts.
MVP 3+ research slice — descriptive labels, weekly CatBoost+Isotonic walk-forward prediction, controls, decision timing, EV, pessimistic simulation, and holdout governance.
```

The project has active executable anomaly research strategies documented in `docs/strategies/anomaly_strategy.md`. It does not currently define a live trading strategy. Live execution comes only after calibrated prediction, decision timing, EV, simulation checks, and a separate shadow-live phase.

Methodology completion is tracked explicitly in `research/METHODOLOGY_GAP_LEDGER.md`. That ledger is the source of truth for what is implemented, partial, missing, or intentionally out of scope for the offline research phase.

## Current executable stage

Primary offline research commands:

```bash
python main.py run-research broad_anomaly_v1_h30
python main.py run-research post_anomaly_extension_v1_h120
python main.py run-research post_pump_distribution_v1_h120
python main.py run-research broad_anomaly_v1_h30 --days 380
python main.py run-research broad_anomaly_v1_h30 --research-mode frozen_holdout --protocol-freeze-id protocol_freeze_v1
```

`run-research` uses the local Binance Vision enriched 1m cache under `.output/market/binance_vision/um_futures/enriched_1m`, creates its output directory automatically under `.output/results/research_runs/`, exports the cache into the MVP1 CSV boundary, writes holdout governance/freeze artifacts from the exported cache period, applies the holdout lock, runs the full research pipeline through simulation, then writes root reproducibility manifests and an independent forensic protocol audit; the run fails if that audit emits any `FAIL` row. Root outputs include `strategy_run_config.csv`, `anomaly_run_config.csv`, `artifact_manifest.json`, and `research_run_summary.csv`. Input-boundary outputs include `cache_export_coverage.csv` and `cache_export_manifest.json`, so the actual exported symbols, excluded delivery-contract symbols, period, missing UTC days, row count, lifecycle gaps, and file hashes are inspectable for every run. Default `--research-mode is` excludes the final holdout from downstream research stages; for short exported windows, IS mode records `requested_holdout_days` and auto-scales `effective_holdout_days` to preserve the final holdout while keeping up to 8 non-holdout research days for weekly WFA proof when the window allows it. `--research-mode frozen_holdout` requires `--protocol-freeze-id` and records explicit approved holdout access. If `--days` is omitted, it uses the full available cache period. The current full-cache proof is recorded in `research/validation/cache_export_380d_validation.json` and validates 2025-06-03..2026-06-17, 796 perpetual symbols, 344,895,197 1m rows, no missing UTC days, no duplicate 1m rows, and no unclassified 1m gaps; 3,570 missing 1m rows are explicitly classified as settlement-transition gaps with `{symbol}SETTLED` evidence.

Low-level MVP1 stage commands remain available for debugging:

```bash
python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit
python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/mvp1_events --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/strategy_events.csv --out tmp/mvp1_state
python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/strategy_state_1m.csv --out tmp/mvp1_future
python main.py run-mvp1-features --out tmp/mvp1_features
python main.py run-mvp1-strategy-registry --out tmp/mvp1_strategy_registry
python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/mvp1_post_extension_events --strategy-name post_anomaly_extension_v1_h120 --horizon-minutes 120
python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/mvp1_post_pump_events --strategy-name post_pump_distribution_v1_h120 --horizon-minutes 120
python main.py run-mvp1-feature-matrix --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/strategy_state_1m.csv --out tmp/mvp1_feature_matrix
python main.py run-mvp1-atlas --state tmp/mvp1_state/strategy_state_1m.csv --future tmp/mvp1_future/strategy_future_paths.csv --features tmp/mvp1_feature_matrix/strategy_feature_matrix.csv --out tmp/mvp1_atlas
python main.py run-mvp1-labels --state tmp/mvp1_state/strategy_state_1m.csv --future tmp/mvp1_future/strategy_future_paths.csv --out tmp/mvp1_labels
python main.py run-mvp1-prediction --state tmp/mvp1_state/strategy_state_1m.csv --labels tmp/mvp1_labels/strategy_outcome_labels.csv --features tmp/mvp1_feature_matrix/strategy_feature_matrix.csv --out tmp/mvp1_prediction --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
python main.py run-mvp1-controls --state tmp/mvp1_state/strategy_state_1m.csv --labels tmp/mvp1_labels/strategy_outcome_labels.csv --features tmp/mvp1_feature_matrix/strategy_feature_matrix.csv --out tmp/mvp1_controls --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
python main.py run-mvp1-expected-value --state tmp/mvp1_state/strategy_state_1m.csv --labels tmp/mvp1_labels/strategy_outcome_labels.csv --predictions tmp/mvp1_prediction/strategy_oos_predictions.csv --out tmp/mvp1_ev --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
python main.py run-mvp1-trade-simulation --input tests/fixtures/minimal_market_data --decision-timing tmp/mvp1_ev/strategy_decision_timing.csv --out tmp/mvp1_simulation --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
python main.py run-mvp1-holdout-governance --out tmp/mvp1_governance --start-date 2024-01-01 --end-date 2024-12-15 --freeze-id protocol_freeze_v1
python main.py run-mvp1-holdout-governance --out tmp/mvp1_governance_holdout --start-date 2024-01-01 --end-date 2024-12-15 --freeze-id protocol_freeze_v1 --research-mode frozen_holdout --holdout-access-artifact manual_review
```

`run-mvp1-state` builds `strategy_state_1m.csv` from normalized closed 1m candles and a strict `strategy_events.csv` artifact boundary. It updates state only at times greater than or equal to `event_detection_time_ms`, and state features use only candles with `available_time_ms <= state_time_ms`.

`run-mvp1-future` builds `strategy_future_paths.csv` from normalized closed 1m candles and a strict `strategy_state_1m.csv` artifact boundary. Future outcomes use only candles with `available_time_ms > snapshot_time_ms`, and every row satisfies `feature_cutoff_time_ms <= snapshot_time_ms < future_start_time_ms`.

`run-mvp1-atlas` reads `strategy_state_1m.csv`, `strategy_future_paths.csv`, and required `strategy_feature_matrix.csv` through strict schema boundaries, joins them one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`, and writes:

- `strategy_nature_atlas.csv`
- `strategy_context_splits.csv`
- `strategy_response_surfaces.csv`
- `strategy_market_shock_groups.csv`

Atlas grouping uses state plus feature-matrix as-of fields. Coarse 30m response bins are descriptive atlas bins only, not calibrated labels, entry logic, exit logic, EV, PnL, or trade simulation. Market-shock groups use point-in-time `market_shock_id` and `systemic_cluster_regime` from the feature matrix.

`run-mvp1-strategy-registry` writes `strategy_registry.csv` for executable variants only and `strategy_implementation_status.csv` as the generated truth table for all declared variants. The current executable anomaly set is `broad_anomaly_v1_h15/h30/h60`, `post_anomaly_extension_v1_h60/h120/h180`, and `post_pump_distribution_v1_h60/h120/h180`. The current anomaly spec has no remaining specified-only registry variants.

`run-mvp1-labels` reads the same state/future artifacts through strict boundaries, joins them one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`, and writes `strategy_outcome_labels.csv` with `scenario_15m`, `scenario_30m`, `scenario_60m`, `scenario_120m`, and `scenario_180m`. Scenario values are descriptive future-nature targets for later walk-forward prediction calibration: `long_continuation`, `short_fade`, `static_or_chop`, `unclear`, or explicit `missing_future`. Trap-like ambiguity maps to `unclear` in MVP1; a separate trap class requires a new label schema.

Low-level prediction/control/EV/simulation commands accept only Core-supported horizons `15/30/60/120/180`, then validate the selected `--strategy-name` + `--horizon-minutes` pair through the strategy registry before file IO. If `--strategy-name` is omitted, the CLI keeps the backward-compatible broad anomaly default `broad_anomaly_v1_h{horizon}`; use explicit `post_anomaly_extension_v1_h120/h180` or `post_pump_distribution_v1_h120/h180` for executable 120m/180m research.

`run-mvp1-prediction` reads `strategy_state_1m.csv`, required `strategy_feature_matrix.csv`, and `strategy_outcome_labels.csv` through strict boundaries, then runs weekly walk-forward CatBoost with one-vs-rest Isotonic calibration. Purge is computed from active strategy `H_max`, so train rows must satisfy `train_snapshot_time + H_max <= weekly_model_freeze_time`. OOS days inside a week use frozen weekly weights, best iteration, feature schema, and calibrators. It writes:

- `strategy_oos_predictions.csv`
- `strategy_calibration.csv`
- `strategy_prediction_metrics.csv`
- `strategy_model_metadata.csv`
- `strategy_feature_importance.csv`
- `strategy_model_training_diagnostics.csv`

Predictions are calibrated future-nature probabilities, not trading commands. `strategy_oos_predictions.csv` and `strategy_model_metadata.csv` carry explicit `target_horizon_minutes`, `target_label_column`, active `H_max`, and strategy identity fields so downstream stages do not infer the target from file names or CLI defaults. If a weekly model cannot be trained honestly, `strategy_model_training_diagnostics.csv` records the explicit skip reason.

`run-mvp1-controls` reads the same strict state/label boundaries and writes negative scientific controls:

- `strategy_placebo_tests.csv`
- `strategy_baseline_comparison.csv`

The controls include deterministic random-label, time-shuffled-label, symbol-shuffled-label placebos, universal simple baselines, and anomaly-specific rule/ablation baselines. Feature-aware baselines and ablations require `strategy_feature_matrix.csv`; missing feature matrix is a hard input error, not a deferred proxy mode.

EV and simplified pessimistic simulation are research artifacts only. Shadow live and production live remain intentionally absent.
