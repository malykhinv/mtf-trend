# Anomaly Science Rebirth Plan

This document fixes the project-level execution order for the clean anomaly-science rebuild.

The old repository is quarantined as reference material only. New code must be implemented as isolated, typed, reusable modules under `src/anomaly_science`. Backtest, shadow live, and live must share the same domain contracts, state builders, feature builders, prediction interfaces, and decision modules.

## Non-negotiable rules

1. Work on one stage at a time.
2. Do not start a later stage until the previous stage has a passing Definition of Done.
3. New code must not import from `legacy_quarantine` or old root packages.
4. Legacy code may be read only as reference; useful practices must be reimplemented cleanly.
5. No silent fallbacks, monkeypatches, global suppressors, string-matching hacks, or post-processing fixes.
6. Features may use only data available at or before `snapshot_time`.
7. Future paths and labels may use only data strictly after `snapshot_time`.
8. Do not optimize for PnL before prediction, calibration, decision timing, and EV are proven.
9. Do not build separate live and backtest strategies; they must use shared core modules.

## Target package layout

```text
src/anomaly_science/
  contracts/      # typed domain contracts, schemas, temporal rules
  data/           # data ports, cache adapters, normalized readers, quality checks
  universe/       # point-in-time symbol universe
  events/         # broad anomaly detector
  state/          # online 1m state builder
  future/         # raw future paths and scenario labels
  features/       # feature catalog and feature builders
  atlas/          # nature atlas, response surfaces, context splits
  validation/     # walk-forward, purging, calibration, placebo
  decision/       # timing, RR feasibility, expected utility
  simulation/     # simplified pessimistic trade simulation
  live/           # shadow/live adapters using shared modules
  artifacts/      # artifact writers/readers and run manifests
  audit/          # protocol, data, temporal, and parity audits
```

## Execution order

```text
legacy quarantine
  -> contracts and artifact schemas
  -> data ports and cache adapters
  -> data quality gates
  -> point-in-time universe
  -> broad anomaly detector
  -> online 1m state builder
  -> future path builder
  -> protocol audit
  -> nature atlas
  -> walk-forward calibrated prediction
  -> decision timing / RR feasibility
  -> expected utility
  -> simplified pessimistic trade simulation
  -> shadow live
  -> production live
  -> legacy deletion
```

---

## Stage 0 — Legacy quarantine

Goal: move the mixed old repository into `legacy_quarantine/old_repo/` and start from a clean source root.

Definition of Done:

- `python main.py doctor` passes.
- `python -m compileall main.py src tests` passes.
- New code has no imports from legacy paths.
- One commit contains quarantine/bootstrap only, no methodology implementation.

Forbidden:

- Do not fix old strategy logic.
- Do not preserve old root packages as shared runtime code.
- Do not make new modules depend on old `research_tools`, `strategy`, `data`, `cli`, or similar root packages.

## Stage 1 — Contracts and artifact schemas

Goal: define stable typed contracts before implementing logic.

Required outputs:

- canonical timestamp/timezone policy;
- candle contracts for 1m and 5m OHLCV;
- closed 5m open-interest contract;
- liquidation-flow contract;
- symbol metadata and point-in-time universe contract;
- anomaly event contract;
- online state contract;
- future path contract;
- feature catalog contract;
- run manifest contract.

Definition of Done:

- Required MVP 1 artifact names and columns are fixed.
- Temporal invariants are explicit: `feature_cutoff_time <= snapshot_time`, `future_start_time > snapshot_time`.
- Unit tests validate schema and temporal invariants.

## Stage 2 — Data ports and cache adapters

Goal: build clean data access boundaries without importing legacy internals.

Required behavior:

- read 1m candles;
- read 5m candles;
- read closed 5m OI if available;
- read liquidation flow if available;
- read symbol metadata;
- report missing data explicitly.

Definition of Done:

- Data access uses replaceable ports/adapters.
- Missing data is represented as data quality condition, not market signal.
- Tests cover missing columns, bad timestamps, duplicates, and empty data.

## Stage 3 — Data quality gates

Goal: reject or mark invalid data before any research result is produced.

Required artifact:

```text
anomaly_data_quality.csv
```

Definition of Done:

- Checks cover missing candles, duplicates, bad timestamps, timezone alignment, non-positive prices, impossible returns, zero-volume anomalies, OI gaps, liquidation gaps, and listing gaps.
- Data quality returns deterministic PASS/WARN/FAIL rows.
- Critical FAIL prevents interpretation of research results.

## Stage 4 — Point-in-time universe

Goal: prevent survivorship bias and future-universe leakage.

Required artifact:

```text
symbol_universe_by_day.csv
```

Definition of Done:

- Static current-universe research is forbidden by audit.
- Delisted historical symbols are not excluded when data exists.
- Universe decisions are reproducible from saved artifacts.

## Stage 5 — Broad anomaly detector

Goal: detect broad market activity moments, not trade setups.

Required artifact:

```text
anomaly_events.csv
```

Definition of Done:

- Detector uses only data available at detection time.
- Detector does not know future outcome.
- Detector is broad enough to include spikes, bursts, grind pumps, volume-only anomalies, range expansions, breakouts, noisy pumps, session bursts, and market-wide impulses.

## Stage 6 — Online 1m state builder

Goal: update state every minute after an anomaly is detected.

Required artifact:

```text
anomaly_state_1m.csv
```

Definition of Done:

- Every state row has `event_id`, `symbol`, `state_time`, `snapshot_time`, `feature_cutoff_time`.
- Running high/low are strictly as-of-state, not final future values.
- Tests prove future candles cannot affect state rows.

## Stage 7 — Future paths and labels

Goal: save raw future outcomes first; labels are derived later from raw outcomes.

Required artifact:

```text
anomaly_future_paths.csv
```

Definition of Done:

- Future windows start strictly after `snapshot_time`.
- Raw outcomes exist before scenario labels.
- Multi-horizon paths are saved for at least 15m, 30m, 60m, and 120m where data exists.

## Stage 8 — Protocol audit

Goal: make temporal and data-contract violations impossible to ignore.

Required artifact:

```text
anomaly_protocol_audit.csv
```

Definition of Done:

- Audit checks feature/label time separation, train/test separation, closed 5m OI usage, point-in-time universe, liquidation timestamp availability, and no static future universe.
- Any FAIL makes the run non-interpretable.

## Stage 9 — Nature atlas

Goal: map anomaly types and future behaviors before ML and trading.

Required artifacts:

```text
anomaly_nature_atlas.csv
anomaly_response_surfaces.csv
anomaly_context_splits.csv
anomaly_market_shock_groups.csv
```

Definition of Done:

- Atlas describes hypotheses only; it is not treated as proof of edge.
- Results are sliced by price shape, speed, volume, flow, OI, liquidation regime, session, and market context.

## Stage 10 — Walk-forward prediction and calibration

Goal: predict future nature with calibrated OOS probabilities.

Required artifacts:

```text
anomaly_oos_predictions.csv
anomaly_calibration.csv
anomaly_feature_stability.csv
anomaly_placebo_tests.csv
research_ledger.csv
holdout_access_log.csv
```

Definition of Done:

- All fit operations occur only inside train folds.
- Purging/embargo is applied for overlapping labels.
- Calibration, placebo, and baseline comparisons pass.

## Stage 11 — Decision timing and expected utility

Goal: prove that confidence appears before RR is gone.

Required artifacts:

```text
anomaly_decision_timing.csv
anomaly_expected_utility.csv
```

Definition of Done:

- Signals are evaluated as wait/no_trade/enter_long/enter_short/hold/exit choices.
- EV is compared against wait and no_trade.
- Late correct predictions are not counted as tradable edge.

## Stage 12 — Simplified pessimistic trade simulation

Goal: test whether calibrated predictions and timing can become positive EV after basic costs.

Required artifacts:

```text
anomaly_trade_simulation.csv
anomaly_position_state.csv
anomaly_exit_policy_oos.csv
anomaly_mae_mfe.csv
```

Definition of Done:

- Entry uses next 1m open only as reference price.
- Entry is pessimised by fixed/ATR slippage penalty.
- Fees are included.
- The simulator uses the same contracts and decision modules intended for shadow/live.

## Stage 13 — Shadow live

Goal: verify live/backtest parity without money.

Definition of Done:

- Shadow live uses the same state, feature, prediction, and decision modules as research.
- Differences are written as audit artifacts, not hidden in logs.

## Stage 14 — Production live

Goal: production execution only after scientific validity and shadow parity.

Definition of Done:

- Execution reliability, latency, partial fills, reconciliation, kill switch, and account safety are implemented separately from research logic.

## Stage 15 — Legacy deletion

Goal: delete `legacy_quarantine` once the new system fully replaces useful legacy behavior.

Definition of Done:

- No required workflow depends on legacy reference files.
- Useful legacy lessons are reimplemented cleanly in the new architecture.
- `legacy_quarantine` is removed in one explicit cleanup commit.
