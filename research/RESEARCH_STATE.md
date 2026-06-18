# Research state

Current phase: anomaly_science rebirth.

Active rule:
- `legacy_quarantine` is reference-only.
- New code must not import legacy modules.
- Active research strategy is the anomaly family documented in `docs/strategies/anomaly_strategy.md`.
- First target is MVP 1: dataset/state/future-path/audit, not live trading.
- Current implemented slice after applying Patch 10: data source boundary, data quality, point-in-time universe skeleton, broad anomaly events, online 1m anomaly state, raw future paths, descriptive anomaly nature atlas, descriptive future-nature outcome labels, first walk-forward calibrated baseline prediction, and placebo/control tests.
- Future paths remain raw outcomes.
- Atlas outcome bins are descriptive discovery bins only, not decision rules, EV, PnL, or trade simulation.
- Outcome labels are descriptive scenario targets for walk-forward prediction calibration, not trading labels.
- Placebo/control rows are negative scientific controls; passing or failing them does not create a trade signal.
- Decision timing, EV, trade simulation, shadow live, and production live are still intentionally absent.

Current local base commit before these working-tree fixes: `bcf39f51`, verified with `git rev-parse --short HEAD`.
Last local validation on 2026-06-18:
- `.venv\Scripts\python.exe main.py doctor`
- `.venv\Scripts\python.exe -m compileall main.py src tests`
- `.venv\Scripts\python.exe -m pytest tests`
- full MVP1 fixture pipeline through data audit, events, state, future, features, feature matrix, atlas, labels, prediction, and controls.


Pending local patches from uploaded snapshot `project_20260618_115220.zip`:
- `perf: remove Binance Vision cache per-block IO amplification`
  - Fixes Binance Vision cache per-block ledger/disk-scan IO amplification.
  - Excludes top-level `tmp/` generated artifacts from project zip creation.
- `perf: prune Binance Vision cache work by requested date range`
  - Skips symbols without kline archives in the requested date range.
  - Writes `metadata/skipped_symbols.csv`.
  - Adds `metadata/symbol_completion.csv` and deletes completed per-symbol parts after final parquet compaction.
- `perf: exclude Binance delivery contracts from perpetual cache`
  - Unconditionally skips `*_YYMMDD` delivery/fixed-date contracts.
  - Records skipped delivery contracts with `reason=delivery_contract_excluded`.
  - Adds no new CLI flag; the cache command remains compact.
- Patch status: PROPOSED until applied and verified locally.
