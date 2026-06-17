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

