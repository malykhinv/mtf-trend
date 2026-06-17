# anomaly_science

Clean scientific rebuild of anomaly-driven crypto research.

Legacy code is quarantined under `legacy_quarantine/old_repo` and is reference-only. New code must be implemented under `src/anomaly_science` without importing legacy modules.

Current target:

```text
MVP 1 — data quality, broad anomaly detector, online 1m state, future paths, protocol audit.
MVP 2 atlas slice — descriptive anomaly nature atlas over MVP1 state/future artifacts.
MVP 3 label/prediction/control slice — descriptive future-nature scenario labels, first daily prequential calibrated baseline prediction, and placebo/control checks.
```

The project does not currently define a live trading strategy. Trading simulation and live execution come only after calibrated prediction, decision timing and EV checks.

## Current executable stage

Current MVP1 commands:

```bash
python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit
python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/mvp1_events
python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/anomaly_events.csv --out tmp/mvp1_state
python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/anomaly_state_1m.csv --out tmp/mvp1_future
python main.py run-mvp1-atlas --state tmp/mvp1_state/anomaly_state_1m.csv --future tmp/mvp1_future/anomaly_future_paths.csv --out tmp/mvp1_atlas
python main.py run-mvp1-labels --state tmp/mvp1_state/anomaly_state_1m.csv --future tmp/mvp1_future/anomaly_future_paths.csv --out tmp/mvp1_labels
python main.py run-mvp1-prediction --state tmp/mvp1_state/anomaly_state_1m.csv --labels tmp/mvp1_labels/anomaly_outcome_labels.csv --out tmp/mvp1_prediction
python main.py run-mvp1-controls --state tmp/mvp1_state/anomaly_state_1m.csv --labels tmp/mvp1_labels/anomaly_outcome_labels.csv --out tmp/mvp1_controls
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

`run-mvp1-prediction` reads `anomaly_state_1m.csv` and `anomaly_outcome_labels.csv` through strict boundaries, then runs a daily prequential walk-forward calibrated baseline. Each test day is predicted only from earlier rows after purging the configured horizon, and the model uses state-only bins from `anomaly_state_1m.csv`. It writes:

- `anomaly_oos_predictions.csv`
- `anomaly_calibration.csv`
- `anomaly_prediction_metrics.csv`

The baseline is an empirical calibrated frequency model over state bins, not trading logic. It estimates scenario probabilities for scientific predictability checks only.

`run-mvp1-controls` reads the same strict state/label boundaries and writes negative scientific controls:

- `anomaly_placebo_tests.csv`
- `anomaly_baseline_comparison.csv`

The controls include deterministic random-label, time-shuffled-label, and symbol-shuffled-label placebos plus simple baselines such as global-prior, session-only, event-time-only, and price-path-only. `volume_only` is explicitly deferred until real volume features exist in `anomaly_state_1m.csv`; no proxy volume fallback is used.

This stage still does not build entry logic, EV, PnL, trade simulation, shadow live, or production live.
