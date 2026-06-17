# Patch log

## refactor: quarantine legacy code and bootstrap anomaly science core

Status: PROPOSED

Intent:
- Move the old repository tree into `legacy_quarantine/old_repo` as reference-only code.
- Add a clean `src/anomaly_science` bootstrap.
- Add tests preventing accidental imports from legacy roots.

Validation:
- `python main.py doctor`
- `python -m compileall main.py src tests`
- `python -m pytest tests`

## feat: add data source boundary and MVP data quality gates

Status: PROPOSED

Intent:
- Add explicit normalized CSV data-source boundary.
- Add MVP1 data-quality checks without proxy fallbacks.
- Add point-in-time universe-by-day skeleton from dated source rows.
- Add `run-mvp1-data-audit` CLI that writes data quality, universe, protocol audit, run config, and manifest artifacts.

Validation:
- `python main.py doctor`
- `python -m compileall main.py src tests`
- `python -m pytest tests/test_bootstrap.py tests/test_no_legacy_imports.py tests/test_project_layout.py tests/test_contracts.py tests/test_artifact_schemas.py tests/test_data_audit.py`
- `python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit`


## feat: build MVP1 online anomaly state

Status: PROPOSED

Intent:
- Add strict `anomaly_events.csv` boundary.
- Build `anomaly_state_1m.csv` from closed 1m candles using only data available as-of each state time.
- Keep future paths, labels, ML, PnL, and trade simulation out of the state layer.

Validation:
- `python main.py doctor`
- `python -m compileall main.py src tests`
- `python -m pytest tests`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/anomaly_events.csv --out tmp/mvp1_state`

## feat: build MVP1 raw future paths

Status: PROPOSED

Intent:
- Add strict `anomaly_state_1m.csv` boundary.
- Build `anomaly_future_paths.csv` from closed 1m candles using only candles strictly after each snapshot.
- Preserve structural-break fields as explicit nulls until structural features exist; do not proxy them from running lows.
- Keep scenario labels, ML, PnL, trade simulation, shadow live, and production live out of this layer.

Validation:
- `python main.py doctor`
- `python -m compileall main.py src tests`
- `python -m pytest tests`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/anomaly_state_1m.csv --out tmp/mvp1_future`
