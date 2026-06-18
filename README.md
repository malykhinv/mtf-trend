# anomaly_science

Clean scientific rebuild of anomaly-driven crypto research.

Legacy code is quarantined under `legacy_quarantine/old_repo` and is reference-only. New code must be implemented under `src/anomaly_science` without importing legacy modules.

Current target:

```text
MVP 1 — data quality, broad anomaly detector, online 1m state, future paths, protocol audit.
MVP 2 atlas slice — descriptive anomaly nature atlas over MVP1 state/future artifacts.
MVP 3+ research slice — descriptive labels, weekly CatBoost+Isotonic walk-forward prediction, controls, decision timing, EV, pessimistic simulation, and holdout governance.
```

The project has an active anomaly research strategy documented in `docs/strategies/anomaly_strategy.md`. It does not currently define a live trading strategy. Live execution comes only after calibrated prediction, decision timing, EV, and simulation checks.

## Current executable stage

Current MVP1 commands:

```bash
python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit
python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/mvp1_events
python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/anomaly_events.csv --out tmp/mvp1_state
python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/anomaly_state_1m.csv --out tmp/mvp1_future
python main.py run-mvp1-features --out tmp/mvp1_features
python main.py run-mvp1-feature-matrix --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/anomaly_state_1m.csv --out tmp/mvp1_feature_matrix
python main.py run-mvp1-atlas --state tmp/mvp1_state/anomaly_state_1m.csv --future tmp/mvp1_future/anomaly_future_paths.csv --features tmp/mvp1_feature_matrix/anomaly_feature_matrix.csv --out tmp/mvp1_atlas
python main.py run-mvp1-labels --state tmp/mvp1_state/anomaly_state_1m.csv --future tmp/mvp1_future/anomaly_future_paths.csv --out tmp/mvp1_labels
python main.py run-mvp1-prediction --state tmp/mvp1_state/anomaly_state_1m.csv --labels tmp/mvp1_labels/anomaly_outcome_labels.csv --features tmp/mvp1_feature_matrix/anomaly_feature_matrix.csv --out tmp/mvp1_prediction
python main.py run-mvp1-controls --state tmp/mvp1_state/anomaly_state_1m.csv --labels tmp/mvp1_labels/anomaly_outcome_labels.csv --features tmp/mvp1_feature_matrix/anomaly_feature_matrix.csv --out tmp/mvp1_controls
python main.py run-mvp1-expected-value --state tmp/mvp1_state/anomaly_state_1m.csv --labels tmp/mvp1_labels/anomaly_outcome_labels.csv --predictions tmp/mvp1_prediction/anomaly_oos_predictions.csv --out tmp/mvp1_ev
python main.py run-mvp1-trade-simulation --input tests/fixtures/minimal_market_data --decision-timing tmp/mvp1_ev/anomaly_decision_timing.csv --out tmp/mvp1_simulation
python main.py run-mvp1-holdout-governance --out tmp/mvp1_governance --start-date 2024-01-01 --end-date 2024-12-15 --freeze-id protocol_freeze_v1
```

`run-mvp1-state` builds `anomaly_state_1m.csv` from normalized closed 1m candles and a strict `anomaly_events.csv` artifact boundary. It updates state only at times greater than or equal to `event_detection_time_ms`, and state features use only candles with `available_time_ms <= state_time_ms`.

`run-mvp1-future` builds `anomaly_future_paths.csv` from normalized closed 1m candles and a strict `anomaly_state_1m.csv` artifact boundary. Future outcomes use only candles with `available_time_ms > snapshot_time_ms`, and every row satisfies `feature_cutoff_time_ms <= snapshot_time_ms < future_start_time_ms`.

`run-mvp1-atlas` reads `anomaly_state_1m.csv` and `anomaly_future_paths.csv` through strict schema boundaries, joins them one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`, and writes:

- `anomaly_nature_atlas.csv`
- `anomaly_context_splits.csv`
- `anomaly_response_surfaces.csv`
- `anomaly_market_shock_groups.csv`

Atlas grouping uses state-only as-of fields. Coarse 30m response bins are descriptive atlas bins only, not calibrated labels, entry logic, exit logic, EV, PnL, or trade simulation. Market-shock groups are only simultaneous `snapshot_time_ms` groups and do not claim market-beta independence.

`run-mvp1-labels` reads the same state/future artifacts through strict boundaries, joins them one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`, and writes `anomaly_outcome_labels.csv` with `scenario_15m`, `scenario_30m`, and `scenario_60m`. Scenario values are descriptive future-nature targets for later walk-forward prediction calibration: `long_continuation`, `short_fade`, `static_or_chop`, `trap`, `unclear`, or explicit `missing_future`. State rows are used only for join and temporal audit; scenario assignment uses raw future path fields only.

`run-mvp1-prediction` reads `anomaly_state_1m.csv`, optional `anomaly_feature_matrix.csv`, and `anomaly_outcome_labels.csv` through strict boundaries, then runs weekly walk-forward CatBoost with one-vs-rest Isotonic calibration. OOS days inside a week use frozen weekly weights, best iteration, feature schema, and calibrators. It writes:

- `anomaly_oos_predictions.csv`
- `anomaly_calibration.csv`
- `anomaly_prediction_metrics.csv`
- `strategy_model_metadata.csv`
- `strategy_feature_importance.csv`
- `strategy_model_training_diagnostics.csv`

Predictions are calibrated future-nature probabilities, not trading commands. If a weekly model cannot be trained honestly, `strategy_model_training_diagnostics.csv` records the explicit skip reason.

`run-mvp1-controls` reads the same strict state/label boundaries and writes negative scientific controls:

- `anomaly_placebo_tests.csv`
- `anomaly_baseline_comparison.csv`

The controls include deterministic random-label, time-shuffled-label, symbol-shuffled-label placebos, universal simple baselines, and anomaly-specific rule/ablation baselines. Data-dependent ablations are deferred explicitly when required source features are absent; no proxy fallback is used.

EV and simplified pessimistic simulation are research artifacts only. Shadow live and production live remain intentionally absent.
