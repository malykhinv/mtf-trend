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

## feat: add MVP1 walk-forward calibrated prediction

Status: PROPOSED; patch generated on top of Patch 8 applied to uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add `run-mvp1-prediction` CLI.
- Read `anomaly_state_1m.csv` and `anomaly_outcome_labels.csv` through strict schema boundaries.
- Join state/label rows one-to-one on `event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms`.
- Run daily prequential walk-forward prediction with purge: `train_snapshot_time_ms + H_max <= test_day_start_ms`.
- Use a state-bin empirical calibrated baseline over state-only fields; labels are used only as train targets and OOS evaluation targets.
- Write `anomaly_oos_predictions.csv`, `anomaly_calibration.csv`, and `anomaly_prediction_metrics.csv`.
- Keep this layer out of trading: no thresholds, EV, PnL, entry/exit, trade simulation, shadow live, or production live.

Validation for this proposed patch:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/check_patch9/events`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/check_patch9/events/anomaly_events.csv --out tmp/check_patch9/state`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/check_patch9/state/anomaly_state_1m.csv --out tmp/check_patch9/future`
- `python main.py run-mvp1-labels --state tmp/check_patch9/state/anomaly_state_1m.csv --future tmp/check_patch9/future/anomaly_future_paths.csv --out tmp/check_patch9/labels`
- `python main.py run-mvp1-prediction --state tmp/check_patch9/state/anomaly_state_1m.csv --labels tmp/check_patch9/labels/anomaly_outcome_labels.csv --out tmp/check_patch9/prediction`

## feat: add MVP1 placebo/control tests

Status: PROPOSED; patch generated on top of Patch 9 applied to uploaded snapshot `project_20260617_104034.zip`; GitHub head not checked.

Intent:
- Add `run-mvp1-controls` CLI.
- Read `anomaly_state_1m.csv` and `anomaly_outcome_labels.csv` through strict schema boundaries reused from the prediction layer.
- Run negative placebo checks: deterministic random-label, time-shuffled-label, and symbol-shuffled-label controls.
- Run simple non-trading baselines: global-prior-only, session-only, event-time-only, and price-path-only.
- Explicitly defer `volume_only` until real volume columns exist in `anomaly_state_1m.csv`; do not proxy volume from unrelated fields.
- Write `anomaly_placebo_tests.csv` and `anomaly_baseline_comparison.csv`.
- Keep this layer out of trading: no thresholds, EV, PnL, entry/exit, trade simulation, shadow live, or production live.

Validation for this proposed patch:
- `python main.py doctor`
- `python -m compileall -q main.py src tests`
- `python -m pytest -q`
- `python main.py run-mvp1-events --input tests/fixtures/minimal_market_data --out tmp/check_patch10/events`
- `python main.py run-mvp1-state --input tests/fixtures/minimal_market_data --events tmp/check_patch10/events/anomaly_events.csv --out tmp/check_patch10/state`
- `python main.py run-mvp1-future --input tests/fixtures/minimal_market_data --state tmp/check_patch10/state/anomaly_state_1m.csv --out tmp/check_patch10/future`
- `python main.py run-mvp1-labels --state tmp/check_patch10/state/anomaly_state_1m.csv --future tmp/check_patch10/future/anomaly_future_paths.csv --out tmp/check_patch10/labels`
- `python main.py run-mvp1-prediction --state tmp/check_patch10/state/anomaly_state_1m.csv --labels tmp/check_patch10/labels/anomaly_outcome_labels.csv --out tmp/check_patch10/prediction`
- `python main.py run-mvp1-controls --state tmp/check_patch10/state/anomaly_state_1m.csv --labels tmp/check_patch10/labels/anomaly_outcome_labels.csv --out tmp/check_patch10/controls`

## perf: remove Binance Vision cache per-block IO amplification

Status: PROPOSED; patch generated from uploaded snapshot `project_20260618_115220.zip`; GitHub head not checked in this environment.

Intent:
- Fix the corrupt previous patch by regenerating a clean unified diff from the current uploaded project snapshot.
- Stop rewriting the full Binance Vision block ledger on every processed block; append block records and keep last-record-wins resume semantics.
- Sample disk usage in progress output instead of recursively walking `.output/market` on every progress tick.
- Avoid expanding a missing monthly archive into daily fallback probes when the S3 archive file index already proves no daily kline archives exist.
- Keep tqdm output shorter and terminal-width aware.
- Exclude top-level `tmp/` generated run artifacts from `zip_project.py` output so project zips stay small.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_zip_project.py tests/test_binance_vision_cache.py`

## perf: prune Binance Vision cache work by requested date range

Status: PROPOSED; patch generated on top of `perf: remove Binance Vision cache per-block IO amplification`; GitHub head not checked in this environment.

Intent:
- Skip symbols whose Binance Vision kline archives do not intersect the requested cache date range.
- Reuse the preloaded archive file index during per-symbol processing instead of rebuilding it after filtering.
- Write `metadata/skipped_symbols.csv` and record skipped no-klines symbols in `manifest.json`.
- Add a `symbol_completion.csv` ledger so a completed final per-symbol parquet can safely replace block parts on subsequent runs.
- Remove `{out_dir}_parts/{symbol}` after final parquet compaction succeeds to keep SSD usage bounded.
- Preserve block-level resume while a symbol is still in progress; parts are deleted only after final parquet and completion ledger are written.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_binance_vision_cache.py tests/test_zip_project.py`


## perf: exclude Binance delivery contracts from perpetual cache

Status: PROPOSED; patch generated on top of `perf: prune Binance Vision cache work by requested date range`; GitHub head not checked in this environment.

Intent:
- Exclude Binance delivery/fixed-date symbols matching `*_YYMMDD` from the USD-M perpetual cache unconditionally.
- Keep the cache command compact: no `--include-delivery-contracts` or other delivery-specific flag is introduced.
- Record excluded delivery contracts in `metadata/skipped_symbols.csv` with `reason=delivery_contract_excluded`.
- Apply delivery filtering before date-range archive probing so old fixed-date contracts do not trigger needless S3 listing/download work.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_binance_vision_cache.py tests/test_zip_project.py`


## perf: make Binance Vision cache startup visible

Status: PROPOSED; patch generated on top of `perf: exclude Binance delivery contracts from perpetual cache`; GitHub head not checked in this environment.

Intent:
- Emit immediate startup stage logs before symbol discovery, delivery filtering, and archive range preflight.
- Show a dedicated `Binance Vision preflight` progress bar while archive file indexes are checked for requested-date overlap.
- Move compact-command network defaults to the optimized values previously used manually: `timeout=45`, `connect-timeout=8`, `retries=2`.
- Record effective network defaults in `manifest.json` for reproducibility.
- Keep the launch command compact; no new CLI flags are introduced.

Validation for this proposed patch:
- `python -m compileall -q main.py src tests zip_project.py`
- `python -m pytest -q tests/test_binance_vision_cache.py tests/test_binance_vision_cache_delivery_symbols.py tests/test_binance_vision_cache_startup.py tests/test_zip_project.py`
