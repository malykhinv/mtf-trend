# Research state

Current phase: anomaly_science rebirth.

Active rule:
- `legacy_quarantine` is reference-only.
- New code must not import legacy modules.
- Active research strategy is the anomaly family documented in `docs/strategies/anomaly_strategy.md`.
- First target is MVP 1: honest research pipeline through calibrated prediction, decision timing, EV, pessimistic simulation, controls, and holdout governance; not live trading.
- Current implemented slice: data source boundary, data quality, point-in-time universe skeleton, broad anomaly events via BaseStrategy `generate_triggers`, online 1m anomaly state, raw future paths through 180m, feature catalog/matrix, descriptive strategy nature atlas, descriptive future-nature outcome labels for 15/30/60/120/180m, weekly frozen walk-forward prediction with active-strategy `H_max` purge, calibration artifacts, placebo/control tests, decision timing with EV, simplified pessimistic trade simulation, holdout governance artifacts, and canonical `strategy_*` artifacts with `anomaly_*` compatibility aliases.
- Future paths remain raw outcomes.
- Atlas outcome bins are descriptive discovery bins only, not decision rules, EV, PnL, or trade simulation.
- Outcome labels are descriptive scenario targets for walk-forward prediction calibration, not trading labels.
- Placebo/control rows are negative scientific controls; passing or failing them does not create a trade signal.
- Decision timing and EV are research decision artifacts, not live trade commands.
- Trade simulation is simplified and pessimistic; it is not shadow live or production execution.
- Shadow live and production live are still intentionally absent.
- Methodology/strategy completion is tracked in `research/METHODOLOGY_GAP_LEDGER.md`; do not claim research completeness while that ledger has in-scope `MISSING` rows or unaudited `PARTIAL` rows.

Current local base commit before the active canonical artifact patch: `f61c11e`, verified with `git rev-parse --short HEAD`.
Last local validation on 2026-06-19:
- `.venv\Scripts\python.exe -m pytest tests\test_artifact_schemas.py tests\test_data_audit.py tests\test_events_detector.py tests\test_state_builder.py tests\test_future_paths.py tests\test_feature_catalog.py tests\test_feature_matrix.py tests\test_atlas.py tests\test_labels.py tests\test_prediction.py tests\test_controls.py tests\test_decision_expected_value.py tests\test_trade_simulation.py tests\test_holdout_governance.py tests\test_strategy_registry.py tests\test_research_run.py`
- `.venv\Scripts\python.exe -m compileall src main.py tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest`


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
- `perf: make Binance Vision cache startup visible`
  - Adds immediate startup stage logs and preflight progress for archive range filtering.
  - Bakes optimized network defaults into the compact cache command: timeout 45s, connect timeout 8s, retries 2.
- `perf: replace per-symbol Binance Vision preflight with run-level klines index`
  - Replaces per-symbol archive range preflight with one run-level monthly klines index.
  - Avoids 873× symbol-index S3 listing during startup.
  - Keeps the cache command unchanged.
- Patch status: PROPOSED until applied and verified locally.
Current GitHub branch check on 2026-06-18:
- Branch: `codex/pno-anomaly-continuation-lab`.
- Head: `e1a4e19a14a6a735a650aaec50f0f41f65da57eb`.
- Combined status: no status checks returned.
- Uploaded snapshot `project_20260618_124949.zip` contains newer local Binance Vision cache startup changes than that GitHub head; this patch is generated against the uploaded snapshot, not directly against GitHub head.

New pending patch from uploaded snapshot `project_20260618_124949.zip`:
- `perf: replace Binance Vision root archive scan with scoped preflight`
  - Replaces the silent recursive S3 root scan with scoped per-symbol/month kline preflight.
  - Adds visible archive-index progress before symbol block processing starts.
  - Preserves current-month daily-only symbols via bounded daily HEAD probes only when monthly overlap is absent.
  - Keeps optional metrics/liquidation archive probing behavior unchanged.
  - Patch status: PROPOSED until applied and verified locally.
