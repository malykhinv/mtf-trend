# Commands

Bootstrap check:

```bash
python main.py doctor
```

MVP1 data audit:

```bash
python main.py run-mvp1-data-audit --input tests/fixtures/minimal_market_data --out tmp/mvp1_audit
```

MVP1 broad anomaly events:

```bash
python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/mvp1_events
```

MVP1 online 1m anomaly state:

```bash
python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/anomaly_events.csv --out tmp/mvp1_state
```

MVP1 raw future paths:

```bash
python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/anomaly_state_1m.csv --out tmp/mvp1_future
```

MVP1 descriptive anomaly atlas:

```bash
python main.py run-mvp1-atlas --state tmp/mvp1_state/anomaly_state_1m.csv --future tmp/mvp1_future/anomaly_future_paths.csv --out tmp/mvp1_atlas
```

MVP1 descriptive future-nature outcome labels:

```bash
python main.py run-mvp1-labels --state tmp/mvp1_state/anomaly_state_1m.csv --future tmp/mvp1_future/anomaly_future_paths.csv --out tmp/mvp1_labels
```

Compile check:

```bash
python -m compileall main.py src tests
```

Tests:

```bash
python -m pytest tests
```
