## 2026-06-04 - P503 fast seed-stage planner pruning

Status: APPLIED locally / UNKNOWN commit. Builds on P502.

Purpose: reduce obviously wasted exact LTF confirm fetch/processing before attempting any longer runner discovery run.

Changes:

- Signal-entry planning now applies fast known-at-seed checks before confirm/next-open fetch: exact seed return, exact internal seed LTF sustained-flow shape, and closed-context seed quote/trade ratios.
- These checks are data-loading guards only. They use seed/pre-seed data already available after pre-entry fetch and never use future labels, exits, PnL, post-entry highs/lows, or selected-trade survival.
- Pre-entry planning can narrow a 2xHTF pair into exact seed windows when 1m upper bounds prove which starts can still pass seed+confirm prerequisites. If 1m coverage is incomplete, it falls back to the old full-pair superset.
- Artifacts expose the planner model/reason and exact seed window counts.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Diagnostics:

```text
2d runtime: 7.21h total; linear 30d estimate about 108h.
5m_15s fetched aggTrade rows: ~19.3M total, ~18.4M pre-entry.
5m_15s fetched LTF windows: ~777.8h total, ~736.0h pre-entry.
50-symbol 5m_15s signal-entry dry sample: 232 passing windows after fast seed-stage gates; major safe rejects were seed return, seed LTF flow shape, seed trade ratio and seed quote ratio.
50-symbol 5m_15s pre-entry merged-window sample: only ~17% merged-window reduction, so this patch is not a full 10x solution.
```

Risk:

```text
Low strategy-bias risk if treated as a data-loading guard: terminal seed rejects are based on known-at-seed data. Medium operational risk because 10x still requires a data-source/path change; this patch should not be sold as making REST-based 30d practical by itself.
```

## 2026-06-03 - P502 throttled Binance aggTrades targeted fetch

Status: APPLIED locally / UNKNOWN commit. Builds on P501/P500.

Purpose: make targeted runner discovery data loading usable after the 1d audit exposed mass `HTTP 418`/`HTTP 429` fetch failures.

Changes:

- Added a process-wide Binance aggTrades request limiter shared by direct target-LTF fetch and legacy 1s aggTrades backfill.
- Added bounded retry/backoff for Binance `HTTP 429` and `HTTP 418`, respecting `Retry-After` when present and surfacing a clear error after bounded attempts.
- Kept fetch failures visible in `targeted_ltf_fetch.csv`/materialize rows instead of converting unavailable data into fake empty candles.
- Reduced default `targeted_fetch_workers` from 4 to 2; planning remains parallel, but network requests are globally paced.
- Added regression coverage proving a rate-limit response is retried before a targeted aggTrades fetch fails.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
Low strategy-bias risk, medium operational risk. The patch slows network fetch enough to avoid bans and retries transient throttles; it does not alter selection thresholds or hide missing data. A 30d run still requires a fresh 1d smoke proving fetch errors are near zero.
```

## 2026-06-03 - P500 runner discovery cache reuse and staged acceleration

Status: APPLIED locally / UNKNOWN commit. Builds on P499.

Purpose: stop 1d `5m_30s` runner discovery from re-downloading/recomputing the same targeted LTF work for hours.

Changes:

- Final targeted decision pass now consumes exact seed timestamps from `signal_entry planned` rows, not all broad `pre_entry` pair timestamps.
- Targeted mode skips legacy entry-window research artifact generation for every rejected seed; selected-signal post-entry replay remains separate.
- Added bounded `targeted_plan_workers` and `targeted_fetch_workers`; targeted aggTrades fetch is parallel by symbol while preserving sequential writes inside each symbol.
- Fixed trusted target-LTF cache subtraction to see direct aggTrades `delta/*.parquet`.
- Added compact `aggtrade_direct_target_ltf_coverage.parquet` sidecar indexes so coverage checks do not repeatedly scan many small delta parquet files.
- Empty aggTrades windows now write verified coverage index buckets instead of being fetched forever.
- `_resolve_end_timestamp_ms` now reads only timestamp columns from HTF base/delta cache.
- Reduced aggTrades HTTP timeout from 30s to 10s so network stalls become visible fetch errors instead of hanging the run.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Diagnostics:

```text
old repeated coverage view: 3831/3831 pre-entry windows missing, 638.5h fetch required
after indexing existing delta: 3491 covered, 523 missing intervals, about 4.93h fetch required
empty-window fetch smoke: 12 windows, ~2.5s fetch, empty coverage index rows written
fast end timestamp lookup: 594 symbols in ~14.2s
```

Risk:

```text
Medium operational risk, low strategy-bias risk. The patch changes data-loading/cache bookkeeping and removes redundant targeted-mode diagnostics, not PumpDecisionCore thresholds. Empty aggTrades windows are marked covered in the coverage index but no synthetic OHLCV candles are created; this is conservative for flow signals because no-trade windows cannot create real flow.
```

## 2026-06-03 - P499 fast conservative signal-entry planner

Status: APPLIED locally / UNKNOWN commit. Builds on P498 and supersedes P494's expensive shared-core seed-stage prefilter inside the fetch planner.

Purpose: stop 1d `5m_30s` discovery from spending hours in `targeted signal-entry plan`.

Changes:

- Replaced full `_collect_symbol_candidates(...)` in signal-entry planning with a lightweight exact rolling seed collector that does not compute future labels, OI, prior-spike diagnostics, setup nature, exits, or PnL fields.
- Replaced full seed-stage core prefilter in the signal-entry planner with a context-free mandatory seed-return gate only.
- Seeds below `ROLLING_SEED_MIN_HTF_RETURN_PCT` remain visible as `not_planned_seed_stage_terminal_reject`.
- Seeds that pass return are treated as possible and get short confirm/next-open LTF fetch; the real shared core still decides them in the normal decision pass.
- Made runner discovery progress output encoding-safe for Windows/non-ASCII symbols.
- Optimized closed 1m context construction by avoiding repeated strict-window DataFrame scans and adding per-symbol context cache for the later decision pass.
- Seed-stage core no longer computes prior-spike/hash payloads for seed-stage-only checks; full seed+confirm decisions still compute snapshot hashes and category features.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Benchmark:

```text
JCT/USDT:USDT 1d 5m_30s signal-entry plan: about 29.6s before planner simplification -> about 1.5s after return-only planner in normal timing.
First 50 cached symbols: pre-entry plan 16.5s, signal-entry plan 54.4s, 595 planned short confirm windows.
```

Risk:

```text
Low-to-medium and conservative. The planner now fetches more short confirm windows than P494 because it no longer runs the full seed-stage core before fetch. This increases LTF fetch volume, but does not create optimistic bias: exact shared-core decisions still happen after data is loaded, and no future labels/exits/PnL are used for fetch selection.
```

## 2026-06-02 - P498 closed cheap baseline context and visible seed-stage rejects

Status: APPLIED locally / UNKNOWN commit. Builds on P497 and supersedes its exact seed-aligned HTF-context assumption for rolling offsets.

Purpose: keep targeted rolling discovery honest and fast before retrying long `5m_30s`/`3m_30s` backtests.

Changes:

- `PumpDecisionCore` version is now `p498_closed_baseline_context`.
- Pre-seed context validation accepts a fully closed HTF-width baseline context ending up to 60s before rolling `seed_open_ms`, instead of requiring the long context to end exactly at every 30s/15s rolling seed start.
- Backtest discovery builds long context from cheap fully closed 1m candles when rolling seeds are offset from calendar HTF boundaries; exact aggTrade LTF is still used for seed and confirm candles only.
- Signal-entry targeted fetch remains the short confirm/next-open window. It does not fetch 24h of 30s/15s context.
- Seed-stage terminal reject rows are preserved in `htf_ltf_runner_targeted_ltf_plan.csv` even when they produce zero signal-entry fetch windows.
- Progress output uses ASCII `aggTrades->30s` to avoid Windows console encoding crashes.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
```

Additional actual smoke:

```text
1 symbol JCT/USDT:USDT, 1d, 5m_30s, max_events_per_symbol=5
decision ledger: 89 rejected, 0 data_dependency_not_ready
top reasons: seed_htf_return_below_min, ltf_confirm_return_below_min, seed_ltf_flow_not_sustained
targeted fetch windows: short 420s-900s windows, not 24h LTF context
```

Risk:

```text
Medium but explicit. The long baseline context may lag rolling seed open by up to 60s when built from cheap 1m candles. This avoids impossible 24h subminute downloads and does not use future data, but it is no longer exact LTF-seed-aligned context. Snapshot hashes change through the core version.
```

## 2026-06-02 - P497 HTF context warmup for targeted runner discovery

Status: APPLIED locally / UNKNOWN commit. Builds on P496.

Purpose: make short targeted discovery smokes provide the shared core's required pre-seed context without expanding the actual scan period.

Changes:

- Added `_htf_context_warmup_ms(...)` based on the rolling contract context length.
- Targeted pre-entry planning now loads HTF/1m context from `start_ms - warmup`, while `_targeted_ltf_backfill_seeds_for_symbol(...)` scans only the requested `start_ms..end_ms` range.
- Signal-entry planning now uses scan HTF for candidate discovery and warmup HTF for seed-stage context checks.
- Main symbol processing now uses scan HTF for candidate collection and warmup HTF for `_build_seed_first_backtest_snapshot(...)`.
- Added regression coverage proving warmup history is used for planning while warmup-period pairs are not scanned.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
Low. This expands data loading for HTF context only, not the scan/evaluation period. Candidates still originate from the requested run range. If a symbol truly lacks 24h HTF cache before the range, the core still returns an explicit data dependency.
```

## 2026-06-02 - P496 pandas FutureWarning cleanup in targeted cache subtraction

Status: APPLIED locally / UNKNOWN commit. Builds on P495.

Purpose: remove repeated pandas `FutureWarning` spam from targeted cache subtraction coverage checks.

Changes:

- Replaced `fillna(False).astype(bool)` on `aggtrade_coverage_verified` metadata with explicit truthy-mask normalization.
- Reused the same warning-safe normalization for related boolean report fields.
- Removed the same pattern from runner discovery `_bool_series(...)`.
- Added regression coverage that runs the targeted cache missing-interval check with `FutureWarning` treated as an error.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
Low. This is log hygiene and explicit boolean parsing only. It does not change trading thresholds, snapshots, or execution behavior.
```

## 2026-06-02 - P495 trusted target-LTF cache interval subtraction

Status: APPLIED locally / UNKNOWN commit. Builds on P494.

Purpose: avoid re-fetching aggTrades for target-LTF buckets already materialized from trusted direct aggTrades coverage.

Changes:

- Added `_trusted_materialized_entry_cache_missing_intervals(...)` to compute missing target-LTF bucket intervals inside a requested window.
- `ensure_targeted_aggtrade_direct_ltf_cache(...)` now fetches only missing bucket intervals instead of the whole requested merged window when part of the target cache is already trusted.
- Fetch artifacts expose `fetch_start_timestamp_ms`, `fetch_end_timestamp_ms`, `requested_window_ms`, `fetched_window_ms`, and `cache_subtraction_model`.
- Materialize artifacts expose requested and materialized interval bounds.
- Added regression coverage for a partially cached 30s target-LTF window.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
Low. This changes fetch volume, not strategy decisions. It only skips network fetch for target-LTF buckets whose existing cache has trusted direct-aggTrades metadata and verified coverage. Untrusted or incomplete cache stays fetch-required.
```

## 2026-06-02 - P494 shared-core seed-stage signal-entry prefilter

Status: APPLIED locally / UNKNOWN commit. Builds on P493.

Purpose: reduce expensive confirm/next-open subminute fetch after exact seed replay without introducing a second strategy implementation or optimistic winner filtering.

Changes:

- Added `evaluate_rolling_seed_stage(...)` to `research_tools/pump_decision_core.py`.
- Refactored seed feature derivation so seed-stage rejects use the same shared-core features and `_seed_reject_reason(...)` as full seed+confirm decisions.
- Added `core_seed_stage_prefilter(...)` to `research_tools/runner_coarse_prefilter.py`; it rejects only terminal `rolling_htf_seed` core rejects and treats dependency/unknown states as possible.
- Wired `run-htf-ltf-runner-discovery` signal-entry planning to skip confirm fetch for terminal seed-stage rejects.
- `htf_ltf_runner_targeted_ltf_plan.csv` now preserves skipped rows as `not_planned_seed_stage_terminal_reject` with seed-stage reason/features.
- Added regression coverage proving the prefilter uses the shared core for a one-print seed reject.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py tests\test_runner_discovery_acceleration.py tests\test_cli_runner_discovery_empty_artifacts.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
Low-to-medium. The acceleration intentionally changes which exact confirm windows are fetched, but only after the shared core proves the seed itself is terminally invalid. The main residual risk is artifact interpretation: rejected seed rows must be counted as plan rejects, not silently removed from the candidate base.
```

## 2026-06-02 - P493 HTF pre-seed context for targeted discovery

Status: APPLIED locally / UNKNOWN commit. Builds on P492.

Purpose: ensure targeted subminute runner discovery can actually reach shared-core strategy verdicts after P491 acceleration.

Changes:

- Added `_pre_seed_context_candles_from_htf(...)` to build seed-aligned context from cheap closed HTF cache.
- `_build_seed_first_backtest_snapshot(...)` now uses HTF cache for `pre_seed_context_candles` and keeps exact LTF for seed internal flow and confirm.
- Added regression coverage proving `5m_30s` can build the required 288 HTF context candles without 24h of subminute LTF.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_cli_runner_discovery_empty_artifacts.py tests\test_pump_decision_contract.py -q
.venv\Scripts\python.exe -m compileall -q research_tools\htf_ltf_runner_discovery.py tests\test_runner_discovery_acceleration.py cli\commands.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
Low and intentional. The shared contract defines pre-seed context as HTF-width closed candles; reconstructing it from HTF cache avoids unnecessary 15s/30s history and should restore parity with live's minute/HTF context repair model.
```

## 2026-06-02 - P492 zero-trade runner discovery artifact guard

Status: APPLIED locally / UNKNOWN commit. Builds on P491.

Purpose: prevent long runner discovery runs from failing during combined aggregation when a profile legitimately produces zero selected trades.

Changes:

- Added CLI `_read_csv_or_empty(...)` so BOM-only or empty CSV artifacts are treated as empty frames instead of raising `EmptyDataError`.
- Runner discovery now writes stable headers for zero-row `signals` and `trades` artifacts.
- Added regression tests for empty CSV reads and empty trade artifact headers.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_cli_runner_discovery_empty_artifacts.py tests\test_runner_discovery_acceleration.py -q
.venv\Scripts\python.exe -m compileall -q cli\commands.py research_tools\htf_ltf_runner_discovery.py tests\test_cli_runner_discovery_empty_artifacts.py tests\test_runner_discovery_acceleration.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
Low. This does not turn zero trades into success metrics; it only keeps zero-trade artifacts readable and allows the multi-profile run index/aggregation to finish.
```

## 2026-06-01 - P491 staged targeted LTF backfill and coarse prefilter

Status: APPLIED locally / UNKNOWN commit. Builds on P480.

Purpose: make long HTF/LTF runner discovery materially faster without using future winners, labels, exits, or PnL as pre-entry filters.

Changes:

- Split targeted subminute fetch into `pre_entry`, `signal_entry`, and `post_entry_replay` phases.
- `signal_entry` fetches only the short first-confirm/next-open window after exact rolling seed candidates.
- `post_entry_replay` fetches long future label/exit replay windows only for core-selected signals.
- Added `research_tools.runner_coarse_prefilter`, an independent 1m upper-bound impossibility prefilter that keeps uncertain or incomplete windows.
- Future runner labels for selected trades are attached after core signal selection and marked `future_label_assignment_model=after_core_signal_selection_not_entry_filter`.
- Fixed `_pre_seed_context_candles_from_ltf` to compare strict window status strings correctly.
- Added focused acceleration tests.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py -q
.venv\Scripts\python.exe -m compileall -q research_tools\runner_coarse_prefilter.py research_tools\htf_ltf_runner_discovery.py tests\test_runner_discovery_acceleration.py
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
The main risk is a false impossible verdict in the new coarse prefilter. The module is intentionally conservative: missing 1m coverage and uncertain bounds return possible, not rejected. A 1d/3d parity smoke must still compare selected snapshot hashes/verdicts before trusting 30d results.
```

## 2026-06-01 - P490 TP1 full-close dust rounding guard

Status: APPLIED locally / UNKNOWN commit. Builds on P489.

Purpose: prevent profitable tiny positions from halting live when exchange-reported position amount is dust-rounded below the symbol's minimum order precision.

Changes:

- `Live2PositionSupervisor` now computes full-TP1 reduce-only close amount as `max(abs(exchange_amount), protected_remaining)` instead of blindly using the fetched exchange amount.
- Partial TP1 close still uses the exchange amount fraction, preserving the existing contract for runner remainders.
- Added regression tests for LITE-like `0.009999999` exchange amount with protected remaining `0.01`, and for unchanged partial-close sizing.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_live2_position_supervisor_recovery.py -q
```

Risk:

```text
For full close only, the submitted reduce-only amount may be slightly above the fetched exchange amount when local protected state has the precise opened quantity. This is intentional for dust-rounded exchange reads and should be safe with reduce-only semantics. If an exchange rejects over-sized reduce-only closes on another venue, artifacts will still expose the failure.
```

## 2026-06-01 - P489 pre-seed dump/rebound guard

Status: APPLIED locally / UNKNOWN commit. Builds on P488.

Purpose: reject PLTR-like rebounds where quote/trade activity wakes up because the coin just dumped and is being bought back, not because dormant flow is organically expanding upward.

Changes:

- `PumpDecisionCore` now computes pre-seed pregrowth downside features: `pregrowth_min_single_return_pct`, `pregrowth_min_path_return_pct`, `pregrowth_range_pct`, and `pre_seed_dump_rebound_ok`.
- Mostly-red pregrowth windows with material cumulative, path, single-candle, or noisy dump are rejected at seed stage with `pre_seed_dump_rebound_pattern`.
- Live2 and backtest decision ledgers expose the new pre-seed fields for audit.
- Added focused regression coverage proving that the same seed/confirm snapshot is rejected when only the pre-seed context is changed into a dump/rebound prelude.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
```

Risk:

```text
This intentionally reduces selected signals for rebound-after-dump setups. It can also reject some V-bottom reversals that later run; that is acceptable for the current strategy definition because we are not trying to trade short-cover rebounds after noisy selloffs.
```

## 2026-06-01 - P488 stop child-fill PnL recovery

Status: APPLIED locally / UNKNOWN commit. Builds on P487.

Purpose: recover final stop-trigger PnL when Binance emits the triggered stop as a child reduce-only market fill whose order id/client id differs from the protected conditional stop id.

Changes:

- `Live2PositionSupervisor._recover_stop_close_from_user_data(...)` now still prefers exact stop `client_order_id` / `order_id`, but also accepts a strict fallback:
  same symbol, `TRADE`, `FILLED`/`PARTIALLY_FILLED`, `SELL`, `MARKET`, `reduce_only`, after the position was opened/last supervised, not the TP1 client id, and quantity not larger than the remaining protected amount.
- Added focused regression coverage in `tests/test_live2_position_supervisor_recovery.py`.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_live2_position_supervisor_recovery.py -q
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_live2\position_supervisor.py tests\test_live2_position_supervisor_recovery.py
```

Risk:

```text
The fallback is deliberately narrow and only runs when the exchange position is already flat and the protected stop is gone. It should not treat TP1 fills as stop fills because it excludes the TP1 client id and requires the fill to occur after the current stop supervision/arming point.
```

## 2026-06-01 - P487 terminal seed-stage rejects

Status: APPLIED locally / UNKNOWN commit. Builds on P486.

Purpose: improve live trading uptime by stopping repeated evaluation of seeds whose nature is already invalid.

Changes:

- `evaluate_ltf_confirm_sequence_after_seed(...)` now stops immediately when the shared core returns a seed-stage reject.
- Live2 consumes rejected pending seeds immediately when the core reject stage is `rolling_htf_seed`, instead of waiting until max-confirm.
- Existing single-print and faded-tail seed tests now assert that `seed_ltf_flow_not_sustained` is terminal at the first minimum confirm window.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
.venv\Scripts\python.exe -m compileall -q research_tools\pump_decision_core.py research_tools\anomaly_live2\signal.py tests\test_pump_decision_contract.py
```

Risk:

```text
This changes confirm sequencing for seed-stage rejects only. It should not remove valid later-confirm selections, because a rolling HTF seed reject depends on seed/context nature, not on later LTF confirm candles. Backtest/live parity ledgers may show earlier rejected snapshot hashes for those seeds.
```

## 2026-06-01 - P486 internal seed flow-shape guard

Status: APPLIED locally / UNKNOWN commit. Builds on P485.

Purpose: reject one-print or faded-tail seed flow by nature, instead of increasing confirmation candle count.

Changes:

- `PumpDecisionCore` now treats `htf_ltf_sustained_flow_ok=False` as a seed-stage reject: `seed_ltf_flow_not_sustained`.
- Internal seed LTF flow now includes tail-shape features: `htf_ltf_tail_quote_share`, `htf_ltf_tail_trade_share`, and `htf_ltf_tail_green_share`.
- Live decision ledgers now include internal seed flow-shape fields for audit.
- Added tests for single-print flow and early-flow-with-empty-tail fade.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
```

Risk:

```text
This changes signal logic and will reduce selected signals. It is intentional: aggregate quote/trade ratios are not enough if the flow is concentrated in one bar or has already faded before entry. Backtest/live parity must be rechecked because snapshot verdicts can change.
```

## 2026-06-01 - P485 live2 seed return pre-context gate

Status: APPLIED locally / UNKNOWN commit. Builds on P484.

Purpose: reduce minute-boundary hot-path spikes by avoiding expensive seed context construction for rolling windows that already fail the shared core seed return prerequisite.

Changes:

- Added `_seed_passes_return_gate(...)` as a context-free prerequisite check.
- `Live2SignalEngine._discover_rolling_seeds(...)` now checks seed return before `_pre_seed_context_for_live(...)`.
- Added `total_seed_return_gate_rejected` to live signal status.
- Added unit coverage for weak vs strong seed return gating.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
```

Risk:

```text
This does not change trading thresholds. It only moves an existing mandatory core seed check earlier, before expensive baseline/context work. If a seed fails return, it could not have selected in the shared core.
```

## 2026-06-01 - P484 live2 seed-possible scheduler gate

Status: APPLIED locally / UNKNOWN commit. Builds on P483.

Purpose: keep high-volume but seed-impossible liquid-symbol buckets out of the live deadline queue.

Changes:

- `Live2SignalEngine.prepare_seed_first_tick(...)` discovers current-tick rolling seeds without running full core/entry evaluation.
- `Live2SignalEngine.has_pending_seed_for_timeframe(...)` checks pending seeds against the active 15s/30s engine timeframe.
- `Live2DeadlineEngine._drain_non_actionable_candidates(...)` now keeps only positions, current-TF pending seeds, or buckets that create a valid current-TF seed candidate.
- Actionable quote/trade/return buckets with no pending/new seed are marked `market_quiet_non_actionable` with reason `no_pending_or_new_rolling_seed_candidate`.
- Added unit coverage for a high-quote/high-trade bucket that would have previously entered the deadline queue despite lacking enough seed context.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
```

Risk:

```text
This is still a scheduler gate, not a profitability filter. It must stay aligned with the seed-first contract: only buckets that cannot produce a trade because no pending/new rolling seed exists are drained. Pending seeds and in-position symbols remain evaluable.
```

## 2026-06-01 - P483 live2 quiet-drain before deadline sorting

Status: APPLIED locally / UNKNOWN commit. Builds on P482.

Purpose: stop routine quiet all-symbol buckets from consuming the deadline engine budget before actionable/pending symbols are evaluated.

Changes:

- `Live2DeadlineEngine.run_cycle(...)` now obtains the live watermark before sorting candidates.
- New `_drain_non_actionable_candidates(...)` marks quiet current buckets as `market_quiet_non_actionable` before `_fresh_first_candidates(...)`.
- The drain keeps pre-live buckets, open positions, and symbols with pending rolling seeds in the normal evaluation path.
- Added a unit test proving a late quiet 15s bucket is not emitted as `deadline_missed`.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
```

Risk:

```text
This is a scheduler/load patch, not a strategy patch. It uses only the same cheap actionability thresholds already used by the deadline engine, and it explicitly does not drain pending-seed or in-position symbols. If deadline misses remain high after restart, the next root cause is likely expensive shared-core evaluation on truly actionable candidates, not quiet-market spam.
```

## 2026-06-01 - P482 live2 actionable/context load fix

Status: APPLIED locally / UNKNOWN commit. Builds on P481.

Purpose: make live2 runtime capable of reaching the shared decision core only for plausible, context-ready buckets instead of flooding the deadline engine with impossible work.

Changes:

- `Live2DeadlineEngine._evaluate_state(...)` now uses the existing `_actionable_reason(...)` before deadline/backlog warning records. Quiet buckets are marked `market_quiet_non_actionable` and do not create high-volume deadline decisions.
- `Live2SignalEngine._discover_rolling_seeds(...)` now requires pre-seed context before storing a pending seed.
- Live pre-seed context first uses exact LTF history when available, then uses startup/maintenance 1m candles for minute-aligned 3m/5m context.
- Pending seed storage now applies the shared core's basic seed gate subset: HTF return, quote ratio and trade ratio against the same baseline length.
- Added unit coverage for 1m-backed live context and quiet seed rejection.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
.venv\Scripts\python.exe -m compileall data/exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Risk:

```text
This reduces live workload and dependency spam, but it also makes the live seed-discovery prefilter meaningful. The prefilter intentionally uses only shared-core seed minima and context readiness; it must not be extended with outcome labels or profitability-derived filters.
```

## 2026-05-31 - P481 shared contract unit tests

Status: APPLIED locally / UNKNOWN commit. Builds on P480.

Purpose: add cheap regression tests around the parts most likely to silently break live/backtest parity or data-loading honesty.

Changes:

- Adds `tests/test_pump_decision_contract.py`.
- Tests that `snapshot_hash` changes for explicit non-ok dependencies and candle `source_status`, while adapter labels/source names do not affect the hash.
- Tests that `evaluate_first_ltf_confirm_after_seed(...)` returns the last exact rejected confirm snapshot, not a seed-only aggregate reject.
- Tests that extra adapter-retained pre-context outside the deterministic contract slice does not change verdict/hash.
- Tests `5m_15s` and `3m_15s` as first-class profile specs.
- Tests the cheap HTF confirm upper-bound gate rejects only proven impossibility and keeps missing/non-adjacent next-HTF context.
- Tests 5m OI `asof` timing and live per-timeframe decision bucket independence.

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
.venv\Scripts\python.exe -m compileall data/exchanges research_tools cli constants.py main.py
```

Known test-suite debt:

```text
Existing legacy tests/test_htf_ltf_runner_discovery.py and tests/test_live2_market_watch.py still contain stale calls to old signatures and removed live-only helpers. They should be migrated in a separate test-cleanup patch, not made authoritative for the current shared-core path.
```

## 2026-05-31 - P480 cheap confirm upper-bound planner gate

Status: APPLIED locally / UNKNOWN commit. Builds on P479.

Purpose: reduce expensive targeted LTF/1s backfill without introducing optimistic bias.

Changes:

- Adds `_pair_can_pass_confirm_upper_bounds(...)` to `research_tools/htf_ltf_runner_discovery.py`.
- The pre-entry LTF planner now rejects a pair only when cheap HTF upper bounds prove the shared core's confirm return, quote pace, or trade pace minimum cannot be reached.
- If the next HTF candle is missing or non-adjacent, the gate keeps the pair instead of guessing.
- Adds planner counters `rejected_impossible_confirm_return`, `rejected_impossible_confirm_quote_pace`, and `rejected_impossible_confirm_trade_pace`.
- Planned rows now expose `confirm_bound_model`, `confirm_bound_reason`, `confirm_quote_volume_upper_bound`, `confirm_number_of_trades_upper_bound`, `confirm_max_possible_return_pct`, `confirm_quote_pace_ratio_upper_bound`, `confirm_trade_pace_ratio_upper_bound`, and confirm duration bounds.

Validation:

```bash
python -m compileall -q research_tools/htf_ltf_runner_discovery.py
python -m research_tools.decision_contract_guard
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
The bound is intentionally loose and may keep many windows. That is acceptable. It must not be tightened using runner labels, PnL, realized future path quality, or post-entry outcomes. Any speedup should be judged from planner counters and exact snapshot coverage, not profitability alone.
```

## 2026-05-31 - P479 live multi-timeframe decision streams

Status: APPLIED locally / UNKNOWN commit. Builds on P478.

Purpose: make live evaluate 15s and 30s independently by default, instead of requiring an operator flag to choose one LTF stream.

Changes:

- `AnomalyLive2Config` adds `decision_timeframes_ms=(15000, 30000)`.
- `AnomalyLive2Runner` now owns one `Live2DeadlineEngine` per decision timeframe and runs all of them each hot-path cycle.
- Deadline status aggregates total counts while preserving per-timeframe engine status under `decision_status.engines`.
- `SymbolState.last_decision_bucket_ms_by_timeframe` prevents the 15s and 30s streams from marking each other's buckets as already processed.
- `SymbolState.rolling_last_seed_discovery_close_ms_by_timeframe` prevents 15s and 30s seed discovery from suppressing each other at shared close timestamps.
- Removed the `--decision-timeframe-ms` CLI switch introduced in P478; live default is now both streams.

Validation:

```bash
python -m compileall -q research_tools/anomaly_live2/runner.py research_tools/anomaly_live2/deadline.py research_tools/anomaly_live2/signal.py research_tools/anomaly_live2/state.py research_tools/anomaly_live2/config.py cli/commands.py cli/parser.py
python -m research_tools.decision_contract_guard
```

Risk:

```text
Live decision load increases because every selected symbol is evaluated on 15s and 30s streams. The next smoke must inspect deadline_missed, budget_exhausted, duplicate same-symbol blocks, and per-timeframe ledger mix before interpreting signal quality.
```

## 2026-05-31 - P478 add 15s rolling profiles

Status: APPLIED locally / UNKNOWN commit. Builds on P477.

Purpose: add `5m_15s` and `3m_15s` as first-class shared-core profiles without creating a backtest-only strategy path.

Changes:

- `PumpDecisionCore` core version becomes `p478_add_15s_profiles`.
- Shared supported TF sets now include `5m_15s` and `3m_15s`.
- Core profile specs add `5m_15s` with 20 seed candles, min/max confirm `4/16`, and `3m_15s` with 12 seed candles, min/max confirm `4/12`.
- Live2 signal adapter now builds seed/context/confirm snapshots from the active decision timeframe instead of hard-coded 30s candles.
- `run-anomaly-live2` initially exposed a `--decision-timeframe-ms` switch in P478, but P479 replaces that with default multi-timeframe 15s+30s live streams.
- `run-htf-ltf-runner-discovery` now runs `5m_30s`, `3m_30s`, `5m_15s`, and `3m_15s` profiles.

Validation:

```bash
python -m compileall -q research_tools/pump_decision_core.py research_tools/anomaly_live2/signal.py cli/commands.py cli/parser.py research_tools/htf_ltf_runner_discovery.py
python -m research_tools.decision_contract_guard
python research_tools/pump_decision_core.py
```

Risk:

```text
15s profiles increase live deadline load and backtest LTF fetch cost. They should be tested on a small symbol/time window first. Category C/A thresholds are reused as source-neutral feature rules; strict S remains the original 5m_30s-specific matcher shape until separately researched.
```

## 2026-05-31 - P477 snapshot hash integrity and live cooldown parity

Status: APPLIED locally / UNKNOWN commit. Builds on the local P465-P476 shared rolling seed-first contract stack.

Purpose: fix contract-level parity issues without changing thresholds, data planners, execution, order placement, exits, fees, or slippage.

Changes:

- `decision_snapshot_hash(...)` now includes core-affecting explicit dependencies and candle `source_status`. It still excludes adapter source labels, portfolio state, execution state and artifact-only labels.
- `evaluate_first_ltf_confirm_after_seed(...)` now returns the final exact rejected confirm-window verdict when no selected confirm exists, instead of manufacturing an aggregate reject on the seed-only snapshot. This makes live rejected ledgers join to one of the backtest exact-window rows.
- `PumpDecisionCore` core version becomes `p477_snapshot_hash_integrity`.
- Core selected features now include `rolling_runner_htf_timeframe_ms` and `rolling_runner_ltf_timeframe_ms`, allowing live execution cooldown to use the selected rolling profile.
- Documents that live/backtest data acquisition can differ: HTF-planned expensive LTF/1s backfill in backtest and REST repair in live are data-availability layers, not alternate strategy decision logic.

Validation:

```bash
python research_tools/pump_decision_core.py
python -m research_tools.decision_contract_guard
python -m compileall -q research_tools/pump_decision_core.py research_tools/anomaly_live2/signal.py research_tools/anomaly_live2/execution.py research_tools/htf_ltf_runner_discovery.py research_tools/decision_parity_join.py
```

Additional synthetic checks:

```text
same candles + different non-ok dependency now produce different snapshot_hash
no-selected confirm sequence now returns a rejected verdict with ltf_confirm present
```

Risk:

```text
Snapshot hashes change for existing ledgers because source_status and explicit dependencies are now part of the core-affecting hash payload. Old and new parity ledgers should not be joined across this patch without noting the contract version/hash payload change.
```

## 2026-05-31 - P470 live2 shared-core-only signal adapter

Status: PROPOSED. Current commit: UNKNOWN. Built on P465-P469 expected applied locally / UNKNOWN commit.

Removes the executable legacy live signal paths after the P469 seed-first migration. `Live2SignalEngine` is now a thin adapter: it discovers pending rolling HTF seeds, builds a source-neutral `DecisionSnapshot`, calls `PumpDecisionCore.evaluate_first_ltf_confirm_after_seed`, and maps the typed core verdict to live execution guards.

Changes:

- Remove the old confirm-backward live evaluator and baseline-free prefilter methods from `anomaly_live2/signal.py`.
- Remove live-only C/A/S/category helper paths (`_rolling_runner_category_setup`, `_evaluate_rolling_profile`, and legacy diagnostic/category functions) from the signal adapter.
- Keep constructor compatibility for stale/context/repair arguments, but signal selection no longer uses live-only gates outside the shared core.
- Status now explicitly reports `rolling_seed_first_core_signal_adapter_active` and `legacy_live_paths_removed=true`.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
grep -R --exclude-dir=__pycache__ "def _rolling_runner_category_setup\|def _evaluate_rolling_profile\|def _rolling_baseline_free_prefilter_reject_reason\|def evaluate_baseline_free_prefilter\|def evaluate(self" -n research_tools/anomaly_live2
grep -R --exclude-dir=__pycache__ "rolling_htf_then_first_category_qualified_30s_confirm\|baseline_free_prefilter\|_rolling_runner_matches\|_runner_candidate_matches" -n research_tools
```

Risk:

```text
This intentionally removes live-only fast rejects and legacy selected diagnostics. Selected/rejected counts may change relative to pre-P469/P470 live because all signal verdicts now come from the shared seed-first core. That is intended parity exposure, not threshold tuning. Execution guards, portfolio/risk, order placement, fills, stops, and position supervision are unchanged.
```

## 2026-05-31 - P469 live2 rolling seed state machine

Status: PROPOSED. Current commit: UNKNOWN. Applies after P468.

Changes:

- Live2 now stores pending rolling HTF seeds per symbol and evaluates post-seed 30s candles through the shared `rolling_htf_seed_first_ltf_confirm_v1` core.
- The live deadline path no longer requires the current 30s bucket to cross the old actionable quote/trade/return gate before a pending seed can be evaluated. Quiet post-seed buckets can advance the first-confirm search.
- Selected live signals export seed/confirm timestamps from the core (`rolling_seed_open_ms`, `rolling_seed_close_ms`, `confirm_start_ms`, `confirm_end_ms`) and use `confirm_end_ms` as the signal timestamp for entry-guard freshness.
- Existing execution, entry guard, portfolio/risk gates, TP/SL, thresholds and order placement are unchanged.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
This changes live orchestration from confirm-backward to seed-first. Selected counts can change because live now evaluates the same chronological contract intended for backtest parity. Legacy live signal helpers remain in the file until P470/P471 cleanup, but the deadline hot path calls the new seed-first tick evaluator.
```

## 2026-05-31 - P468 backtest seed-first core adapter

Status: PROPOSED. Current commit: UNKNOWN. Applies after P467.

Changes:

- HTF/LTF runner discovery now builds source-neutral `DecisionSnapshot` objects for selected rolling HTF seed candidates and calls `evaluate_first_ltf_confirm_after_seed(...)` from `research_tools/pump_decision_core.py`.
- The backtest selected-signal path no longer performs its own LTF confirmation/category decision; it maps a shared-core `selected` verdict into the existing next-open-plus-slippage execution simulation.
- Adds `htf_ltf_runner_decision_ledger.csv`, `htf_ltf_runner_rejected_exact_windows.csv`, and `htf_ltf_runner_data_dependencies.csv` so core rejects and data-dependency misses are visible instead of disappearing before trade artifacts.
- Keeps execution, portfolio, TP1/structural trailing, fees, slippage, risk caps, and thresholds unchanged.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
This migrates the backtest selected-signal decision boundary, so selected counts may change if the old duplicate LTF/category logic differed from the shared core. That is intended parity exposure, not threshold tuning. Live still has its own seed/confirm orchestration until P469/P470.
```

## P467 - seed-first rolling decision core

Status: PROPOSED.

Files: `research_tools/pump_decision_core.py`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/STRATEGY_SPEC.md`.

Purpose: add the first executable shared decision core for `rolling_htf_seed_first_ltf_confirm_v1`. The new pure evaluator accepts normalized seed/context/confirm snapshots, validates typed data dependencies and candle continuity, derives the C/A/S feature set, returns selected/rejected/data-dependency verdicts, and provides a deterministic smoke fixture.

Honesty: no live/backtest adapters are migrated in this patch; no thresholds, execution model, portfolio cap, fill model, exits, or source-specific fallback are changed. This is the clean decision boundary that later patches must call.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `python research_tools/pump_decision_core.py`.

## 2026-05-31 - P466 shared rolling C/A/S category matcher

Status: PROPOSED. Current commit: UNKNOWN. Applies after P465.

Moves the existing rolling C/A/S category matching rules out of the separate live2 and HTF/LTF discovery implementations into the source-neutral decision-core module. This is a no-threshold-change parity patch.

Changes:

- Add `match_rolling_categories()`, `rolling_tf_set_from_features()`, and `rolling_category_priority_rank()` to `research_tools/pump_decision_core.py`.
- Preserve the exact C/A/S thresholds and priority order from the existing duplicate matchers.
- Make `research_tools/anomaly_live2/signal.py` call the shared matcher and priority helper.
- Make `research_tools/htf_ltf_runner_discovery.py` call the same shared matcher and priority helper.
- Remove the duplicated `_rolling_runner_matches()` and `_runner_candidate_matches()` implementations.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
grep -R "def _rolling_runner_matches\|def _runner_candidate_matches" research_tools
```

Risk:

```text
Trading behavior should not change. The remaining parity risk is orchestration: live still searches from a closed confirm backward, while discovery is seed-first. P467/P468 must fix that rather than adding more live/backtest exceptions.
```

## 2026-05-31 - P465 rolling seed-first decision contract types

Status: PROPOSED. Current commit: UNKNOWN.

Introduces the source-neutral typed contract for the required architecture: live and backtest must normalize their own data into the same rolling HTF seed-first / first LTF confirm snapshots, then receive a source-neutral decision verdict from one shared core. This patch is intentionally contract-only: no live path, backtest path, thresholds, portfolio allocation, execution model, or trading behavior is changed.

Changes:

- Add `research_tools/pump_decision_core.py` with immutable dataclass contracts for rolling profiles, category rules, normalized decision candles, seed snapshots, LTF confirm snapshots, data dependencies, rejects, snapshots, and verdicts.
- Freeze `contract_id=rolling_htf_seed_first_ltf_confirm_v1` and supported rolling profiles `5m_30s` / `3m_30s`.
- Freeze category priority names `C_balanced_flow_acceptance -> A_resonance_prior_spike -> S_7d_5m30_strict` without moving matcher logic yet.
- Document that signal decision and portfolio/execution remain separate layers.

Validation:

```bash
python -m compileall -q research_tools/pump_decision_core.py
```

Risk:

```text
None to trading behavior. The risk is only architectural drift if follow-up patches do not migrate live/backtest adapters to this contract and remove duplicate matchers.
```

## 2026-05-30 - P462 live2 all-symbol baseline-free deadline gate

Status: PROPOSED. Current commit: UNKNOWN.

Fixes the main runtime bottleneck seen in the Ctrl+C live artifact review without adding top-K blindness. The deadline engine now runs an exact baseline-free impossibility gate before the expensive rolling context/category evaluation. The gate observes every dirty symbol, but skips full signal evaluation only when closed 30s candles already prove the rolling C/A/S contract cannot pass.

Changes:

- Add `Live2SignalEngine.evaluate_baseline_free_prefilter()` for deterministic no-baseline rejects.
- Reject only on exact prerequisites already required by full rolling evaluation: contiguous confirmation, LTF return/acceleration shape, HTF seed availability/return, confirmation not undercutting HTF low, and structural risk cap.
- Preserve full evaluation for candidates with aggTrade gap dependencies or anything that may still pass after baseline/dormancy/category checks.
- Add timing fields for the baseline-free prefilter and expose `total_baseline_free_prefilter_rejected` in signal-engine status.
- Align default live2 decision engine cycle budget with the 1500 ms decision deadline instead of stopping at 1000 ms.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
The gate must remain a strict impossibility proof, not a quality ranker. Do not add baseline ratio estimates, category-like thresholds, symbols top-K, or outcome-like filters here. If false rejects are suspected, compare prefilter reject reasons with full signal evaluation on an offline replay sample.
```

## 2026-05-30 - P449 proposed - live2 entry/stop audit truthfulness hygiene

Status: PROPOSED against uploaded workspace / commit UNKNOWN.

Files: `research_tools/anomaly_live2/signal.py`, `research_tools/anomaly_live2/entry_guard.py`, `research_tools/anomaly_live2/deadline.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/execution.py`, `research_tools/anomaly_live2/position_supervisor.py`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/EXPERIMENT_LOG.md`.

Purpose: remove the dead signal-layer entry-drift check that compared the decision close to itself, expose the live price source used by entry guard (`aggtrade_last_price` vs `ticker_last_price`), and replace ambiguous stop visibility checks with a typed `visible / absent / api_error` result on execution/supervision safety paths.

Honesty: no trading thresholds, category selection, entry guard limits, fill model, TP/SL math, or scheduler policy change. Exchange `ExchangeOrderNotFound` is classified as `absent`; other exceptions are `api_error` and remain integrity/safety failures instead of being collapsed into “stop not visible”.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.

## 2026-05-30 - P448 proposed - live2 flat-stop recovery NameError fix

Status: PROPOSED after P447 / commit UNKNOWN.

Files: `research_tools/anomaly_live2/position_supervisor.py`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/EXPERIMENT_LOG.md`.

Purpose: fix a crash in the protected-position supervisor when the exchange position is already flat and the protected stop is gone. The recovery artifact used `refreshed_amount` outside its scope; this path already has the current fetched amount as `exchange_amount`. The patch records `exchange_amount` and lets the supervisor remove the protected position, recover stop-close PnL from user-data when available, and finish the run instead of crashing.

Honesty: no trading thresholds, fill inference, stop semantics, or exchange calls change. This only fixes the recovery-path artifact payload variable.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; grep should show no `refreshed_amount` reference in the direct flat-position branch.

## 2026-05-29 - P447 proposed - live2 rolling 1m context maintenance

Status: PROPOSED after P446 / commit UNKNOWN.

Files: `research_tools/anomaly_live2/market_data/warmup.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/status_grid.py`, `cli/parser.py`, `cli/commands.py`, `research/STRATEGY_SPEC.md`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/EXPERIMENT_LOG.md`.

Purpose: add bounded async official Binance 1m kline maintenance for rolling baseline/dormancy context. The worker fetches only closed 1m klines, uses an explicit `binance_futures_klines_maintenance_rest_1m_rolling_context` source label, prioritizes active/actionable symbols, and never blocks the deadline hot path or replaces live 30s aggTrade flow.

Honesty: this is not a trading fallback and does not invent candles. It only maintains historical 1m continuity that aggTrade streams cannot represent during zero-trade dormant minutes. Entry still requires fresh WS flow, entry guard, actual fill, and verified stop.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic maintenance smoke should verify closed-only fetch range, source label, status counters, and non-blocking start/close.

## 2026-05-29 - P446 proposed - live2 actionable data-readiness hygiene

Status: PROPOSED after local audit writer patch / commit UNKNOWN.

Files: `research_tools/anomaly_live2/deadline.py`, `research_tools/anomaly_live2/signal.py`, `research_tools/anomaly_live2/state.py`, `research/STRATEGY_SPEC.md`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/EXPERIMENT_LOG.md`.

Purpose: fix the source of excessive `data_not_ready` / `data_dependency_not_ready` without adding fallback. Weak real-trade buckets no longer enter the live signal engine unless quote/trade/return thresholds are crossed; stale last trade inside a closed bucket is reported as `flow_freshness_reject`, not data readiness failure; rolling 1m repair metadata is preserved so the existing official-kline REST repair can actually run on threshold-actionable candidates.

Honesty: no synthetic candles, no silent fallback, no changed fill/risk/exit logic. REST repair uses the existing typed Binance 1m kline boundary and re-runs the same continuity contract.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic smokes for quiet bucket gating, stale-flow verdict, and rolling repair metadata propagation.

## P445 - speed rolling targeted fetch with C/A/S impossibility gate

Status: PROPOSED.

Files: `research_tools/htf_ltf_runner_discovery.py`, `research_tools/anomaly_strategy_backtest.py`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/EXPERIMENT_LOG.md`.

Purpose: reduce the P444 multi-day pre-entry 1s aggTrades fetch without adding fallback or hiding data-quality problems. The pair gate stays a data-loading proof: fetch only when the official rolling seed and at least one frozen C/A/S family are not mathematically impossible. Also skip network fetches when the requested target LTF cache already honestly covers the same windows.

Honesty: no future labels, no PnL, no exits, no MFE/MAE, and no post-entry data are used. The C/A/S pair guard uses only necessary conditions that can prove impossibility; exact category and entry still require closed LTF candles.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `git apply --check` on source+P439-P444.

## P444 - widen rolling pair gate to safe-superset

Status: PROPOSED.

Files: `research_tools/htf_ltf_runner_discovery.py`, `cli/commands.py`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/EXPERIMENT_LOG.md`.

Purpose: make the 2xHTF LTF-fetch planner a real safe-superset. The previous P443 stack still used strict legacy targeted absolute/range thresholds at the data-loading gate. P444 fetches a pair unless pair upper bounds prove no exact rolling HTF seed could pass the official quote-ratio/trade-ratio/return gate.

Honesty: the pair gate remains data-loading only, not a signal/filter. C/A/S category, entry and exit continue to use only exact rolling LTF windows and already-closed historical context.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `git apply --check` on source+P439-P443.

## P443 - Fix rolling baseline overlap contamination

Status: PROPOSED.

Files: `research_tools/htf_ltf_runner_discovery.py`, `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md`, `research/EXPERIMENT_LOG.md`.

Purpose: ensure rolling HTF seed context uses only calendar HTF candles fully closed before the rolling-window start. The prior P440 implementation used calendar candle starts before the rolling start, which could include a candle overlapping the rolling anomaly window. This was not future data at decision time, but it polluted pre-seed baseline/dormancy/pregrowth with current anomaly data.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic rolling smoke should include no overlapping calendar candle in baseline history.

## P442 - restrict rolling discovery to validated TFs and add combined portfolio

Status: PROPOSED.

Reason: P441 made entries category-qualified, but the command still ran unvalidated profiles (`5m_1m`, `1m_15s`) and applied risk-cap/cooldown separately per TF profile. That can overstate live-like capacity when the real bot scans multiple TF sets at once.

Changes:
- run runner discovery only on the currently validated `5m_30s` and `3m_30s` profiles;
- prevent C/A/S categories from matching any other TF set;
- make symbol cooldown use each trade row's own HTF width;
- write combined root-level raw/live-filtered trades and combined portfolio events across TF profiles.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.

## P441 - first category-qualified rolling signal

Status: PROPOSED.

Reason: P439/P440 compiled and fixed rolling seed discovery, but C/A/S category assignment still happened after generic signal simulation. That was not the intended live-like contract.

Changes:
- require C/A/S fixed-priority match inside `_build_first_ltf_signal` before returning a selected signal;
- persist matched category fields on the signal/trade;
- keep `_with_runner_candidate_categories` idempotent so it preserves signal-time categories instead of silently re-mining them later.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.

## 2026-05-29 - P440 proposed - fix rolling seed baseline and safe-superset restriction

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/EXPERIMENT_LOG.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Correct P439 without adding fallback or optional modes. Exact rolling seed detection must work after the cheap two-HTF-pair LTF fetch, while still keeping calendar HTF only as historical context / safe-superset planning, not as the trading seed.
```

Fix:

```text
Rolling current OHLCV is built from the LTF pair. Baseline, dormancy, pregrowth and prior-spike context are taken from calendar HTF rows strictly before the rolling window start. The final discovery pass is restricted to pre-entry pair timestamps so post-entry/full replay cache cannot introduce signals outside the planned safe-superset.
```

Status: PROPOSED after P439 / commit UNKNOWN.

Validation: synthetic pair-only rolling seed smoke; `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.

## 2026-05-29 - P439 proposed - rolling HTF runner discovery with C/A/S portfolio constraints

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/EXPERIMENT_LOG.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make runner discovery match the intended live model: rolling HTF seed, first valid category trigger, fixed C/A/S priority, explicit total-risk cap, one open trade per symbol, rolling-window symbol cooldown, and auditable capacity/cooldown rejects.
```

Implementation:

```text
The old calendar HTF candle is no longer the trading seed. A pair of adjacent closed HTF candles is used only as a cheap safe-superset: if the pair could not contain a rolling seed, no LTF is fetched; if it could, only those two HTF candles are fetched first. Exact rolling HTF windows are then built from LTF. Full confirm/label/exit LTF is fetched only after an exact rolling seed exists.
```

Honesty boundary:

```text
No future labels, PnL, MFE/MAE, or exit fields are used for category selection. C/A/S uses known-at-entry fields. Risk-cap, same-symbol blocking, cooldown and rejects are part of the simulated portfolio and are written to artifacts.
```

Status: PROPOSED against uploaded `source.zip` / commit UNKNOWN.

Validation: `git apply --check p439_rolling_htf_runner_discovery_source.patch`; `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.

## 2026-05-28 - P438 proposed - runner/fader OOS v1 research filter artifacts

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/EXPERIMENT_LOG.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Freeze the 7d runner/fader readout as a held-out 45d hypothesis instead of silently optimizing live logic. The next discovery run will score known-at-entry OOS v1 masks and write strict-filter live-filtered trades plus daily, summary, and top-dependency artifacts.
```

Hypothesis:

```text
Runner-like candidates require real HTF trade-count awakening but should not be entered after LTF trade pace has already overheated. Research candidate: htf_trade_ratio >= 12 and ltf_trade_pace_ratio <= 6. Strict tier: additionally htf_quote_ratio <= 48 to avoid quote-volume blow-off/chase.
```

Honesty boundary:

```text
No live order logic, signal timing, entry price, stop/trailing, fees, slippage, future label generation, or post-entry replay is changed. The new masks use only htf_trade_ratio, ltf_trade_pace_ratio, and htf_quote_ratio, which are already present at the selected entry decision. Future labels and PnL are output metrics only.
```

Status: PROPOSED against uploaded workspace / commit UNKNOWN.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; then run 45d discovery and inspect `htf_ltf_runner_oos_runner_fader_v1_*` artifacts plus the `oos_v1_*` rows in trade and entry-window rule-score CSVs.

## 2026-05-28 - P437 proposed - merge post-entry targeted LTF windows and prune broad post-entry candidates early

Files:

```text
research_tools/anomaly_strategy_backtest.py
research_tools/htf_ltf_runner_discovery.py
research/EXPERIMENT_LOG.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Reduce runner discovery runtime without weakening data quality: post-entry targeted aggTrade/LTF fetch uses gap-only merging for overlapping 60m replay windows, and post-entry planning builds candidate rows only for strict pre-entry HTF seed timestamps.
```

Honesty boundary:

```text
No trading thresholds, future-label rules, execution prices, stops, fees, slippage, or replay paths are changed. The patch only removes repeated fetch/CPU work for windows and candidates that were already selected/excluded by pre-entry strict seed data.
```

Status: PROPOSED against uploaded workspace / commit UNKNOWN.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; then rerun discovery and verify post-entry `raw_targeted_windows > merged_targeted_windows` plus unchanged selected/trade logic semantics.

## 2026-05-28 - P436 proposed - bind post-entry fetch to strict HTF seeds

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the remaining runner-discovery targeted-fetch explosion: post-entry 1s backfill was still rebuilt from the broad HTF anomaly gate, not from the stricter pre-entry HTF seed gate. This made P434's strict LTF-download definition ineffective for the expensive post-entry phase. P436 carries the pre-entry seed timestamps forward and fetches post-entry horizon only for candidates that passed the closed-HTF strict seed gate and then passed known-at-entry LTF confirmation/entry guards.
```

Honesty boundary:

```text
The post-entry fetch restriction uses only the closed-HTF seed decision already made before any LTF/post-entry data, plus normal known-at-entry LTF confirmation/entry guards. It does not use future runner labels, post-entry prices, stop outcomes, MFE/MAE, PnL, or arbitrary per-symbol caps. Broad HTF anomalies that did not pass the strict seed gate are audited as skipped for post-entry fetch, not counted as failed trades.
```

Status: PROPOSED against P435 workspace / commit UNKNOWN.

Validation: run `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; then rerun 7d discovery and inspect `htf_ltf_runner_targeted_ltf_plan.csv`. The post-entry summary must show `selection_model=closed_htf_strict_seed_gate_then_known_at_entry_ltf_confirmation_no_future` and a nonzero `skipped_broad_htf_candidates_not_in_strict_seed_gate` when the broad anomaly gate is much wider than the strict seed gate.

## 2026-05-28 - P435 proposed - finite LTF decay features without RuntimeWarning spam

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix noisy All-NaN RuntimeWarnings in runner discovery LTF decay feature extraction by treating all-NaN adjacent-ratio/top-share inputs as missing values explicitly. This is not a warning suppressor and does not change entry logic, seed selection, replay, future labels, fees, slippage, or targeted fetch windows.
```

Status: PROPOSED against P434 workspace / commit UNKNOWN.

Validation: run `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; then rerun discovery and confirm the terminal no longer floods with `All-NaN slice encountered` from `htf_ltf_runner_discovery.py`.

## 2026-05-28 - P434 proposed - stricter HTF awakening seed gate for LTF backfill

Files:

```text
cli/commands.py
cli/parser.py
research_tools/htf_ltf_runner_discovery.py
research/EXPERIMENT_LOG.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Replace budget-style LTF backfill narrowing with a stricter, explicit definition of an HTF awakening worth downloading true 1s/LTF data for. The seed gate now requires a strong closed-HTF price expansion, real quote/trade burst, dormancy-to-anomaly jump, tight prior dormancy, and minimum absolute liquidity before any targeted subminute fetch is planned.
```

Honesty boundary:

```text
The stricter LTF-download seed gate uses only the closed HTF candle and its prior baseline/dormancy context. It must not use future runner labels, post-entry prices, stop outcomes, MFE/MAE, PnL, or any later LTF candles to decide whether to download LTF. Entries can only occur after the event has passed this closed-HTF gate and then passed the normal closed-LTF confirmation/entry guards.
```

Status: PROPOSED against P432b workspace. Do not apply P433 cap patch together with this patch unless explicitly comparing budgeted research modes.

Validation: run `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; then run a 7d discovery and inspect `htf_ltf_runner_targeted_ltf_plan.csv`. The pre-entry summary should show the stricter seed gate and no arbitrary per-symbol truncation unless `--targeted-backfill-max-events-per-symbol` was explicitly set.

## 2026-05-28 - P432b proposed - two-stage targeted LTF backfill after P431

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/EXPERIMENT_LOG.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Reduce runner discovery 1s aggTrade fetch cost without weakening honesty. Fetch only HTF seed -> max LTF confirmation/next-open first, evaluate entry-worthiness using known-at-entry LTF confirmation and entry guards, then fetch post-entry hold/runner horizon only for executable pre-entry signals.
```

Honesty boundary:

```text
Post-entry fetch selection must not use future labels, post-entry OHLCV, PnL, runner hits, or stop outcomes. It can use only closed HTF seed data plus LTF confirmation/entry guard fields available by decision/entry time.
```

Status: PROPOSED against P431-applied local workspace / commit UNKNOWN.

Validation: run `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; then check that targeted plan phases include `pre_entry` and `post_entry`.

## 2026-05-28 - P431 proposed - four-profile runner discovery and non-spam progress

Files:

```text
cli/commands.py
cli/parser.py
research_tools/anomaly_continuation_lab.py
research_tools/anomaly_strategy_backtest.py
research_tools/htf_ltf_runner_discovery.py
research/EXPERIMENT_LOG.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Run discovery on the requested profile set: 5m/1m, 5m/30s, 3m/30s, 1m/15s. Keep targeted 1s loading bounded to suspected HTF seed windows and make seed gates profile-specific so shorter HTFs do not inherit 5m thresholds. Reduce terminal spam by making percentage progress overwrite the same line at whole-percent increments.
```

Changes:

```text
- Replace the fixed profile set with 5m_1m, 5m_30s, 3m_30s, and 1m_15s.
- Add profile-level targeted seed defaults for quote ratio, trade ratio, HTF return/range and max events per symbol.
- Make runner-discovery CLI seed overrides optional; absent args use the profile defaults, explicit args still override all profiles.
- Add seed thresholds to the root discovery index for auditability.
- Change shared progress emission from decimal/new-line progress to same-line whole-percent progress.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
git apply --check /mnt/data/p431_runner_discovery_profiles_and_progress.patch
```

Risk:

```text
The seed gates are intentionally cost controls, not live edge rules. They are stricter than the generic anomaly scan to avoid loading 1s for every small move; if a profile plans too few windows, loosen one seed threshold at a time and rerun.
```

## 2026-05-28 - P430 proposed - targeted runner-discovery LTF backfill

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research_tools/anomaly_strategy_backtest.py
cli/commands.py
cli/parser.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Commit message:

```text
P430: add targeted LTF backfill for runner discovery
```

Change:

```text
`run-htf-ltf-runner-discovery` no longer treats 15s/30s profiles as cache-only experiments. For subminute LTF profiles it first builds a strict HTF-only seed plan, fetches true Binance futures aggTrades only for windows from HTF anomaly start through possible entry/position-follow horizon, materializes the requested LTF from 1s, writes plan/fetch/materialize artifacts, then runs P429 strict replay. The subminute materializer now emits zero-trade candles only when the aggTrade coverage metadata proves the bucket is fully covered; these rows are explicitly marked.
```

Validation:

```text
compileall passed for data/exchanges research_tools cli constants.py main.py. No live/network fetch smoke was run in the sandbox.
```

Risk:

```text
The default seed gate is intentionally stricter than the generic anomaly gate to control 1s download cost and may miss weaker runners. Treat it as a data-fetch/research-cost profile, not final live logic.
```

## 2026-05-27 - P429 proposed - strict LTF wall-clock honesty for runner discovery

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove the optimistic sparse-LTF simulation path found in the 5m/15s and 5m/30s artifacts.
```

Change:

```text
HTF/LTF runner discovery no longer simulates post-entry exits by taking the next N available LTF rows. Entry confirmation, future runner labels, HTF-internal LTF features and post-entry replay now use configured wall-clock LTF steps. If a required candle is missing before the trade exits, the row is skipped/marked missing with an explicit LTF gap/incomplete status. Real quote_volume and number_of_trades are required for flow logic; close*volume proxy is not used.
```

Validation:

```text
compileall passed for data/exchanges research_tools cli constants.py main.py. launcher.py is not present in the uploaded workspace. Synthetic strict-window smoke checked: stop before a later gap stays closed; gap before exit becomes skipped; no-hit future label before a gap becomes missing.
```

Risk:

```text
Fresh discovery runs will likely show many skipped/missing rows until subminute cache coverage is continuous. This is intended: missing time must reduce evidence, not inflate PnL. Existing pre-P429 artifacts remain tainted by sparse-row exit simulation and should not be used as edge proof.
```

## 2026-05-27 - P428 applied locally - runner discovery 5m profile set and daily artifacts

Files:

```text
cli/commands.py
research_tools/htf_ltf_runner_discovery.py
tests/test_htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Replace the weak `1m_5s` runner discovery profile with two 5m-HTF alternatives and make daily stability visible in standard artifacts.
```

Change:

```text
The fixed `run-htf-ltf-runner-discovery --days N` profile set is now `5m_30s`, `5m_1m`, and `5m_15s`. The command still requires only `--days`. Each profile now writes daily summary artifacts for selected trades and entry-window research trades, raw and same-symbol live-filtered.
```

Validation:

```text
Focused tests passed: 45 passed. compileall passed for data/exchanges research_tools cli constants.py main.py. Module smokes for BSB on `5m_1m` and `5m_15s` wrote the new daily artifacts. The current 30d run was not regenerated by this patch; its day breakdown was computed from existing artifacts.
```

Risk:

```text
Changing TF profiles invalidates direct comparison with old `1m_5s` output. Use old `1m_5s` only as rejected research evidence; new strategy validation should compare 5m-based profiles on a fresh run after P427/P428.
```

## 2026-05-27 - P427 applied locally - remove future-tainted setup nature

Files:

```text
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Keep HTF/LTF runner discovery artifacts honest after the 30d analysis found that `setup_nature` included one future-derived category.
```

Change:

```text
`setup_nature` no longer reads `anomaly_low_broken_before_runner`. The live nature category is now based only on anomaly/dormancy/smooth pregrowth/OI/flow features available at candidate time. Future low-break and +10% runner outcomes remain separate evaluation labels and must not feed live-entry categories.
```

Validation:

```text
Focused tests passed: 44 passed. compileall passed for research_tools/htf_ltf_runner_discovery.py and the standard code set.
```

Risk:

```text
Existing 30d artifacts still contain the old mixed `setup_nature` values and should not use `anomaly_low_broken_after_wakeup` or generic setup-nature shares as live-available filters. Rerun discovery after P427 before relying on setup-nature distributions.
```

## 2026-05-27 - P426 applied locally - fixed-window runner entry research artifacts

Files:

```text
research_tools/htf_ltf_runner_discovery.py
tests/test_htf_ltf_runner_discovery.py
cli/parser.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the next HTF/LTF discovery run useful for honest long-entry strategy research: compare early LTF volume-sustain windows against fader-style volume decay, with OI context, structural SL/trailing, no TP, and no future-label entry leakage.
```

Change:

```text
- Add fixed closed-LTF entry windows per candidate: 5m/30s uses 2/4/6/8 candles; 1m/5s uses 6/12/18/24 candles.
- Each window decides only after its last closed LTF candle and enters at the next LTF open with adverse slippage.
- Add prior 24h spike context and early LTF volume/trade decay/sustain features, including whether current spike exceeds prior spike median/150% median and whether OI is non-negative from pregrowth end.
- Write entry-window artifacts, simulated no-TP structural trades, raw and same-symbol-filtered trade rows, per-rule score tables, and shortlist rows for volume-sustain vs fader-decay rules.
- Score same-symbol overlap per rule, so one broad research window does not suppress later more selective windows from the same strategy rule.
- Keep the standard discovery CLI fixed-profile with only `--days`; TF sets and window counts live in code.
```

Validation:

```text
Focused tests passed: 44 passed. compileall passed for data/exchanges research_tools cli constants.py main.py. BSB 7d 5m/30s profile smoke wrote entry-window artifacts and closed 12 executable fixed-window trades without using future labels as entry filters.
```

Risk:

```text
This patch does not prove edge. It expands honest research observability and adds bounded fixed windows, which can increase runtime versus P425 but should remain far cheaper than the old all-row candidate artifact path. Treat `runner_10pct_next_hour` and clean-runner labels as evaluation labels only.
```

## 2026-05-27 - P425 applied locally - runner discovery gate-first speedup and decay research

Files:

```text
research_tools/htf_ltf_runner_discovery.py
tests/test_htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Speed up HTF/LTF runner discovery without changing entry/exit honesty, and analyze the completed 7d `5m_30s` artifact for early runner-vs-fader volume-decay structure.
```

Change:

```text
Candidate collection now applies the cheap HTF anomaly gate before expensive future-label, HTF-internal LTF, dormancy/pregrowth/OI and signal replay work. The candidate artifact writes only HTF anomaly rows; scanned and pre-artifact rejected row counts stay visible in the funnel and run_config. Regression tests assert rejected HTF rows are not written as candidates.
```

Validation:

```text
Focused tests passed: 42 passed. compileall passed for data/exchanges research_tools cli constants.py main.py. BSB 7d 5m/30s smoke preserved 24 candidates / 6 clean runner labels while reducing runtime to 2.36s and candidate CSV to 17KB for that symbol.
```

Risk:

```text
Candidate CSV scope changed from all scanned HTF rows to HTF anomaly rows only. This is intended for speed and artifact usefulness; scanned/rejected counts remain in funnel. Any analysis that relied on rejected non-anomaly candidate rows should use a separate diagnostic scan, not the standard discovery artifact.
```

## 2026-05-27 - P424 proposed - clean up speed-patch regressions

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Remove regressions introduced by the recent speed/diagnostics patches without changing signal logic, execution model, fees/slippage, TP/SL, portfolio filtering, or data-quality contract.
```

Changes:

```text
- Make symbol-level workers opt-in again by defaulting anomaly-lab and HTF/LTF runner discovery to 1 worker. P420's default 4 workers can be slower on Windows/Parquet IO-bound runs.
- Restore early trusted subminute cache prefilter for multi-TF candidate precollection. P419's file-presence-only prefilter could push invalid/partial cache symbols into the expensive main pass.
- Make that trusted prefilter metadata-only and cached per entry-cache/entry-timeframe pair, avoiding the old full-parquet read-all behavior.
- Keep final flow validation in the main symbol pass unchanged.
- Stop timing the speed-diagnostics CSV writes inside the speed-diagnostics rows themselves, avoiding self-referential artifact-write noise.
- Clean the duplicate `seconds` column in the empty speed diagnostics frame.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --help
python main.py run-htf-ltf-runner-discovery --help
```

Risk:

```text
No research/trading math is changed. Default serial execution may be slower only on machines where diagnostics prove CPU-bound work; in that case opt in with `--backtest-symbol-workers N`.
```

## 2026-05-27 - P423 proposed - responsive Ctrl+C for long backtests

Files:

```text
main.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make long anomaly backtests operable from the terminal. A first Ctrl+C must be visible and request a normal stop; a repeated Ctrl+C must force process exit instead of waiting indefinitely for thread-pool workers or pandas/parquet IO to unwind.
```

Changes:

```text
- main.py installs a responsive SIGINT handler before command execution.
- First Ctrl+C prints an operator-visible stop message and raises KeyboardInterrupt.
- Repeated Ctrl+C prints a force-exit message and exits with code 130 via os._exit, avoiding hangs on non-daemon worker threads.
- _run_with_logging now prints the user stop message to console instead of only writing it to the log.
- anomaly-lab and HTF/LTF runner ThreadPoolExecutor sections cancel queued futures and avoid context-manager shutdown waits when interrupted.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --help
python main.py run-htf-ltf-runner-discovery --help
```

Risk:

```text
No trading or research math is changed. A forced second Ctrl+C intentionally skips normal cleanup and can leave partial artifacts from the interrupted run; this is preferable to an operator being unable to stop a hung backtest.
```

## 2026-05-27 - P422 proposed - anomaly-lab speed diagnostics

Files:

```text
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Stop guessing about backtest speed. Add low-overhead timing artifacts that show stage-level, per-symbol, and CSV-write costs without changing candidate logic, execution model, fees, slippage, TP/SL, portfolio filtering, or data-quality gates.
```

Changes:

```text
- anomaly-lab pair runs now write anomaly_speed_diagnostics.csv, anomaly_speed_summary.csv, and anomaly_slowest_symbols.csv beside anomaly_timing_summary.csv.
- Multi-timeframe precollection now writes root-level anomaly_lab_precollection_speed_diagnostics.csv, anomaly_lab_precollection_speed_summary.csv, and anomaly_lab_precollection_slowest_symbols.csv.
- Candidate collection records per-symbol setup/entry parquet read time, flow validation time, slice/aggregate time, collector time, output row count, and errors.
- Trade simulation records per-symbol entry-frame read time, latency 1s cache time, long-signal simulation time, signal count, and closed/skipped count.
- CSV artifact writing records per-file rows, columns, bytes, status, and write seconds.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --help
```

Risk:

```text
This is diagnostics-only. It adds small timing/list bookkeeping overhead and extra CSV writes at the end of the run. It should not affect trading results; compare candidate/signal/trade counts before/after if needed.
```

## 2026-05-27 - P421 proposed - cached targeted-flow and prepump fast path

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Speed up repeated honest anomaly-lab runs after P420 did not reduce wall time. Avoid rereading and rewriting already-covered targeted 1s/subminute cache windows, and keep the expensive offline pre-pump separability study opt-in instead of running it on every normal backtest.
```

Changes:

```text
- Targeted 1s backfill now does a narrow parquet metadata coverage check first. If the requested window is already trusted, it records exists_covered_requested_window and avoids full 1s frame loading.
- Subminute materialization now skips any target timeframe whose existing materialized cache already covers all requested interval buckets with trusted 1s aggregation metadata.
- run-anomaly-lab exposes --write-prepump-context and defaults it to false; the core backtest still writes candidate/signal/trade/funnel/honesty/timing artifacts.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --help
```

Risk:

```text
This is intended to preserve trading results. It skips work only when trusted cache metadata proves the requested windows are already covered. The pre-pump context study becomes opt-in from the CLI because it is offline diagnostic research, not the execution/honesty contract. Enable it explicitly with --write-prepump-context true when that artifact is needed.
```

# Anomaly Patch Log

## 2026-05-27 - P420 proposed - symbol-parallel backtest execution

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Further reduce cache-only backtest wall-clock time without changing candidate rules, signal availability, execution prices, TP/SL math, fees, slippage, or portfolio filtering semantics.
```

Change:

```text
1. Adds bounded symbol-level worker threads, default 4 and capped at 8 / CPU count / symbol count, with --backtest-symbol-workers to force a different value or 1 for serial runs.
2. Multi-TF anomaly candidate precollection now processes independent symbols concurrently and merges results back in sorted symbol order before per-pair sorting/enrichment.
3. Trade simulation resolves per-symbol independent trade paths concurrently, then applies the existing portfolio overlap/max-open filter in the original decision-time order. This preserves portfolio semantics while parallelizing expensive cache reads and path simulation.
4. HTF/LTF runner discovery processes independent symbols concurrently and merges candidate/signal/trade/quality rows back in the original selected-symbol order.
```

Validation:

```text
Sandbox validation passed: python -m compileall -q data/exchanges research_tools cli constants.py main.py. Patch applies with git apply --check --ignore-whitespace against the P419 workspace. Full before/after artifact equality still requires the local cache; run a small fixed-symbol/fixed-period comparison with --backtest-symbol-workers 1 vs default 4.
```

Risk:

```text
Low-to-medium operational risk. The strategy/execution model is unchanged, but default parallelism can increase peak memory and disk IO because up to four symbols may load subminute frames at once. If the machine starts swapping or the disk queue explodes, run with --backtest-symbol-workers 2 or 1.
```

## 2026-05-27 - P419 proposed - safe backtest hot-path speedup

Files:

```text
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research_tools/htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Reduce backtest wall-clock time without changing signal selection, future-label separation, entry pricing, stop/TP math, fees, slippage, or artifact honesty.
```

Change:

```text
1. Anomaly-lab subminute collection no longer pre-reads every 5s/15s/30s or fallback 1s parquet file just to build an eligible-symbol set. It now uses file presence for the cheap prefilter and keeps the exact trusted-flow validation in the main per-symbol load path.
2. Multi-timeframe anomaly-lab precollection now tells each per-pair run when candidates already include recent-spike and OI context, avoiding duplicate context enrichment passes immediately after the shared symbol-major collection. External --reuse-candidates-dir still refreshes context by default.
3. OHLCV slicing no longer rebuilds availability columns when the loaded frame already has them.
4. Structural trailing loops in anomaly backtest and HTF/LTF runner discovery keep the rolling prior lows in memory instead of rescanning the whole future frame on every candle.
```

Validation:

```text
Sandbox validation passed: python -m compileall -q data/exchanges research_tools cli constants.py main.py. Synthetic old/new smoke comparisons matched for anomaly structural trailing and HTF/LTF runner structural trailing. Still run the same small local backtest before/after and compare anomaly_signals.csv/anomaly_trades.csv key counts plus skip reasons because full cache artifacts are not in the uploaded zip.
```

Risk:

```text
Low. Flow validation is not removed; it moves from duplicate pre-scan to the existing authoritative per-symbol path. The in-memory trailing-low windows intentionally reproduce the previous timestamp < current candle semantics on deduplicated, sorted frames.
```

## 2026-05-27 - P418 applied locally - compact runner discovery progress

Files:

```text
research_tools/htf_ltf_runner_discovery.py
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Stop printing hundreds or thousands of per-symbol progress lines during HTF/LTF runner discovery runs.
```

Change:

```text
The runner discovery scanner now renders one carriage-return progress line per profile with processed count, percent, current symbol, and ETA. Each profile still prints one final summary line. Non-interactive output is throttled to start/end lines to avoid massive logs.
```

Validation:

```text
Focused tests passed: 41 passed. compileall passed for data/exchanges research_tools cli constants.py main.py. Small two-symbol module smoke showed compact start/end progress lines and one final summary line. A full `main.py ... --days 1` smoke was intentionally stopped after the short validation timeout because it began scanning the full cache.
```

Risk:

```text
Progress is now less verbose by design. Detailed per-symbol diagnostics remain in CSV artifacts, not stdout.
```

## 2026-05-27 - P417 applied locally - runner discovery same-symbol-only overlap

Files:

```text
research_tools/htf_ltf_runner_discovery.py
tests/test_htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove the unintended portfolio-wide concurrent position cap from HTF/LTF runner discovery. The only replay overlap limit should be one open position per symbol.
```

Change:

```text
The live-filtered discovery artifact now allows parallel positions on different symbols and skips only a new signal for the same symbol while that symbol already has an open simulated position. The old `max_open_positions` config field and max-position skip reason were removed from this discovery tool.
```

Validation:

```text
Focused tests passed: 41 passed. compileall passed for data/exchanges research_tools cli constants.py main.py. Search confirmed no `max_open_positions`/portfolio-cap code remains in `htf_ltf_runner_discovery`.
```

Risk:

```text
Live-filtered trade count can increase materially versus the old cap-1 artifact. This is intended; aggregate return is still not account return because position sizing/margin allocation is not modeled.
```

## 2026-05-27 - P416 applied locally - fixed TF-set runner discovery command

Files:

```text
cli/parser.py
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove tuning/timeframe flags from the runner discovery command and make the agreed TF sets part of the code contract.
```

Change:

```text
`run-htf-ltf-runner-discovery` now accepts only `--days`. It always runs two cache-only profiles: `5m_30s` and `1m_5s`, each into its own output subdirectory under `htf_ltf_runner_discovery_<days>d`, with profile-specific confirmation/trailing/max-hold candle counts.
```

Validation:

```text
Help output exposes only `--days` for `run-htf-ltf-runner-discovery`. Focused tests passed: 40 passed. compileall passed for data/exchanges research_tools cli constants.py main.py.
```

Risk:

```text
This intentionally removes ad-hoc CLI tuning for the discovery command. Future parameter research should change named profiles in code, not one-off shell flags.
```

## 2026-05-27 - P415 applied locally - complete HTF/LTF runner discovery scoring

Files:

```text
research_tools/htf_ltf_runner_discovery.py
cli/parser.py
cli/commands.py
tests/test_htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Complete the runner-discovery backtest so it is useful for learning early runner nature, not just replaying a raw structural-no-TP trade stream.
```

Change:

```text
The discovery run now scores pre-entry candidate rule sets, live-filtered trade rule sets, winrate/PNL/top20-dependency balance, and a research shortlist. Candidate rows also expose HTF-internal LTF distribution/acceleration features so single-print spikes can be separated from sustained volume/trade awakenings. CLI exposes the main dormancy, pregrowth, LTF acceptance, risk and trailing knobs. The run config and honesty report explicitly mark the backtest as cache-only with no exchange/seconds download.
```

Validation:

```text
Focused tests passed: 40 passed. compileall passed for data/exchanges research_tools cli constants.py main.py. Cache-only 5m/1m CLI smoke on CATI/ZEC completed and wrote empty-but-valid artifacts without exchange fetching.
```

Risk:

```text
Rule scores are discovery diagnostics, not a live edge claim. Future +10% labels are used only for post-event evaluation and shortlist context; entry replay remains next-LTF-open after closed confirmation with structural SL/trailing and no TP.
```

## 2026-05-27 - P414 applied locally - live2 stop-recovery symbol match and HTF/LTF runner discovery

Files:

```text
research_tools/anomaly_live2/position_supervisor.py
research_tools/htf_ltf_runner_discovery.py
cli/parser.py
cli/commands.py
tests/test_live2_market_watch.py
tests/test_htf_ltf_runner_discovery.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the live2 crash `NameError: _same_symbol is not defined` during stop-close PnL recovery, then add an honest HTF/LTF runner discovery replay for learning early +10% runner structure without TP.
```

Change:

```text
`position_supervisor` now compares user-data order events to protected positions through Binance market-id normalization, so `CATI/USDT:USDT` and `CATIUSDT` match without crashing. New command `run-htf-ltf-runner-discovery` writes HTF anomaly labels, LTF confirmation signals, no-TP structural stop/trailing trades, live-filtered trades, funnel, label distribution, data-quality, top-dependency and honesty artifacts.
```

Validation:

```text
Focused live2/discovery tests passed: 37 passed. compileall passed for data/exchanges research_tools cli constants.py main.py. Discovery and CLI smokes wrote artifacts for CATI/ZEC.
```

Risk:

```text
The discovery tool is research-only. It labels future +10% runners but does not use that label for entry. OI is only as honest as cached 5m open_interest availability; missing OI is visible in data-quality artifacts and cannot prove runner nature.
```

## 2026-05-26 - P413 applied locally - unlimited live2 positions and truthful stop-close PnL

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/status_grid.py
research_tools/anomaly_live2/telegram.py
research_tools/anomaly_live2/user_data_stream.py
cli/parser.py
cli/commands.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Allow live2 to hold more than one protected position, fix operator-grid trade outcome counters, and stop reporting fake +0 USDT PnL when an exchange stop flattens a position.
```

Change:

```text
`execution_max_open_positions=0` now means unlimited protected positions and is the live2 default; a positive value still enforces a hard cap. CLI exposes `--execution-max-open-positions`. The runner passes session-scoped execution status into the grid, and the grid now displays final/early/SL/BE/TP counters plus realized PnL from the supervisor. Stop-trigger final closes try to recover realized PnL from private user-data `ORDER_TRADE_UPDATE` events matched by stop client/order id. If no matching fill is available, Telegram prints `PNL: n/a` instead of using the protected-position default zero.
```

Validation:

```text
Focused live2 tests passed: 34 passed. compileall passed for data/exchanges research_tools cli constants.py main.py.
```

Risk:

```text
Unlimited positions removes the local portfolio cap; exchange/account exposure is now bounded by signal frequency, order notional, exchange margin, and per-symbol duplicate protection only. Stop PnL recovery depends on recent private user-data events; if the event is missing or old, artifacts and Telegram mark PnL unavailable rather than inventing a fill.
```

## 2026-05-26 - P412 applied locally - runner shape gate for rolling live/backtest categories

Files:

```text
research_tools/anomaly_category_contract.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_strategy_backtest.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Tighten runner entries after the 20260526_120454 live audit without simply raising quote-volume. Require a real rolling wake-up shape: quote volume, number_of_trades, and range must expand together across the full 12x5s rolling setup, and the setup must not be dominated by one quote-volume print.
```

Change:

```text
The shared category contract is now v11. `runner_oi_confirmed`, `runner_flow`, and `runner_balanced` add runner-shape thresholds for quote/trade/range ratios, second-half acceleration, non-negative second-half return, and top1 quote-share cap. Live2 computes these from the last 12 closed 5s candles only. Backtest candidate rows now compute the same runner_shape_* fields and category profiles apply the same filters, so the new gate is not live-only.
```

Validation:

```text
Focused live2 tests passed: 32 passed. compileall passed for data/exchanges research_tools cli constants.py main.py.
```

Risk:

```text
This is stricter and will reduce trades. It is not proof of edge; it only removes some first-spike/single-print noise and makes the rule auditable. A follow-up rolling 1m/5s backtest/profile run is still required to quantify missed runners versus filtered noise.
```

## 2026-05-26 - P411 applied locally - current OI endpoint in live2 OI poller

Files:

```text
research_tools/anomaly_live2/market_data/open_interest.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/deadline.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the live2 OI poller fetch Binance current OI directly, not only 5m open-interest-history candles.
```

Change:

```text
Each OI poll now fetches `/fapi/v1/openInterest` through `fetch_current_open_interest()` and stores it separately as `current_oi_*`. The 5m `oi_*` history fields remain the 3x5m baseline/change context and are not overwritten by the current point. Near-miss/deadline artifacts expose the current-OI columns directly.
```

Validation:

```text
Focused live2 tests passed: 30 passed. compileall passed for data/exchanges research_tools cli constants.py main.py.
```

Risk:

```text
Current OI is REST-polled, not websocket/tick. It is fresher than 5m history, but still bounded by poll interval, symbol cooldown, request queue and exchange latency.
```

## 2026-05-26 - P410 proposed - fix ticker current-OI signature boundary

Files:

```text
research_tools/anomaly_live2/state.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Complete the P409 state boundary: SymbolStateStore.update_ticker can pass a current-OI snapshot, so SymbolState.update_ticker must accept and persist the same fields.
```

Change:

```text
SymbolState.update_ticker now accepts current_fetched_at_ms/current_timestamp_ms/current_open_interest/current_source/current_status/current_reason, updates the latest current-OI snapshot when provided, and preserves the first valid ok current-OI snapshot for pump-start baseline.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
No trading logic change. This only fixes the state-layer signature mismatch that stopped live2 during startup ticker snapshot.
```

## 2026-05-26 - P413/P414 proposed - live2 unlimited positions and truthful final PnL

Status: PROPOSED against GitHub head / uploaded workspace.

Files: `cli/parser.py`, `cli/commands.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/execution.py`, `research_tools/anomaly_live2/position_supervisor.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/status_grid.py`, `research_tools/anomaly_live2/telegram.py`.

Intent: remove the live2 one-position global cap by making `execution_max_open_positions=0` mean unlimited while keeping per-symbol duplicate protection. Fix the grid totals so TP, early exits, final closes, SL/BE buckets and realized PnL are counted from supervisor action deltas, not only TP events.

Stop-close accounting: stop/flat final closes now recover realized stop PnL from recent private user-data `ORDER_TRADE_UPDATE` events matched by stop order id/client id. If recovery succeeds, final action top-level `realized_pnl_usdt` is cumulative position PnL and `realized_pnl_delta_usdt` is only the stop-leg delta. If recovery is unavailable, Telegram shows `PNL: n/a` instead of the misleading protected-position default `+0 USDT`.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py` passed in the uploaded workspace. Patch apply was checked against a reconstructed GitHub-head baseline for the touched hunks.

## 2026-05-26 - P409 proposed - live2 honest current-OI baselines

Files:

```text
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/state.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Separate current-OI baselines instead of pretending that entry-time OI is pump-start OI. Keep entry-current-OI for execution risk, add pump-start/current-radar and selected-signal current-OI baselines for pump thesis and post-entry supervision.
```

Change:

```text
SymbolState now preserves the first valid current-OI snapshot seen while the symbol is active/radar. Signal features expose that as `pump_start_current_oi_*` and also expose the latest selected-signal current-OI snapshot as `signal_current_oi_*`. Protected positions persist pump-start, signal, and entry current-OI baselines separately. The supervisor reports OI deltas from all three baselines and can early-exit on exhausted flow when current OI has fallen from pump-start, signal, or entry baseline. Default OI-down threshold is a fixed supervisor default of 0.3%; no new CLI flag is added.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Pump-start current OI is the first valid snapshot live2 actually saw after the symbol became active/radar, not a retroactive historical current-OI value at the first price tick. If the current-OI poller was late, the artifact will show that through pump_start_current_oi_last_seen_ms/timestamp_ms; do not call it exact movement birth time.
```

## 2026-05-26 - P408 applied locally - live2 partial TP1, OI context monitor, flow velocity

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/telegram.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Keep TP1, but stop flattening the whole position there. Increase default live2 notional to 12 USDT so a 50% TP leaves about the previous 6 USDT runner. Persist current/anomaly OI context and source-flow speed so management decisions can distinguish continuing flow from exhausted short-cover/squeeze behavior.
```

Change:

```text
Live2 defaults are now 12 USDT notional and `position_supervisor_tp1_close_fraction=0.5`; both are exposed as CLI overrides. At TP1 the supervisor submits a reduce-only close for 50%, verifies the fill, creates/verifies a replacement stop for the remaining exchange amount, then cancels/verifies the old stop before keeping the protected position as `tp1_partial_protected_stop_verified`.

Protected positions now persist `category_id`, entry-time 5m OI fields, and selected source-flow velocity/ratio fields. Runner and post-HTF signal features expose flow window duration, quote/sec and trades/sec. Near-miss/deadline artifacts include the new flow-speed columns. The early-exit decision compares current OI to entry 5m OI and can close a post-MFE position when OI falls while flow is exhausted. The post-TP1 remainder can trail its stop structurally from recent closed post-fill 5s lows.
```

Validation:

```text
Focused live2 tests passed: 29 passed. compileall passed for data/exchanges research_tools cli constants.py main.py.
```

Risk:

```text
OI remains coarse Binance 5m `openInterestHist`, not tick/Coinglass OI. The partial-TP runner may improve upside capture or may add churn; only forward live artifacts with actual fills can judge it. Stop replacement is intentionally strict: failure to verify replacement/old-stop removal becomes a position integrity error.
```

## 2026-05-26 - P407 applied locally - live2 runner categories use rolling 60s setup

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/deadline.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Remove calendar-minute dependency from the legacy live2 runner categories (`runner_oi_confirmed`, `runner_flow`, `runner_balanced`) so they evaluate the same rolling 60s HTF idea as the current live direction.
```

Change:

```text
`_live_backtest_like_setup` no longer starts at floor(decision_time, 1m). It now builds the runner setup from the trailing contiguous 12 closed 5s candles ending at the decision candle. Baseline remains the 60 closed 1m candles ending before the rolling setup window. Near-miss/deadline artifacts expose `live_setup_alignment=rolling_60s_5s_step`, `live_setup_calendar_aligned=false`, and setup open/close timestamps.
```

Validation:

```text
Focused live2 tests passed: 28 passed. compileall passed for data/exchanges research_tools cli constants.py main.py.
```

Risk:

```text
This changes live runner entry timing and live/backtest parity versus old calendar-minute forming backtests. It is closer to the desired live rolling behavior, but old anomaly-lab results for runner categories should not be compared directly without a rolling replay/backtest.
```

## 2026-05-26 - P406 applied locally - live2 post-entry flow/OI early exit

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/telegram.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent live2 long positions from sitting for many minutes after the post-entry buyer flow is exhausted, seller pressure appears, or OI rises while price no longer progresses.
```

Change:

```text
The live2 position supervisor now evaluates only closed 5s candles that fully start after the verified entry fill timestamp. After a default 6 closed-candle minimum hold it can full-close the exchange position with a reduce-only market order when conservative post-entry conditions appear: OI-up/non-progress stall, seller pressure after at least 0.25R MFE, flow exhaustion after at least 0.25R MFE, or prolonged stall without progress.

The close remains actual-fill based: the supervisor verifies the reduce-only close fill, verifies the exchange position is flat, cancels/verifies the old initial stop, emits `position_early_exit_full_close_verified`, and removes the protected position. Defaults are exposed through live2 config and CLI flags.
```

Validation:

```text
Focused live2 tests passed: 27 passed. compileall passed for data/exchanges research_tools cli constants.py main.py.
```

Risk:

```text
This is a live-management safety/expectancy heuristic, not an edge proof. It does not change entry categories, does not solve artifact spam/deadline backlog, and should be judged from forward artifacts by comparing avoided stalls versus missed late TP1 continuations.
```

## 2026-05-26 - P405 applied locally - rolling HTF live2 baseline and OI divergence guard

Files:

```text
cli/parser.py
cli/commands.py
data/exchanges/ccxt_futures_client.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/market_data/warmup.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/deadline.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove calendar-minute dependency from the live2 post-HTF acceptance category without loading universal 75m 5s/aggTrade history.
```

Change:

```text
The post-HTF acceptance category now builds HTF as a rolling 60s window from the 12 closed 5s candles immediately before the 6-candle confirmation window. Startup loads a lightweight 75m raw Binance 1m kline HTF baseline with real quote/trade-count columns intact; the 5s aggTrade warmup stays short/default 15m. Artifacts mark `post_htf_acceptance_htf_alignment=rolling_60s_5s_step` and `post_htf_acceptance_htf_calendar_aligned=false`.

Added a long-entry block when OI is rising while 15m price context is falling: `post_htf_acceptance_oi_divergence_reason=oi_up_price_down_blocked`. Reduced live2 default and CLI order notional to 6 USDT.
```

Validation:

```text
compileall passed for data/exchanges research_tools cli constants.py main.py. Focused live2 tests passed: 25 passed. CLI help exposes startup HTF baseline and 6 USDT notional flags.
```

Risk:

```text
The rolling signal uses real 5s trade-built HTF, while the startup baseline is raw 1m Binance kline baseline. This avoids full-market 5s backfill cost and keeps real quote/trade-count baseline, but it is not a rolling-5s baseline. Artifacts expose the baseline/alignment distinction for later analysis.
```

## 2026-05-26 - P404 applied locally - live2 post-HTF acceptance long trading category

Files:

```text
research_tools/anomaly_category_contract.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/artifacts.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Enable the researched post-HTF acceptance long category in live2, with trading through the existing selected-signal -> entry guard -> real execution path, and make the mode separable in artifacts.
```

Change:

```text
Added `post_htf_acceptance_long` to the shared category contract and default live2 category priority. The signal engine now evaluates a closed 1m HTF anomaly, waits for exactly 6 closed 5s candles after the HTF close, requires LTF acceptance and controlled flow distribution, uses a structural stop at the closed HTF anomaly low with a 5 bps buffer, and sets TP1 at 1.5R. Selected features override the generic live2 stop/TP fields before entry guard/execution.

Artifacts now carry `category_id=post_htf_acceptance_long` and `post_htf_acceptance_*` feature fields in event JSON, plus explicit post-HTF fields in `live2_near_misses.csv` for later separate analysis.
```

Validation:

```text
compileall passed for data/exchanges research_tools cli constants.py main.py; `launcher.py` is absent in this workspace, so the literal AGENTS command cannot list it. Focused live2 tests passed: 23 passed.
```

Risk:

```text
This enables real trading only through existing live2 readiness, entry-guard, max-position, actual-fill and verified-stop gates. It does not bypass safety. Research used a 72h-style prior-spike label; live2 currently has 24h prior context, so artifacts explicitly mark the parity limitation and the first live run must be treated as forward validation, not proof.
```

## 2026-05-25 - P403 applied locally - stricter short/fader HTF anomaly prefilter

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Stop `bare_htf_short_fader` discovery from planning too many targeted 1s aggTrade windows on weak flow-only HTF anomalies.
```

Change:

```text
Added a short/fader HTF anomaly prefilter before targeted 1s flow planning and inside final bare-HTF candidate collection. Defaults: `short_fader_prefilter_min_quote_ratio=10.0`, `short_fader_prefilter_min_trade_ratio=8.0`, `short_fader_prefilter_min_htf_return=0.015`. CLI flags expose all three thresholds. `targeted_flow_plan.csv` records the thresholds and dropped-row count.
Also fixed decay-category and factor-separation artifact bugs where missing `ltf12_*` columns on very small strict runs produced scalar NaN values instead of index-aligned Series.
```

Validation:

```text
compileall passed for research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py. Focused short_fader tests passed: 5 passed. P403 smoke at .output/results/bare_htf_short_fader_p403_smoke completed. targeted_flow_plan.csv: 23 coarse candidates, 21 dropped by short_fader prefilter, 2 coarse_prefilter candidates, 2 targeted windows, 2 ready windows. anomaly_backtest_honesty_report.csv shows 0 failures.
```

Risk:

```text
This intentionally changes the discovery universe. It makes 1s research cheaper and focuses on stronger price-confirmed HTF awakenings, but can miss weak-flow/low-return anomalies that later fade.
```

## 2026-05-25 - P402 applied locally - short/fader decision availability contract

Files:

```text
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the bare-HTF short/fader artifact contract so post-close LTF trigger rows truthfully state when the decision became available.
```

Change:

```text
Short/fader candidates now carry the standard OHLCV timestamp semantics (`ohlcv_timestamp_is_candle_open;available_timestamp_is_candle_close`). Trigger rows override `decision_available_timestamp_ms` to the close of the triggering LTF candle (`decision_timestamp_ms + entry_timeframe_ms`) instead of inheriting the HTF-close timestamp from the base anomaly row.
```

Validation:

```text
compileall passed for research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py. Focused short_fader tests passed: 4 passed. P402 smoke at .output/results/bare_htf_short_fader_p402_smoke completed; anomaly_backtest_honesty_report.csv shows OHLCV cache loader availability and baseline calculations ok with 0 failures.
```

Risk:

```text
This fixes artifact truthfulness and honesty-report availability validation. It should not change simulated entry timing because short/fader execution was already using the next LTF open after the trigger timestamp.
```

## 2026-05-25 - P401 applied locally - short/fader category analysis script

Files:

```text
research_tools/short_fader_category_analysis.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add a standalone analyzer over `bare_htf_short_*` discovery artifacts so decay/fader categories can be extracted after a wide run without rerunning the backtest.
```

Change:

```text
Added `python -m research_tools.short_fader_category_analysis`. It reads a bare-HTF short/fader run directory, builds event/category buckets, scores rule families across trigger, decay category, prior context, LTF direction, taker weakness, red-share, and trigger delay, joins raw/live-filtered trade outcomes by event key, and writes `short_fader_category_analysis/*` artifacts: summary, event category matrix, rule scores, category candidates, and watchlist.
```

Validation:

```text
compileall passed for research_tools/short_fader_category_analysis.py. Smoke analysis completed on .output/results/bare_htf_short_fader_expanded_smoke and wrote five category-analysis CSV files.
```

Risk:

```text
This is post-run research analysis only. Candidate rules are in-sample discoveries and must be replayed separately before any trading conclusion.
```

## 2026-05-25 - P400 applied locally - expanded wide short/fader discovery artifacts

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the short/fader mode useful as a broad discovery dataset for later category extraction by decay nature and post-close LTF path, not as a narrow fixed trigger test.
```

Change:

```text
Expanded default trigger library to `failed_new_high,taker_fade_red,close_below_htf_close,close_below_post_mid,lower_high_close_down,effort_no_progress,pullback_without_recovery`. Added compact post-close LTF path artifact `bare_htf_short_post_close_path_slices.csv`. Added heuristic decay-category artifacts `bare_htf_short_decay_category_events.csv` and `bare_htf_short_decay_category_summary.csv` for research triage. Signal priority now keeps one first/priority trigger per HTF event for execution while preserving all trigger rows in `bare_htf_short_triggers.csv`.
```

Validation:

```text
compileall passed for research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py.
Focused short_fader tests passed. Expanded smoke at .output/results/bare_htf_short_fader_expanded_smoke completed: 142 candidate rows / 76 events, 81 triggered rows / 15 triggered events, 15 discovery signals, 15 raw closed trades, 14 live-filtered closed trades. New path/category artifacts were written.
```

Risk:

```text
The new decay categories are heuristic research labels, not trading categories. They are meant to guide manual/statistical analysis on a larger discovery run.
```

## 2026-05-25 - P399 applied locally - make bare-HTF short/fader a wide discovery artifact mode

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Stop treating the first short/fader pass as a parameter/category optimization problem. Collect a wide discovery dataset from bare HTF anomalies and post-close LTF behavior, then derive decay/fader categories from artifacts later.
```

Change:

```text
`bare_htf_short_fader` now uses wide discovery by default: every enabled post-close short-pressure trigger can become a discovery signal. Prior spike/fade thresholds are annotation columns, not an entry gate, unless `--short-fader-require-prior-context true` is explicitly passed. RR exit grid is disabled by default through `--short-fader-run-exit-grid false`. Added discovery artifacts: label distribution, data-quality summary, funnel, top trades, and live-filtered top trades.
```

Validation:

```text
compileall passed for research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py.
Focused short_fader tests passed. Discovery smoke at .output/results/bare_htf_short_fader_discovery_smoke completed: 79 candidate rows / 76 unique events, 16 triggered rows / 13 triggered events, 13 discovery signals, 13 raw closed trades, 10 live-filtered closed trades. The smoke result was negative and is not an edge claim.
```

Risk:

```text
Wide discovery intentionally includes dirty signals. Profitability should not be judged until artifacts are analyzed into separate decay/fader nature categories and then replayed out-of-sample or on a larger period/universe.
```

## 2026-05-25 - P398 applied locally - honest bare-HTF short/fader backtest mode

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Implement the separate short/fader research path from bare closed HTF anomalies without reusing long continuation categories as short entry classes.
```

Change:

```text
Added `--pair-collection-mode bare_htf_short_fader` with `feature_contract=bare_htf_short_fader_v1`. The mode collects closed HTF anomaly events, scans only post-HTF-close LTF behavior inside the configured analysis window, detects `failed_new_high` and `taker_fade_red` triggers, filters by prior crowding/fade context, and simulates short entries at the next LTF open with adverse short slippage, structural stop, fixed RR target, max hold, cap-1 live filter, skip reasons, factor separation, trigger summaries, top dependency, edge health, and RR exit grid artifacts.
```

Validation:

```text
compileall passed for research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py.
Focused tests passed: bare HTF short/fader collector waits until post-HTF close; short fader execution enters at next LTF open and computes short-side PnL.
Smoke run completed at .output/results/bare_htf_short_fader_smoke with feature_contract=bare_htf_short_fader_v1 and slippage_model=adverse_short_entry_and_exit. The tiny INJ/BEAT smoke produced 79 candidates and 0 final signals under strict prior crowding/fade filters; this validates plumbing, not edge.
```

Risk:

```text
This is a research backtest mode only. It does not enable live shorts. Edge still requires larger period/universe validation, cap-1/live-filtered readout, top-dependency review, and data-quality audit.
```

## 2026-05-25 - P397 research plan - honest bare-HTF short/fader backtest

Files:

```text
research/EXPERIMENT_LOG.md
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Record the next short/fader research protocol before implementation so future code changes do not mix this path with long continuation categories.
```

Change:

```text
Added the planned honest bare-HTF short/fader backtest protocol: closed HTF anomaly, post-close LTF feature windows within 60 minutes, independent factor search, trigger-based next-LTF-open short execution, structural stop, RR variants, cap-1 portfolio filtering, and top-dependency/distribution artifacts.
```

Validation:

```text
No code validation required; research memory update only.
```

Risk:

```text
This records a plan, not a completed implementation or edge proof.
```

## 2026-05-25 - P396 spec update - proposed bare-HTF short/fader contract

Files:

```text
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Document the short/fader research strategy as a separate contract so it is not confused with long continuation categories.
```

Change:

```text
Added a proposed short/fader research contract: closed HTF anomaly first, post-close HTF/LTF analysis within one hour, prior crowding/fade context, post-close LTF short-pressure triggers, structural stop, and full RR2.0-2.5 exit. Explicitly rejects converting long categories into short categories and marks partial/BE/trailing as not proven defaults.
```

Validation:

```text
No code validation required; documentation/spec update only.
```

Risk:

```text
This is a proposed research contract, not implemented live short logic and not an edge proof. It requires larger-period/universe validation with cap-1/live-filtered artifacts before live use.
```

## 2026-05-25 - P395 applied locally - add post-HTF forward LTF confirmation mode

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add a separate honest mode for the idea: closed HTF candle N proves setup interest, then LTF confirmation is searched only after N closes, starting in N+1. Left LTF context remains available for rejection/context, but no entry is allowed retroactively inside HTF N.
```

Change:

```text
New CLI mode `--pair-collection-mode post_htf_close_ltf_forward_confirmation` with feature_contract `post_htf_close_ltf_forward_confirmation_v1`. Candidate collection first checks closed HTF setup anomaly, requires full LTF left context, then emits LTF decision rows from the next HTF window only after enough forward confirmation candles. Targeted flow windows include left context, the HTF setup, the forward confirmation window, and the immediate execution tail.
```

Validation:

```text
compileall passed for data\exchanges data\fetchers research_tools cli constants.py main.py.
Focused tests passed for post-HTF left-context and post-HTF forward-confirmation collectors.
```

Risk:

```text
This is not current live2 parity. It is a late-confirmation research mode. Results should be compared against forming live-parity runs separately, and only after artifacts show the forward contract columns and entry timestamps after HTF close.
```

## 2026-05-25 - P394 applied locally - add post-HTF LTF left-context parity

Files:

```text
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make `post_htf_close_ltf_confirmation` honest about LTF context: the backtest may inspect LTF candles before the anomalous HTF candle only after the HTF close decision point, but must still enter no earlier than the HTF close. This mirrors live having already accumulated left-side LTF tape/context without allowing retroactive fills.
```

Change:

```text
Post-HTF-close targeted flow windows now include a left LTF context window equal to the HTF baseline duration. Pair collection slices entry frames with that left context, the symbol-major multi-pair collector now respects the post-HTF collector, and post-HTF rows require full left-context LTF coverage. In this mode `prior_up_down_whipsaw_to_impulse_range` is sourced from `entry_timeframe_left_context`; artifacts expose left-context status/source/window fields.
```

Validation:

```text
compileall passed for data\exchanges data\fetchers research_tools cli constants.py main.py.
Focused new post-HTF left-context test passed.
Full tests/test_anomaly_continuation_lab.py still has two pre-existing/current-HEAD forming-mode failures: tests expect entry-derived baseline for forming 1m/5s, while current collector uses setup-timeframe baseline. This is a separate parity question, not fixed by P394.
```

Risk:

```text
This can reduce signals because missing left LTF context now removes post-HTF candidates instead of silently evaluating only the HTF baseline. It also increases targeted aggTrade fetch size for post-HTF runs by baseline_candles * setup_timeframe per planned setup. It does not make late post-HTF mode an early-entry proof.
```

## 2026-05-24 - P393 proposed - add post-HTF-close LTF confirmation mode

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Add a cheap honest pair-mode where an HTF setup candle is selected only after it closes, LTF candles inside that closed HTF candle are used only as after-close confirmation, and the simulated entry cannot occur inside the already-closed HTF candle. This separates late HTF+LTF confirmation from the existing forming-LTF early-entry mode.
```

Change:

```text
run-anomaly-lab now accepts --pair-collection-mode forming|post_htf_close_ltf_confirmation. The new mode uses feature_contract=post_htf_close_ltf_confirmation_v1, emits one candidate per closed HTF setup candle with full LTF coverage, records explicit post_htf_close_* audit fields, and keeps market entry on the next LTF candle after the HTF close via the existing next-bar-open execution model. Honesty report adds a dedicated post-HTF-close collector node.
```

Validation:

```text
python -m compileall -q data/exchanges data/fetchers research_tools cli constants.py main.py
Synthetic smoke confirmed a 5m/1m post-close row has decision_available_timestamp_ms == post_htf_close_entry_not_before_ms and the market entry timestamp is not earlier than the HTF close.
```

Risk:

```text
This mode is honest for after-close confirmation, not proof of early intra-HTF LTF entry. It is later by construction, so RR/TP/drift guards may skip many moves that the old forming mode appeared to catch. If used with subminute entry timeframes and targeted backfill, the result is still a selected-event diagnostic unless LTF coverage and fetch latency are explicitly treated as the tested live contract.
```

## 2026-05-23 - P392 applied locally - add live-like portfolio filter after wide simulation

Files:

```text
cli/commands.py
constants.py
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Preserve category-discovery material while still producing a live-like one-position portfolio readout. The anomaly-lab raw simulation default stays wide at max_open_positions=1000, then a deterministic post-simulation live portfolio filter writes separate cap-1 artifacts and counts what was cut. Reused-candidate run_config parsing also accepts legacy `WindowsPath(...)`/`PosixPath(...)` strings inside lab_config so old guarded candidate sets can be replayed.
```

Validation:

```text
compileall passed for data\exchanges data\fetchers research_tools cli constants.py main.py.
Focused tests passed: anomaly portfolio cap, live portfolio filter, and live2 execution timing.
Reused-candidate 1m/5s replay wrote core raw/live-filter trade artifacts at .output/results/anomaly_lab/live_parity_1m_5s_p392 before the command timed out during late artifact stages with render_charts=false.
```

Risk:

```text
Raw anomaly_trades.csv remains a wide research simulation, not live PnL. Live-readiness must be read from anomaly_trades_live_filtered.csv and matching live_filtered summaries. The post-filter is still candle-level proxy execution, not exchange actual fills.
```

## 2026-05-22 - P385 applied locally - backtest honesty hardening

Files:

```text
research_tools/anomaly_strategy_backtest.py
cli/parser.py
cli/commands.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Close remaining non-lookahead self-deception gaps in anomaly-lab execution parity: default backtest execution must no longer be unbounded versus live2, and fill/exit prices must include an explicit adverse slippage model instead of acting as frictionless candle prices.
```

Change:

```text
Anomaly backtest config now defaults to max_open_positions=1, entry_slippage_pct=0.0005 and exit_slippage_pct=0.0005. Market and trigger entries use raw candle/trigger price plus adverse long-entry slippage; exits and TP1 fills use adverse long-exit slippage. Trade artifacts expose raw/fill prices, fill models, slippage fields and portfolio state. simulate_anomaly_trades now enforces same-symbol overlap and global max_open_positions at the actual simulated entry timestamp. run-anomaly-lab CLI exposes --max-open-positions, --entry-slippage-pct and --exit-slippage-pct. Each run now writes anomaly_backtest_honesty_report.csv with the 28 audited nodes from the lookahead/parity checklist.
```

Validation:

```bash
python -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m pytest tests\test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe main.py run-anomaly-lab --symbols INJ/USDT:USDT BEAT/USDT:USDT --days 2 --setup-timeframe 1m --entry-timeframe 1m --render-charts false --output-dir .output\results\anomaly_lab\lookahead_honesty_p385_closed_1m
```

Risk:

```text
Backtest results become stricter and are not directly comparable to older frictionless/unbounded runs. The slippage model is still a conservative candle-level proxy, not order-book replay. Portfolio cap ordering is deterministic by decision timestamp and symbol for same-timestamp signals; this is stricter than assuming invisible simultaneous fills.
```

## 2026-05-22 - P384 applied locally - backtest lookahead closure audit

Files:

```text
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_continuation_lab.py
research_tools/runner_fader_prepump_context.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Close remaining lookahead/optimism gaps found after P374-P383 and make the affected audit paths testable: execution must evaluate the entry candle, forming HTF setup availability must match the LTF decision close, chart/prepump context must be as-of only, and closed-TF candidates must expose the same prior/flow-hold fields required by category replay.
```

Change:

```text
Market-entry simulation now includes the entry candle in the post-entry path and uses stop-first intrabar conflict handling there. Delayed market entry also rejects if TP1 was already reached before the delayed fill. Forming HTF pair candidates now set setup_available_timestamp_ms to the entry decision availability timestamp and separately record setup_full_available_timestamp_ms. Trade artifacts carry setup/decision availability fields. Entry-grid signal sets are rebuilt after derivatives-context enrichment. Trade chart 1h context/levels are computed from closed hours available at decision availability, not from post-exit chart range. Runner/fader prepump windows now select rows by available_timestamp_ms < pump anchor; spot/OI/context readers attach or require availability. Closed-TF candidates now write flow_hold_* and price_retention_model fields and all run paths refresh prior 24h context before category replay.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m compileall data\exchanges research_tools cli constants.py main.py launcher.py
.venv\Scripts\python.exe main.py run-anomaly-lab --symbols INJ/USDT:USDT BEAT/USDT:USDT --days 2 --timeframe 1m --end-timestamp-ms 1779426180000 --render-charts false --output-dir .output\results\anomaly_lab\lookahead_audit_p384_closed_1m
```

Risk:

```text
Backtest results can become more conservative because entry-candle stop/TP conflicts are no longer skipped. Old derivatives funding caches without available_timestamp_ms are still refused and appear as explicit missing_available_timestamp diagnostics; this is intentional until those caches are rebuilt or migrated with a proven source contract.
```

## 2026-05-22 - P379 proposed - closed-candidate future-label boundary guard

Files:

```text
research_tools/anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix a critical lookahead/audit leak in the closed setup candidate collector: candidate existence must not depend on whether the full future outcome-label horizon is already present. The collector now only requires data through the decision candle. Future outcome fields are still written for research when the horizon is complete, but incomplete right-edge labels are explicitly marked as `unlabeled_insufficient_future`.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low for live/backtest signal logic: `build_anomaly_signals()` does not read `future_*` or `outcome_label`. Near the right edge, closed-TF runs may now produce additional candidates/signals that are later skipped by execution if no future execution candles exist, which is more honest than silently hiding them during candidate collection.
```

## 2026-05-22 - P383 proposed - make derivatives context availability-aware

Files:

```text
data/exchanges/ccxt_futures_client.py
data/fetchers/derivatives_context_fetcher.py
research_tools/anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical p.13 derivatives-context lookahead gap: funding/premium/mark/long-short/taker context must be selected by an explicit availability timestamp, not merely by the exchange period timestamp.
```

Change:

```text
Derivatives context fetch now writes `available_timestamp_ms` for all context sources. Mark/premium klines use raw Binance close time + 1ms and are dropped if unavailable by the request/fetch cutoff. Long-short and taker ratio rows use a conservative period timestamp + period lag. Funding rows use fundingTime as their availability timestamp. Backtest enrichment refuses legacy context caches that lack `available_timestamp_ms` and selects context rows using `available_timestamp_ms <= decision_available_timestamp_ms`.
```

Validation:

```bash
python -m compileall -q data/exchanges data/fetchers research_tools cli constants.py main.py
```

Risk:

```text
Medium operationally: old derivatives context caches without `available_timestamp_ms` are no longer trusted by backtest enrichment and will need to be rebuilt. This is intentional; old caches may contain partial current context candles or ambiguous period timestamps.
```

## 2026-05-22 - P382 proposed - make OI context availability-aware

Files:

```text
research_tools/anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical p.12 Open Interest lookahead gap: backtest must not select an OI row merely because its period timestamp is <= decision time when the row may only be available after the 5m OI period closes.
```

Change:

```text
OI enrichment now computes an availability timestamp for every cached OI row (`timestamp + 5m` unless the cache explicitly stores `available_timestamp_ms`) and selects the latest row with `oi_available_timestamp_ms <= decision_available_timestamp_ms`. Artifacts now separate OI period timestamp from OI as-of/available timestamp and report available cache coverage. Precollected/reused candidates refresh OI context at run time so old candidate CSVs cannot carry pre-P382 OI as-of semantics into signal filtering.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium result impact: OI-confirmed categories may produce fewer signals because the current 5m OI period is no longer usable before its conservative availability timestamp. This is intentional; using period timestamp as known-time is a critical lookahead risk.
```

## 2026-05-22 - P381 proposed - trust-gate flow ratio sources

Files:

```text
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical p.9 flow-ratio trust gap: subminute flow ratios must not be built from old/missing-version aggTrade-derived caches or from candidate rows whose source labels are missing/unknown/proxy.
```

Change:

```text
Backtest now rejects untrusted subminute entry-flow caches before candidate collection and before execution-frame reads. Materializing 5s/15s/30s entry caches now requires a trusted P378 1s aggTrade cache. Signal building requires explicit trusted flow source columns, and pair/forming candidates now label levels/setup flow from the same entry-flow source that actually built the forming HTF candle. The trusted 1s cache version matches P378 (`p378_latency_aggtrades_full_buckets_v1`).
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium operationally: existing pre-P378 1s/5s/15s/30s caches will be rejected until regenerated. This is intentional; otherwise old partial/proxy flow can still influence start_quote_ratio/start_trade_ratio.
```

## 2026-05-22 - P380 proposed - pair/forming candidate future-label boundary guard

Files:

```text
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the critical p.7 pair/forming collector lookahead leak: an HTF/LTF candidate row must not exist only when a full future outcome-label horizon is already present after decision_ts. Candidate construction should depend only on baseline data and closed entry candles through the decision candle.
```

Change:

```text
Removed the future-horizon gate from _collect_symbol_pair_rows(). _build_pair_candidate_row() now writes future_label_status plus observed future high/low candle counts, and marks right-edge labels as unlabeled_insufficient_future instead of dropping the candidate. Future labels remain artifact-only.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low for signal logic: build_anomaly_signals() does not consume future_* or outcome_label. Near explicit right-edge cutoffs, pair/forming runs may now surface candidates that were previously hidden by future-label availability and later get skipped by execution if no post-entry candles exist.
```

## 2026-05-22 - P378 proposed - require full aggTrade aggregation buckets

Files:

```text
research_tools/anomaly_aggtrade_cache.py
research_tools/anomaly_strategy_backtest.py
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical p.5 aggTrade/event-cache leakage where arbitrary REST aggTrade windows could materialize the first or last incomplete bucket as a final OHLCV/flow candle. A 1s row built from only timestamp=start_ms inside a second, or a 5s row built from an event-window edge, must not be treated as closed historical flow.
```

Change:

```text
aggTrade-to-OHLCV aggregation now emits only buckets whose entire [bucket_start, bucket_end] interval is covered by the requested aggTrade window. The helper records candle availability and aggTrade coverage metadata on generated rows. 1s->subminute aggregation uses that coverage metadata to drop target buckets whose full interval is not covered by the source event windows. The 1s and materialized subminute aggregation version strings were bumped so newly generated caches are distinguishable from old partial-bucket caches.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'CHECK'
import pandas as pd
from research_tools.anomaly_aggtrade_cache import aggregate_aggtrades_to_ohlcv_frame
rows = pd.DataFrame([
    {'T': 1000, 'p': '10', 'q': '1', 'm': False},
    {'T': 1999, 'p': '11', 'q': '2', 'm': True},
])
full = aggregate_aggtrades_to_ohlcv_frame(rows, timeframe_ms=1000, start_timestamp_ms=1000, end_timestamp_ms=1999)
assert full['timestamp'].tolist() == [1000]
partial = aggregate_aggtrades_to_ohlcv_frame(rows, timeframe_ms=1000, start_timestamp_ms=1000, end_timestamp_ms=1000)
assert partial.empty
CHECK
```

Risk:

```text
Low/medium. This makes historical aggTrade caches stricter and can reduce rows at targeted-window edges. Existing old caches are not automatically deleted; regenerate/backfill affected 1s and derived 5s/15s/30s caches when using P378 for parity or edge claims.
```

## 2026-05-22 - P377 proposed - drop unavailable OHLCV fetch rows

Files:

```text
data/exchanges/ccxt_futures_client.py
data/fetchers/ohlcv_fetcher.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical p.4 fetch-normalization leak where Binance/CCXT OHLCV fetches can return the currently forming candle, or a historical candle whose open time is <= end_timestamp_ms but whose close/volume/flow were not available at that as-of cutoff. Such a row can poison cache as if quote_volume, number_of_trades and taker_buy fields were final.
```

Change:

```text
Binance kline normalization now uses the raw kline close-time field and drops rows with close_time + 1 > min(end_timestamp_ms, fetch_time_ms). The generic OHLCV fetch path applies the same closed-candle rule from timestamp + timeframe_ms. M10 aggregation from fetched or cached M5 data also drops target buckets that were not closed by the same availability cutoff, preventing partial aggregate candles from being saved as final.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'CHECK'
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
rows = [
    [0, '1', '2', '0.5', '1.5', '10', 59999, '15', 3, '4', '6', '0'],
    [60000, '1.5', '2', '1', '1.8', '20', 119999, '36', 5, '8', '14', '0'],
]
frame = CcxtFuturesClient._normalize_binance_kline_rows(rows, available_cutoff_timestamp_ms=60000)
assert frame['timestamp'].tolist() == [0]
CHECK
```

Risk:

```text
Low-to-medium research-output impact: fresh cache updates can stop saving the latest forming candle. That is intentional. It may reduce the newest cached row by one candle but prevents partial flow from being treated as final historical data.
```

## 2026-05-22 - P376 proposed - make OHLCV cache windows availability-aware

Files:

```text
data/storage/parquet_storage.py
research_tools/anomaly_continuation_lab.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical p.3 ambiguity where cached OHLCV `timestamp` is the exchange candle open time, but high/low/close/volume/flow are only known after the candle closes. Historical as-of windows must not include a candle merely because its open timestamp is <= end_timestamp_ms.
```

Change:

```text
ParquetStorage now attaches explicit `candle_open_timestamp_ms`, `candle_close_timestamp_ms`, and `available_timestamp_ms` to loaded OHLCV frames. Closed-TF and pair candidate collectors now slice backtest input windows by candle availability (`available_timestamp_ms <= end_timestamp_ms`) instead of treating open timestamp as known time. Candidate artifacts record setup/decision availability timestamps and timestamp semantics for audit. Aggregated lower-TF frames also carry availability timestamps so partially formed target buckets are excluded at the as-of boundary.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium research-output impact: historical runs with explicit end_timestamp_ms can lose the last open-time candle because it was not yet closed/available. This is intentional. Trading/live code is not changed. This does not solve OI/derivatives publication lag; that remains a separate node.
```

## 2026-05-22 - P375 proposed - guard backtest universe scope

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical universe/symbol-scope self-deception path where a historical anomaly backtest could silently scan the current local cache snapshot and where reused candidates could be treated as the current universe without proving the same symbol scope.
```

Change:

```text
run-anomaly-lab now refuses --end-timestamp-ms with no explicit --symbols unless --allow-cache-snapshot-universe true is passed, making cache-snapshot survivorship bias an explicit operator choice. Backtest artifacts now write anomaly_universe_contract.csv and run_config.csv fields for universe_symbol_scope, requested normalized symbols, historical_listing_snapshot_available=false, and survivorship_bias_risk. --reuse-candidates-dir now requires those universe fields and rejects mismatched explicit/cache-snapshot symbol scope. Explicit symbol matching is normalized so BTC/USDT and BTC/USDT:USDT compare consistently against cached futures paths.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low trading risk. This only affects research/backtest/cache artifact paths. Historical cache-snapshot runs without explicit symbols now need an intentional override; old reusable candidate artifacts without universe metadata are rejected and must be regenerated.
```

## 2026-05-22 - P374 proposed - guard reused backtest candidates

Files:

```text
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Close the critical CLI/config lookahead path where --reuse-candidates-dir could silently feed candidates collected under another window, timeframe pair, symbol set or collection contract into a new anomaly backtest.
```

Change:

```text
Reused anomaly_candidates.csv now requires a sibling run_config.csv and must match critical candidate-collection config exactly: setup/entry timeframe, feature contract, days/end timestamp, baseline/confirmation/future windows, and collection min quote/trade ratios. The loader rejects missing audit columns, invalid decision timestamps, and then filters reused rows to the current timeframe pair, feature contract, explicit end window and requested symbols.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low trading risk. This only affects --reuse-candidates-dir. Old candidate artifacts without run_config.csv are now rejected instead of being trusted; regenerate candidates once to reuse safely.
```

## 2026-05-22 - P373 applied locally - live2 event-cache and backtest parity repair

Files:

```text
cli/commands.py
cli/parser.py
data/fetchers/derivatives_context_fetcher.py
research_tools/anomaly_category_contract.py
research_tools/anomaly_continuation_lab.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Compare live2 trades from runs 20260521_164122 and 20260521_194030 against category-only backtests without building a full 1s cache, then fix concrete live/backtest parity errors and operator-grid misreporting.
```

Change:

```text
AggTrade backfill now supports targeted timestamp windows, so event studies can fill only the 5s/1s cache around interesting live signals/trades. Binance aggTrade pagination was corrected to time-cursor pagination for bounded windows.

Backtest derivatives context now uses availability-aware as-of lookup, mark context is fetched/read at 1m, and artifacts expose mark_asof timestamps. Category contract v9 removes mark-basis as a trading blocker because live has mark WS ticks but historical backtest has only delayed mark klines; mark basis remains diagnostic.

Backtest prior spike/fade context now counts closed cached 5m candles over the same effective 24h live-priority window as live2, and missing/insufficient prior context rejects explicitly instead of acting like zero spikes. Pair-mode 1m/5s candidates now derive setup quote/trade baselines from the same 5s aggTrade cache used for entry flow, avoiding raw-kline trade-count inflation versus live.

Live2 grid/state now records stage/actionable timestamps consistently, `Active` is current/session-seen actionable symbols, and the trading row uses run-level execution status for positions/orders. Supervisor now verifies the transient stop-trigger settlement state before declaring a protected position unprotected.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests\test_anomaly_continuation_lab.py tests\test_live2_market_watch.py -q
.venv\Scripts\python.exe -m compileall data\exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. Backtest/live signal parity is materially closer, but same-period backtest still uses next-bar proxy fills while live uses exchange fills. The next live run must verify that BEAT-like signals are selected earlier now that mark-basis is diagnostic only, and that no runtime gate remains stuck after normal stop-trigger settlement.
```

## 2026-05-21 - P370 applied locally - deeper live2/backtest signal parity

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/artifacts.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make live2 signal construction match the backtest candidate/filter math more closely, and remove substituted trading baselines.
```

Change:

```text
Live2 now computes prior_up_down_whipsaw_to_impulse_range from the 60x1m setup baseline instead of 24h prior context, computes quote/trade effort per return from the forming setup return, computes taker share/delta and flow_hold from the backtest confirmation segment, and uses the same baseline range percentage denominator as backtest. Live quote/trade setup ratio constants now match current backtest CLI defaults, 5.0/5.0. The old 5s-scaled baseline fallback no longer feeds trading decisions. Any real closed 5s trade bucket can reach the signal engine so cumulative backtest candidates are not skipped by single-bucket actionability thresholds.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py tests\test_anomaly_continuation_lab.py
python -m compileall data\exchanges research_tools cli constants.py main.py
git diff --check
```

Risk:

```text
Medium. This may increase live2_near_misses.csv volume because low-volume real buckets now reach the signal engine for parity. That is intentional diagnostic truth, not a trading loosen. Category acceptance still requires the backtest-like setup quote/trade/retention/verticality/risk/category filters.
```

## 2026-05-21 - P369 applied locally - live2/backtest execution parity

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/entry_guard.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_strategy_backtest.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove remaining live2-overfilters versus the backtest execution model and make selected-signal risk levels use the same stop/TP1 contract.
```

Change:

```text
Live2 selected signals now use backtest-equivalent initial_stop_at_decision=max(box_low - 0.05*box_range, decision EMA20) and TP1=rounded(entry + 0.75*(entry - box_low)). Live2 entry guard defaults now match the backtest executable-entry guard: max signal age 5000ms for the 1m/5s next-candle market model, drift 0.4%, and min RR to signal TP1 0.70. Backtest latency stress defaults now compare 0ms and 5000ms, matching the live freshness window.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
python -m compileall research_tools\anomaly_live2\signal.py research_tools\anomaly_live2\entry_guard.py research_tools\anomaly_live2\config.py research_tools\anomaly_strategy_backtest.py tests\test_live2_market_watch.py
```

Risk:

```text
Medium. This intentionally loosens live entry freshness/RR back to the backtest contract instead of hiding the discrepancy. Drift, TP1-already-touched, positive-risk, actual fill, max-position, and verified-stop guards remain in force. Next live validation should inspect live2_near_misses.csv and entry_guard rejects for stale/drift/RR after restart.
```

## 2026-05-21 - P368 applied locally - remove live1 monolith and setup-level live2 confirmation

Files:

```text
research_tools/anomaly_micro_live.py
research_tools/anomaly_aggtrade_cache.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/top_growth.py
research_tools/anomaly_strategy_backtest.py
cli/commands.py
cli/parser.py
tests/test_live_order_lifecycle.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove obsolete live1 code without losing still-used helper behavior, and close the remaining live2-overfilter where live required the final 5s candle to be green although backtest does not.
```

Change:

```text
Deleted the old live1 monolith and its live1-only lifecycle tests. Moved aggTrade->OHLCV helper functions into research_tools/anomaly_aggtrade_cache.py and updated backtest/cache imports. Removed run-anomaly-live from CLI. Standalone top-growth now uses live2 top-growth plumbing. Live2 no longer rejects solely because the final 5s candle is red; it applies backtest-like setup price_retention, verticality, hold_count and max-risk checks and writes the corresponding live_setup_* near-miss fields.
```

Validation:

```bash
python -m compileall data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py tests\test_anomaly_continuation_lab.py
python main.py run-anomaly-top-growth --help
python main.py --help
```

Risk:

```text
Medium. This intentionally removes the old run-anomaly-live command and its monolithic implementation. Live2 remains the live path. Top-growth visibility output no longer includes live1-specific missed_pump_visibility enrichment; live2 already writes its own top_growth audit and near-miss artifacts.
```

## 2026-05-21 - P367 applied locally - live2/backtest candidate parity and reject diagnostics

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/status_grid.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Stop live2 from applying shared backtest category caps to a stricter single-5s candidate shape, and make rejection artifacts show the exact parity/blocker layer.
```

Change:

```text
Live2 signal features now build a backtest-like forming 1m/5s setup before shared-category evaluation. Category features use cumulative setup quote/trade pace, forming setup range/risk, and prior-whipsaw divided by forming setup range. Near-miss rows include live_setup_* diagnostics, baseline source, and feature-level initial risk. The status grid active metric now renders current active symbols versus unique symbols active since the current session metric start.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
python -m compileall research_tools\anomaly_live2 cli\commands.py cli\parser.py constants.py main.py
python -m compileall data\exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. Trading category timing now matches the current backtest candidate confirmation horizon instead of trying to classify every single 5s actionable bucket. This is a parity repair, not a threshold optimization. If <5s pump-start-to-order is required, the backtest candidate contract must be changed and revalidated too.
```

## 2026-05-21 - P366 applied locally - post-P365 live2 choke audit record

Files:

```text
research/EXPERIMENT_LOG.md
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Record the post-P365 code/run audit without loosening live2 trading filters blindly.
```

Change:

```text
No trading code changed. The audit records that the confirmed live2 choke bugs were already fixed in P365 and that remaining strict gates require a post-P365 near-miss/top-growth validation before any threshold change.
```

Validation:

```bash
Not run; documentation-only audit record.
```

Risk:

```text
None to trading behavior. This commit does not change signal filters, entry guard, runtime gates, exchange execution, fills, stops, artifacts, or operator grid code.
```

## 2026-05-21 - P365 applied locally - live2 signal feature contract parity

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/artifacts.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix live2 feature-scale mismatches that could make all live-priority categories impossible before entry guard.
```

Change:

```text
Live2 now computes baseline_quote_daily_proxy from the 5s baseline quote pace before comparing it with min_baseline_quote_daily_proxy, matching the backtest daily-proxy contract. The signal stop/risk and prior-whipsaw denominator now use a closed-live decision box built from recent 5s candles plus the decision candle, rather than the micro range of the single current 5s candle. Near-miss artifacts include the daily proxy and decision-box fields.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py
python -m compileall data/exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py
```

Risk:

```text
Medium. This changes live signal feature semantics to match the existing category contract. It does not alter category thresholds, entry guard, runtime gates, exchange execution, fills, stops, or position lifecycle. The next live2 run must be audited for selected_count and entry_guard rejects before treating any trade as edge evidence.
```

## 2026-05-21 - P364 applied locally - live2 session-scoped operator grid

Files:

```text
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/session_top.py
research_tools/anomaly_live2/status_grid.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make the live2 operator grid read as a current-session control panel instead of mixing run-lifetime counters with session top movers.
```

Change:

```text
The grid now receives session-scoped decision/execution/runtime counters using the same session metric window as trading percent and session tops. On first live start inside a session, counters start from the run start; on session rollover, the baseline resets. Session top growth keeps four items instead of three. Separator rows render as a full-width underscore line instead of four dot cells.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py
python -m compileall data/exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py
```

Risk:

```text
Low. Operator grid/status presentation only. Trading gates, signal filters, entry guard, exchange execution, fills, stops, and artifact truth are unchanged. Cumulative totals remain in live2_events.csv and diagnostics artifacts.
```

## 2026-05-21 - P363 applied locally - live2 active-symbol grid truth

Files:

```text
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/status_grid.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the operator grid's misleading `Активные 0/0` display. In live2 the ACTIONABLE enum is not retained after a deadline verdict, so counting only state.status=actionable reports zero even while thousands of actionable buckets are processed.
```

Change:

```text
SymbolStateStore now reports actionable_symbol_counts as current symbols with actionable_since_ms inside the radar TTL and total symbols that have ever become actionable in the run. The live2 status payload exposes that object, and the grid renders `Активные current/seen` from it instead of the transient enum count.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py
python -m compileall data/exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py
```

Risk:

```text
Low. Operator/status artifact semantics only. No signal filter, entry guard, runtime gate, exchange order, fill, stop, or position lifecycle behavior changed.
```

## 2026-05-21 - P362 applied locally - live2 near-miss artifacts

Files:

```text
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/runner.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make post-actionable non-selected live2 candidates directly auditable without scraping nested deadline_decision JSON.
```

Change:

```text
Live2 now writes live2_near_misses.csv with one row for every actionable bucket that reached signal evaluation but did not become selected because of signal-contract rejection or missing signal dependency. Rows include latency, return/flow, stage, reject/dependency reasons, prior whipsaw/spike/fade context, OI/mark status, flow-hold fields, and the full event payload JSON for forensic joins.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py
python -m compileall data/exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py
```

Risk:

```text
Low to medium. This is artifact-only and does not change signal thresholds, entry guard, execution, fills, stops, or runtime gates. It adds one bounded artifact-writer queue job per signal-evaluated near miss; artifact writer backpressure remains a hard no-entry gate.
```

## 2026-05-21 - P361 applied locally - live2 four-column operator grid semantics

Files:

```text
research_tools/anomaly_live2/status_grid.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make the live2 terminal grid match the requested four-column operator layout without mixing active entry candidates with universe size.
```

Change:

```text
The grid now renders four-column sections with `◆` titles and non-empty dot separator rows. Sections are Соединение, Задержки, Контекст, Рынок, current session top, and Торговля %. `Рынок/Аномалии` remains total actionable decisions; `Рынок/Активные` is now current actionable symbols / total selected entry signals instead of current actionable / universe size, so `0/578` no longer implies 578 active entry candidates.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2/status_grid.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
```

Risk:

```text
Low. Console UI only. Artifacts and trading logic are unchanged.
```

## 2026-05-21 - P360 applied locally - live2 entry attempt timing artifacts

Files:

```text
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/execution.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make every live2 attempted entry auditable from market bucket close through signal evaluation, entry guard, runtime gate, exchange order, fill, post-position check, stop submit, and stop visibility verification.
```

Change:

```text
`deadline_decision.data_json` now carries `entry_attempt_timing` for actionable decisions. When a selected signal reaches execution, the event also carries top-level execution start/finish/duration plus `execution_timing` from the execution engine. Execution timing includes pre-position fetch, entry order submit/fill, post-position fetch, stop submit, stop verification, and emergency close timing when needed.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. This is artifact instrumentation only. No signal thresholds, entry guards, runtime gates, exchange fills, stop verification, or position lifecycle decisions were loosened.
```

## 2026-05-21 - P359 applied locally - anomaly latency grid matches live2 guard

Files:

```text
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Keep anomaly-lab latency stress testing focused on no extra delay versus a realistic live2 entry budget instead of old broad 7/10/15s stress buckets.
```

Change:

```text
Default latency extra delay is now 2000ms and DEFAULT_LATENCY_GRID_MS is `(0, 2000)`. The 2s value matches the current live2 entry_guard_max_signal_age_ms, so the stress variant represents the maximum signal age live2 is willing to execute rather than a stale-entry scenario that live would reject.
```

Validation:

```bash
python -m compileall research_tools/anomaly_strategy_backtest.py cli/commands.py cli/parser.py
```

Risk:

```text
Low. This changes only anomaly-lab latency stress defaults. Normal runs without `--latency true` remain non-latency-grid runs.
```

## 2026-05-20 - P358 applied locally - live2 top-growth audit off hot path

Files:

```text
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent the new live2 top-growth audit from moving REST latency into the heartbeat/decision loop.
```

Change:

```text
The runner now starts top-growth audit chunks in a daemon background worker and only reads immutable status/completion events from the main loop. If the worker is still running, the heartbeat does not start another chunk. Completion is still written as `live2_top_growth_audit_completed`.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
```

Risk:

```text
Low. Audit-only threading; no signal, entry guard, execution, fill, stop, or position lifecycle changes. The next live run should verify `top_growth_audit.status` progresses without decision-loop overruns.
```

## 2026-05-20 - P357 applied locally - live2 closed-hour top-growth audit

Files:

```text
cli/commands.py
cli/parser.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/top_growth.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make live2 write closed-hour top-growth artifacts inside the same run so missed pumps can be compared against observed live visibility without relying on ticker/session UI snapshots.
```

Change:

```text
Added a bounded incremental top-growth audit for live2. On heartbeat it processes a small number of selected-universe symbols against the previous fully closed 1h exchange candle and writes top_growth_index.csv, top_growth_YYYYMMDD_HH0000_UTC.csv, and top_growth_status_YYYYMMDD_HH0000_UTC.csv under the run directory. Empty top files are still written with headers; status rows retain below_threshold, empty_ohlcv, missing_columns, no_exact_hour_candle, invalid_open_close, and fetch_ohlcv_failed reasons.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
```

Risk:

```text
Low to medium. The audit is bounded to one symbol per heartbeat by default and is not a signal source or entry gate. It still uses REST OHLCV calls, so live artifacts must monitor `top_growth_audit.cycle_seconds` and processing lag; increase symbols_per_cycle only if decision latency stays clean.
```

## 2026-05-20 - P356 applied locally - live2 entry-stream gate and honest backlog/context diagnostics

Files:

```text
cli/commands.py
cli/parser.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/market_data/prior_context.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/status_grid.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Reduce live2 false downtime and missed fresh buckets without hiding data failures. Global ticker/mark flaps should not block every entry when aggTrade is fresh; per-symbol required context must still gate signals.
```

Change:

```text
Market-data readiness now uses an entry-stream gate: selected universe + ready aggTrade shards + live decision watermark. Global ticker/mark coverage remains visible diagnostics, while signal categories use stale-aware per-symbol mark/OI/prior-context checks. The deadline engine processes fresh buckets first and classifies buckets older than decision_backlog_expire_ms as deadline_expired_backlog, separate from near-deadline misses. Prior-context live 5m aggTrade-id gaps are no longer hard rejects by id discontinuity alone; all gaps are tolerated for context roll-forward and above-tolerance gaps are counted as diagnostics.
```

Validation:

```bash
python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
```

Risk:

```text
Medium. The entry gate is less globally conservative, but required per-symbol mark/OI/prior context remains strict and stale-aware. Large aggTrade-id gaps no longer invalidate 5m prior context by themselves; the new counters must be monitored in live2 artifacts to confirm this removes false Ctx stale without masking real WS outages.
```

## 2026-05-20 - P355 applied locally - live2 session trading-allowed percent

Files:

```text
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/status_grid.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Expose the operator-facing share of time when live2 actually allowed new entries, scoped to the current crypto session metric window like session tops, not to the whole process uptime.
```

Change:

```text
Runtime gate accounting now keeps separate session-scoped allowed/blocked seconds that reset on the same metric_start_ms used by live2 session top-growth. The status grid title renders `Торговля N%` from session_seconds, with a runtime-wide fallback only for old artifacts that do not yet carry the new field.
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_live2\runner.py research_tools\anomaly_live2\status_grid.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
```

Risk:

```text
Low. Display/diagnostics only. It does not change market-data gates, signal decisions, execution, fills, stops, or position lifecycle.
```

## 2026-05-20 - P354 applied locally - live2 rolling WS-maintained prior context

Files:

```text
research_tools/anomaly_live2/market_data/candles.py
research_tools/anomaly_live2/market_data/prior_context.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/runner.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Keep live2 24h prior context fresh cheaply without repeatedly repolling a full 24h OHLCV window for every selected symbol. Preserve honest diagnostics and avoid turning WS gaps into hidden ok context.
```

Change:

```text
Live2 now includes a 5m real-trade candle ring. Prior context still bootstraps from REST closed 5m OHLCV at startup, then the runtime prior-context source maintains a rolling 24h buffer by appending closed live WS 5m candles. Runtime full-universe REST polling is disabled for symbols with an ok rolling buffer; REST remains only for bootstrap/repair of missing or non-ok context. Intra-5m aggTrade id gaps are tolerated only when missing ids <= max(5, 10% of observed trades); tolerated and rejected gaps are exposed in prior_context status and symbol-state artifacts. A gap above tolerance marks the symbol `ws_gap_exceeds_tolerance` until repair instead of silently continuing.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. Prior-context freshness becomes dependent on live aggTrade WS 5m closure after startup. This should reduce REST load and Ctx stale, but the next live2 restart must verify that `total_ws_5m_candles_appended` rises, `total_ws_5m_gap_rejected` stays near zero, and prior_context status does not silently hide rejected gaps.
```

## 2026-05-20 - P353 applied locally - live2 prior-context CLI wiring and atomic rewrite artifacts

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/artifacts.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the remaining live2 market-watch instability visible in run 20260520_122522 after P352. The runtime dataclass defaults were widened, but CLI parser/command fallback defaults still passed the old 15m stale / 5m cooldown / 4-symbol cycle values, so normal launched live2 processes could keep producing prior_24h_context_not_ready despite the intended selected-universe poller budget. Also remove in-place rewrite risk for status/diagnostic/symbol-state artifacts so audit files cannot briefly appear as empty/truncated during writer replacement.
```

Change:

```text
CLI defaults and command fallbacks now match AnomalyLive2Config: prior_context_stale_ms=1200000, prior_context_symbol_cooldown_seconds=600, prior_context_max_symbols_per_cycle=10. The prior-context startup event now states the true selected-universe active-priority polling scope instead of the old active/radar-only label. Live2 status, diagnostics summary, and symbol-state full rewrites are written to a flushed/fsynced temp file and atomically replaced. Writer errors are not suppressed; artifact readiness remains the safety gate.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low-to-medium. The change increases default prior-context REST budget for launched live2 to the already intended runtime values, about 60 OHLCV requests/minute at default cycle/cooldown. It does not add fallback data, zero substitution, signal threshold loosening, order changes, or hidden retries. Existing currently-running live2 processes must be restarted to pick up CLI defaults.
```

## 2026-05-20 - P352 applied locally - live2 wall-clock candle close and prior-context freshness

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/market_data/candles.py
research_tools/anomaly_live2/market_data/prior_context.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/state.py
tests/test_live2_market_watch.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix live2 market-watch stability issues visible in run 20260520_112804: actionable 5s real-trade candles could become deadline_missed when no next trade arrived to close the bucket, and prior 24h context could not stay fresh across the selected universe with the old 4-symbol/10s active/radar-only poller. Also prevent signal categories from treating stale OI/prior context as ok.
```

Change:

```text
Live2 now finalizes ended real-trade candles on the decision loop wall clock without synthetic candles, REST backfill, or zero-volume gap fill. Gap diagnostics are preserved when the next real trade arrives after idle buckets. The prior-context poller now refreshes the whole selected universe with active/actionable/in-position symbols prioritized and oldest-first fairness; defaults are 10 symbols per 10s, 600s symbol cooldown, and 20m stale window. Signal features now expose effective stale-aware OI/prior-context statuses plus raw statuses, so stale context becomes data_dependency_not_ready instead of hidden ok.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py
```

Risk:

```text
Medium. Decision timing should improve because buckets close at wall-clock bucket end, but the decision loop now checks due candle closure for selected symbols every cycle. Prior-context REST load increases to about 60 OHLCV requests/minute at default universe size; it is bounded and visible through prior_context status/errors/stale counts.
```

## 2026-05-20 - P351 proposed - event-driven live2 deadline loop and honest aggTrade/OI health

Files:

```text
cli/commands.py
cli/parser.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/market_data/open_interest.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/state.py
```

Intent:

```text
Reduce live2 decision latency at the source and make market-data health more truthful after run 20260520_103312 showed stable WS but frequent deadline misses. The deadline loop now evaluates only symbols dirtied by aggTrade updates, caches the live WS watermark once per cycle, and avoids full per-symbol count aggregation on every 50ms runtime-gate pass. Full diagnostics still write on heartbeat/status. AggTrade diagnostics replace permanent `ok_with_gaps` with `ok_active`, `ok_idle_no_trades`, `gap_missing_expected_bucket`, and `stale`. Runtime OI refresh becomes starvation-free by ordering due symbols by priority and oldest poll timestamp.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the supplied zip, so the project-hygiene command that includes launcher.py cannot be run literally here.
```

Risk:

```text
Medium. The hot path changes from full-universe polling to aggTrade-dirty evaluation. This should reduce latency without hiding missed data, but the next live2 smoke must confirm that selected/actionable buckets are still emitted exactly once and that deadline_missed drops. OI request rate increases to 20 symbols per 5s by default; this is intended to keep the selected universe fresh without hot-path REST fallback and should be watched in OI error counters.
```

Compact active patch log for the anomaly-first source tree. Retired strategy history was removed from active research memory in P129 to avoid stale contracts controlling current work.

| ID | Title | Status | Files | Type | Purpose | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| P349 | Fix live2 aggTrade shard stale_ms wiring | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/market_data/aggtrade_ws.py`, `research/*` | live2-market-data/ws-bugfix | Pass the configured aggTrade stale timeout into every WS shard so receive timeouts and stale watchdogs do not crash with `AttributeError: _Live2AggTradeWsShard object has no attribute stale_ms` during startup. No endpoint, strategy, order, fill, stop, TP, mark, OI, or prior-context logic changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; startup artifact 20260520_090142 confirmed the blocker before fix. |
| P337 | Add live2 24h prior-context poller | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/market_data/prior_context.py`, `research_tools/anomaly_live2/market_data/open_interest.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/signal.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/status_grid.py`, `cli/*`, `research/*` | live2-market-data/prior-context | Add an active/radar-only closed-5m OHLCV prior-context poller for an exact 24h lookback, store prior spike/fast-fade/whipsaw source/status/coverage fields per symbol, expose context counts/diagnostics/grid fields, and let legacy category `*_72h` checks consume explicitly labelled 24h live context. No full-universe hot polling, no zero fallback, no order/fill/stop/TP logic changed. Also fixes the P336 OI status snapshot copy bug. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic 24h prior-context snapshot/poller smoke |
| P336 | Add live2 active-symbol OI poller | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/market_data/open_interest.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/signal.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/status_grid.py`, `cli/*`, `research/*` | live2-market-data/oi-context | Add an active/radar-only 5m open-interest poller for live2, store real 3x5m OI change per symbol, expose OI status counts/diagnostics/grid fields, and let OI-required categories evaluate `min_oi_change_pct_3x5m` from real exchange OI history. No full-universe hot polling, no zero fallback, no prior-24h, order, fill, stop, or TP logic changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic open-interest snapshot/poller smoke |
| P335 | Add live2 markPrice WS context | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/market_data/mark_price_ws.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/signal.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/status_grid.py`, `cli/*`, `research/*` | live2-market-data/mark-context | Add a Binance USD-M all-market `!markPrice@arr@1s` routed market WebSocket for selected live2 symbols, store mark/index/funding context per symbol, require mark stream readiness in market-data coverage, expose mark diagnostics/status counts/grid fields, and let the signal adapter evaluate `mark_close_vs_decision_close_basis` from real mark-price data instead of rejecting all mark-required categories as unavailable. No OI, prior-24h, order, fill, stop, or TP logic changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic mark-price payload smoke |
| P334 | Add live2 planned WS rotation lifecycle | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/market_data/ticker_ws.py`, `research_tools/anomaly_live2/market_data/aggtrade_ws.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/status_grid.py`, `cli/*`, `research/*` | live2-market-data/ws-lifecycle | Rotate live2 Binance market WebSocket connections before the 24h server lifetime, expose connection age/max-age, planned rotation counters, close/exception diagnostics, and avoid exponential backoff after intentional rotations. No strategy, category, order, fill, stop, TP, mark, OI, or prior-context logic changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic short-lifetime rotation smoke |
| P333 | Make live2 market-data events transition-only | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research/*` | live2-audit/diagnostics | Replace noisy `market_data_coverage_update` events with transition-only `market_data_coverage_transition` events whose dedup key excludes changing clean/degraded windows and reconnect counters. Add `live2_diagnostics_summary.json`, artifact event-type counters, market-data/runtime-gate transition counts, runtime-gate allowed/blocked seconds, and compact decision/execution funnels. No market-data ingestion, strategy, category, order, fill, stop, or TP logic changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic artifact-writer diagnostics smoke |
| P332 | Separate live2 startup warmup from live WS decisions | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/deadline.py`, `research_tools/anomaly_live2/market_data/candles.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/status_grid.py`, `research/*` | live2-market-data/audit | Keep startup REST aggTrade warmup as baseline-only data, expose separate startup/live aggTrade counters and candle source labels, and make the deadline engine ignore closed buckets until the first valid live aggTrade payload watermark across all shards. Prevents old warmup candles from producing fake `deadline_missed` decisions. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic warmup/live watermark smoke |
| P331 | Route live2 aggTrade WS via /market and require payload readiness | PROPOSED / UNKNOWN commit | `research_tools/anomaly_live2/market_data/aggtrade_ws.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/status_grid.py`, `research/*` | live2-market-data/ws | Move aggTrade combined streams to the Binance USD-M Futures routed `/market/stream?streams=` endpoint; a shard is ready only after a fresh applied aggTrade row; reconnect backoff resets only after first valid payload; close/exception/pre-first-payload diagnostics become visible in status/grid. No REST fallback, no strategy/order changes. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic shard readiness smoke |
| P324 | Add live2 Telegram operator safety messages | PROPOSED | `research_tools/anomaly_live2/telegram.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/__init__.py`, `cli/commands.py`, `research/*` | live2-operator-safety | Require live2 Telegram env config, add compact operator messages for startup, verified entry, TP1/BE, final close, strict integrity errors, and new-entry gate transitions. Symbol names are clickable Coinglass links and Telegram remains notification-only; artifacts stay source of truth. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic Telegram formatter smoke |
| P320 | Add live2 fast decision loop gates | PROPOSED | `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/artifacts.py`, `research/*` | live2-runtime/latency | Split deadline decisions from heartbeat writes: live2 now runs the DeadlineEngine on a fast 100ms loop while heartbeat/status/symbol-state artifacts stay periodic. Add runtime gates for market-data coverage, decision-latency degradation/recovery, artifact-writer readiness, and explicit no-new-entries reasons. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic fast-loop/degraded-gate smoke |
| P319 | Add live2 bounded async artifact writer | PROPOSED | `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-audit/latency | Move live2 event/status/symbol-state disk writes behind a bounded single-thread artifact writer queue. Hot signal/deadline code no longer performs blocking CSV/JSON writes; writer health/backpressure is surfaced in artifacts and flips the artifact readiness gate off so new entries remain disabled if audit is unsafe. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic bounded-writer smoke |
| P315 | Add live2 deadline verdict engine | PROPOSED | `research_tools/anomaly_live2/deadline.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-deadline/latency | Add a deadline engine that reads already-built in-memory 5s candles, marks diagnostic actionable buckets, and gives every processed actionable bucket an explicit verdict (`rejected_signal_engine_todo`, `data_not_ready`, or `deadline_missed`) without candidate queues, pressure drops, REST fetches, signal strategy, or order placement. Decision counters and latency fields are written into live2 artifacts. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic deadline-cycle smoke |
| P314 | Add live2 ticker-selected startup universe | PROPOSED | `research_tools/anomaly_live2/market_data/universe.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/config.py`, `cli/*`, `research/*` | live2-market-data/universe | Select a startup aggTrade universe from already-received `!ticker@arr` state when `--symbols` is not provided. Starts aggTrade shards for the selected top USDT futures by 24h quote volume/trade count, marks universe rank in symbol-state artifacts, and keeps REST out of the signal hot path. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic universe-selection smoke |
| P313 | Add live2 aggTrade WS candle rings | PROPOSED | `research_tools/anomaly_live2/market_data/*`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-market-data/latency | Start explicit-symbol Binance futures aggTrade combined WS shards, normalize trades, and update per-symbol in-memory 5s/15s/30s/1m candle rings from real trades only. No REST backfill, no synthetic buckets, no candidate queues, no signal decisions, and no order placement. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P312 | Add live2 all-ticker WS state ingestion | PROPOSED | `research_tools/anomaly_live2/market_data/*`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-market-data | Start Binance futures `!ticker@arr` ingestion inside `run-anomaly-live2`; update exactly one mutable `SymbolState` per symbol/market id with last price, 24h quote volume, trade count, source/status/reason, and write ticker status counts into live2 artifacts. No candidate queues, REST ticker fallback, signal decisions, or order placement. Full market-data readiness remains false until aggTrade/candle coverage exists. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P311 | Add live2 v0 runtime skeleton | PROPOSED | `research_tools/anomaly_live2/*`, `cli/parser.py`, `cli/commands.py`, `research/*` | live2-architecture/runtime | Add `run-anomaly-live2` as a separate deadline-driven runtime skeleton with its own artifacts (`live2_events.csv`, `live2_status.json`, `live2_symbol_state.csv`). V0 is a real-live command name, not shadow/dry-run; market-data, signal, and execution are explicit TODO readiness gates and new entries are forbidden until implemented. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P310 | Prior fake-pump scheduler quarantine | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/scheduler-data-quality | Pre-populate and refresh a hot-lane quarantine from symbol-context snapshots for symbols with `prior_fast_fade_count_24h > 2`. The release time is computed from the excess fast-fade timestamps, not a fixed TTL: quarantine lasts until enough events age out of the 24h lookback. Blocks warm/radar promotion before those symbols create queue pressure, while keeping them in ticker/top-growth visibility. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P309 | Strict warm cap and class latency audit | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/scheduler-diagnostics | Reduce warm backlog cap from 12 to 8 and pressure keep floor from 8 to 6; rank warm waiting by immediate-danger then score. Add per-class latency fields for active/immediate_danger/ticker_radar/warm_watch to `symbol_batch_selected` and `live_cycle_summary`, and add scan origin first_seen/promote lag fields to `signal_symbol_scan_summary`. No signal/category/execution guards changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P308 | Hot-waiting priority prefetch | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/data-readiness | After the critical scan/order path, prefetch due subminute aggTrade gap debt for up to 2 top waiting radar/warm symbols when they are immediate-danger flow or score >= 8.0, even if the queue is not idle or latency SLA is breached. Signals/opening positions still block it. Ignore tiny open-tail gaps <=250ms in the prefetch path and emit `aggtrade_rest_gap_prefetch.status=tail_gap_ignored` instead of making a REST call. No signal/category/execution guards changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P307 | Mark live heartbeat quality values | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux | Add inline quality marks to live heartbeat values so connection, pulse, data source, guard, rolling latency, queue, and cycle health are readable without mentally mapping thresholds. Display-only change; scheduler, orders, guards, artifacts, and trading logic are unchanged. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P304 | Add immediate danger-flow precise lane | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/scheduler | Bypass ordinary warm-watch observation/defer/drop policy for extreme real-flow ticker spikes. Immediate danger-flow candidates are promoted to radar on first observation, get up to two reserved precise slots above the normal adaptive cap, are sorted ahead of ordinary radar/warm candidates, and are not pressure-dropped before first precise scan. Signal/category/execution guards are unchanged. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P301 | Make live optional work idle-only | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance/scheduler | Move hot-waiting aggTrade prefetch out of batch selection and run it only after critical scan/open when the loop is idle enough; defer periodic orphan-order reconcile whenever positions/active/radar/warm candidates or latency SLA pressure exist; hard-gate precise cold coverage whenever hot candidates exist; count prior fast-fade/spike timestamps with exact bisect over cached snapshot timestamps. No trading thresholds or order semantics changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; full standard command including `launcher.py` not run because this ZIP has no `launcher.py` |
| P300 | Show potential-anomaly latency in live grid | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux/diagnostics | Add a second Control row to the live heartbeat showing potential-anomaly processing delay from the existing latency SLA samples: `Задержка p95`, `max`, and radar/warm `Очередь`. Also add explicit `potential_anomaly_latency_*` aliases to `live_cycle_summary` so the terminal value is auditable in CSV without changing scheduler, guards, or order logic. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; inline heartbeat smoke |
| P299 | Audit rejected market-entry execution price | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_strategy_backtest.py`, `research/*` | backtest-diagnostics | Preserve rejected delayed market-entry timestamp, price, drift, RR-after-latency, TP1 price, and risk for execution-guard skips so `market_entry_price_drift`, `market_entry_rr_collapsed`, and `tp1_already_reached_before_market_entry` can be audited and drift threshold changes can be compared honestly. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic `_resolve_signal_entry` drift-reject smoke checked rejected price/timestamp/drift fields |
| P298 | Raise executable entry drift guard to 0.4% | PROPOSED | `constants.py`, `cli/*`, `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | live/backtest-execution | Centralize executable entry drift default as `DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT = 0.004` and use it for live `max_entry_price_drift_pct` plus market/latency backtest `max_market_entry_drift_pct`, preserving live/backtest parity. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; parser default smoke for live/lab = 0.004 |
| P297 | Speed up noticed-symbol live decisions | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance/scheduler | Add a fixed hot-lane for noticed radar/warm symbols, shrink score-ranked warm backlog, allow explicit high-score warm promotion under SLA pressure, prefetch bounded hot waiting subminute gap debt, lower decision SLA to 12s, and split prescan stale decisions into `reject_stale_decision_latency`. Drift guard semantics unchanged. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py cli\\commands.py cli\\parser.py main.py`; `.venv\\Scripts\\python.exe -m pytest tests\\test_anomaly_continuation_lab.py -q`; `.venv\\Scripts\\python.exe -m unittest tests.test_live_order_lifecycle -v` |
| P296 | Fix latency validation command in research state | APPLIED locally / UNKNOWN commit | `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md` | docs/research-memory | Align the next validation note with the simplified anomaly-lab CLI: use public `--latency true`, not removed grid flags. | `rg -n "run-latency-grid|latency-grid-ms|latency-ms" research/RESEARCH_STATE.md research/STRATEGY_SPEC.md cli/parser.py main.py` |
| P295 | Reframe active anomaly docs away from legacy strategy wording | APPLIED locally / UNKNOWN commit | `research/*` | docs/research-memory | Remove misleading legacy strategy wording from active anomaly live/backtest project memory. Legacy `strategy/pno` code/tests remain untouched because removing that historical module is a separate high-risk cleanup. | Checked active research notes for stale legacy strategy phrases. |
| P294 | Live cache catch-up and latency backtest grid | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `cli/*`, `research/*` | live-performance/research-execution | Keep mandatory startup context backfill, add bounded catch-up to close accumulated freshness lag, write all live OHLCV flushes as delta parquet, and make `--latency true` run hidden 1s-cache 10s execution delay plus a cheaper 0/7/10/15s latency grid that reuses the primary 10s simulation. Missing 1s latency windows are backfilled from Binance aggTrades during backtest and timing/market summaries are printed at the end. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py cli\\commands.py cli\\parser.py data\\storage\\parquet_storage.py`; `.venv\\Scripts\\python.exe main.py run-anomaly-lab --help`; synthetic latency smoke |
| P289 | Review latest live run and align 24h context labels/parity | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `cli/parser.py`, `tests/*`, `research/*` | diagnostics/parity/live-ux | Record live run 20260517_200159 review; remove hardcoded `72ч` from live context startup/reprepare labels and artifact `history_days`; make backtest legacy `*_72h` category fields use the same 24h effective prior-context window as live while keeping column names compatible; update lifecycle tests for full TP1. | `.venv\\Scripts\\python.exe -m pytest tests\\test_anomaly_continuation_lab.py -q`; `.venv\\Scripts\\python.exe -m unittest tests.test_live_order_lifecycle -v`; `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py research_tools\\anomaly_exit_portfolio_replay.py cli\\parser.py cli\\commands.py constants.py main.py tests\\test_anomaly_continuation_lab.py tests\\test_live_order_lifecycle.py` |
| P290 | Correct live-window backtest interpretation | APPLIED locally / UNKNOWN commit | `research/*` | research-correction | Record targeted anomaly-lab over live run 20260517_200159 showing that offline backtest does find weak discovery trades, but zero live-priority entries. Correct the prior overstatement that backtest would show nothing. | Analysis-only; artifact root `.output\\results\\anomaly_live_window_20260517_200159_targeted` |
| P288 | Full TP1 at 0.75R from pump leg bottom | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_strategy_backtest.py`, `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | exit/live-ready | Make TP1 a full-position exit at `entry + 0.75 * (entry - pump_leg_bottom)` using `decision_box_low` / live segment low as pump leg bottom. Keep SL based on max(structural buffered stop, EMA20). | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_strategy_backtest.py research_tools\\anomaly_micro_live.py cli\\commands.py cli\\parser.py cli constants.py main.py`; defaults smoke prints backtest/live TP1 basis/r/fraction |
| P287 | Add anomaly exit portfolio replay tool | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_exit_portfolio_replay.py`, `research/*` | diagnostics/portfolio-replay | Add a runnable artifact-level no-overlap portfolio replay for saved anomaly_lab trades with exit models `current`, `full_tp1`, and TP1-fraction plus fixed-R remainder variants. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_exit_portfolio_replay.py`; `.venv\\Scripts\\python.exe research_tools\\anomaly_exit_portfolio_replay.py --lab-dir .output\\results\\anomaly_lab --families live_priority --context-parity ok --max-positions 1 --same-symbol-overlap reject --models current,tp1_25_rest_1p5r,tp1_50_rest_1p5r,full_tp1 --output-dir .output\\results\\anomaly_lab\\portfolio_exit_replay` |
| P286 | Explain missed pumps and category parity | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | diagnostics/parity | Replace generic top-growth missed-pump reasons with concrete precise-scan reject events/reasons and add context parity columns for `discovery_only`, `live_priority_pass`, `live_priority_reject_reason`, `strict_live_replay_enter`. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py cli constants.py main.py` |
| P285 | Active tail-stale context suffix refresh and 24h fake-pump window | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_category_contract.py`, `research/*` | live-data-quality/category | For active/retryable symbols whose prior fake-pump context is stale, fetch only the missing levels-TF OHLCV suffix, update cache/snapshot, and re-check the category. Reduce live prior fake-pump lookback from 72h to 24h and allow up to 2 prior fast-fade events. | `.venv\\Scripts\\python.exe -m compileall -q research_tools/anomaly_micro_live.py research_tools/anomaly_category_contract.py cli constants.py main.py` |
| P284 | Refresh priority context under latency pressure | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-data-quality | Let open/active/retryable-dependency symbols run a tiny cache-only symbol-context snapshot refresh even when optional context snapshots are gated by latency SLA, so candidates do not expire only because prior-fast-fade context stayed stale. | `python -m compileall -q research_tools/anomaly_micro_live.py cli constants.py main.py` |
| P240 | Treat unavailable category context as retryable dependency | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-diagnostics | Stop emitting `category_rejected` / delayed replay final-reject cases when prior-fast-fade context is unavailable; keep the decision unconsumed and record `signal_scan_retryable_dependency_blocked` with blocked categories/details. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P239 | Startup context backfill and gap tolerance | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-data-quality | Backfill and compute 72h+baseline prior-fast-fade context on live startup by default using levels timeframes only, and accept only tiny internal OHLCV gaps under explicit coverage limits. | `python -m compileall data/exchanges research_tools cli constants.py main.py` |
| P123 | Decouple anomaly runtime helpers | PROPOSED | `research_tools/anomaly_config.py`, `research_tools/charting.py`, anomaly live/backtest modules | cleanup | Move timeframe validation and chart helpers into anomaly-owned/common modules. | `python -m compileall research_tools/anomaly_config.py research_tools/charting.py research_tools/anomaly_micro_live.py research_tools/anomaly_strategy_backtest.py` |
| P124 | Isolate retired strategy CLI imports | PROPOSED | `cli/commands.py`, `cli/parser.py`, `config/*`, `strategy/factory.py` | cleanup | Stop startup/config/parser paths from importing retired strategy modules. | `python -m compileall cli/commands.py cli/parser.py config strategy main.py` |
| P125 | Prune unused launcher and dead constants | PROPOSED | `launcher.py`, `constants.py`, docs | cleanup | Remove unused wrapper and dead sizing constants. | `python -m compileall constants.py cli config research_tools main.py` |
| P126 | Remove stale historical pytest marker | PROPOSED | `pyproject.toml`, `research/*` | cleanup | Remove a test marker for a test suite absent from the ZIP source. | `git grep historical_marker_name` equivalent returns empty. |
| P127 | Neutralize old sizing names | PROPOSED | `constants.py`, `config/*`, runtime modules | cleanup | Rename old sizing defaults to neutral position defaults. | `git grep old_sizing_prefix` equivalent returns empty. |
| P128 | Remove retired strategy active path | PROPOSED | `strategy/`, `cli/`, `config/`, `README.md`, `research/*` | deletion | Remove retired strategy source, diagnostics, CLI, config and factory path. | `python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py` |
| P129 | Purge retired strategy history from active memory | PROPOSED | `README.md`, `research/*.md` | cleanup | Remove stale historical strategy references from current project docs/logs. | `git grep -i retired_strategy_token -- .` returns empty for tracked files. |
| P209 | DANGER adaptive cold coverage controller | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance | Scale default/explicit precise cold coverage by WS health, scheduler heartbeat EWMA, active-waiting load and REST/cache pressure; hard-off for positions and active-due symbols. | `python -m compileall data/exchanges research_tools constants.py main.py` |
| P213 | Warm-watch scheduler gate | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-scheduler | Insert warm-watch between cheap ticker/flow radar and precise scan; subscribe warm symbols to micro-cache but promote to precise only after continuing flow and non-chase/non-fade checks. | `python -m compileall data/exchanges research_tools cli constants.py main.py` |
| P218 | Budget optional context and warm micro-cache | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance | Move symbol context snapshot maintenance after the critical scan path, SLA/budget it, use effective freshness based on universe throughput, and cap warm-watch aggTrade targets without capping active/radar. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help | grep -E "warm-watch-aggtrade|symbol-context-snapshot-max"` |
| P219 | Fix live order-id NameError and fatal Telegram alert | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-reliability | Import `re` for deterministic live client order ids and send fatal live/internal-integrity errors synchronously to Telegram, recording `telegram_sync_send_failed` if delivery itself fails. | `python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py`; live smoke with one cycle or synthetic `_live_client_order_id` call. |
| P220 | Protect unresolved entry-order failures | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-safety | Track the symbol before market entry submission and close any actual unprotected exchange exposure if entry order/fill resolution raises before a `LivePosition` and stop are created. | `python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py`; synthetic fill-resolution failure should emit `unprotected_entry_reduce_only_exit_filled`. |
| P221 | Fix live ledger scan-mode fields | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-reliability | Store `source_scan_mode`, cold-coverage marker and entry-position guard source on `LivePosition` and include them in `live_positions.csv` schema instead of referencing undefined writer locals after an entry opens. | `python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py`; synthetic `LiveArtifactWriter.append_position()` smoke. |
| P130 | Strict live fills and executable market entry | PROPOSED | `data/exchanges/*`, `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `cli/*`, `research/*` | bugfix | Stop executing stale live signals; require exchange fill fields; separate signal price from actual fill; compute live PnL/BE/TP from actual fill; make market backtest enter on execution candle. | `python -m compileall data/exchanges research_tools cli constants.py main.py` |
| P131 | Live blocked-order Telegram alerts | PROPOSED | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | bugfix | Send Telegram event alerts when a selected live signal is blocked as stale/non-executable; make entry-price drift guard absolute in live and market backtest. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic stale/drift smoke. |
| P137 | Repair 1h overhead level scanner wiring | PROPOSED | `research_tools/hourly_levels.py`, `cli/*`, `research/*` | diagnostics | Restore the non-empty scanner module and register `run-hourly-levels`; detect bounce-validated 1h overhead levels and export metrics/charts. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-hourly-levels --source-timeframe 5m --days 45`. |
| P138 | Make hourly-level chart text ASCII-safe | PROPOSED | `research_tools/hourly_levels.py`, `research/*` | diagnostics | Avoid matplotlib missing-glyph warning spam by rendering non-ASCII symbols as escaped ASCII in chart titles/filenames. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; run `run-hourly-levels` on symbols with non-ASCII names. |
| P139 | Add hourly-level scan progress and ETA | PROPOSED | `research_tools/hourly_levels.py`, `cli/*`, `research/*` | diagnostics | Emit start/progress lines with processed count, elapsed time, ETA, levels, chart count and last symbol status during long all-cache hourly-level scans. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; run `run-hourly-levels` and verify progress appears before completion. |
| P140 | Humanize and de-spike hourly levels | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Draw dark bot-style charts, count retouches only after a meaningful reset away from the level, cap nearby levels per symbol, draw levels from first valid touch, and reject pierced/spiked-through resistance levels by default. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` with pierced-level guard enabled. |
| P141 | Speed up hourly-level scan and chart run | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Read only scanner-needed parquet columns, trim stale source candles before 1h aggregation, and replace expensive pandas per-row/copy checks in touch/break/pierce logic with numpy scans. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` and compare elapsed_seconds. |
| P142 | Remove hourly chart tight-layout pass | PROPOSED | `research_tools/hourly_levels.py`, `research/*` | diagnostics | Replace matplotlib `tight_layout()` with fixed subplot margins to avoid warning spam and avoid an unnecessary per-chart layout solver pass. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; run chart export and confirm no tight_layout warning. |
| P143 | Speed up live scan without hiding stale rejects | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance | Fetch each levels timeframe once per symbol per cycle, reject stale decisions before heavy signal build while preserving `reject_stale_signal` artifacts, and move top-growth export to standalone CLI. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; live smoke: `reject_stale_signal` remains visible with `stage=prescan`; `run-anomaly-live` no longer starts top-growth. |
| P144 | Skip unchanged live signal rescans | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance | Do not spend OHLCV/API budget rescanning a symbol/timeframe until a new closed levels candle exists; active symbols are checked every cycle but only due ones consume scan slots, while waiting active symbols remain visible in `symbol_batch_selected`. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic scheduler smoke: active waiting symbol is not scanned again on unchanged closed candle and frees a slot for inactive scan. |
| P148 | Inline live heartbeat status | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux | Keep routine live heartbeat status on one mutable console line while preserving real events/errors/position logs as normal sequential lines; clarify that the number is cycle duration. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; console smoke: consecutive heartbeat lines overwrite in-place, any event log first terminates the heartbeat line. |
| P149 | Strict high-based hourly levels | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Count only high-based 1h touches, enforce 6h spacing between counted touches, and reject pierced levels strictly by default. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` with `--min-touch-spacing-hours 6 --max-level-pierce-pct 0.0`. |
| P150 | Reject stale source candles for 1h levels | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Reject a pivot/high level source when any close in the previous 12h is above that candidate high. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` with `--level-source-close-lookback-hours 12`. |
| P151 | Rework trade chart context and flow panels | PROPOSED | `research_tools/anomaly_strategy_backtest.py`, `research/*` | diagnostics | Merge quote-volume and relative trade-count into one bottom line panel, and replace the middle context with independent 1h/7d candles plus strict hourly levels. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic `render_anomaly_trade_chart` smoke. |
| P152 | Attach open-position trade chart | PROPOSED | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | live-operator-ux | Send the canonical three-panel trade chart with live open-position Telegram messages; open charts use TP1/SL lines instead of risk/reward rectangles. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic open `render_anomaly_trade_chart` smoke. |
| P153 | Limit trade-chart level discovery to 7d context | PROPOSED | `research_tools/anomaly_strategy_backtest.py`, `research/*` | diagnostics | Ensure levels drawn on trade/open charts are discovered only from the same 1h/7d context window shown in the middle panel, even if a wider context frame is supplied. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic wider-context level smoke. |
| P167 | Symbol-first live multi-TF scan | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance | For selected live symbols, scan all due TF sets before moving to the next symbol, cache setup/entry fetches within the batch, and add an explicit cold round-robin slot cap so fast live can prioritize active/radar symbols without hiding skipped work. | `python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py`; `python main.py run-anomaly-live --help`. |
| P168 | Cache-backed live OHLCV fetch | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance/data-quality | Live reads local parquet first, fetches only missing OHLCV/aggTrade-derived ranges, writes fetched rows with provenance, keeps a process-memory frame cache, and emits explicit cache read/gap artifacts. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help`. |
| P169 | Fix live cache candle boundary reads | PROPOSED | `data/storage/parquet_storage.py`, `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-performance | Read only requested parquet windows, fetch subminute missing ranges through the full final candle, pass aggTrades endTime on paged requests, and exclude non-closed cached candles from live decision frames. | `python -m compileall data/storage/parquet_storage.py research_tools/anomaly_micro_live.py`; `python main.py run-anomaly-live --help`. |
| P170 | Buffer live OHLCV cache writes | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance/data-quality | Buffer live-fetched OHLCV rows in memory, flush parquet writes by interval/row cap or on shutdown/error, and emit buffered/flushed/failed artifacts instead of rewriting parquet during every fetch. | `python -m compileall data/exchanges data/storage research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help`. |
| P181 | Number live batches within full symbol cycles | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux | Show operator status as `cycle batch/full-cycle` so batch ticks are not confused with full universe passes. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help`. |

## P219 — Fix live order-id NameError and fatal Telegram alert

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-15
Commit: UNKNOWN

### Reason

The real `run-anomaly-live --confirm-real-orders` run `20260514_201044` selected GWEI as `runner_reclaim` and reached the live entry path, then crashed before order submission with `NameError: name 're' is not defined` in `_live_client_order_id()`. The fatal error Telegram notification was queued asynchronously and the process shut down immediately, so no Telegram failure/success artifact appeared and the operator did not receive an alert.

### Change

- Add the missing `re` import used by deterministic live client order id generation.
- Add `TelegramDispatcher.send_critical_sync()` for fatal live/data-integrity errors.
- Record `telegram_sync_send_failed` if the synchronous critical Telegram send fails.
- Use the synchronous path for `live_internal_error` and `live_data_integrity_error`.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Low. This is an import/runtime reliability fix and a fatal-error notification path. Signal filters, category selection, order sizing, stop/TP math, and position monitoring are unchanged.

## P220 — Protect unresolved entry-order failures

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-15
Commit: UNKNOWN

### Reason

Reviewing the last 15 live commits found a safety gap in the real entry path. If Binance accepted a market entry but `create_market_order_with_fill()` raised while resolving the fill, live had not yet created `LivePosition`, had not placed the initial stop, and had not tracked the symbol for forced reconcile. A generic internal-error exit could therefore leave real exposure outside the local position ledger.

### Change

- Track the symbol for order reconciliation before submitting the market entry.
- Wrap entry order/fill resolution.
- On any exception, call `_close_unprotected_entry_exposure()` with the requested entry amount and a deterministic unresolved entry id before re-raising.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py
# synthetic: create_market_order_with_fill raises after exchange position appears -> reduce-only close is attempted
```

### Risk

Low-to-medium. This only changes failure handling after an entry order/fill exception. In the normal filled path, sizing, signal selection, stop/TP math, and position monitoring are unchanged.

## P221 — Fix live ledger scan-mode fields

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-15
Commit: UNKNOWN

### Reason

The live health review found a second real entry-path crash after P219. `LiveArtifactWriter.append_position()` attempted to write `source_scan_mode`, `danger_cold_coverage_source`, and `entry_position_guard_source`, but those values were neither in `LIVE_LEDGER_COLUMNS` nor available as locals inside the writer. A successful entry that got past order id creation, fill validation, and stop creation could therefore fail while appending `live_positions.csv`.

### Change

- Add the three scan/guard diagnostic fields to the live ledger schema.
- Persist those fields on `LivePosition` when the entry is opened.
- Make `append_position()` read the diagnostics from `position` instead of undefined writer locals.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py
```

### Risk

Low. This changes only live ledger diagnostics and prevents a post-entry writer crash. Order selection, sizing, fill validation, stop creation, TP and monitor logic are unchanged.

## P129 — Purge retired strategy history from active memory

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

After removing the retired source path, the active research memory still contained extensive historical entries for the previous strategy. Those entries were no longer executable and could keep steering new work toward stale assumptions.

### Change

- Rewrite `README.md` as anomaly-only project documentation.
- Rewrite `research/RESEARCH_STATE.md` around current anomaly runtime paths and risks.
- Rewrite `research/STRATEGY_SPEC.md` as the current anomaly strategy contract.
- Reset `research/PATCH_LOG.md` to a compact active patch log for the current source tree.
- Reset `research/EXPERIMENT_LOG.md` to anomaly-only experiment tracking.

### Validation

```bash
git grep -n -i -E 'retired_strategy_token|old_strategy_token|old_sizing_token' -- .
python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py
```

### Risk

Medium for project memory: detailed historical notes are intentionally removed from the active repo. Source behavior is unchanged.

## P130 — Strict live fills and executable market entry

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

NVDA live audit showed a signal price/time and execution price/time mismatch: a stale backfilled signal could be selected several minutes after its decision candle, while ledger, PnL and chart still used the signal close as entry. That makes live PnL and edge evidence invalid.

### Change

- Add typed exchange fill boundary: market entry must resolve order id, timestamp, average price and filled amount through exchange order/trade payloads.
- Reject live entries when signal is stale, live price drift is too high, TP1 is already reached, RR to signal TP1 collapsed, or exchange already has a position for the symbol.
- Ledger and chart now separate `signal_entry_price` from actual `entry_price`; TP1/BE/PnL are based on actual fill.
- Stop replacement verifies the new stop is open and does not silently ignore old-stop cancellation state.
- Market backtest no longer enters at decision close; it enters on the configured execution candle and rejects drift/RR-collapsed cases.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Medium: live will reject more signals and backtest results for `entry_method=market` will change. This is intended; old behavior was optimistic and non-executable.

## P131 — Live blocked-order Telegram alerts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

P130 blocks stale and non-executable selected live signals, but the operator would only see those decisions in `live_events.csv` unless watching artifacts. Second review also found the drift guard was one-sided: it rejected only positive drift, while a selected signal could still be bought after price moved too far below the signal entry.

### Change

- Send Telegram event-channel alerts for `reject_stale_signal` and live executability rejects: invalid live price, TP1 already reached, invalid/wide live risk, entry price drift, and RR collapsed.
- Keep artifact events as source of truth; Telegram is only an operator notification queued through the existing dispatcher.
- Change live and market-backtest entry-price drift from one-sided positive drift to absolute drift around the signal entry.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: stale signal and absolute drift reject enqueue Telegram event and create no market order
```

### Risk

Low/medium: fewer fresh-but-dislocated live entries pass, and Telegram event channel can receive more messages. Existing Telegram cooldown key is per symbol/event.

## P132 — Live position safety audit artifacts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

P130/P131 fixed stale/non-executable entry selection, but live position management could still hide operational problems: monitor integrity errors were handled like temporary network failures, TP1 partial exits used unverified fills, initial stops were accepted by id only, repeated empty monitor OHLCV had no hard failure, and market-backtest skip reasons were not exported as first-class artifacts.

### Change

- Verify initial and replacement stop orders through open-order state: id, side, stop type, reduce-only flag, amount and stop price.
- If initial stop verification fails after entry fill, attempt a verified reduce-only emergency exit and write the exact outcome to `live_events.csv`.
- Execute TP1 partial close through verified fill resolution and record actual TP1 fill price/amount/PnL in `live_events.csv`.
- Treat `LiveDataIntegrityError` in position monitor as `position_integrity_error` with Telegram alert and no generic retry masking.
- Record repeated empty monitor OHLCV as `position_monitor_empty_ohlcv`; after `max_monitor_empty_ohlcv_cycles`, escalate to integrity error.
- Export `anomaly_skip_reasons.csv`, include skip reasons in profitability summary, expose execution-guard skips in entry-grid summary, and mark market backtest execution as `next_bar_open_proxy_latency_N`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Medium: live may halt position monitoring when exchange stop/order state cannot be verified. This is intentional; unknown protection state must be visible and handled manually instead of being treated as a transient loop error.

## P133 — Human Telegram live messages

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

Live Telegram messages were technically correct but too verbose and used raw Coinglass URLs. Operator messages should be short, human-readable and use clickable symbols while `live_events.csv` remains the full audit source.

### Change

- Replace startup/error/network blocked-entry/integrity/open/close/stop Telegram text with compact human-style templates.
- Render symbols as Telegram HTML links to Coinglass instead of printing a separate raw URL.
- Keep detailed blocked-entry and integrity metadata in artifacts; Telegram only shows the concise reason and context.
- Display close location as `SL`, `BE`, `TP-` or `TP+`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low: Telegram wording changes only. Audit detail is intentionally preserved in `live_events.csv`, not repeated in every operator message.

## P134 — Active-symbol live scheduler

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The live loop treated only open positions as active. When at least one position was open it used a separate hard-coded inactive batch size, so a symbol that had just passed an important pump/signal stage could wait behind the normal round-robin instead of being rescanned on the next few cycles.

### Change

- Replace the old `inactive_batch_with_active = 7` path with one scheduler rule: `active_symbols + (symbol_batch_size - active_count)` inactive symbols.
- Add an explicit active-symbol watchlist for recent pump-flow candidates, selected entry signals, symbols waiting for a free position slot, and currently opening symbols.
- Keep open positions always active; if active symbols exceed the configured batch size, all active symbols are scanned and inactive slots become zero.
- Export scheduler state into `live_events.csv`: `active_symbol_marked`, `active_symbol_cleared`, `active_symbol_expired`, `symbol_batch_selected`, and `signal_decision_consumed`.
- Do not consume a selected signal when it is blocked only by `max_open_positions`; it remains active until the signal expires or a free slot appears.
- Add CLI knobs `--symbol-batch-size` and `--active-symbol-ttl-ms`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic scheduler smoke: two active symbols with symbol_batch_size=5 => two active + three inactive
```

### Risk

Low/medium: a burst of many active symbols can make a cycle longer because active symbols are not dropped to preserve a fixed batch cap. This is intentional; symbols with near-term action priority must not be starved by inactive round-robin scanning.


## P135 — Hourly live top-growth artifacts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

Manual review and later backtests need a reproducible record of which symbols made large hourly moves during live sessions. Keeping this only in terminal/TG is not enough, and using partial candles would make later replay ambiguous.

### Change

- Add hourly closed-1h top-growth snapshots under each live run root: `top_growth/`.
- Write one compact `top_growth_YYYYMMDD_HH0000_UTC.csv` per completed hour, capped at `top_growth_limit` rows and filtered by `top_growth_min_return_pct` (default 10%).
- Write matching `top_growth_status_YYYYMMDD_HH0000_UTC.csv` with per-symbol status/reason so missing candles/API failures are visible instead of silently reducing the universe.
- Maintain `top_growth_index.csv` for quick navigation across hourly snapshots.
- Run collection in a non-overlapping background worker so live scanning is not blocked by the hourly universe pass.
- Add CLI knobs: `--top-growth-enabled`, `--top-growth-min-return-pct`, `--top-growth-limit`, `--top-growth-fetch-spacing-seconds`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic top-growth smoke: two >=10% symbols exported, one below threshold excluded, one fetch failure visible in status CSV
```

### Risk

Low/medium: one hourly universe pass adds exchange requests. It is throttled and non-overlapping, but on a very large universe it can still add API load. If this interferes with live scans, disable it with `--top-growth-enabled false` or increase fetch spacing.

## P136 — Fix live active-symbol runtime error and stop masking internal bugs as network pauses

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The first live run after P135 failed immediately with `name 'levels_timeframe_ms' is not defined`. The error came from the active-symbol pump candidate path added in P134: `_build_signal_at_start()` used `levels_timeframe_ms` without defining it locally. The live loop then incorrectly reported the programming error as a network/API pause.

### Change

- Define `levels_timeframe_ms` inside `_build_signal_at_start()` before using it to compute signal availability.
- Import and catch `ExchangeConnectivityError` explicitly for network/API pauses.
- Stop treating arbitrary `Exception` as network/API degradation in the main live loop.
- Write unexpected internal failures as `live_internal_error` in `live_events.csv`, send a TG error, and exit with code 4.
- Make `CcxtFuturesClient._retry_exchange_call()` raise `ExchangeConnectivityError` on retry exhaustion so real exchange connectivity failures still use the pause/retry path.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low/medium: real exchange retry exhaustion still pauses. Internal code errors no longer keep the bot alive pretending the API is down; live stops instead, which is the intended safe behavior.

## P137 — Repair 1h overhead level scanner wiring

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The local tree has `research_tools/hourly_levels.py` as an empty file and the CLI registration is missing from `cli/parser.py` and `cli/commands.py`. As a result, `main.py -h` cannot show `run-hourly-levels` even though the scanner patch was expected to be present.

### Change

- Restore the full 1h overhead-level scanner implementation.
- Register `run-hourly-levels` in the parser and command handler map.
- Scan cached symbols from `5m` by default, aggregate to `1h`, and write `hourly_levels_summary.csv`, `hourly_levels_status.csv`, and review charts.
- Count a level touch only when a meaningful bounce follows; wick-only marks without reaction do not qualify.
- Reject clear downtrend symbols and downtrend pseudo-levels by default.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```

### Risk

Low: diagnostics only. It does not change live signal selection, order execution, stops, exits, or backtest trading rules.


## P138 — Make hourly-level chart text ASCII-safe

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

Some cached futures symbols contain non-ASCII characters. Matplotlib's default DejaVu Sans font cannot render those glyphs, so every saved chart can emit repeated `Glyph ... missing from font(s) DejaVu Sans` warnings during `tight_layout()` and `savefig()`.

### Change

- Render chart titles with an ASCII-safe symbol representation using unicode escapes for non-ASCII characters.
- Use the same ASCII-safe representation for chart file stems.
- Do not suppress warnings globally; avoid creating unsupported glyphs in the chart text.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```

### Risk

Low: diagnostics/chart output only. CSV keeps the original symbol value; only chart title/file stem becomes ASCII-safe when needed.

## P139 — Add hourly-level scan progress and ETA

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

`run-hourly-levels` can spend a long time scanning the full cache and saving charts, but after the initial command-start log it emits nothing until completion. That makes the operator watch an apparently idle process with no progress, ETA or current-stage feedback.

### Change

- Add visible progress logging to the hourly-level scanner: processed symbols, percent, elapsed time, ETA, levels found, symbols with levels, charts saved, last symbol and last status/reason.
- Emit a start line immediately after symbol discovery.
- Add CLI knobs `--progress-every-symbols` and `--progress-min-seconds`.
- Pass the command logger into the scanner as an explicit progress callback; no global warning/log suppression is used.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --progress-every-symbols 5 --progress-min-seconds 5
```

### Risk

Low: diagnostics only. More console/log lines during long scanner runs; level detection, live execution and backtest behavior are unchanged.

## P140 — Humanize and de-spike hourly levels

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The first hourly-level charts were visually and logically noisy: one local grind near resistance could be counted as dozens of touches, levels were drawn as infinite full-chart horizontals, nearby duplicate levels crowded the chart, and wick spikes through resistance could still leave a level marked as valid.

### Change

- Reuse the shared dark chart style from `research_tools/charting.py` and add a volume panel.
- Count a new touch only after price has moved materially away from the previous touch and then returned to the level.
- Draw each level segment from its first valid touch instead of as a full-width horizontal line.
- Cap major levels per symbol with `--max-levels-per-symbol` and de-duplicate close levels.
- Reject pierced levels by default with `--reject-pierced-levels true`: if price spikes materially above the level and closes back below the accepted-break zone, the level is treated as damaged rather than clean resistance.
- Export `pierce_count` and `max_pierce_pct` in the summary for audit when the guard is disabled.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --reject-pierced-levels true --max-level-pierce-pct 0.015 --max-levels-per-symbol 4 --progress-every-symbols 5 --progress-min-seconds 5
```

### Risk

Low/medium for diagnostics: the scanner will output fewer levels, and some previously visible wick-spiked levels will disappear. Trading/live/backtest execution logic is unchanged.

## P141 — Speed up hourly-level scan and chart run

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

`run-hourly-levels` was doing avoidable work: loading every parquet column, resampling the full cached history even when only the recent `days/lookback_bars` window is needed, and using pandas row/copy loops inside per-level touch, break and pierce checks. Chart runs then paid that cost for every scanned symbol before saving review images.

### Change

- Read only OHLCV columns used by the hourly-level scanner instead of full parquet payloads.
- Add `--fast-source-trim true` default: trim raw cached candles to the needed recent 1h review window plus buffer before aggregation.
- Replace per-row `iterrows()` touch detection with numpy candidate-index scanning while preserving the retouch re-arm rule.
- Replace break/pierce dataframe copies with numpy slices.
- Progress start line now reports whether fast source trimming is enabled.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --fast-source-trim true --progress-every-symbols 5 --progress-min-seconds 5
```

### Risk

Low/medium for diagnostics. Source pre-trim intentionally keeps a buffer before the final review window; disabling `--fast-source-trim false` restores the slower full-source aggregation path for exact forensic comparison. Trading/live/backtest execution is unchanged.


## P142 — Remove hourly chart tight-layout pass

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

After the humanized chart layout, `matplotlib` warns that the figure contains axes not compatible with `tight_layout()`. The warning is caused by right-side price tags drawn outside the axes; `tight_layout()` is also unnecessary work on every saved chart.

### Change

- Replace `fig.tight_layout()` in hourly-level chart export with explicit `fig.subplots_adjust(...)` margins.
- Reserve fixed right margin for price tags instead of asking matplotlib's layout solver to infer it.
- Do not suppress warnings globally; remove the warning source.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --fast-source-trim true
```

### Risk

Low: chart layout only. Level detection, CSV metrics, live trading and backtest behavior are unchanged.


## P143 — Speed up live scan without hiding stale rejects

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The 2026-05-11/12 micro-live run found selected signals only after they were already stale. Safety worked, but discovery was too slow: the live loop scanned the same `5m` levels timeframe separately for `5m/30s` and `5m/15s`, and top-growth snapshots competed with live REST/API budget. Stale decisions must still remain visible in artifacts.

### Change

- Group live scan work by `levels_timeframe`, so one `5m` OHLCV fetch per symbol serves both `5m/30s` and `5m/15s` entry metadata.
- Add a prescan freshness guard before `_build_signal_at_start()`: stale decisions are marked consumed and written as `reject_stale_signal` with `stage=prescan`, but the expensive signal/category build is skipped.
- Keep the execution-time freshness guard for selected signals and mark those rows with `stage=execution_guard`.
- Remove top-growth from the live loop and add standalone `run-anomaly-top-growth` CLI for closed-hour top-growth artifacts.
- Do not add dynamic universe pruning and do not relax `max_signal_age_ms`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-top-growth --top-growth-min-return-pct 0.10 --top-growth-limit 5
# live smoke: run-anomaly-live no longer emits top_growth_snapshot_started during trading loop
# artifact smoke: stale backfilled decisions still emit reject_stale_signal with stage=prescan
```

### Risk

Low/medium. Signal rules and order execution are unchanged, but live scan ordering and artifact staging change. Top-growth is now an explicit operator command rather than an automatic live side task.

---

## 17. Current audit note — P144

P144 is live-performance only. It adds a per-symbol/per-levels-timeframe closed-candle scan ledger so the runner does not re-fetch OHLCV for the same already-processed closed candle. Active symbols are still evaluated every cycle for scheduling, but if no new closed candle exists they are reported as `active_waiting_*` in `symbol_batch_selected` and their slot is released to inactive symbols. Empty OHLCV results are visible as `signal_scan_empty_ohlcv`; exchange/network exceptions are not marked as scanned and still bubble through the existing retry path.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: repeated _scan_batch on unchanged now_ms fetches each symbol/timeframe once, then fetches again after the next closed candle.
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20
```


## P145 — Ticker-radar live watch promotion

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

P143/P144 reduce redundant live work, but a full cold-universe round-robin can still discover fast wake-ups too late. The next acceleration must not prune the universe, must not trade from ticker data, and must not hide normal round-robin candidates.

### Change

- Add a typed `fetch_ticker_snapshots(...)` exchange boundary returning explicit source/status fields for ticker `last`, real 24h `quoteVolume`, and optional trade count.
- Add live ticker-radar state that compares current ticker snapshots to the previous snapshot and promotes only symbols with positive price delta plus real quote-volume delta.
- Add a separate bounded ticker-radar watch layer in scheduling. Radar watch symbols are extra scans; they do not consume normal inactive round-robin slots.
- Add artifacts: `ticker_radar_snapshot`, `ticker_radar_promoted`, `ticker_radar_missing_fields`, `ticker_radar_failed`, `ticker_radar_watch_expired`, and ticker-radar fields in `symbol_batch_selected`.
- Preserve strategy logic: radar data cannot open a position and cannot replace kline-based quote-volume/trade-count evidence.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: first snapshot does not promote; second snapshot with price+quote delta promotes one symbol; batch keeps normal inactive slots and adds the radar symbol as an extra scan.
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20 --ticker-radar-watch-batch-size 5
```

### Risk

Low/medium. It cannot remove cold symbols or create trades, but each promoted radar symbol adds extra OHLCV work. If cycle time worsens or rate-limit pressure appears, reduce `--ticker-radar-watch-batch-size` or disable with `--ticker-radar-enabled false`.


---

## P146 — Ticker-radar safety cleanup

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Post-P145 audit found three low/medium-risk scheduler issues: passing a large symbol list into `fetch_tickers` can create exchange-specific oversized requests; radar-waiting symbols could be selected again by inactive rotation and waste a cold slot; and symbols promoted to real active state kept stale radar-watch entries until TTL expiry.

### Change

- Fetch ticker snapshots through the typed boundary with one all-tickers request and local filtering, avoiding giant per-symbol ticker query parameters.
- Exclude `ticker_radar_waiting_symbols` from inactive selection so unchanged radar watches do not consume cold slots.
- Clear a symbol from ticker-radar watch when it becomes a real active symbol and emit `ticker_radar_watch_cleared`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: radar waiting symbols are not reselected as inactive; active promotion clears radar watch.
```

### Risk

Low. Trading logic is unchanged. The patch only reduces scheduler waste and makes ticker fetch safer for large universes.

---

## P147 — Add operator command runbook and 30d defaults

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Operator commands had drifted into chat history and long CLI lines. The base research/cache window also differed by command, which made copy-paste runs error-prone.

### Change

- Add root `COMMANDS.md` with short copy-paste commands for cache, quality, lab, live smoke/full live, top-growth and hourly-level diagnostics.
- Add `DEFAULT_COMMANDS_BASE_DAYS = 30` in `constants.py`.
- Use that base for `fetch-data`, `update-cache`, `run-anomaly-lab`, and `run-hourly-levels`.
- Keep strategy/live/radar thresholds unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low. This is operator UX/default wiring only. It changes default day windows to 30 days and does not change strategy thresholds, live execution guards, ticker-radar behavior, or order logic.
---

## P148 — Inline live heartbeat status

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The routine live heartbeat looked like a periodic event log, but the displayed seconds were only the latest cycle duration and the line was emitted on the old `cycle % 10` cadence. That created noisy historical status lines and made the operator read `20.3s` as a wall-clock heartbeat interval.

### Change

- Add a small live-only status logger boundary around the runner logger.
- For the default interactive console logger, render routine heartbeat status with carriage-return overwrite instead of appending a new line.
- Before any non-status live/Telegram/error/position log, terminate the current heartbeat line so important events remain sequential.
- Rename the heartbeat text to a cycle-duration label; this was later superseded by P201's prefix-free heartbeat.
- Keep the old sparse status cadence for external/non-interactive loggers.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# console smoke: 20s -> 19s -> 22s heartbeat updates collapse to one visible status line; an error/position log appears as a new permanent line.
```

### Risk

Low. Trading logic, artifacts, order guards, Telegram payloads and backtest behavior are unchanged. The only behavior change is console presentation of routine heartbeat status.


---

## P149 — Strict high-based hourly levels

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The 1h overhead-level scanner still accepted visually weak levels: range intersections could count as touches even when the level ran through the candle body/interior, touches could cluster too close in time, and the pierce filter allowed small wick pierces by default.

### Change

- Count touches only when the 1h candle high is near the level and the candle body remains below the touch band.
- Require at least 6h between counted touches by default via `--min-touch-spacing-hours`.
- Make pierced-level rejection strict by default: any later high above the level is counted as a pierce when `--reject-pierced-levels true`.
- Keep the existing bounce/re-arm checks; the patch tightens touch eligibility instead of post-processing chart output.
- Update the 1h overhead-level strategy spec to match the stricter diagnostic contract.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --min-touches 3 --min-touch-spacing-hours 6 --reject-pierced-levels true --max-level-pierce-pct 0.0
```

### Risk

Medium for diagnostics: fewer levels will be emitted, especially symbols where prior detections were body/interior intersections or recently repeated taps. Live trading and anomaly backtest execution are unchanged.

---

## P150 — Reject stale source candles for 1h levels

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

A pivot high can look like resistance even when the market closed above that same price shortly before it. Such a candle is not a clean source for an overhead level: the level was already accepted/reclaimed within the previous 12h.

### Change

- Add `level_source_close_lookback_hours=12` to the hourly-level scan config.
- Filter pivot/high source candles before level clustering: if any close in the previous 12h is above the candidate candle high, that candle is not allowed to seed a level.
- Expose the guard as `--level-source-close-lookback-hours` and print it in scan startup context.
- Keep the guard at pivot selection time, not as chart/output post-processing.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --level-source-close-lookback-hours 12
```

### Risk

Medium for diagnostics: fewer levels will be emitted when a later-looking pivot is actually below a recent accepted close. Live trading and anomaly backtest execution are unchanged.


---

## P151 — Rework trade chart context and flow panels

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The trade chart spent two lower panels on histogram-style volume and quote-per-trade bars, while the context panel only showed a short timeframe-compressed view tied to the same x-axis as the trade window. This made it harder to review a trade against broader 1h resistance context.

### Change

- Merge quote volume and `number_of_trades` into one bottom panel.
- Render both lower metrics as normalized line curves: quote volume in orange, relative trade count in green.
- Replace the old time-aligned context panel with an independent 1h context panel covering the last 7 fully closed days before entry.
- Draw strict `run-hourly-levels` overhead levels on the 1h context panel as unlabeled horizontal lines without legend.
- Keep trade execution/chart annotations on the upper trade-window panel only.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python - <<'PY'
# synthetic render_anomaly_trade_chart smoke
PY
```

### Risk

Low for trading logic: this is chart/artifact-only. Medium for chart review: lower flow lines are independently normalized to 0-100, so they compare timing/shape, not absolute magnitude.

---

## P152 — Attach open-position trade chart

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The operator should see the same three-panel trade context immediately when a live position opens, not only after close. The open chart must avoid implying a completed risk/reward box and should instead show active TP1/SL levels.

### Change

- Add a synchronous Telegram photo send path that returns the Telegram message id, preserving later stop/close replies to the opening message.
- Render an open-position chart after verified entry fill and stop placement.
- Reuse the canonical trade-chart renderer with an explicit 1h context frame, so the middle panel covers 7 days before entry without forcing the top trade-window fetch to load 7 days of small-timeframe data.
- For open charts, disable risk/reward rectangles and draw TP1 as a green dashed level and SL as a red dashed level.
- Keep text-only Telegram fallback if chart rendering or photo delivery fails.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic open render_anomaly_trade_chart smoke with hourly_context_frame and no risk/reward blocks
```

### Risk

Low for trading logic: the patch runs after verified open state is created and does not alter signal selection, order placement, stop placement, TP/SL math, or monitoring. Medium for live operator UX/API load: each opened position now performs one extra chart render and one H1 OHLCV context fetch before sending the Telegram open photo. Context fetch failures are recorded as `chart_context_fetch_failed`; chart/photo failures fall back to text-only Telegram.
---

## P153 — Limit trade-chart level discovery to 7d context

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The trade/open chart middle panel shows only the last 7 fully closed 1h days before entry. Any overhead levels drawn on that panel must be discovered from that exact displayed window, not from a wider source frame or cache history. Otherwise the chart can show levels whose source/touches are invisible in the panel.

### Change

- Add an explicit `_slice_trade_chart_hourly_level_context` guard before calling `find_hourly_overhead_levels`.
- Pass the entry timestamp and 7-day context length into trade-chart level discovery.
- Run hourly-level discovery only on the sliced 1h window: `[entry_hour - 7d, entry_hour)`.
- Keep the change artifact-only; live/backtest signal selection and execution are unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: a full 10d context with level touches older than 7d yields no drawn chart level
```

### Risk

Low for trading logic: chart-only. Medium for chart review: some previously drawn context levels may disappear when their confirming touches existed only outside the visible 7-day panel.
---

## P154 — Standardize Telegram text style and symbol emoji

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Telegram operator messages used mixed icon semantics and mixed plain/code formatting for timeframe/context and error payloads. Position messages also used event-specific pseudo-random nature emojis, so the same symbol could show different emojis across open/stop/close messages.

### Change

- Replace event-key nature emojis with deterministic animal emojis derived from the compact symbol string via SHA-256.
- Use the same animal emoji for every symbol-specific message: blocked order, open, stop move, close, and position integrity error.
- Use service emojis for non-symbol service messages: `🚧` for startup/network pause and `⚠️` for errors.
- Render startup timeframe pairs and symbol signal context lines as Telegram `<code>...</code>`.
- Render error details/reasons as Telegram `<code>...</code>` consistently.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python - <<'PY'
# synthetic Telegram formatter smoke: stable BTC emoji, escaped code context, service error text
PY
```

### Risk

Low for trading logic: Telegram/UI-only. Low operator UX risk: symbol emoji assignment changes once after deployment but remains stable across future runs for the same compact symbol.

---

## P155 — Make live audit failures explicit

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Several live operator/audit failures were visible only in console logs or implicit fallbacks. That is not enough for post-run review because console output can be lost and Telegram delivery is not the source of truth.

### Change

- Add `network_degraded` and `network_recovered` events to `live_events.csv`.
- Give `TelegramDispatcher` an artifact event writer and record async Telegram send failures as `telegram_async_send_failed`.
- Record generic `telegram_photo_send_failed` from the photo helper.
- Record open-message chart missing id and text fallback/missing-id events.
- Record close-message photo sent/missing/failed events and explicit text fallback.
- Record `reject_stop_cooldown` when a valid signal is consumed because the symbol is still in stop cooldown.
- Preserve existing text/chart fallback behavior; no order, signal, risk, stop, TP, or monitor logic is changed.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python - <<'PY'
# synthetic Telegram dispatcher failure callback smoke
# static grep smoke for network_degraded/network_recovered/reject_stop_cooldown/telegram_close_photo_failed
PY
```

### Risk

Low for trading logic: audit-only. Low runtime risk: Telegram failure events are written through the existing artifact writer lock. If artifact event writing itself fails inside the Telegram dispatcher, the failure is logged instead of recursively trying to write another event.

---

## P156 — Stop unresolved live exits from producing proxy PnL

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Live could remove an already-closed exchange position from monitoring while still writing it as a normal `position_closed` row with PnL calculated from a proxy stop/last-known price when the actual exit fill was not recovered. That makes edge/PnL review look more certain than the exchange evidence supports. The same patch also fixes the Linux CLI startup blocker caused by importing `ctypes.windll` at module import time.

### Change

- Import `ctypes.windll` only inside the Windows console-encoding branch so `python main.py -h` works on Linux.
- Add ledger terminal status `exit_unresolved` for live positions whose exchange exposure is gone but actual exit fill is unknown.
- Replace proxy-PnL finalization for external zero-position and unresolved stop-fill cases with `position_exit_unresolved` artifacts and blank realized PnL fields.
- Keep stop cooldown accounting when the stop appears to have closed the exchange position but the stop fill cannot be recovered.
- Preserve verified-fill close behavior for TP1/full stop exits.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-anomaly-live --help
# fake-exchange smoke: external zero amount -> position_exit_unresolved, ledger status=exit_unresolved, no realized PnL
# fake-exchange smoke: stop trigger + fetch_order_fill failure -> position_exit_unresolved, ledger status=exit_unresolved, no realized PnL
```

### Risk

Medium audit semantics change: some rows previously counted as normal closed trades now become non-PnL terminal rows. Trading entry logic, verified stop placement, TP1 behavior, and verified stop-fill close logic are unchanged.


---

## P158 — Split HTF setup from LTF live entry

Status: SUPERSEDED by P159 combined patch
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The previous live code used the configured `levels_timeframe/entry_timeframe` pair mostly as a label: the signal was built on the higher timeframe only. P157 was intentionally not applied because making the entry timeframe the whole signal source would reject valid HTF flow wake-ups whenever micro candles were noisy. The strategy needs a two-stage contract instead: HTF/forming-HTF detects the pump wake-up, LTF confirms that the entry is still executable.

### Change

- Live scans every closed `entry_timeframe` candle, not every closed `levels_timeframe` candle.
- Live builds a forming HTF setup candle from closed LTF candles inside the current HTF bucket.
- HTF/forming-HTF owns dormancy baseline, quote-volume/trade-count anomaly, exhaustion checks, initial risk box and setup context.
- LTF owns freshness, entry activation hold, verticality/path confirmation and final live execution guards.
- Sub-minute live frames keep zero-flow buckets visible so no-trade periods do not disappear from the signal path.
- Backtest gains pair-aware `--setup-timeframe` / `--entry-timeframe` mode with `feature_contract=htf_setup_ltf_entry_v1`.
- Backtest forms HTF setup rows from historical entry-timeframe candles and simulates entries/exits on the entry timeframe.
- `entry_delay_candles` is now calculated from the actual simulation frame step.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic pair-aware backtest smoke: 5m setup + 30s entry creates one forming_htf_from_entry_tf_backtest candidate after 4 closed entry candles
```

### Risk

Medium. This changes signal timing and candidate construction. It should increase comparability between live and backtest, but historical results from older single-timeframe backtests are not directly comparable to `htf_setup_ltf_entry_v1` runs. Requires cache for the chosen entry timeframe; without it, pair-aware backtest records explicit read errors instead of pretending comparability.

---

## P159 — HTF/LTF live health and rounded TP1 target

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

P158 fixed the fake timeframe-label problem but left four live-health issues: forming HTF flow was compared to full HTF baseline without pace normalization, transient entry/setup fetch failures consumed the scan slot, pair-aware sub-minute backtest could silently depend on missing historical entry-TF cache, and TP1 remained a raw 1R level rather than the next usable round number above 1R.

### Change

- TP1 is now the nearest higher round market number above the previous 1R TP1, with the rounding step derived from current price and movement size.
- Live pre-order RR/TP-already-reached guards use the rounded TP1, and verified-fill position management recomputes TP1 from the actual fill then rounds it again.
- Forming HTF setup flow uses pace-normalized quote/trade ratios while also requiring a raw-progress floor so tiny early microbursts do not pass as HTF wake-ups.
- Live scan timestamps are marked only after setup and entry data fetch/build completes; transient fetch failures are visible but retryable on the next cycle.
- Pair-aware backtest applies the same pace-normalized forming HTF ratio contract.
- Pair-aware sub-minute backtest now explicitly errors with `missing_subminute_entry_cache:<tf>` / `requires_historical_aggtrades_cache` when no historical entry-TF cache exists instead of pretending comparability.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic: 1R TP is rounded upward to the next movement-sized 1/2/5 grid level
# synthetic: forming 5m from 30s uses quote/trade pace ratios plus raw-progress floor
# synthetic: setup/entry fetch failure does not advance _last_signal_scan_closed_at
```

### Risk

Medium. TP1 is now usually farther than exactly 1R, so TP1 hit-rate may drop while RR/chase checks become more meaningful. Pace-normalized forming HTF can admit earlier setups, but the raw-progress floor is meant to prevent single-bucket noise from passing. Historical results before P159 are not directly comparable.

---

## P160 — Remove retired closed-HTF live/backtest code

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

After P159, the active live path is `forming HTF setup from closed LTF buckets -> LTF entry permission`. The older closed-HTF signal builder remained in `anomaly_micro_live.py` but was no longer reachable. Backtest also retained two unused helper functions from the older single-timeframe/grid path. Leaving these around increases the risk that future patches edit inactive code or reintroduce closed-HTF semantics by mistake.

### Change

- Remove unused live methods `_build_recent_signals()` and `_build_signal_at_start()`.
- Remove unused Telegram/category formatting helpers `_detail_float()` and `_format_category_rejection_summary()`.
- Remove unused backtest helpers `_resolve_signal_stop()` and `_entry_grid_signal_universe()`.
- Keep active HTF/LTF signal construction, category rejection events, order/fill/stop handling, TP1 rounding and pair-aware backtest logic unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
```

### Risk

Low. This is deletion-only cleanup of functions with no runtime call sites in the current P159 path. No signal thresholds, entry/exit rules, order handling, stop handling, TP1 math, or backtest execution semantics are changed.

---

## P161 — Fix anomaly market-entry safe divide helper

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Market-entry backtest execution crashed during `simulate_anomaly_trades()` because `_resolve_signal_entry()` called the old helper name `_safe_divide`, while this module defines and uses `_safe_divide_value`.

### Change

- Replace the two stale `_safe_divide(...)` calls in market-entry drift/RR guards with the existing local `_safe_divide_value(...)` helper.
- Keep market-entry execution semantics unchanged: drift remains absolute, TP1-before-entry and RR-collapse guards still use next-bar-open proxy prices.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP
# synthetic smoke: market entry path calls _resolve_signal_entry without NameError
```

### Risk

Low. This is a direct NameError fix in the executable backtest path; no thresholds, signal filters, TP/SL logic, live order logic, or data-quality fallback behavior change.

---

## P162 — Fix live terminal PnL amount accounting

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Live terminal PnL could double-count size when a position had `remaining_amount == 0`. `_finalize_position()` fell back to the original `position.amount`, so a TP1/full-close path could add a second terminal PnL leg on top of already realized PnL. A second related audit issue existed in the TP1 monitor branch: if the TP1 reduce-only fill was only partial but the exchange position became zero, the disappeared remainder had no verified exit fill and should not be written as a normal closed trade with proxy PnL.

### Change

- `_finalize_position()` now uses the verified remaining amount by default and accepts an explicit terminal `exit_amount`; it never falls back from zero remaining amount to the original entry amount.
- `position_closed` events now include terminal exit amount/price and realized PnL before the terminal leg for audit.
- TP1 monitoring now tracks previous remaining amount, TP1 filled amount and expected remaining amount.
- If TP1 fill is confirmed but a material residual position disappears without a verified fill, live records `tp1_remaining_exit_unresolved` and finalizes as `position_exit_unresolved` with blank realized PnL instead of fabricating a normal closed trade.
- If TP1 genuinely closes the whole remaining amount, `_finalize_position(..., exit_amount=0.0)` records only the already verified realized PnL.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic smoke still needed: remaining_amount=0 terminal close must not add position.amount PnL
# synthetic smoke still needed: partial TP1 fill + exchange amount zero -> position_exit_unresolved, blank realized PnL
```

### Risk

Low-to-medium audit semantics change. Some rows that previously looked like normal profitable/loss-making closes can become `exit_unresolved` when the residual exchange exposure disappears without a verified fill. That is intentional; trading entry/selection logic is unchanged.

---

## P163 - Export anomaly flow provenance and rework trade-chart stack

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The latest `anomaly_lab` artifact could not prove quote-volume / trade-count provenance from exported CSVs, which blocks strong conclusions about anomaly nature at artifact-review level. The trade chart also needed an operator-driven layout correction: visible HTF context, bottom `1h` panel, 4-day level-search window, Russian panel labels, and less merged candle rendering on dense windows.

### Change

- Export explicit anomaly flow provenance fields into candidate/signal/trade artifacts:
  - `trade_count_proxy_used`
  - `levels_trade_count_source`
  - `entry_trade_count_source`
  - `levels_quote_volume_source`
  - `entry_quote_volume_source`
- Keep provenance explicit as cached OHLCV native fields; no proxy fallback is introduced.
- Rebuild canonical anomaly trade charts as:
  - LTF panel
  - HTF panel
  - normalized volume/trades panel
  - bottom 1h context panel
- Make 1h context end at the hour candle that contains the main chart end, use a 4-day window, and search chart levels only inside that same 4-day window.
- Render Russian watermark-style panel names in the upper-left background of each panel.
- Narrow candle bodies adaptively as panel density increases.
- Fetch 4-day 1h context for both live open and close charts.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --help
# synthetic/render smoke: canonical chart contains LTF/HTF/flow/1h panels and saves successfully
# artifact check: provenance columns exist in anomaly_candidates.csv / anomaly_signals.csv / anomaly_trades.csv
```

### Risk

Low for trading logic: artifact/export/chart behavior only. Medium for visual artifact expectations: chart layout and chart aspect ratio change materially, so chart consumers should not assume the old three-panel geometry.

---

## P164 - Handle empty subminute anomaly signal sets

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Running the intended subminute TF sets (`5m/30s`, `1m/15s`, `1m/5s`) exposed a diagnostics bug when entry-timeframe cache is absent. The pair-aware collector can return no valid signal rows, but derivative-context fetch setup still tried to select `symbol` and `decision_timestamp_ms` from an empty no-column signals frame.

### Change

- If post-filter signals are empty or lack the signal-universe columns, use an empty `symbol/decision_timestamp_ms` frame.
- Let the existing no-signal derivative context path write empty fetch status instead of crashing.
- Reduce trade-chart watermark label font size from 28 to 18.
- For pair-aware subminute backtests, use cached `1s` OHLCV as an in-memory source for `5s/15s/30s` entry frames when exact entry-timeframe cache directories are absent.
- Mark entry provenance as `cached_1s_aggregated_to_<tf>` when that fallback is used.
- Use the same `1s -> target subminute` aggregation path during trade simulation, not only during candidate construction.

### Validation

```bash
python -m compileall research_tools/anomaly_strategy_backtest.py
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 7
python main.py run-anomaly-lab --setup-timeframe 1m --entry-timeframe 15s --days 7
python main.py run-anomaly-lab --setup-timeframe 1m --entry-timeframe 5s --days 7
```

### Risk

Low/medium: diagnostics-only, but fresh subminute backtests may now run from `1s` cache instead of failing on absent aggregated `5s/15s/30s` cache directories. It does not aggregate from `1m`, and provenance labels distinguish the `1s` aggregation path.

---

## P165 - Materialize subminute anomaly cache and add red-flag profiles

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

True anomaly TF-set runs were too slow and the 7-day result was misleading: the setup cache covered the requested end timestamp, but executable `1s`/derived subminute cache ended on 2026-05-06 11:40 UTC while the requested end was 2026-05-11 08:36 UTC. That made the run effectively a 3-active-day sample. The first red-flag filters also needed to be explicit and audit-visible, not hidden in post-analysis.

### Change

- Add `materialize-anomaly-subminute-cache` CLI command.
- Materialize honest `1s -> 5s/15s/30s` parquet caches with aggregation provenance columns.
- Preserve provenance in anomaly artifacts when exact subminute cache is materialized from `1s`.
- Add `entry_cache_coverage.csv` to anomaly-lab artifacts so requested start/end coverage is visible.
- Replace repeated boolean LTF slicing in pair candidate collection with timestamp `searchsorted`.
- Add optional `red_flag_profile` values:
  - `none`
  - `cautious`
  - `strict`
- Add explicit pre-decision red-flag filters for mark basis, OI-down + mark discount, stale/missing context, taker-buy delta and trade/quote effort per return.
- Add `anomaly_red_flag_summary.csv` over the pre-red-flag signal universe.
- Correct intended anomaly TF-set config from `5m/15s` to `1m/15s`.

### Validation

```bash
python -m compileall research_tools/anomaly_strategy_backtest.py research_tools/anomaly_config.py cli/parser.py cli/commands.py
python main.py materialize-anomaly-subminute-cache --timeframes 5s 15s 30s --overwrite true --output .output/results/anomaly_subminute_cache_materialization_p165.csv
python main.py run-anomaly-lab --days 7 --setup-timeframe 5m --entry-timeframe 30s --end-timestamp-ms 1778488560000 --output-dir .output/results/anomaly_lab_p165_5m30s_cautious --run-entry-grid false --red-flag-profile cautious
python main.py run-anomaly-lab --days 7 --setup-timeframe 1m --entry-timeframe 15s --end-timestamp-ms 1778488560000 --output-dir .output/results/anomaly_lab_p165_1m15s_cautious --run-entry-grid false --red-flag-profile cautious
python main.py run-anomaly-lab --days 7 --setup-timeframe 1m --entry-timeframe 5s --end-timestamp-ms 1778488560000 --output-dir .output/results/anomaly_lab_p165_1m5s_cautious --run-entry-grid false --red-flag-profile cautious
```

### Risk

Medium research risk: `cautious`/`strict` profiles are not production-proven and are currently fit from a very small 3-active-day sample. Defaults remain unchanged. Low data-risk: derived caches are explicitly marked as `1s` aggregation and do not fabricate missing post-2026-05-06 executable data.

---

## P166 - Add aggTrade backfill and runner/dormancy diagnostics

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The intended 14-day anomaly study needs honest executable subminute data. Binance futures kline API rejects `1s` with `Invalid interval`, so `update-cache --timeframes 1s` cannot be used for this. Recent-spike / dormancy and early-runner separation also needed first-class artifact columns instead of ad hoc notebook logic.

### Change

- Add `backfill-anomaly-aggtrade-cache` CLI command to build true `1s` OHLCV from Binance futures `aggTrades`.
- Add chunk skipping for already covered `1s` windows and write a backfill manifest.
- Add `--render-charts true/false` to anomaly-lab so metric sweeps can skip PNG rendering.
- Add recent-spike diagnostics to pair-aware candidates/signals/trades:
  - `prior_spike_count_24h/72h`
  - `prior_fast_fade_count_24h/72h`
  - `prior_big_move_count_24h/72h`
  - `prior_spike_density_72h`
  - `time_since_prior_spike_ms/hours`
- Prior fast-fade / big-move counts only include matured prior spikes whose diagnostic forward window ended before the current decision.

### Validation

```bash
python -m compileall cli/commands.py cli/parser.py research_tools/anomaly_strategy_backtest.py
python main.py backfill-anomaly-aggtrade-cache --help
python main.py backfill-anomaly-aggtrade-cache --symbols COIN/USDT:USDT HOOD/USDT:USDT --days 1 --end-timestamp-ms 1778488560000 --chunk-hours 24
python main.py materialize-anomaly-subminute-cache --timeframes 5s 15s 30s --overwrite true
python main.py run-anomaly-lab --days 14 --setup-timeframe 5m --entry-timeframe 30s --end-timestamp-ms 1778488560000 --render-charts false
python main.py run-anomaly-lab --days 14 --setup-timeframe 1m --entry-timeframe 15s --end-timestamp-ms 1778488560000 --render-charts false
```

### Risk

Medium. The aggTrade backfill is honest but can be very slow for a large active universe. Full `1m/5s` 14-day universe did not complete in one hour; use active-symbol subset results only as directional evidence.

## P167 - Symbol-first live multi-TF scan

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

REST-only live should not spend heavy subminute aggTrade work evenly across cold symbols when the goal is early runner capture. Once a symbol is selected by active state or ticker radar, all configured TF sets should be checked together so `5m/30s`, `1m/15s`, and `1m/5s` do not wait on separate outer loops.

### Change

- Add `scan_hot_timeframes_per_symbol=true` as the default live scan mode.
- Add `inactive_scan_slots_per_cycle` as an explicit optional cap for cold round-robin scans; `0` means active/radar-only scanning.
- Cache setup and entry frames inside a live batch so shared setup windows are not refetched for the same symbol.
- Export `signal_symbol_scan_summary` and add scan-mode/cold-slot fields to `symbol_batch_selected`.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: setting `--inactive-scan-slots-per-cycle 0` deliberately stops cold round-robin discovery and relies on ticker radar plus active state. This is suitable for fast production-style live only if ticker snapshots are healthy; artifacts now show the chosen cap and effective scan count.

## P168 - Cache-backed live OHLCV fetch

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P167 reduces which symbols are scanned, but selected symbols still re-requested full setup/entry windows from the exchange. REST-only live should reuse the same honest parquet cache as research where possible, fetch only exact missing ranges, and make any remaining cache gap explicit.

### Change

- Wire `cache_dir` into `LiveAnomalyConfig`.
- Add `--live-ohlcv-cache-enabled` and `--live-ohlcv-cache-write-enabled` CLI flags.
- Route live setup and entry frame fetches through a parquet-backed provider.
- Load cached frames once per process and reuse them from memory across cycles.
- For missing ranges, fetch only those ranges from exchange OHLCV or Binance futures `aggTrades`, then write one incremental parquet batch with `live_cache_source`, target timeframe and version.
- Emit `live_cache_config`, `live_ohlcv_cache_read`, and `live_ohlcv_cache_gap` events.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Medium: `save_incremental` rewrites the target parquet file, so live cache writes should be watched during high-churn runs. Remaining gaps are not hidden; they emit `live_ohlcv_cache_gap` and can still lead to existing empty/missing-signal rejects.

## P169 - Fix live cache candle boundary reads

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Review of P168 found a candle-boundary risk: live cache missing ranges are represented by candle start timestamps, but subminute `aggTrades` must be fetched through the end of the final candle. Reading whole parquet files on first use was also unnecessary work for large caches.

### Change

- Add `ParquetStorage.load_window_result()` with parquet timestamp filters and safe full-load fallback.
- Live cache first-load now reads only the requested closed-candle window.
- Missing range fetches now request `missing_end + timeframe_ms - 1`, so the last subminute candle is complete.
- Paged `aggTrades` calls keep `endTime`, reducing overshoot beyond the requested window.
- Live decision frames return only closed cached candles up to `expected_end_ms`; accidental current/partial cached rows are excluded.
- `live_ohlcv_cache_read` now reports expected/window end timestamps.

### Validation

```bash
python -m compileall data/storage/parquet_storage.py research_tools/anomaly_micro_live.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: parquet filter support depends on the installed parquet engine; fallback preserves behavior by loading and filtering in memory. The main remaining speed limit is `save_incremental`, which still rewrites the target parquet file when live writes fetched rows.

## P170 - Buffer live OHLCV cache writes

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P168/P169 made live cache-backed and closed-candle aligned, but `save_incremental` still rewrites the target parquet file. Calling it during each selected-symbol fetch can slow the live loop exactly when hot symbols need fast follow-up scans.

### Change

- Add `--live-ohlcv-cache-flush-interval-seconds` and `--live-ohlcv-cache-max-buffer-rows`.
- Buffer fetched live OHLCV rows in process memory after they are already merged into the decision frame cache.
- Flush buffered parquet writes after the interval/cap, and force flush on max-cycles, keyboard interrupt, data-integrity stop and internal-error stop.
- Emit `live_ohlcv_cache_buffered`, `live_ohlcv_cache_flushed`, `live_ohlcv_cache_flush_failed`, and `live_ohlcv_cache_flush_summary`.
- If memory cache does not cover a wider later request, read that window from parquet before going to the exchange.

### Validation

```bash
python -m compileall data/exchanges data/storage research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: a hard process crash can lose rows still in the write buffer, but trading decisions use the in-memory fetched frame immediately and the missing cache rows can be re-fetched. Flush failures are kept visible and failed rows remain pending for the next flush attempt.

## P171 - Run anomaly lab across working TF sets by default

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

`python main.py run-anomaly-lab --days 7` silently inherited the legacy parser default `--timeframe 1m`, so the command ran `1m/1m` instead of the current working anomaly TF sets.

### Change

- Remove the hidden `--timeframe 1m` parser default.
- Treat no explicit timeframe flags as a multi-run over `5m/30s`, `1m/15s`, and `1m/5s`.
- Keep explicit `--timeframe`, `--setup-timeframe`, or `--entry-timeframe` as single-pair override mode.
- Write multi-run outputs into per-pair subdirectories and emit `anomaly_lab_timeframe_runs.csv`.

### Validation

```bash
.venv\Scripts\python.exe -m compileall cli\commands.py cli\parser.py research_tools\anomaly_strategy_backtest.py research_tools\anomaly_config.py main.py
.venv\Scripts\python.exe -c "from cli.parser import build_parser; p=build_parser(); a=p.parse_args(['run-anomaly-lab','--days','7']); print(a.timeframe, a.setup_timeframe, a.entry_timeframe)"
```

Monkeypatched smoke confirmed default pairs:

```text
5m/30s
1m/15s
1m/5s
```

### Risk

Low/medium: the default command now does roughly three backtests instead of one, so runtime and chart generation cost increase. This is intended because the old default result was misleading; explicit `--timeframe 1m` still allows the legacy single run.

## P172 - Collect anomaly TF sets in one symbol-major pass

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P171 made the default anomaly lab run all working TF sets, but it still launched three independent backtests. That meant the backtest walked the symbol universe once per TF pair instead of checking all TF sets while the symbol frames were already loaded.

### Change

- Add `collect_pair_anomaly_rows_for_configs()` for symbol-major candidate collection across multiple `AnomalyBacktestConfig` objects.
- Read each symbol/timeframe frame once per symbol pass, then evaluate all eligible TF pairs for that symbol.
- Let `run_anomaly_strategy_backtest()` accept `precollected_candidates`, preserving existing per-pair artifact writing, signal filtering, trade simulation, grids and charts.
- Change default `run-anomaly-lab` multi-TF mode to precollect candidates once, then run per-pair artifact pipelines from those precollected rows.
- Add `collection_mode` to `anomaly_lab_timeframe_runs.csv`.

### Validation

```bash
.venv\Scripts\python.exe -m compileall cli\commands.py cli\parser.py research_tools\anomaly_strategy_backtest.py main.py
```

Smoke checks:

```text
CLI default calls collect_pair_anomaly_rows_for_configs once with 5m/30s, 1m/15s, 1m/5s, then runs three per-pair artifact pipelines with precollected candidates.
Direct single-config comparison on ATH/USDT:USDT 1m/15s over 7 days produced the same candidate decision timestamp in single and multi collectors.
```

### Risk

Medium: candidate collection order changed for default multi-TF runs. The per-pair downstream logic is unchanged, but the next full run should compare candidate/signal counts against the prior indexed run for the same end timestamp before using profitability deltas.

## P173 - Add runner category filters and candidate reuse replay

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The latest anomaly-lab directory had completed 5m/30s and 1m/15s artifacts, but 1m/5s was interrupted. Recollecting all candidates took hours, while filter iteration only needs the existing enriched `anomaly_candidates.csv` files.

### Change

- Add red-flag profiles: `runner_balanced`, `runner_reclaim`, `runner_flow`.
- Add filter fields for flow hold, 72h prior spike/fade counts, lower wick and upper wick shape.
- Extend red-flag summaries to include quote/trade ratio caps and effort-per-return caps.
- Add `--reuse-candidates-dir` to `run-anomaly-lab`; it replays filters from existing candidate artifacts and skips missing pair artifacts explicitly.
- Reused candidate mode avoids refetching derivatives context because existing candidates are already enriched.

### Validation

```bash
.venv\Scripts\python.exe -m compileall cli\commands.py cli\parser.py research_tools\anomaly_strategy_backtest.py main.py
.venv\Scripts\python.exe main.py run-anomaly-lab --help
.venv\Scripts\python.exe main.py run-anomaly-lab --days 30 --reuse-candidates-dir .output\results\anomaly_lab --output-dir .output\results\anomaly_lab_reuse_runner_balanced_fast --red-flag-profile runner_balanced --render-charts false
```

### Result

Replay used completed pairs only:

```text
5m/30s runner_balanced: 42 closed, avg +1.6435%, median +1.5601%, sum +69.03%, WR 83.33%, TP1 78.57%
1m/15s runner_balanced: 82 closed, avg +1.8407%, median +1.2530%, sum +150.94%, WR 79.27%, TP1 74.39%
Combined: 124 closed, avg +1.7739%, median +1.3985%, sum +219.97%, WR 80.65%, TP1 75.81%, active days 25
```

### Risk

Medium/high: this is an in-sample replay on partial executable coverage and excludes the interrupted 1m/5s pair. Treat it as a strong category hypothesis, not proven production edge.

## P174 - Align live entry category with OI-confirmed runner replay

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The previous live defaults traded `balanced_market,mild_market`, while the strongest recent backtest category was a runner-oriented replay. OI is materially useful in the latest artifacts, but the useful zone is moderate confirmation, not the old live default `OI > 5%`.

### Change

- Add backtest red-flag profile `runner_oi_confirmed`.
- Add live pump category `runner_oi_confirmed` and make it the live default category.
- Live `runner_oi_confirmed` requires `OI 3x5m > 0.3%`, mark basis >= 30bp, quote/trade ratio caps, quote/trade effort caps and start taker-buy delta cap.
- Live mark basis is fetched from Binance mark-price context as-of the decision timestamp; missing/stale mark context is a category reject, not a fallback.
- Live OI requirement is category-level; the global live OI default is now unset unless a category or CLI flag requires it.

### Validation

```bash
.venv\Scripts\python.exe main.py run-anomaly-lab --days 30 --reuse-candidates-dir .output\results\anomaly_lab --output-dir .output\results\anomaly_lab_reuse_runner_oi_confirmed --red-flag-profile runner_oi_confirmed --render-charts false
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py research_tools\anomaly_strategy_backtest.py cli\parser.py cli\commands.py
```

Replay used completed pairs only:

```text
5m/30s runner_oi_confirmed: 15 closed, avg +2.574%, median +1.717%, sum +38.61%, WR 100.00%, TP1 100.00%
1m/15s runner_oi_confirmed: 21 closed, avg +3.014%, median +2.710%, sum +63.29%, WR 80.95%, TP1 76.19%
Combined: 36 closed, avg +2.831%, median +1.885%, sum +101.90%, WR 88.89%, TP1 86.11%, top10 dependency 75.58%
```

### Risk

High: this is very selective, in-sample, excludes interrupted 1m/5s, and top10 dependency is high. Live still cannot perfectly enforce backtest `prior_fast_fade_count_72h` until it has persisted enough same-symbol signal outcomes.

## P175 - Add live entry lag diagnostics

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Live can arrive after the first closed-candle executable entry that the backtest model would have used. This must be measured without slowing the trading path.

### Change

- Add `first_executable_entry_timestamp_ms`, `entry_lag_ms`, `entry_lag_ltf_candles`, `entered_late_vs_first_executable`, `entry_order_submit_lag_ms`, and `entry_order_submit_lag_ltf_candles` to `live_positions.csv`.
- Add `previous_live_scan_closed_timestamp_ms`, `first_unscanned_decision_timestamp_ms`, and `live_scan_gap_ltf_candles` to distinguish order-entry lag from symbol-scheduler scan lag.
- Add non-blocking `missed_entry_replay_probe`: when a selected signal has a scan gap, a background thread replays the skipped LTF decision candles from the already loaded OHLCV window and emits the first earlier candle where the same live category filter would have selected a signal.
- Add the same fill-lag fields to `position_opened` events.
- Add entry-lag diagnostics to execution guards/reject events such as stale signal, entry drift and RR collapse.
- The diagnostic uses only existing signal timestamps, order submission timestamp, fill timestamp and the already-fetched live price. It performs no extra exchange request.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py
Inline smoke: synthetic 1m/15s scan-gap replay found the first prior signal at the 4th skipped 15s candle and reported a 6-LTF-candle lag to the current signal.
```

### Risk

Low: this is diagnostic-only. Existing live ledger readers that assume the exact old column set may need to tolerate the added columns.

## P176 - Make missed-entry replay probe honest and budget-tunable

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The missed-entry probe must not understate live scheduler lag when the skipped window is larger than the probe cap, and API/context limits are scarce.

### Change

- Replay skipped LTF decisions from the earliest missed candle, not from the newest skipped tail.
- Add `candidate_decision_count_total`, `probe_truncated`, and `unprobed_newer_decision_count` to probe events.
- If no prior signal is found in a truncated prefix, emit `no_prior_signal_found_in_probed_prefix` instead of implying that the full skipped window was checked.
- Expose live `--signal-scan-backfill-candles` so the probe budget can be lowered without code edits.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
```

### Risk

Low/medium: this is diagnostic-only, but OI/mark-confirmed categories can still spend context calls inside the background probe. Use a lower `--signal-scan-backfill-candles` when exchange limits are tight.

## P177 - Live latency accounting and precise-scan budget

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The current live loop can report a 10+ minute full-universe cycle while the expensive work is concentrated in active/radar precise scans. We need honest timing attribution before adding more wake-up logic, and a clean budget control that does not hide active symbols.

### Change

- Add stage timings to `live_cycle_summary`: ticker radar, batch selection, signal scan, open-signal handling, order reconcile, and cache flush seconds.
- Add optional `max_precise_scan_symbols_per_cycle`.
- Active symbols are never dropped by the cap. The cap only limits how many ticker-radar watch symbols are promoted into expensive precise scans in the current cycle; overflow remains in `ticker_radar_waiting_symbols`.
- Expose CLI `--max-precise-scan-symbols-per-cycle`.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
```

### Risk

Low for diagnostics, medium for cap usage: too low a cap can delay radar-watch symbols. This is explicit in artifacts via waiting counts/symbols, not hidden.

## P178 - Static high-cap live universe exclusion

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

PNO is looking for early runner potential. Large-cap majors rarely provide the 20%+ runner profile and still consume ticker/universe slots. We need a no-external-source way to remove obvious majors without pretending this is a market-cap oracle.

### Change

- Add a conservative static high-cap base list for live default universe: BTC, ETH, BNB, SOL, XRP, DOGE, ADA, TRX, LINK, AVAX, LTC, BCH, DOT.
- Apply it only to the default exchange USDT-swap universe.
- Do not filter explicit `--symbols`; manual symbol tests remain exact.
- Emit `live_symbol_universe_filter` with input/output counts and excluded symbols.
- Expose `--exclude-default-high-cap-symbols` to disable the filter.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
```

### Risk

Medium: this is a subjective static universe choice, not a data-derived market-cap filter. It improves focus and speed but creates selection bias; excluded symbols must stay visible in artifacts.

## P179 - Live cache flush and aggTrade tail cost reduction

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Timing run `20260513_164045` showed signal scan and cache flush as the dominant live bottlenecks. The expensive scan path repeatedly touches subminute aggTrade tails; cache flush can block a cycle for 10-15 seconds.

### Change

- Expand the static live high-cap exclusion list with additional obvious majors.
- Change live OHLCV cache write defaults to flush less often and with a larger buffer: 30 seconds and 50k rows.
- Add `live_ohlcv_cache_flush_max_symbol_timeframes` default 20 to bound non-forced flush work per cycle.
- Keep forced shutdown/error/max-cycle flush behavior as full flush.
- Split flush summary into `failed_symbol_timeframes` and `deferred_symbol_timeframes` so deferred work is not mislabeled as failed.
- Improve cycle-local aggTrade raw cache from full-range-only hits to partial interval coverage: only missing raw time intervals are fetched, then cached rows are deduped and sorted before aggregation.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: aggTrade missing-range subtraction and dedupe passed.
```

### Risk

Medium: fewer cache flushes means a hard process kill can lose more recently fetched cache rows, but trading diagnostics/events are still written immediately and the cache is forced on graceful shutdown. Partial aggTrade cache must be monitored with `aggtrade_cache_hits`, `aggtrade_network_calls`, and `live_ohlcv_cache_gap`.

## P180 - Human KeyboardInterrupt handling

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Ctrl+C during live or another CLI command should stop cleanly without a Python traceback.

### Change

- Catch `KeyboardInterrupt` in the common command wrapper and return code 130 with a short user-facing message.
- Catch `KeyboardInterrupt` at top-level `main()` as a final guard for interrupts outside command wrappers.

### Validation

```bash
.venv\Scripts\python.exe -m compileall main.py cli\commands.py research_tools\anomaly_micro_live.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
```

### Risk

Low. Existing live-loop cleanup still handles graceful live interruption first; this patch covers interrupts that escape that loop.

## P181 - First reactive seam: live ticker snapshot source

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Reactive live should be introduced through narrow data-source seams, not by rewriting anomaly decision logic. The first safe seam is ticker radar ingestion because it is scheduling-only and does not directly decide trades.

### Change

- Add `LiveTickerSnapshotSource` protocol.
- Add `RestLiveTickerSnapshotSource` implementation backed by current exchange `fetch_ticker_snapshots`.
- Inject the source into `AnomalyMicroLiveRunner`; default behavior remains REST-backed.
- Add ticker radar `source` to `ticker_radar_snapshot` and `ticker_radar_failed` events.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\commands.py cli\parser.py main.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: fake ticker source promoted a symbol and wrote source=fake_ticker_source in ticker_radar_snapshot.
```

### Risk

Low: no trade decision logic changes. Future WS ticker source can be plugged into the same protocol and compared in shadow before becoming primary.

## P182 - Second reactive seam: live scheduler batch selection

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Reactive ingestion should change how symbols become scan candidates without changing anomaly evaluation. The scheduler needs an explicit selection contract and scan-reason provenance before WS/event-driven sources are introduced.

### Change

- Add `LiveSymbolBatchSelection` data structure.
- Split current `_next_symbol_batch` into selection plus event emission.
- Preserve current REST/ticker-radar/round-robin behavior under `scheduler_source=rest_round_robin_scheduler`.
- Add `scan_reason_by_symbol` to `symbol_batch_selected` diagnostics.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\commands.py cli\parser.py main.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: scheduler selection returns the same inactive batch and writes scheduler_source/scan_reason_by_symbol.
```

### Risk

Low: this is a structural seam only. Future scheduler implementations must not silently drop symbols; waiting/dropped reasons must be explicit in `symbol_batch_selected`.

## P183 - WebSocket ticker radar source without REST fallback

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The next reactive step is to make all-symbol ticker wake-up event-driven. This must not silently fall back to REST, because hidden transport fallback would mask stream gaps and latency problems.

### Change

- Add `BinanceWsAllTickerSnapshotSource` backed by Binance USD-M futures `!ticker@arr`.
- Make WebSocket ticker source the live default via `live_ws_ticker_enabled=True`.
- Add `--live-ws-ticker-enabled`, `--live-ws-ticker-stale-ms`, and `--live-ws-ticker-startup-wait-seconds`.
- If WebSocket ticker is not ready, stale, malformed, or disconnected, ticker radar emits `ticker_radar_failed` with `source=binance_ws_all_ticker`; no REST fallback is used.
- Keep explicit REST ticker source available only when `--live-ws-ticker-enabled false`.
- Add `aiohttp` dependency for WebSocket transport.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\commands.py cli\parser.py main.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: not-ready WS source raises explicit ws_ticker_not_ready.
Inline smoke: WS all-ticker payload normalizes to ExchangeTickerSnapshot with source=binance_ws_all_ticker.
```

### Risk

Medium/high: while WS ticker is unhealthy, radar promotions stop instead of falling back to REST. This is intentional honesty. Live diagnostics must monitor `ticker_radar_failed`, `source`, and stale/not-ready reasons before reducing REST cold audit further.

## P177 - Fix live OHLCV cache concat warning

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Live cache fill could concatenate an empty cache placeholder with freshly fetched candles. Pandas 2.2 emits `FutureWarning` for this pattern, and future dtype inference could change.

### Change

- Add a typed empty OHLCV cache frame schema.
- Add `_concat_cached_ohlcv_frames` that prepares inputs and excludes empty cache placeholders before `pd.concat`.
- Use the helper for live cache memory merge, fetched candle merge, and buffered cache flush.
- Keep live signal, entry, stop and PnL logic unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low: cache-frame assembly only. Empty cache placeholders are skipped before concat, while non-empty candles still go through the same timestamp dedupe/sort preparation.


## P178 - Reduce live aggTrades duplicate fetches and expose honest cycle timing

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Live batch scans can spend most of the cycle in repeated Binance `aggTrades` REST calls for the same symbol/time window across S30/S15/S5 entry frames. Operator status also showed only the last batch duration, not the effective full universe rotation time, and did not expose local closed-trade PnL.

### Change

- Add a per-cycle raw aggTrades range cache. If a later S15/S5 request is covered by an earlier S30/S15 raw window for the same symbol, reuse the raw rows and aggregate locally to the requested timeframe.
- Keep closed-candle OHLCV output and signal logic unchanged: the same raw trades are aggregated through the existing `_aggregate_aggtrades_to_ohlcv_frame` path.
- Emit `live_cycle_summary` with batch seconds, full-symbol-cycle seconds, active symbol count, local closed PnL percent, and aggTrades request/cache-hit/network-call counts.
- Change operator status to `цикл batch/full · открыто N · активно N · закрыто N · PNL X%`.
- Track PnL only from locally finalized closed positions; no exchange balance/PnL fetch is used for the status line.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: raw aggTrade rows are reused only within the same live cycle and only when the cached raw range fully covers the requested window. This should reduce duplicate REST calls without changing signal thresholds, but live smoke must confirm `aggtrade_network_calls < aggtrade_requests` and no increase in cache gaps/stale rejects.

## P179 - Defer inactive subminute aggTrades until ticker-radar or active state

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Cold inactive symbols can dominate live cycle time by fetching S5/S15/S30 entry frames through Binance `aggTrades` before any cheap wake-up evidence exists. A fallback back to full inactive subminute scans would reintroduce the same latency and stale-entry problem, so the scheduler must make discovery explicit instead of hiding the old behavior behind a fallback.

### Change

- Inactive round-robin symbols defer subminute entry pairs (`S5`/`S15`/`S30`) and do not call `_fetch_chart_frame` / `fetch_binance_agg_trades` for those pairs.
- Precise subminute scans remain enabled for active symbols, opening/open positions through active scheduling, and ticker-radar watch symbols.
- No silent fallback to full inactive `aggTrades` scans exists.
- Startup validation refuses subminute live pairs when `ticker_radar_enabled=false` or `ticker_radar_watch_batch_size < 1`, because that would make inactive discovery blind after deferring subminute scans.
- Deferred inactive pairs are not marked as scanned; once ticker radar promotes the symbol or the symbol becomes active, the latest due precise entry candle is still evaluated.
- Add diagnostics: `inactive_subminute_scan_policy`, `scan_mode`, `subminute_entry_scan_allowed`, `inactive_visit_symbols`, `precise_scan_symbols`, `deferred_inactive_subminute_pairs`, and per-symbol `skipped_inactive_subminute_count`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: inactive S5/S30 defers without fetching, ticker-radar S5 fetches, ticker_radar_enabled=false with subminute pairs raises LiveStartupError.
```

### Risk

Medium and explicit: with subminute entry pairs, cold inactive discovery depends on ticker radar before precise `aggTrades` evaluation. This is intentional to protect live latency. If missed top-growth artifacts show radar is too strict, adjust ticker-radar thresholds/watch batch size; do not restore full inactive subminute `aggTrades` scans.

## P180 - Keep live cycle summary JSON finite before first closed trade

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

After P178/P179, the live status line writes local closed-trade PnL into `live_cycle_summary`. Before the first closed trade, closed notional is zero, and the old `_safe_divide` path produced `NaN`. `append_event(..., allow_nan=False)` correctly rejected that as a live-data integrity error.

### Change

- Make `_closed_pnl_pct_total` return `0.0` when there are no locally finalized closed trades yet.
- Keep integrity strict: non-finite local PnL counters still raise `LiveDataIntegrityError` instead of being written to artifacts.
- Do not fetch balance/PnL from the exchange and do not change trading logic.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: no closed trades -> closed_pnl_pct=0.0 and live_cycle_summary JSON is allow_nan=False compliant.
```

### Risk

Low: this only fixes local status/artifact accounting before the first closed trade. Real closed-trade PnL remains based on `_finalize_position` counters.

## P181 - Number live batches within full symbol cycles

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

After P178, the inline live status showed `batch_seconds/full_symbol_cycle_seconds`, but the word `цикл` still looked like the outer while-loop tick. Operators need to know both the current batch number inside the full round-robin pass and which full pass over the symbol universe is running.

### Change

- Track `batch_in_full_cycle` and `full_symbol_cycle` separately from the existing outer loop `cycle`.
- Reset batch numbering to 1 when a full inactive round-robin pass completes.
- Change the inline status to `цикл batch/full-cycle · batch_seconds/full_symbol_cycle_seconds · ...`.
- Add the same counters to `live_cycle_summary` and `symbol_batch_selected` artifacts.
- Do not change scheduling, signal selection, data fetching, orders, or PnL accounting.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: batch counter starts at 1, increments per selected batch, and resets after cursor completes a full symbol-universe pass.
```

### Risk

Low: operator/artifact numbering only. The counter is tied to the inactive round-robin cursor, so active/radar-only loops without inactive progress should be interpreted as scheduler ticks, not completed full-universe coverage.

## P184 - DANGER: WS aggTrade source for subminute precise scan

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The live loop already avoids full cold subminute scans, but precise active/radar scans still pay repeated REST `aggTrades` tail fetches. The next clean reactive step is a primary WS aggTrade buffer for watched symbols, with explicit gap diagnostics and no silent REST fallback.

### Change

- Add `BinanceWsAggTradeBuffer` using Binance USD-M combined streams and dynamic `@aggTrade` subscriptions for active/ticker-radar watch symbols.
- Default `run-anomaly-live` to `live_ws_aggtrade_enabled=true`.
- Subminute precise scan reads from WS rows first; uncovered ranges are explicitly REST-backfilled and logged as `ws_aggtrade_frame_read`, not hidden.
- Detect stream holes by aggregate trade id gaps and keep those time ranges missing until explicit backfill covers them.
- Add `ws_aggtrade_subscription_target`, `ws_aggtrade_subscription_seconds`, and live cache provenance for WS-backed explicit backfill.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py main.py
python main.py run-anomaly-live --help
# inline smoke: WS aggTrade id gap is detected, backfill coverage removes the gap.
# short live max-cycles=1: startup/artifacts ok; local DNS could not resolve fstream.binance.com, so no real WS tape validation occurred in this environment.
```

### Risk

Medium. WS coverage cannot prove a quiet symbol had zero trades without an exchange heartbeat, so empty/partial intervals remain explicit backfills. This is honest but means first-cycle radar symbols can still pay REST cost until the WS buffer warms. Real validation must inspect `ws_aggtrade_frame_read.status`, `missing_ranges`, `backfill_ranges`, and `aggtrade_network_calls`.

## P195 - DANGER: live WS aggTrade coverage and shutdown health

Status: PROPOSED
Date: 2026-05-14
Commit: UNKNOWN

### Reason

Live run `20260513_202255` showed connected ticker and aggTrade WebSockets, but aggTrade reads were almost always `partial`/`stale`, causing REST gap backfill on nearly every precise scan. The old coverage rule used first/last trade timestamps, so harmless no-trade edge intervals were treated as data holes. Ctrl+C could also be caught by the command wrapper before the runner's internal cleanup path.

### Change

- Track per-symbol WS coverage as subscription-active time, not only first/last trade timestamps.
- Advance coverage while the combined WS connection remains alive, so quiet subscribed intervals are not repeatedly REST-backfilled.
- Keep aggregate trade id gaps as explicit holes until REST backfill covers them.
- When explicit backfill covers a historical range, extend the coverage start/end accordingly, including empty backfills.
- Reduce the aggTrade WS receive timeout to make subscription changes sync faster.
- After setting target symbols, wait briefly for the WS thread to apply subscriptions to reduce `not_subscribed` race reads.
- Add command-level KeyboardInterrupt cleanup: if an interrupt escapes `runner.run()`, force live cache flush and close WS sources before re-raising to the common wrapper.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py main.py
# inline smoke: no-trade covered interval is covered; pre-subscription history needs backfill; empty backfill extends coverage; id-gap still creates a hole.
git diff --check
```

### Risk

Medium. The patch assumes that once Binance confirms/keeps a subscribed combined stream alive, absence of aggTrade messages for that symbol means no trades, not missing data. This is the intended WS contract; id gaps and pre-subscription history remain strict. Next live run must verify `covered` reads increase and REST backfills drop materially without new `signal_scan_empty_ohlcv` or cache gaps.

## P185 - Bound WS aggTrade backfill and fail fast on blind ticker radar

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The short WS live artifacts showed a dangerous blind mode: ticker radar could be unavailable while the loop still emitted cycles with no real discovery. Separately, WS aggTrade precise scans can still spend a full slow cycle in REST backfills when the WS buffer has not yet covered the requested subminute window. That makes a nominal WebSocket run behave like a delayed REST scan.

### Change

- Refuse live startup when subminute entry pairs require ticker radar but the configured ticker source is not ready/healthy.
- Add `live_ws_aggtrade_max_backfill_ms` / `--live-ws-aggtrade-max-backfill-ms` with default `0`.
- When a WS aggTrade precise scan has uncovered ranges larger than that explicit budget, do not REST-backfill; emit `ws_aggtrade_frame_read.status=coverage_pending`, emit `signal_entry_ws_aggtrade_pending`, and leave the signal unscanned for a later covered pass.
- Keep REST backfill possible only by explicitly raising the budget; it is visible through `missing_total_ms`, `backfill_max_ms`, `backfill_skipped`, and `backfill_ranges`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py launcher.py
python main.py run-anomaly-live --help
# live smoke: with WS connected, first promoted symbols should show coverage_pending until the buffer covers the requested interval; cycle time should not be dominated by REST aggTrade backfill.
```

### Risk

Medium and intentional: strict default WS coverage can skip fresh radar symbols until the WS buffer has enough history, especially for 5m/30s windows. If that misses too many opportunities, set a small explicit backfill budget instead of restoring unbounded REST backfill.

## P186 - Make WS scheduler heartbeat and diagnostics event-driven

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The current ZIP has P185 and partial scheduler knobs, but subminute WS-live still treats `symbol_batch_size` as implicit inactive market discovery. The operator status also prints ambiguous cycle/full-cycle timing, which keeps the old polling mental model alive and hides the actual WebSocket health signals.

### Change

- Default subminute+ticker-radar live to zero implicit inactive round-robin slots; inactive scans run only when `inactive_scan_slots_per_cycle` is explicitly configured or in legacy non-subminute mode.
- Keep `symbol_batch_size` as a legacy inactive scan cap outside the WS event-driven default; it is no longer presented as WebSocket market discovery.
- Add typed per-cycle ticker-radar and aggTrade subscription stats to `live_cycle_summary`.
- Replace the inline status from `цикл ...s/full...` with WebSocket-relevant heartbeat diagnostics: scheduler seconds, ticker radar status/coverage/promotions, aggTrade connection/target/subscribed counts, precise vs inactive scan counts, and legacy coverage only when relevant.
- No change to signal filters, entry guards, order/fill/stop logic, or PnL accounting.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout: `symbol_batch_selected.inactive_scan_slots_source=default_ws_event_driven_subminute`, `inactive_count=0` by default for subminute WS-live, `live_cycle_summary.scheduler_cycle_seconds` exists, and the console status reports ticker/aggTrade health instead of ambiguous cycle/full-cycle timing.

### Risk

Low/medium and intentional. Cold inactive scans stop pretending to be WebSocket discovery. If ticker radar misses movers, fix ticker WS health/thresholds or set an explicit diagnostic inactive budget; do not restore hidden polling discovery through `symbol_batch_size`.

## P187 - Restore compact human live heartbeat

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P186 exposed useful WebSocket counters, but putting them directly into the inline operator heartbeat made the console noisy and hard to read. Routine console status should show only the live tempo and business-level state; detailed WS diagnostics remain in `live_cycle_summary`.

### Change

- Replace verbose inline `scheduler/ticker/aggTrade/scan/coverage` text with a compact line:

```text
5.0s · 99.4% · аномалии 104 · активно 2/6 · позиции 1/2 · PNL 4.60% · ticker ok · flow ok
```

- Track `live_events.csv` rows in `LiveArtifactWriter` and expose the count as `events_written` for the operator heartbeat.
- Append only short exception suffixes when something requires attention, e.g. `WS: ticker нет`, `WS: flow подключается`, `WS: flow подписка`, legacy `обход ~...s`, or cancelled orphan orders.
- Do not remove detailed per-cycle WebSocket diagnostics from `live_cycle_summary`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low. This is operator UI and event counting only. Trading logic, signal filters, order path, fill/stop handling, and PnL accounting are unchanged.

## P188 - Prevent hidden WS-live missed-entry paths

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The uploaded ZIP can still miss actionable live entries without an obvious operator-level failure: freshly promoted ticker-radar symbols require subminute aggTrade history from the setup bucket, max-position rejects are not consumed but also are not re-scanned until the next candle, and position monitor threads classify every unexpected exception as a temporary handling error.

### Change

- Default `live_ws_aggtrade_max_backfill_ms` / `--live-ws-aggtrade-max-backfill-ms` to `300000` so radar-promoted symbols can repair the initial bounded WS coverage gap instead of always waiting for the buffer to accumulate history.
- Keep the backfill explicit in existing artifacts: `ws_aggtrade_frame_read` still records missing ranges, `backfill_max_ms`, `backfill_skipped`, `backfill_ranges`, and row counts.
- Runtime ticker radar now emits `ticker_radar_snapshot.status` and raises `ExchangeConnectivityError` if all snapshots are unusable while subminute inactive discovery depends on ticker radar.
- `reject_max_positions` now re-enables scanning of the same unconsumed decision candle until it is stale/free-slot executable, and writes `signal_scan_retry_enabled`.
- Position monitor threads now treat only `ExchangeConnectivityError` as temporary network degradation; unexpected exceptions become `position_monitor_internal_error` + `position_integrity_error` instead of being hidden as temporary noise.

### Validation

```bash
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
# smoke: radar-promoted fresh symbol should show bounded backfill instead of persistent coverage_pending when missing_total_ms <= 300000
# smoke: max_open_positions reject should emit signal_scan_retry_enabled and retry before signal stales
# smoke: injected monitor ValueError should emit position_monitor_internal_error and position_integrity_error, not position_monitor_error
```

### Risk

Medium. Default live may use bounded REST aggTrade repair for hot radar symbols, increasing API cost. This is intentional and visible; set `--live-ws-aggtrade-max-backfill-ms 0` for strict WS-only diagnostics.

## P189 - Add explicit REST ticker-radar degraded source for WS DNS failures

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

A real Windows live start stopped before the first cycle because the Binance futures WebSocket host could not be resolved:
`ClientConnectorDNSError: Cannot connect to host fstream.binance.com`. Strict startup refusal was honest, but operationally too brittle because ticker-radar discovery already has a normalized REST ticker snapshot source that can be used without restoring hidden OHLCV/aggTrade full-universe scans.

### Change

- Keep WS `!ticker@arr` as primary ticker-radar source when `live_ws_ticker_enabled=true`.
- If the primary WS ticker source fails at startup or during a ticker-radar cycle, try `RestLiveTickerSnapshotSource` as an explicit degraded ticker-radar source.
- Emit `ticker_radar_primary_source_failed`, `ticker_radar_source_degraded`, and, if needed, `ticker_radar_fallback_source_failed` events with source ids, exception details, and snapshot ok/missing counts.
- Refuse startup / pause the live loop if both WS and REST ticker-radar sources fail, or if the chosen source returns all snapshots missing.
- Do not restore hidden OHLCV/aggTrade full-universe scans; fallback is ticker snapshots only and remains visible in `ticker_radar_snapshot.source_status=degraded_rest_fallback`.

### Validation

```bash
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
```

Expected live smoke with broken WS DNS but working REST API: startup continues, `ticker_radar_source_degraded` is written, `ticker_radar_startup_ready.source=rest_fetch_tickers`, and `live_cycle_summary.ticker_radar_status=degraded_rest_fallback`. If REST/DNS is also broken, startup still fails instead of scanning with fake data.

### Risk

Medium. REST ticker polling is slower and less reactive than WS ticker pushes, so discovery latency can increase during degraded mode. This is preferable to pretending WS is healthy or silently restoring full inactive subminute scans.

## P190 - proposed - widen default WS aggTrade backfill budget for 5m/30s live

Status: PROPOSED. Commit: UNKNOWN.

Context:
- After P189, a broken Binance WS ticker can be replaced by explicit degraded REST ticker radar, but the default WS aggTrade backfill budget stayed at 300000 ms.
- For the default 5m/30s pair, the last valid scan of the previous 5m forming setup can request slightly more than 300000 ms of aggTrade history while the signal is still fresh.
- With WS aggTrade unavailable or cold, that path can emit `coverage_pending` and miss the executable window even though REST aggTrade could have repaired a bounded gap.

Changes:
- Raise the default `live_ws_aggtrade_max_backfill_ms` / `--live-ws-aggtrade-max-backfill-ms` to 360000.
- Keep the behavior explicit through `ws_aggtrade_frame_read.backfill_max_ms`, `backfill_ranges`, `backfill_skipped`, and `signal_entry_ws_aggtrade_pending`.
- Show degraded ticker discovery in the operator status line as `WS: ticker REST` instead of only relying on CSV artifacts.

Risk:
Medium: degraded WS runs may perform more REST aggTrade work on radar-promoted symbols. This is bounded and visible, but cycle latency must be watched through `signal_scan_seconds` and `aggtrade_network_calls`.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Live smoke expectation: with WS ticker DNS broken but REST available, startup continues in degraded REST mode; fresh 5m/30s radar-promoted scans should backfill bounded gaps up to 360000 ms instead of `coverage_pending` at the end of the setup window.


## P191 - proposed - make aggTrade WS degraded execution visible in live status

Status: PROPOSED. Commit: UNKNOWN.

Context:
- A live run stayed for several minutes on the operator line `WS: flow подключается` while ticker discovery had already been moved to explicit degraded REST mode.
- That string conflates three materially different states: WS is still warming up, WS is unavailable but bounded REST aggTrade backfill is covering requested flow windows, or WS/REST coverage is pending and entries can be missed.

Changes:
- Add per-cycle counters for WS aggTrade REST backfill reads, backfilled rows, not-connected backfill reads, and coverage-pending reads.
- Add `live_cycle_summary.ws_aggtrade_effective_source` with explicit values such as `rest_backfill_degraded`, `uncovered_ws_pending`, `ws_with_rest_gap_backfill`, or `ws_*_no_entry_read`.
- Replace the long-running generic status suffix `WS: flow подключается` with more diagnostic operator states: `WS: flow REST`, `WS: flow pending`, `WS: flow нет`, or `WS: flow gap REST`.

Risk:
Low. Trading logic and order execution are unchanged. The patch only makes the existing degraded aggTrade path and coverage holes visible at cycle/operator level.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Live smoke expectation: if aggTrade WS cannot connect but bounded REST aggTrade reads are covering entry windows, the console should show `WS: flow REST` and `live_cycle_summary.ws_aggtrade_effective_source=rest_backfill_degraded`; if coverage still exceeds the backfill budget, it should show `WS: flow pending` and emit `signal_entry_ws_aggtrade_pending`.


## P192 - proposed - force aiohttp WS to use OS DNS resolver and show WS DNS label

Status: PROPOSED. Commit: UNKNOWN.

Context:
- A live run showed both Binance ticker and aggTrade WebSockets failing with `ClientConnectorDNSError: Could not contact DNS servers` while REST ticker fetches continued to work.
- That points to the aiohttp WebSocket DNS resolver path rather than a generic exchange/data outage.
- The operator line only showed `WS: ticker REST` / `WS: flow REST`, so the root transport cause was buried in `live_events.csv`.

Changes:
- Force aiohttp WebSocket sessions to use `aiohttp.ThreadedResolver()` via an explicit `TCPConnector`, matching the OS `getaddrinfo` path used by normal REST clients instead of any c-ares/async resolver path that can fail independently.
- Add short WS error labels (`dns`, `timeout`, `ssl`, `proxy`, `connect`) to `live_cycle_summary`.
- Append those labels to the inline operator status, for example `WS: ticker REST/dns` or `WS: flow REST/dns`.

Risk:
Low/medium. Trading logic is unchanged. The patch changes WebSocket transport resolver selection and diagnostics only. If the OS itself cannot resolve `fstream.binance.com`, the status will still show `/dns` and the environment must be fixed; no silent success is introduced.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Live smoke expectation: if the previous failure came from aiohttp's async DNS resolver, WS ticker/flow should connect and the `/dns` degraded suffix should disappear. If local DNS/network still cannot resolve `fstream.binance.com`, the console should explicitly show `WS: ticker REST/dns` or `WS: flow REST/dns` and artifacts should keep the full exception.


## P193 — route Binance futures WS market streams to /market (PROPOSED)

Status: PROPOSED. Commit: UNKNOWN.

Reason: after P192, live artifacts show DNS is no longer the blocker: WS transport reaches `connected`, but all-ticker stays `ws_ticker_not_ready;status=connected;last_message_at_ms=` and ticker discovery degrades to REST. Binance USD-M futures docs moved regular market streams such as `!ticker@arr` and `<symbol>@aggTrade` to the routed `/market` WebSocket endpoint; legacy unrouted `/ws` and `/stream` paths can connect but stop pushing market streams after the migration deadline.

Changes:
- Change all-market ticker URL from `wss://fstream.binance.com/ws/!ticker@arr` to `wss://fstream.binance.com/market/ws/!ticker@arr`.
- Change dynamic aggTrade combined connection from `wss://fstream.binance.com/stream` to `wss://fstream.binance.com/market/stream`.
- Keep ThreadedResolver and explicit REST degraded fallback; no silent fallback is added.

Validation to run after apply:
- `python -m compileall data/exchanges research_tools cli constants.py main.py`
- Small live smoke: expect `ticker_radar_startup_ready.source=binance_ws_all_ticker`, no repeated `ticker_radar_source_degraded`, and `ws_aggtrade_effective_source=ws` after subscription warm-up.


## P194 — seed WS ticker startup cache and show anomaly count in operator line (PROPOSED)

Status: PROPOSED. Commit: UNKNOWN.

Reason: after P193, ticker WS is healthy but the all-ticker stream warms gradually, leaving the first live seconds with partial universe coverage. The console also showed `события`, which was only the audit-row count and could be mistaken for detected anomalies.

Changes:
- Add a one-shot REST ticker startup seed for the Binance WS ticker cache when subminute ticker-radar discovery is required.
- Mark seeded snapshots with `rest_startup_seed.*` source labels and `source_status=primary_seeded_rest` / `WS: ticker seed` until real WS updates replace them.
- Emit `ticker_radar_startup_seeded` or `ticker_radar_startup_seed_failed` in `live_events.csv`; failed seed does not fake success and live still relies on primary WS or explicit degraded fallback.
- Replace inline `события <audit rows>` with `аномалии <detected ticker-radar promotions>` and store `detected_anomalies_total` in `live_cycle_summary`.

Validation to run after apply:
- `python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py`
- `python -m compileall data/exchanges research_tools cli constants.py main.py`
- Small live smoke: expect `ticker_radar_startup_seeded.seeded_count` close to universe size, no startup burst of `ticker_radar_missing_fields`, and console heartbeat like `... · аномалии 0 · ...`; during the first seed-backed cycle `ticker_radar_snapshot.source_status=primary_seeded_rest`, then normal WS cycles should return to `primary` once ticker messages arrive.


## P196 - proposed - strict backtest/live parity for forming setup decisions

Status: PROPOSED. Commit: UNKNOWN.

Context:
- Uploaded ZIP review found that backtest and live use duplicated forming-setup paths.
- The highest-confidence mismatch was backtest-side: the first raw candidate inside an HTF setup consumed that setup/cooldown before later OI/mark/category/execution filters ran.
- Live can keep evaluating later closed LTF decision candles in the same HTF setup, so the backtest could miss valid later runner_oi_confirmed entries.

Changes:
- `research_tools/anomaly_strategy_backtest.py` now collects every valid LTF decision candidate inside a forming HTF setup instead of stopping after the first raw pump-flow candidate.
- `research_tools/anomaly_micro_live.py` now writes selected category OI-change and mark-basis status/value/age into `category_selected`, not only into reject rows.
- Candidate rows now include `setup_decision_index` and `candidate_collection_policy=all_ltf_decisions_per_setup` for auditability.
- `runner_balanced` now rejects stale derivatives context when it requires mark basis, matching live's fresh mark-basis fetch.
- `runner_oi_confirmed` now forces `require_oi_status_ok=True` in addition to OI-change and mark-basis thresholds.

Risk:
Medium. Candidate counts can rise materially, especially on 1m/5s. This is expected and more honest; execution overlap is still resolved in trade simulation. The next run must inspect candidate/signal/trade counts and runtime.

Validation:
```
python -m py_compile research_tools/anomaly_strategy_backtest.py research_tools/anomaly_micro_live.py
python -m compileall research_tools cli constants.py main.py
```


## P197 - proposed - broad backtest category overlay

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The only currently live-candidate category is `runner_oi_confirmed`.
- Backtest should still run broad discovery signals so potentially profitable categories are not hidden by a single strict production profile.
- Category statistics need to be separated by bucket instead of inferred from separate narrowed runs.

Changes:
- Add `pump_category_id`, `pump_category_rank`, `pump_category_matches`, and `pump_category_source` to signal/trade context columns.
- Add a backtest overlay classifier that tags broad signals with the strongest matching profile in this order: `runner_oi_confirmed`, `runner_flow`, `runner_reclaim`, `runner_balanced`, then `discovery`.
- Add `anomaly_profitability_by_category.csv` with closed-trade stats per category.

Risk:
Medium. Category overlay runs several profile filters over the same candidate table, so signal-filter time increases. This is deliberate and keeps the primary trade stream broad.

Validation:
```
python -m py_compile research_tools/anomaly_strategy_backtest.py
python -m compileall research_tools cli constants.py main.py
```


## P198 - proposed - cheap flow prescreen for broad forming-setup backtest

Status: PROPOSED. Commit: UNKNOWN.

Context:
- After P196, 30d all-symbol/all-TF backtest became very slow because forming HTF from LTF collection evaluates every closed LTF decision candle inside every setup.
- The expensive row builder was being called even for decision candles that would immediately fail the same raw/paced quote/trade flow thresholds.

Changes:
- Add rolling setup baseline medians and an in-loop cumulative LTF quote/trade prescreen in `_collect_symbol_pair_rows`.
- The prescreen uses the same baseline medians, elapsed fraction, raw ratio thresholds, and pace thresholds as `_build_pair_candidate_row`.
- Only decision candles that can pass the existing flow gate proceed to baseline slicing, aggregation, feature construction, future labels, and category overlay.

Risk:
Low/medium. The prescreen must remain mathematically identical to the row-builder flow gate. It should reduce work without changing accepted candidates.

Validation:
```
python -m py_compile research_tools/anomaly_strategy_backtest.py
python -m compileall research_tools cli constants.py main.py
```


## P199 - proposed - enable profitable live categories with TF priorities

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The 30d broad run showed profitable buckets beyond `runner_oi_confirmed`, especially `runner_flow`.
- Live should test these categories explicitly while preserving category attribution in Telegram and artifacts.

Changes:
- Add live-supported `runner_flow`, `runner_reclaim`, and `runner_balanced` categories alongside `runner_oi_confirmed`.
- Default live `--pump-categories` is now `runner_oi_confirmed,runner_flow,runner_reclaim,runner_balanced`.
- Add TF-specific category priority:
  - `5m/30s`: `runner_flow`, `runner_oi_confirmed`, `runner_reclaim`, `runner_balanced`
  - `1m/15s`: `runner_oi_confirmed`, `runner_flow`, `runner_reclaim`, `runner_balanced`
  - `1m/5s`: `runner_oi_confirmed`, `runner_flow`, `runner_balanced`, `runner_reclaim`
- Add flow-hold and reclaim wick checks to live category filtering.
- Emit `category_contract=live_category_overlay_v1_no_prior_fast_fade`, category id/label, OI/mark values, flow hold, and wick metrics in selected-category artifacts.

Risk:
Medium. Live category contract is explicit because live does not yet enforce the backtest 72h prior_fast_fade exclusion. This is not a fallback, but a known contract gap that must be measured in shadow/live parity.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

## P200 - proposed - enforce live prior_fast_fade with trailing cache lag ignored

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The expanded live categories inherit the backtest `max_prior_fast_fade_count_72h=0` exclusion.
- The first live implementation only added a cache-based calculator and still used a strict window ending at the live decision timestamp, which would reject most live candidates when the OHLCV cache lags by minutes or hours.
- Ignoring the whole filter would admit serial fast-fade symbols; requiring full tail coverage would make the filter practically unusable in live.

Changes:
- Replace strict cache-window loading for this filter with contiguous-from-start loading that allows only a trailing cache lag.
- Keep start-of-window and internal cache gaps blocking; these produce `reject_prior_fast_fade_filter_unavailable`.
- Actually apply `max_prior_fast_fade_count_72h` inside live category selection before mark/OI work.
- Emit `reject_prior_fast_fade_72h` when cached mature prior candidates contain too many fast fades.
- Add prior-fast-fade status/count/coverage/tail metadata to `category_selected` and category rejection payloads.
- Bump `category_contract` to `live_category_overlay_v3_prior_fast_fade_cache_tail_ignored`.

Risk:
Medium. The live filter can be slightly less strict than a perfectly up-to-date backtest during cache lag because the trailing unavailable interval is ignored. This is intentional and visible; it is not a silent fallback. Start coverage and internal gaps remain hard rejects.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py launcher.py
python main.py run-anomaly-live --help
```

## P201 - proposed - live WS healthy time percentage

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The inline live heartbeat showed the current WS issue label, but not how much wall-clock time the session was actually WS-healthy versus degraded by REST seed/fallback, flow backfill, pending coverage, or network waits.
- Operator needs a compact health percentage in the heartbeat line.

Changes:
- Track cumulative wall-clock seconds between health samples, split into WS-healthy and observed seconds.
- A cycle is counted healthy only when ticker radar is `primary` WS and, when flow WS has targets, aggTrade WS is connected, fully subscribed, has no coverage pending, and did not use REST backfill.
- Network-connectivity waits are sampled as unhealthy time.
- Add `соединение N%` to the live heartbeat.
- Add `ws_healthy`, `ws_health_reason`, `ws_health_pct`, `ws_health_observed_seconds`, and `ws_health_healthy_seconds` to `live_cycle_summary`.
- Preserve the last attempted ticker-radar health state across `not_due` cycles so the percentage does not read as 0% between normal ticker-radar intervals.
- Treat `ticker_radar_status=ok` from `binance_ws_all_ticker` as healthy primary WS, while REST `ok` remains non-WS health.
- Count expected startup bootstrap as healthy for the first 180 seconds: `ticker seed` and bounded startup `flow gap REST` do not force the health percentage to begin at 0%.
- Keep non-bootstrap REST fallback, flow pending, subscription mismatch, and network errors as unhealthy connection time.
- Count aggTrade subscription mismatch as unhealthy only when subscribed targets are fewer than requested targets; extra still-active old subscriptions are overhead, not a lost connection.
- Render heartbeat as `{seconds}s · {connection_pct}% · аномалии N · активно current/seen · позиции open/closed · PNL X% · {connection_status}` without the `live` prefix.
- Align heartbeat metric columns to compact width 9 with right-aligned values; connection status is a non-empty left-flowing suffix without fixed padding.
- Render healthy connection status as `ticker ok` / `ticker ok · flow ok`, and degraded status as `ticker seed`, `ticker REST`, `ticker нет`, `flow pending`, `flow REST`, `flow подписка`, or `flow gap REST`.
- Restore single-line heartbeat rendering with ANSI clear-line instead of padding-only carriage return, so PowerShell does not keep duplicate heartbeat rows.
- Highlight the heartbeat line when at least one position is open.
- Remove the `live:` prefix from console live-run messages.

Risk:
Low. Diagnostics/operator UX only; no signal, entry, stop, exit, or sizing logic changes.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

## P202 - proposed - reduce live cache flush hot-loop stalls

Status: PROPOSED. Commit: UNKNOWN.

Context:
- Longest recent live run `.output/results/live_anomaly_runs/20260514_104416` had 500 cycles and 0 positions.
- Cycle time was dominated by `signal_scan_seconds` (~1054s total) and `cache_flush_seconds` (~904s total).
- Non-forced cache flush was writing up to 20 symbol/timeframe parquet shards synchronously in the decision loop; p95 flush was ~13.2s and max was ~31.9s.

Changes:
- Lower default `live_ohlcv_cache_flush_max_symbol_timeframes` from 20 to 4 in config, CLI parser, and command fallback.
- Forced shutdown/error/max-cycle flush remains unlimited and persists all pending shards.

Risk:
Low for trading logic. Signal selection, categories, entry/exit, stops and cache correctness are unchanged. Runtime can carry a larger in-memory pending write queue if fetched rows arrive faster than the smaller flush budget can persist them.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

## P203 - proposed - harden live WS subscription ACK and position amount parsing

Status: PROPOSED. Commit: UNKNOWN.

Context:
- Live review found that Binance aggTrade subscriptions were treated as active immediately after sending `SUBSCRIBE`, before the exchange ACK arrived.
- That can make `wait_for_targets` and `read_rows` report subscribed/covered too early, especially during a subscription error or a just-promoted ticker-radar symbol.
- Position amount parsing also preferred CCXT `contracts`/`side` before Binance `info.positionAmt`; if `contracts` is missing/zero while `positionAmt` is present, live can misread an open position as flat.

Changes:
- Track pending aggTrade subscribe/unsubscribe request ids and move symbols into the subscribed coverage set only after the WebSocket ACK payload is received.
- Clear pending subscription state on reconnect and on subscription errors.
- Parse `info.positionAmt` before side-based return when normalized contracts are zero.

Risk:
Low/medium. Trading thresholds are unchanged. Freshly promoted symbols may use explicit REST backfill for a little longer until WS ACK arrives, but this is more honest than claiming unacknowledged WS coverage.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py data/exchanges/ccxt_futures_client.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

## P204 - proposed - coalesce and reuse live aggTrade REST gap backfills

Status: PROPOSED. Commit: UNKNOWN.

Context:
- WS aggTrade coverage is now mostly healthy, but every explicit gap/backfill can still become a separate REST `aggTrades` request.
- Cycle-local raw cache avoids only repeated reads inside one cycle; adjacent windows across later cycles or multiple small WS holes can still hit REST repeatedly.
- The goal is to reduce REST call count and latency without treating missing data as a zero-signal condition.

Changes:
- Add process-memory REST aggTrade raw range cache with 20 minute default TTL.
- Coalesce uncovered aggTrade ranges and apply 60s default padding within the requested scan window, so one REST fetch can satisfy nearby 5s/15s/30s consumers and later cycles.
- Route both WS missing-range backfill and non-WS subminute raw fetches through the same cache/planner.
- Add CLI knobs: `--live-aggtrade-rest-cache-ttl-ms` and `--live-aggtrade-rest-cache-padding-ms`.
- Add live_cycle_summary counters: `aggtrade_process_cache_hits`, `aggtrade_coalesced_missing_ranges`, and `aggtrade_rest_fetched_ms`.
- Add per-read diagnostics: cache missing range count and actual network backfill range count.

Risk:
Medium operationally. The patch should reduce repeated REST work, but padding can fetch extra rows for hot symbols. It does not relax WS coverage rules, signal filters, entry guards, stops, exits, or PnL accounting.

Validation:
```
.venv/Scripts/python.exe -m compileall data/exchanges research_tools cli constants.py main.py
.venv/Scripts/python.exe main.py run-anomaly-live --help
# smokes: process cache avoids second overlapping REST fetch; adjacent gaps coalesce into one padded request.
```

---

## P189 — Live aggTrade REST gap prefetch planner

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Reduce REST churn and improve live stability by coalescing due subminute S30/S15/S5 WS aggTrade gaps per precise symbol before signal evaluation.
```

Changes:

```text
- Adds a mandatory per-symbol subminute entry gap prefetch step for precise active/radar scans.
- Reads WS coverage for all due subminute entry ranges, merges missing ranges, and performs one bounded cached REST backfill pass before timeframe evaluation.
- Populates the WS aggTrade buffer from the coalesced backfill so later S30/S15/S5 frame reads do not re-open the same REST debt.
- Adds `aggtrade_rest_gap_prefetch` events and cycle summary counters for requested ranges, missing ranges, backfill ranges, fetched rows, and pending oversized gaps.
- Keeps oversized gaps as explicit coverage-pending; no stale signal fallback and no feature flag.
```

Verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout:

```text
live_events.csv shows aggtrade_rest_gap_prefetch for active/radar symbols with WS gaps; subsequent ws_aggtrade_frame_read events for the same symbol/time window should have fewer network backfill ranges. live_cycle_summary exposes aggtrade_gap_prefetch_* counters. Oversized missing ranges remain coverage_pending.
```

## 2026-05-14 - P190 proposed: idempotent live order placement and pre-stop exposure cleanup

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
data/exchanges/ccxt_futures_client.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Remove ambiguous state-changing REST retries from live order placement and close any verified post-entry exposure before raising pre-stop integrity errors.
```

Changes:

```text
- Live market and STOP_MARKET order placement now requires a deterministic client order id.
- create_order is sent once; only ambiguous transport/order-mutation failures are reconciled by client order id.
- Non-ambiguous exchange validation errors are not retried and are not relabeled as network errors.
- Entry, TP1, stop, and protective reduce-only exits all use explicit client order ids in live artifacts.
- Pre-stop entry integrity failures read the actual exchange position and either record no exposure or send a verified reduce-only close before raising.
- No feature flags, no silent fallback, no candle-price fill substitution, and no trading logic threshold changes.
```

Verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout:

```text
position_opened, position_stop_order_verified, tp1_partial_exit_filled, and unprotected_entry_reduce_only_exit_filled events include client_order_id. A synthetic ambiguous create_order transport failure should reconcile by client order id instead of submitting a duplicate order. A synthetic fill/position mismatch before initial stop should emit an unprotected_entry_* event before the live runner stops with LiveDataIntegrityError.
```

## 2026-05-14 - P192 proposed: live account preflight and close-only startup position cleanup

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
data/exchanges/ccxt_futures_client.py
data/exchanges/ccxt_types.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Refuse unsupported live account mode and start real-order live from a clean exchange-position state by closing any pre-existing position explicitly before the live loop.
```

Changes:

```text
- Adds Binance USD-M account-mode preflight before real-order live startup.
- Hedge mode is a hard startup error; the live runner requires one-way position mode.
- Reads exchange positions for the live universe before ticker startup and signal scanning.
- Any non-flat startup position is closed with a reduce-only market order using an explicit client order id.
- The close is verified by reading the post-close exchange position amount.
- After a verified startup close, orphan open orders for the symbol are cancelled.
- Startup blocks if a pre-existing position cannot be closed and verified.
- No restore path, no ledger-based adoption, no feature flag, no silent fallback.
```

Verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout:

```text
live_events.csv contains live_account_preflight_ok before ticker startup. If the account is in hedge mode, startup stops with live_account_preflight_failed. If a live-universe symbol has a pre-existing exchange position, startup emits startup_position_cleanup_started, startup_position_closed, startup_position_cleanup_finished, then starts only after the position is verified flat and orphan orders are cancelled.
```


## 2026-05-14 - P205 proposed: live/backtest category parity and 72h prior-fast-fade context

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make backtest category attribution match live TF priority while keeping discovery as backtest-only fallback, and stop live runner categories from being rejected just because 72h subminute entry cache is unavailable.
```

Changes:

```text
- Backtest category annotation now uses the same TF priority order as live: 5m/30s starts with runner_flow, 1m/15s starts with runner_oi_confirmed, and 1m/5s tries runner_balanced before runner_reclaim.
- Backtest still falls back to discovery when no live category profile matches, so artifacts show whether a trade came from a confirmed live category or backtest-only discovery.
- Live tradable categories remain unchanged; discovery is not added to live.
- Live prior-fast-fade 72h filter now computes historical context from the levels timeframe and can fetch/fill that bounded OHLCV window, instead of requiring 72h of subminute entry timeframe aggTrade cache.
- Category reject artifacts preserve the real reject reason in reason/category_reject_reason and move nested detail reason into coverage_reason/detail_reason, so prior-fast-fade coverage failures are no longer mislabeled.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
Backtest anomaly_trades.csv / anomaly_signals.csv should contain pump_category_source=backtest_live_priority_overlay_v1_discovery_fallback and pump_category_id showing runner_* or discovery. Live category_rejected rows should keep reason=reject_prior_fast_fade_filter_unavailable when coverage is unavailable and include coverage_reason for the underlying context issue. With normal 1m/5m cache coverage, prior_fast_fade_filter_status should be ok instead of failing because 5s/15s/30s cache does not span 72h.
```

Risk:

```text
Low/medium. Live category eligibility can increase where the only previous blocker was missing subminute 72h history. This is intentional because prior-fast-fade is historical symbol context, not a requirement for 72h of executable subminute entry cache. It does not make discovery tradable in live and does not change order/fill/stop logic.
```

## 2026-05-14 - P206 proposed: bounded shutdown reconcile and visible graceful stop

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make Ctrl+C shutdown observable immediately and avoid a forced full-universe open-order reconcile on exit.
```

Changes:

```text
- Ctrl+C now writes live_shutdown_started and logs the beginning of graceful shutdown before reconcile/flush/close work starts.
- Forced orphan-order reconciliation on Ctrl+C/max-cycles now checks only symbols that had exchange order activity in the current run, plus currently open/opening active symbols.
- Symbols are tracked after startup close orders, live entry fills, and emergency unprotected-entry reduce-only exits.
- Forced reconcile writes orphan_order_reconcile_started with the bounded scope size.
- A second Ctrl+C during cleanup writes live_shutdown_forced and exits with code 130 after closing live sources.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
On the first Ctrl+C, console immediately prints graceful shutdown with reconcile_symbols=N and live_events.csv contains live_shutdown_started. If no orders were placed in this run, forced orphan reconcile checks zero symbols instead of the whole universe. If one symbol had a live entry, forced reconcile checks that symbol only.
```

## 2026-05-14 - P207 proposed DANGER: live retryable dependencies and cold coverage

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Keep live category rejects honest while reducing false missed entries from temporary data dependencies and making limited cold coverage explicit.
```

Changes:

```text
- Live taker-buy share now matches the backtest contract: mean taker_buy_quote_volume / quote_volume is computed only over rows with finite taker value and quote_volume > 0. If no valid rows exist, the reject remains explicit and artifacts include valid/total row counts.
- Category dependency rejects for prior-fast-fade coverage, mark context, and OI context are treated as retryable when the blocking reason is data availability/staleness, not a real below-threshold value.
- Retryable dependency rejects no longer consume the decision candle or mark the timeframe as scanned; live will retry until the signal goes stale or reaches a final non-retryable reject/selection.
- Added signal_scan_retryable_dependency_blocked artifacts with retryable reasons and contract id.
- DANGER: subminute ticker-radar live now has a small default precise cold-coverage budget of 5 inactive symbols per cycle. This is deliberately labeled DANGER in constants, scheduler source, scan mode, live_cache_config, symbol_batch_selected, and live_cycle_summary.
- Setting inactive_scan_slots_per_cycle=0 explicitly disables cold coverage and returns to active/radar-only scanning.
- If max_precise_scan_symbols_per_cycle is configured, DANGER cold coverage is capped by remaining precise budget after active and ticker-radar symbols.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
live_events.csv should show category_contract=live_category_overlay_v5_retryable_dependencies_cold_coverage. Sparse entry segments with zero-volume synthetic buckets should no longer produce reject_invalid_taker_buy_share if at least one valid quote-volume row exists. Temporary mark/OI/prior-fast-fade unavailable rejects should emit signal_scan_retryable_dependency_blocked and the same decision timestamp can be retried until stale. symbol_batch_selected should show scheduler_source=DANGER_ws_event_driven_plus_precise_cold_coverage by default, inactive_scan_slots_source=DANGER_default_precise_cold_coverage_subminute, and cold symbols should have scan mode precise_DANGER_cold_coverage. Set inactive_scan_slots_per_cycle=0 to disable this DANGER mode.
```

Risk:

```text
High / DANGER. Cold coverage intentionally increases live workload and REST/WS aggTrade pressure, and can change which symbols reach precise subminute evaluation. This is for parity/audit discovery coverage, not a proven production improvement. Monitor signal_scan_seconds, aggtrade_network_calls, ws_aggtrade_coverage_pending_count, rate-limit/API errors, and scheduler health before using real orders for long runs.
```


## 2026-05-14 - P208 proposed DANGER: health-gated idle cold coverage

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Keep P207 cold coverage explicit, but prevent it from competing with active trading work or running during degraded WS health.
```

Changes:

```text
- DANGER cold coverage now requires cumulative WS health strictly above 95%.
- DANGER/explicit precise cold coverage is gated off when any active symbol, opening position, or open position exists.
- symbol_batch_selected and live_cycle_summary now record inactive_cold_coverage_gate_reason, health percentage at selection, threshold, and active/position blockers.
- The heartbeat no longer prints the ambiguous Russian "DANGER обход ~Ns" when cold coverage is gated off; it prints cold off <reason>, and only prints a cold full-cycle estimate when cold coverage actually selected inactive symbols.
- Setting inactive_scan_slots_per_cycle=0 still disables cold coverage completely.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
With WS health <=95% or active/open/opening positions present, symbol_batch_selected should show scheduler_source=ws_event_driven_scheduler_cold_coverage_gated, inactive_count=0, inactive_cold_coverage_gate_reason populated, and no precise_DANGER_cold_coverage symbols. Only after health >95% and no active/open/opening positions should scheduler_source become DANGER_ws_event_driven_plus_precise_cold_coverage with inactive cold symbols.
```

Risk:

```text
Medium / DANGER-limited. Cold coverage becomes less aggressive and may miss cold discoveries during early startup, degraded WS periods, or while a setup/position is active. This is intentional: cold coverage is for idle parity/audit discovery, not for competing with live trade management.
```


## 2026-05-14 - P209 proposed DANGER: adaptive cold coverage controller

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Keep cold coverage useful as an idle parity/audit scanner, but make it self-throttle before it steals latency or REST/cache budget from live/radar/active work.
```

Changes:

```text
- DANGER cold coverage is now adaptive, not a fixed 5-slot gate.
- Open/opening positions hard-disable cold coverage.
- Active symbols due for scan hard-disable cold coverage; active waiting symbols reduce the adaptive score instead of forcing an all-or-nothing gate.
- Adaptive score = WS-health factor * scheduler-heartbeat speed factor * active-waiting factor * REST/cache load factor.
- WS health ramps from 95.0% to 99.5%; heartbeat EWMA ramps from 8s slow to 2s fast; REST/cache pressure EWMA is driven by aggTrade network calls, REST fetched time span, and pending WS/cache gaps.
- Cold coverage runs only when score >= 0.30, and selected slots scale from 1 up to the configured/default hard cap.
- Artifacts now expose inactive_cold_coverage_adaptive_score plus health/speed/active/load factors, pressure EWMA, active due/waiting counts, base slots, and max slots.
- Heartbeat shows DANGER cold <slots> score <score> only when cold coverage actually selected symbols; otherwise it shows cold off <reason>.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
With open/opening positions: cold off open_or_opening_position_present.
With active due symbols: cold off active_due_symbols_present.
With slow heartbeat, low WS health, active waiting soft-cap, or high REST/cache pressure: cold off <specific reason> or fewer DANGER cold slots.
When idle, fast, healthy, and low-pressure: symbol_batch_selected shows precise_DANGER_cold_coverage symbols and inactive_cold_coverage_adaptive_score >= 0.30.
```

Risk:

```text
Medium / DANGER-controlled. The patch can increase cold universe coverage during very healthy idle periods, which can increase discovery and parity-audit coverage, but it is not proof of PnL edge. Monitor scheduler_cycle_seconds, signal_scan_seconds, aggtrade_network_calls, aggtrade_rest_fetched_ms, ws_aggtrade_coverage_pending_count, cold score and selected slots before real-order use.
```

## 2026-05-14 - P210 proposed DANGER: local guard, flow radar, wider micro-cache metrics

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Move more live discovery work into explicit DANGER observability without increasing exchange-position preflight latency or silently scanning the full universe.
```

Changes:

```text
- DANGER local entry-position guard now uses in-process open/opening symbol state before entry instead of fetching exchange position amount before every signal. Startup cleanup plus post-fill position verification remain required; post-fill mismatch closes only the new fill amount and raises integrity error.
- DANGER ticker flow radar uses existing all-ticker WS quote-volume and trade-count deltas to promote early flow-only watch symbols before the normal price-delta radar threshold, without REST calls.
- WS aggTrade rolling buffer default increases to 60 minutes, but subscriptions remain limited to active/ticker-radar/watch/current cold symbols; there is no full-universe micro-tape subscription.
- Cold coverage usefulness is now measurable in live_cycle_summary: cold scanned symbols, due/evaluated TFs, retryable dependencies, selected signals, order attempts, and totals.
- Heartbeat prints selected cold slots and adaptive score only when DANGER cold actually runs.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
Check live_cache_config for DANGER local guard, DANGER flow radar thresholds, and widened active/radar-only micro-cache policy. In ticker_radar_snapshot, danger_flow_radar_candidate_count/promoted_count should stay visible. In live_cycle_summary, cold_* counters show whether cold coverage actually produces candidates/signals/orders or only burns latency.
```

Risk:

```text
High / DANGER-labeled. Flow radar can increase false positive watch symbols; widened WS buffers increase memory; local pre-entry guard assumes startup exchange-position cleanup and single live process. Do not run multiple live processes. Monitor post-fill position mismatch, scheduler_cycle_seconds, ws target counts, memory, and cold counters before judging edge.
```

## P211 — PROPOSED — DANGER runner/fader pre-pump context study
- Adds `research_tools/runner_fader_prepump_context.py` offline experiment.
- Labels closed backtest trades as runner/fader/mixed and computes only pre-anomaly HTF context on 30m/1h/2h/6h windows.
- Writes feature separation artifacts to test whether runners/faders are distinguishable before pump start.
- Commit: UNKNOWN.

## P212 — PROPOSED — DANGER backtest prepump runner/fader artifacts by default
- Supersedes standalone-only P211 by wiring the runner/fader pre-pump context study into `run_anomaly_strategy_backtest` after `anomaly_trades.csv` is written.
- Backtest now writes `runner_fader_prepump_*.csv` by default in the same artifact directory, using `anomaly_timestamp_ms` as the exclusive feature anchor and 30m/1h/2h/6h HTF context windows.
- Adds `runner_fader_prepump_run_status.csv` so missing cache/read failures are visible without deleting the primary backtest artifacts.
- Adds CLI knobs: `--write-prepump-context`, `--prepump-context-timeframe`, `--prepump-context-windows`, `--prepump-context-min-coverage-ratio`.
- Commit: UNKNOWN.

## 2026-05-14 - P213 applied locally: warm-watch scheduler gate

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Stop sending every cheap radar hit directly into expensive precise scan. Keep cheap all-symbol visibility, hold suspicious symbols in a bounded warm-watch layer, widen micro-cache only for those symbols, and scan precisely only when the next radar observation still shows rising flow without fade/chase.
```

Changes:

```text
- Adds warm-watch config/CLI: enabled flag, TTL, min observations, and price-delta window.
- Ticker/flow radar candidates now become warm-watch rows first; precise radar watch is created only after repeated qualifying observations.
- Warm-watch rejects explicit fade/chase/not-rising cases and writes warm_watch_rejected artifacts.
- WS aggTrade subscription targets include active + warm-watch + precise-radar + current batch symbols, not the full universe.
- symbol_batch_selected and live_cycle_summary expose warm-watch waiting/marked/promoted/rejected counts.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Expected smoke readout:

```text
live_cache_config shows warm_watch_* settings. ticker_radar_snapshot shows warm_watch_marked_count first, then warm_watch_promoted_count only after continuing qualifying radar observations. symbol_batch_selected shows warm_watch_waiting_count while those symbols are cached but not precise-scanned. ticker_radar_promoted appears after warm_watch_precise_promoted, not on the first cheap radar hit.
```

Risk:

```text
Medium. This deliberately adds one radar-observation delay before precise scan, so it can miss one-shot pumps; the tradeoff is lower precise-scan latency pressure and better pre-pump visibility. It does not change entry/category logic.
```
## 2026-05-14 - P214 applied locally: rolling symbol context snapshot

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Move expensive prior-fast-fade/HTF context work out of the precise scan path. Live keeps a cache-only rolling symbol_context_snapshot.csv and category checks consume the prepared in-memory snapshot.
```

Changes:

```text
- Adds symbol context snapshot config/CLI: enabled flag, interval, symbols per cycle, and freshness SLA.
- Adds symbol_context_snapshot.csv artifact with per-symbol/per-timeframe baseline medians, cache coverage, available prior spike timestamps, and prior fast-fade timestamps.
- The rolling updater reads local parquet cache only; it does not fill missing context via REST during snapshot refresh.
- _live_prior_fast_fade_72h now reads the prepared snapshot and filters event timestamps relative to the current decision timestamp, avoiding lookahead.
- Missing/stale/unavailable snapshots are explicit retryable category dependencies instead of hidden synchronous fallback fetches.
- live_cycle_summary and live_events expose snapshot update status, counts, and output file.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep symbol-context
```

Expected smoke readout:

```text
live_cache_config shows symbol_context_snapshot_policy=cache_only_rolling_table_no_precise_scan_context_fetch. live_cycle_summary shows symbol_context_snapshot_status/updated/failed counts. symbol_context_snapshot.csv exists even before any trades. Category rejects caused by missing context show symbol_context_snapshot_missing/stale or context=<cache reason>, and precise scan no longer calls the old 72h context fetch path.
```

Risk:

```text
Medium. Early live cycles can reject runner categories as retryable until snapshots are populated and fresh. This is intentional latency protection, not a trading-filter change. If cache is stale or absent, the artifact will show unavailable snapshots instead of silently fetching context inside precise scan.
```


## 2026-05-14 - P215 applied locally: missed-pump visibility artifact

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make top-growth pumps auditable against live scheduler visibility instead of manually guessing whether radar, warm-watch, precise scan, category, or execution guard missed them.
```

Changes:

```text
- Adds missed_pump_visibility.csv plus per-hour missed_pump_visibility_YYYYMMDD_HH0000_UTC.csv in top_growth artifacts.
- Adds optional --visibility-events-csv for standalone top-growth runs to join top movers against a live run's live_events.csv.
- Visibility rows expose radar_promoted, flow_radar_promoted, warm_watch, precise_scanned, category_rejected, execution_rejected, position_opened, first timestamps, and not_scanned_reason.
- top_growth_index.csv now records the per-hour visibility file.
- Missing/invalid live_events input is explicit in visibility_source_status/reason; no synthetic visibility is inferred.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-top-growth --help | grep visibility
```

Expected smoke readout:

```text
run-anomaly-top-growth writes top_growth/missed_pump_visibility.csv and a per-period missed_pump_visibility_*.csv. With --visibility-events-csv pointing to a live run, top movers get first radar/warm/scan/reject timestamps and a not_scanned_reason that points to radar, warm-watch, precise scan, category, execution, or missing visibility input.
```

Risk:

```text
Low/medium. This is artifact-only and does not change signal selection or orders. The first version depends on live_events.csv event coverage; if precise/category paths do not emit enough events for a symbol, the artifact will show untracked/no-actionable rather than pretending to know.
```


## 2026-05-14 - P216 applied locally: latency SLA optional scan controller

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Protect active/radar reaction latency before expanding visibility with optional warm precise promotions or cold coverage.
```

Changes:

```text
- Adds latency_sla_controller config/CLI with active+radar due-scan p95 threshold and min sample count.
- Computes due-scan latency as now minus the close time of the latest unscanned entry candle, not candle start time.
- Keeps active and already-promoted radar precise scans eligible even when SLA is breached.
- Gates optional precise cold coverage to zero when active/radar due-scan p95 breaches SLA.
- Defers warm-watch-to-precise promotion while SLA is breached and emits warm_watch_precise_deferred_latency_sla instead of silently dropping the candidate.
- Exposes latency SLA status, p95/max/sample count, threshold, reason, and warm_watch_deferred_count in live events and cycle summaries.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep latency-sla
```

Expected smoke readout:

```text
live_cache_config shows latency_sla_policy and threshold. symbol_batch_selected/live_cycle_summary show latency_sla_status. Under backlog, optional cold coverage has gate_reason=latency_sla_due_scan_p95_above_threshold and warm candidates emit warm_watch_precise_deferred_latency_sla instead of becoming ticker_radar_watch.
```

Risk:

```text
Medium. This can reduce discovery breadth during latency spikes by design. It should not suppress active/radar reaction scans or alter entry/category/order logic. If the SLA is set too low for the chosen TF/host, warm precise promotion and cold coverage may stay mostly off; artifacts will show the reason.
```

## 2026-05-14 - P217 applied locally: prepump runner/fader warm-watch scoring

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/runner_fader_prepump_context.py
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Use P212 runner/fader pre-pump context only as a warm-watch priority scorer after explicit 30d validation, not as an entry filter or trade veto.
```

Changes:

```text
- Adds public compute_spot_prepump_window_features() helper so live and P212 share the same spot/flow pre-pump feature names.
- Extends symbol_context_snapshot.csv to v2 with cache-only prepump spot/flow feature JSON for configured windows.
- Adds optional --prepump-warm-watch-scoring-enabled and profile CSV controls.
- Loads runner_fader_prepump_feature_separation.csv only when explicitly enabled; invalid/missing/unstable profiles fail startup instead of falling back.
- Applies the profile only as a bounded additive warm-watch/radar priority score adjustment.
- Emits prepump_warm_watch_* score/status fields in warm-watch and ticker-radar artifacts.
- Does not change category selection, execution guards, entries, exits, stops, or order logic.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep prepump
```

Expected smoke readout:

```text
With scoring disabled, live behavior is unchanged except for new dormant config fields. With scoring enabled and a valid 30d feature separation CSV, live_cache_config shows profile status/feature count, symbol_context_snapshot.csv includes prepump_spot_features_json, and warm_watch/ticker_radar events include prepump_warm_watch_scoring_status plus score adjustment. No entry/category reject reason should come from prepump scoring.
```

Risk:

```text
Medium. This can change which warm-watch candidates get precise scan first, so it affects discovery priority. It intentionally cannot block a trade directly. The current live feature subset is cache-only spot/flow features; OI/derivatives prepump features remain offline-only until a typed rolling context source is added.
```

## 2026-05-14 - P218 proposed: budget optional context and warm micro-cache

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make P213-P217 operationally safe under load: active/radar reaction latency must win over optional context upkeep and warm micro-cache breadth.
```

Changes:

```text
- Moves symbol_context_snapshot maintenance after ticker, batch selection, WS subscription, precise scan, order open, reconcile, and cache flush.
- Gates symbol_context_snapshot updates by the same active/radar latency SLA used for optional scans.
- Adds a per-cycle wall-clock budget for cache-only context snapshot work.
- Advances the context snapshot cursor only for processed symbols, so budgeted cycles do not silently skip unprocessed universe slices.
- Uses an effective snapshot freshness window based on universe size, symbols_per_cycle, and update interval, while keeping the configured value as the minimum.
- Caps warm-watch aggTrade WS targets by score/recency without capping active symbols, opening symbols, current batch, or already-promoted radar watches.
- Emits explicit context budget/freshness and warm-watch target/drop counts in live artifacts.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E 'warm-watch-aggtrade|symbol-context-snapshot-max'
```

Expected smoke readout:

```text
live_cycle_summary should show symbol_context_snapshot_seconds after the critical scan path and symbol_context_snapshot_cycle_budget_seconds. Under active/radar backlog, symbol_context_snapshot_skipped should appear with skipped_latency_sla. ws_aggtrade_subscription_target should include warm_watch_aggtrade_target_cap, target_count, and dropped_count.
```

Risk:

```text
Low/medium. Optional context snapshots may refresh more slowly under load by design, but the artifact now states this directly. Active/radar scans and order logic are unchanged. Warm-watch micro-cache breadth is bounded, so a very noisy radar can delay flow detail for lower-score warm symbols.
```

## 2026-05-15 - P222 proposed: live synthetic bucket diagnostics and terminal ledger provenance

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Keep the existing closed-candle entry aggregation behavior, but make synthetic zero-bucket repair visible in artifacts and preserve scan/guard provenance on terminal ledger rows.
```

Changes:

```text
- Adds synthetic missing OHLCV bucket counting before `_fill_missing_ohlcv_buckets()` fills entry segments.
- Emits `entry_segment_synthetic_ohlcv_buckets` when a live forming-setup scan uses one or more synthetic zero buckets.
- Adds synthetic bucket count to missed-entry replay probe diagnostics when a prior missed signal is found.
- Writes `source_scan_mode`, `danger_cold_coverage_source`, and `entry_position_guard_source` to closed/exit-unresolved live ledger rows, not only open rows.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Expected smoke readout:

```text
If an entry segment has missing closed buckets, live_events.csv contains `entry_segment_synthetic_ohlcv_buckets` with synthetic_bucket_count and entry_segment_bucket_count. Closed or exit-unresolved live_positions.csv rows keep the same source_scan_mode / guard provenance as the open row.
```

Risk:

```text
Low. This is diagnostic/audit only. It does not change signal thresholds, order logic, position guard behavior, fills, stops, or exits.
```

## 2026-05-15 - P231 proposed: clear wrapped live heartbeat rows

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Fix duplicated/concatenated live heartbeat output in narrow terminals where the inline status line wraps across multiple physical rows.
```

Changes:

```text
- Tracks how many terminal rows the last inline live status occupied.
- Clears every occupied row before drawing the next heartbeat instead of clearing only the current row.
- Keeps normal log messages ordered by finishing the open status line before printing them.
- Does not change live scan, delayed replay, order, Telegram, or artifact logic.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Console-output-only change. It fixes terminal rendering when status messages exceed the terminal width; trading and audit data paths are unchanged.
```


## 2026-05-15 - P223 applied locally: idle-only delayed replay audit for live anomalies

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Capture every live category-level anomaly decision for delayed postmortem without stealing critical resources from live execution.
```

Changes:

```text
- Adds --delayed-replay-enabled and budget/idle/delay flags to run-anomaly-live.
- Captures category_selected and category_rejected live anomaly decisions into delayed_replay/delayed_replay_queue.jsonl.
- Processes queued cases only after the critical live cycle path and only when active symbols, opening symbols, and open positions are all zero for the configured idle window.
- Uses cache/process-memory data only for delayed outcome checks; it does not fetch missing data, write orders, or mutate live state.
- Writes delayed_replay_results.csv, delayed_replay_summary.csv, and live_status.json with the explicit contract delayed_replay_v1_live_event_frozen_decision_cache_only_idle.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
With --delayed-replay-enabled true, category_selected/category_rejected events enqueue delayed replay cases. While live has active/opening/open positions, delayed_replay_summary.csv says live_not_idle or idle_window_too_short. Once idle and the delay has elapsed, delayed_replay_results.csv records audit-only frozen-decision rows using cache-only outcome data or an explicit cache_only_* insufficiency status.
```

Risk:

```text
Low/medium. Trade logic and order execution are unchanged. The replay is intentionally cache-only and artifact-based, so it may report insufficient replay data instead of forcing REST/network work. It is not yet a full backtest recomputation; recompute_status says this explicitly to avoid fake would-enter claims.
```

## 2026-05-15 - P224 applied locally: frozen-decision delayed replay recompute and Telegram alert

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make delayed replay answer whether the same frozen decision candle would produce an entry under the live/backtest-style signal builder, while keeping replay cache-only, idle-only, and visible to the operator when live did not open.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v2_frozen_decision_recompute_cache_only_idle_tg.
- Adds --delayed-replay-telegram-enabled true/false to run-anomaly-live.
- Recomputes each queued case from cached closed OHLCV windows up to the original decision timestamp, without using future candles and without fetching missing exchange context.
- Captures execution guard rejects such as stale/drift/TP1-reached/RR-collapsed/max-positions as replay cases, not only category selected/rejected cases.
- Writes recompute_status, would_select_signal, would_enter_under_frozen_decision, recomputed prices/category, reject reasons, data status, mismatch_type, and telegram_notified to delayed_replay_results.csv.
- Sends an events-channel Telegram message when replay recomputes an entry for a case where live did not open/select the entry.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py launcher.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
With --delayed-replay-enabled true, replay rows no longer say not_recomputed_artifact_frozen_decision_snapshot. They show recomputed_signal/recomputed_no_signal or an explicit cache-only insufficiency status. If a live rejected/execution-rejected case recomputes to an entry, delayed_replay_results.csv has mismatch_type live_rejected_replay_would_enter or live_execution_rejected_replay_would_enter and a Telegram events message says Replay found the entry.
```

Risk:

```text
Medium. This adds more replay computation, but only after the existing idle gate and within existing per-cycle budgets. It deliberately disables synchronous mark/OI exchange-context fetches during replay, so categories requiring unavailable derivatives context may produce explicit replay_exchange_context_fetch_disabled rejects instead of optimistic entries.
```

## 2026-05-15 - P225 applied locally: delayed replay frozen signal snapshot fallback

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from silently missing execution-rejected runner signals when cache-only recomputation cannot refetch mark/OI exchange context.
```

Changes:

```text
- Adds a typed parser for the frozen `LiveSignal.to_json()` snapshot already stored in delayed replay cases.
- Adds `recompute_source` to delayed replay results.
- If cache-only recomputation reaches `replay_exchange_context_fetch_disabled` but the original live signal snapshot is present, records the signal as `frozen_live_signal_snapshot_exchange_context_unavailable` instead of `recomputed_no_signal`.
- Keeps the no-network rule: replay still does not fetch mark/OI context and the result source explicitly says when a frozen live signal snapshot was used.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
For execution-rejected cases with a stored signal_json, delayed_replay_results.csv can now show recompute_status=frozen_live_signal_snapshot_exchange_context_unavailable and recompute_source=frozen_live_signal_snapshot instead of suppressing the case as replay_no_signal when mark/OI fetch is intentionally disabled. TG notification still fires only for live non-selected/non-opened cases where would_enter_under_frozen_decision=true.
```

Risk:

```text
Low/medium. This is audit-only and does not change live trading. It uses a frozen live signal snapshot only when exact cache-only recomputation is blocked by intentionally disabled exchange-context fetch, so the row remains honest about source.
```



## 2026-05-15 - P226 proposed: label delayed replay evidence source

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from overstating frozen live-signal snapshot fallback as a strict backtest-like recomputation, and make post-decision outcome data explicit.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v3_evidence_labeled_cache_only_idle_tg.
- Adds strict_recompute_signal and frozen_signal_snapshot_used result columns so recomputed candle evidence and live snapshot evidence are separated.
- Splits mismatch labels: frozen snapshot cases now use *_frozen_signal_snapshot instead of *_replay_would_enter.
- Adds decision_data_end_timestamp_ms and outcome_is_post_decision to make the no-lookahead boundary visible in artifacts.
- Changes Telegram wording for snapshot fallback from "Replay found entry" to "Replay raised frozen signal" and includes the recompute source/caveat.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
Strict cache-only candle recompute entries have strict_recompute_signal=true and mismatch_type *_replay_would_enter. Frozen snapshot fallback entries have frozen_signal_snapshot_used=true, strict_recompute_signal=false, and mismatch_type *_frozen_signal_snapshot; Telegram does not call them strict replay-found entries.
```

Risk:

```text
Low. Audit/artifact/Telegram wording only; live trading logic, order placement, fills, stops, and scan thresholds are unchanged.
```

## 2026-05-15 - P227 proposed: delayed replay final-decision queue hardening

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from treating intermediate category-profile rejects as ignored live entries, while keeping audit coverage for final no-signal decisions and important execution rejects.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v4_final_decision_evidence_labeled_cache_only_idle_tg.
- Stops enqueueing a delayed replay case from each intermediate category_rejected event.
- Enqueues one final all_categories_rejected case only when the category loop ends without a selected signal.
- Adds delayed replay capture for reject_symbol_position_already_active, reject_stop_cooldown, reject_signal_not_closed_yet, and reject_stale_signal.
- Splits result semantics into strict_replay_would_enter, snapshot_signal_available, and operator_alert_kind; would_enter_under_frozen_decision is true only for strict cache-only candle recompute.
- Keeps frozen live-signal snapshot alerts labeled separately as frozen_signal_snapshot_only instead of strict replay-found entries.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low/medium. Audit-only; no order placement, live trading thresholds, fills, stops, or scanner selection are changed. The main behavior change is fewer false-positive replay queue cases from intermediate category-profile rejects.
```


## 2026-05-15 - P228 proposed: delayed replay result source column

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make delayed replay evidence source visible in the results CSV instead of computing it and then dropping it through csv extrasaction=ignore.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v5_result_source_visible_cache_only_idle_tg.
- Adds recompute_source to DELAYED_REPLAY_RESULTS_COLUMNS so strict cache-only recompute and frozen live-signal snapshot rows remain distinguishable in delayed_replay_results.csv.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Artifact schema/contract only; no live scan, order, fill, stop, or replay decision logic changes.
```

## 2026-05-15 - P229 proposed: immutable delayed replay decision snapshot

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make delayed replay recompute from the exact live decision inputs captured at signal/reject time when available, instead of relying first on later cache state.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v6_immutable_decision_snapshot_idle_tg.
- Captures an immutable live decision snapshot for delayed replay cases: baseline rows, setup row, entry rows through decision_timestamp_ms, and frozen mark/OI/prior-fast-fade context.
- Stores the snapshot on queued delayed replay cases and remembers it in memory so later execution rejects for the same decision reuse the same snapshot.
- Recomputes delayed replay from the immutable snapshot first; cache-only reconstruction remains a fallback when no snapshot exists.
- Allows replay recompute to use frozen live mark/OI/prior-fast-fade context without REST/network fetch.
- Adds decision_snapshot_status and decision_snapshot_source to delayed_replay_results.csv.
- Removes the duplicate recompute_source column introduced by the previous schema patch.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
python - <<'PY'
from research_tools.anomaly_micro_live import DELAYED_REPLAY_RESULTS_COLUMNS
from collections import Counter
assert not [k for k, v in Counter(DELAYED_REPLAY_RESULTS_COLUMNS).items() if v > 1]
PY
```

Risk:

```text
Medium-low. Audit-only and delayed-replay-only; no live order/fill/stop logic changes. Queue rows become larger because they carry compact candle snapshots, but only when delayed replay is explicitly enabled.
```


## 2026-05-15 - P226 proposed: label delayed replay evidence source

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from overstating frozen live-signal snapshot fallback as a strict backtest-like recomputation, and make post-decision outcome data explicit.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v3_evidence_labeled_cache_only_idle_tg.
- Adds strict_recompute_signal and frozen_signal_snapshot_used result columns so recomputed candle evidence and live snapshot evidence are separated.
- Splits mismatch labels: frozen snapshot cases now use *_frozen_signal_snapshot instead of *_replay_would_enter.
- Adds decision_data_end_timestamp_ms and outcome_is_post_decision to make the no-lookahead boundary visible in artifacts.
- Changes Telegram wording for snapshot fallback from "Replay found entry" to "Replay raised frozen signal" and includes the recompute source/caveat.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
Strict cache-only candle recompute entries have strict_recompute_signal=true and mismatch_type *_replay_would_enter. Frozen snapshot fallback entries have frozen_signal_snapshot_used=true, strict_recompute_signal=false, and mismatch_type *_frozen_signal_snapshot; Telegram does not call them strict replay-found entries.
```

Risk:

```text
Low. Audit/artifact/Telegram wording only; live trading logic, order placement, fills, stops, and scan thresholds are unchanged.
```

## 2026-05-15 - P230 proposed: delayed replay Telegram follows enablement

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Remove the separate delayed-replay Telegram switch. Delayed replay is an operator-audit mode: when it is enabled and finds a meaningful mismatch, the Telegram alert path should be part of that mode instead of controlled by a second flag.
```

Changes:

```text
- Removes --delayed-replay-telegram-enabled from run-anomaly-live.
- Removes delayed_replay_telegram_enabled from MicroLiveConfig and CLI config construction.
- Records delayed_replay_telegram_policy=enabled_with_delayed_replay in live config artifacts.
- Sends delayed replay operator alerts whenever delayed replay is enabled and operator_alert_kind is produced.
- Artifact writing remains independent from Telegram delivery; queue/results/summary are still written even if Telegram send fails.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
python main.py run-anomaly-live --help | grep delayed-replay-telegram && exit 1 || true
```

Risk:

```text
Low. Audit/TG-control contract only. Live trading, order placement, fills, stops, scan thresholds and delayed replay decision logic are unchanged.
```


## 2026-05-15 - P233 proposed: fixed-width live status block

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Replace the long inline live heartbeat with a fixed-width operator status block that keeps replay backlog visible without wrapping into duplicated-looking terminal lines.
```

Changes:

```text
- Renders live heartbeat as a five-line fixed-width block: LIVE, FEED, PUMP, RPLY, RISK.
- Uses four-character main labels and three-character field labels.
- Adds a blank line before the block for visual separation.
- Keeps delayed replay backlog visible as RPLY/pnd, with off when replay is disabled.
- Updates inline status clearing to count explicit newline rows as well as terminal wrapping.
- Leaves trading, delayed replay decisions, Telegram and artifact logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Console rendering only; no live execution or strategy behavior changes.
```


## 2026-05-15 - P234 proposed: Russian grouped live status block

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Replace the four-letter technical live status block with a Russian operator block grouped by Connection, Market, Trading and Control semantics while keeping delayed replay backlog visible.
```

Changes:

```text
- Renders the live heartbeat as sections: Соединение, Рынок, Торговля, Контроль.
- Uses fixed-width space-padded cells with title and value on the same line.
- Shows live runtime as Время instead of only per-cycle seconds.
- Renames Replay to Повтор and cold coverage to Покрытие.
- Keeps delayed replay backlog visible as Повтор <pending>/<total> or Повтор Выкл.
- Leaves trading, scan, delayed replay decision, Telegram and artifact logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Console rendering only; no live execution or strategy behavior changes.
```


## 2026-05-15 - P235 proposed: regroup Russian live status metrics

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Move active-symbol load into the Market block and move PNL into the Trading block so the Russian live heartbeat grouping matches operator semantics.
```

Changes:

```text
- Market now renders: Время, События, Активные.
- Trading now renders: PNL, Позиции, Ордера.
- Leaves heartbeat clearing, delayed replay, Telegram, artifacts and trading logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. Console rendering only; no live execution or strategy behavior changes.
```

## 2026-05-15 - P236 proposed: live network error visibility and Telegram retry

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
data/exchanges/ccxt_futures_client.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make live network/API failures visible as real operator alerts instead of letting retry warnings stick to the inline status block, and keep Telegram alert attempts alive while network degradation persists.
```

Changes:

```text
- Adds a typed retry logger boundary to CcxtFuturesClient so live can route retry warnings through the live status logger without touching private client internals.
- Adds highlighted live console alerts with a leading blank line for exchange retry exhaustion and network/API degraded state.
- Replaces one-shot async Telegram network-degraded notification with periodic enqueue retry while degraded.
- Records `network_degraded_telegram_alert_enqueued` artifacts for operator-audit visibility.
- Sends a Telegram recovery note with the original degradation reason when connectivity returns.
- Leaves trading, signal selection, order placement and position management logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'PY'
from research_tools.anomaly_micro_live import _LiveRetryLogger, _LiveStatusLogger
rows = []
status = _LiveStatusLogger(rows.append)
retry = _LiveRetryLogger(status)
retry.warning('Источник не ответил на «%s». Попытка %s/%s.', 'x', 3, 3)
status.alert('⚠️ сеть/API недоступны, жду восстановления.\nПричина: test')
assert rows[0].startswith('\n⚠️ Источник не ответил')
assert rows[1].startswith('\n⚠️ сеть/API недоступны')
PY
```

Risk:

```text
Low-to-medium. Console/artifact/Telegram alert path only; no trading decisions change. During a network outage the code retries Telegram enqueue every 60 seconds, but TelegramDispatcher cooldown still prevents repeated successful sends with the same key.
```

## P245 - proposed - live candidate queue pressure controller

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Stop old/weak radar and warm-watch candidates from keeping latency SLA permanently breached. The live loop should keep the strongest fresh candidates, expire stale backlog, and record explicit pressure/drop events instead of deferring the same tail forever.
```

Changes:

```text
- Adds an internal candidate queue pressure controller with no CLI flags.
- Under latency pressure or oversized radar/warm queues, keeps the strongest candidates and trims stale/weak tail.
- Emits candidate_dropped_latency_pressure and candidate_expired_backlog_stale events with source, score, flow fields, and latency SLA payload.
- Adds candidate_queue_* counters to symbol_batch_selected and live_cycle_summary.
- Treats candidate drop/expiry events as visibility evidence for closed-hour missed-pump audit.
- Does not loosen stale/drift/RR/actual-risk/order safety and does not add REST work to the scan path.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "candidate-queue|queue-pressure|live-top-growth|symbol-context-startup"  # expected: no output
```

Risk:

```text
Medium. This intentionally discards low-priority backlog under pressure, which should reduce false SLA breaches but can drop a weak candidate before precise scan. The event trail is explicit, and top-growth/missed-pump visibility can audit whether dropped candidates later became top movers.
```

## P246 - proposed - adaptive precise scan budget

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent live from spending the same precise-scan capacity during normal and overloaded states. Under backlog, active symbols keep priority and radar precise scans are reduced to the freshest/highest-score tail instead of expanding stale due-scan latency.
```

Changes:

```text
- Adds an internal adaptive precise-scan budget with no new CLI flags.
- Active/opening symbols are never dropped by the adaptive cap.
- Under latency SLA breach, radar precise scans are limited to the top 1 candidate for the cycle.
- Under queue pressure or active-symbol pressure, radar precise scans are limited to the top 2 candidates for the cycle.
- Under runtime/cache pressure, radar precise scans are limited to the top 3 candidates for the cycle.
- Adds adaptive_precise_budget_* fields to symbol_batch_selected and live_cycle_summary.
- Keeps execution safety, discovery filters, order/fill/stop logic and REST fetch behavior unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "adaptive-precise|precise-budget"  # expected: no output
```

Risk:

```text
Medium. This intentionally scans fewer radar candidates per cycle during pressure. The tradeoff is deliberate: fresher top candidates are preferable to scanning a wider stale queue. New artifact fields make the cap auditable.
```

## 2026-05-15 - P237 proposed: live session top-growth status from ticker snapshots

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Show the operator current session top movers in the live heartbeat without spending extra REST/OHLCV budget or faking closed-hour top-growth artifacts.
```

Changes:

```text
- Tracks session top growth from the already-required ticker snapshots only.
- Uses the first usable ticker price seen inside the current UTC session as the baseline; if live starts mid-session, the baseline is explicitly the first available live price, not a reconstructed/fallback session open.
- Adds `top_growth/session_top_growth.csv` with one-minute evidence snapshots, including status/reason rows when no usable top exists.
- Extends the human heartbeat with a final session block such as `Азия` / `ALCH 74%    MEW 48%    HBAR 42%`.
- Leaves closed-hour `top_growth_index.csv` and `missed_pump_visibility.csv` semantics unchanged; those still require the standalone `run-anomaly-top-growth` command against closed 1h candles.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP; include it locally if your checkout has it.
```

Risk:

```text
Low-to-medium. Operator UI and artifacts only. The numbers are session-live ticker growth, not closed-hour OHLCV top-growth, and are labeled by source/status instead of being used as trading signals.
```

## 2026-05-15 - P238 proposed: align live session top-growth columns

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Keep the live heartbeat session-top row visually aligned with the three fixed-width operator status columns.
```

Changes:

```text
- Formats the live session top-growth row as three fixed-width cells instead of joining symbols with ad-hoc spacing.
- Pads missing top cells without inventing extra symbols or fallback growth values.
- Leaves session-top source semantics unchanged: ticker-snapshot baseline only, no OHLCV/REST fallback.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP; include it locally if your checkout has it.
```

Risk:

```text
Low. Operator UI formatting only; no trading logic, artifacts, or filters change.
```


## 2026-05-15 - P241 proposed: prioritize hot symbols in rolling context snapshot

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the budgeted rolling symbol-context snapshot spend its limited time on symbols that can actually affect near-term live decisions, especially symbols blocked by retryable prior-fast-fade context dependency.
```

Changes:

```text
- Adds an in-memory TTL queue for symbol-context priority requests.
- Marks symbols blocked by retryable category dependencies as priority context symbols.
- Rolling context snapshot priority order is now open positions / active symbols, retryable dependency requests, ticker radar, warm watch, then round-robin universe coverage.
- Adds `priority_reason_counts` to `symbol_context_snapshot_updated` events for auditability.
- Keeps context snapshot cache-only during live cycles; no synchronous REST/precise fetch is introduced in the hot path.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP; include it locally if your checkout has it.
```

Risk:

```text
Low-to-medium. This changes context maintenance priority, not entry thresholds. Round-robin coverage still advances only for non-priority processed symbols, so hot-symbol retries should not starve context cursor accounting.
```

## 2026-05-15 - P242 proposed: incremental live closed-hour top-growth visibility

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make live runs populate closed-hour top_growth_index.csv and missed_pump_visibility artifacts without ticker-derived approximations and without blocking the hot scan path with a full-universe one-shot REST sweep.
```

Changes:

```text
- Adds default-on incremental live top-growth audit for closed 1h exchange candles.
- Processes only a bounded number of symbols per live cycle and writes the same top/status/visibility files as the standalone top-growth command when the hour scan completes.
- Uses live_events.csv from the same run as the visibility source.
- Keeps session-top heartbeat separate from closed-hour top-growth research artifacts; no ticker fallback is used for closed-hour audit.
- Adds CLI controls for enabling/disabling live top-growth audit, threshold, limit, per-cycle symbol budget, max cycle budget, and fetch spacing.
- Adds live_cycle_summary fields for the current top-growth audit status/progress.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "live-top-growth"
```

Risk:

```text
Medium. This adds exchange OHLCV reads during live, but bounded incrementally by symbols_per_cycle/max_cycle_seconds and separated from trading decisions. It does not change entry filters or order logic.
```

## 2026-05-15 - P243 proposed: make live top-growth always-on and latency-gated

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove operator-facing live top-growth toggles/knobs and make the closed-hour audit run only under the existing live latency gate, with fixed conservative quotas when the live loop is not clearly idle.
```

Changes:

```text
- Removes the new `--live-top-growth-*` CLI flags and command plumbing from P242.
- Keeps live closed-hour top-growth audit default-on with internal constants instead of runtime operator switches.
- Skips top-growth processing when latency SLA blocks optional work.
- Uses the normal quota only when latency SLA is OK; otherwise uses a conservative quota for low-evidence/insufficient-sample states.
- Keeps closed-hour audit source strict: exchange 1h candles only, no ticker/session fallback.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "live-top-growth"  # expected: no output
```

Risk:

```text
Low-to-medium. This preserves P242 artifact visibility but prevents the audit from competing with delayed signal scans during latency pressure. It also removes new operator knobs, so quota changes now require code review instead of ad-hoc CLI changes.
```

## P244 - proposed - discovery live loosen + retryable data dependencies

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Reduce false live non-entries without weakening execution safety: loosen discovery-oriented market filters modestly, keep entry price drift unchanged, and classify unavailable taker-buy/mark/OI context as retryable data dependency instead of final market/category rejection.
```

Changes:

```text
- Live discovery defaults: start flow 5x -> 4x, min retention 70% -> 65%, verticality 0.25 -> 0.20, prior whipsaw cap 0.60 -> 0.75.
- Runner categories: prior_fast_fade cap 0 -> 1, taker-buy delta cap 0.25 -> 0.35, mark/OI confirmation thresholds slightly loosened.
- max_entry_price_drift_pct stays 0.003; late-entry safety is not loosened.
- Missing/invalid taker-buy context, unavailable mark basis, and unavailable OI now become retryable dependency rows with emit=false for category_rejected.
- Real computed mark/OI below threshold remain final market/category rejects.
- Removes P239 operator-facing startup/gap-tolerance flags; startup context backfill is always-on policy, tiny-gap tolerance remains code-reviewed constants.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "symbol-context-startup|symbol-context-snapshot-min|symbol-context-snapshot-max-gap|live-top-growth"  # expected: no output
```

Risk:

```text
Medium. This intentionally increases discovery sensitivity and may create more selected candidates. It does not loosen stale/drift/RR/actual-risk/order safety. New live validation must compare category_selected, signal_scan_retryable_dependency_blocked, execution rejects, and closed-hour missed-pump visibility before any further parameter changes.
```

## P252 - proposed - fix live cycle selection accounting and timestamp startup backfill status

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the runtime NameError caused by live_cycle_summary referencing the local symbol-batch selection outside its scope, and make the startup 72h context backfill status line show the current local clock time on every symbol update.
```

Changes:

```text
- Stores candidate-queue pressure fields from the latest symbol batch selection on runner state, like existing latency/adaptive budget fields.
- Reads those runner-state fields in live_cycle_summary instead of the out-of-scope selection variable.
- Adds HH:MM:SS to the inline 72h context backfill status line.
- Adds no CLI flags and no trading-logic changes.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "candidate-queue|queue-pressure|startup-backfill-time"  # expected: no output
```

Risk:

```text
Low. This fixes event-summary accounting and operator display only. Candidate selection, precise-scan budget, dependency cooldown, backfill, cache writes, and execution guards are unchanged.
```

## 2026-05-16 — P253 proposed

Startup after the 72h context backfill had silent stages before the first live heartbeat: forced cache flush, startup snapshot computation/writing, ticker radar seed/validation, and entering the first live cycle. P253 adds inline status updates with the current clock time for these stages so the operator can see where startup is spending time. It changes display/status only; backfill, cache, ticker source policy, selection, and trading logic are unchanged.

Current commit: UNKNOWN.
Next validation: restart live and confirm startup progresses through `контекст 72ч · запись кеша`, `контекст 72ч · снимок N/total`, `контекст 72ч · запись snapshot`, `тикеры · стартовый снимок`, `тикеры · проверка радара`, then the normal live heartbeat appears after the first cycle summary.

## 2026-05-16 — P254 proposed

Live heartbeat operator UI now shows the connection group as `Время / Пульс / Данные`. `Пульс` is the last scheduler-cycle duration. `Данные` is a compact health label derived from real data-path evidence: ticker REST fallback, aggTrade REST gap backfill, subscription/coverage wait, cache REST fill, or remaining cache gaps. No trading logic, retry policy, or execution guard is changed.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "heartbeat|data-status|пульс"  # expected: no output
```

Risk: low. Display and live_cycle_summary diagnostics only. The new status label must not be used as a trading condition.

## 2026-05-16 — P255 proposed

Startup 72h context preparation now ends with an explicit readiness verdict before the first live cycle. The verdict counts fully ready symbols, partial symbols, unavailable symbols, ready snapshots, tolerated-gap snapshots, missing snapshots, and top unavailable reasons. Real-orders live is refused when ready symbol/snapshot ratios are below internal reviewed thresholds, rather than starting with a mostly blind prior-fast-fade context. No CLI flags are added, and retryable dependency remains only a residual safety path.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "startup-readiness|readiness-threshold|context-ready"  # expected: no output
```

Risk: medium-low. The patch can intentionally stop real-orders startup when context coverage is poor. Trading filters and execution guards are unchanged.

## 2026-05-16 — P256 proposed

Startup 72h context preparation progress is made fully observable for the heavy post-backfill stages. The forced cache flush now reports per symbol/timeframe progress with local clock time and ETA instead of one silent `запись кеша` line. Symbol-context snapshot computation also reports ETA per symbol, and final snapshot/readiness lines finish with explicit ETA status. This is operator-visibility only: cache write policy, context readiness thresholds, trading filters, and execution guards are unchanged.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "startup-cache-flush-eta|context-snapshot-eta"  # expected: no output
```

Risk: low. Display and progress callback only. The flush still writes through the same storage path; the callback must not be used as trading state.

## 2026-05-16 — P257 proposed

Startup/reprepare context preparation no longer accumulates the whole 72h OHLCV write buffer and then performs one heavy final cache flush. During the 72h backfill loop it now flushes cache writes in bounded symbol/timeframe chunks, with the same inline `HH:MM:SS + ETA` progress format as the rest of preparation. The final flush remains as a safety drain for any remaining buffered rows.

Live context reprepare is state-based, not scheduled. It only starts when symbol-context readiness is degraded or stale and the runner is safe: zero active symbols, zero open positions, zero opening symbols, and zero tracked order-reconcile symbols. If unsafe, it writes `live_context_reprepare_deferred`; if safe, it writes explicit `live_context_reprepare_started/completed` events and terminal progress. If real-orders context is still below readiness thresholds after a safe reprepare, live stops safely with a data-integrity error instead of continuing with blind context.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "reprepare|переподготов|context-reprepare|startup-flush-chunk"  # expected: no output
```

Risk: medium. Cache writes now happen in smaller chunks during startup/reprepare, reducing the final silent flush, but this changes write timing. It does not change trading filters, signal construction, order/fill/stop logic, or CLI flags.

## 2026-05-16 — P258 proposed

Mandatory 72h context preparation now sends concise Telegram events when the mandatory 72h preparation/reprepare starts and when the readiness verdict is known. The messages follow the existing events-channel style and do not change preparation policy, readiness thresholds, cache writes, trading filters, or execution guards.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "context-preparation-telegram"  # expected: no output
```

Risk: low. Notification-only patch. Telegram failures remain non-blocking and are already recorded through the dispatcher error path.


## P247 - proposed - dependency retry cooldown

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent retryable data-dependency blocks from immediately re-entering precise scan every cycle. A missing context/taker/mark/OI dependency should wait briefly for the dependency updater or expire by the existing stale guard, not create a self-sustaining backlog.
```

Changes:

```text
- Adds an internal retry cooldown for retryable dependency blocks, with no new CLI flags.
- Schedules retryable dependency cases for a short cooldown bounded by the entry timeframe and stale timeout.
- Skips active cooldowns in due-scan latency calculations so intentional dependency waits do not breach latency SLA.
- Prioritizes cooldown symbols in rolling symbol_context_snapshot refresh.
- Emits signal_scan_dependency_retry_scheduled and candidate_expired_dependency_timeout artifacts.
- Adds dependency_retry_cooldown_* counters to live_cycle_summary and signal_symbol_scan_summary.
- Keeps pass-by-default forbidden: dependency unavailable still blocks entry until it becomes checkable or the signal expires.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "dependency-retry|retry-cooldown|dependency-cooldown"  # expected: no output
```

Risk:

```text
Medium-low. Retryable dependency candidates are intentionally not rescanned every cycle, which reduces load but can delay a newly ready dependency by up to the internal cooldown. Stale timeout still prevents late entries, and artifacts show scheduled retries plus dependency timeouts.
```


## P248 - proposed - entry-below-stop diagnostics

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make invalid initial-risk live rejects diagnosable without changing stop logic. When the computed long stop is at/above entry, artifacts must show whether the active stop came from EMA20 or the structural low-based stop instead of emitting a generic invalid_initial_risk row.
```

Changes:

```text
- Replaces the generic live event reject_invalid_initial_risk with reject_entry_below_initial_stop.
- Adds risk_side, stop_source, previous_stop, decision_ema20, and stop_above_entry_pct to event details.
- Keeps the same safety behavior: the setup is rejected; no alternate stop is substituted.
- Adds both old and new event names to precise-scan visibility so older/live mixed artifacts remain readable.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "entry-below-stop|initial-risk-diagnostics"  # expected: no output
```

Risk:

```text
Low. Artifact naming/details change only; trade selection, stop calculation, and execution guards are unchanged.
```

## P249 - proposed - dependency cooldown scan-summary accounting

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the P247 accounting path where dependency-retry cooldown skips could be counted as generic skipped_not_due in per-symbol scan summaries. Cooldown waits must stay visible as dependency cooldown, not as ordinary not-due scheduling noise.
```

Changes:

```text
- When a timeframe scan is skipped because a dependency retry cooldown is active, signal_symbol_scan_summary increments dependency_retry_cooldown_skipped_count instead of skipped_not_due_count.
- Keeps the cooldown gating behavior unchanged; this is artifact accounting only.
- Adds no CLI flags and no trading-logic changes.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "candidate-queue|queue-pressure|adaptive-precise|precise-budget|dependency-retry|retry-cooldown|dependency-cooldown|entry-below-stop|initial-risk-diagnostics"  # expected: no output
```

Risk:

```text
Low. The patch changes only per-symbol summary classification for an already-skipped cooldown scan. It does not change candidate selection, dependency retry timing, stale guards, or execution logic.
```

## P250 - proposed - startup context backfill ETA status

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the default-on 72h startup context backfill observable while it is running. The operator status line should refresh for every symbol with ETA instead of appearing stuck on the first progress message, and the status must not show misleading ok/error counters.
```

Changes:

```text
- Replaces the every-50-symbol startup backfill logger line with an inline status update for every symbol.
- Shows current symbol and ETA based on completed-symbol throughput.
- Removes ok/error counters from the operator status line; detailed fetch failures remain in live_events.csv and completion artifacts.
- Adds no CLI flags and no trading-logic changes.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "startup-backfill-eta|context-backfill-progress"  # expected: no output
```

Risk:

```text
Low. Console/status output only. Backfill coverage, fetch behavior, failure events, cache flushing, snapshot computation, and execution guards are unchanged.
```

## 2026-05-16 — P259 proposed

P259 adjusts only the operator heartbeat display. The connection block now shows stability, pulse, and data health; runtime moved back to the market block and is counted from live-loop start after mandatory 72h context preparation, not from process startup. Session top movers are rendered to 0.1%. The live status logger now clears the previous multi-line heartbeat before warnings/ordinary log messages, so an error does not leave a stale grid above the alert.

Current commit: UNKNOWN.
Next validation: run live until the first warning/retry message and verify the old heartbeat is cleared before the alert, then the next heartbeat renders once.

## 2026-05-16 — P261 applied — c0b5dfd970da — stop verification exchange lookup and critical halt TG

Files:

```text
data/exchanges/ccxt_types.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make initial/replacement stop protection verification strict against Binance/CCXT eventual consistency: a created stop is not trusted from the create response alone, and live halt messages must explicitly show the affected symbol.
```

Changes:

```text
- Keeps the 5 x 0.5s stop visibility verification loop.
- Matches open orders by exchange order id and clientOrderId.
- If open orders do not show the stop, verifies through the typed exchange boundary `fetch_order_by_client_order_id`.
- Rejects terminal stop statuses and still validates side/type/reduceOnly/amount/stopPrice.
- Adds `symbol` to stop integrity exceptions, terminal log, live_events.csv and synchronous Telegram halt message.
- Adds the client-order lookup method to the CCXT protocol contract.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: fetch_open_orders empty for first 1-4 checks then stop visible -> position_stop_order_visibility_delayed
# synthetic smoke: fetch_open_orders empty but fetch_order_by_client_order_id returns NEW/STOP_MARKET/reduceOnly -> position_stop_order_client_lookup_confirmed
# synthetic smoke: stop unresolved after 5 checks -> live_data_integrity_error with symbol and Telegram Live остановлен message
```

Risk:

```text
Medium-low. Touches live stop verification and halt reporting only. It does not change signal selection, entry guards, order sizing, TP/SL math or PnL. A stop that cannot be confirmed by open orders or clientOrderId lookup still halts live unless P262 danger continue mode is explicitly enabled.
```

## 2026-05-16 — P262 proposed — danger continue after order/position errors

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Allow long data-collection live runs to continue after order/position integrity failures only when the operator explicitly enables a dangerous diagnostic mode.
```

Changes:

```text
- Adds `--danger-continue-after-order-position-errors`, default false.
- Keeps strict halt behavior by default.
- Classifies order/position flow failures with `LiveOrderPositionIntegrityError`.
- In danger mode, writes `live_order_position_integrity_error`, sends synchronous Telegram, flushes live cache when inside the loop, and continues startup/live execution.
- Records the selected policy in the live config artifact event.
- Applies the same danger policy to startup exchange-position cleanup failures.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# strict smoke: without flag, injected stop verification failure returns code 3.
# danger smoke: with flag, injected stop/order/position failure writes live_order_position_integrity_error, sends TG, and continues to the next cycle.
```

Risk:

```text
High when enabled. The flag intentionally keeps live running after order/position integrity failures, including startup exchange-position cleanup failures, so it is for data collection / diagnostics, not normal protected trading. Default behavior remains strict.
```

## 2026-05-17 — P273 proposed — restore live helper dataclasses after category extraction

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
```

Intent:

```text
Fix `NameError: name 'WsAggTradeReadResult' is not defined` in live by restoring non-category helper dataclasses used by WS aggTrade, OI, mark-basis, and raw aggTrade cache paths. These containers are separate from the shared pump category contract.
```

Validation:

```bash
| P264 | Share live/backtest category contract and parity labels | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_category_contract.py`, `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | parity/data-quality | Move runner category thresholds/priority into one shared contract used by live and backtest; keep discovery as explicit backtest fallback; add live-vs-discovery category family artifacts; stop materialized-cache metadata gaps from crashing candidate collection; preserve original forming setup source in delayed replay; add selected terminal outcome events; mark synthetic OHLCV buckets explicitly. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P266 | Add backtest context parity artifacts and skipped category metadata | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_strategy_backtest.py`, `research/*` | parity/diagnostics | Preserve pump category metadata on every skipped trade row, including execution-guard rejects; widen the derivatives pre-context universe with live-priority category intents before final mark/OI checks; add `anomaly_context_parity_report.csv` so context request/coverage/final-signal/trade outcome can be audited without mixing discovery with live-priority rules. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |

| P265 | Track discrete signal missed after live execution guard | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live observability | When a selected signal would have been executable at its discrete signal snapshot price but is rejected at current live price, write `discrete_signal_snapshot_entry_missed` and format the existing order-blocked Telegram as a missed discrete entry instead of a generic reject. Does not allow stale/discrete execution. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P267 | Preserve replay synthetic provenance and OI cache freshness | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_continuation_lab.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | data-quality/parity | Keep `synthetic_ohlcv_bucket` through delayed replay snapshots; reject live/replay signals when real entry buckets are fewer than confirmation candles; preserve existing synthetic flags when filling gaps; add OI cache/load/asof freshness fields to backtest context parity artifacts; align research status for P264-P266 as applied locally in the uploaded ZIP. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |

| P268 | Add real Binance order lifecycle smoke command | APPLIED locally / UNKNOWN commit | `research_tools/live_order_smoke.py`, `data/exchanges/ccxt_futures_client.py`, `domain/exceptions.py`, `cli/parser.py`, `cli/commands.py`, `research/*` | live safety/exchange-boundary | Add an explicit real-order smoke command that opens one minimal Binance USD-M market long, verifies reduce-only STOP_MARKET visibility through open orders and clientOrderId lookup, then cancels the stop and closes reduce-only by default; classify Binance `-2013 Order does not exist` as `ExchangeOrderNotFound` instead of connectivity retry exhaustion. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `python main.py run-live-order-smoke --help` |

## 2026-05-16 — P263 proposed — live order lifecycle unit tests

Files:

```text
tests/test_live_order_lifecycle.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add deterministic unit coverage for the live real-order lifecycle without changing trading logic: entry order, exchange position delta, initial stop verification, unprotected exposure cleanup, stop exit handling, and TP1 -> BE stop replacement.
```

Changes:

```text
- Adds a fake exchange that exercises the same `_maybe_open_position()` and `_monitor_position()` live paths used by real orders.
- Covers successful entry with verified initial STOP_MARKET reduce-only order and ledger/event artifacts.
- Covers the observed live failure class: entry filled, initial stop never visible, then reduce-only cleanup and `LiveOrderPositionIntegrityError`.
- Covers monitor stop-exit finalization with verified stop fill.
- Covers TP1 partial reduce-only fill, stop replacement to break-even, old-stop cancel, then verified stop close.
```

Validation:

```bash
python -m unittest tests.test_live_order_lifecycle -v
python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Risk:

```text
Low. Test-only patch. It does not change live trading logic, exchange client behavior, signal selection, sizing, TP/SL math, Telegram formatting, or artifacts outside test execution.
```
| P269 | Use Binance conditional algo stop-order boundary | APPLIED locally / UNKNOWN commit | `data/exchanges/ccxt_futures_client.py`, `data/exchanges/ccxt_types.py`, `research_tools/anomaly_micro_live.py`, `research_tools/live_order_smoke.py`, `research/*` | live safety/exchange-boundary | Create, verify, cancel, reconcile, and smoke-test protective stops through Binance USD-M conditional/algo endpoints (`algoOrder`/`openAlgoOrders`) instead of ordinary order APIs; keep ordinary order lookup only as a legacy secondary path; final smoke checks include conditional/algo open orders. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `python main.py run-live-order-smoke --help` |

| P270 | Exercise real position management smoke | APPLIED locally / UNKNOWN commit | `research_tools/live_order_smoke.py`, `cli/parser.py`, `cli/commands.py`, `research/*` | live safety/exchange-boundary | Extend the real Binance smoke with an optional management lifecycle: create the initial algo stop, create and verify a closer replacement stop, cancel the old stop after replacement, close the position reduce-only while the replacement stop is still open, cancel remaining smoke orders, and assert final flat/no ordinary or algo orders. Existing quick smoke behavior is unchanged unless `--replacement-stop-distance-pct` is provided. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py`; `python -m unittest tests.test_live_order_lifecycle -v`; `python main.py run-live-order-smoke --help` |
| P271 | Tolerate exchange-normalized stop trigger precision | APPLIED locally / UNKNOWN commit | `research_tools/live_order_smoke.py`, `research_tools/anomaly_micro_live.py`, `research/*` | live safety/exchange-boundary | Treat Binance algo `triggerPrice`/`stopPrice` as exchange-normalized to tick/price precision when verifying protective stops, so a valid rounded price such as `0.035643999999999995 -> 0.03564` does not fail stop verification. Still rejects missing, non-finite, or more-than-one-displayed-price-unit mismatches. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py`; `python -m unittest tests.test_live_order_lifecycle -v`; `python main.py run-live-order-smoke --help` |

| P272 | Preserve initial and active stop ids in smoke summary | PROPOSED | `research_tools/live_order_smoke.py`, `research/*` | live safety/artifacts | Keep the initial protective stop id in `stop_order_id` after a replacement, add `active_stop_order_id` for the currently managed stop, and keep `replacement_stop_order_id` separate so `live_order_smoke_summary.json` no longer overwrites the initial stop with the replacement id. Trading logic and exchange calls are unchanged. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py`; `python -m unittest tests.test_live_order_lifecycle -v`; `python main.py run-live-order-smoke --help` |

## 2026-05-17 — P274 proposed — exchange-side TP1 limit and LTF position monitor

Files:

```text
research_tools/anomaly_micro_live.py
data/exchanges/ccxt_futures_client.py
domain/abstract/exchange_client.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make live position management exchange-side for TP1 and LTF-consistent for structural trailing.
On entry, create and verify a reduce-only TP1 limit sell for half the position after the verified stop exists.
The monitor no longer triggers TP1 from candle high; it only reconciles the TP1 order fill and moves the remaining stop to BE after confirmed TP1 fill.
Structural trail now reads the signal entry timeframe, including 30s/15s/5s aggTrade-derived frames, instead of hardcoded 1m.
Before the first closed post-fill LTF candle, empty OHLCV is recorded as position_monitor_waiting_first_candle, not an integrity error.
Telegram open message is shortened to signal price, signed entry drift, TP/SL with matching decimals, and compact TF/category/session context.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium/high until real-order smoke confirms Binance accepts and exposes the new reduce-only limit TP order through ordinary open orders and that TP fill reconciliation moves the stop to BE without leaving orphan TP/stop orders.
```

## 2026-05-17 — P275 proposed — pin live heartbeat grid as terminal tail

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the terminal live heartbeat lifecycle. The multi-section grid is now a single cached status block: status updates replace the previous grid in place, ordinary logs and red alerts first erase the grid, print the event/error, then repaint the latest grid so the heartbeat is always the last terminal element. The heartbeat no longer starts with a leading blank line, which keeps row counting and clearing aligned for the full multi-section block.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. Console-output lifecycle only. It does not change trading logic, Telegram, exchange orders, artifacts, or signal selection. In non-interactive output, status blocks remain plain periodic logs because in-place terminal repaint is not available.
```

## 2026-05-17 - P276 applied locally - TP1-failure stop cleanup and lifecycle parity tests

Files:

```text
research_tools/anomaly_micro_live.py
tests/test_live_order_lifecycle.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the live order-flow failure path introduced by exchange-side TP1 limits. If entry and initial stop are verified but TP1 limit creation/verification fails, live now closes the exchange exposure reduce-only and then cancels the already-created initial stop instead of leaving an orphan conditional stop after cleanup.
Update the fake-exchange lifecycle tests to match the current live contract: entry requires verified stop + verified TP1 limit; monitor reconciles TP1 from exchange fill, not candle high; stop/TP1 cleanup is asserted.
```

Validation:

```bash
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Risk:

```text
Low/medium. Patch touches only the TP1-order failure cleanup path after a verified initial stop. Normal signal selection, entry guards, stop math, TP1 math, position sizing, and successful position management are unchanged. The new behavior reduces orphan-order risk after a failed TP1 limit setup.
```

## 2026-05-17 - P277 applied locally - conservative TP1 backtest and pre-context mark-basis fix

Files:

```text
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Reduce optimistic TP1 bias in backtest/live parity by treating TP1 as a conservative exchange-limit proxy: TP1 fills only on candle trade-through (`high > tp1_price`), exact touch is labeled but not filled, and same-candle SL/TP1 conflict remains stop-first.
Fix the anomaly-lab crash where pre-context signal universe construction reintroduced runner profile mark/OI requirements before context enrichment and raised `candidates missing required columns: ['mark_close_vs_decision_close_basis']`.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py tests/test_live_order_lifecycle.py
```

Risk:

```text
Medium for reported historical metrics. TP1 hit-rate, winrate, and expectancy can drop because old candle-high TP1 fills were optimistic. Signal selection and live order handling are unchanged.
```

## 2026-05-17 - P278 local research memory update - anomaly_lab review

Files:

```text
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Record the detailed anomaly_lab category/session/metric review in compact project memory. This is analysis-only and does not change signal selection, live execution, backtest logic, configs, or tests.
```

Validation:

```text
No code validation required. Reviewed current .output/results/anomaly_lab artifacts and updated research memory only.
```

Risk:

```text
Low. The only risk is overinterpreting a single 30d artifact; the recorded next step explicitly requires strict context_parity_status=ok ablation before threshold changes.
```

## 2026-05-17 - P279 applied locally - live-first category hardening

Files:

```text
research_tools/anomaly_category_contract.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Harden the default live category contract before running real 12 USDT live collection.
runner_reclaim is removed from the default live category set but remains supported for explicit/shadow runs.
shared_pump_category_contract_v1_live_overlay_v6 adds category-specific minimum range expansion, minimum initial risk, prior-whipsaw cap, prior-spike cap, and tighter trade-effort-per-return gates where the anomaly_lab review showed cleaner runners.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Medium. Frequency will drop versus v5 and some valid winners can be filtered. The change is intentional for minimum-size live collection: prefer fewer cleaner trades over broad discovery-like exposure. Do not interpret old discovery/backtest totals as account-return promises.
```

## 2026-05-17 - P280 applied locally - stale startup context tail guard

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent the first live cycles after a slow 72h startup backfill from accepting prior-spike / prior-fast-fade category evidence with a stale trailing context gap.
If the snapshot effective cache end is older than the signal decision by more than symbol_context_snapshot_fresh_ms, live returns symbol_context_snapshot_tail_stale as retryable dependency instead of treating the old 72h snapshot as usable.
The new P279 prior-spike unavailable reason is also retryable, so affected symbols are prioritized by the rolling symbol-context refresher instead of being consumed as final rejects.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Low/medium. Very slow startup can cause early candidates to wait for context refresh instead of trading immediately. This is intentional: stale prior-spike/fade context should block as a retryable dependency, not pass by default.
```

## 2026-05-17 - P281 applied locally - baseline liquidity floor

Files:

```text
research_tools/anomaly_category_contract.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Prevent tiny baseline liquidity from passing live runner categories only because a small print creates a large relative flow ratio.
shared_pump_category_contract_v1_live_overlay_v7 adds min_baseline_quote_daily_proxy=300k USDT/day proxy to runner categories.
Backtest and live compute the same proxy from baseline_quote_volume_median and setup timeframe.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Low/medium. This can filter some early microcap runners. The threshold is intentionally 300k, not 1m, because the 300k-1m bucket still had positive live-priority expectancy in the current artifact.
```

## 2026-05-18 - P293 applied locally - live context cache delta writes

Files:

```text
data/storage/parquet_storage.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Keep mandatory live startup context collection/readiness intact, but remove the wasteful full parquet rewrite from live tail context updates.
```

Implementation:

```text
ParquetStorage now supports per-symbol/timeframe delta parquet files under delta/*.parquet.
load() and load_window_result() transparently merge base data.parquet with delta files and dedupe by timestamp, so downstream code sees complete data.
Live symbol_context_* cache flushes use save_incremental_delta() and emit storage_mode=delta in live_ohlcv_cache_flushed.
symbol_context_startup_backfill_completed now records fetch_phase_seconds, flush_seconds, final_flush_seconds, snapshot_seconds, snapshot_write_seconds, and cache_write_storage_mode.
Regular offline cache builders still call save_incremental() and retain canonical full-merge behavior.
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q data\storage\parquet_storage.py research_tools\anomaly_micro_live.py
inline ParquetStorage smoke: base save_incremental + delta save_incremental_delta + load/load_window timestamp dedupe passed
.venv\Scripts\python.exe -m pytest tests\test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
```

Risk:

```text
Medium. Reads now include delta files, so repeated live restarts can accumulate small delta parquet files until a canonical cache rebuild/compaction is run. This is still safer than skipping startup context; next live must verify startup elapsed_seconds drops and no context readiness degradation appears.
```

## 2026-05-18 - P292 applied locally - live heartbeat/session-top label cleanup

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Keep the session-scoped live metrics from P290, but restore the operator heartbeat wording and remove the extra visible session-top suffix from live output.
```

Implementation:

```text
Heartbeat label changed back from "WS сессия" to "Стабильность".
The session metric window label is now empty, so session-top display/artifacts do not print "с начала сессии" while still using the same session baseline internally.
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_micro_live.py
inline smoke: _format_live_heartbeat contains "Стабильность" and does not contain "WS сессия" or "с начала сессии".
```

Risk:

```text
Low. Display/label-only change; data windows, filters, and execution logic are unchanged.
```

## 2026-05-18 - P291 applied locally - live session metric NameError fix

Files:

```text
research_tools/anomaly_micro_live.py
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix startup crash introduced by P290 session metrics and make live command errors print on a fresh terminal line.
```

Implementation:

```text
Replaced two accidental _HOUR_MS references with the module-local HOUR_MS constant.
runner.shutdown now finishes any active live status line before cache/source cleanup.
run-anomaly-live now calls runner.shutdown before LiveStartupError and generic exception exits, so the CLI exception logger starts after the status row is closed.
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_micro_live.py cli\commands.py
.venv\Scripts\python.exe -m pytest tests\test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
```

Risk:

```text
Low. This is a constant-name bug fix and terminal cleanup path; trading logic is unchanged.
```

## 2026-05-18 - P290 applied locally - live data-readiness retry and session metrics

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Prevent live from skipping an otherwise valid LTF decision when setup/entry OHLCV is temporarily empty because cache fill has not caught up.
Make live stability and top-growth display use the current session metric window instead of process-lifetime stability and rolling 6h growth.
Clarify operator data labels so OHLCV REST cache fills are not read as aggTrade/flow degradation.
```

Implementation:

```text
signal_scan_empty_ohlcv now returns retryable_dependency=True with reason ohlcv_data_unavailable and does not consume the decision timestamp as a normal no_signal.
WS health samples are stored with wall-clock timestamps and ws_health_pct is computed from the session metric baseline. Cumulative observed/healthy seconds remain in separate artifact fields.
Session top tracker prunes/baselines from the same session metric start: Asia+Europe from Asia start, Europe from Asia+Europe start, Europe+America from Europe start, America from Europe+America start, America+Asia from America start.
Terminal heartbeat stability value is session-scoped; OHLCV cache fill/gap labels are "OHLCV REST"/"OHLCV gap".
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_micro_live.py
```

Risk:

```text
Low/medium. Trade thresholds and order path are unchanged, but retrying empty OHLCV can let a still-fresh setup be evaluated later instead of being silently skipped. Session-scoped health resets at session metric boundaries by design.
```

## 2026-05-17 - P282 applied locally - live liquidity universe filter

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Avoid spending startup context and live scan budget on symbols with too little current market liquidity, while still allowing symbols to enter later if liquidity arrives.
Default live startup and 12h refresh use Binance REST 24h ticker quoteVolume >= 300k USDT for the implicit exchange universe.
Explicit --symbols bypass this filter. Source failures or empty filter results keep the previous universe instead of emptying live trading.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
.venv\Scripts\python.exe main.py run-anomaly-live --help | Select-String -Pattern "live-universe"
```

Risk:

```text
Low/medium. A fresh liquidity arrival can be missed until the next refresh interval. The default 12h interval is a conservative cost/freshness tradeoff; shorten it only if live artifacts show missed symbols that already had enough quoteVolume.
```

## 2026-05-17 - P283 applied locally - live terminal grid repaint

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Keep the live terminal status grid as one pinned block and make any warning/log line appear above the latest grid.
Python warnings are now routed through _LiveStatusLogger while the live loop is running, so warnings clear/repaint the grid instead of writing into the last grid row via stderr.
Multi-line grid clearing now moves to the top of the rendered block and clears downward, which is safer for wrapped terminal rows.
The pandas synthetic_ohlcv_bucket fill warning source was removed by using nullable boolean conversion before fillna.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Low. This is terminal display and warning routing only. It does not change signal selection, order placement, or artifacts except fewer stderr warnings in the operator terminal.
```

## 2026-05-19 - P303 proposed - recover external position exits and order UI count

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
If the exchange position is already flat when the monitor wakes up, try to recover the terminal TP1/stop exchange fill by known order id before writing exit_unresolved. Do not infer PnL from candles or stop price.
Add explicit entry_order_submit_started and entry_fill_verified audit events, because order submit/fill timing was only indirectly visible through position_opened.
Make the terminal heartbeat Orders cell show the cheap local count of verified protective order ids for open positions instead of orphan-order cancel delta.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low/medium. The patch does not change signal selection or entry/exit order placement. It can turn future external-flat exits from unresolved into closed only when the exchange returns a real fill for the known TP1 or stop order. Binance algo stop fill recovery may still stay unresolved if the exchange endpoint does not expose a fill by algo id; that is safer than synthetic PnL.
```

## 2026-05-19 - P305 proposed - strict hot-idle optional work and reject cooldown

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
After P304 immediate danger-flow promotion, keep the hot path clear by skipping top-growth audit, symbol-context snapshots, and normal cache flush while candidate queue/active/open/opening work exists. Cache flush may only run in a limited emergency mode when the buffer is too large.
Add symbol-level reject cooldown for repeated non-retryable weak-flow / mark-basis rejections. Immediate danger-flow scans bypass the cooldown, and a selected signal clears it.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This changes live scheduling, not strategy thresholds. It can reduce repeated scan load, but may delay rechecking a symbol that improves gradually without a fresh immediate danger-flow event. Top-growth/context/cache work becomes more idle-only, so research artifacts may be delayed during sustained hot queues, not lost by design.
```

## 2026-05-19 - P306 proposed - live quality trend windows

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the live operator view stop depending on one jumpy point-in-time metric. Record rolling 1m/5m/15m/run quality windows for WS health, cycle pulse, due-scan latency, queue pressure, and REST/gapREST data status. Show these windows in the terminal heartbeat and append a live_quality_window_summary event every minute, while also adding the same quality_* fields to live_cycle_summary.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. This is telemetry/display/artifact-only. It does not change signal selection, scan scheduling, order placement, fills, stops, exits, or guards. The heartbeat becomes longer by two rows, but the existing pinned-status renderer already supports multi-line grids.
```

## P302 live current OI guard no flags refresh - PROPOSED

- Status: PROPOSED / commit UNKNOWN.
- Adds a mandatory live-only current-OI short-cover guard before market entry.
- Blocks real entries when live price is up but Binance current OI is down vs recent 5m OI reference, or when current OI cannot be fetched.
- Keeps the exchange raw endpoint behind `CcxtFuturesClient.fetch_current_open_interest()`; no CLI flags.

## 2026-05-19 - P316 proposed - live2 stream signal adapter

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/market_data/candles.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/artifacts.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Replace the generation-0 `rejected_signal_engine_todo` endpoint with a pure hot-path SignalEngine adapter. The adapter evaluates only already-built live2 stream candles, maps available flow/price/baseline features to the shared pump category contract subset, and explicitly rejects categories requiring unavailable derivative context instead of silently falling back.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This adds the first live2 selected signal verdicts, but it still does not place orders and `new_entries_allowed=false` remains because entry guards and execution are not implemented. The adapter is intentionally conservative and not a full dataframe backtest clone: OI/mark/prior-fast-fade context is rejected as unavailable in live2 generation 0 rather than guessed.
```

## 2026-05-19 - P317 proposed - live2 executable entry guards

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/entry_guard.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add the live2 executable-entry guard layer after stream signal selection, before any order path exists. Selected signals are rejected if the live stream price shows stale signal age, excessive entry drift, TP1 already touched, or collapsed RR. The guard uses only already-known stream state and performs no network/cache/file IO.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This still does not place real orders and `new_entries_allowed=false` remains mandatory. The guard only makes selected-signal verdicts safer and more auditable before P318 execution. P317 also includes the missing live2 `signal.py` module required by the P316 deadline import so the P311-P317 stack imports cleanly.
```

## 2026-05-19 - P318 proposed - live2 strict execution boundary

Files:

```text
cli/commands.py
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/artifacts.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add the first live2 execution boundary after accepted entry guards. The boundary performs real exchange account preflight at startup and pre-entry exchange position checks for accepted selected signals. It rejects non-flat symbols and still blocks order placement as `rejected_execution_order_placement_not_implemented` until the verified-fill plus verified-stop path exists. No candle/ticker/local fallback is used for position state.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This patch introduces exchange reads into the post-guard path and startup preflight, but it deliberately does not place orders. `new_entries_allowed=false` remains because actual order submit, actual fill verification, verified stop placement, and position supervision are not implemented yet.
```

## 2026-05-19 - P321 proposed - live2 verified entry and initial stop lifecycle

Files:

```text
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/state.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Enable the first real-order live2 execution lifecycle after runtime gates and entry guards: pre-entry exchange position check, deterministic client-id market buy, actual fill recovery from exchange order/trades, post-entry exchange position delta verification, reduce-only initial stop submit, and strict stop visibility verification. If exposure exists and protection cannot be verified, live2 attempts emergency reduce-only close and halts further execution via an execution readiness gate.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
High. This is the first live2 patch that can place real orders when all runtime gates are ready and a selected signal passes entry guards. The patch intentionally caps generation-0 exposure to one protected position by default and uses a small fixed notional. TP1/BE/final-position supervision remains the next patch, so P321 must be tested on one explicit low-risk symbol/notional only after reviewing exchange credentials and artifacts.
```

## 2026-05-19 - P322 proposed - live2 verified position supervisor

Files:

```text
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/config.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add the first live2 PositionSupervisor for P321 protected positions. The supervisor checks exchange position amount, verifies the current stop remains visible, performs TP1 reduce-only partial close with verified actual fill, submits and verifies a breakeven replacement stop for the remaining position, cancels the old stop, and accepts final close only when the exchange position is flat. Any stop/position integrity failure halts further execution and attempts emergency reduce-only close.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Synthetic smoke:

```text
Fake exchange: verified entry -> protected position -> stream price reaches TP1 -> reduce-only TP1 fill -> BE stop verified -> old stop cancelled -> exchange position flat -> final close verified.
```

Risk:

```text
High. This patch supervises real live2 positions and can submit reduce-only TP1 exits, replacement stops, and emergency closes once P321 execution is enabled and all runtime gates are ready. It deliberately does not infer final fill prices from candles/tickers; final close is verified by exchange position flat until a dedicated stop-fill lookup is added.
```

## 2026-05-19 - P323 proposed - live2 runtime coverage hardening

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/market_data/ticker_ws.py
research_tools/anomaly_live2/market_data/aggtrade_ws.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Harden live2 runtime readiness around WS coverage and loop pressure. Add ticker/aggTrade connect/reconnect/disconnect counters, market-data coverage update events, market-data readiness recovery hysteresis, and decision-loop overrun counters in runtime gate status. New entries remain blocked immediately on stale/disconnected coverage and recover only after clean market-data windows.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This patch changes readiness gating and market-data status semantics but does not alter signal thresholds, order sizing, fill accounting, stops, or position supervision. The main operational effect is stricter no-new-entries during WS stale/reconnect/coverage transitions and a short clean-window delay before entries are allowed again after recovery.
```

## 2026-05-19 - P325 live2 startup and coverage hardening

Status: PROPOSED.

- Lazy-loads heavy CLI dependencies so run-anomaly-live2 can resolve without pyarrow-only research imports.
- Rejects Binance hedge mode in live2 execution preflight before any order path.
- Keeps cumulative candle gap/out-of-order counters as diagnostics instead of permanently blocking future real aggTrade buckets.
- Makes post-fill integrity result serialization safe for malformed/missing fill fields.

## 2026-05-19 - P326 proposed - live2 v1-style grid log

Files:

```text
research_tools/anomaly_live2/status_grid.py
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add a compact terminal operator grid for run-anomaly-live2 in the same practical style as v1: Соединение / Рынок / Торговля / Контроль sections, fixed-width cells, quality marks, runtime gates, WS shard health, deadline misses, artifact-writer queue, positions, TP1/final-close counters, and integrity-risk counter. The grid is console UI only; live2_events.csv and live2_status.json remain the source of truth.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'PY'
from research_tools.anomaly_live2.status_grid import format_live2_status_grid
print(format_live2_status_grid(...))
PY
```

Risk:

```text
Low. This patch changes operator output only. It does not change strategy thresholds, market-data ingestion, runtime gates, order placement, fill accounting, stops, or position supervision.
```

## 2026-05-19 - P327 proposed - live2 startup warmup and WS backoff

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/status_grid.py
research_tools/anomaly_live2/market_data/backoff.py
research_tools/anomaly_live2/market_data/ticker_ws.py
research_tools/anomaly_live2/market_data/aggtrade_ws.py
research_tools/anomaly_live2/market_data/warmup.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix two live2 startup/runtime gaps found after P326: WebSocket reconnects had fixed tight retry sleeps, and live2 had no startup warm-up for in-memory aggTrade candle rings. Add bounded exponential WS reconnect backoff with jitter, explicit watchdog stale restarts for ticker/aggTrade sources, startup-only Binance aggTrades REST warm-up before aggTrade WS starts, and configurable bounded candle ring depth. Warm-up is explicitly not available from signal/decision hot path.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live2 --help
```

Risk:

```text
Medium. Startup now makes bounded REST aggTrades requests for the selected universe before aggTrade WS starts, so first readiness can be slower but decisions start with real recent candles. During prolonged WS outage, reconnect pressure is lower and safer for Binance/IP throttling. Trading logic, thresholds, fill accounting, stops, and position supervisor behavior are not changed.
```

## 2026-05-19 - P328 proposed - live2 startup visibility and operator log polish

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/console.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/telegram.py
research_tools/anomaly_live2/market_data/warmup.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make run-anomaly-live2 visible during startup and stop noisy Telegram runtime-gate flips. Add a v1-style repaintable terminal status logger, show startup stages before the first heartbeat, show startup warm-up progress, default the auto universe to broad coverage (600 max, no 24h quote/trade-count minimum), and keep entries-enabled/disabled status only in the terminal grid/artifacts instead of Telegram.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live2 --help
```

Risk:

```text
Low-to-medium. Operator output and startup defaults change. Broader default universe increases WS/warm-up load, but this matches the intended v1-like broad coverage and remains controlled by --universe-max-symbols. Trading logic, fills, stops, TP/BE, and position supervision are unchanged.
```


## 2026-05-19 - P329 proposed - live2 universe quote-volume floor

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_live2/config.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Set the default live2 auto-universe 24h quote-volume floor to 30,000 USDT instead of 0. This keeps broad coverage closer to v1 while filtering completely inactive or dust symbols, without changing the maximum universe size, signal logic, execution, stops, TP/BE, or runtime gates.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live2 --help
```

Risk:

```text
Low. The startup universe becomes slightly narrower than P328 when many symbols have extremely low 24h quote volume, but should remain far broader than the old 300k floor. Users can still override with --universe-min-quote-volume-24h.
```

## 2026-05-19 - P330 proposed - live2 REST startup universe snapshot

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/market_data/startup_tickers.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix live2 auto-universe startup coverage. The first Binance !ticker@arr WebSocket payload can be partial and caused live2 to select/warm only about 90 symbols. Hydrate SymbolStateStore once at startup from the exchange ticker/liquidity snapshot, then select the universe from startup snapshot + live ticker state. Keep this REST call startup-only and unavailable to signal/decision hot path. Add a minimum auto-universe coverage guard so live2 does not silently proceed with a tiny auto universe.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live2 --help
python - <<'PY'
from research_tools.anomaly_live2.state import SymbolStateStore
from research_tools.anomaly_live2.market_data.startup_tickers import Live2StartupTickerSnapshot
from research_tools.anomaly_live2.market_data.universe import Live2UniverseSelector
class FakeExchange:
    def get_futures_symbols_with_liquidity_metrics(self):
        return [{"symbol": f"T{i:03d}/USDT:USDT", "quote_volume": 30000+i, "trade_count_24h": i} for i in range(520)]
store=SymbolStateStore(())
res=Live2StartupTickerSnapshot(state_store=store, exchange_client=FakeExchange()).run()
sel=Live2UniverseSelector(state_store=store, explicit_symbols=(), max_symbols=600, min_quote_volume_24h=30000, min_trade_count_24h=0).select()
assert res.rows_applied == 520
assert len(sel.selected_symbols) == 520
PY
```

Risk:

```text
Medium. Startup now does one broad exchange ticker/liquidity call before WS universe selection. This is intentionally outside the hot signal path and should prevent tiny partial-WS universes, but if the startup exchange snapshot fails live2 will emit explicit startup_ticker_snapshot/status diagnostics and the minimum auto-universe guard will expose undercoverage instead of silently warming only a small market subset.
```

## 2026-05-19 - P316 proposed - live2 stream signal adapter

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/market_data/candles.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/artifacts.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Replace the generation-0 `rejected_signal_engine_todo` endpoint with a pure hot-path SignalEngine adapter. The adapter evaluates only already-built live2 stream candles, maps available flow/price/baseline features to the shared pump category contract subset, and explicitly rejects categories requiring unavailable derivative context instead of silently falling back.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This adds the first live2 selected signal verdicts, but it still does not place orders and `new_entries_allowed=false` remains because entry guards and execution are not implemented. The adapter is intentionally conservative and not a full dataframe backtest clone: OI/mark/prior-fast-fade context is rejected as unavailable in live2 generation 0 rather than guessed.
```

## 2026-05-20 - P335 live2 markPrice WS context

```text
Current patch status: P335 PROPOSED / UNKNOWN commit.
Question: current default live2 pump categories require decision-time mark-vs-decision basis, but live2 had no mark-price source and rejected those category requirements as unavailable.
Change: live2 now starts a Binance USD-M routed market `!markPrice@arr@1s` WebSocket for the selected universe, stores per-symbol mark/index/funding fields, includes mark readiness in market-data coverage, exposes mark status counts/grid/diagnostics, and computes `mark_close_vs_decision_close_basis = (mark_price - decision_close) / decision_close` inside the hot-path signal adapter. There is no ticker/last-trade fallback for mark price.
Trading impact: stricter and more truthful. New entries remain blocked until ticker, live aggTrade, and markPrice streams are all fresh. Categories that require OI or prior-24h context can still reject until later patches add those sources.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic mark-price payload smoke.
Next validation: short live2 smoke after P331-P335; require `mark_price_ws.ready=true`, `mark_price_status_counts.ok > 0`, grid Mark rows > 0, and no `mark_price_context_not_ready` for symbols that have fresh mark rows.
```

## 2026-05-20 - P338 proposed - live2 dependency-aware signal gate

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/artifacts.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/status_grid.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Separate missing required live2 category dependencies from real strategy rejects. Missing mark/OI/24h prior/baseline/derived required fields now produce `data_dependency_not_ready` with per-category dependency reasons. Real threshold failures remain `rejected_signal_contract`. This prevents incomplete data sources from being counted as strategy failure or silently accepted.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'PY'
from research_tools.anomaly_live2.signal import Live2SignalEngine
from research_tools.anomaly_live2.state import SymbolState
from research_tools.anomaly_live2.market_data.candles import Live2Candle
state = SymbolState("TEST/USDT:USDT")
candle = Live2Candle(5000, 10000, 15000, 1.0, 1.05, 0.99, 1.04, 10.0, 10000.0, 100, 7000.0, 10001, 14999)
result = Live2SignalEngine().evaluate(state=state, candle=candle, actionable_reason="synthetic")
assert result.verdict == "data_dependency_not_ready", result
assert result.dependency_reasons
PY
```

Risk:

```text
Medium and intentionally stricter. Live2 may show fewer `rejected_signal_contract` and more `data_dependency_not_ready`. If current categories require flow-hold/taker-delta features that are not yet fully implemented, this patch exposes that as a dependency instead of accepting incomplete category evaluation.
```


## 2026-05-20 - P339 proposed - live2 flow-hold/taker confirmation contract

Files:

```text
research_tools/anomaly_live2/signal.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Implement the live2 flow-hold/taker-buy confirmation fields required by current category contracts without lookahead. The signal adapter now derives flow-hold only from already closed live WS 5s candles at or before the decision candle, exposes the exact definition/source fields, and maps category `min_flow_hold_count` / `min_next_taker_buy_quote_share` to live-confirmed pre-entry flow instead of future candles or zero fallback.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# synthetic closed-live-flow signal smoke
```

Risk:

```text
Medium. The patch intentionally changes live2 from `flow_hold_count_not_ready` to a real closed-live-candle confirmation contract. It does not use future data, but live semantics are explicitly labelled as trailing closed pre-entry flow, not backtest lookahead. This may make `runner_flow` computable where it was previously blocked by dependency status.
```

## 2026-05-20 - P340 proposed - live2 private user-data stream

Files:

```text
cli/parser.py
cli/commands.py
data/exchanges/ccxt_futures_client.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/contracts.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/status_grid.py
research_tools/anomaly_live2/user_data_stream.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Add a Binance USD-M private user-data stream to live2 execution truth. Live2 now creates/keeps alive a listenKey, connects to the routed `/private/ws/<listenKey>` WebSocket, emits ORDER_TRADE_UPDATE / ACCOUNT_UPDATE / conditional reject payloads into artifacts, redacts the listenKey in status, and blocks new entries unless the private stream is connected and the listenKey is active.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# synthetic user-data order payload smoke
```

Risk:

```text
Medium. This intentionally makes live2 stricter: real entries are blocked when the private user-data stream is not healthy. The patch does not replace REST fill/stop verification yet; it adds the private stream as mandatory execution telemetry and audit truth so order/fill/position lifecycle events are no longer invisible between REST checks.
```

## 2026-05-20 - P341 proposed - live2 full-TP1 position contract

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/telegram.py
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Bring live2 position supervision to the current no-runner TP1 contract: TP1 closes 100% of the exchange position, no BE stop is submitted for a remainder, and a TP1 close is accepted only after the exchange position is flat and the old initial stop is cancelled/verified gone. A non-flat post-TP1 position or orphan-stop cancellation failure is a strict position-integrity error.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# synthetic full-TP1 close smoke: verified fill -> exchange flat -> old stop cancelled -> protected registry removed
```

Risk:

```text
Medium. This intentionally removes the live2 TP1 partial/runner path. It is safer and simpler for the current strategy contract, but it changes position lifecycle semantics: there is no BE stop replacement after TP1 because there is no remaining position.
```

## 2026-05-20 - P342 proposed - live2 hard startup/execution safety

Files:

```text
data/exchanges/ccxt_futures_client.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/position_supervisor.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Remove ambiguous watch-only startup behavior from live2 after P341. Live2 is a real-order runtime: failed execution preflight, missing private user-data stream, or auto-universe below the configured minimum are startup failures, not long-running blocked states. Also align Binance USD-M listenKey keepalive/close REST calls with the documented no-parameter endpoints, and prevent orphan stop orders when a protected position is already flat outside the TP1 path.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# synthetic flat-position orphan-stop smoke: exchange flat + visible stop -> cancel -> verify gone before registry removal
```

Risk:

```text
Low-to-medium. This makes live2 fail faster instead of continuing as a blocked diagnostic process when real-order prerequisites are missing. That is intended for the current non-optional live2 contract. No signal thresholds, order sizing, market-data ingestion, TP price, or stop price calculation are changed.
```

## 2026-05-20 - P343 proposed - live2 hard market-data startup

Files:

```text
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make live2 fail fast when mandatory market-data streams are not ready at startup. After P342, execution/user-data/universe prerequisites were hard startup gates, but ticker, aggTrade, and markPrice WS could still leave the process running indefinitely in a blocked state. Live2 is a real-order runtime: missing mandatory market data is a startup failure, not an optional watch-only mode.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# synthetic startup-fail smoke: ticker/aggTrade/mark not-ready paths raise RuntimeError after writing explicit startup_failed events
```

Risk:

```text
Low. This only changes startup failure semantics for missing mandatory market-data streams. It does not change signal thresholds, category logic, order sizing, fill/stop/TP behavior, WS URLs, or poller behavior.
```

## 2026-05-20 - P344 proposed - live2 reconnect backoff API fix

Files:

```text
research_tools/anomaly_live2/market_data/mark_price_ws.py
research_tools/anomaly_live2/user_data_stream.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix a runtime-only reconnect defect introduced by the newer markPrice and private user-data stream sources. They called a non-existent Live2ReconnectBackoff.next_delay() method on reconnect/error paths, while the shared backoff helper exposes next_delay_seconds(). This could crash the WS thread after the first markPrice/private-stream reconnect instead of backing off and recovering.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# synthetic reconnect backoff smoke: markPrice and user-data sources call next_delay_seconds() without AttributeError
```

Risk:

```text
Low. This only fixes method-name usage on reconnect/error paths. It does not change endpoints, readiness rules, signal logic, order sizing, fill/stop/TP behavior, or strategy thresholds.
```

## 2026-05-20 - P345 proposed - live2 startup context prewarm

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/market_data/open_interest.py
research_tools/anomaly_live2/market_data/prior_context.py
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make live2 less blind at the first impulse by prewarming OI and 24h prior context for the entire selected startup universe before runtime entries begin. Runtime pollers remain active/radar scoped, but the initial context dataset is no longer lazy-loaded only after a symbol already trades. Missing/empty context remains explicit data_dependency_not_ready; there is no zero fallback and no signal hot-path REST fetch.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# synthetic startup context prewarm smoke: explicit OI/prior poll methods update SymbolState without starting poller threads
```

Risk:

```text
Medium. Startup becomes heavier because it performs explicit OI and 24h prior-context requests for the selected universe. This is intentional: live2 is meant to be a continuous real-order runtime, not a best-effort lazy scanner. Trading logic, thresholds, order sizing, WS endpoints, fill/stop/TP behavior are unchanged.
```

## 2026-05-20 - P346 proposed - add missing live2 prior-context module

Files:

```text
research_tools/anomaly_live2/market_data/prior_context.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the incomplete P345 patch stack by adding the missing live2 24h prior-context module imported by runner.py. Without this file, run-anomaly-live2 fails at import time with ModuleNotFoundError before startup diagnostics can run.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. This adds the module that P337/P345 already referenced. It does not change thresholds, order execution, WS endpoints, TP/stop behavior, or runtime gates.
```


## 2026-05-20 - P347 proposed - add missing live2 user-data startup event writer

Files:

```text
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the incomplete P340/P342 private user-data stream patch stack by adding the missing `_write_user_data_stream_starting_event()` method called during live2 startup. Without it, run-anomaly-live2 fails with AttributeError before listenKey creation and private WS startup can be audited.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. This adds only the missing startup audit event writer. It does not change execution gates, endpoints, signal logic, thresholds, order placement, fill verification, stop handling, or TP behavior.
```


## 2026-05-20 - P348 proposed - improve live2 aggTrade startup readiness diagnostics

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/market_data/aggtrade_ws.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the too-generic `live2 aggTrade WS startup failed: partial_or_connecting` startup failure by extending the default aggTrade startup wait and surfacing per-shard readiness blockers in `aggtrade_ws_startup_failed` artifacts/status. This keeps fail-fast strictness, but makes the failure actionable: no_messages vs connecting vs stale vs payload_error vs closed/error, including URL length, close code/reason, exception, rows and reconnect counters.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'CHECK'
from research_tools.anomaly_live2.market_data.aggtrade_ws import Live2AggTradeWsStatus
assert callable(getattr(Live2AggTradeWsStatus, '_readiness_reason'))
CHECK
```

Risk:

```text
Low. This does not weaken the startup gate and does not add a fallback. It only gives aggTrade shards more realistic startup time and makes readiness blockers explicit.
```

## 2026-05-20 - P351 proposed - keep live2 OI fresh across selected universe

Files:

```text
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/market_data/open_interest.py
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the post-P345 runtime OI blind spot where startup prewarm hydrated the full selected universe, but the runtime OI poller refreshed only active/radar symbols. After the 180s stale window, most passive selected symbols became `OI stale`, so their first impulse could still hit `data_dependency_not_ready` before OI refreshed.
```

Change:

```text
Runtime OI polling now continuously refreshes the whole selected universe with active/actionable/in-position symbols still prioritized first. OI stale window is aligned to the 5m OI-history cadence and the amortized full-universe refresh rate: default `oi_stale_ms=720000` and `oi_max_symbols_per_cycle=10` at a 5s poll interval. This is still bounded below Binance's documented open-interest history limit envelope and still has no zero fallback or hot-path REST decision fetch.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium-low. It increases steady OI REST traffic from active/radar-only to an amortized selected-universe refresh. The default request rate is about 600 requests / 5 minutes at 10 symbols per 5s cycle, below the documented 1000 requests / 5 minutes open-interest history limit, but this must be watched in live2_status: OI err should not rise, and OI stale should trend toward near zero after one full refresh cycle.
```

## 2026-05-20 - P350 proposed - polish live2 operator market status

Files:

```text
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/session_top.py
research_tools/anomaly_live2/status_grid.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix operator-visible live2 status issues found after the first successful market-data startup: show runtime from the beginning of market monitoring rather than startup/prewarm, show live positions as open/session-total instead of open/max-capacity so a fresh run displays 0/0, add v1-style session top movers between Market and Trading, and rename the cryptic deadline counter in the grid to `Опоздало`.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'CHECK'
from research_tools.anomaly_live2.status_grid import format_live2_status_grid
assert 'Позиции 0/0' in format_live2_status_grid(
    runtime_seconds=1,
    cycle_seconds=0.1,
    state_counts={'watching': 1},
    ticker_counts={},
    aggtrade_counts={},
    mark_counts={'ok': 1},
    open_interest_counts={},
    prior_context_counts={},
    candle_counts={'live_ready': 1},
    market_data_status={
        'ws_health': {'shards_total': 1, 'shards_connected': 1},
        'aggtrade_ws': {'rows_applied': 1},
        'mark_price_ws': {'rows_applied': 1},
        'open_interest': {'ready_symbols': 0, 'active_target_symbols': 0},
        'prior_context': {'ready_symbols': 0, 'active_target_symbols': 0},
        'universe': {'selected_symbols': 1},
        'startup_warmup': {},
        'stream_coverage_ready': True,
        'market_data_ready_for_entries': True,
    },
    decision_status={'total_deadline_missed': 0},
    execution_status={'open_protected_positions': 0, 'total_positions_protected': 0, 'max_open_positions': 1},
    user_data_stream_status={'ready': True},
    runtime_gate_status={'reason': 'ok', 'readiness': {'new_entries_allowed': True}},
    artifact_writer_status={'ready': True, 'queue_size': 0, 'queue_max_size': 8192},
)
CHECK
```

Risk:

```text
Low. This is operator UI/diagnostics only. It does not change signal thresholds, market-data gates, execution, stops, TP, OI/prior-context fetching, or order placement.
```
## 2026-05-21 - P371 proposed - live2 stage-aware active grid

Files:

```text
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/status_grid.py
research_tools/anomaly_live2/artifacts.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Stop the operator grid from calling ordinary real-trade buckets active. Replace `Активные current/seen` with live stage TTL counters: stage0 threshold-crossed bucket, stage1 signal selected, stage2 entry guard checked, stage3 entry guard accepted, stage4 execution attempted, stage5 position opened/protected. Keep the old actionable counter in status JSON for compatibility, but make it source from stage0 instead of `real_trade_bucket_for_backtest_parity`.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'CHECK'
from research_tools.anomaly_live2.status_grid import format_live2_status_grid
text = format_live2_status_grid(
    runtime_seconds=1,
    cycle_seconds=0.1,
    state_counts={'watching': 1},
    ticker_counts={},
    aggtrade_counts={},
    mark_counts={'ok': 1},
    open_interest_counts={},
    prior_context_counts={},
    candle_counts={'live_ready': 1},
    market_data_status={
        'ws_health': {'shards_total': 1, 'shards_connected': 1},
        'aggtrade_ws': {'rows_applied': 1},
        'mark_price_ws': {'rows_applied': 1},
        'open_interest': {'ready_symbols': 0, 'active_target_symbols': 0},
        'prior_context': {'ready_symbols': 0, 'active_target_symbols': 0},
        'universe': {'selected_symbols': 1},
        'startup_warmup': {},
        'stream_coverage_ready': True,
        'market_data_ready_for_entries': True,
        'stage_symbol_counts': {'stages': {'stage0': {'current': 1}, 'stage1': {'current': 0}, 'stage2': {'current': 0}, 'stage3': {'current': 0}, 'stage4': {'current': 0}, 'stage5': {'current': 0}}},
    },
    decision_status={'total_decisions': 1, 'total_deadline_missed': 0},
    execution_status={'open_protected_positions': 0, 'total_positions_protected': 0, 'max_open_positions': 1},
    user_data_stream_status={'ready': True},
    runtime_gate_status={'reason': 'ok', 'readiness': {'new_entries_allowed': True}},
    artifact_writer_status={'ready': True, 'queue_size': 0, 'queue_max_size': 8192},
)
assert 'stage0/1/2 1/0/0' in text
CHECK
```

Risk:

```text
Low. This changes diagnostics/operator UI and status JSON counters only. It does not change signal thresholds, category evaluation, guards, execution, exchange calls, stops, or TP management. Stage0 deliberately ignores `real_trade_bucket_for_backtest_parity` so passive liquid symbols no longer inflate `Активные`.
```

## 2026-05-21 - P372 proposed - live2 stage state store hotfix

Files:

```text
research_tools/anomaly_live2/state.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the local P371 runtime crash: `AttributeError: 'SymbolStateStore' object has no attribute 'stage_symbol_counts'`. P371 added runner/status-grid reads of stage counters, but the state-store hunk was not present in the local runtime path. Add the missing stage fields and `SymbolStateStore.stage_symbol_counts()` boundary in the state module itself.
```

Runtime failure fixed:

```text
run-anomaly-live2 failed in AnomalyLive2Runner._market_data_status() when reading self.state_store.stage_symbol_counts(...). This is a direct missing-method bug, not a data issue or exchange issue.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'CHECK'
from research_tools.anomaly_live2.state import SymbolStateStore

store = SymbolStateStore()
state = store.get_or_create('TEST/USDT:USDT')
state.stage0_passed_ms = 1_000
counts = store.stage_symbol_counts(now_ms=1_500, ttl_ms=1_000, session_start_ms=0)
assert counts['stages']['stage0']['current'] == 1
assert counts['stages']['stage0']['session_seen'] == 1
CHECK
```

Risk:


## 2026-05-22 - P387 proposed - align aggTrade 1s trust version with backfill

Files:

```text
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the backtest choke where subminute entry candidates are rejected as untrusted because the aggTrade 1s backfill writes `p378_aggtrades_to_1s_full_buckets_v1` while the strategy trust gate expects a different P378 version string. Also prevent the backfill command from skipping old covered 1s cache ranges unless the existing cache already carries the trusted version.
```

Validation:

```bash
python -m compileall -q data/exchanges data/fetchers research_tools cli constants.py main.py
```

Risk:

```text
Old p165/missing-version 1s/subminute caches remain untrusted. They must be regenerated; this patch only makes the regeneration path produce and reuse the version that the strategy accepts.
```

## 2026-05-22 - P388 proposed - targeted flow backfill inside backtest

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Restore the cheap backtest path: coarse setup-timeframe anomaly scan first, targeted 1s aggTrade backfill only around interesting anomaly windows, then materialize 5s/15s/30s entry caches before pair/forming candidate collection. Do not require full-universe 1s cache for the whole backtest period.
```

Validation:

```bash
python -m compileall -q data/exchanges data/fetchers research_tools cli constants.py main.py
```

Risk:

```text
The coarse setup-timeframe scan is intentionally a fetch planner, not a signal source. It can miss pathological cases where only the subminute forming path is interesting while the closed setup candle is not. The benefit is bounded network/cache work without reintroducing untrusted p165 subminute flow.
```

## 2026-05-22 - P389 proposed - bounded targeted flow backfill planner

Files:

```text
research_tools/anomaly_continuation_lab.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix P388's too-broad targeted flow planner. Coarse scan now only schedules 1s aggTrade windows for rows that pass pre-context closed setup signal guards, skips OI/derivatives enrichment during that planning scan, and no longer fetches setup baseline history in 1s data. Pair/forming baseline uses the trusted closed setup-timeframe cache; subminute data is used for the forming setup candle and execution path.
```

Validation:

```bash
python -m compileall -q data/exchanges data/fetchers research_tools cli constants.py main.py
```

Risk:

```text
This intentionally reduces backfill scope. A pathological setup that is only visible in the subminute forming path while not passing the closed setup prefilter can be missed by the automatic planner. That is preferable to accidentally rebuilding full-universe 1s history inside a backtest; use explicit symbols/date slices for deeper forensic scans.
```


## 2026-05-22 - P390 proposed - aggressively bounded targeted flow backfill

Files:

```text
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make targeted aggTrade flow backfill genuinely targeted. The planner now applies cheap coarse/pre-context guards before fetch, prunes windows that cannot pass subminute flow by closed setup-candle upper bounds, fetches only setup-candle plus immediate-entry-tail 1s windows instead of exit horizons, merges nearby windows per symbol, materializes only requested intervals, and ignores old/untrusted subminute cache files when choosing pair-collection symbols.
```

Validation:

```bash
python -m compileall -q data/exchanges data/fetchers research_tools cli constants.py main.py
```

Risk:

```text
The automatic planner is intentionally bounded. It is appropriate for normal research/backtest runs, but a forensic search for rare cases visible only in subminute flow while failing closed setup-TF prefilters still needs an explicit narrow symbol/date slice.
```

## 2026-05-22 - P391 proposed - backtest pipeline integrity artifacts

Files:

```text
cli/commands.py
research_tools/anomaly_strategy_backtest.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Make targeted-flow backtests self-auditing before edge metrics are interpreted. The runner now splits the targeted flow artifact into plan/fetch files, writes materialization and coverage reports, gates pair collection when planned flow windows produced zero trusted coverage, writes a funnel and run verdict, and disables grid/edge-health artifacts when the run is not data-valid.
```

Validation:

```bash
python -m compileall -q data/exchanges data/fetchers research_tools cli constants.py main.py
```

Risk:

```text
Runs with planned subminute flow windows and zero trusted coverage will now be explicitly invalid instead of continuing into misleading edge summaries. This can reduce apparent result availability, but it prevents treating data-pipeline failures as strategy outcomes.
```

## 2026-05-26 - P409 proposed - live2 current-OI entry baseline completed

Files:

```text
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/position_supervisor.py
research_tools/anomaly_live2/state.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Make live2 position management actually compare current open interest after entry against the current open interest baseline captured for the protected entry. Fix the state-store boundary that prevented current OI snapshots from being persisted by the OI poller.
```

Changes:

```text
- SymbolStateStore.update_open_interest now accepts and forwards current_* OI fields.
- Live2ExecutionEngine fetches current OI through the explicit exchange boundary after actual fill and verified initial stop.
- Live2ProtectedPosition stores entry_current_oi source/status/reason plus value/timestamps.
- Live2PositionSupervisor uses current-OI-vs-entry-current-OI for the OI-down early-exit branch and artifacts comparability fields.
- The 5m historical OI comparison remains visible as context, not as the OI-down trigger baseline.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Focused smoke:

```text
Fake exchange execute_selected captured entry_current_oi_open_interest=12345.0 into the protected position. SymbolStateStore.update_open_interest persisted current_oi_open_interest=11900.0 without TypeError.
```

Risk:

```text
The current-OI baseline is captured only after stop verification, intentionally after safety protection. It is close to actual entry but not an exchange-fill-timestamp OI tick. Binance current OI remains a polled endpoint, so use artifacts before treating this as a high-frequency liquidation signal.
```



## P446 - direct targeted aggTrades to LTF cache

Status: PROPOSED. Replace the rolling discovery targeted fetch path that materialized 1s cache first with direct aggTrades-to-target-LTF materialization for the official 30s LTF profiles. This preserves true trade-count/quote-volume provenance while removing the intermediate 1s parquet bottleneck, reuses already trusted direct/1s-derived target LTF cache windows, merges targeted windows up to one hour, and changes progress ETA formatting to `Hh MMm SSs`. No future labels, PnL, exits, or post-entry candles are used to decide fetch coverage.

## P447 - align live2 with rolling C/A/S runner portfolio

Status: PROPOSED. Prepare live2 for the same rolling runner category contract as the rolling discovery backtest: 30s decision buckets, rolling 5m/30s and 3m/30s HTF windows, fixed C -> A -> S priority, closed-candle-only baseline/dormancy/pregrowth context, risk-based sizing, total open-risk cap, and symbol cooldown equal to the selected rolling HTF width.

Changes:

```text
- Live2 signal evaluation no longer uses the legacy 5s/1m category contract for entries.
- Live2 evaluates rolling 5m/30s and 3m/30s profiles from closed 30s candles.
- C/A/S category selection uses only pre-entry features and returns data_dependency_not_ready when required closed 1m/30s context is missing.
- Live2 defaults to 30s decision buckets, 24h closed-1m startup baseline, and larger in-memory candle history.
- Execution sizes orders from account USDT balance, signal stop distance, and risk_per_trade_pct=2%.
- Execution rejects entries when total protected open risk would exceed 8% of account balance.
- Symbol cooldown is applied after a protected position is removed; the cooldown width is the selected rolling HTF window.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
The live signal contract is now closer to rolling backtest mechanics, but exact parity still depends on the 45d rolling backtest passing after P446 and on live having enough closed 1m baseline history. Missing history blocks entries explicitly instead of falling back to legacy categories.
```

## P449 - restore true rolling live context

Status: PROPOSED. Replace the P448 calendar-aligned live HTF context with event-rolling context. Live now builds baseline/dormancy/pregrowth/prior-spike history from non-overlapping HTF-width chunks ending at the latest closed 1m candle before the rolling HTF seed starts, rather than from wall-clock calendar HTF buckets. Startup 1m baseline warmup is capped to the latest 48h so live has enough prior-spike/baseline history without pretending to need full history.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
This deliberately prioritizes true rolling live mechanics over calendar-backtest parity. The next rolling discovery analysis should treat live-context parity separately and not assume old calendar-context feature distributions transfer unchanged.
```


## P450 - live2 rolling baseline warmup completeness guard

Status: PROPOSED. Current commit: UNKNOWN.

Fixes a live2 data-quality bug after P449: startup rolling HTF baseline warmup requested 48h of 1m Binance klines in one call and accepted partial results as warmed. P450 paginates the 1m kline warmup, keeps zero-volume 1m candles for continuity, and counts a symbol as warmed only when the recent contiguous 1m baseline is long enough for rolling C/A/S context. Incomplete symbols remain loaded for audit but become data-dependency-not-ready instead of silently producing decisions on partial baseline.

P450 also blocks rolling live C/A/S decisions when selected 30s confirmation or rolling HTF candles contain aggTrade-id gaps, so incomplete websocket/rest trade candles become data dependencies instead of tradable signals.


## 2026-05-29 - P451 live2 rolling gap tolerance

Status: PROPOSED. P450 hardened live2 data completeness but treated any selected 30s aggTrade-id gap as a hard dependency failure. P451 keeps hard rejection for large holes but allows small explicit tolerance for confirmation/rolling-HTF 30s candles, exporting quality fields into signal artifacts so tolerated-gap trades can be audited separately. It does not synthesize missing flow and does not use future/outcome data.

## 2026-05-29 - P452 live2 emergency 1m REST repair

Status: PROPOSED. After P451, live C/A/S had explicit 30s aggTrade gap tolerance, but rolling 1m context could still become stale after startup when quiet/no-trade minutes did not create live 1m candles. P452 adds a bounded emergency REST repair: when signal evaluation detects missing/stale/not-enough 1m context, it fetches official Binance 1m klines for the symbol, stores real zero-volume candles when the exchange reports them, rechecks continuity, and only then evaluates C/A/S. Large 30s aggTrade gaps remain governed by P451 tolerance; no synthetic candles, future labels, PnL, or outcome fields are used.
## P448 - fix live2 rolling context parity

Status: PROPOSED. Fix live2 rolling C/A/S context so the signal engine has enough closed 1m history for 24h prior-spike features plus the pre-spike baseline, and align live HTF context aggregation with the rolling discovery backtest. The traded HTF seed remains rolling, but baseline/dormancy/pregrowth/prior-spike context uses calendar HTF candles fully closed before the rolling window start. No fallback signal path is added.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

## 2026-05-29 - P453 TP1 partial structural-runner parity

Status: PROPOSED. Current commit: UNKNOWN.

Aligns the rolling runner discovery replay and live2 position supervisor around the intended runner-management contract: close 50% at TP1=0.75R, keep the remainder protected, and trail structurally. Live2 no longer uses early-exit conditions as close triggers by default; when an early-exit condition appears, it is written as `position_early_exit_reason_observed` telemetry on the protected position/artifacts and the position remains managed by TP1/structural trailing. Discovery replay now simulates the same TP1 partial leg before structural trailing.

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py launcher.py
```

Risk:

```text
This changes the backtest exit model, so old discovery PnL is not comparable to new discovery PnL. Live still has actual-fill/exchange-boundary differences by design; this patch only aligns position-management policy.
```

## 2026-05-29 - P454 live2 product audit backpressure hardening

Status: PROPOSED. Current commit: UNKNOWN.

Fixes live2 audit loss caused by high-volume routine deadline rows being treated as raw warning events. Routine `data_not_ready` / `data_dependency_not_ready` decisions are now info-level audit data and are aggregated into `live2_deadline_summary.csv`; raw event CSV remains bounded. Deadline rows that reached selected/entry-guard/execution/position-integrity stages remain protected from budget drops by typed verdict/data fields, not by broad severity.

Near-miss audit now has a durable aggregate layer: every near-miss updates `live2_near_miss_summary.csv`, and bounded top examples are kept in `live2_near_miss_examples.csv` even after raw `live2_near_misses.csv` reaches budget. Top-growth audit writes partial closed-hour artifacts on graceful shutdown when a chunked scan was interrupted, and the index records `completion_status` plus `remaining_count`.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Note: `launcher.py` is not present in this zip, so the requested launcher compile target could not be checked here.

Risk:

```text
Raw routine decision CSV is intentionally bounded and incomplete after budget. Strategy analysis should use the new summary/example artifacts for full-run funnels and raw CSV only for detailed examples. This patch does not change trading logic, fills, stops, or data-source fallbacks.
```


## 2026-05-30 - P458 live2 NameError sweep

Status: PROPOSED. Current commit: UNKNOWN.

Fixes the remaining live2 undefined global found by a sweep after the flat-stop recovery crash: `Live2PositionSupervisor.run_cycle()` referenced `_dict(...)` when classifying final-close reasons, but this helper was not defined or imported in `position_supervisor.py`. The patch adds a local typed `_dict()` helper and does not change position-management logic, exchange calls, fills, stops, or PnL recovery.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Additional static sweep used for live2: import all `research_tools.anomaly_live2.*` modules and inspect function bytecode for unresolved `LOAD_GLOBAL` / `LOAD_NAME`; after this patch no unresolved live2 globals remain.

## 2026-05-30 - P459 live2 runtime health cleanup

Status: PROPOSED. Current commit: UNKNOWN.

Fixes the next live2 health issues observed in run `20260529_190712` after the NameError crash: protected open positions were not projected back into `SymbolState.status`, routine deadline events still carried full heavy `signal_features` payloads, synchronous rolling-context REST repair could block the signal hot path while background maintenance was already running, and top-growth used the generic OHLCV path even though Binance raw closed 1h klines are available.

Changes:

- Project open protected positions into symbol state on every loop, so `live2_symbol_state.csv` and operator grid show `in_position` for protected positions instead of `watching`.
- Keep full deadline event payloads only for selected / entry-guard / execution / integrity rows. Routine rejects, backlog, missed deadline, flow freshness, and dependency decisions use a compact payload while full-run counts stay in summary artifacts.
- Defer emergency rolling 1m repair to the async maintenance worker when maintenance is healthy, avoiding REST in the decision hot path.
- Prefer explicit Binance raw 1h klines for top-growth snapshots, preserving quote volume / trade count / taker buy fields and avoiding generic OHLCV empty-frame ambiguity.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
A rolling-context gap may remain visible for one or more maintenance cycles instead of being synchronously repaired inside the signal evaluation. This is intentional: the live bot should preserve low-latency socket/decision processing and expose the gap, not block the hot path on REST. Trading entries still require fresh WS flow, live price guard, actual fill, and verified stop.
```

## 2026-05-30 - P460 live2 rolling 1m maintenance gap bridge

Status: PROPOSED. Current commit: UNKNOWN.

Fixes the remaining high `rolling_1m_history_not_ready` seen in run `20260530_065636`. The problem was not socket health and not ring capacity in the patched run: 1m rings already held ~3000 candles. The product bug was that background maintenance treated a symbol as current when the latest 1m candle existed, even if a no-trade gap immediately before that fresh WS-built tail broke the contiguous rolling baseline. Maintenance now verifies recent contiguous official 1m coverage up to the expected closed minute and fetches a bounded official-kline lookback window when the tail is fresh but not contiguous.

Changes:

- Increase default maintenance lookback to 720 closed 1m candles so one bounded request can bridge long post-startup gaps without touching the decision hot path.
- Add `rolling_1m_maintenance_recent_contiguous_count` to symbol artifacts.
- Do not skip maintenance solely because `latest_open >= expected_open`; require contiguous recent 1m coverage as well.
- Keep WS aggTrade 30s decision flow untouched; official 1m klines remain baseline/dormancy context only.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Initial maintenance may fetch a larger 1m lookback for symbols whose recent tail is fresh but internally gappy. This increases REST rows per repaired symbol, but not request count. If Binance rate-limit errors appear, lower max_symbols_per_cycle or add auto-degrade before changing trading logic.
```


## 2026-05-30 - P461 live2 maintenance snapshot and console throttling

Status: PROPOSED. Current commit: UNKNOWN.

Fixes two live stability defects seen in run `20260530_080733`: the rolling 1m maintenance status path could iterate a candle deque while websocket/maintenance writers mutated it, crashing live with `RuntimeError: deque mutated during iteration`; and non-inline consoles/IDEs printed the full status grid on every heartbeat, producing repeated `◆ Соединение` blocks.

Changes:

- Add a locked `SymbolStateStore.rolling_1m_context_snapshot()` read boundary for latest closed 1m open time and recent contiguous count.
- Move rolling 1m maintenance currentness checks to the locked boundary instead of iterating `ring.closed` from a mutable `SymbolState` reference.
- Make `Live2RollingContextMaintenance.status()` a cheap lock-only snapshot; target discovery now publishes `active_target_symbols` from the worker cycle instead of rescanning candle rings from the runner heartbeat.
- Throttle full status-grid snapshots when inline terminal repaint is unavailable; artifacts remain the source of truth.
- Expand tabs before rendered-row counting to reduce stale-line leftovers in inline repaint mode.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Non-inline consoles now receive periodic status snapshots instead of every heartbeat. This reduces operator-log spam but does not change artifacts or trading logic. Rolling 1m maintenance reads are serialized only while copying open_time_ms values; REST fetches and websocket ingestion remain outside this read path.
```

## 2026-05-30 - P463 live2 runtime operator hygiene

Status: PROPOSED. Current commit: UNKNOWN.

Fixes the next live2 runtime/operator issues found after P462: Windows file locks on non-critical snapshot artifacts could disable new entries, startup warmup phases ran sequentially when they could safely overlap, top-growth progressed too slowly because the heartbeat kicked only one bounded chunk, user-data stream readiness did not distinguish transport readiness from observed payload/order events, and non-inline consoles could still receive non-grid log lines after startup.

Changes:

- Treat `live2_events.csv` and `live2_near_misses.csv` writer failures as critical, but keep status/symbol-state/diagnostics snapshot write failures non-critical so a locked `live2_symbol_state.csv` does not disable entries.
- Add critical/non-critical artifact writer error counters to status artifacts.
- Run startup HTF 1m baseline and aggTrade warmups concurrently; run startup OI and 24h prior-context prewarms concurrently.
- Add compact ETA startup progress lines for aggTrade, 1m OHLCV, OI, and 24h context.
- After startup, suppress normal console log lines and keep only the repaintable status grid; runtime errors remain in artifacts/Telegram paths.
- Make top-growth continue bounded chunks in its own worker until the closed-hour audit is complete or shutdown is requested.
- Add `completion_status`, processed/remaining counts, and symbol totals directly to top-growth top/status CSV rows.
- Split user-data stream status into `transport_ready`, `payload_seen`, and `order_event_seen` while keeping `ready` as transport/listenKey readiness.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Startup now uses two concurrent public REST warmups and two concurrent context prewarms. If Binance rate-limit errors rise, reduce the overlapping startup concurrency or add an explicit shared rate gate before changing trading filters. Non-critical snapshot write failures are visible in artifact_writer_status but no longer block entries; critical audit stream failures still block entries.
```

## P464 live2 operator console isolation — PROPOSED / UNKNOWN commit

Reason: P463 made startup progress compact, but ordinary logging StreamHandlers, retry warnings, and uncaught background-thread tracebacks could still write into stdout/stderr and split the repainting warmup/grid UI.

Changes:
- live2 operator console is now a single repainting ANSI surface; it clears and redraws the full operator block instead of appending snapshot blocks;
- run-anomaly-live2 uses file-only command logging, so command start/finish lines are not printed to console;
- non-operator stdout/stderr and existing console logging handlers are redirected to `live2_suppressed_stdout.log` / `live2_suppressed_stderr.log` inside the run artifacts;
- exchange retry diagnostics for live2 use a file-only logger;
- top-growth background worker exceptions are caught and written to `live2_events.csv` instead of printing a thread traceback.

Risk: terminal UI only. Trading logic, signal selection, order placement, and data artifacts are unchanged.

Verification: run `python -m compileall data/exchanges research_tools cli constants.py main.py`; then live-smoke must show only the repainting warmup block before readiness and the repainting grid after readiness. Detailed errors must appear in artifacts only.

## 2026-05-31 - P471 separate signal and portfolio verdicts

Status: PROPOSED. Current commit: UNKNOWN.

Separates shared-core signal truth from live/backtest portfolio allocation artifacts. The patch does not change thresholds, seed-first logic, execution, exits, risk sizing, or order placement.

Changes:

- Adds `signal_verdict` / `signal_reason` and `portfolio_verdict` / `portfolio_reason` to live2 deadline decisions and summary artifacts.
- Marks live portfolio blocks explicitly: same-symbol open, symbol cooldown, max open positions, per-trade risk budget, total risk cap, existing exchange position, runtime gates.
- Keeps final `verdict` for backward compatibility, but it is no longer the only field used to judge signal quality.
- Adds the same signal/portfolio fields to HTF/LTF discovery decision ledger, raw trades, live-filtered trades, and portfolio events.
- Marks backtest portfolio-selected trades as `accepted_for_execution`; blocked rows remain visible in `htf_ltf_runner_portfolio_events.csv`.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Artifact schema expands. Existing readers that only use final verdict continue to work, but research should switch to signal_verdict for strategy-quality counts and portfolio_verdict for capacity/allocation counts.
```

## 2026-05-31 - P472 decision snapshot hash parity ledger

Status: PROPOSED. Current commit: UNKNOWN.

Adds source-neutral snapshot hashing and parity ledger artifacts for the shared rolling seed-first decision core. This patch does not change thresholds, signal selection, portfolio constraints, execution, exits, sizing, or order placement.

Changes:

- Adds `decision_snapshot_hash(...)` and `decision_snapshot_match_key(...)` to `research_tools/pump_decision_core.py`.
- Backtest decision ledger now writes `snapshot_hash` and `snapshot_match_key` for every shared-core verdict row.
- Live2 writes `live2_decision_ledger.csv` for every shared-core decision row with the same hash/key fields plus signal/portfolio/execution outcomes.
- Adds `research_tools/decision_parity_join.py` to build exact live-vs-backtest parity comparisons by `snapshot_hash`.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python research_tools/pump_decision_core.py
```

Risk:

```text
Artifact schema expands and live2 writes one additional append-only CSV. Same snapshot hash with different signal verdict is now a hard parity bug; same signal verdict with different portfolio/execution verdict is allocation/execution parity, not signal-core parity.
```

## 2026-05-31 - P474 remove legacy duplicate decision paths

Status: PROPOSED. Current commit: UNKNOWN. P473 scope-hygiene patch intentionally skipped by operator request.

Cleanup-only patch after P472. It removes the remaining post-hoc backtest category rematch path and adds an explicit source guard against reintroducing duplicate live/backtest strategy decision implementations. It does not change thresholds, rolling seed-first logic, portfolio constraints, execution, exits, sizing, orders, or artifact scope rules.

Changes:

- HTF/LTF discovery now passes `trade_rows` directly into portfolio simulation instead of calling `_with_runner_candidate_categories(...)`.
- Removes `_with_runner_candidate_categories(...)`; category id, matched categories and priority rank must originate from the shared decision core verdict/features.
- Removes the adapter-side `match_rolling_categories` import from discovery.
- Adds `research_tools/decision_contract_guard.py` to fail on legacy duplicate decision paths or adapter-side category rematching.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python -m research_tools.decision_contract_guard
python research_tools/pump_decision_core.py
```

Risk:

```text
If an older intermediate patch stack still creates trade rows without `runner_candidate_category`, portfolio simulation will now fail visibly via missing required columns instead of silently recomputing categories after the fact. That is intentional: signal categories must come from the shared core only.
```

## 2026-05-31 - P475 normalize rolling context parity

Status: PROPOSED. Current commit: UNKNOWN.

Fix-up patch after P465-P472/P474. It makes the shared seed-first contract actually source-neutral by using rolling HTF context from the same LTF substrate on both adapters, removes the remaining backtest pre-core HTF gate, emits per-confirm exact-window verdict rows, expires stale live pending seeds, and removes adapter decision time from `snapshot_hash`.

Changes:

- `PumpDecisionCore` core version becomes `p475_rolling_context_parity`.
- `snapshot_hash` no longer includes `decision_time_ms`; adapter time remains in ledgers only.
- Adds `evaluate_ltf_confirm_sequence_after_seed(...)` so every exact confirm window can be written to rejected/decision ledgers.
- Validates pre-seed context as rolling HTF-width windows stepping by the LTF interval, not calendar HTF candles.
- HTF/LTF discovery builds pre-seed context from rolling LTF-derived HTF windows and no longer filters seeds by `anomaly_gate` before the core.
- Live2 builds the same rolling HTF context from closed 30s candles and expires pending seeds after the max confirm window instead of letting dependency seeds linger forever.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python research_tools/pump_decision_core.py
python -m research_tools.decision_contract_guard
python -m research_tools.decision_parity_join --help
```

Risk:

```text
Backtest candidate/decision ledgers can grow materially because non-anomaly rolling seeds are now passed to the shared core instead of being silently skipped by the adapter. That is intentional for parity/audit, but broad runs may need artifact size monitoring.
```

## 2026-05-31 - P476 deterministic seed-aligned context contract

Status: PROPOSED. Current commit: UNKNOWN.

Fix-up patch after P475. It makes the pre-seed context contract deterministic and seed-aligned without changing C/A/S thresholds, execution, portfolio, exits, sizing, or orders.

Changes:

- `PumpDecisionCore` core version becomes `p476_deterministic_seed_aligned_context`.
- Defines exact contract context length per TF as max(baseline/dormancy/pregrowth windows, 24h prior-spike horizon).
- Hash and feature derivation ignore adapter-retained history outside the exact contract slice.
- Pre-seed context is now non-overlapping seed-aligned HTF windows ending exactly at `seed_open_ms`.
- Backtest builds the same seed-aligned HTF context from LTF cache, not calendar HTF and not overlapping every LTF step.
- Live2 builds the same seed-aligned context from closed 30s candles.
- Backtest post-seed strict LTF window now loads exactly `max_confirm` candles, not `max_confirm + 1`.
- Core smoke validates that extra adapter history before the contract slice does not change `snapshot_hash` or verdict.
- Live2 default in-memory closed-candle retention is raised to 3000 so a 24h 30s context can fit when the runner uses defaults.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python research_tools/pump_decision_core.py
python -m research_tools.decision_contract_guard
python -m research_tools.decision_parity_join --help
```

Risk:

```text
Live signal selection now honestly requires enough closed 30s history to build the 24h seed-aligned context. If startup/maintenance has not populated the ring, the core returns `contract_seed_aligned_context_not_ready` instead of using a shorter accidental baseline.
```

## 2026-06-04 - P505 targeted LTF accelerator module

Status: APPLIED locally / UNKNOWN commit.

Implements the first honest runner-discovery accelerator step without changing strategy logic:

- Adds `research_tools/targeted_ltf_accelerator.py` as the single orchestration module for targeted subminute cache acceleration.
- Keeps `PumpDecisionCore` and runner discovery signal logic unchanged; the accelerator only subtracts trusted target-LTF cache coverage, fetches missing Binance futures aggTrades, and materializes requested target candles.
- Turns `ensure_targeted_aggtrade_direct_ltf_cache(...)` into a compatibility facade that delegates to the accelerator, avoiding a second active orchestration path.
- Lets 15s/30s runner-discovery fetches materialize both sibling target caches from the same raw aggTrade interval. The active profile timeframe is not changed.
- Adds accelerator artifact labels: `accelerator_model`, `source_priority`, and direct aggTrades data source fields.
- Adds tests for multi-target materialization and partial-bucket honesty.

Validation:

```bash
python -m pytest tests/test_runner_discovery_acceleration.py -q
```

Risk:

```text
This is not the full 10x solution by itself. It removes duplicated orchestration and enables cache reuse across 15s/30s materialization, but a 30d run can still be too slow if most windows are cold and REST is the dominant source. Next acceleration should plug bulk/raw aggTrade archive reuse into this module's source-priority chain, then prove parity by snapshot_hash/signal_verdict overlap.
```

## 2026-06-04 - P506 archive-backed targeted LTF acceleration and parity summary

Status: APPLIED locally / UNKNOWN commit.

Extends P505 toward the actual 30d goal without changing signal logic, thresholds, portfolio rules, execution, or exits:

- `research_tools/targeted_ltf_accelerator.py` now uses source priority `trusted_target_ltf_cache -> Binance public futures daily aggTrades archive -> Binance aggTrades REST`.
- Public archive ZIPs are cached under `.output/cache/_raw_aggtrade_archive/...`; missing daily archives get a `.missing` sentinel so broad runs do not repeatedly request known-missing symbol-days.
- Archive data is filtered back to the exact requested interval before materialization. Full-day archive availability is transport/cache only, not a feature lookahead.
- Fetch artifacts expose `raw_aggtrade_source`, `archive_status`, `archive_error`, `archive_files`, `archive_urls`, and `rest_fallback_used`.
- Direct target-LTF coverage now records every fully contained target bucket in the fetched raw interval, including quiet buckets with no trades. This avoids repeat downloads for already-proven empty subminute buckets.
- `research_tools.decision_parity_join` can now write an optional summary CSV with exact `snapshot_hash` coverage, shared signal parity, and selected-signal overlap metrics.

Validation:

```bash
python -m pytest tests/test_runner_discovery_acceleration.py tests/test_decision_parity_join.py -q
```

Risk:

```text
Downloading a full daily aggTrades archive can be heavier than REST for a tiny one-off interval on very liquid symbols, but it is reusable and avoids REST rate-limit collapse in broad 30d discovery. Acceptance is not PnL: require no same-snapshot signal mismatches and target selected-overlap >= 90% on a matched live/backtest period before treating the simulator as live-like.
```

## 2026-06-04 - P507 simplify runner discovery CLI

Status: APPLIED locally / UNKNOWN commit.

Removes shell-level tuning from `run-htf-ltf-runner-discovery` so the standard command is again:

```bash
python main.py run-htf-ltf-runner-discovery --days N
```

Changes:

- Removes public targeted-backfill/backfill-threshold flags from `cli/parser.py`.
- Removes command-side profile override plumbing from `cli/commands.py`; fixed profile values now come only from the profile table.
- Keeps targeted subminute backfill enabled internally for the fixed research profiles.
- Adds parser regression tests that accept `--days` and reject removed tuning flags.
- Updates `research/STRATEGY_SPEC.md` to state that `--days` is the only public flag and that raw aggTrade loading is data transport, not a strategy knob.

Validation:

```bash
python -m pytest tests/test_cli_runner_discovery_empty_artifacts.py -q
```

Risk:

```text
Users can no longer run ad-hoc runner-discovery threshold variants from the shell. That is intentional: threshold/profile changes should be deliberate code/research patches so live/backtest parity and experiment identity remain auditable.
```

## 2026-06-04 - P508 unsupported Binance market id data-source rejection

Status: APPLIED locally / UNKNOWN commit.

Fixes a 3d readiness-run data-source hygiene issue before broad 30d discovery. Some cached symbols had non-ASCII pseudo market ids such as Chinese-name tokens; the targeted LTF accelerator attempted public archive/REST aggTrade loading and produced `UnicodeEncodeError` rows. The errors were visible, but broad 30d would multiply useless retries and noisy artifacts.

Changes:

- Adds an explicit `unsupported_binance_market_id` status in `research_tools/targeted_ltf_accelerator.py`.
- Non-ASCII/non-alphanumeric Binance market ids no longer call public archive or REST aggTrades.
- Fetch/materialize artifacts still retain the rejected windows with `unsupported_binance_market_id`; this is a visible data-source rejection, not a silent skip.
- Adds a regression test covering a Unicode symbol and proving REST is not called.

Validation:

```bash
python -m pytest tests/test_runner_discovery_acceleration.py -q
```

Risk:

```text
If Binance ever lists a symbol requiring non-ASCII market ids, this guard would reject it. Current Binance futures market ids are ASCII, and rejecting unsupported pseudo-symbols is safer than emitting runtime encoding errors during 30d discovery.
```

## 2026-06-05 - P511 conservative TP fill report and archive CSV noise cleanup

Status: APPLIED locally / UNKNOWN commit.

Changes:

- `research_tools/htf_ltf_runner_discovery.py` now treats TP1 fill as a conservative exchange-limit proxy: exact candle-high touch is ambiguous, so TP1 requires `high > tp1_price` instead of `high >= tp1_price`.
- The runner discovery honesty report now describes the actual exit model: TP1 partial close followed by structural trailing or max-hold exit. The stale "no TP is simulated" wording was wrong after the TP1 partial runner contract.
- `research_tools/targeted_ltf_accelerator.py` reads public-archive aggTrade CSVs with `low_memory=False` to avoid noisy dtype warnings from mixed Binance archive rows.
- Research memory was updated with the frozen clean-buyer category hypotheses and the strong-category exit-policy replay.

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
The TP fill change is conservative and can only remove ambiguous exact-touch TP fills from future backtests. It does not change signal/category selection, targeted data loading, live order placement, actual exchange fills, or stop logic. Historical 30d artifacts generated before this patch remain tied to their recorded code/report versions; the follow-up replay showed no difference between >= and > for current TP0.75/50 on the 118 strong-category refill subset.
```

## 2026-06-05 - P512 shared clean-buyer trade policy

Status: APPLIED locally / UNKNOWN commit.

Purpose:

Move the profitable mined category family into one source-neutral trade-policy layer so live2 and runner discovery do not implement separate versions of the same rules.

Changes:

- Added `research_tools/pump_trade_policy.py` with `clean_buyer_continuation_v1`.
- The broad `buyer55 + no pre-seed dump` rule is watchlist-only. Accepted trade rules are stronger clean-buyer variants: high-win clean no-dump, 5m strong confirmation/history, 15s fast tape/history, 5m15 active tape, distributed seed, and not-late tape.
- Exit policy is now part of the policy decision: `tp075_close75_structural_trail_v1`, `TP1=0.75R`, close `75%`, trail `25%`.
- Runner discovery calls policy after `PumpDecisionCore selected`; policy rejects are written as signal-quality rejects with visible `trade_policy_*` fields and are not simulated as trades.
- Runner discovery replay reads `tp1_r` and `tp1_close_fraction` from the accepted signal policy row.
- Live2 signal adapter calls the same policy after core selected. Policy-rejected live rows do not reach entry guard/order execution.
- Live2 decision ledger includes `core_signal_*`, `trade_policy_*`, and `exit_*` fields.
- Live2 protected positions carry trade/exit policy fields. Managed TP1 is recalculated from actual fill and actual risk; signal TP is kept as audit context.
- Position supervisor uses per-position `tp1_close_fraction`; config is fallback for legacy/manual positions.
- Added unit coverage for policy decisions and live signal adapter policy acceptance/rejection.

Validation:

```bash
python -m pytest tests/test_pump_trade_policy.py tests/test_live2_trade_policy_signal.py tests/test_pump_decision_contract.py tests/test_htf_ltf_runner_discovery.py::test_runner_simulation_marks_policy_tp1_partial_hit -q
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Known unrelated test limitation:

```text
tests/test_live2_market_watch.py currently fails collection because it imports legacy _effective_context_status from research_tools.anomaly_live2.signal. Several tests/test_htf_ltf_runner_discovery.py helpers also still use stale pre-P5xx signatures. Those failures pre-existed the shared trade-policy patch and should be cleaned separately.
```

Risk:

```text
This intentionally reduces live/backtest trade count because broad C/A/S core-selected rows now need to pass the clean-buyer trade policy. The change should improve quality and parity, but 30d metrics before P512 are no longer directly comparable to P512 runs because the traded signal layer changed.
```
