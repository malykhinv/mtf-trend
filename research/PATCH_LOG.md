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
