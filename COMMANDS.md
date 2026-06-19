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
python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/mvp1_events/strategy_events.csv --out tmp/mvp1_state
```

MVP1 raw future paths:

```bash
python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/strategy_state_1m.csv --out tmp/mvp1_future
```

MVP1 feature catalog:

```bash
python main.py run-mvp1-features --out tmp/mvp1_features
```

MVP1 strategy registry truth table:

```bash
python main.py run-mvp1-strategy-registry --out tmp/mvp1_strategy_registry
```

`strategy_registry.csv` contains executable variants only. `strategy_implementation_status.csv` also lists specified-only variants that must fail explicitly until implemented.

MVP1 as-of feature matrix:

```bash
python main.py run-mvp1-feature-matrix --input tests/fixtures/minimal_market_data --state tmp/mvp1_state/strategy_state_1m.csv --out tmp/mvp1_feature_matrix
```

MVP1 descriptive anomaly atlas:

```bash
python main.py run-mvp1-atlas --state tmp/mvp1_state/strategy_state_1m.csv --future tmp/mvp1_future/strategy_future_paths.csv --features tmp/mvp1_feature_matrix/strategy_feature_matrix.csv --out tmp/mvp1_atlas
```

MVP1 descriptive future-nature outcome labels:

```bash
python main.py run-mvp1-labels --state tmp/mvp1_state/strategy_state_1m.csv --future tmp/mvp1_future/strategy_future_paths.csv --out tmp/mvp1_labels
```


Low-level target-horizon commands use the same horizon contract as `run-research`: `--horizon-minutes` must be one of `15/30/60/120/180`, and the selected `--strategy-name` must be an executable registry variant that semantically allows that horizon. Omitting `--strategy-name` resolves to `broad_anomaly_v1_h{horizon}` for compatibility, so `120/180` are rejected until a matching strategy is implemented.

`run-research` defaults to `--research-mode is`, which excludes the final holdout from downstream research stages before data audit/events/state/features/prediction. `--research-mode frozen_holdout` requires `--protocol-freeze-id` and records an explicit approved holdout access row. `run-research` writes `stages/forensic_audit/strategy_protocol_audit.csv` after simulation and exits non-zero if the independent forensic audit has any `FAIL` row. `research_run_summary.csv` records holdout mode plus forensic audit status/counts.

MVP1 weekly CatBoost+Isotonic calibrated prediction:

```bash
python main.py run-mvp1-prediction --state tmp/mvp1_state/strategy_state_1m.csv --labels tmp/mvp1_labels/strategy_outcome_labels.csv --features tmp/mvp1_feature_matrix/strategy_feature_matrix.csv --out tmp/mvp1_prediction --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
```

MVP1 placebo/control checks:

```bash
python main.py run-mvp1-controls --state tmp/mvp1_state/strategy_state_1m.csv --labels tmp/mvp1_labels/strategy_outcome_labels.csv --features tmp/mvp1_feature_matrix/strategy_feature_matrix.csv --out tmp/mvp1_controls --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
```

MVP1 decision timing / EV:

```bash
python main.py run-mvp1-expected-value --state tmp/mvp1_state/strategy_state_1m.csv --labels tmp/mvp1_labels/strategy_outcome_labels.csv --predictions tmp/mvp1_prediction/strategy_oos_predictions.csv --out tmp/mvp1_ev --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
```

MVP1 pessimistic trade simulation:

```bash
python main.py run-mvp1-trade-simulation --input tests/fixtures/minimal_market_data --decision-timing tmp/mvp1_ev/strategy_decision_timing.csv --out tmp/mvp1_simulation --strategy-name broad_anomaly_v1_h30 --horizon-minutes 30
```

MVP1 holdout governance:

```bash
python main.py run-mvp1-holdout-governance --out tmp/mvp1_governance --start-date 2024-01-01 --end-date 2024-12-15 --freeze-id protocol_freeze_v1
python main.py run-mvp1-holdout-governance --out tmp/mvp1_governance_holdout --start-date 2024-01-01 --end-date 2024-12-15 --freeze-id protocol_freeze_v1 --research-mode frozen_holdout --holdout-access-artifact manual_review
```


Binance Vision USD-M Futures cache build:

```bash
python main.py build-binance-vision-cache --days 380
```

Smoke test on a small subset:

```bash
python main.py build-binance-vision-cache --symbols BTCUSDT,ETHUSDT --days 7 --overwrite
```

Compile check:

```bash
python -m compileall main.py src tests
```

Tests:

```bash
python -m pytest tests
```

Apply newest generated patch and commit it:

```bash
python apply_latest_patch.py
```

Dry-run patch application without modifying the working tree:

```bash
python apply_latest_patch.py --dry-run
```

Patch files are read from `.patches/*.patch`. The commit message is the patch filename without `.patch`, so use filenames like `Add technical noise shock gate for raw 1m timestamp gaps.patch`.
