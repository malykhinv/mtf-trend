# anomaly_science

Clean scientific rebuild of anomaly-driven crypto research.

Legacy code is quarantined under `legacy_quarantine/old_repo` and is reference-only. New code must be implemented under `src/anomaly_science` without importing legacy modules.

Current target:

```text
MVP 1 — data quality, broad anomaly detector, online 1m state, future paths, protocol audit.
```

The project does not currently define a live trading strategy. Trading simulation and live execution come only after calibrated prediction, decision timing and EV checks.

## Current executable stage

Patch 3 adds the first runnable MVP1 boundary check:

```bash
python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit
```

This command only audits normalized CSV inputs, data quality, point-in-time universe construction, protocol status, run config, and the artifact manifest. It does not run detector, state building, future paths, prediction, trading, or live execution.

