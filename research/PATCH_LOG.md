# Patch log

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
- Keep atlas grouping bins derived from state-only as-of fields; use future fields only for descriptive response summaries.

Validation for this proposed patch:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/check_patch7/events`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/check_patch7/events/anomaly_events.csv --out tmp/check_patch7/state`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/check_patch7/state/anomaly_state_1m.csv --out tmp/check_patch7/future`
- `python main.py run-mvp1-atlas --state tmp/check_patch7/state/anomaly_state_1m.csv --future tmp/check_patch7/future/anomaly_future_paths.csv --out tmp/check_patch7/atlas`

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
