# anomaly_science

Clean scientific rebuild of anomaly-driven crypto research.

Legacy code is quarantined under `legacy_quarantine/old_repo` and is reference-only. New code must be implemented under `src/anomaly_science` without importing legacy modules.

Current target:

```text
MVP 1 — data quality, broad anomaly detector, online 1m state, future paths, protocol audit.
```

The project does not currently define a live trading strategy. Trading simulation and live execution come only after calibrated prediction, decision timing and EV checks.

## Current executable stage

Current MVP1 commands:

```bash
python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit
python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/mvp1_events
python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/anomaly_events.csv --out tmp/mvp1_state
python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/anomaly_state_1m.csv --out tmp/mvp1_future
```

`run-mvp1-state` builds `anomaly_state_1m.csv` from normalized closed 1m candles and a strict `anomaly_events.csv` artifact boundary. It updates state only at times greater than or equal to `event_detection_time_ms`, and state features use only candles with `available_time_ms <= state_time_ms`.

`run-mvp1-future` builds `anomaly_future_paths.csv` from normalized closed 1m candles and a strict `anomaly_state_1m.csv` artifact boundary. Future outcomes use only candles with `available_time_ms > snapshot_time_ms`, and every row satisfies `feature_cutoff_time_ms <= snapshot_time_ms < future_start_time_ms`.

This stage builds raw future paths only. It still does not build scenario labels, ML, PnL, trade simulation, shadow live, or production live.
