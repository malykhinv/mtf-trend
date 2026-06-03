## 2026-06-03 - P500 1d runner discovery bottleneck investigation

User observation:

```text
1d run started about 08:00; near noon the first TF had still not finished.
```

Findings:

```text
active process stack #1: _collect_symbol_candidates -> _pregrowth_features
active process stack #2: _decision_ledger_row -> decision_snapshot_hash
active process stack #3: _fetch_binance_futures_aggtrades_rows
planner profile: _resolve_end_timestamp_ms -> ParquetStorage.get_last_timestamp -> full load/concat
cache coverage bug: direct target-LTF writes went to 30s/delta, but trusted coverage checked only 30s/data.parquet
empty aggTrades bug: empty verified windows were not marked covered and were fetched repeatedly
```

Quantitative diagnostics:

```text
pre-entry plan: 3831 windows / 490 symbols
coverage before delta/index fix: 0 covered, 3831 missing intervals, 638.5h fetch
coverage after indexing existing delta: 3491 covered, 523 missing intervals, 4.93h fetch
coverage index migration: 587 symbols written, ~1,010,702 rows, 156s
limited fetch smoke: 12 windows, ~2.5s fetch, empty-window coverage index rows written
fast end timestamp lookup: 594 symbols in ~14.2s
```

Interpretation:

```text
The long run was mostly infrastructure waste, not evidence that the strategy needs thresholds changed. The biggest issue was broken cache reuse: already downloaded direct aggTrades LTF deltas were invisible to cache subtraction, so reruns kept redownloading and recomputing.
```

Next experiment:

```text
Run only 5m_30s / 1d first. Accept if targeted pre-entry LTF starts mostly from covered cache and any remaining Binance failures are visible fetch rows, not silent hangs or repeated hundreds-of-hours missing coverage.
```

## 2026-06-03 - P499 signal-entry planning bottleneck

Patch: P499 applied locally / UNKNOWN commit.

Observed user run:

```text
command: .\.venv\Scripts\python.exe main.py run-htf-ltf-runner-discovery --days 1
stage: runner discovery 5m_30s targeted signal-entry plan
progress: 277/594 symbols
ETA: about 7h18m
```

Finding: the slow stage was not aggTrades network fetch. It was CPU/IO inside signal-entry planning. The planner used `_collect_symbol_candidates(...)` and then ran full `core_seed_stage_prefilter(...)` for every exact rolling seed candidate. Profiling JCT showed expensive repeated context/prior-spike/hash work before any short confirm fetch.

Fix/benchmark:

```text
JCT 1d 5m_30s:
  pre-entry plan: ~0.6s
  signal-entry plan after P499: ~1.5s normal timing, ~6s in later runs with IO/cache overhead

First 50 cached symbols:
  pre-entry plan: 16.5s
  signal-entry plan: 54.4s
  seed symbols: 40
  pre-entry seeds: 331
  planned short confirm windows: 595
  return-gate terminal rejects: 2780
```

Interpretation: this is now minutes-scale for a 1d full-universe smoke, not a multi-hour planning black hole. Planned confirm windows increase because the planner is deliberately conservative; the normal shared core still decides signal quality after data is loaded.

Next experiment: rerun the original 1d command and inspect `htf_ltf_runner_targeted_ltf_plan.csv`, `targeted_ltf_fetch.csv`, and `decision_ledger.csv` before attempting 30d.

## 2026-06-02 - P498 pre-start smoke after context correction

Patch: P498 applied locally / UNKNOWN commit.

A focused actual smoke was run before retrying a long backtest:

```text
output: .output/results/htf_ltf_runner_discovery_smoke_p498_jct
symbol: JCT/USDT:USDT
period: 1d ending 1780329000000
profile: 5m_30s
targeted_backfill_max_events_per_symbol: 5
```

Result:

```text
candidates: 53
decision ledger rows: 89
signal_verdict: 89 rejected, 0 data_dependency_not_ready
top reasons:
  seed_htf_return_below_min: 39
  ltf_confirm_return_below_min: 30
  seed_ltf_flow_not_sustained: 7
  ltf_second_half_return_below_min: 6
targeted plan statuses:
  not_planned_seed_stage_terminal_reject: 47
  planned: 11
targeted fetch windows:
  4 ok fetch rows
  requested/fetched windows: 420s to 900s
```

Interpretation: the old context-contract failure is fixed for this sample without downloading 24h of 30s context. This is not edge evidence and not proof of no trades. It is a pre-start health check showing that the current path can reach real shared-core rejects and that seed-stage terminal rejects remain visible in targeted plan artifacts.

Next experiment: fresh full-universe 1d `5m_30s` smoke. Acceptance is a visible funnel with few/no context-contract dependencies, not profitability.

## 2026-06-02 - P497 current-code check after 1d zero diagnosis

Patch: P497 applied locally / UNKNOWN commit.

Follow-up to the old `.output/results/htf_ltf_runner_discovery_1d/5m_30s` zero run: the old artifact was pre-P493, but current-code review found the same class could persist in 1d smokes because HTF was loaded only from scan `start_ms`.

Current-code sample check over 40 old 1d candidates after P497:

```text
context length 288: 32 candidates
context length 0: 8 candidates
real seed rejects: 32 candidates
remaining context dependencies: 8 candidates with insufficient pre-run HTF cache
top real reasons: seed_htf_return_below_min, seed_htf_trade_ratio_below_min
```

Interpretation: adapter range bug is fixed; remaining `contract_seed_aligned_context_not_ready` rows in a fresh run should now mean genuine missing 24h HTF cache for that symbol, not failure to load available warmup.

## 2026-06-02 - 1d 5m_30s zero run diagnosis

Run artifact: `.output/results/htf_ltf_runner_discovery_1d/5m_30s`.

Finding:

```text
run timestamp: 2026-05-31 artifact, pre-P493/P494/P495/P496
days: 1
scanned_htf_rows: 37156
candidates: 37156 ok
entry_window_rows: 148624 ok
signals: 0
raw trades: 0
live-filtered trades: 0
decision_ledger rows: 37156
decision_ledger signal_verdict: 37156 data_dependency_not_ready
top dependency: 36778 contract_seed_aligned_context_not_ready
secondary dependency: 378 post_seed_ltf_missing
targeted fetch errors: 843 total, dominated by DNS getaddrinfo and Binance 429
```

Interpretation: this 1d run is not evidence that `5m_30s` found no trades. It is an old adapter/data run. The shared core never received valid seed-aligned pre-seed context for almost all exact candidates, and a smaller subset lacked post-seed LTF due targeted fetch failures. The 148,624 `entry_windows` are diagnostic windows, not replayed selected trades in targeted subminute mode.

Required next check: rerun a fresh 1d `5m_30s` smoke on current code. Acceptance is not profit; acceptance is collapse of `contract_seed_aligned_context_not_ready` and visible real seed/confirm/category/risk reject reasons or selected signals.

## 2026-06-02 - P496 warning-spam validation

Patch: P496 applied locally / UNKNOWN commit.

Validation target: targeted cache subtraction must not emit pandas `FutureWarning` spam when `aggtrade_coverage_verified` is object-typed or partially missing. Regression test runs the missing-interval helper under `FutureWarning` as error.

## 2026-06-02 - P495 cache subtraction validation plan

Patch: P495 applied locally / UNKNOWN commit.

Finding before patch: post-entry replay was already planned after executable selected signals only; no patch was needed there. The remaining safe speedup was repeated network fetch for windows whose target-LTF buckets were already partially materialized from direct aggTrades.

Validation protocol:

```text
1. Rerun a short targeted discovery over a period with existing partial target-LTF cache.
2. Inspect htf_ltf_runner_targeted_ltf_fetch.csv:
   - cache_subtraction_model present;
   - fetched_window_ms < requested_window_ms for partial-cache rows;
   - target_ltf_exists_covered_requested_window rows for fully cached windows.
3. Confirm decision ledger still uses the same shared-core signal path.
```

Interpretation boundary: cache subtraction reduces IO/network work only. It is not evidence about edge, no-trade outcomes, or market inactivity.

## 2026-06-02 - P494 seed-stage signal-entry prefilter validation plan

Patch: P494 applied locally / UNKNOWN commit.

Hypothesis: after P493 restores HTF pre-seed context, many exact rolling seeds can be rejected by the shared core at seed stage before downloading post-seed confirm/next-open subminute data. This should reduce `signal_entry` fetch volume without changing any selected signal that could pass the shared core.

Validation protocol:

```text
1. Run a short 5m_30s smoke.
2. Inspect htf_ltf_runner_targeted_ltf_plan.csv:
   - planned signal_entry windows;
   - not_planned_seed_stage_terminal_reject count;
   - seed_stage_prefilter_reason distribution.
3. Inspect htf_ltf_runner_decision_ledger.csv:
   - no collapse back to contract_seed_aligned_context_not_ready;
   - normal seed/confirm/category/risk rejects and any selected signals.
4. Do not judge profitability from this smoke unless trades are numerous enough; judge data-loading honesty and funnel visibility first.
```

Forbidden interpretation: lower fetch volume is not edge evidence. It is acceptable only if skipped rows are visible and their reasons are terminal shared-core seed rejects.

## 2026-06-02 - 5m_30s no-signal run diagnosis

Run artifact: `.output/results/htf_ltf_runner_discovery_7d/5m_30s`.

Finding:

```text
htf_rows_after_scan: 180682
entry_window_rows: 722728
entry_window_execution_ok: 492779
ltf_signals_selected: 0
decision_ledger signal_verdict: 180682 data_dependency_not_ready
top dependency reason: contract_seed_aligned_context_not_ready
```

Interpretation: the run did reach exact decision ledger generation, but it did not reach meaningful strategy rejects/selects. This was a data adapter/context bug introduced by targeted acceleration, not evidence that `5m_30s` had no possible entries. P493 fixes the adapter by sourcing pre-seed context from cheap HTF cache.

Next experiment: rerun a short `5m_30s` smoke and inspect `htf_ltf_runner_data_dependencies.csv` plus reject reasons before restarting a multi-hour run.

## 2026-06-02 - Runner discovery zero-trade aggregation failure

Observed failure:

```text
runner discovery 5m_30s: done candidates=180682 signals=0 closed=0 avg_net=0.0000% win_rate=0.00%
pandas.errors.EmptyDataError: No columns to parse from file
```

Interpretation: the long run did finish the `5m_30s` profile, but combined CLI aggregation crashed while reading a zero-row `htf_ltf_runner_trades_raw.csv`. The result is a post-processing/artifact bug, not proof about profitability. The important strategy fact remains: this profile had many candidates but zero selected shared-core signals, so the next analysis should inspect `decision_ledger` reject reasons before changing thresholds.

Patch: P492 makes empty CSV artifacts readable and writes stable headers for new zero-row signal/trade artifacts.

Next experiment: rerun a short smoke or resume the multi-profile command and verify the run writes `htf_ltf_runner_discovery_index.csv` even when one profile has zero trades.

## 2026-06-01 - Backtest acceleration investigation

Question: how to make 30d HTF/LTF runner discovery much faster without lookahead or optimistic bias.

Implementation status: P491 applied locally / UNKNOWN commit.

Finding: the safe target is not a winner filter. It is a data-loading impossibility planner. It may reject only when information available at the historical decision time proves no exact 15s/30s shared-core snapshot can pass. Otherwise it must fetch exact aggTrade-derived LTF and let `PumpDecisionCore` decide.

Current bottlenecks from the local 1d artifacts:

```text
3m_30s: 7,699 pre-entry windows, 46,194 pre-entry minutes; 933 post-entry windows, 59,245.5 post-entry minutes; 954 candidates, 105 signals, 29 live-filtered trades.
5m_30s: 2,872 pre-entry windows, 28,720 pre-entry minutes; 1,160 post-entry windows, 74,820 post-entry minutes; 1,160 candidates, 73 signals, 16 live-filtered trades.
```

Implication: post-entry LTF fetch is currently planned for exact rolling seed candidates before core signal selection. Deferring post-entry fetch until after selected/executable signals should cut post-entry minutes by roughly an order of magnitude on these 1d profiles, without changing signal selection or using future outcome.

Proposed experiment sequence:

```text
1. P491a: split discovery into pre-entry exact replay and post-entry replay fetch after selected/executable signals only.
2. Validate on 1d: selected signal snapshot hashes and signal verdicts must match the old path; only post-entry fetch volume should fall.
3. P491b: add independent HTF/1m coarse impossibility prefilter before pre-entry aggTrade fetch.
4. Validate with synthetic/property tests: any exact selected snapshot must be classified possible by the coarse prefilter.
5. Run 1d/3d smoke before retrying 30d.
```

Explicitly forbidden for this acceleration: future top-growth membership, runner labels, post-entry high/low, trade result, PnL, survival to horizon, or any filter that answers "would this have worked" before exact signal selection.

Implemented changes:

```text
1. Staged targeted fetch: pre-entry seed windows -> signal-entry confirm/next-open windows -> selected-signal post-entry replay windows.
2. New independent 1m coarse prefilter module for seed/confirm upper-bound impossibility checks.
3. Future label assignment for selected trades happens after core signal selection and remains marked as not an entry filter.
4. Exact pre-seed context status check fixed from invalid `window.status.ok` to string status comparison.
```

Validation:

```text
.venv\Scripts\python.exe -m pytest tests\test_runner_discovery_acceleration.py tests\test_pump_decision_contract.py -q
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
```

Remaining experiment: run a small 1d/3d discovery smoke and compare selected signal `snapshot_hash`/`signal_verdict` plus targeted LTF plan minutes before retrying 30d.

## 2026-06-01 - live2 20260601_125722 all-position audit

Run: `.output/results/live2_anomaly_runs/20260601_125722`.

Real opened positions: `NFP/USDT:USDT`, `LITE/USDT:USDT`, `PLTR/USDT:USDT`.

Summary:

```text
NFP: 13:32:02 UTC entry fill 0.01227, qty 984.4; final stop fill 0.01157 at 14:10:17; gross realized -0.68908 USDT, fees 0.01173404, net about -0.70081404.
LITE: 13:38:00 UTC entry fill 853.64, qty 0.01; reduce-only market close 873.05 at 13:40:53; gross realized +0.1941 USDT, fees 0.00690676, net about +0.18719324.
PLTR: 13:38:32 UTC entry fill 160.10, qty 0.07; still open/protected in the inspected status snapshot with stop 157.091415 and latest artifact price about 159.42.
```

Verdict by position:

```text
NFP: execution truthfulness is OK: selected -> actual fill -> stop-trigger child fill -> final close verified. Strategy quality is weak: initial risk was near the 5% cap, pregrowth was already +5.17%, seed was +4.22%, and the trade stopped out. This is more late chase than clean early awakening.
LITE: trade lifecycle closed profitably and actual user-data fills support the PnL. But the supervisor emitted `position_integrity_error` after close: `tp1_full_reduce_only_close_failed` because the close amount was at/below Binance min precision. That halted new entries even though final close was later verified.
PLTR: execution entry/fill/stop were truthful, but signal nature was invalid for Pump Awakening. Pregrowth was -2.07% with zero positive pregrowth steps, so this was dump/rebound, not dormancy -> upward expansion. P489 added `pre_seed_dump_rebound_pattern` to reject this class.
```

Run-level execution status at inspection:

```text
orders submitted: 3
positions protected total: 3
open protected positions: 1 (PLTR)
integrity errors: 1
new entries allowed: false
reason: position_supervisor_not_ready + execution_not_ready
decision_loop_max_elapsed_ms: 11994
decision_loop_overrun_count: 113
artifact writer: ready, no drops/errors
```

Top-growth mismatch: hourly top movers were H/FLNC for 12:00-13:00 and SIREN for 13:00-14:00. None of the three opened positions were hourly top-growth winners, so this run is not evidence that live is catching the strongest real runners yet.

Next: patch execution sizing/supervisor handling for below-min TP1/full-close amounts before trusting the next live run. Then restart with P489 and verify PLTR-like rebounds are rejected by `pre_seed_dump_rebound_pattern`.

## 2026-06-01 - live2 PLTR dump/rebound trade review

Run: `.output/results/live2_anomaly_runs/20260601_125722`.

Trade: `PLTR/USDT:USDT`.

Finding: PLTR should not be treated as pump-awakening evidence. The selected signal was a rebound after pre-seed downside, not quiet dormancy into growth.

Observed from `live2_decision_ledger.csv`:

```text
selected snapshots: 3m_15s and 3m_30s
selected category: A_resonance_prior_spike
seed: 13:34:00-13:37:00 UTC
confirm: 13:37:00-13:38:15/13:38:30 UTC
signal entry: 160.07 / 160.19
actual fill: 160.10, amount 0.07
stop: 157.091415
pregrowth_return_pct: -2.07%
pregrowth_positive_step_share: 0.0
htf_ltf_sustained_flow_ok: true
```

Interpretation: P486 worked on internal seed flow, but PLTR passed because the seed flow itself was sustained. The missing guard was pre-seed nature: the last pregrowth windows were red/down, so the later buy flow was a rebound/short-covering style event. P489 adds `pre_seed_dump_rebound_pattern` to reject this at seed stage.

Additional live issue in the same run: trading was halted by an unrelated LITE integrity error, `tp1_full_reduce_only_close_failed` because the full TP1 close amount was below Binance precision/min amount. That needs a separate execution-sizing patch; it is not the PLTR signal-nature fix.

## 2026-06-01 - live2 Chinese-symbol trade audit

Run: `.output/results/live2_anomaly_runs/20260601_102722`.

Trade: `龙虾/USDT:USDT`.

Signal/execution:

```text
selected snapshot: 3m_15s C_balanced_flow_acceptance
seed: 12:01:00-12:04:00 UTC
confirm: 12:04:00-12:05:15 UTC, 5x15s candles
signal entry: 0.006544
actual entry fill: 0.006552, 1833 qty, order 182667448
initial stop verified: 0.006328834
TP1 target: 0.0067053745
entry age at guard: 270ms
price drift at guard: 0
execution call duration: about 3.2s
```

Core features:

```text
seed return: 2.83%
htf quote ratio: 8.30
htf trade ratio: 5.09
dormancy quote ratio: 10.66
dormancy trade ratio: 6.90
internal seed flow sustained: true
tail quote share: 57.6%
tail trade share: 51.2%
confirm return: 0.785%
confirm quote pace: 5.04
confirm trade pace: 3.37
confirm second-half return: 0.184%
confirm trade acceleration: 1.72
initial risk at decision: 3.29%
```

Management:

```text
TP1 partial close: 916 qty at 0.006685, realized +0.121828 USDT
remaining stop resized, then structurally trailed to 0.006628684
final stop child fill: 917 qty at 0.00662, realized +0.062356 USDT
gross realized from user-data fills: +0.184184 USDT
fees from user-data fills: 0.0121019 USDT
net approximate realized: +0.1720821 USDT
```

Verdict: this was a good live lifecycle test. Signal, actual fill, initial stop, TP1 partial close, stop resize, structural trailing and final flat-close were all visible in artifacts/user-data. However, the final `position_final_close_verified` event failed to recover the final stop-fill PnL because Binance reported the triggered stop as a child market order with a different/sanitized `client_order_id` (`l2sr____...`) and a different exchange order id. P488 fixes recovery by matching strict same-symbol reduce-only SELL child fills after the stop was armed.

## 2026-06-01 - live2 20260601_102722 continued stability check

Run: `.output/results/live2_anomaly_runs/20260601_102722`.

Finding: current state is green, but not perfectly stable.

Observed in the later check:

```text
current runtime gate: all_gates_ready
session runtime gate: about 3994s allowed / 148s blocked (~96.4% trading uptime)
current continuous allowed state: about 1874s
decision_loop_max_elapsed_ms: 1867
decision_loop_overrun_count: 2
total_deadline_missed: 5
selected_count: 0
orders submitted: 0
execution / supervisor integrity errors: 0
market WS current: ticker, aggTrade, mark ready; 4/4 shards connected
private user-data WS current: ready, but had 7 reconnects / DNS failures earlier
```

Blocked-time mix:

```text
all_gates_ready: about 3994s
entry_stream_not_ready + user_data_stream_not_ready: about 64s
decision_latency_degraded: about 60s
entry_stream_not_ready: about 15s
user_data_stream_not_ready: about 10s
```

Interpretation: P487 held up. The old all-symbol deadline flood is not back. The run had one real stream reconnect episode around 11:20-11:21 UTC and a few short latency watchdog holds. Top-growth had one Windows PermissionError, then a later top-growth write completed successfully. Still no selected trades, so this is runtime stability evidence only, not execution or edge evidence.

Next: keep live running through the next hour boundary. If uptime remains above 95% and top-growth PermissionError does not repeat, do not patch. If the PermissionError repeats, patch top-growth writes with retry/backoff around atomic replace. If `decision_latency_degraded` keeps growing, profile the remaining JCT/FLNC confirm-return rejects.

## 2026-06-01 - live2 20260601_102722 post-P487 stability audit

Run: `.output/results/live2_anomaly_runs/20260601_102722`.

Finding: materially more stable than the prior post-P486 run, but still not edge evidence because there were no selected/executed trades.

Observed during audit:

```text
current runtime gate: all_gates_ready
session runtime gate: about 1161s allowed / 15s blocked (~98.7% trading uptime)
decision_loop_max_elapsed_ms: 1464
decision_loop_overrun_count: 0
total_deadline_missed: 1
selected_count: 0
orders submitted: 0
execution / supervisor integrity errors: 0
market WS: ticker, aggTrade, mark ready; 4/4 shards connected
private user-data WS: transport ready, no payload yet because no order/account events
rolling 1m maintenance: running, 713 successes, 0 errors
```

Reject funnel:

```text
ltf_confirm_return_below_min: 22-23 rows, mostly MOVR/JCT
seed_ltf_flow_not_sustained: 11 rows, MOVR/JCT
deadline_missed after signal evaluation: 1 row, JCT
```

Residual issues:

```text
1. One top-growth audit write hit PermissionError on a .tmp -> .csv replace, likely an external file lock or Windows file-access race. It did not block trading, but top-growth artifact durability is not perfectly clean.
2. Startup remains heavy: about 10 minutes for aggTrade warmup and about 10.5 minutes for HTF baseline warmup. This is startup cost, not current trading-loop instability.
3. No selected trades, so execution/fill/stop lifecycle was not exercised in this run.
```

Next: keep this live running long enough to see whether uptime stays near 100% across another hour boundary and whether top-growth write errors repeat. If top-growth PermissionError repeats, patch artifact writes for retry/backoff/atomic replace on Windows.

## 2026-06-01 - live2 20260601_093452 post-P486 uptime audit

Run: `.output/results/live2_anomaly_runs/20260601_093452`.

Finding: P486 improved signal nature filtering, but trading uptime is still constrained by runtime gates.

Observed during audit:

```text
runtime gate current state: all_gates_ready at audit time
runtime gate session time: about 567s allowed / 476s blocked
dominant blocked time: user-data/private stream and entry-stream reconnects, about 342s combined
avoidable code-side blocked time: decision_latency_degraded, about 133s
decision loop max: about 9.3s
deadline misses: single digits, not the old all-symbol flood
dominant signal reject: seed_ltf_flow_not_sustained
```

Interpretation: the remaining code-side uptime loss is not threshold tuning. Seeds that already fail at the seed-nature stage were being re-evaluated across later confirm windows, creating rare but large hot-path stalls. P487 makes seed-stage rejects terminal.

Next validation after P487 restart:

```text
1. decision_loop_max_elapsed_ms should fall materially during JCT/ALPINE-like flow-fade events
2. decision_loop_overrun_count should stop climbing from repeated seed_ltf_flow_not_sustained evaluations
3. runtime gate blocked seconds should mostly be external stream/DNS outages, not decision_latency_degraded
4. if private user-data DNS/reconnect remains the largest block, treat that as an infrastructure/connectivity issue rather than strategy logic
```

## 2026-06-01 - live2 APR selected trade review

Run: `.output/results/live2_anomaly_runs/20260601_084401`.

Finding: APR should not be treated as a clean pump-awakening example. It exposed a missing flow-shape guard.

Observed from live artifacts:

```text
symbol: APR/USDT:USDT
live state: in_position
execution fill: 0.18792
stop: 0.182898505
signal: 3m_15s C_balanced_flow_acceptance
seed return: ~2.06%
htf_trade_ratio: ~5.74
dormancy_to_anomaly_trade_ratio: ~2.87
confirm candles: 5
initial risk: ~2.65%
```

Interpretation: the signal passed the aggregate shared-core checks, but visually and structurally it looked like one 1m impulse with flow fade, not sustained awakening. The correct fix is not changing `5 confirm candles` to `6`; it is making internal seed LTF flow stability part of the core contract. P486 implements this with `seed_ltf_flow_not_sustained`.

Next validation: after restart, inspect `live2_decision_ledger.csv` for `seed_ltf_flow_not_sustained` and the new `htf_ltf_*` flow-shape fields on APR-like setups.

## 2026-06-01 - live2 20260601_080623 post-P484 audit

Run: `.output/results/live2_anomaly_runs/20260601_080623`.

Finding: P484 materially improved runtime, but the run still shows avoidable hot-path spikes.

Observed during audit:

```text
selected_count: 0 execution positions, but 2 core-selected signals reached entry guard
entry guard selected rejects: stale_signal_before_execution on QCOM
total decisions: about 152
deadline missed: about 59
dominant remaining spike: deadline engine 4-8s on minute-boundary seed discovery
latest readiness: all_gates_ready
session seconds: about 70% allowed / 30% blocked
```

Interpretation: all-symbol deadline spam is mostly gone. Remaining blocking is caused by expensive seed discovery/context building when many candles close at once. P485 moves the mandatory seed return check before context construction, so most non-runner windows can be discarded cheaply without changing strategy rules.

Next validation after P485 restart:

```text
1. total_seed_return_gate_rejected should be visible and high during quiet/liquid periods
2. decision_loop_max_elapsed_ms should fall below the degraded threshold on minute boundaries
3. selected core signals should reach entry guard before max_signal_age_ms unless truly delayed by exchange/runtime
4. if overrun persists, cache rolling context/medians incrementally
```

## 2026-06-01 - live2 20260601_071606 post-P483 audit

Run: `.output/results/live2_anomaly_runs/20260601_071606`.

Finding: P483 did not restore acceptable trading uptime. The run is still runtime evidence, not edge evidence.

Observed during audit:

```text
selected_count: 0
data_dependency_not_ready: 0
deadline_missed: ~11k total in status, ~12k grouped in summary
dominant reason: closed_bucket_was_not_evaluated_before_deadline
runtime gate: decision_latency_degraded
session seconds: roughly half allowed / half blocked
artifact writer: ready, no drops
```

Interpretation: the absolute quote/trade/return actionability gate is too broad for liquid symbols. Many buckets are "active" in the market-data sense, but impossible for the seed-first strategy because no rolling seed is pending or newly discovered. P484 adds a seed-possible scheduler gate before deadline sorting.

Next validation after P484 restart:

```text
1. closed_bucket_was_not_evaluated_before_deadline should fall sharply
2. last_cycle.checked_symbols should track pending/new-seed symbols, not liquid-universe size
3. trading uptime should recover if seed discovery itself is cheap enough
4. if degradation remains, measure seed-discovery/core-evaluation cost separately
```

## 2026-06-01 - live2 20260601_064408 post-P482 audit

Run: `.output/results/live2_anomaly_runs/20260601_064408`.

Finding: P482 materially improved data quality visibility but did not yet make the run clean enough for edge conclusions.

Observed during audit:

```text
market streams: ready; artifact writer: no drops
selected_count: 0
data_dependency_not_ready: 0
signal rejects: visible in live2_decision_ledger.csv
runtime gate: intermittently decision_latency_degraded
dominant live failure: thousands of deadline_missed / expired backlog rows
```

Interpretation: the current live is better than `20260601_055306` because it is no longer mostly a missing-context run. The remaining failure is scheduler load: all-symbol dirty candles still reach deadline accounting before quiet buckets are cheaply drained. P483 addresses this by draining `market_quiet_non_actionable` buckets before candidate sorting.

Next validation after P483 restart:

```text
1. total_deadline_missed should fall sharply, especially closed_bucket_was_not_evaluated_before_deadline
2. decision_latency_degraded should stop dominating new-entry gating
3. live2_decision_ledger.csv should show real rejected_signal_contract / entry_guard rows, not mostly deadline loss
4. if selected_count remains zero, analyze reject funnel rather than thresholds first
```

## 2026-06-01 - live2 20260601_055306 runtime audit

Run: `.output/results/live2_anomaly_runs/20260601_055306`.

Finding: this run is not edge evidence. It primarily exposed a runtime architecture problem:

```text
total_decisions ~79k
total_deadline_missed ~57k
selected_count 0
runtime gate: no_new_entries / decision_latency_degraded
dominant dependency: pre_seed_context:live_seed_aligned_context_not_ready
market streams: ticker/aggTrade/mark ready, shards connected, no reconnect storm
```

Interpretation: live2 was evaluating too many all-symbol buckets and generating pending rolling seeds without usable pre-seed context. P482 addresses this with an actionable bucket gate, context-ready seed storage, 1m-backed minute-aligned context, and basic shared-core seed gating.

Next validation after restart: watch `decision_status.engines` for much lower deadline misses, lower dependency spam, and non-degraded runtime gate before interpreting selected/rejected signal quality.

## 2026-05-31 - P481 unit-test validation

Patch under test: P481 shared contract unit tests.

Checks run locally:

```text
.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q
.venv\Scripts\python.exe -m research_tools.decision_contract_guard
.venv\Scripts\python.exe -m compileall data/exchanges research_tools cli constants.py main.py
```

Result: new focused contract tests passed (`8 passed`). The guard and compileall passed. Broader legacy test files are not clean under the current architecture: `tests/test_htf_ltf_runner_discovery.py` has stale helper signatures/expectations and `tests/test_live2_market_watch.py` imports removed live-only signal helpers.

Next validation: migrate those legacy tests to the shared seed-first core instead of tuning strategy thresholds from stale test failures.

## 2026-05-31 - P480 planner speedup validation

Patch under test: P480 cheap confirm upper-bound planner gate.

Next validation:

```text
1. run a small runner discovery over a short window/symbol subset
2. inspect htf_ltf_runner_targeted_ltf_plan.csv
3. compare planned_pairs vs rejected_impossible_confirm_*
4. confirm exact decision ledger still has selected/rejected/data_dependency rows
5. do not compare profitability to older runs without noting the planner/hash contract change
```

Acceptance: planned LTF windows fall only because confirm return/quote pace/trade pace was mathematically impossible under loose HTF upper bounds. Missing next-HTF data must keep pairs rather than reject them.

## 2026-05-31 - P479 multi-timeframe live smoke plan

Patch under test: P479 default live 15s+30s decision streams.

Next runtime validation should verify mechanics before edge:

```text
1. short live smoke with default config; no timeframe flag
2. confirm diagnostics expose decision_status.engines for 15000 and 30000
3. confirm live2_decision_ledger.csv contains 15s and 30s tf_set rows when signals/rejects occur
4. inspect deadline_missed / budget_exhausted separately by timeframe
5. inspect portfolio blocks for duplicate same-symbol signals from nearby 15s/30s snapshots
```

If deadline load rises materially, the next fix is scheduler budgeting/prioritization by active symbols and cheap impossible checks, not threshold tuning.

## 2026-05-31 - P478 15s profile smoke plan

Patch under test: P478 add `5m_15s` and `3m_15s`.

Local validation already run: compile selected changed modules, decision contract guard, and core self-smoke. Next runtime validation should be small and controlled:

```text
1. live2 dry/small-notional smoke with --decision-timeframe-ms 15000
2. matching short htf/ltf runner discovery covering the same symbols/window
3. decision_parity_join by snapshot_hash
4. inspect deadline_missed and data_dependency_not_ready before any signal-quality conclusion
```

Do not tune thresholds from the first 15s result. First question is whether 15s produces honest snapshots without deadline overload.

## 2026-05-31 - P477 contract smoke checks

Patch under test: P477 snapshot hash integrity and live cooldown parity.

Checks run locally:

```text
python research_tools/pump_decision_core.py
python -m research_tools.decision_contract_guard
python -m compileall -q research_tools/pump_decision_core.py research_tools/anomaly_live2/signal.py research_tools/anomaly_live2/execution.py research_tools/htf_ltf_runner_discovery.py research_tools/decision_parity_join.py
```

Synthetic result: adding a non-ok explicit dependency to the same normalized candles now changes `snapshot_hash`, so the previous `same_hash selected vs data_dependency_not_ready` failure is closed. A no-selected confirm sequence now returns a rejected exact confirm snapshot with `ltf_confirm` present.

Next validation: run a small overlapping live/backtest parity smoke and join ledgers by `snapshot_hash`. Do not tune thresholds until `same_snapshot_different_signal_verdict` is zero or explained as a real contract bug.

## 2026-05-30 - P462 live2 all-symbol deadline-load validation protocol

Hypothesis: live2 deadline misses are dominated by expensive full rolling signal evaluation on candidates that already fail exact baseline-free prerequisites. An all-symbol impossibility gate should reduce `closed_bucket_was_not_evaluated_before_deadline` without hiding symbols or changing the trading contract.

Protocol after P462: run live2 for 60-90 minutes with the same universe size. Accept only if every cycle still reports full symbol visibility, `signal_engine.total_baseline_free_prefilter_rejected` increases, `deadline_missed_count` and `budget_exhausted_count` fall materially, and selected/entry-guard/execution rows still contain normal full diagnostics. Inspect `live2_deadline_summary.csv` for new baseline-free reject reasons; they should replace missed deadlines, not selected signals.

If deadline misses remain high, next step is incremental per-symbol rolling aggregates/caches. Do not introduce top-K.

## 2026-05-30 - Live2 deadline load review after Ctrl+C run

Input: uploaded `20260530_155320` live artifacts and uploaded workspace code.

Interpretation: Ctrl+C explains partial final artifacts such as top-growth completion, but it does not explain the earlier stream of `deadline_missed` decisions. The observed bottleneck is sequential per-symbol signal evaluation under a 1.5s deadline after many symbols become actionable on the same 30s bucket.

Rejected fix idea: top-K candidate evaluation. It would violate the live goal of an all-symbol pump radar.

Preferred experiment: measure and then implement all-symbol cheap impossibility classification. Necessary-condition gates must be deterministic and contract-safe, not ranking/capping. Longer-term target is incremental rolling C/A/S state per symbol so the deadline cycle reads precomputed features instead of rebuilding rolling 1m/30s context for every dirty symbol.

P449 validation only: compileall. A separate P450 should address deadline load with timing instrumentation and a no-top impossibility/incremental design.

## 2026-05-30 - P448 live2 stop recovery regression check

Incident: live2 crashed during position supervision when the exchange position was already flat and the protected stop was gone, because the direct flat branch wrote `refreshed_amount` without defining it.

Protocol after P448: run live2 through a stop-trigger/flat-stop-gone transition or synthetic supervisor smoke. Acceptance: no NameError; `position_final_close_verified` is written with `exchange_position_amount` from the fetched flat amount; protected position is removed; realized PnL is present when user-data recovery has the fill.

## 2026-05-29 - P447 live2 data-health smoke plan

Patch under test: P447 rolling 1m context maintenance.

Run: 60-90 minute `run-anomaly-live2` with previous P445/P446/P447 stack. Do not judge profitability if no fills. Judge data health: maintenance status/counters, rolling dependency reasons, decision latency, WS reconnects, artifact writer drops, selected/entry_guard visibility, top-growth completion.

Acceptance gates:
- `rolling_context_maintenance.status in {running, degraded-after-success/ready-equivalent}` and no sustained error loop;
- `rolling_1m_maintenance_source` / status columns present in `live2_symbol_state.csv`;
- `rolling_1m_history_not_ready` and stale 1m context reasons materially lower than the previous run;
- `data_not_ready` remains limited to true data-readiness issues;
- `market_quiet_non_actionable` dominates quiet weak buckets instead of signal-engine rejects;
- no increase in decision loop overruns or WS reconnects attributable to maintenance.

## 2026-05-29 - P446 live2 data health smoke

Question: after removing weak real-trade buckets from the live signal path and enabling rolling 1m repair metadata, do the remaining data-readiness failures represent real missing context rather than routine market quietness or flow fade?

Run: start `run-anomaly-live2` for 60-90 minutes after P446 on the same universe/settings.

Acceptance:

```text
- `deadline_engine.total_data_not_ready` near zero except true invalid stream cases;
- no `real_trade_bucket_for_backtest_parity` in deadline summary/event data;
- grid has many `market_quiet_non_actionable` statuses instead of raw deadline rows;
- `flow_freshness_reject` exists only for threshold-actionable candles whose last trade did not hold into the decision window;
- `signal_engine.rolling_context_repair_status_counts` shows ready/partial/throttled/error when rolling 1m context is missing;
- selected/entry_guard/execution artifacts remain present and undropped.
```

## 2026-05-29 - P445 rolling fetch cost audit

Hypothesis: P444's coverage-safe pair gate is too expensive because it fetches many pairs that can pass the broad seed upper bounds but cannot possibly match the frozen C/A/S entry families. P445 should reduce fetch count by rejecting only mathematically impossible C/A/S pairs and by reusing already trusted target LTF caches before 1s aggTrade download.

Protocol after P445: restart 45d rolling discovery and compare planned/fetched pairs. Accept the speed patch only if `rejected_impossible_runner_category_family` is nonzero and selected signals still satisfy `entry_timestamp_ms >= decision_available_timestamp_ms`, non-empty `runner_candidate_category`, and combined portfolio artifacts are written.

## 2026-05-29 - P444 rolling pair coverage audit

Hypothesis: a rolling backtest can avoid full continuous subminute cache if the 2xHTF fetch planner is a broad safe-superset rather than an optimizer. The planner should skip only pairs where upper bounds prove the official rolling seed is impossible; everything else must be fetched and checked exactly on LTF.

Protocol after P444: run 45d and inspect `htf_ltf_runner_targeted_ltf_plan.csv` for `pair_gate_model=fetch_pair_unless_exact_rolling_seed_is_mathematically_impossible`, rejection counts by reason, planned pair counts, and post-entry exact rolling seed counts. If planned pairs explode, reduce cost only by better impossible-proof bounds, not by adding outcome-like or C/A/S-like filters to the pair gate.

## 2026-05-29 - P443 rolling context purity check

Hypothesis: rolling discovery must separate current rolling HTF window from baseline/dormancy/pregrowth context. Calendar HTF candles may be used as cheap historical context only when their close timestamp is `<= rolling_htf_window_start_ms`.

Protocol: after P443, run 45d and audit selected signals for `entry_timestamp_ms >= decision_available_timestamp_ms`, non-empty C/A/S categories, root-level combined portfolio artifacts, and `rolling_baseline_model=calendar_htf_candles_fully_closed_before_rolling_window_start`.

## 2026-05-29 - P442 rolling combined portfolio audit

Hypothesis: C/A/S was mined and validated only on `3m_30s` and `5m_30s` after excluding weak/missing TF sets. The 45d rolling replay must therefore test the combined strategy on those two profiles only, with one global risk-cap/cooldown pass across both.

Protocol: after P442, use root-level `htf_ltf_runner_combined_trades_live_filtered.csv`, `htf_ltf_runner_combined_portfolio_events.csv`, daily summary, top-dependency, and profitability summary as the primary readout. Per-profile artifacts are diagnostic only.

## 2026-05-29 - P441 rolling C/A/S trigger audit

Hypothesis: rolling discovery should enter on the first closed-LTF signal that also matches one of the frozen C/A/S candidate natures. Generic LTF-confirm without a category is only a near-miss, not a trade.

Protocol: rerun 45d after P441 and compare counts, category mix, daily stability, top dependency, and blocked risk-cap/cooldown events. Do not compare PnL directly with pre-P441 calendar runs.

## 2026-05-29 - P440 rolling discovery correction check

Scope:

```text
Apply after P439 before trusting any 45d rolling discovery result.
```

Why:

```text
P439's exact rolling scan accidentally required baseline/dormancy history inside the short two-HTF LTF slice. That can hide valid rolling seeds. P440 changes exact seed detection to use rolling LTF only for the current candidate window and calendar HTF history for already-known historical context.
```

Checks after run:

```text
- pre-entry planned pairs > 0 when HTF upper-bound sees possible rolling seeds;
- post-entry exact_rolling_seed_candidates is not always zero;
- candidates have rolling_baseline_model=calendar_htf_history_before_rolling_window;
- portfolio_events contains selected/blocked reasons, not silent drops.
```

## 2026-05-29 - P439 rolling 45d discovery validation

Command:

```bash
.\.venv\Scripts\python.exe main.py run-htf-ltf-runner-discovery --days 45
```

Model under test:

```text
rolling HTF seed -> first valid C/A/S category -> next LTF open -> structural exit replay -> portfolio risk cap
```

Fixed candidate priority:

```text
1. C_balanced_flow_acceptance
2. A_resonance_prior_spike
3. S_7d_5m30_strict
```

Portfolio constraints:

```text
risk_per_trade = 2%
max_total_open_risk = 8%
one open trade per symbol
symbol cooldown = one rolling HTF window
blocked signals written to htf_ltf_runner_portfolio_events.csv
```

Acceptance criteria:

```text
- enough closed trades after risk/cooldown filters;
- median net return > 0;
- positive-day share > 55%;
- top dependency remains acceptable after removing top symbols/days;
- portfolio_events shows no hidden capacity/cooldown drops;
- rolling seed artifacts show pair safe-superset -> exact rolling seed -> full replay path.
```

## 2026-05-28 - P438 planned 45d runner/fader OOS validation

Command:

```bash
.\.venv\Scripts\python.exe main.py run-htf-ltf-runner-discovery --days 45
```

Profiles under test:

```text
5m_1m, 5m_30s, 3m_30s, 1m_15s
```

Frozen hypothesis from the 7d readout:

```text
Candidate: htf_trade_ratio >= 12 AND ltf_trade_pace_ratio <= 6
Strict:    htf_trade_ratio >= 12 AND ltf_trade_pace_ratio <= 6 AND htf_quote_ratio <= 48
```

Rationale:

```text
The provided 7d selected/live-filtered artifacts showed that the profitable split was not maximum quote-volume or maximum LTF pace. The better pre-entry pattern was real HTF trade-count expansion with LTF pace still below blow-off. Extreme quote-ratio and overheated LTF trade pace are fader/chase probes, not promotion rules.
```

Acceptance criteria before any live promotion:

```text
- enough closed trades for a real conclusion, target >= 100 across profiles;
- median net return > 0 and sum net return > 0 after fees/slippage;
- positive-day share > 55%;
- top20 winners do not explain almost all positive PnL;
- runner_10pct label share materially exceeds all_selected;
- result is not isolated to one symbol, one day, or one profile;
- future labels remain artifact-only and `uses_future_label_as_entry_filter` stays false.
```

## 2026-05-28 - P437 targeted LTF speed validation

Question: can the 5m/30s runner discovery finish faster with identical strategy semantics by merging overlapping post-entry fetch windows and avoiding broad candidate work outside strict pre-entry HTF seed timestamps?

Design: compare the previous 7d run artifacts against a rerun after P437. The expected change is operational: post-entry `raw_targeted_windows` should remain the audit count of requested replay windows, while `merged_targeted_windows` and fetch rows should drop because overlapping 60m windows are fetched as larger continuous intervals. Candidate construction should mark `strict_seed_gate_applied_before_candidate_build=True`.

Acceptance: runtime and targeted fetch rows fall materially; selected signal/trade logic remains honest (`future_label_available_at_entry=False`, strict wall-clock replay, no entry before decision). If PnL changes, inspect cache coverage and strict path statuses before treating it as a strategy change.

Status: PROPOSED / commit UNKNOWN.

## 2026-05-28 - P434 strict HTF awakening seed gate experiment

Question: can runner discovery stay honest and become operationally usable by loading true 1s/LTF only for stronger closed-HTF awakening seeds, instead of applying arbitrary post-entry fetch caps?

Design: after P432b, require every targeted LTF seed to pass closed-HTF-only thresholds for quote ratio, trade ratio, return, range, dormancy-to-anomaly quote/trade ratios, prior dormancy range, and absolute quote/trade liquidity. Future labels and post-entry prices are not available to the gate.

Acceptance: 7d run should finish without multi-hour post-entry fetch ETA, write non-empty plan/fetch/materialize artifacts, and leave enough selected events for research. If coverage is too low, tune one seed threshold at a time on held-out windows and keep the gate documented in root index/run_config.

Status: PROPOSED / commit UNKNOWN.

## 2026-05-28 - P432b two-stage targeted fetch validation

Experiment status: planned after P432b.

Question: can targeted subminute discovery remain honest while fetching far less 1s data by splitting pre-entry confirmation windows from post-entry hold/runner horizons?

Run: `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 45` after applying P431 then P432b.

Acceptance: `htf_ltf_runner_targeted_ltf_plan.csv` must contain both `pre_entry` and `post_entry` phases. Pre-entry windows should cover only HTF seed start through max confirm + next open. Post-entry windows should exist only for rows selected by `known_at_entry_ltf_confirmation_and_entry_guards_only`. Closed trades must still satisfy strict wall-clock/no-gap replay.

## 2026-05-28 - P431 four-profile HTF/LTF discovery rerun

Experiment status: planned after P431.

Question: does the runner/fader separation survive across upper timeframes when tested on `5m_1m`, `5m_30s`, `3m_30s`, and `1m_15s` with strict wall-clock replay and targeted 1s backfill only around strong seed events?

Preload: `./.venv/Scripts/python.exe main.py update-cache --days 45 --timeframes 1m 3m 5m`. This loads normal OHLCV upper-TF cache only; targeted 1s/subminute fetch remains inside discovery for 30s/15s profiles.

Run: `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 45`.

Acceptance: every profile must report its seed thresholds in the root index and run config. Subminute profiles must have non-empty targeted plan/fetch/materialize artifacts, high strict path coverage, and no gap-hopping trades. Edge acceptance remains positive median/average after fees/slippage, clean runner density materially above base, positive-day share, and limited top-symbol/top20 dependency on a held-out window.

## 2026-05-28 - P430 targeted subminute rerun

Experiment status: planned after P430.

Question: do 5m/15s and 5m/30s have edge when the LTF path is honestly built only around strong suspected HTF events instead of requiring a full historical subminute universe?

Run: `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 45`. Keep default targeted seed gate first: quote ratio >= 12, trade ratio >= 12, HTF return >= 2.27%, HTF range >= 3.0%, max 20 events per symbol.

Acceptance: target profiles must have non-empty targeted plan/fetch/materialize artifacts, high `entry_ltf_path_status=ok` and `post_entry_ltf_path_status=ok` share among planned windows, and enough closed trades to judge day stability. Edge acceptance remains median net > 0, positive expectancy after fees/slippage, clean runner share materially above base rate, and limited top-symbol dependency.

## 2026-05-27 - P429 strict replay rerun required

Experiment status: planned after P429.

Reason: the existing 5m/15s and 5m/30s runner-discovery artifacts are optimistic because sparse LTF rows were treated as consecutive candles during post-entry replay.

Required rerun: after applying P429 and rebuilding continuous subminute cache, run the fixed profile command over the same period. Compare old vs new only through counts, gap statuses, valid closed trades, day stability and top dependency; do not compare old PnL as an edge baseline.

Acceptance: `future_ltf_path_status`, `entry_ltf_path_status` and `post_entry_ltf_path_status` must explain missing rows. Valid PnL can use only closed trades that exited before a gap or had a complete post-entry path.


## 2026-05-27 - 5m/30s win-loss feature analysis

```text
Source:
.output/results/htf_ltf_runner_discovery_30d/5m_30s

Question:
What distinguishes winning trades from losing trades, and which filters improve winrate, median return, top dependency, and positive-day share?

Winner-vs-loser readout:
- Selected winners had much higher LTF quote/trade pace than losers. Median ltf_quote_pace_ratio: 41.3 winners vs 21.0 losers. Median ltf_trade_pace_ratio: 8.51 vs 6.04.
- Selected winners had stronger HTF trade/quote expansion. Median htf_trade_ratio: 10.33 vs 7.87. Median htf_quote_ratio: 18.18 vs 15.47.
- Entry-window winners were more often dormancy-backed: dormancy_ok true share 50.9% winners vs 43.1% losers.
- Entry-window winners had higher HTF-internal concentration and trade acceleration: higher htf_ltf_trade_top1_share, htf_ltf_quote_top1_share, htf_ltf_trade_acceleration.
- Smooth pregrowth was not a win marker here. It was more common in losers on entry-window trades, so "slow smooth accumulation" should not be promoted from this run.
- Simple LTF volume sustain was not enough. `window_volume_sustain_ok` lifted winners only mildly and did not solve median/top dependency alone.

Rules checked on raw entry-window trades with same-symbol overlap filtering:
- Base known rule: ltf_confirm_return_pct >= 0.5236% and dormancy_range_pct_median <= 0.4566% -> 80 trades / 75 symbols, WR 62.5%, median +1.46%, sum +499.7%, top20/sum 0.824, 18/25 positive days.
- Add ltf_quote_pace_ratio >= 17.1 -> 67 trades / 62 symbols, WR 67.2%, median +2.81%, sum +456.3%, top20/sum 0.782, 17/25 positive days.
- Add pregrowth_oi_change_pct <= 0.3947% -> 69 trades / 66 symbols, WR 62.3%, median +2.43%, sum +469.7%, top20/sum 0.807, 16/21 positive days.
- Add htf_ltf_trade_top1_share >= 0.38433 -> 35 trades / 32 symbols, WR 77.1%, median +4.17%, sum +314.6%, top20/sum 0.601, 14/19 positive days.
- Alternative: htf_ltf_trade_acceleration >= 6.06 and pregrowth_return_pct <= 0.0083% -> 58 trades / 51 symbols, WR 62.1%, median +3.85%, sum +427.5%, top20/sum 0.766, 16/21 positive days.

Interpretation:
The tradable shape looks like compressed dormancy plus abrupt real flow/price acceptance. Stronger winners are not defined by a calm smooth OI/price build-up; they look more like sudden awakening where LTF price confirms and HTF-internal activity is concentrated enough to show urgency.

Risk:
All thresholds above are in-sample from this 30d artifact. They are hypotheses only. Validate on a held-out period and on P428's 5m_30s/5m_1m/5m_15s profile set before changing live logic.
```

## 2026-05-27 - P428 5m profile set and daily breakdown artifacts

```text
Patch: P428 changes the fixed HTF/LTF runner discovery set.

New standard profiles:
- 5m_30s
- 5m_1m
- 5m_15s

Removed from standard run:
- 1m_5s, because the 30d artifact had more labels but weak trade quality: WR 44.0%, median -0.18%, strong top dependency.

New artifacts per profile:
- htf_ltf_runner_by_day.csv
- htf_ltf_runner_by_day_live_filtered.csv
- htf_ltf_runner_entry_window_by_day.csv
- htf_ltf_runner_entry_window_by_day_live_filtered.csv

Use these to judge positive-day share, day-level concentration, worst-day damage, and whether a rule is a real strategy or a few large outliers.
```

## 2026-05-27 - 30d HTF/LTF discovery readout and P427 honesty fix

```text
Source artifact:
.output/results/htf_ltf_runner_discovery_30d

Honesty:
- Entry timing looks honest in the run artifacts: future labels are marked unavailable at entry for signals and entry windows.
- Checked rows had no `entry_timestamp_ms < decision_available_timestamp_ms`.
- No evidence of backdated trade entry was found.
- Caveat found: `setup_nature` included future-derived `anomaly_low_broken_after_wakeup`. That did not open trades, but it contaminated category artifacts. P427 removes the future low-break branch from `setup_nature`; rerun before using setup-nature distributions as live-available features.

5m_30s:
- scanned HTF rows 4,743,864; HTF anomaly rows 12,426; clean +10% runners 29.
- selected live-filtered trades: 83 closed / 76 symbols, WR 54.2%, avg +2.99%, median +0.35%, sum +248%.
- top20 winners exceed total net, so top dependence remains material.
- Best next hypothesis from fixed-window research: `ltf_confirm_return_pct >= 0.5236%` and `dormancy_range_pct_median <= 0.4566%`.
- Verified on raw same-symbol filtering: 80 trades / 75 symbols, WR 62.5%, avg +6.25%, median +1.46%, sum +500%, 18/25 positive days, but selected after seeing the 30d result.

1m_5s:
- scanned HTF rows 20,723,117; HTF anomaly rows 14,399; clean +10% runners 239.
- selected live-filtered trades: 518 closed / 233 symbols, WR 44.0%, avg +0.66%, median -0.18%, sum +339%.
- strong top dependence; sustained-flow labels lift runner share but do not produce robust PnL.

Conclusion:
No live-ready edge yet. The next research path is 5m/30s, not 1m/5s: validate the confirm+tight-dormancy rule on a held-out period after P427, with setup_nature kept live-only and future labels used only for evaluation.
```

## 2026-05-27 - P426 fixed-window entry research upgrade

```text
Patch: P426 adds fixed closed-LTF entry-window artifacts to HTF/LTF runner discovery.

Purpose:
- make the next 30d run answer whether early sustained flow can separate runners from fader spikes;
- keep the replay honest by making every window decision after closed LTF candles and entering only at the next LTF open;
- keep future +10% labels as evaluation labels only.

New artifacts per profile:
- htf_ltf_runner_entry_windows.csv
- htf_ltf_runner_entry_window_trades_raw.csv
- htf_ltf_runner_entry_window_trades_live_filtered.csv
- htf_ltf_runner_entry_window_rule_scores.csv
- htf_ltf_runner_entry_window_rule_scores_live_filtered.csv

Research questions for the next run:
- Does `volume_sustain` beat `fader_decay_under50` on avg/median/sum net return after fees/slippage?
- Does requiring current quote-volume >= prior 24h spike median or >=150% median improve runner capture without killing sample size?
- Does OI non-negative from pregrowth end help, or is it only a diagnostic on this market/cache?
- Are wins distributed across symbols/time, or mostly top20 trades?
- Do both fixed profiles agree, or is the apparent edge TF/cache-specific?

Validation already run:
- `.\.venv\Scripts\python.exe -m pytest tests\test_htf_ltf_runner_discovery.py tests\test_live2_market_watch.py -q` -> 44 passed.
- `.\.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py` -> passed.
- BSB 7d 5m/30s profile smoke wrote entry-window artifacts and closed 12 executable fixed-window trades.

Next command:
.\.venv\Scripts\python.exe main.py run-htf-ltf-runner-discovery --days 30
```

## 2026-05-27 - 7d 5m/30s runner decay research and P425 validation

```text
Source artifact:
.output/results/htf_ltf_runner_discovery_7d/5m_30s

Baseline run:
- Runtime: 8571.266s for 581 symbols.
- Scanned HTF rows: 935824.
- HTF anomaly gate rows: 2484.
- Candidate CSV size: 596MB because rejected/non-anomaly rows were written too.
- Future +10% labels among strict HTF anomaly rows: 15/2484 clean runners, 0.604%.
- Live-filtered trades: 41 closed / 38 symbols, WR 56.1%, avg +1.15%, median +0.35%, sum +47.1%.
- Runner capture in selected trades: 1/41, so current entry path is profitable continuation harvesting on this slice, not robust runner identification.

Decay research artifacts:
.output/results/htf_ltf_runner_discovery_7d/5m_30s/runner_decay_research/
- runner_decay_enriched_anomalies.csv
- runner_decay_rule_scores.csv
- runner_decay_feature_bins.csv
- runner_decay_research_summary.csv

Findings:
- 30s post-close coverage is too sparse for a strong conclusion: post_ltf_status ok for 240/2484 anomaly rows; 4 closed 30s candles available for only 72/2484.
- `post4_sustained_flow` had 11 events / 10 symbols / 1 clean +10% runner, clean-runner share 9.1% versus 0.6% baseline, but sample is too small.
- `post4_sustain_150pct_prior_median` had 10 events / 1 runner, suggesting current spike above prior spike median may help, again low sample.
- `post4_decay_under50` still had 3/49 runners, so one <50% volume-decay print alone is not a clean fader reject; price acceptance and later recovery matter.
- OI did not separate runners in this artifact: runner anomalies mostly had `pregrowth_oi_change_pct=0.0`, so OI should be a positive/negative context only when it is actually moving and available.

P425 speed validation:
- Changed runner discovery to apply cheap HTF anomaly gate before expensive future-label/LTF/OI work and before candidate artifact writes.
- BSB 7d 5m/30s smoke preserved 24 candidates / 6 clean runner labels, funnel scanned=1943 and rejected-before-artifact=1919.
- BSB runtime after patch: 2.36s; candidate CSV: 17KB.
- Tests: `python -m pytest tests/test_htf_ltf_runner_discovery.py tests/test_live2_market_watch.py -q` -> 42 passed.
- compileall passed for data/exchanges research_tools cli constants.py main.py.

Strategy implication:
Do not trade first HTF spike alone. Research next should test: HTF anomaly + current spike >= prior 24h spike median + early LTF no-decay/sustain + non-negative price acceptance + OI not falling. This must be validated on refreshed 5m/30s and 1m/5s runs before live promotion.
```

## 2026-05-27 - P418 compact progress validation plan

```text
Patch: P418 compact HTF/LTF runner discovery progress.

Expected terminal behavior:
- one rewritten progress line per profile in an interactive terminal;
- progress includes processed/total, percent, current symbol, and ETA;
- each profile prints one final summary line;
- non-interactive logs are throttled to start/end progress lines plus final summaries.

Validation to run:
- `python -m pytest tests/test_htf_ltf_runner_discovery.py tests/test_live2_market_watch.py -q` -> 41 passed;
- `python -m compileall -q data/exchanges research_tools cli constants.py main.py` -> passed;
- small two-symbol module smoke showed compact start/end progress lines and one final summary line;
- full `main.py run-htf-ltf-runner-discovery --days 1` smoke was stopped after the short validation timeout because it began scanning the full cache.
```

## 2026-05-27 - P417 same-symbol-only discovery filter

```text
Patch: P417 remove portfolio-wide cap from HTF/LTF runner discovery.

Contract:
- Different symbols may be open at the same time in the discovery replay.
- A symbol may not open a second simulated position while a prior position on the same symbol is still open.
- The artifact column `parallel_other_symbol_positions_at_entry` is diagnostic only; it is not a cap.

Validation to run:
- `python -m pytest tests/test_htf_ltf_runner_discovery.py tests/test_live2_market_watch.py -q` -> 41 passed;
- `python -m compileall -q data/exchanges research_tools cli constants.py main.py` -> passed;
- search confirmed no `max_open_positions`/portfolio-cap code remains in `htf_ltf_runner_discovery`.
```

## 2026-05-27 - P416 fixed TF-set command plan

```text
Patch: P416 fixed runner discovery TF profiles.

Command contract:
- `run-htf-ltf-runner-discovery --days N`
- no symbols, output, timeframe, threshold, risk, or trailing flags on the operator command;
- code runs `5m_30s` and `1m_5s` profiles under `.output/results/htf_ltf_runner_discovery_<N>d/`.

Validation to run:
- help output exposes `--days` only for this command;
- `python -m pytest tests/test_htf_ltf_runner_discovery.py tests/test_live2_market_watch.py -q` -> 40 passed;
- `python -m compileall -q data/exchanges research_tools cli constants.py main.py` -> passed.
```

## 2026-05-27 - P415 HTF/LTF runner discovery scoring validation

```text
Patch: P415 complete runner discovery scoring.

What changed:
- Added pre-entry candidate rule scores for HTF anomaly, dormancy, smooth pregrowth, actual OI growth, strong HTF flow, and sustained HTF-internal LTF flow.
- Added trade rule scores with net-PnL winrate, avg/median/sum net return, MFE/MAE, clean-runner label share, balance score, and top20 positive-PnL dependency.
- Added `htf_ltf_runner_research_shortlist.csv` to rank research-only rule candidates without using future labels as entry filters.
- Added HTF-internal LTF distribution/acceleration fields to separate sustained flow from single-print noise.
- Marked the run as cache-only/no exchange fetch in run_config and honesty report.

Validation:
- `python -m pytest tests/test_htf_ltf_runner_discovery.py tests/test_live2_market_watch.py -q` -> 40 passed.
- `python -m compileall -q data/exchanges research_tools cli constants.py main.py` -> passed.
- `python main.py run-htf-ltf-runner-discovery --symbols ZEC/USDT:USDT CATI/USDT:USDT --days 1 --htf-timeframe 5m --ltf-timeframe 1m ...` -> completed with 0 candidates/0 signals and valid empty artifacts.

Next experiment:
Run the broad 30d 5m/1m cache-only command, then inspect `htf_ltf_runner_candidate_rule_scores.csv`, `htf_ltf_runner_trade_rule_scores_live_filtered.csv`, `htf_ltf_runner_research_shortlist.csv`, and data-quality artifacts before interpreting profitability.
```

## 2026-05-27 - live2 run 20260526_175528 runner/noise audit and P414 discovery smoke

```text
Run: .output/results/live2_anomaly_runs/20260526_175528

Runtime finding:
- Crash cause was code, not exchange/network: `_same_symbol` was undefined in stop-close user-data recovery.
- At crash, execution_status showed two protected positions: CATI partial runner remainder and TST initial-stop protected.
- CATI had a private stop fill in user_data_order_trade_update; recovery crashed before final close accounting.

Trade readout from live artifacts plus Binance public 1m klines after entry:
- 15 opened positions found: GWEI, DEXE, UAI, 1000LUNC, NIL, GTC, AGT, CHIP, PRL, KAITO, MU, XAN, BLUAI, CATI, TST.
- No symbol reached +10% from actual entry within 1h or 2h in this replay.
- CATI was the only live-managed runner state: TP1 partial, two structural stop trails, then stop fill.
- UAI had the largest later continuation, about +6.3% within 1h, but it is not a +10% runner.
- Noise traits were common: early OI-down/exhaustion, seller pressure after MFE, high stall, flow collapse, or structural stop/anomaly-low break before any large continuation.

Filtering hypothesis:
- Do not call this run proof of runner edge; there were no true +10% runners.
- For runner discovery, require dormancy + coordinated HTF flow/trades + smooth pre-pump price/OI growth, then wait for closed LTF acceptance.
- Explicitly label whether anomaly low was broken after the HTF anomaly; broken-low cases should be separated from clean runners before optimizing entries.

P414 discovery smoke:
- `python -m research_tools.htf_ltf_runner_discovery --symbols ZEC/USDT:USDT CATI/USDT:USDT --days 1 ...` completed and wrote artifacts.
- A loose synthetic smoke generated closed trades and confirmed no-TP structural stop/trailing artifact shape.
- Focused tests passed for future-label separation, next-LTF-open entry availability, no-TP replay, and live2 stop-recovery symbol matching.
```

## 2026-05-26 - P413 live2 multi-position / stop-PnL validation

```text
Patch: P413 unlimited live2 positions and stop-close PnL recovery.

Validation already run:
- tests/test_live2_market_watch.py: 34 passed;
- compileall passed for data/exchanges research_tools cli constants.py main.py.

Live validation required after restart:
- confirm `execution_status.max_open_positions_unlimited=true`;
- confirm two different symbols can be protected concurrently while same-symbol duplicate is still rejected;
- on a stop exit, check nearby `user_data_order_trade_update` event and `position_final_close_verified` have matching stop client/order id;
- Telegram must show actual recovered PnL, or `PNL: n/a` if the private fill event is unavailable. It must not show `+0 USDT` for an unrecovered stop.
```

## 2026-05-26 - P412 runner shape gate validation plan

```text
Patch: P412 runner-shape gate for rolling runner categories.
Purpose: reduce first-spike/single-print noise seen in live2 run 20260526_120454 while keeping live/backtest category parity.

Rule under test: after dormancy, useful candidates should show coordinated expansion in range, quote volume, and real number_of_trades across the full rolling 60s setup, with the second 30s stronger than the first 30s. A setup dominated by one quote-volume print is rejected.

Required validation:
- run rolling 1m/5s category-profile backtest with P412;
- inspect skip/reject reasons for runner_shape_* distribution;
- compare rejected symbols against later hourly/top-growth runners;
- restart live2 only after confirming reject volume is interpretable and not caused by missing 5s/1m baseline fields.

Risk: stricter gate can miss AZTEC-like delayed runners where acceleration is only moderate before the later leg. If that happens, the correct next patch is a stateful delayed-acceptance candidate mode, not a blind threshold loosen.
```

## 2026-05-26 - live2 run 20260526_120454 selected trade runner audit

```text
Run: .output/results/live2_anomaly_runs/20260526_120454
Symbols requested: HIGH, VVV, FF, OPG, NAORIS, AZTEC, BLUAI, IN.
Artifact: selected_trade_1m_runner_audit.csv.

Executed positions found: BLUAI, AZTEC, NAORIS, OPG, FF, VVV x2, HIGH. IN had deadline/reject rows but no artifact-confirmed position in this run.

Replay method: position events from live2_events.csv plus Binance public 1m klines from 90m before entry to 2h after entry. Local 5s parquet cache was stale for these timestamps, so it was not used for post-entry path claims.

Readout:
- AZTEC: live stopped/finalized after ~30s, but later 2h high was +5.9% from fill; waiting 2m would have had about +8.1% max. Looks like a missed delayed runner.
- OPG: live early-exited at loss, but later 2h high was +5.2% from fill and +6.1% after exit; waiting 1-10m still showed +6% area. Strong delayed-runner candidate.
- VVV: two live entries. Both had later +2.2% to +3.5% possible after waits; second entry had stronger acceleration shape than first.
- BLUAI: small but real post-exit upside; later high exceeded original TP area. Potential runner only if confirmation waited and adverse stayed small.
- FF: very strong flow expansion and later +4-6% possible after waits, but original structural TP was wide and delayed adverse was meaningful. Treat as hot-flow candidate, not clean proof.
- HIGH: reached about the original TP area but did not clearly become a large runner; early OI-down exit may be too aggressive for this shape, but evidence is mixed.
- NAORIS: mostly noise; no clean runner after entry.
- IN: no fill; rejects were mostly quote-ratio/price-retention failures and later path was weak-to-negative from early rows.

Pattern hypothesis: better runners tend to show dormancy then simultaneous acceleration in range/volatility, quote volume, and trade count. The useful shape is not just quote-volume spike; it is all three dimensions moving from flat baseline into p/P expansion. This supports stricter category confirmation or delayed acceptance before entry.
```

## 2026-05-26 - P408 live2 partial-runner validation plan

```text
Patch: P408 partial TP1 + OI/flow-speed management.
Purpose: validate real live behavior after TP1 no longer flattens the whole position.

Required first run checks:
1. New entries use about 12 USDT notional unless explicitly overridden.
2. TP1 emits `position_tp1_partial_close_verified`, not full close, and the protected position remains with `status=tp1_partial_protected_stop_verified`.
3. The partial TP event shows old stop id, new stop id, remaining exchange amount, and actual reduce-only fill.
4. Later remainder outcomes separate `position_early_exit_full_close_verified`, `position_structural_stop_trail_verified`, and final stop settlements.
5. Analyze OI fields: entry 5m OI, current 5m OI, `post_entry_oi_change_pct_from_entry_5m`, and source-flow quote/sec/trades/sec.

Interpretation rule: this is execution/management validation first. OI is coarse Binance 5m history, so do not claim liquidation detection from it without a lower-latency source.
```

## 2026-05-26 - P405 rolling post-HTF live2 validation plan

```text
Patch: P405 rolling HTF update after P404.
Purpose: validate that live2 now trades the post-HTF acceptance category without calendar-minute anchoring and without expensive full-market 75m 5s warmup.

Required first run checks:
1. `startup_htf_baseline_warmup_completed` exists and shows raw 1m HTF baseline candles loaded.
2. Selected/rejected post_htf rows show `post_htf_acceptance_htf_alignment=rolling_60s_5s_step` and `post_htf_acceptance_htf_calendar_aligned=false`.
3. Any OI-up/price-down case is rejected with `oi_up_price_down_blocked`.
4. Actual orders, if any, use 6 USDT notional unless explicitly overridden.
5. Analyze P405 forward rows separately from P404/calendar-minute assumptions.

Interpretation rule: baseline is real 1m kline flow baseline, not rolling 5s baseline. This is the deliberate cost/parity tradeoff to avoid loading universal 1s/5s history.
```

## 2026-05-26 - P404 live2 post-HTF acceptance long validation plan

```text
Patch: P404 live2 post_htf_acceptance_long enabled.
Purpose: forward-validate the best long post-HTF acceptance candidate through real live2 entry guards and exchange execution path.

Required first run checks:
1. Confirm `live2_events.csv` has selected/rejected rows with `category_id=post_htf_acceptance_long` or `post_htf_acceptance_artifact_mode=post_htf_acceptance_long`.
2. Confirm selected rows used closed HTF timestamps and exactly 6 post-close closed 5s candles.
3. Confirm entry_guard accepted/rejected from live price, not signal price reuse.
4. Confirm actual entry fill, stop order id, stop price, and integrity status are present for any real order.
5. Analyze post_htf rows separately from runner_oi_confirmed/runner_flow/runner_balanced.

Interpretation rule: first live run is safety/parity validation only unless enough real fills accumulate. Do not infer edge from near-misses or one/two trades.
```

## 2026-05-26 - bare HTF short/fader 30d category audit

```text
Run: .output/results/bare_htf_short_discovery_30d_1m_5s
Mode: 1m/5s bare_htf_short_fader, 30d, strict HTF prefilter quote>=10/trades>=8/HTF return>=1.5%, RR2.5 fixed short, adverse 0.05% entry/exit slippage, fees 0.04% per side.

Quality: honesty report has 0 failures, but universe scope is cache_snapshot_scan, so survivorship/listing bias remains. Flow is real: trade_count_proxy_rows=0; entry flow source is cached_1s_aggregated_to_5s number_of_trades/quote_volume. Targeted 1s plan: 167930 coarse candidates, 166845 dropped by strict prefilter, 1085 targeted windows, 1040 ready, 45 fetch_not_ok.

Funnel: 4427 unique HTF anomaly events / 5553 rows; only 215 events had full post-close LTF windows and short-pressure triggers; 215 raw closed trades; 191 live-filtered closed trades plus 24 max-position skips.

Baseline result: live-filtered all trades are not tradable: 191 closed, sum_net -120.34%, avg -0.63%, median -1.15%, WR 29.8%, stop rate 67.5%.

In-sample category discovery found broad post-close pressure motifs, but most `ltf12_*` positives are not executable at the original early fill because the 12-candle features are only known after 60s. Delayed-entry replay shows the broad `ltf12_red>=50 & taker<48` motif turns negative if entry waits until 12 candles close.

Most honest executable candidate from this run: wait 6x5s after HTF close / trigger, require ltf6_red_share>=66% and delayed risk<1.5%, then RR2.5 short. Replay: 23 trades, 19 symbols, 13 days, WR 52.2%, avg +0.52%, median +0.25%, sum +11.86%, PF 1.89, top5 positive-profit share 60.7%, max-symbol positive-profit share 21.0%, first half +6.78%, second half +5.08%. This is a weak but plausible research candidate, not live-ready.

Small stronger motifs: lower_high_close_down + early red pressure remains interesting but only 10-12 trades; close_below_htf_close + weak taker has 16 trades but top dependence is high. These are watchlist only.

Exit readout: all-trades RR/BE/time-exit variants remain negative. On the candidate subsets, full RR2.5/RR3 works better than RR1.0-1.5 and partial exits; BE generally reduces median or winrate. Early time exits raise winrate but reduce expectancy. Current best interpretation: if short is taken, it needs room for a 2.5R runner and strict pre-entry risk cap; do not scalp it at 1R by default.
```

## 2026-05-26 - one-print/downtrend short-fader audit

```text
Run: .output/results/bare_htf_short_discovery_30d_1m_5s
New artifacts:
- short_fader_category_analysis/one_print_downtrend_features.csv
- short_fader_category_analysis/one_print_downtrend_rule_audit.csv
- short_fader_category_analysis/one_print_downtrend_delayed6_rule_audit.csv

Question: whether a coin in downtrend with a single concentrated HTF up-print is a better short setup, consistent with short-cover / liquidation squeeze rather than organic pump awakening.

Method: for each live-filtered event, read the cached 5s candles inside the anomalous 1m HTF candle and compute top-5s concentration of quote volume, trade count, and absolute return; taker-buy share on the top quote candle; total taker-buy share; plus pretrend from cached 1m/5m candles before the anomaly. Then evaluate current-fill and honest delayed6 replay categories.

Result: downtrend alone is bad, one-print alone is bad, and one-print+downtrend is still bad/fragile. Examples: one_score>=0.55 had 33 trades, sum -29.2%; quote_top1>=0.60 had 41 trades, sum -32.2%; pre1m30_down had 39 trades, sum -34.6%; one_score>=0.55 & pre1m30_down had 11 trades, sum -18.6%.

Useful refinement: one-print only helps when paired with immediate post-close LTF weakness. Honest delayed6 replay:
- one_score>=0.45 & ltf6_taker<48: 32 trades, 25 symbols, WR 50.0%, avg +0.38%, median ~0.0%, sum +12.3%, PF 1.42, top5 positive share 63.3%.
- quote_top1>=0.50 & ltf6_taker<48: 34 trades, WR 50.0%, avg +0.23%, median ~0.0%, sum +7.7%, PF 1.26.
These are weaker than the prior delayed6 candidate `ltf6_red_share>=66% & delayed risk<1.5%`.

Interpretation: the liquidation/short-cover story is plausible as a label, but not proven from available data. AggTrades show aggressive buy flow, not whether it is short close versus new long. OI is 5m/as-of and not available fast enough for a 30s entry. Without liquidation feed or lower-latency OI delta, do not market this as liquidation detection. Treat it as "concentrated one-print upthrust that fails to attract follow-through".
```

## 2026-05-25 - P403 short/fader strict prefilter validation plan

```text
Patch: P403 stricter short/fader HTF anomaly prefilter.
Reason: a 30d 1m/5s full-market short/fader discovery began targeted 1s flow with a multi-thousand-minute ETA, meaning the flow-only coarse anomaly set was too broad.

Validation: compileall; focused short_fader tests; small explicit-symbol smoke. Inspect targeted_flow_plan.csv for short_fader_prefilter_dropped, thresholds, and reduced coarse_prefilter_candidates before running the larger 30d discovery. If still too expensive, raise --short-fader-prefilter-min-htf-return to 0.02 or 0.03.
```

## 2026-05-25 - P403 short/fader strict prefilter smoke result

```text
Run: .output/results/bare_htf_short_fader_p403_smoke
Command shape: explicit BEAT/INJ, 2 days, 1m/5s, pair_collection_mode=bare_htf_short_fader, render_charts=false, short_fader_analysis_minutes=5, max_hold_candles=60.

Result: compileall passed; focused short_fader tests passed. targeted_flow_plan.csv: 23 coarse candidates, 21 dropped by the new short_fader prefilter, 2 coarse_prefilter candidates, 2 targeted windows, 2 ready windows. Honesty report: 0 failures. Final signals on this tiny strict smoke: 0.

Interpretation: P403 solved the cost-control problem on the smoke slice. It does not prove or disprove the short edge.
```

## 2026-05-25 - P402 short/fader honesty revalidation plan

```text
Patch: P402 short/fader decision availability contract.
Issue found: bare_htf_short_fader trigger rows inherited decision_available_timestamp_ms=HTF close while decision_timestamp_ms was the post-close LTF trigger candle open. The standard timestamp_semantics tokens required by the honesty checker were also missing.

Required checks: compileall; focused short_fader tests; rerun a small 1m/5s short/fader smoke. Inspect anomaly_backtest_honesty_report.csv and require candidate availability / baseline checks to pass before interpreting PnL or category artifacts.
```

## 2026-05-25 - P402 short/fader honesty smoke result

```text
Run: .output/results/bare_htf_short_fader_p402_smoke
Command shape: explicit BEAT/INJ, 2 days, 1m/5s, pair_collection_mode=bare_htf_short_fader, render_charts=false, short_fader_analysis_minutes=5, max_hold_candles=60.

Result: compileall passed; focused short_fader tests passed; smoke completed. anomaly_backtest_honesty_report.csv now shows OHLCV cache loader availability ok and baseline calculations ok with 0 failures. Funnel: 142 candidate rows / 76 HTF events; 81 post-close LTF ok rows / 15 events; 15 discovery signals; 15 raw closed trades; 14 live-filtered closed trades. Tiny smoke PnL remained negative: sum_net -2.60%, winrate 33.3%.

Interpretation: P402 fixes artifact truthfulness for decision availability. It does not prove a short edge.
```

## 2026-05-25 - P401 short/fader category analysis smoke

```text
Run source: .output/results/bare_htf_short_fader_expanded_smoke
Command: python -m research_tools.short_fader_category_analysis --run-dir .output/results/bare_htf_short_fader_expanded_smoke --min-events 1 --min-live-trades 1

Result: completed and wrote short_fader_category_analysis/{summary,event_category_matrix,rule_scores,category_candidates,category_watchlist}. Smoke readout: 15 analysis rows, 15 unique events, 15 raw/live trades, 63 rule rows. This only validates the analyzer shape on a tiny sample.
```

## 2026-05-25 - P400 expanded short/fader discovery smoke

```text
Run: .output/results/bare_htf_short_fader_expanded_smoke
Purpose: verify expanded trigger library, compact post-close path slices, and heuristic decay category artifacts.

Result: completed. Default trigger list includes failed_new_high, taker_fade_red, close_below_htf_close, close_below_post_mid, lower_high_close_down, effort_no_progress, pullback_without_recovery. Funnel: 142 candidate rows / 76 unique HTF events; 81 triggered rows / 15 triggered events; 15 discovery signals; 15 raw closed trades; 14 live-filtered closed trades.

Artifacts confirmed: bare_htf_short_post_close_path_slices.csv, bare_htf_short_decay_category_events.csv, bare_htf_short_decay_category_summary.csv. Tiny smoke by-trigger readout is not edge evidence.
```

## 2026-05-25 - P399 wide bare HTF short/fader discovery smoke

```text
Run: .output/results/bare_htf_short_fader_discovery_smoke
Purpose: verify wide discovery behavior after removing the default prior crowding/fade gate and disabling RR grid by default.

Result: completed. run_config.csv records short_fader_require_prior_context=False and short_fader_run_exit_grid=False. Funnel: 79 candidate rows / 76 unique events; 18 post-close-window-ok rows / 15 events; 16 triggered rows / 13 events; 13 discovery signals; 13 raw closed trades; 10 live-filtered closed trades.

Interpretation: plumbing is now suitable for broad discovery artifacts. The tiny INJ/BEAT smoke was negative (13 raw closed, sum_net -6.68%, winrate 7.69%) and is not an edge test.
```

## 2026-05-25 - P398 bare HTF short/fader smoke

```text
Run: .output/results/bare_htf_short_fader_smoke
Command shape: explicit INJ/BEAT, 2 days, 1m/5s, pair_collection_mode=bare_htf_short_fader, render_charts=false, short_fader_analysis_minutes=5, max_hold_candles=60.

Result: command completed. run_config.csv records feature_contract=bare_htf_short_fader_v1, pair_collection_mode=bare_htf_short_fader, execution_model=next_bar_open_proxy_latency_1_slip_entry_0.0005_exit_0.0005_bare_htf_short_fader, slippage_model=adverse_short_entry_and_exit, valid_backtest=True, backtest_verdict=research_only.

Artifact check: bare_htf_short_candidates.csv has 79 rows; bare_htf_short_triggers.csv exists; strict prior crowding/fade signal filter produced 0 final signals on this tiny INJ/BEAT window. This is a plumbing smoke, not an edge test.
```

## 2026-05-25 - planned honest bare-HTF short/fader backtest

```text
Goal: build a separate honest short/fader research path from bare closed HTF anomalies, not from long continuation categories.

Protocol:
1. Detect closed HTF anomaly N using only live-available HTF fields and real quote_volume/number_of_trades provenance.
2. Start executable short research only after HTF N close.
3. Build post-close LTF feature windows inside the first 60 minutes after N close, plus clearly marked left-context features available by then.
4. Label outcomes from actual post-close executable time: short MFE/MAE, clean short2, long continuation adverse, time-to-low, time-to-stop, and missed/no-trigger cases.
5. Search factors independently of long categories: prior crowding/fade, first-window LTF weakness, failed high, taker fade, effort/no-progress, range position, volume/trade-count decay or continuation.
6. Simulate only next-LTF-open entries after a trigger, with adverse slippage, fees, structural stop, max hold, cap-1 portfolio filter, skip reasons, and top-dependency artifacts.
7. Compare variants by expectancy, median trade, winrate, trade count, top5/top15 dependency, per-symbol/month/session distribution, and reject funnel before any live claim.

Initial candidate contract to validate:
closed HTF anomaly + prior_spike>=10 or prior_fast_fade>=3 + failed_new_high/taker_fade_red + next-5s-open short + structural stop + full RR2.0-2.5.

Hard rejection: do not derive short buckets from discovery/runner long categories. Those can be reported as diagnostics only, not as entry classes.
```

## 2026-05-25 - P394 validation plan

```text
Patch: P394 post-HTF LTF left-context parity.
Purpose: verify that post_htf_close_ltf_confirmation uses LTF candles left of the anomalous HTF candle for decision context while keeping entry no earlier than HTF close.
Required checks: compileall; focused anomaly continuation tests; small 5m/1m post-HTF run with targeted flow enabled. Inspect targeted_flow_plan.csv window_before_ms, candidate post_htf_close_ltf_left_context_* columns, prior_up_down_whipsaw_source=entry_timeframe_left_context, and entry_timestamp_ms >= post_htf_close_entry_not_before_ms.
Do not compare old/new profitability directly: this changes the candidate/data contract and can reject setups that previously lacked LTF left context.
```

## 2026-05-25 - anomaly_lab post-HTF run audit

```text
Run root: .output/results/anomaly_lab
TF folders: 1m_5s, 1m_15s, 5m_30s.
Question: whether the current run proves post_htf_close_ltf_confirmation behavior and whether the mode can be extended to wait for LTF confirmation after HTF N close.

Finding: run_config.csv in each TF folder says pair_collection_mode=post_htf_close_ltf_confirmation and execution_model has the post_htf suffix, but anomaly_candidates.csv shows feature_contract=htf_setup_ltf_entry_v1 and candidate_collection_policy=all_ltf_decisions_per_setup. Candidate rows do not show the post-HTF contract. This run therefore used the forming collector path despite the config label and should not be interpreted as a post-HTF-close result.

Useful raw readout only as forming-style reference: 1m_5s live-filtered 45 closed / sum_net +16.43%; 1m_15s live-filtered 19 closed / +9.07%; 5m_30s live-filtered 31 closed / +27.99%. All still have cache_snapshot_scan survivorship risk and are not post-HTF evidence.

Next: rerun after P394 collector fix, then inspect feature_contract=post_htf_close_ltf_confirmation_v1, candidate_collection_policy=single_post_htf_close_ltf_confirmation_per_setup, post_htf_close_ltf_left_context_status, and entry_timestamp_ms >= post_htf_close_entry_not_before_ms before judging PnL.
```

## 2026-05-25 - P395 forward-confirmation validation plan

```text
Patch: P395 post_htf_close_ltf_forward_confirmation.
Purpose: test "HTF N showed interest, then LTF confirms after N close" without retroactive entry inside N.
Required first run: explicit small 5m/30s or 1m/5s slice with --pair-collection-mode post_htf_close_ltf_forward_confirmation and render_charts=false.
Inspect before PnL: feature_contract=post_htf_close_ltf_forward_confirmation_v1, candidate_collection_policy=all_ltf_forward_confirmations_after_post_htf_close, post_htf_close_ltf_left_context_status=ok, post_htf_close_ltf_forward_confirmation_candles >= confirmation_candles, entry_timestamp_ms > post_htf_close_entry_not_before_ms.
Interpretation: this is a late-confirmation mode, not live2 parity. Compare separately against forming live-parity runs.
```

## 2026-05-25 - post-HTF forward 1m/5s readout

```text
Run: .output/results/anomaly_lab_post_htf_forward_1m_5s
Contract check: ok. Candidates use feature_contract=post_htf_close_ltf_forward_confirmation_v1, candidate_collection_policy=all_ltf_forward_confirmations_after_post_htf_close, and left context status ok for all 1473 candidates. Targeted flow coverage: 3805/3808 ready.

Raw: 435 trade rows, 86 closed, sum_net -26.49%, avg -0.31%, median -0.70%, WR 32.6%.
Live-filtered: 21 closed, sum_net +9.96%, avg +0.47%, median -0.41%, WR 38.1%; top5 dependency 257%, so positive result is top-led.

Category readout:
- discovery: raw 72 closed / -37.36%; live-filtered 16 closed / -8.96%. Not tradable.
- runner_balanced: raw 9 closed / -11.44%; live-filtered 2 closed / -1.11%. Not tradable.
- runner_oi_confirmed: raw 5 closed / +22.31%; live-filtered 3 closed / +20.03%. Interesting but far too few and top-dependent.

Conclusion: forward post-HTF 1m/5s is not broadly good. Only runner_oi_confirmed survives, but sample is too small for an edge claim.
```

## 2026-05-25 - post-HTF forward 1m/5s fader/short research

```text
Run: .output/results/anomaly_lab_post_htf_forward_1m_5s
Artifact added: short_fader_research_variants.csv
Question: whether post-HTF forward anomalies that later print a low move >=2% can become a short/fader strategy, and which factors/exits matter.

Label readout: among 435 forward signals, 247 (56.8%) later reached future_dd_low_after_decision <= -2%. Discovery had 203/348 (58.3%), runner_balanced 38/59 (64.4%), runner_oi_confirmed only 6/28 (21.4%). Strongest observable fader markers were prior crowding/fade density: prior_spike_count_72h>=10 gave 120/128 down2 (93.8%), not OI-confirmed + prior_spike>=5 gave 154/172 (89.5%), prior_fast_fade>=3 gave 82/92 (89.1%). OI up and negative mark basis helped only moderately (62.5% down2 together).

Short simulation: structural short stop above decision_box_high + 0.05*box_range, adverse 0.05% entry/exit slippage, fees 0.04% each side, max hold 240x5s. Raw multi-position results can be positive only at high RR: discovery rr2.0 closed 347 / +89.9%, avg +0.26%, median -0.22%, WR 41.2%; discovery_or_balanced rr2.0 closed 406 / +85.8%. But live-like cap-1 turns them negative: discovery rr2.0 cap1 44 / -2.07%, discovery_or_balanced rr2.0 cap1 45 / -1.54%, all rr2.0 cap1 39 / -6.65%. Best near-flat cap1 was prior_spike>=5 rr2.0: 19 closed / -0.09%, median -0.30%.

Exit tests: RR below 1.5 was broadly worse; if this is researched further, 2R is the only plausible fixed target. BE after 0.5R/0.75R did not improve edge and usually hurt. Structural trailing did not rescue cap-1. Early no-progress exits after 12/24 candles did not improve the result.

Conclusion: there is a real post-HTF fader phenomenon in labels, but current short execution is not a tradable edge under cap-1. The factor set is useful for a fader dataset, not for enabling live shorts.
```

## 2026-05-25 - bare HTF anomaly fader research

```text
Run source: .output/results/anomaly_lab_post_htf_forward_1m_5s
Research output dir: .output/results/anomaly_lab_post_htf_forward_1m_5s/htf_fader_bare_research
Artifacts:
- bare_htf_anomaly_labels_features.csv
- bare_htf_short2_factor_separation.csv
- bare_htf_short2_rule_rates.csv
- bare_htf_short_trigger_exit_grid.csv
- bare_htf_short_partial_exit_focus.csv

Method: ignore long-selected categories as strategy classes. Collapse candidates to 167 unique bare HTF anomalies. Label post-close LTF path from first executable 5s after HTF close. Build fixed-window LTF features from 4/6/12/24 closed 5s candles after HTF close. Search short-pressure triggers and simulate short entry at next 5s open with fees/slippage, structural stop, RR/BE/trailing/early-exit variants, plus cap-1 filter.

Label result: 81/167 (48.5%) bare HTF anomalies reached >=2% short MFE after HTF close; 49/167 (29.3%) were clean with adverse_up_before_short_low <=1.5%.

Main factor result: faders are strongly tied to prior crowding/fade, especially when LTF starts weak. prior_spike_count_72h>=10: 35/49 short2 (71.4%). prior_spike>=5 and ltf12_ret<0: 9/13 short2 (69.2%) with 61.5% clean. prior_fade>=1 and ltf12_ret<0: 11/16 short2 (68.8%) with 56.3% clean. OI up + negative mark was weaker and dirtier: 16/25 short2 (64.0%) but only 12.0% clean.

Best executable short probes after LTF pressure:
- prior_fast_fade>=3 + failed_new_high + RR2.5: cap1 9 closed, +23.87%, avg +2.65%, median +3.41%, WR 66.7%.
- prior_spike>=10 + failed_new_high + RR2.5: cap1 10 closed, +22.87%, avg +2.29%, median +2.76%, WR 60.0%.
- prior_spike>=5 + taker_fade_red + RR2.5: cap1 12 closed, +22.31%, avg +1.86%, median +1.65%, WR 66.7%.
Top5 dependency remains high around/above 1.0 because sample is small.

Exit result: useful winners need large RR. RR2.0-2.5 is the only plausible area. Focused partial-exit checks confirm that full RR2.5 beat 50% partial at 1R and 1.5R on the best probes: prior_fade_ge3_failed cap1 +23.87% full vs +17.51%/+19.63% partial; prior_spike10_failed +22.87% full vs +16.51%/+18.63%; prior_spike5_takerfade +21.30% full vs +14.61%/+16.84%. BE after partial did not help. BE/trailing usually did not change the best probes materially; early no-progress exits helped some noisy pullback variants but was not a robust improvement.

Conclusion: this is the first short/fader result worth follow-up, but not live-ready. It is a small-sample candidate contract: prior crowding/fade + observed LTF short pressure, not long category inversion.
```

## 2026-05-23 - P392 1m/5s live-filtered replay

```text
Run: .output/results/anomaly_lab/live_parity_1m_5s_p392
Source: reused candidates from .output/results/anomaly_lab/1m_5s

Purpose: keep the wide 1m/5s category-discovery simulation, then apply the new final cap-1 live portfolio filter.

Raw simulation: 1120 closed, 4048 skipped, sum_net +188.65%, avg +0.168%, median +0.275%, WR 58.48%.
Live-filtered: 45 closed, 5123 skipped, sum_net +16.43%, avg +0.365%, median +0.122%, WR 55.56%.
Final live filter cut: 1075 of 1120 raw closed trades; kept share 4.02%. All final-filter skips were live_portfolio_filter_max_open_positions_at_entry.

Live-filtered category readout:
- runner_balanced: 11 trades, sum +10.03%, avg +0.911%, median +1.014%, WR 72.73%. Best current live-default candidate, but too few cap-1 trades for final confidence.
- runner_flow: 4 trades, sum +7.86%, avg +1.964%, median -0.272%, WR 50.00%. Positive sum is tail-led; keep watch/research, not a standalone default.
- runner_oi_confirmed: 2 trades, sum +2.49%, avg +1.246%, WR 100%. Too few trades; OI category remains unproven under cap-1.
- discovery: 28 trades, sum -3.94%, avg -0.141%, median -0.148%, WR 46.43%. Discovery should not trade live by default.

Honesty: 28/28 honesty nodes ok, 0 failures. Universe remains cache_snapshot_scan, so survivorship risk still applies. Replay command timed out after core trade artifacts were written; no chart artifact was needed because render_charts=false.
```

## 2026-05-23 - latest anomaly_lab TF/session/category readout

```text
Run root: .output/results/anomaly_lab
TF sets: 5m/30s, 1m/15s, 1m/5s, feature_contract=htf_setup_ltf_entry_v1.

Purpose: evaluate the latest multi-TF backtest by category/session/TF set, check whether discovery is only research-grade, and audit lookahead/live2 parity risks.

Core result:
- 1m/5s is the strongest discovery surface: 1120 closed, sum_net +188.65%, avg +0.168%, median +0.275%, WR 58.5%, top5 dependency 22.5%, top15 about 51%.
- 1m/5s live_priority is cleaner: 280 closed, sum_net +114.06%, avg +0.407%, median +0.927%, WR 61.4%, top15 about 68%.
- 1m/15s live_priority is also usable but lower frequency: 160 closed, sum_net +68.85%, avg +0.430%, median +0.95%, WR 60.6%.
- 5m/30s has positive sum but is more top-tail dependent: 221 closed, sum_net +63.20%, avg +0.286%, top5 70.3%, top15 147%.

Session readout:
- America and Europe carry most of the edge. On 1m/5s, non-Asia keeps almost all profit with fewer trades: 761 closed, sum_net +186.51%, avg +0.245%, worst_day about -7.3% vs all-run worst_day -21.0%.
- Asia is weak across TF sets: 1m/5s Asia +2.14% total with severe top dependence and worst_day -20.4%; 1m/15s and 5m/30s Asia are negative.

Discovery interpretation:
- Discovery is useful as a dirty search category, not as live default. On 1m/5s it adds frequency and some sum, but avg trade is only +0.089% and top15 dependency is near 100%. On 1m/15s discovery is negative.
- Candidate promotion should come from discovery into explicit live_priority subcategories, not by loosening live rules around the discovery bucket.

Honesty/parity audit:
- anomaly_backtest_honesty_report.csv has 28 nodes, 0 failures for all three TF sets. Artifact checks also found no candidate/context as-of timestamp greater than decision_available_timestamp_ms and all closed trades include the entry candle in post-entry simulation.
- Flow labels are real aggTrade-derived 1s materializations for closed trades; trade_count_proxy_used=false.
- Residual honesty limitations: universe_symbol_scope=cache_snapshot_scan with survivorship_bias_risk=true, and this run uses max_open_positions=1000. Therefore the run is edge-evaluable for research, but not a strict live2 PnL projection.
- Current code/run conflicts with the older P385 memory claim that anomaly-lab default is live-like max_open_positions=1; constants.py and run_config.csv show 1000.
- live2 parity is only close for 1m/5s signal math. live2 is hardcoded around 1m setup / 5s entry constants and max_open_positions=1, while this backtest also includes 1m/15s and 5m/30s and uses max_open_positions=1000.

Best next test: rerun only 1m/5s with explicit live-like settings first: max_open_positions=1, explicit symbol universe/as-of universe if available, and compare all vs non-Asia vs live_priority_non_asia. Do not optimize category thresholds until that live-like replay is available.
```

## 2026-05-22 - P385 compact backtest honesty smoke

```text
Run: .output/results/anomaly_lab/lookahead_honesty_p385_closed_1m
Command: .venv\Scripts\python.exe main.py run-anomaly-lab --symbols INJ/USDT:USDT BEAT/USDT:USDT --days 2 --setup-timeframe 1m --entry-timeframe 1m --render-charts false --output-dir .output\results\anomaly_lab\lookahead_honesty_p385_closed_1m

Purpose: verify the hardened default execution model after P385. This is a correctness smoke, not an edge test.

Result: run completed with execution_model=next_bar_open_proxy_latency_1_slip_entry_0.0005_exit_0.0005 and portfolio_model=global_max_open_positions_1_at_actual_entry. candidates=23, signals=5, closed=5, skipped=0, avg_net=0.8786%, sum_net=4.3930%, win_rate=80.00%.

Honesty artifact: anomaly_backtest_honesty_report.csv has 28 rows matching the audit checklist. All 28 nodes are ok with 0 failures. run_config.csv records explicit symbols, max_open_positions=1, fee_rate=0.0004, entry_slippage_pct=0.0005 and exit_slippage_pct=0.0005.

Interpretation: the compact run proves the audit plumbing and default execution assumptions are active. It does not prove edge. Larger runs should now be read through the 28-node honesty report first; any warning/failure blocks profitability claims until explained.
```

## 2026-05-22 - P384 compact lookahead-audit backtest

```text
Run: .output/results/anomaly_lab/lookahead_audit_p384_closed_1m
Command: .venv\Scripts\python.exe main.py run-anomaly-lab --symbols INJ/USDT:USDT BEAT/USDT:USDT --days 2 --timeframe 1m --end-timestamp-ms 1779426180000 --render-charts false --output-dir .output\results\anomaly_lab\lookahead_audit_p384_closed_1m

Purpose: compact artifact-level validation after the P374-P384 lookahead audit. This is not an edge test.

Result: run completed. candidates=23, signals=5, closed=5, skipped=0, avg_net=0.9796%, sum_net=4.8981%, win_rate=80.00%. Do not treat these tiny targeted numbers as profitability evidence.

Artifact checks:
- candidate timestamp contract passed: setup_available >= timestamp, decision_available >= decision_timestamp, decision_available >= setup_available, and timestamp_semantics contains available-at-candle-close.
- OI/mark/premium/funding/long-short as-of timestamps are <= decision_available_timestamp_ms where present.
- future_label_status was `ok` for this short historical window, but unit coverage confirms `build_anomaly_signals()` does not gate on insufficient future labels.
- all five trades have post_entry_simulation_includes_entry_candle=True.
- runner/fader prepump context was written with availability-window filtering.

Diagnostics: market_context_status still reports explicit funding missing_available_timestamp for old INJ funding cache rows. Those rows were not silently consumed; rebuild/migrate funding cache before using funding context for claims.

Fixes discovered by this run before successful completion: closed-TF run path needed prior-context refresh before category replay, and closed candidates needed flow_hold_* fields to match category filter inputs.
```

## 2026-05-22 - live2 INJ/BEAT targeted parity audit after P373

```text
Artifacts reviewed: .output/results/live2_anomaly_runs/20260521_164122 and .output/results/live2_anomaly_runs/20260521_194030.
Targeted cache: filled only event windows around INJ, BEAT, and same-run selected/top interesting symbols with --window-timestamps-ms, then materialized 5s cache. No full universal 1s cache is required for this audit pattern.

Live INJ: selected 2026-05-21T17:36:42.752Z, bucket_close_ms=1779385000000, category runner_oi_confirmed, signal_entry=5.209, fill=5.211.
Live BEAT: selected/executed 2026-05-21T22:11:47.668Z, bucket_close_ms=1779401505000, category runner_oi_confirmed, signal_entry=0.8496, fill=0.8475, later stopped near 0.809.

Pre-fix issue: BEAT at 2026-05-21T22:11:30Z had a valid setup except mark_basis_below_category_min. That gate depended on live mark WS ticks, while backtest was using delayed/historical mark klines. INJ also exposed a baseline mismatch: backtest entry flow used aggTrade-derived counts but setup baseline used raw kline number_of_trades, inflating the denominator versus live.

Fixed4 category-only targeted backtests:
runner_oi_confirmed: signals=25, closed=6, skipped=19, avg_net=0.5928%, sum_net=3.5565%, win_rate=50.00%.
runner_flow: signals=19, closed=4, skipped=15, avg_net=0.3004%, sum_net=1.2017%, win_rate=50.00%.
runner_balanced: signals=52, closed=12, skipped=40, avg_net=0.7061%, sum_net=8.4734%, win_rate=66.67%.

Parity result: runner_oi_confirmed now sees INJ at decision 2026-05-21T17:36:30Z / entry 17:36:35 and BEAT at decision 2026-05-21T22:11:30Z / entry 22:11:35. Old live entered both later, especially BEAT, because of live-only/runtime differences now addressed by P373. These small event-window numbers are not an edge proof; they only validate that the backtest can now replay the live-relevant category path more honestly.

Next test: run live2 on P373 and verify no mark_basis trading rejects, no permanent execution_not_ready after normal stop settlement, and compare new selected trades against targeted event-window backtests.
```

## 2026-05-21 - P370 live2/backtest signal math parity audit

```text
Run analyzed: code audit, no new market run.
Question: make live2 match backtest excluding network/CPU latency, and check math/substituted values.
Result: fixed signal-math mismatches that could change live2 decisions versus backtest: quote/trade setup ratio constants now match the current backtest CLI defaults, whipsaw now uses the 60x1m setup baseline, effort-per-return uses the whole forming setup return, taker and flow_hold use the confirmation segment, and the range baseline denominator matches backtest. Removed the trading 5s-scaled baseline fallback; missing 60x1m baseline is now an explicit data dependency.
Expected impact: live2 should no longer choke or pass candidates because of single-5s proxy math where backtest uses forming 1m/5s setup math. Near-miss volume may rise because every real 5s trade bucket can reach the signal engine for parity; acceptance remains gated by the same backtest-like filters.
Next test: restart live2 and verify live2_near_misses.csv shows live_setup_* reject distribution dominated by true setup-level reasons, not old 24h whipsaw or final-5s effort artifacts.
```

## 2026-05-21 - P369 live2/backtest execution parity audit

```text
Run analyzed: code audit, no new market run.
Question: check live2/backtest parity carefully.
Result: fixed two execution-level live-overfilters: live freshness 2000ms -> 5000ms and live RR guard 0.95 -> 0.70, matching the default backtest next-5s market-entry contract. Also fixed selected-signal stop/TP1 parity so live2 uses the same decision EMA20/structural-buffer stop and rounded 0.75R pump-leg TP1 basis as backtest.
Expected impact: fewer live-only rejections after a signal is selected; entry guard should now reject mainly real stale >5s, drift >0.4%, TP1 already touched, invalid risk, or RR<0.70. Candidate/category filters are unchanged by this patch.
Next test: restart live2 and compare live2_near_misses.csv plus live2_events.csv entry_guard reasons against a same-profile backtest. The important check is selected_count>0 when the backtest would have a candidate, and no live-only stale/RR choke inside <=5s.
```

## 2026-05-21 - live2/backtest parity follow-up after P367

```text
Code review after P367 found one remaining overfilter: live2 had a pre-category single-candle `stream_candle_is_not_upward_price_confirmation` gate, while backtest uses setup-level price_retention, verticality and hold_count across the confirmation segment. This could reject a valid forming 1m setup whose final 5s candle was red but retained enough of the move.
Patch P368 removes that single-5s upward gate and adds live setup-level parity checks/diagnostics. Remaining observed strict live2 filters are now expected shared/backtest filters: quote/trade setup pace, retention, verticality, hold count, initial risk, OI/mark category context, prior spike/fade/whipsaw caps, and entry guard.
Potential residual mismatch to monitor: runner_flow flow_hold naming differs (`next_n` in backtest artifacts, trailing no-lookahead in live), but live uses only already closed 5s candles at decision and is not currently proven stricter than backtest.
```

## 2026-05-21 - live2 run review 20260521_124514 parity choke

```text
Artifacts reviewed: .output/results/live2_anomaly_runs/20260521_124514.
Verdict: live2 was still choking pre-entry. Infrastructure was not the blocker: selected_count=0, total_orders_submitted=0, total_integrity_errors=0, OI ok=578/579, prior_context ok=578/579. The signal funnel had 123388 decisions, 122303 rejected, 365 dependency-not-ready, 351 deadline_missed, and 333 expired backlog.
Main blockers: stream_candle_is_not_upward_price_confirmation=67516; prior_whipsaw_24h_above_category_max about 50k per live-priority category; prior spike/fade caps about 2k-2.6k; mark/OI/range rejects were small after prior filters. Near-miss distributions showed prior_up_down_whipsaw_to_impulse_range p50 about 9.9 and p95 about 34 against live limits 0.5-0.6.
Top-growth disproves "no market": closed 12:00 UTC hour had BSB +14.0%, EDEN +11.9%, FIDA +10.6%; closed 13:00 UTC hour had BSB +17.5%.
Root cause: live2 evaluated shared categories on single 5s actionable buckets, while backtest forms a 1m setup from 5s entry candles with default confirmation_candles=4. That made prior_whipsaw/range/risk stricter in live than in backtest. Follow-up patch P367 aligns live2 category features to a backtest-like 1m/5s forming setup and adds live_setup_* near-miss diagnostics.
Live1 note: research_tools/anomaly_micro_live.py is removable only after extracting still-used utilities/imports; deleting it directly would break CLI/backtest/tests.
```

## 2026-05-21 - P359 anomaly-lab latency grid plan

```text
Patch applied locally. For no latency stress, run anomaly-lab without `--latency true` or pass `--latency false` explicitly. For latency stress, grid artifacts will compare 0ms versus 2000ms extra delay.

Interpretation: 2000ms is not a promise that live will always enter in 2s; it is the current live2 maximum signal age accepted by entry guard. If future live runs show a stricter stable p99 entry path, reduce this value.
```

## 2026-05-21 - live2 run review 20260521_110133 active-count audit

```text
Artifacts reviewed: .output/results/live2_anomaly_runs/20260521_110133.
Verdict: the displayed `Активные 0/0` was a misleading operator metric, not evidence that the market had no actionable symbols. The run had about 67m market_runtime, 41053 deadline decisions, 40981 signal evaluations, selected_count=0, entry_guard total_checked=0, and execution total_execute_calls=0.
Primary signal blockers: stream_candle_is_not_upward_price_confirmation=23806; prior_whipsaw_24h_above_category_max=15505 for all live priority categories; prior_spike_count caps=796; prior_fast_fade caps=722/457; prior_context_not_ready=210. Data/latency issues were secondary: deadline_missed=62, data_not_ready=8, backlog=2, latency p95=49ms, p99=133ms, max=19548ms from one startup/audit burst.
Market context: closed 10:00-11:00 UTC top-growth completed with top_count=0 at the 10% threshold. Best closed-hour growth was MITO +8.67%, then MAVIA +5.07%, FIDA +5.02%, B +3.66%, CL +3.49%. Session ticker top later showed UB about +4.94%, FIDA about +4.59%, CYS about +4.28%.
Follow-up patch P363 changes `Активные` to current/seen actionable symbols based on actionable_since_ms TTL, because live2 does not retain state.status=actionable after a verdict.
Follow-up patch P365 fixes feature-scale mismatches found in this audit: 5s baseline quote was being compared directly to a daily-proxy threshold, prior-whipsaw used one 5s candle range as denominator, and signal risk used the current 5s low instead of the decision-box low.
```

## 2026-05-21 - live2 run review 20260521_103438 active-count audit

```text
Artifacts reviewed: .output/results/live2_anomaly_runs/20260521_103438.
Verdict: zero active/selected symbols was not caused by a dead market-data path. The run had 9129 deadline decisions in about 11m of market runtime after startup/context warmup, selected_count=0, orders_submitted=0, entry_guard total_checked=0, and execution total_execute_calls=0.
Primary blocker: signal contract, before entry guard. Top rejects were stream_candle_is_not_upward_price_confirmation=5361 and prior_whipsaw_24h_above_category_max=3343 for all live priority categories, followed by prior_fast_fade/prior_spike caps. Data dependency misses were minor (21 prior context not ready, 1 stale trade-flow bucket), and WS/reconnect health was clean.
Session movers existed but were not clear hourly top-growth confirmations in the available artifacts: final session leaders were USAR about +4.0%, UB about +2.1%, 1000CHEEMS about +1.8%; top-growth audit had not completed before keyboard_interrupt. These symbols were also rejected by the same two classes: upward price confirmation and prior_whipsaw caps.
Next useful test: near-miss/top-growth replay for USAR, UB, and 1000CHEEMS plus a backtest/shadow variant that reports what would pass with the prior_whipsaw cap relaxed or category-specific, without loosening live orders first.
Follow-up patch P362 adds live2_near_misses.csv for future runs, so this analysis no longer requires ad hoc parsing of nested deadline_decision JSON.
```

## 2026-05-20 - P358 top-growth off-hot-path validation

```text
Patch applied locally. Top-growth audit now runs through a daemon worker. In the next live2 run, heartbeat/status should show `top_growth_audit.status=scheduled/processing/completed` while `decision_loop_overrun_count` and fresh `total_deadline_missed` remain near zero.

Failure condition: if worker scheduling accumulates stale `scheduled` status for many minutes or decision latency still degrades at the same time as top-growth processing, disable top-growth or move it to a fully detached automation/process.
```

## 2026-05-20 - P357 live2 top-growth audit validation plan

```text
Patch applied locally. The next live2 run should create `top_growth/top_growth_index.csv` immediately with headers, then after the first heartbeat after a closed UTC hour should process the previous closed 1h period incrementally. Completion should write top/status files even when no symbol crosses the 10% threshold.

Expected improvement: every run can now answer whether live2 saw or missed the real hourly movers. This is audit/research data only, not an entry filter.

Failure condition: if top-growth processing increases `decision_loop_overrun_count`, `total_deadline_missed`, or artifact writer backpressure, reduce `top_growth_symbols_per_cycle` or disable it until a fully background-safe exchange boundary is implemented.
```

## 2026-05-20 - P356 live2 stability patch validation plan

```text
Patch applied locally. Validate on the next live2 restart, not against the already-running process. Expected artifact changes: `market_data_status.entry_stream_ready`, `readiness_policy`, per-source WS reconnect/disconnect/age fields, `decision_status.total_deadline_expired_backlog`, and `prior_context.total_ws_5m_gap_above_tolerance_tolerated`.

Expected improvement: global ticker/mark stream flaps should no longer turn the whole runtime into no-entry if aggTrade and per-symbol dependencies are fresh. Fresh actionable buckets should be processed before reconnect/backlog buckets. Ctx stale caused only by aggTrade-id discontinuity inside an otherwise closed live 5m candle should disappear; the gap evidence should remain in counters.

Failure condition: if `entry_stream_ready=true` but selected signals still show stale per-symbol mark/OI/prior context passing as ok, or if above-tolerance id gaps grow while no artifact counter changes, the patch is masking a data-quality problem and must be reverted or tightened with outage-window detection.
```

## 2026-05-19 - P319 live2 audit latency hardening

```text
Patch: P319 bounded async artifact writer.
Purpose: remove blocking CSV/JSON writes from live2 deadline/signal loop while preserving audit as a safety gate.
Acceptance in next live2 smoke: live2_status.json contains artifact_writer_status.ready=true, rejected_count=0, error_count=0, queue_size remains bounded, and heartbeat/deadline decisions continue while symbol-state/status files are written by the background writer. If rejected_count or error_count becomes non-zero, new entries must remain disabled through artifact_writer_ready=false.
```
# Anomaly Experiment Log

Compact active experiment log for anomaly-first research.

Retired strategy experiments were removed from active research memory in P129 because they are no longer an executable strategy contract. Preserve old external run archives separately if forensic comparison is needed.

---

## 2026-05-21 - post-P365 live2 choke audit

```text
Question: after the signal feature contract patch, are we still choking live2 with dumb blockers?
Artifacts/code reviewed: current live2 signal path plus .output/results/live2_anomaly_runs/20260521_110133 diagnostics. The run predates P365, so it is useful for identifying the old choke point but cannot validate the post-P365 funnel.
Finding: the primary confirmed bugs were already fixed in P365: baseline_quote_daily_proxy units, prior-whipsaw denominator, and initial risk based on one 5s low. The remaining strict filters in current code are not proven bugs: upward price confirmation, OI 3x5m increase, mark premium, prior spike/fade/whipsaw caps, range expansion, and no-lookahead live flow hold. These are strategy filters from the shared category contract.
No trading-logic patch applied in this audit. Lowering mark/OI/prior/range thresholds now would be parameter loosening, not a root-cause fix. The next live2 restart must be judged by live2_near_misses.csv and decision_funnel; if selected_count remains zero after P365, compare near-miss distributions against hourly top_growth before changing thresholds.
Expected healthy post-P365 outcome: rejects shift away from impossible unit/risk/whipsaw-scale blockers toward real mark/OI/range/category rejects, or selected_count becomes non-zero and entry_guard/execution timing artifacts start proving the <5s path.
```

## 2026-05-21 - anomaly-lab category/TF review

```text
Artifacts reviewed: .output/results/anomaly_lab/5m_30s, 1m_15s, 1m_5s. These are the latest local anomaly_lab artifacts found, last written 2026-05-18, not a rerun after P359 latency-grid default changes.
Verdict: current live_priority categories show a promising long edge across all three TF sets, but not yet a production-grade "edge found" proof. The result is 40 days only and still uses next_bar_open_proxy_latency_1, not real live fill/stop exchange execution.
By TF live_priority: 5m_30s n=141, WR=70.9%, avg_net=1.78%, median=2.01%, sum=2.505, top5 dependency=23.6%; 1m_15s n=141, WR=77.3%, avg_net=1.93%, median=1.73%, sum=2.724, top5 dependency=22.1%; 1m_5s n=94, WR=87.2%, avg_net=2.12%, median=1.59%, sum=1.990, top5 dependency=25.5%.
By category: runner_oi_confirmed is the cleanest current category; runner_flow is strong but small/fragile on 1m_5s; runner_balanced is the weakest and most top-tail dependent on 1m_15s.
Weaknesses: all-TF health still fails worst-day containment; discovery fallback is much weaker than live_priority and should not be treated as tradable; 1m_5s all-trades median is negative because discovery dominates; latency stress artifacts are old 0/7/10/15s and show material decay with delay, especially 1m_15s and 1m_5s.
Short live: no evidence from these artifacts supports real short live. Current backtest/live code is long-entry oriented. A short version should start as a research-only inverse/fade lab or shadow live audit, not real orders.
```

## 2026-05-21 - P360 recent live2 blocker audit

```text
Artifacts reviewed: .output/results/live2_anomaly_runs/20260521_073643, 20260521_044228, 20260520_182501, and 20260520_135941.
Usable latest full run: 20260521_044228. 20260521_073643 stopped during early startup with 0 decisions, so it cannot evaluate entry blockers.
Finding: 20260521_044228 had 97882 decisions, selected_count=0, total_orders_submitted=0, total_integrity_errors=0, entry_guard checks absent, and execution calls absent. The latest full run was not losing entries to order submission, fill verification, stop verification, artifact writer, or private user-data stream.
Remaining blocker class: signal contract. Top rejects were stream_candle_is_not_upward_price_confirmation=55842, prior_whipsaw category caps about 38758 per category, prior_spike caps up to 3073, and prior_fast_fade caps up to 1318. These are hypothesis filters, not infrastructure blockers. They may be too strict, but changing them needs a near-miss/top-growth/backtest check rather than a live safety patch.
Latency: 20260521_044228 rejected-signal p95=49ms/p99=134ms/max=678ms; deadline_missed=37 with p50=3196ms and max=3924ms; deadline_expired_backlog=8 with max=13196ms. No selected signal hit these paths in this run.
```

## 2026-05-21 - live2 run review 20260521_044228

```text
Artifact reviewed: .output/results/live2_anomaly_runs/20260521_044228.
Window: live2_started 2026-05-21T04:42:28Z; latest inspected status 2026-05-21T05:23:54Z; market_runtime about 26m after startup warmup/context prewarm.
Verdict for "all-seeing eye <5s": materially improved and acceptable for market-watch hot path, with one audit weakness. Runtime gates are currently all_gates_ready; session allowed share is 1560.582s allowed / 0.756s blocked = 99.95%, rounded in UI to 100%. WS health is clean: ticker/aggTrade/markPrice reconnects=0, disconnects=0, payload_errors=0, shards_connected=4/4, last message ages low.
Data coverage: selected universe 578. mark ok=578, prior_context ok=578, OI ok=576 with 2 not_enough_history. prior_context rolling maintenance is active and clean: total_ws_5m_candles_appended=3459, gap_tolerated=0, gap_above_tolerance_tolerated=0, gap_rejected=0, total_errors=0. No Ctx stale was observed.
Decision latency: total_decisions=14167, selected_count=0, data_dependency_not_ready=0, data_not_ready=4, deadline_missed=28, deadline_expired_backlog=6. Rejected_signal_contract latency p50=26ms, p95=49ms, p99=137ms, max=402ms; no normal evaluated decision exceeded 750ms or 5s. deadline_missed rows were 3196ms, still <5s. The 6 deadline_expired_backlog rows were >5s stale backlog, not fresh accepted signals.
Trading/execution truth: no selected signals, orders, fills, stops, protected positions, or integrity errors. Therefore this run proves market observation/gating health, but does not prove real entry/fill/stop lifecycle under live signal pressure.
Signal funnel: no entries because categories rejected, dominated by stream_candle_is_not_upward_price_confirmation and prior_whipsaw/prior_spike/prior_fast_fade caps. No profit/edge conclusion can be made.
Residual issues: top_growth audit exists but is too slow to complete promptly with default one symbol per heartbeat; at inspection it was still scheduled/processing around 304/578 symbols and top_growth_index had only headers. This is not a trading blocker because it is off the hot path, but it weakens missed-pump audit until completion. Telegram startup notification had one SSL handshake timeout; artifacts remained healthy and this is operator-notification risk, not source-of-truth risk.
Next action: no critical live-entry blocker found in this run. For audit quality, increase top_growth_symbols_per_cycle moderately or keep the run alive long enough to confirm top_growth completion; then evaluate any hourly top movers against live decision/reject visibility.
```

## 2026-05-20 - live2 run review 20260520_135941

```text
Artifact reviewed: .output/results/live2_anomaly_runs/20260520_135941.
Window: live2_started 2026-05-20T13:59:41Z, market loop after startup prewarm about 2026-05-20T14:15:11Z through latest summary 2026-05-20T17:18:16Z.
Verdict for "all-seeing eye": improved prior-context freshness, but not ironclad. Startup selected 578 symbols; aggTrade warmup 578/578; prior-context prewarm 578/578. P354 rolling context is active (`runtime_maintenance=live_ws_closed_5m_roll_forward_after_startup_rest_bootstrap`), current prior_context_status_counts are ok=578/not_seen=44, and Ctx stale is no longer the main problem.
Main blocker: market-data readiness flaps. Runtime gate was all_gates_ready about 6551s and blocked by stream_coverage_not_ready about 4529s in the reviewed market window. Market transitions were dominated by ticker/mark/aggTrade not-ready combinations; reconnect totals are high (ticker disconnects 260, aggTrade 548, markPrice 101), with stale_ms=5000.
Decision latency: most evaluated non-missed decisions are fast (rejected_signal_contract p95 46ms, data_dependency p95 47ms), but deadline_missed remains 1446 decisions. Missed latency p50 8120ms, p95 52148ms, max 108942ms; 908 missed decisions were >5s late. This violates the ideal pump->entry <5s guarantee for the affected buckets.
Prior-context rolling diagnostics: `total_ws_5m_candles_appended=12907`, `total_ws_5m_gap_tolerated=432`, `total_ws_5m_gap_rejected=9042`. Rejected 5m gap candles are visible, not hidden, but the tolerance is likely too strict for Binance aggTrade id sequencing across sparse/fragmented symbols or reconnects. Current symbol-state still shows ok context because previous valid rolling/bootstrap context remains within stale_ms; rejected gaps are tracked in counters.
Execution/audit: no orders, fills, stops, or integrity errors. artifact_writer ready=true with error_count=0/rejected_count=0. Entry guard never ran because no category selected.
Signal funnel: 91417 decisions, selected_count=0, total_rejected=84930, total_data_dependency_not_ready=5049. Rejects dominated by non-upward candles and prior whipsaw/spike/fast-fade caps; no profitability conclusion.
Audit gap: no top_growth directory was present, so missed pump comparison against hourly top movers is not available from this run.
Next action: fix WS readiness stability / stale handling first. The prior-context stale issue is mostly gone; the current risk is that stream_coverage_not_ready and deadline_missed windows can block or delay an otherwise valid pump beyond 5s.
```

## 2026-05-20 - P354 live2 rolling prior-context test plan

```text
Patch: P354 changes live2 prior-context maintenance from repeated selected-universe full-window REST polling to startup REST bootstrap plus live WS closed-5m rolling append.
Synthetic validation: minor intra-candle aggTrade id gaps are tolerated and counted; large gaps mark `ws_gap_exceeds_tolerance`; atomic artifact tests and wall-clock candle closure tests still pass.
Live acceptance after restart: prior_context startup event must show `runtime_maintenance=live_ws_closed_5m_roll_forward_after_startup_rest_bootstrap`, status must show `maintenance_mode=startup_rest_bootstrap_plus_live_ws_5m_rolling_append`, `total_ws_5m_candles_appended` must increase every closed 5m interval for symbols with trades, `Ctx stale` should not accumulate from normal cooldown mechanics, and any rejected WS gap must be visible through prior_context status counts plus symbol-state gap counters.
Residual risk: symbols with no trades in a 5m interval do not create synthetic candles; this is intentional. If those symbols later become hot and their rolling buffer lacks enough coverage, they must be repaired or blocked, not marked ok.
```

## 2026-05-20 - live2 run review 20260520_122522 after P352

```text
Artifact reviewed: .output/results/live2_anomaly_runs/20260520_122522 while preserving the running process.
Verdict: P352 removed the mass decision-lateness failure mode, but this live process still did not have the intended prior-context polling budget. Deadline misses were down to a small minority of processed decisions, while prior 24h context remained materially stale/not_seen and prior_24h_context_not_ready still dominated data-dependency blocks.
Root cause: launched CLI defaults still passed the old prior_context_stale_ms=900000, prior_context_symbol_cooldown_seconds=300, and prior_context_max_symbols_per_cycle=4 values into AnomalyLive2Config, overriding the newer runtime defaults. This is a wiring bug, not a market signal issue.
Audit durability issue: live2_symbol_state.csv was observed as 0 bytes during an async writer full rewrite before recovering. That can hide the state at exactly the moment an operator or post-mortem reader inspects it. P353 changes full-rewrite status/summary/symbol-state artifacts to atomic temp-file replacement.
No-sweep check: stale/missing context remains visible as data_dependency_not_ready; the patch does not turn stale context into ok, does not synthesize context, and does not loosen signal/category/execution guards.
Next test: restart live2 on P353 and inspect the first 20-30 minutes. Required evidence: prior_context status counts trend toward ok for the selected universe, prior_24h_context_not_ready accumulation slows materially, `prior_context_poller_starting.poll_scope=selected_universe_active_priority`, no tmp files remain, and artifact_writer_status stays ready with zero errors/rejections.
```

## 2026-05-19 - P324 live2 operator-message smoke plan

```text
P324 proposed: live2 operator notifications are added without changing signal, guard, execution, stop, TP, or sizing logic. Acceptance: startup sends one events message; verified entry messages show the linked Coinglass symbol name; TP1/BE and final close messages are sent from supervisor actions; integrity errors use strict critical wording; live2_events.csv records telegram_message_sent or telegram_*_failed. Telegram is not source of truth.
Remaining patches: restart/open-order reconciliation; exact stop-trigger fill reconstruction; deeper performance/shard stress hardening.
```

## 2026-05-19 - live2 architecture patch stack

```text
P314 proposed: live2 no longer requires explicit symbols for aggTrade startup. It uses ticker WS state as a startup universe source and opens aggTrade shards for the selected liquid USDT futures. This is still market-data only: no signal verdicts and no orders. Acceptance for next run: universe selected >0, aggTrade shards connected for selected universe, `candidate_dropped_latency_pressure` is not a live2 concept, and no REST/gap backfill appears in live2 market-data status.
Remaining patches: DeadlineEngine/actionable verdicts, SignalEngine adapter, executable entry guards, real ExecutionEngine, PositionSupervisor, bounded async ArtifactWriter.
```


## 2026-05-19 - live latency plan after BAS and post-P301 smoke

```text
Finding: after P301, batch selection/reconcile/prefetch are no longer the dominant hot-path cost. The remaining delay is cold/warm discovery: strong ticker/flow spikes can sit in warm/radar queues, wait for a second observation, or be dropped/deferred under pressure even though precise scan and order path are comparatively fast once selected.
Decision: propose P304 as scheduler-only latency patch. Extreme real-flow candidates are not treated as ordinary warm symbols; they get immediate precise-lane promotion and reserved precise slots. This preserves signal/category/execution rules while reducing cold->precise delay.
Acceptance: in the next live artifact, inspect `danger_flow_immediate_promoted`, `ticker_radar_promoted.immediate_danger_flow=true`, `symbol_batch_selected.scan_reason_by_symbol=precise_immediate_danger_flow`, and time to first `signal_symbol_scan_summary`. Target <1-2s from immediate promotion to precise scan when data dependencies are ready.
```

## 2026-05-19 - live latency/data review 20260519_112206 while running

```text
Artifact reviewed read-only while run was still active: .output/results/live_anomaly_runs/20260519_112206, post-startup window only. Startup context warm-up is intentionally excluded from the pump->entry latency budget.
Data stability: ws_aggtrade_connection_status=connected for all reviewed live_cycle_summary rows, ws_health_pct p50 about 99.4%, and ws_aggtrade_frame_read rows were covered with no network backfill in the read path. OHLCV/aggTrade REST labels mostly mean cache/gap fill, not full flow outage.
Latency: active_symbol_marked->next precise scan was good (p50 0.291s, p95 0.969s, max 1.220s). danger_flow_immediate_promoted->next precise scan was mostly within target (p50 1.604s, p95 4.397s, max 6.065s; 96.4% <=5s), but first_seen_ms->next scan still exceeded 5s for 4/28 immediate-danger cases because some waited for later observations or backlog.
Main bottleneck: generic warm/radar holding remains overloaded. warm_watch_marked->next scan p50 was about 150s and p95 about 1167s for matched symbols; ticker_radar_promoted->scan p95 was 7.5s with two 34-35s outliers; warm_watch_precise_promoted->scan p95 was about 22s.
Queue pressure: potential_anomaly_queue_count p50 18 / p95 22; candidate_queue_dropped_pressure_count p95 7 per cycle; latency_sla_status breached in 254/636 cycles. There were 902 candidate_dropped_latency_pressure and 42 warm_watch_precise_deferred_latency_sla rows.
Wasted time: hot_waiting_prefetch was skipped in almost all cycles because the queue was not idle (`due_hot_scan_has_priority`, SLA breached, or queue_count > 4). As a result, subminute aggTrade gaps are often REST-backfilled inside the precise scan itself. aggtrade_rest_gap_prefetch ran 242 times; 154 had <=100ms missing, yet still made network backfill calls, while large first-scan gaps of ~270-318s also occurred for newly selected symbols.
Execution funnel: no category_selected/order_attempt/position_opened in the reviewed window; delayed replay was mostly skipped because live was not idle. Category rejects were dominated by reject_mark_basis_below_min. This run is therefore latency/data-path evidence, not profitability evidence.
Next direction: keep active/immediate-danger priority, but move data readiness earlier for hot waiting symbols. Subscribe/prefetch aggTrade for radar/immediate-danger symbols at mark/promote time under a strict small cap, and stop making REST calls for tiny tail gaps that are not needed for closed decision buckets. Target artifact acceptance: first_seen_ms->precise scan p95 <=5s, ticker_radar_promoted->scan p95 <=5s, potential_anomaly_queue_count p95 <8, and aggtrade_rest_gap_prefetch count sharply lower during hot scans.
```

Patch follow-up:

```text
P308 applied locally / UNKNOWN commit. It keeps critical scan/order priority, then allows bounded priority prefetch for up to 2 top waiting immediate-danger or score>=8 radar/warm symbols even under queue/SLA pressure. It also records tiny open-tail prefetch gaps as tail_gap_ignored instead of making REST calls. Acceptance remains artifact-based in the next live smoke: hot_waiting_priority_prefetch_cycle present, lower hot-scan REST gap backfills, and first_seen/promote->precise scan latency closer to <=5s.
P309 applied locally / UNKNOWN commit. Warm backlog is tightened to a smaller score-first queue and live artifacts now expose active/immediate_danger/ticker_radar/warm_watch latency plus per-scan origin first_seen/promote lag. This makes the next live run's <=5s delivery claim directly auditable without external reconstruction.
P310 applied locally / UNKNOWN commit. Symbols with prior_fast_fade_count_24h > 2 are quarantined from warm/radar hot lanes before they create backlog. Quarantine release is timestamp-derived from the excess fake-fade events aging out of the 24h window, not a fixed TTL. Ticker/top-growth visibility remains intact.
```

## 2026-05-19 - live connection check 20260519_122440 while running

```text
Artifact reviewed read-only while run was still active: .output/results/live_anomaly_runs/20260519_122440.
Connection verdict: not an exchange/websocket outage. In the latest 120 live_cycle_summary rows, ws_aggtrade_connection_status was connected in all rows and ws_health_pct stayed about 98.86-99.01%. No live_internal_error, live_data_integrity_error, or monitor_empty_ohlcv_escalation events were present.
Problem: the live path is still too loaded for pump->entry <=5s. Recent potential_anomaly_queue_count was p50 13 / p95 15, warm queue p50 8 / p95 10, radar p50 5 / p95 6. The run accumulated 1450 candidate_dropped_latency_pressure and 68 warm_watch_precise_deferred_latency_sla events.
Data path: recent cycles alternated between ok, OHLCV REST, and Поток gapREST. aggTrade WS reads continued, but there were 621 aggtrade_rest_gap_prefetch events; recent prefetch statuses were mostly tail_gap_ignored plus some backfilled. This is not a total data outage, but REST/gap dependency still appears in the hot path.
Latency evidence after P309: recent signal scan duration itself was cheap (p50 about 78ms, p95 about 984ms), but origin-to-scan was not: first_seen_to_scan p50 about 33.6s / p95 about 110.7s and promote_to_scan p50 about 21.1s / p95 about 108.8s over the latest 800 scan summaries.
Server implication: a closer/more stable VPS may reduce exchange RTT, local Wi-Fi/ISP jitter, and REST tail gaps, but it will not fix the dominant backlog by itself. The code/scheduler still needs to reduce hot-lane load or scan fewer waiting symbols for the <=5s requirement to be credible.
```

## 2026-05-18 - live decision-speed review 20260518_090759

```text
Artifact reviewed: .output/results/live_anomaly_runs/20260518_090759/live_events.csv.
The run noticed 489 symbols through warm/radar/active paths. Active symbols were fast after promotion: active->next precise scan p50 0.293s, p95 1.178s. The bottleneck is before active selection: warm/radar backlog and latency SLA. 54.7% of cycles had latency_sla_status=breached; due-scan p95 p50 was 17.384s and p95 28.512s versus a 15s threshold. The queue had warm_total_before p50 25 / p95 38, 11802 warm_queue_over_pressure_cap drops, and 1570 warm_watch_precise_deferred_latency_sla rows.
Precise scan itself is usually cheap: signal_symbol_scan_summary duration p50 281ms, p95 1031ms, p99 1578ms, but batch_seconds p95 was 17.86s because selection/backlog/rest/cache pressure serializes noticed candidates. Radar->next precise scan p50 was 20.77s, p95 3334s; warm->next precise scan p50 was 1743s, p95 8079s, so warm watch is mostly an overloaded holding pen, not a timely decision path.
Data-dependency misses still exist: 143 signal_scan_empty_ohlcv and 143 ohlcv_data_unavailable expiries; 190 category_dependency_unavailable expiries from reject_prior_fast_fade_filter_unavailable. The only selected signal was TOWNS 1m/15s runner_balanced; it reached category_selected, then execution rejected by price drift with entry_lag_ms=10188, drift=-0.4607%, RR improved to 1.51, and live_scan_gap_ltf_candles=0.
Plan direction: prioritize noticed symbols with a small hot-lane, make warm->precise promotion much stricter or score-ranked, keep active symbols protected, prefetch/cache entry frames for hot symbols, and split favorable vs adverse drift so fast execution work is not wasted by an absolute drift guard.
```

## 2026-05-18 - live artifact review 20260517_200159

Input:

```text
Artifact: .output/results/live_anomaly_runs/20260517_200159
Window: 2026-05-17 20:02:04Z..2026-05-18 04:38:21Z
Universe: 529 symbols after live 24h ticker liquidity filter; 6 symbols excluded below 300k USDT 24h quote-volume.
Configured pairs observed in scan summaries: 5m/30s, 1m/15s, 1m/5s
Real orders: confirm-real-orders path was preflighted, danger_continue_after_order_position_errors=true, but no order attempt occurred.
```

Data quality:

```text
No live_internal_error, live_data_integrity_error, order failure, or halt event was found.
Startup position cleanup closed 0 positions and account preflight passed.
Startup context backfill completed with status=ok, 529 symbols, 1587 snapshots, ready_symbol_ratio=1.0 and ready_snapshot_ratio=1.0. A safe live_reprepare also completed ready.
WS aggTrade path was active: ws_aggtrade_frame_read=23082 and entry_ws_aggtrade_pending_count summed to 0 in scan summaries.
Trade-count/tape conclusions are usable for scanned rows because subminute evidence came through real aggTrade-derived frames, but no profitability conclusion is possible because trades=0.
```

Funnel:

```text
signal_symbol_scan_summary rows: 10425
evaluated timeframe rows: 23082
signal_count: 0
category_selected: 0
order_attempt: 0
position_opened: 0
live_positions.csv rows: 0
Delayed replay: 758 processed cases, all live_decision_class=all_categories_rejected, recomputed_no_signal=758, strict_replay_would_enter=false for all rows.
```

Bottlenecks:

```text
Pre-signal precise rejects dominated: reject_weak_start_flow=13043 and reject_setup_too_early=8567.
Other pre-category rejects: reject_insufficient_real_entry_buckets=66, reject_entry_below_initial_stop=170, reject_invalid_tp1_pump_leg_bottom_risk=23.
Category stage had 2274 category_rejected rows, exactly 758 candidate decisions x 3 default runner categories. Main reason was reject_mark_basis_below_min=2062; weaker reasons were weak_range_expansion=54, poor_effort_per_return=47, poor_trade_effort_per_return=42, prior_up_down_whipsaw=34, large_print_signature=26.
Retryable context dependency still exists: signal_scan_retryable_dependency_blocked=269 and candidate_expired_dependency_timeout=269, all with reject_prior_fast_fade_filter_unavailable; however this did not produce any delayed replay case that would have entered.
```

Top-growth / near-miss:

```text
Nonempty missed-pump visibility covered FIDA, BAS, APR, and AIGENSYN. FIDA/APR/AIGENSYN were precise-scanned and primarily rejected by weak start flow / setup too early. BAS reached category rejection with reject_weak_range_expansion and also showed dependency timeouts plus invalid pump-leg-bottom TP1 risk in the precise reject summary.
This run therefore shows missed movers, not missed executable anomaly entries under the current live-priority contract.
```

Conclusion:

```text
The absence of trades was not caused by exchange execution. The run never reached category_selected or order_attempt.
Current live filters rejected candidates before execution: mostly weak/too-early pump flow, then negative/insufficient mark-basis at runner category selection.
Delayed replay does not contradict live: it found no strict replay entry among 758 saved rejected decisions.
Do not loosen filters from this single run. The next useful validation is to confirm that 24h context labels/parity are clean and then collect another live run; if zero trades persist, analyze whether mark-basis gates are too strict out of sample.
```

Patch note:

```text
P289 aligns live/backtest context semantics after this review: live already used a 24h prior context window, but operator labels/artifact history text still said 72ч and backtest legacy *_72h fields still used a real 72h window. P289 makes those labels/artifacts/config readouts report 24h and makes backtest use the same 24h effective context window while keeping legacy column names.
```

## 2026-05-18 - targeted anomaly-lab check for live run 20260517_200159

Input:

```text
Artifact root: .output/results/anomaly_live_window_20260517_200159_targeted
Command scope: 37 symbols selected from top-growth visibility, category rejects, retryable dependency blocks, weak-flow rejects, and delayed replay frequency.
Window: --days 2 --end-timestamp-ms 1779079101988, covering the live run ending 2026-05-18 04:38Z.
TF sets: default 5m/30s, 1m/15s, 1m/5s.
Purpose: check the user's concern that an offline backtest over the period may find trades even though delayed replay found no strict live entry.
```

Result:

```text
The concern was valid: offline targeted backtest did find signals/trades in the live period.
5m/30s: 20 signals, 6 closed trades, sum -5.64%, avg -0.94%, WR 50%, TP1 hit 50%.
1m/15s: 6 signals, 6 closed trades, sum -4.38%, avg -0.73%, WR 33.3%, TP1 hit 33.3%.
1m/5s: 56 signals, 10 closed trades, sum -6.80%, avg -0.68%, WR 20%, TP1 hit 30%; most other rows skipped as overlaps.
Inside/post-startup live window: 60 signal rows, 16 closed non-overlap trades, sum -11.6%, avg -0.73%, WR 43.8%, TP1 hit 50%.
All live-window signals/trades were pump_category_family=discovery and pump_category_id=discovery. There were zero live_priority trades.
```

Data quality:

```text
trade_count_proxy_used=false for all 60 live-window signals.
levels_trade_count_source=cached_ohlcv.number_of_trades for all 60.
entry_trade_count_source was cached_ohlcv_missing_aggregation_metadata.number_of_trades for 42 rows and cached_ohlcv.number_of_trades for 18 rows. This is real number_of_trades evidence, but the missing aggregation metadata means subminute provenance is not as clean as live WS aggTrade artifacts.
Context parity was not clean for live-priority claims: all 60 discovery_only rows had context_parity_status=oi_context_stale_asof.
```

Live comparison:

```text
Many offline discovery decision timestamps had exact live events. Live commonly rejected them via category_rejected:reject_mark_basis_below_min or signal_scan_retryable_dependency_blocked -> candidate_expired_dependency_timeout with reject_prior_fast_fade_filter_unavailable.
The parity report had strict_live_replay_enter=false for all 841 live-window parity rows: 60 discovery_only and 781 not_in_pre_context_universe.
Therefore the offline backtest does not contradict the live no-trade result under the current live-priority contract. It shows hindsight/discovery opportunities that the production-style category contract rejects.
```

Conclusion:

```text
The previous shorthand "backtest would show nothing" was wrong.
Correct statement: targeted backtest over the live period shows weak discovery trades, all non-live-priority, with negative summed returns and stale OI/context parity. Current live was not expected to open them.
Next most valuable check, if desired, is not to loosen execution; it is a wider full-universe live-window lab or a strict live-priority ablation focused on mark-basis and prior-fast-fade dependency handling.
```

## 2026-05-17 - latest 30d anomaly_lab readout

```text
Artifact: .output/results/anomaly_lab, pairs 1m/5s, 1m/15s, 5m/30s.
Category contract in closed trades: shared_pump_category_contract_v1_live_overlay_v8.
Covered entries: 2026-04-17 15:40 UTC through 2026-05-17; cache coverage ends around 2026-05-17 15:35-15:36 UTC, so the final few requested hours are not fully covered.
Trade-count quality is usable: trade_count_proxy_used=false for all 2363 closed trades; levels source is cached_ohlcv.number_of_trades, entry source is mostly cached_1s_aggregated_to_5s/15s/30s.number_of_trades.
All TF variants combined: 2363 closed, WR 51.8%, avg +0.38%, median +0.07%, summed trade returns +900.8%, positive trade-day share 88.5%. This is a variant sum, not one deployable bot/account return.
Live-priority subset: 218 closed, WR 73.9%, avg +1.61%, median +1.44%, summed trade returns +350.3%, PF 3.45, TP1 hit 67.9%, positive trade-day share 87.5%.
Strict context-ok live-priority subset: 195 closed, WR 76.4%, avg +1.66%, median +1.51%, summed trade returns +324.0%, PF 3.71, TP1 hit 70.3%, positive trade-day share 91.7%.
Discovery subset remains weaker: 2145 closed, WR 49.5%, avg +0.26%, median -0.01%, summed trade returns +550.4%, PF 1.46.
Dynamics: April partial period 1349 trades / +522.1% summed trade returns / avg +0.39%; May partial period 1014 trades / +378.7% / avg +0.37%. Live-priority stayed positive in both months: April 122 / +212.7% / avg +1.74%; May 96 / +137.6% / avg +1.43%.
Risk: live-priority still has top-tail dependence: top 5 trades are 23.1% of live-priority summed return, top 15 are 49.8%; context-ok top 15 are 52.2%. Combined TF rows can duplicate the same move across variants.
Conclusion: live-priority edge looks materially better than discovery and broadly stable across the two partial months, but do not interpret summed returns as account return. Next honest check is a unified no-overlap portfolio replay across TF variants with context_parity_status=ok.
```

## 2026-05-17 - anomaly_lab fixed-R / tail illusion report

```text
Artifact written: .output/results/anomaly_lab/portfolio_illusion_report/README.md and fixed_r_tail_report.csv.
Purpose: make the portfolio illusion visible by reporting live/discovery separately, TF/category slices, top-tail dependence, and fixed-R full-exit scenarios.
Method: closed trades only. fixed 1R/2R/3R exits use saved initial_risk_pct and mfe_pct; if mfe_pct > R * initial_risk_pct, the full position exits at that R net of roundtrip fee, otherwise the current recorded exit is kept. This is a scenario approximation from saved trades, not a fresh intrabar exchange replay.
Live-priority all TF: current runner sum +350.3%, avg +1.61%, PF 3.45, top15 share 49.8%, break-even top cut 60/218=27.5%.
Live-priority fixed 1R full exit: sum +343.9%, avg +1.58%, PF 4.33, top15 share 33.0%, break-even top cut 91/218=41.7%.
Live-priority fixed 2R full exit: sum +420.3%, avg +1.93%, PF 3.94, top15 share 42.4%, break-even top cut 70/218=32.1%.
Live-priority fixed 3R full exit: sum +458.5%, avg +2.10%, PF 4.21, top15 share 50.4%, break-even top cut 66/218=30.3%.
TF readout: 1m/15s benefits from 2R/3R versus 1R and current; 1m/5s is strongest at 2R; 5m/30s improves in sum with 1R/2R/3R but remains more tail-sensitive.
Category readout: runner_flow improves strongly with fixed 1R/2R/3R; runner_oi_confirmed improves with 2R/3R; runner_balanced is hurt by fixed 1R but improves with 2R/3R.
Duplicate-pressure lower-bound: live-priority has 30 rows in 15 exact symbol+entry-minute duplicate clusters across TF variants. Real no-overlap replay must use position lifetime, not only entry minute.
Next validation: implement/run unified no-overlap portfolio replay with context_parity_status=ok and compare current runner vs fixed 1R/2R/3R exits.
```

## 2026-05-17 - anomaly_lab 0.5 TP1 + 0.5 2R scenario

```text
Artifact written: .output/results/anomaly_lab/portfolio_illusion_report/half_tp1_half_2r_report.csv.
Method: closed trades only. Current trade return is treated as 0.5 TP1 + 0.5 runner. For TP1-hit trades, recover the current runner leg approximately, then replace that runner half with fixed 2R net if mfe_pct > 2 * initial_risk_pct; otherwise keep the current runner leg. Non-TP1 trades keep current exit. This is a scenario approximation, not fresh intrabar replay.
Live-priority all TF: current +350.3%, avg +1.61%, PF 3.45, top15 share 49.8%, break-even top cut 60/218=27.5%.
Live-priority 0.5 TP1 + 0.5 2R: +348.8%, avg +1.60%, PF 3.44, top15 share 43.4%, break-even top cut 65/218=29.8%.
TF live-priority: 1m/15s drops from +171.3% to +154.4%; 1m/5s improves from +69.0% to +78.3%; 5m/30s improves from +110.0% to +116.1%.
Category live-priority: runner_balanced drops from +100.3% to +88.7%; runner_flow improves from +120.6% to +126.3%; runner_oi_confirmed improves from +129.5% to +133.8%.
Conclusion: 0.5 TP1 + 0.5 2R keeps total almost unchanged versus current runner while reducing top-tail dependence. It helps runner_flow and runner_oi_confirmed, hurts runner_balanced.
```

## 2026-05-17 - anomaly_lab exit scenario grid

```text
Artifact written: .output/results/anomaly_lab/portfolio_illusion_report/exit_scenario_grid_live_priority.csv and exit_scenario_selected_by_category.csv.
Grid: live-priority closed trades only; TP1 fraction 0/25/50/75/100%, remainder target 1R/1.5R/2R/2.5R/3R/4R, fallback either current runner or BE after TP1.
Best balance by sum and tail haircut was around fixed remainder 1.5R with current-runner fallback. All-TF live-priority current runner: sum +350.3%, avg +1.61%, PF 3.45, top15 share 49.8%, break-even top cut 60/218=27.5%.
0% TP1 + 1.5R/current fallback: sum +372.5%, avg +1.71%, PF 3.59, top15 share 41.5%, break-even cut 65/218=29.8%.
25% TP1 + 1.5R/current fallback: sum +365.8%, avg +1.68%, PF 3.56, top15 share 40.9%, break-even cut 66/218=30.3%.
50% TP1 + 1.5R/current fallback: sum +359.0%, avg +1.65%, PF 3.51, top15 share 40.4%, break-even cut 68/218=31.2%.
75% TP1 + 1.5R/current fallback: sum +352.3%, avg +1.62%, PF 3.46, top15 share 40.0%, break-even cut 68/218=31.2%.
100% TP1/full 1R-style exit: sum +345.6%, avg +1.59%, PF 3.42, top15 share 39.6%, break-even cut 68/218=31.2%.
BE-after-TP1 variants generally reduced total and did not improve tail enough; avoid as default based on this artifact.
Interpretation: for a conservative live policy, 50-75% TP1 plus remainder target around 1.5R with current/live trailing fallback gives the cleanest tradeoff. 25% TP1 + 1.5R preserves more profit but leaves slightly more tail.
Next validation: no-overlap portfolio replay of current runner, 25/50/75% TP1 + 1.5R remainder, and full TP1.
```

## 2026-05-17 - anomaly_lab strict no-overlap exit replay

```text
Command: .venv/Scripts/python.exe research_tools/anomaly_exit_portfolio_replay.py --lab-dir .output/results/anomaly_lab --families live_priority --context-parity ok --max-positions 1 --same-symbol-overlap reject --models current,tp1_25_rest_1p5r,tp1_50_rest_1p5r,full_tp1 --output-dir .output/results/anomaly_lab/portfolio_exit_replay
Artifacts: portfolio_exit_replay_summary.csv, portfolio_exit_replay_trades.csv, portfolio_exit_replay_reviewed.csv, portfolio_exit_replay_daily.csv, portfolio_exit_replay_top_tail.csv.
This is the first strict no-overlap portfolio replay: one concurrent position, same-symbol overlap rejected, context_parity_status=ok, deterministic non-hindsight ordering by entry timestamp/category rank/TF priority/symbol.
Accepted trades: 105 for each model; skipped trades: 90 due to portfolio overlap rules.
current: sum +187.8%, avg +1.79%, WR 76.2%, PF 4.34, top15 share 71.3%, break-even top cut 35/105=33.3%, max daily DD -1.01%.
tp1_25_rest_1p5r: sum +188.8%, avg +1.80%, PF 4.35, top15 share 63.5%, break-even top cut 36/105=34.3%, max daily DD -0.97%.
tp1_50_rest_1p5r: sum +189.4%, avg +1.80%, PF 4.36, top15 share 62.3%, break-even top cut 38/105=36.2%, max daily DD -1.01%.
full_tp1: sum +190.7%, avg +1.82%, PF 4.39, top15 share 60.3%, break-even top cut 39/105=37.1%, max daily DD -1.83%.
Initial conclusion: after strict no-overlap, full_tp1 is not worse; it is slightly better by sum/avg/PF and materially lower top-tail dependence, though daily drawdown is a bit worse on this artifact.
```

## 2026-05-17 - anomaly_lab wide no-overlap exit replay grid

```text
Artifacts: .output/results/anomaly_lab/portfolio_exit_replay_grid, portfolio_exit_replay_grid_mp2, portfolio_exit_replay_grid_mp3.
Grid: current/full_tp1 plus TP1 fractions 0/25/50/75/100%, rest targets 1R/1.5R/2R/2.5R/3R/4R, with and without BE-after-TP1 fallback. Filters: live_priority, context_parity_status=ok, same-symbol-overlap=reject.
max_positions=1: current 105 trades, sum +187.8%, top15 71.3%, break-even top cut 33.3%. Best balance with sum>=current is full_tp1: sum +190.7%, top15 60.3%, break-even cut 37.1%. Top-profit is 0% TP1/rest 4R: sum +195.1%, but top15 83.9% and break-even cut 21.9%, so it is too tail-heavy.
max_positions=2: current 130 trades, sum +232.2%, top15 62.1%, break-even cut 35.4%. Best balance is around 75% TP1/rest 2.5R-4R: 75/rest4R sum +235.7%, top15 53.7%, break-even cut 38.5%; 75/rest2.5R sum +234.0%, top15 53.4%, break-even cut 38.5%.
max_positions=3: current 137 trades, sum +248.6%, top15 59.1%, break-even cut 37.2%. Best balance is 75% TP1/rest4R: sum +253.2%, top15 51.1%, break-even cut 40.1%. Lowest-tail with sum>=current is full_tp1: sum +250.7%, top15 50.6%, break-even cut 39.4%.
BE-after-TP1 variants do not dominate except the degenerate 100% TP1 case; BE fallback generally lowers return without enough tail benefit.
Conclusion: strict one-position replay favors full_tp1 as the cleanest anti-tail rule. With 2-3 concurrent positions, 75% TP1 plus a 2.5R-4R rest target becomes the best compromise, preserving/upgrading return while materially reducing top-tail dependence.
```

## Current experiment policy

Do not claim edge from a single run. Every experiment should record:

```text
period
symbols
cache/data availability
entry method
exit rule
trade count
average trade
winrate
monthly distribution
top-trade dependence
main reject reasons
known leakage/data-quality risks
```

If real trade-count or quote-volume is missing, write that conclusions about anomaly nature are limited.

---

## 2026-05-18 - TOWNS live miss and latency-model follow-up

Input:

```text
Artifact: .output/results/live_anomaly_runs/20260518_090759
Case: TOWNS/USDT:USDT category_selected at 2026-05-18T13:30:08Z, levels=1m, entry=15s, decision_timestamp_ms=1779110985000.
```

Result:

```text
Live selected runner_balanced, then blocked execution at 2026-05-18T13:30:10.188Z with reject_entry_price_drift.
Signal entry was 0.003690; live executable price was 0.003673, a favorable -0.4607% drift, but the guard uses absolute drift with max 0.30%.
The delay was 25.188s from decision candle and 10.188s after the first executable next 15s candle.
Binance aggTrades show the hypothetical trade would have hit stop first at 2026-05-18T13:31:58.200Z before TP1. Backtest-style entry at 0.003690 would be about -2.75%; live accepted entry at 0.003673 would be about -2.30%.
```

Follow-up implemented:

```text
P294 adds opt-in anomaly-lab latency simulation behind one public flag: --latency true.
The hidden defaults run a 10s 1s-cache execution delay and write a 0/7/10/15s latency grid, reusing the primary 10s run for that grid point. Missing 1s latency windows are backfilled from Binance aggTrades during backtest instead of becoming silent synthetic fills.
This is required before changing live drift guards, because a latency grid can show whether the current backtest edge survives realistic live delay.
```

---

## 2026-05-17 - live artifact review 20260517_122732

Input:

```text
Artifact: .output/results/live_anomaly_runs/20260517_122732/1.zip
Window: 2026-05-17 12:27:34Z..15:36:11Z; effective live scan cycles start after context startup around 13:07Z
Universe: 530 symbols after high-cap and 24h quote-volume liquidity filters
Configured pairs observed in scan summaries: 5m/30s, 1m/15s, 1m/5s
Real orders: enabled path appears preflighted, but no order attempt occurred
```

Result:

```text
No evidence of live crash: no internal/data-integrity/order errors, startup exchange-position cleanup found 0 nonzero positions, live_positions.csv is empty.
Data path is usable for flow/tape diagnostics: ws_aggtrade_frame_read covered 7516/7517 requests; ticker radar and top-growth artifacts have real quote_volume, number_of_trades and taker_buy_quote_volume fields.
Funnel: 3465 symbol scan summaries, 7517 evaluated timeframe rows, detected_anomalies_total=398, signal_count=0, category_selected=0, order_attempt_total=0, opened_total=0.
Main rejections: reject_weak_start_flow=4251, reject_setup_too_early=2822, reject_insufficient_real_entry_buckets=14, reject_entry_below_initial_stop=9. Only 3 category_rejected rows were emitted, all for Q; none selected.
Operational bottleneck: symbol_context_snapshot freshness. Startup context backfill was partial and took 2347.6s; rolling updates were skipped 759 times by latency SLA and only 118 partial-budget updates were emitted. 344 decisions were blocked as retryable dependency and 338 expired before context became fresh enough, usually symbol_context_snapshot_tail_stale / reject_prior_fast_fade_filter_unavailable.
Top-growth miss check: EDEN was seen before and during the pump, radar/warm/precise scan all triggered, but no actionable signal/category was produced. Its flow repeatedly failed the strict start-flow contract, often because real trade-count expansion lagged quote-volume/price expansion.
```

Conclusion:

```text
This run does not prove the bot is broken at exchange execution; it proves it is not reaching execution.
There is one fix-worthy live issue: context snapshot freshness cannot keep up under current latency/SLA settings, so otherwise interesting candidates can expire as dependency-not-ready.
There is one strategy/research question, not a bugfix: the strict min start trade/quote pace contract may intentionally reject EDEN-like pumps; do not loosen it without delayed replay / offline counterfactual evidence.
```

Next:

```text
P284 applied locally after this review: priority retryable/active/open symbols can refresh cache-only context even when optional context snapshots are gated by latency SLA.
Run the same live profile with delayed replay enabled; require lower retryable dependency timeouts before evaluating threshold changes.
```

---

## 2026-05-17 - targeted cache refill and backtest for live window

Input:

```text
Symbols: EDEN/USDT:USDT, Q/USDT:USDT, PTB/USDT:USDT
Window end: 2026-05-17 15:36:00Z
Cache refill: update-cache --days 4 --timeframes 1m 5m --with-derivatives-context
AggTrade refill: backfill-anomaly-aggtrade-cache --days 1 --chunk-hours 2
Subminute: materialize-anomaly-subminute-cache --timeframes 5s 15s 30s --overwrite true
Lab pairs: 1m/5s, 1m/15s, 5m/30s
Live-like start params: min_quote_ratio_start=4, min_trade_ratio_start=4, min_price_retention=0.65, min_verticality_score=0.20, confirmation_candles=4, max_initial_risk_pct=0.16, market entry latency=1 candle.
```

Data result:

```text
OHLCV/OI/derivatives update succeeded for all 3 symbols.
aggTrade 1s historical backfill failed for EDEN and Q with Binance 400 Internal error: 1 on old fromId/endTime ranges; PTB succeeded.
Subminute materialization was overwritten successfully. Entry cache now uses cached_1s_aggregated_to_{5s,15s,30s} sources for PTB signals, so PTB trade-count/quote-volume evidence is real aggregated aggTrade cache.
Entry cache coverage still reports symbols_covering_end=0 because the requested end is 15:36:00Z while the last closed subminute candles are 15:35:55/15:35:45/15:35:30. This is expected boundary behavior, not evidence of a full-window gap.
```

Backtest result:

```text
Discovery fallback:
- 1m/5s: PTB only, 26 signals, 4 closed non-overlap trades, 22 overlapping skips, avg net -0.68%, TP1 hit 0%.
- 1m/15s: PTB only, 2 closed trades, avg net -0.68%, TP1 hit 50%.
- 5m/30s: 0 trades.

Live-priority category profiles on 1m/15s:
- runner_oi_confirmed: 0 trades; candidate rejected by mark_basis_below_min.
- runner_flow: 0 trades; no post-filter signal.
- runner_balanced: 0 trades; candidate rejected by mark_basis_below_min.
```

Conclusion:

```text
Backtest can show discovery-fallback PTB trades in this window, but not live-priority runner trades. EDEN/Q did not produce backtest trades under this targeted run despite EDEN being the top-growth missed pump.
The honest explanation for live no-trades is a combination of strict live-priority anomaly category gates, stale/late symbol_context_snapshot causing retryable dependency expiry, and weak/early start-flow rejects. It is not proven to be an exchange order-placement failure.
```

Next:

```text
Run live with P284 and delayed replay enabled, then compare live decisions against strict replay before changing category thresholds.
```

---

## 2026-05-17 - prior fast-fade filter frequency on latest 30d run

Input:

```text
Artifacts: .output/results/anomaly_lab/{1m_5s,1m_15s,5m_30s}/anomaly_candidates.csv
Question: how often does the old prior fake-pump filter reject candidates, and what changes if live uses 24h lookback and allows 2 prior fast-fades?
```

Result:

```text
1m/5s: rows=134013, old prior_fast_fade_72h>1 = 31108 (23.2%), new prior_fast_fade_24h>2 = 21671 (16.2%), released by new policy = 9437 (30.3% of old rejects).
1m/15s: rows=12141, old prior_fast_fade_72h>1 = 1858 (15.3%), new prior_fast_fade_24h>2 = 1114 (9.2%), released by new policy = 744 (40.0% of old rejects).
5m/30s: rows=33446, old prior_fast_fade_72h>1 = 6727 (20.1%), new prior_fast_fade_24h>2 = 4145 (12.4%), released by new policy = 2582 (38.4% of old rejects).
```

Released-bucket quality:

```text
1m/5s released_new_pass: candidates=9437, fast_fade_share=14.6% vs old_pass 8.5%; closed trades=89, WR 56.2%, avg +0.335%, median +0.313%, TP1 53.9%. Old-pass trades: WR 48.8%, avg +0.247%, median -0.018%.
1m/15s released_new_pass: candidates=744, fast_fade_share=32.5% vs old_pass 17.0%; closed trades=34, WR 61.8%, avg +1.038%, median +1.049%, TP1 64.7%. Old-pass trades: WR 56.0%, avg +0.553%, median +0.298%.
5m/30s released_new_pass: candidates=2582, fast_fade_share=27.3% vs old_pass 24.3%; closed trades=33, WR 57.6%, avg +0.381%, median +0.721%, TP1 48.5%. Old-pass trades: WR 57.6%, avg +0.611%, median +0.571%.
```

Interpretation:

```text
The old 72h / max 1 prior fast-fade rule is a frequent filter, not an edge case. Moving to 24h / max 2 materially reduces this rejection source, but still keeps a meaningful fake-pump guard.
The released bucket is riskier at candidate level, especially on 1m/15s, but the simulated closed trades are not worse on 1m/5s and 1m/15s; 5m/30s is weaker than old-pass but still positive in this artifact.
This is not a final profitability proof because it is broad/discovery artifact analysis, not strict post-P285 live-priority parity. Next check must compare live-priority trades/expectancy after the new contract.
```

---

## Next experiment

Run a small anomaly-lab smoke after applying P123-P129:

```bash
python main.py run-anomaly-lab --days 3 --timeframe 1m --run-entry-grid false
```

Goal:

```text
confirm current anomaly CLI runs after cleanup
confirm cache/data-quality statuses are explicit
confirm no retired strategy imports are required
```

Expected output:

```text
anomaly run directory
summary metrics
reject/status distribution
no hidden fallback for core evidence
```


---

## 2026-05-15 — P219 live GWEI crash audit

Input:

```text
Run artifact: .output/results/live_anomaly_runs/20260514_201044
Command: .venv/Scripts/python.exe main.py run-anomaly-live --confirm-real-orders
Observed stop: NameError: name 're' is not defined
```

Result:

```text
The run selected GWEI/USDT:USDT as runner_reclaim on 5m/30s after runner_flow and runner_oi_confirmed rejected.
It reached the real-entry path and emitted danger_local_entry_position_guard_used, then crashed before order submission while building the deterministic entry client order id.
Root cause: _live_client_order_id() used re.sub() but anomaly_micro_live.py did not import re.
Telegram did not alert because live_internal_error used the async queue immediately before shutdown; no telegram_* event was recorded in live_events.csv.
P219 imports re and uses synchronous Telegram delivery for fatal live_internal_error/live_data_integrity_error, with telegram_sync_send_failed artifact on delivery failure.
```

Next:

```text
Run compileall and a short live smoke. Expected: no re NameError on entry-client-id generation; any future fatal internal error produces a Telegram message or telegram_sync_send_failed artifact.
```

---

## 2026-05-15 — Last-15-commit live correctness review

Input:

```text
Scope: git log -15 from bb99a459..91765081 plus current local P219
Focus: run-anomaly-live startup/preflight, WS ticker/aggTrade, warm-watch, cold coverage, cache, order placement, stop protection, Telegram/artifacts
```

Result:

```text
compileall passed and run-anomaly-live --help works.
P219 fixed the observed GWEI pre-order NameError.
One additional live-safety gap was found: if market entry submission was accepted but fill resolution raised before LivePosition/stop creation, live could exit without local position tracking or immediate reduce-only cleanup.
P220 now tracks the symbol before entry submission and calls _close_unprotected_entry_exposure() on any entry order/fill exception.
No PnL/edge conclusion is made from this review; it is an operational correctness audit only.
```

Next:

```text
Run a fake-exchange/synthetic entry-fill-failure smoke: create_market_order_with_fill raises after fetch_symbol_position_amount returns >0, and live must emit unprotected_entry_reduce_only_exit_filled or a hard unprotected_entry_* failure artifact.
```

---

## 2026-05-15 — Live data/processing/threading/accelerator health review

Input:

```text
Run artifact: .output/results/live_anomaly_runs/20260514_201044
Scope: data source labels, OHLCV/aggTrade cache processing, thread boundaries, warm-watch/cold-coverage/context accelerators
```

Result:

```text
Data health was mostly explicit: subminute precise scans used WS aggTrade coverage with quote_volume, number_of_trades and taker_buy_quote_volume derived from exchange aggTrade rows; ticker radar used Binance all-ticker quoteVolume/trade-count deltas and does not synthesize quote volume from base volume.
Run 20260514_201044 had 758 ws_aggtrade_frame_read events, all covered/connected with no missing WS coverage, and 1308 live_ohlcv_cache_read events; 17 cache reads still emitted explicit live_ohlcv_cache_gap instead of silent no-signal evidence.
Processing health is partially limited by optional context throughput: symbol_context_snapshot was mostly partial/skipped under latency SLA, which produced temporary reject_prior_fast_fade_filter_unavailable for GWEI until a cache-only snapshot became available.
Threading health: artifact writes are locked, WS sources use locks, and position monitors use direct exchange OHLCV rather than shared live OHLCV cache. No cache race was found on the monitor path.
P221 was required because append_position had a real post-entry writer crash: scan-mode/guard fields were referenced from undefined writer locals and missing from the live ledger schema.
Accelerators are directionally justified as scheduling/latency tools, not evidence substitutes: warm-watch and ticker radar cannot open trades directly; cold coverage is DANGER idle/audit work and was gated by WS health/latency; context snapshot remains optional but can delay category selection when missing.
```

Next:

```text
Run a short supervised WS-live smoke after P219-P221 and verify no post-entry crash: live_positions.csv must include scan-mode/guard fields, live_events.csv must show position_opened or explicit reject/unprotected_entry_* handling, and Telegram fatal path must be synchronous on any internal error.
```

---

## 2026-05-11 — NVDA micro-live execution audit

Input:

```text
Run artifact: 20260511_134124
Symbol: NVDA/USDT:USDT
Observed issue: plotted entry time near live order handling, but entry price from older signal close
```

Conclusion:

```text
The position is invalid for edge/PnL measurement.
The runner selected an old backfilled signal and then recorded/managed the trade using signal entry price instead of verified exchange fill.
A live-realistic backtest must not enter at decision close for market entries.
```

Action:

```text
P130 proposed: strict fill resolution, stale/executability rejects, actual-fill PnL/TP/BE, and execution-candle market backtest.
```


---

## 2026-05-11 — P131 live blocked-order notification check

Input:

```text
Current local tree after P130
Synthetic selected signals for stale and entry-drift blocks
```

Result:

```text
compileall passed
stale signal emits reject_stale_signal and queues Telegram event
absolute entry drift emits reject_entry_price_drift and queues Telegram event
no market order is needed for either smoke condition
```

Next:

```text
Run one real dry/live observation window and confirm live_events.csv and Telegram event channel agree on blocked-order reasons.
```

---

## 2026-05-11 — P132 live/backtest audit visibility

Input:

```text
Review of P130/P131 local tree
Concern: live/backtest may still hide position-management and skip-reason failures
```

Result:

```text
Patch proposed. Live position safety now emits explicit events for verified stop state, TP1 actual fills, repeated empty monitor OHLCV, emergency reduce-only exits and integrity errors. Backtest writes anomaly_skip_reasons.csv and includes skip_reason:* metrics in profitability summary.
```

Next:

```text
Run fake-exchange smoke for initial stop verification failure, TP1 fill price different from target, and repeated empty monitor OHLCV. Then run one small anomaly backtest and inspect anomaly_skip_reasons.csv before interpreting PnL.
```

---

## 2026-05-11 — P133 Telegram wording cleanup

Input:

```text
Operator-requested Telegram message templates for live start/error/pause/blocked-entry/integrity/open/close/stop events.
```

Result:

```text
Patch proposed. Messages are shorter, symbols are clickable Coinglass links, blocked/integrity messages show concise reasons, and full detail remains in live_events.csv.
```

Next:

```text
Run a Telegram formatting smoke with one fake blocked order and one fake open/close message before unattended live.
```

---

## 2026-05-11 — P134 active-symbol live scheduler

Input:

```text
Operator concern: live should actively follow symbols that are close to actionable state, not only symbols with already open positions. The old explanation exposed a hard-coded active-position path of active + 7 inactive symbols.
```

Result:

```text
Patch proposed. Live now keeps an active-symbol watchlist and schedules active symbols first. Inactive slots are calculated from one configured batch size: active_count=1 => 1 active + batch-1 inactive; active_count=2 => 2 active + batch-2 inactive. Active reasons, batch composition and consumed decision reasons are written to live_events.csv. Signals blocked only by max-open-position capacity remain retryable until expiry.
```

Next:

```text
Run a max-cycles smoke with --symbol-batch-size 20 and inspect active_symbol_marked / symbol_batch_selected events before unattended live.
```

---

## 2026-05-11 — P135 hourly live top-growth artifacts

Input:

```text
Need hourly live records of the top growing symbols, capped at five and only when hourly growth is at least 10%, saved as Notepad-readable files for later backtest/manual analysis.
```

Result:

```text
Patch proposed. Each live run now has a top_growth/ directory with hourly closed-1h top files, matching per-symbol status files and an index CSV. The top file is capped/thresholded; the status file preserves skipped/error reasons so the universe is auditable.
```

Next:

```text
Run live for slightly over one hour, then inspect top_growth_index.csv, top_growth_*.csv and top_growth_status_*.csv before using the snapshot as a candidate source for replay/backtest.
```

---

## 2026-05-11 — P136 live startup bugfix

Input:

```text
Live run stopped on the first cycle with `name 'levels_timeframe_ms' is not defined`, while the operator-facing log incorrectly classified the internal bug as a network/API pause.
```

Result:

```text
Patch proposed. `_build_signal_at_start()` now computes `levels_timeframe_ms` locally. Main live loop catches typed `ExchangeConnectivityError` as retryable network/API degradation, while unexpected internal exceptions become `live_internal_error` artifacts + TG error and stop live.
```

Next:

```text
Apply P136 and rerun `run-anomaly-live --confirm-real-orders` for one smoke cycle. The previous NameError must not appear; any future programming error must be `live_internal_error`, not `network_degraded`.
```


---

## 2026-05-11 — P137 1h overhead-level review dataset

Input:

```text
Current ZIP shows the hourly-level patch was only partially present: `research_tools/hourly_levels.py` exists but is empty, and CLI wiring is absent. Need a trader-like 1h level scanner for all cached coins. Important levels require 3+ hits into a price value, but a hit is valid only when a large bounce followed. Do not mark random wick levels or downtrend pseudo-levels.
```

Result:

```text
Patch proposed. The scanner module is restored and the new run-hourly-levels command exports charts plus hourly_levels_summary.csv and hourly_levels_status.csv. Level context is diagnostic only and can later be compared against top-growth/missed-pump outcomes before becoming a filter.
```

Next:

```bash
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --output-dir results/hourly_levels_manual_review
```


---

## 2026-05-11 — P138 hourly-level chart warning cleanup

Input:

```text
Manual run of run-hourly-levels works, but matplotlib emits repeated missing-glyph warnings for non-ASCII symbols while saving charts.
```

Result:

```text
Patch proposed. Chart titles and file stems now use ASCII-safe symbol text when a symbol contains non-ASCII characters. This addresses the rendering source of the warning instead of globally suppressing matplotlib warnings.
```

Next:

```bash
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --output-dir results/hourly_levels_manual_review
```


---

## 2026-05-11 — P139 hourly-level run observability

Input:

```text
Manual run of run-hourly-levels started successfully, but after "Команда запущена" there was no progress, no current symbol, and no ETA while the full cache scan/chart export was running.
```

Result:

```text
Patch proposed. The scanner now emits an immediate start line and periodic progress lines with processed count, percent, elapsed, ETA, levels found, symbols with levels, charts saved, and last symbol status/reason.
```

Next:

```bash
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --progress-every-symbols 5 --progress-min-seconds 5 --output-dir results/hourly_levels_manual_review
```


---

## 2026-05-11 — P140 hourly-level de-spike review

Input:

```text
Manual review of generated white hourly-level charts showed many false levels: clustered duplicate lines, inflated touch counts from one local approach, full-width horizontal levels, and wick-spiked resistance that should not be treated as clean overhead.
```

Result:

```text
Patch proposed. The scanner now uses the shared dark chart style, requires a meaningful reset before a repeated touch can count, draws each level from its first valid touch, caps nearby major levels, and rejects materially pierced levels by default.
```

Next:

```bash
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --reject-pierced-levels true --max-level-pierce-pct 0.015 --max-levels-per-symbol 4 --progress-every-symbols 5 --progress-min-seconds 5 --output-dir results/hourly_levels_manual_review
```


---

## 2026-05-11 — P141 hourly-level speedup

Input:

```text
Need to reduce time spent scanning hourly levels and generating review charts; current full-cache runs are slower than necessary.
```

Result:

```text
Patch proposed. The scanner reads only needed parquet columns, trims source candles before 1h aggregation, and uses numpy scans for touch/break/pierce validation.
```

Next:

```bash
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --fast-source-trim true --progress-every-symbols 5 --progress-min-seconds 5 --output-dir results/hourly_levels_manual_review
```


---

## 2026-05-11 — P142 hourly-level chart layout warning cleanup

Input:

```text
During fast hourly-level scan, chart export emitted: `UserWarning: This figure includes Axes that are not compatible with tight_layout, so results might be incorrect.`
```

Result:

```text
Patch proposed. Chart export now uses fixed subplot margins instead of `tight_layout()`, so right-side price tags are supported without warning spam and without a per-chart layout solver pass.
```

Next:

```bash
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --fast-source-trim true --output-dir results/hourly_levels_manual_review
```


---

## 2026-05-12 — P143 live scan latency cleanup

Input:

```text
2026-05-11/12 micro-live run: 563 symbols, no positions, all selected signals rejected as stale after roughly 5-8 minutes. Operator requested first clean speedup patch without losing stale-reject artifacts and with top-growth moved out of live.
```

Result:

```text
Patch proposed. Live now fetches each levels timeframe once per symbol per cycle, skips expensive build for stale backfilled decisions while preserving reject_stale_signal artifacts, and runs top-growth only through standalone run-anomaly-top-growth.
```

Next:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --confirm-real-orders --max-cycles 30 --symbol-batch-size 20
python main.py run-anomaly-top-growth --top-growth-min-return-pct 0.10 --top-growth-limit 5
```

Success criteria:

```text
No top_growth_snapshot_started during live.
No duplicate 5m OHLCV fetch per symbol for 5m/30s + 5m/15s.
Stale historical decisions remain visible as reject_stale_signal with stage=prescan.
```

---

## 2026-05-12 — P144 unchanged-candle live scan skip

Input:

```text
Post-P143 live scan still rescans active symbols each cycle even when no new closed levels candle exists.
Operator requirement: active symbols must remain observable, but stale/unchanged scan work should not block cold-universe traversal.
```

Result:

```text
Patch proposed. The runner tracks the last scanned closed candle per symbol/timeframe, skips duplicate OHLCV scans until a new closed candle exists, reports waiting active symbols in symbol_batch_selected, and preserves empty OHLCV artifacts.
```

Next:

```text
Run a 60-cycle live latency smoke and compare symbols_per_minute, batch cycle time, active_waiting_count, reject_stale_signal and signal_scan_empty_ohlcv before implementing ticker-radar promotion.
```

---

## 2026-05-12 — P145 ticker-radar watch promotion

Input:

```text
After P143/P144, operator applied the unchanged-candle scan ledger and asked for the next patch only after checking whether it could lose normal signals.
```

Result:

```text
Patch proposed. Ticker radar is promotion-only and scheduling-only. It adds bounded extra watch scans for symbols with fresh ticker price+real-quote-volume delta, while preserving the normal inactive round-robin batch size. It cannot trade and cannot replace closed-kline flow evidence.
```

Next:

```text
Run a 60-cycle live smoke and compare ticker_radar_promoted, effective_scan_count, inactive_count, reject_stale_signal and cycle time. If extra radar work increases latency or rate-limit pressure, lower ticker_radar_watch_batch_size before adding any stronger scheduler changes.
```


---

## 2026-05-12 — P146 ticker-radar audit cleanup

Input:

```text
After applying P143-P145, operator asked to check the patches for errors before continuing.
```

Result:

```text
Audit found no compile errors, but found scheduler hygiene issues: huge fetch_tickers(symbols) requests are risky on large universes, radar-waiting symbols could waste inactive slots, and radar watch state was not cleared on active promotion. P146 proposed targeted cleanup without changing signal logic.
```

Next:

```text
Apply P146, then run a 60-cycle live smoke with ticker_radar_watch_batch_size=2 before increasing radar pressure.
```


---

## 2026-05-12 — P149 strict 1h level filter

Input:

```text
Operator found false 1h levels: pierced levels, touches repeated too close together, and levels drawn by body/interior candle intersections instead of highs.
```

Result:

```text
Patch proposed. The hourly-level diagnostic now requires high-based touches, 6h spacing between counted touches, and strict pierced-level rejection by default. This is diagnostics-only and should be validated by comparing emitted charts/CSV counts before using levels as continuation context.
```

Next:

```text
Run run-hourly-levels on the same cache/output window used for the rejected examples and inspect whether body/interior and wicked-through levels disappear without losing clean high-touch levels.
```

---

## 2026-05-12 — P150 source-candle close-above guard

Input:

```text
Operator clarified that a 1h level must not be built from a candle if there was a close above that would-be level during the previous 12h.
```

Result:

```text
Patch proposed. Pivot/high source candles are now filtered before clustering when any close in the previous 12h is above the candidate candle high. This is diagnostics-only and should remove stale/reclaimed resistance sources from run-hourly-levels output.
```

Next:

```text
Run run-hourly-levels on the same rejected examples and confirm that levels sourced from candles below a prior 12h close disappear while clean untouched high-based levels remain.
```


---

## 2026-05-12 — P151 trade-chart context layout

Input:

```text
Operator requested trade-chart cleanup: merge volume and relative trade-count into one bottom line panel, then use the freed middle space for independent 1h context over 7 days with found levels marked silently.
```

Result:

```text
Patch proposed. Trade chart rendering is now artifact-only three-panel layout: upper trade window, middle 1h/7d context with strict hourly levels as unlabeled lines, lower normalized quote-volume and number_of_trades line curves.
```

Next:

```text
Regenerate a small chart batch and visually verify that the context panel shows useful 7-day 1h structure without time-locking to the trade window, and that lower orange/green curves are readable on volatile symbols.
```

---

## 2026-05-12 — P152 live open chart attachment

Input:

```text
Operator requested the P151 three-panel trade chart in the live opening message, with TP1/SL levels instead of risk/reward rectangles.
```

Result:

```text
Patch proposed. Live now renders an open-position chart after verified fill/stop creation and sends it as the Telegram opening message photo when possible. The renderer accepts an explicit hourly context frame, so live can fetch 7 days of 1h context separately from the small trade-window frame.
```

Next:

```text
Run one controlled dry/live open-message smoke and verify: photo is sent, caption matches the old open text, TP1 is green, SL is red, no risk/reward rectangles are shown, and close/stop messages still reply to the opening message id.
```
---

## 2026-05-12 — P153 trade-chart level-search window

Input:

```text
Operator clarified that levels on the live/open trade chart must be searched only on the 7-day 1h context interval shown in the chart.
```

Result:

```text
Patch proposed. Trade-chart level discovery now explicitly slices the supplied hourly context to the same [entry_hour - 7d, entry_hour) window before running the strict hourly-level scanner. Older hidden context cannot contribute levels to the visible 1h panel.
```

Next:

```text
Regenerate an open-position chart for a symbol with older resistance history and verify that levels without touches inside the visible 7-day context no longer appear.
```

---

## 2026-05-12 — P154 Telegram style normalization

Input:

```text
Operator requested unified Telegram text style: monospace TF/context lines, monospace error payloads, deterministic animal emoji per symbol, and fixed service icons for non-symbol messages.
```

Result:

```text
Patch proposed. Telegram formatter paths now share deterministic symbol emoji assignment and consistent code formatting for service context and errors. This is UI-only and does not alter live execution.
```

Next:

```text
Trigger one startup/error/blocked/open-message smoke and verify Telegram rendering: context lines are monospace, error text is monospace, and the same symbol keeps the same animal emoji across open/stop/close/blocked messages.
```

---

## 2026-05-12 — P155 live audit failure visibility

Input:

```text
Operator asked whether live run problems are fully visible in logs/artifacts and approved adding missing audit events.
```

Result:

```text
Patch proposed. Live now records network degradation/recovery, Telegram async/photo failures, open/close message fallbacks, and stop-cooldown rejects in live_events.csv without changing trading behavior.
```

Next:

```text
Run a controlled smoke with a fake/invalid Telegram token or blocked network path and verify live_events.csv contains telegram_async_send_failed or telegram_open/close fallback events instead of relying on console logs only.
```

---

## 2026-05-12 — P161 anomaly market-entry NameError

Input:

```text
Backtest run crashed at anomaly trades simulation with `NameError: name '_safe_divide' is not defined` in `_resolve_signal_entry()`.
```

Result:

```text
Patch proposed. Market-entry drift and RR guards now call the existing `_safe_divide_value` helper in `anomaly_strategy_backtest.py`; execution model and thresholds are unchanged.
```

Next:

```text
Rerun the same anomaly backtest. Expected outcome: no `_safe_divide` NameError; inspect generated skip-reason artifacts before interpreting PnL.
```

---

## 2026-05-12 â€” anomaly_lab 1m/1m closed-setup review

Input:

```text
Artifact: .output/results/anomaly_lab
Config: setup_timeframe=1m, entry_timeframe=1m, feature_contract=closed_setup_tf_v1
Execution model: next_bar_open_proxy_latency_1
Observed entry interval: 2026-05-04 13:48 UTC -> 2026-05-11 08:32 UTC
```

Result:

```text
Candidates: 44,824
Signals: 1,121
Closed trades: 1,095
Skipped trades: 26
Symbols with closed trades: 476

Win rate: 45.57%
Average net return: -0.0760%
Median net return: -0.1810%
Sum net return: -83.20%
TP1 hit rate: 43.93%

Positive trade sum: +570.79%
Negative trade sum: -653.99%
Top 5 winners sum: +65.78%
Top 15 winners sum: +145.58%
```

Consistency check:

```text
Only 2 of 8 active days were positive.
Best day: 2026-05-05, +0.96%
Worst day: 2026-05-04, -27.27%
Only month present: 2026-05, so no month-level robustness can be claimed.
```

Main execution skips:

```text
market_entry_rr_collapsed: 10
overlapping_signal: 9
invalid_actual_market_risk: 6
market_entry_price_drift: 1
```

Data-quality notes:

```text
All closed trades show flow_taker_buy_status=ok and oi_status=ok.
However, exported anomaly CSVs do not include explicit provenance fields such as:
trade_count_proxy_used
levels_trade_count_source / entry_trade_count_source
levels_quote_volume_source / entry_quote_volume_source

Therefore conclusions about tape/flow/organic anomaly nature remain limited at artifact-review level.
```

Conclusion:

```text
This run does not support a profitable or robust edge for the tested 1m/1m closed-setup contract.
High trade count is not the bottleneck; expectancy and day-level stability are.
Do not optimize thresholds from this artifact before provenance is explicit and before comparing against the pair-aware HTF/LTF contract on the same window.
```

---

## 2026-05-12 - P163 provenance export and chart layout correction

Input:

```text
Operator asked to execute the next-best research step and update canonical charts:
explicit artifact provenance, chart panel reorder, 4-day 1h context, Russian panel labels, and denser candle readability.
```

Result:

```text
Patch applied locally.
Fresh anomaly-lab exports now carry:
trade_count_proxy_used
levels_trade_count_source / entry_trade_count_source
levels_quote_volume_source / entry_quote_volume_source

Canonical chart order is now:
LTF -> HTF -> volume/trades -> 1h.
The 1h panel covers 4 days, ends at the hour candle that contains the main chart end, and level search uses exactly that 4-day window.
```

Next:

```text
Validate P163 with compile/render smoke, rerun anomaly-lab on the existing 1m/1m window so provenance is materialized in CSV artifacts, then compare against the same-window pair-aware HTF/LTF contract before tuning filters.
```

Validation:

```text
compileall passed.
run-anomaly-lab --help passed.
Synthetic render smoke saved .output/results/anomaly_lab_p163_chart_smoke.png.
Fresh 1m/1m run completed at .output/results/anomaly_lab_p163_1m1m.
Fresh 5m/1m run completed at .output/results/anomaly_lab_p163_5m1m.
Provenance columns exist in candidates/signals/trades for both fresh runs.
```

Same-window comparison:

```text
End timestamp: 1778488560000

1m/1m closed_setup_tf_v1:
closed trades: 1105
skipped trades: 26
symbols: 480
win rate: 45.79%
avg net return: -0.0568%
median net return: -0.1794%
sum net return: -62.73%
TP1 hit rate: 44.16%
positive days: 1 of 8
worst day: 2026-05-10, -21.99%

5m/1m htf_setup_ltf_entry_v1:
closed trades: 423
skipped trades: 1
symbols: 305
win rate: 47.75%
avg net return: -0.0521%
median net return: -0.2407%
sum net return: -22.05%
TP1 hit rate: 45.15%
positive days: 4 of 8
worst day: 2026-05-06, -16.32%
```

Conclusion:

```text
HTF/LTF is less damaging and more evenly distributed by day than 1m/1m, but still negative. It is not a tradable edge. The useful follow-up is category/rejection research: separate losing wake-up types from the minority that can reach TP1/trail, instead of tightening generic entry parameters.
```

Correction:

```text
The 5m/1m comparison was only a cheap proxy and is not one of the intended TF sets.
Do not use it as a trading-grid result.
The intended TF sets are 5m/30s, 1m/15s, and 1m/5s.
```

---

## 2026-05-12 - Intended subminute TF-set run attempt

Input:

```text
Requested TF sets:
5m/30s
1m/15s
1m/5s
End timestamp: 1778488560000
```

Initial result:

```text
All three commands hit a diagnostics crash before writing normal no-signal artifacts:
KeyError: None of [Index(['symbol', 'decision_timestamp_ms'], dtype='object')] are in the [columns]
```

Cause:

```text
The local cache has 1m, 5m and 1s directories visible, but not 30s/15s/5s directories. Pair-aware subminute backtest can therefore produce no valid signal frame. The derivative-context fetch setup did not handle an empty no-column signals frame.
```

Action:

```text
P164 applied locally: empty/no-column post-filter signals now produce an empty signal universe instead of crashing.
Because 350 symbols have `1s` cache, pair-aware backtest can now aggregate `1s` to the intended `30s/15s/5s` entry frames in memory.
Fresh artifacts must label entry flow source as `cached_1s_aggregated_to_<tf>`.
Second fix: trade simulation now uses the same aggregated entry frame instead of looking for a non-existent exact `30s/15s/5s` cache file.
```

Validation result:

```text
5m/30s artifact: .output/results/anomaly_lab_p164_5m30s_from1s
1m/15s artifact: .output/results/anomaly_lab_p164_1m15s_from1s
1m/5s artifact: .output/results/anomaly_lab_p164_1m5s_from1s
Metric analysis: .output/results/anomaly_metric_analysis_tfsets
```

Performance:

```text
5m/30s: 17 closed trades, avg +0.1281%, sum +2.18%, WR 52.94%, TP1 47.06%
1m/15s: 44 closed trades, avg +0.5832%, sum +25.66%, WR 70.45%, TP1 65.91%
1m/5s: 37 closed trades, avg +0.0793%, sum +2.94%, WR 54.05%, TP1 51.35%
Combined: 98 closed trades, avg +0.3140%, sum +30.78%, WR 61.22%, TP1 57.14%
```

Pattern readout:

```text
Best current TF set is 1m/15s, but sample is only 44 trades.
Best broad nature combos require clean path + high retention + large ticket + taker improvement.
Positive mark basis is the strongest single pre-entry separator in this sample.
Fast_fade rows are strongly negative, but only 4 examples.
Do not build a confident production grid until the same TF-set logic is rerun on a longer 1s-backed window.
```

Leakage audit:

```text
Executable filters in build_anomaly_signals use pre-decision columns: retention/hold/risk/OI/context and configured min/max fields.
future_high, future_low, future_ret_high_after_decision, future_dd_low_after_decision, outcome_label, exit_reason, tp1_hit and return columns are diagnostics only and are not part of current signal filtering.
The next-bar market-entry resolver intentionally reads candles after decision for execution simulation. This is not a signal leak, but it is still an execution-model assumption and should be stress-tested with slippage/spread.
Fields named next_n_* are misleading in pair-aware mode: they are computed from the closed LTF confirmation segment before the decision timestamp, not from candles after decision.
```

Run-period clarification:

```text
Commands used --days 7 --end-timestamp-ms 1778488560000.
Closed true-TF trades are concentrated on 2026-05-04, 2026-05-05 and 2026-05-06 UTC only.
Counts by TF set:
5m/30s: 4 / 10 / 3
1m/15s: 12 / 29 / 3
1m/5s: 12 / 23 / 2
Total: 98 closed trades over 3 active UTC days.
```

Red-flag hypotheses to test first:

```text
1. mark_discount_or_flat, especially with oi_down_price_up.
2. stale/missing derivatives context.
3. excessive taker-buy quote-share delta at start/confirmation.
4. high quote/trade effort per unit of price displacement.
5. future diagnostic fast_fade label must not be used directly; derive only pre-entry proxies for it.
```

Bottleneck notes:

```text
5m/30s took about 8.8 minutes, 1m/15s about 26.1 minutes, and 1m/5s about 36.8 minutes.
The slow part is candidate construction over 350 symbols with 1s parquet reads, in-memory 1s->target aggregation and repeated LTF segment scans.
Derivative-context enrichment and trade/chart simulation are secondary for these runs.
Speed patch priority: materialize honest 1s-derived 5s/15s/30s caches with source provenance, then reuse them across TF-set runs.
```

---

## 2026-05-12 - P165 subminute cache / red-flag validation

Coverage finding:

```text
Requested end timestamp: 1778488560000 = 2026-05-11T08:36:00Z
Requested start timestamp: 2026-05-04T08:36:00Z
Setup cache coverage: 1m/5m reaches requested end.
Executable subminute cache coverage: 1s-derived 5s/15s/30s reaches only 2026-05-06T11:40:xxZ.
Therefore the previous "7 day" true-TF result is actually a 3-active-day executable-data sample, not evidence that four days had no trades.
```

Materialization:

```text
Command: materialize-anomaly-subminute-cache --timeframes 5s 15s 30s --overwrite true
Manifest: .output/results/anomaly_subminute_cache_materialization_p165.csv
Written rows: 1050 cache files = 350 symbols * 3 target TFs.
Source remains cached 1s OHLCV; no 1m fallback and no fabricated missing days.
```

Speed result:

```text
Old true-TF runs:
5m/30s ~8.8m
1m/15s ~26.1m
1m/5s ~36.8m

P165 after materialized cache + searchsorted slicing:
5m/30s cautious ~4.6m
1m/15s cautious ~3.4m on first post-optimization run, ~5.6m on rerun
1m/5s cautious ~11.2m
```

Red-flag profile result on same incomplete executable-data window:

```text
5m/30s cautious:
closed trades = 8
avg net return = +0.3529%
sum net return = +2.82%
WR = 62.50%
TP1 = 62.50%

1m/15s cautious:
closed trades = 18
avg net return = +1.1566%
sum net return = +20.82%
WR = 83.33%
TP1 = 72.22%
pre-red-flag signal universe = 33
any red flag = 15
mark_basis_below_min = 15
oi_down_with_mark_discount = 9
stale_or_missing_market_context = 2

1m/5s cautious:
closed trades = 10
avg net return = +0.9471%
sum net return = +9.47%
WR = 100.00%
TP1 = 100.00%

Combined cautious:
closed trades = 36
avg net return = +0.9198%
sum net return = +33.11%
WR = 83.33%
TP1 = 77.78%
```

Interpretation:

```text
The cautious red flags cut frequency hard and improve the same-window metrics, but this is not yet proven edge because the executable-data sample is only 3 active UTC days.
Do not trust the 100% WR on 1m/5s as stable.
The next required test is to extend/fix 1s coverage to the requested end and rerun the same base-vs-cautious comparison without changing thresholds.
```

---

## 2026-05-12 - Dormancy / recent-spike sanity check

Question:

```text
Does 98 trades in 3 active days mean the strategy is not really detecting multi-day sleep?
Should recent similar spikes / recent failed spikes be cheap red flags?
```

Current implementation read:

```text
The current "sleep" proxy is local: baseline_candles=60 on the setup timeframe.
That means about 60 minutes for 1m setup and about 5 hours for 5m setup.
It is not a real 24h/72h dormancy condition.
```

Quick artifact check:

```text
Sample: 98 closed true-TF P164 trades from 2026-05-04..2026-05-06 UTC.
Proxy: count previous same-symbol anomaly candidates in the prior 72h from the same artifact family.

prior_72h_candidates = 0:
n = 23, avg = -0.0431%, WR = 60.87%, sum = -0.99%

prior_72h_candidates > 0:
n = 75, avg = +0.4235%, WR = 61.33%, sum = +31.77%

prior_72h_candidates >= 2:
n = 56, avg = +0.5647%, WR = 62.50%, sum = +31.62%

prior_72h_candidates >= 5:
n = 26, avg = -0.0214%, WR = 46.15%, sum = -0.56%

prior_72h_fast_fades > 0:
n = 1, avg = -2.1897%, WR = 0.00%
```

Interpretation:

```text
Do not filter all recent activity. Repeated attention can mean the symbol is in an active theme and still tradable.
The better hypothesis is serial failed/overcrowded wake-ups: many recent similar spikes, especially if mature prior spikes faded quickly.
This must be implemented first as diagnostics: prior_spike_count_24h/72h, prior_fast_fade_count_24h/72h, time_since_prior_spike, and prior_spike_density.
Only then test a cheap red flag such as prior_spike_count_72h >= 5 or prior_fast_fade_count_72h > 0.
```

---

## 2026-05-12 - P166 14d runner-oriented anomaly study

Cache/backfill result:

```text
`update-cache --timeframes 1s` is not valid for Binance futures: fapi klines rejects interval=1s.
Implemented explicit aggTrades -> 1s backfill.
Smoke backfill:
COIN/USDT:USDT + HOOD/USDT:USDT, 1 day, chunk-hours=24, status ok.
Materialized 1s -> 5s/15s/30s after the smoke.
Full aggTrades backfill for 350+ symbols * 14 days is too slow to run blindly in this session.
```

Run window:

```text
days = 14
end = 1778488560000 = 2026-05-11T08:36:00Z
render_charts = false
```

Coverage caveat:

```text
Setup cache covers the requested end.
Subminute cache is improved by the smoke backfill but still not complete for the whole universe.
Full-universe 14d results are valid for symbols/windows with executable subminute cache and must be read with `entry_cache_coverage.csv`.
Full 1m/5s universe did not complete within 1 hour, so the 5s result below is active-symbol subset only.
```

Results:

```text
5m/30s base full:
closed = 60, skipped = 1
avg = +0.0418%, sum = +2.51%, WR = 50.00%, TP1 = 45.00%, runner = 41.67%

5m/30s cautious full:
closed = 32
avg = +0.8597%, sum = +27.51%, WR = 65.63%, TP1 = 59.38%, runner = 56.25%

1m/15s base full:
closed = 148, skipped = 1
avg = +0.6110%, sum = +90.43%, WR = 63.51%, TP1 = 60.14%, runner = 59.46%

1m/15s cautious full:
closed = 62
avg = +1.4642%, sum = +90.78%, WR = 80.65%, TP1 = 77.42%, runner = 75.81%

1m/5s active-symbol subset base:
closed = 81, skipped = 2
avg = +0.3455%, sum = +27.98%, WR = 55.56%, TP1 = 55.56%, runner = 54.32%

1m/5s active-symbol subset cautious:
closed = 17
avg = +1.1908%, sum = +20.24%, WR = 88.24%, TP1 = 82.35%, runner = 82.35%
```

Runner metric read:

```text
Full base sample = 5m/30s + 1m/15s = 208 closed trades, avg +0.4468%, sum +92.94%, WR 59.62%, runner 54.33%.

Strongest early runner separators:
1. Positive/large mark basis versus decision close.
   Lowest quartile avg -0.6422%, runner 34.62%; highest quartile avg +1.9295%, runner 71.15%.
2. Mark close momentum in the 1/3/6 context bars.
   mark_close_change_pct_6 lowest quartile avg -0.2385%, runner 27.50%; highest quartile avg +1.8409%, runner 66.67%.
3. Low effort per price displacement.
   start_trade_ratio_per_abs_return highest quartile avg -0.3236%, runner 38.46%; lowest quartile avg +1.1711%, runner 61.54%.
   start_quote_ratio_per_abs_return highest quartile avg -0.3526%, runner 42.31%; lowest quartile avg +1.0825%, runner 63.46%.
4. Moderate hold ratio is better than extremes.
   hold_ratio 0.5..0.75 avg +0.8890%, runner 63.77%; low hold avg +0.2142%, high hold avg +0.2499%.
5. Recent prior spike is not automatically bad.
   time_since_prior_spike 2.4h..4.7h avg +0.9479%; >10.8h avg -0.2042%.
```

Interpretation:

```text
The runner signature is not "first spike after total dormancy".
The better early runner profile is: real mark-led displacement, broad enough continuation, not too much quote/trade effort per unit return, and not a stale isolated one-off.
Current cautious filters improve runner rate and expectancy, but they still need a clean full subminute backfill before being treated as production grid.
```

---

## 2026-05-13 - P167 live scan scheduling prep

Hypothesis:

```text
Backtest does not miss closed-candle signals because it scans the whole window. REST-only live cannot do full universe * all subminute TFs every cycle cheaply, so it should spend heavy aggTrade/entry-frame work only on hot symbols and scan all due TF sets for each selected symbol before moving on.
```

Implementation to validate:

```text
scan_hot_timeframes_per_symbol = true
inactive_scan_slots_per_cycle = 0 for production-fast smoke
ticker radar remains scheduling-only
signal_symbol_scan_summary must show per-symbol due/evaluated/fetch counts
```

Next smoke:

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20 --inactive-scan-slots-per-cycle 0 --ticker-radar-watch-batch-size 10 --scan-hot-timeframes-per-symbol true
```

Readout:

```text
If ticker_radar_snapshot is healthy and symbol_batch_selected shows active/radar-driven batches with low duration_ms in signal_symbol_scan_summary, keep this as live baseline.
If ticker radar has missing fields or no promotions, do not treat active/radar-only mode as coverage-complete; increase inactive_scan_slots_per_cycle or fix ticker data first.
```

---

## 2026-05-13 - P168 live cache-backed fetch prep

Hypothesis:

```text
For REST-only live, selected-symbol latency is dominated by repeated full-window OHLCV and subminute aggTrade fetches. A cache-backed provider should make repeated scans mostly local, fetch only exact missing ranges, and improve later replay by writing live-fetched rows with provenance.
```

Implementation to validate:

```text
live_ohlcv_cache_enabled = true
live_ohlcv_cache_write_enabled = true
setup and entry frames both use parquet-tail-fetch
process memory cache avoids re-reading parquet every cycle
remaining cache gaps emit live_ohlcv_cache_gap
```

Next smoke:

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20 --inactive-scan-slots-per-cycle 0 --ticker-radar-watch-batch-size 10 --scan-hot-timeframes-per-symbol true --live-ohlcv-cache-enabled true --live-ohlcv-cache-write-enabled true
```

Readout:

```text
Expected: first scan for a hot symbol may show status=filled and fetched_rows>0; repeated scans should move toward status=hit or load_status=memory_hit.
Any live_ohlcv_cache_gap means the signal evidence is incomplete and should be investigated before treating missed/no-signal rows as meaningful.
```

---

## 2026-05-13 - P169 live cache/exchange boundary audit

Audit result:

```text
Order/fill/position paths were not changed by P168; monitor OHLCV still goes directly to exchange, which is correct for position safety.
Signal setup/entry data path had one important boundary risk: cache missing ranges are candle-start based, but aggTrades must be fetched through the final candle end.
```

Fix:

```text
Parquet first-load uses timestamp window filters.
Subminute missing range fetch uses missing_end + timeframe_ms - 1.
Paged aggTrades calls include endTime.
Decision frames are clipped to expected closed-candle end.
```

Live-smoke readout:

```text
For each live_ohlcv_cache_read row, expected_end_ms/window_end_ms should equal the latest closed candle start for that timeframe.
For subminute filled rows, fetched_ranges should end at candle_end_ms, not candle_start_ms.
Remaining live_ohlcv_cache_gap rows are hard data-quality signals, not no-signal evidence.
```

---

## 2026-05-13 - P170 buffered live cache writer

Hypothesis:

```text
The safe live speedup after cache-backed reads is to move parquet rewrites out of the signal fetch path. Decisions can use fetched rows immediately from process memory; persistence can flush by interval/row cap with artifacts.
```

Implementation:

```text
live_ohlcv_cache_flush_interval_seconds default = 10
live_ohlcv_cache_max_buffer_rows default = 5000
flush on normal cycle when due, max-cycles, keyboard interrupt, data-integrity stop and internal-error stop
flush failures remain visible and pending
```

Readout:

```text
Healthy live should show live_ohlcv_cache_buffered followed by live_ohlcv_cache_flush_summary with remaining_rows=0.
If remaining_rows grows, parquet writes are the bottleneck or failing; trading decisions may still be using fresh memory rows, but cache/replay completeness is degraded until flush succeeds.
```

---

## 2026-05-13 - P171 anomaly lab default TF-set run

Experiment contract:

```text
Command: python main.py run-anomaly-lab --days 7
Expected TF sets: 5m/30s, 1m/15s, 1m/5s
Expected artifact layout: output_dir/5m_30s, output_dir/1m_15s, output_dir/1m_5s plus anomaly_lab_timeframe_runs.csv
Legacy single 1m/1m runs must be explicit, not parser default.
```

Readout:

```text
After the next real run, analyze per-pair summaries from the indexed subdirectories only.
If a root-level output exists from an older 1m/1m run, treat it as stale unless its timestamp and run index prove otherwise.
```

---

## 2026-05-13 - Indexed anomaly-lab live readiness analysis

Data:

```text
Path: .output/results/anomaly_lab
Included: indexed subdirs 5m_30s, 1m_15s, 1m_5s
Excluded: root-level CSVs/charts from older single-run artifacts
Feature contract: htf_setup_ltf_entry_v1
Entry sources: cached_1s_aggregated_to_30s/15s/5s with real number_of_trades and quote_volume sources
```

Result:

```text
5m/30s: 17 closed, avg +0.1281%, sum +2.18%, WR 52.94%, TP1 47.06%
1m/15s: 44 closed, avg +0.5832%, sum +25.66%, WR 70.45%, TP1 65.91%
1m/5s: 37 closed, avg +0.0793%, sum +2.94%, WR 54.05%, TP1 51.35%
Combined: 98 closed, avg +0.3140%, median +0.2478%, sum +30.78%, WR 61.22%, TP1 57.14%
Active trade days: 2026-05-04..2026-05-06 UTC only
```

Reliability:

```text
Not production-live-ready. The evidence is promising but too short and top-tail sensitive.
Top 5 winners contribute +21.79%; removing them leaves +8.98%.
Removing top 10 winners leaves -3.90%, so the current result is not robust enough.
Entry cache coverage has symbols_covering_end=0 for all entry TFs; the run requested through 2026-05-13 but executable subminute coverage ends around 2026-05-11 08:35Z.
```

Live-roadmap implication:

```text
Do not launch real orders from this evidence alone.
Next most valuable work is a strict paper/shadow live readiness run with the same TF arbitration as production, explicit cache/exchange coverage telemetry, would-enter/would-skip diagnostics, and fillability checks.
Candidate filters to validate before enabling: mark basis > 0, flow_hold>=1, effort-per-return cap, extreme quote/trade ratio cap, recent serial failed-spike/fast-fade context.
```

---

## 2026-05-13 - P172 symbol-major multi-TF backtest collector

Implementation:

```text
Default run-anomaly-lab multi-TF mode precollects candidates once across 5m/30s, 1m/15s, 1m/5s.
The collector loops symbols first, reads required setup/entry cache frames once per symbol, and evaluates all eligible TF configs before the next symbol.
Each pair still writes its own candidates/signals/trades/charts/run_config artifacts.
```

Validation:

```text
Compileall passed.
Monkeypatched CLI smoke confirmed one collector call and three per-pair artifact pipeline calls receiving precollected candidates.
Single vs multi collector comparison for ATH/USDT:USDT 1m/15s over 7 days matched candidate timestamp 1777885245000.
```

Next readout:

```text
Run a fixed-end small sample and compare candidate/signal/trade counts against the previous sequential collector before using any profitability changes.
```

---

## 2026-05-13 - P173 runner-category filter replay

Input:

```text
Path: .output/results/anomaly_lab
Completed pairs: 5m_30s, 1m_15s
Interrupted/missing pair: 1m_5s
Requested days: 30
Actual closed-trade range: 2026-04-11..2026-05-06 UTC
Coverage caveat: subminute entry cache has symbols_covering_end=0
```

Baseline completed pairs:

```text
5m/30s: 157 closed, avg +0.6001%, median +0.5604%, sum +94.22%, WR 57.32%, TP1 51.59%
1m/15s: 410 closed, avg +0.5931%, median +0.2762%, sum +243.16%, WR 56.59%, TP1 55.12%
Combined: 567 closed, avg +0.5950%, median +0.3274%, sum +337.38%, WR 56.79%, TP1 54.14%
```

Runner-balanced replay:

```text
Command: python main.py run-anomaly-lab --days 30 --reuse-candidates-dir .output/results/anomaly_lab --output-dir .output/results/anomaly_lab_reuse_runner_balanced_fast --red-flag-profile runner_balanced --render-charts false
5m/30s: 42 closed, avg +1.6435%, median +1.5601%, sum +69.03%, WR 83.33%, TP1 78.57%
1m/15s: 82 closed, avg +1.8407%, median +1.2530%, sum +150.94%, WR 79.27%, TP1 74.39%
Combined: 124 closed, avg +1.7739%, median +1.3985%, sum +219.97%, WR 80.65%, TP1 75.81%, active days 25
```

Interpretation:

```text
Runner-balanced is the best current category hypothesis. It separates clean runners from fast-fade/overheated/noisy pumps much better than the base filter.
Do not treat as proven edge yet: replay is in-sample, 1m/5s is missing, and executable coverage is incomplete at the requested end.
```

---

## 2026-05-13 - P174 OI-confirmed runner category replay

Input:

```text
Source candidates: .output/results/anomaly_lab
Completed pairs: 5m_30s, 1m_15s
Missing pair: 1m_5s
Command: python main.py run-anomaly-lab --days 30 --reuse-candidates-dir .output/results/anomaly_lab --output-dir .output/results/anomaly_lab_reuse_runner_oi_confirmed --red-flag-profile runner_oi_confirmed --render-charts false
```

OI readout:

```text
Baseline completed-pair sample: OI > 0.3% improved avg/WR versus flat or negative OI; OI > 5% was only 4 trades and negative.
Runner-balanced sample: OI > 0.3% produced 43 trades, avg +2.486%, WR 88.37%, TP1 83.72%.
Runner-balanced + OI > 0.3% + mark basis >= 30bp produced 36 trades, avg +2.831%, WR 88.89%, TP1 86.11%.
```

Result:

```text
5m/30s runner_oi_confirmed: 15 closed, avg +2.574%, median +1.717%, sum +38.61%, WR 100.00%, TP1 100.00%
1m/15s runner_oi_confirmed: 21 closed, avg +3.014%, median +2.710%, sum +63.29%, WR 80.95%, TP1 76.19%
Combined: 36 closed, avg +2.831%, median +1.885%, sum +101.90%, WR 88.89%, TP1 86.11%, top10 dependency 75.58%
```

Interpretation:

```text
OI should be enforced as moderate confirmation for live category selection. Do not require OI > 5%; it is too sparse and looked bad in this run.
The category remains in-sample and narrow. Next validation is shadow-live parity plus complete 1m/5s coverage, not stronger thresholds.
```

---

## 2026-05-13 - P175 live entry lag diagnostics

Patch:

```text
Added first-executable entry lag diagnostics to live opened positions and order-block/reject events.
The metric compares decision_timestamp_ms + entry_tf_ms against order submit/fill/check timestamps.
entered_late_vs_first_executable is true when lag is at least one entry LTF candle.
Added scheduler scan-gap fields so live can show when a symbol/TF was not scanned for N closed LTF candles before the detected signal.
Added non-blocking missed_entry_replay_probe to identify the first skipped LTF decision candle where the same live filter would already have selected a signal.
```

Validation:

```text
Compileall passed for research_tools/anomaly_micro_live.py.
Synthetic 1m/15s replay smoke found the expected first prior signal and lag count.
```

Next readout:

```text
Run shadow/micro-live and group position_opened/reject_entry_price_drift/reject_rr_collapsed/reject_stale_signal by entry_lag_ltf_candles.
If profitable-looking signals cluster in rejected lag >= 1, scheduler latency is still a primary blocker.
```

---

## 2026-05-13 - P176 live correctness review

Finding:

```text
The missed-entry replay probe was non-blocking, but with a large skipped interval it checked the newest skipped candles first while reporting first_prior_signal_found. That could understate the real live-vs-backtest scheduler lag.
```

Patch:

```text
Probe skipped LTF decisions from the earliest missed candle, emit truncation fields, and expose --signal-scan-backfill-candles for scarce API-limit sessions.
```

Next live-readout:

```text
For scarce limits start shadow/test live with a low signal-scan-backfill-candles value and monitor missed_entry_replay_probe status/probe_truncated plus live_ohlcv_cache_read filled/gap rates.
```

---

## 2026-05-13 - P177 live latency review

Readout from `.output/results/live_anomaly_runs/20260513_131621`:

```text
Full symbol cycle: roughly 10-12.5 minutes.
Last 50 cycles: batch_seconds avg 21.65s, p50 21.64s, max 38.97s.
Inactive symbols are nearly free because subminute pairs are deferred.
Precise ticker-radar/active symbols cost roughly 2s median and up to 9.75s each, mostly from subminute aggTrade tail fetches.
```

Decision:

```text
Do not add another per-symbol 1m/5m OHLCV wake-up yet; it risks becoming a hidden second scan path.
Add timing attribution and explicit precise-scan budget first.
```

Next live-readout:

```text
Run a short live sample and compare ticker_radar_seconds, signal_scan_seconds, cache_flush_seconds, precise_scan_symbols, ticker_radar_waiting_count, and aggtrade_network_calls.
```

---

## 2026-05-13 - P178 live high-cap exclusion

Decision:

```text
Use a conservative static list for default live universe only. Do not call CoinGecko or another external source.
This is a universe narrowing choice, not proof of edge.
```

Expected readout:

```text
Next live run should contain live_symbol_universe_filter with excluded_count and excluded_symbols.
Compare cycle timings before/after, but do not interpret PnL improvement as edge until the excluded universe is disclosed.
```

---

## 2026-05-13 - Live timing diagnostic run 20260513_164045

Input:

```text
Artifacts: .output/results/live_anomaly_runs/20260513_164045
Duration: 69 completed live_cycle_summary rows
Universe: 563 symbols; run predates static high-cap exclusion because live_symbol_universe_filter is absent.
Orders: 0 opened, 0 closed
```

Timing readout:

```text
batch_seconds avg 16.259s, p50 16.047s, p90 27.656s, p95 31.578s, max 43.500s
signal_scan_seconds avg 10.161s, p50 9.562s, p95 20.109s, max 34.375s
cache_flush_seconds avg 4.977s, p50 4.000s, p95 11.734s, max 15.109s
ticker_radar_seconds avg 0.454s, p50 0.313s, max 8.422s
order_reconcile_seconds avg 0.665s; mostly 0 but occasional ~6.4-6.9s spikes
```

Interpretation:

```text
Main bottleneck is precise signal scan (~62.5% of batch time), then cache flush (~30.6%).
Inactive symbols are almost free because subminute pairs are deferred.
Precise ticker-radar symbols cost p50 ~2.0s, p95 ~5.7s, max 9.75s.
AggTrade tail fetches remain the expensive path: 765 aggTrade requests, 612 network calls, 251 cycle-local cache hits.
Cache flush is unexpectedly large and needs batching/less frequent flush or async/deferred write investigation.
```

Trading readout:

```text
No category_selected and no positions. Rejections were mostly reject_weak_start_flow (429) and reject_setup_too_early (326).
This run proves latency bottlenecks, not edge.
```

---

## 2026-05-13 - P179 live speed patch

Patch:

```text
Expanded static high-cap exclusion list.
Bounded non-forced live cache flush to 20 symbol/timeframe shards per cycle and changed defaults to 30s/50k rows.
Implemented partial aggTrade raw range reuse inside the cycle cache.
```

Validation:

```text
compileall passed.
run-anomaly-live --help exposes live_ohlcv_cache_flush_max_symbol_timeframes.
Inline aggTrade range/dedupe smoke passed.
```

Next readout:

```text
Run the same default live command and compare against 20260513_164045:
batch_seconds, signal_scan_seconds, cache_flush_seconds, aggtrade_network_calls, aggtrade_cache_hits, live_ohlcv_cache_gap, deferred_symbol_timeframes.
```

---

## 2026-05-13 - P183 WebSocket ticker source

Patch:

```text
Default live ticker radar source changed from REST fetch_tickers to Binance USD-M futures !ticker@arr WebSocket.
No REST fallback is used while live_ws_ticker_enabled=true.
```

Validation:

```text
compileall passed.
run-anomaly-live --help exposes live-ws-ticker controls.
Inline not-ready and payload-normalization smokes passed.
Real public WS smoke could not connect because local DNS could not resolve fstream.binance.com in the test environment; no REST fallback was used.
```

Next readout:

```text
Run a short live sample and check ticker_radar_snapshot.source, ticker_radar_failed reasons, radar promotions, and whether WS startup/stale windows create blind periods.
```

---

## 2026-05-13 - P184 WS aggTrade precise-scan source

Patch:

```text
Added Binance WS aggTrade buffer for active/ticker-radar watch symbols.
Subminute precise scan reads WS rows first and emits ws_aggtrade_frame_read for coverage/backfill health.
Aggregate trade id gaps are treated as missing intervals until explicit backfill covers them.
```

Validation:

```text
compileall passed.
run-anomaly-live --help exposes live-ws-aggtrade controls.
Inline WS buffer smoke passed: id gap -> partial read; explicit backfill coverage -> covered read.
Short live max-cycles=1 with --confirm-real-orders exited 0 and wrote live_cache_config/ws_aggtrade_subscription_target/live_cycle_summary.
The live sample could not validate real WS messages because local DNS could not resolve fstream.binance.com; ticker and aggTrade WS sources both reported that condition explicitly.
```

Next readout:

```text
Run 10-30 minutes on the trading host and inspect ws_aggtrade_subscription_target.connection_status, ws_aggtrade_frame_read.status, missing_range_count, backfill_ranges, aggtrade_network_calls, and precise_scan_symbols.
Success criterion: active/radar symbols move from first-cycle backfilled/partial to mostly covered after warm-up, without ticker-radar blind periods or hidden REST fallback.
```

---

## 2026-05-14 - Live health readout 20260513_202255

Run:

```text
.output/results/live_anomaly_runs/20260513_202255
Window: 2026-05-13 20:22:56 UTC to 2026-05-14 04:34:38 UTC
Rows: 97,397 live_events; positions: 0
Universe: 525 symbols after static high-cap exclusion
Scheduler: ws_event_driven_scheduler, inactive_scan_slots_per_cycle=0
```

Funnel:

```text
ticker_radar_promoted: 1,626 events / 267 unique symbols
signal_symbol_scan_summary: 5,242 symbol scans
due/evaluated timeframe rows: 14,281 / 14,281
fetch_failures: 0
entry_ws_aggtrade_pending_count: 0
category_selected: 0
positions: 0
```

Main rejection:

```text
reject_weak_start_flow: 7,385
reject_setup_too_early: 6,207
category_rejected: 612
  reject_mark_basis_below_min: 561
  reject_prior_up_down_whipsaw: 30
  reject_oi: 3
reject_invalid_initial_risk: 63
```

Exchange/data health:

```text
Ticker WS: healthy after one explicit REST startup seed; 1,305/1,306 snapshots primary, ok_count=525, missing_count=0, no ticker_radar_failed.
aggTrade WS: connected, but no clean covered reads; ws_aggtrade_frame_read status = partial 13,669, stale 586, not_subscribed 26.
REST gap backfill remained dominant: 14,278 backfill reads, 18,357 aggTrade network calls, 969,091 backfilled rows.
live_ohlcv_cache_gap: 87; signal_scan_empty_ohlcv: 14.
```

Interpretation:

```text
No trades are mainly explained by strategy/category filtering, not exchange failure: every due timeframe row was evaluated and no entry remained after runner_oi_confirmed filters.
The strongest blocker is mark-basis >= 0.3%: 561/612 category rejects. OI was not the main blocker in this run.
However WS aggTrade health is not good enough for the intended speed target. The implementation is honest because gaps are explicit, but most precise scans still rely on REST backfill.
The current WS coverage rule is probably too strict at interval edges and treats low-trade silence as stale because Binance aggTrade has no per-symbol heartbeat. That preserves data honesty but prevents the expected REST reduction.
```

Next:

```text
Patch WS aggTrade coverage accounting: separate true id-gap holes from harmless no-trade edge intervals, track subscription warm coverage, and reduce repeated REST edge backfills without claiming uncovered windows are complete.
Also inspect 26 not_subscribed reads as a subscription race; keep explicit backfill, but remove the race if confirmed.
```

Patch follow-up P195:

```text
Implemented WS coverage accounting fix after this readout:
- active subscriptions now cover quiet no-trade intervals;
- pre-subscription history still needs explicit backfill;
- id gaps remain strict holes;
- empty backfill responses extend coverage;
- command-level KeyboardInterrupt now invokes runner shutdown cleanup.
```

Validation target:

```text
Repeat a 10-30 minute live health run. Expected improvement: ws_aggtrade_frame_read.status should include many covered reads after warm-up, stale/not_subscribed should be near zero, and aggtrade_network_calls/ws_aggtrade_backfill_reads should drop materially.
```

---

## 2026-05-14 - Backtest/live parity review from uploaded code ZIP

Input:

```text
Source: .zip, inspected as code only.
Scope: anomaly live vs anomaly backtest parity for runner_oi_confirmed and forming HTF from LTF entry sets.
```

Findings:

```text
Backtest and live both use the same default TF sets and broadly similar forming-setup formulas, but the signal builders are duplicated, so parity is not structurally guaranteed.
Highest-confidence parity bug is in backtest candidate collection: _collect_symbol_pair_rows appends the first raw candidate in a setup bucket, sets cooldown, and stops scanning later LTF decision candles even if later category/OI/mark/entry filters reject that first candidate. Live can continue to evaluate later closed LTF decisions inside the same forming HTF setup.
runner_oi_confirmed also needs explicit freshness parity: live OI and mark basis fetches reject stale context at decision time, while backtest profile relies on optional require_oi_status_ok / reject_stale_derivatives_context flags unless forced by profile/context path.
Regular all-candle backtest is not enough to validate live parity because live discovery is ticker-radar/active-symbol scheduled. A parity experiment should compare the exact live-scanned 30m window against cached candle reconstruction and live event decisions/rejections.
```

Next experiment:

```text
After the current 30m live run, backfill/materialize 1s-derived cache for the exact live end timestamp plus a 2d warmup. Then run the normal 2d anomaly lab only as context, and separately slice the exact live window to compare: scanned symbol/TF decisions, category rejects, selected categories, mark/OI status, entry drift/RR, and live scheduler lag. Do not interpret the 2d run as profitability evidence.
```

Action P196:

```text
Applied local code changes before the parity experiment: backtest now collects all LTF decision candidates inside a forming HTF setup, and runner_oi_confirmed requires oi_status=ok plus fresh derivatives/mark context through the profile.
Live category_selected rows now carry accepted OI/mark context status and values, which are required for exact accepted-signal parity checks.
The next parity run must expect higher candidate volume; this is intended because raw candidates no longer hide later valid decisions.
```

Action P197:

```text
Added broad backtest category overlay. The run should keep the broad signal stream, then analyze anomaly_profitability_by_category.csv for runner_oi_confirmed, runner_flow, runner_reclaim, runner_balanced, and discovery.
This is category discovery, not live promotion. A category needs enough trades and period robustness before live trading.
```

Action P198:

```text
Added cheap cumulative quote/trade flow prescreen to broad forming-setup collection after the 30d run showed multi-hour ETA.
Expected effect: fewer expensive row-builder calls with identical flow gate semantics. Validate by comparing a small fixed-window candidate set before/after if exact parity proof is needed.
```

Review 30d broad run after P196-P198:

```text
Input: .output/results/anomaly_lab, completed all TF sets 5m/30s, 1m/15s, 1m/5s.
Run is broad discovery, not runner_oi_confirmed-only.
Main artifact caveat: requested_end is around 2026-05-14 08:09-08:16 UTC, while subminute cache max is around 06:04-06:05 UTC; symbols_covering_end=0 for all TF sets. This means the final ~2h of requested window is not covered, although historical trades before cache max remain analyzable.
OI health is good for candidate rows: ok share roughly 97.8%-98.8%. Category overlay accepted rows for runner_oi_confirmed/runner_flow/runner_reclaim/runner_balanced mostly have oi_status=ok and mark_status=ok.
Broad discovery is profitable but weak as a live category: 1m/5s discovery median is slightly negative and 5m/30s/1m/15s discovery has weaker expectancy than category buckets.
Best category candidates: runner_oi_confirmed is strongest by robustness across TFs; runner_flow looks promising but trade counts are lower; runner_reclaim is mixed; runner_balanced is too small on 5m/30s and 1m/15s, better on 1m/5s but still needs wider validation.
```

Action P199:

```text
Enabled profitable research categories for test live: runner_oi_confirmed, runner_flow, runner_reclaim, runner_balanced.
Added TF-specific live priority and explicit live_category_overlay_v1_no_prior_fast_fade contract in artifacts.
Next experiment should be a short live/shadow run measuring category mix and live-vs-backtest parity before treating expanded live as production-ready.
```

Action P200:

```text
Live prior_fast_fade_72h is now an active category filter, not just a contract caveat.
The implementation uses local cached candidate history and allows only trailing cache lag to be ignored; start/internal cache gaps reject the category.
Artifacts must be checked for reject_prior_fast_fade_filter_unavailable, reject_prior_fast_fade_72h, prior_fast_fade_count_72h, effective_cache_end_timestamp_ms, and ignored_tail_ms before treating expanded live categories as parity-ready.
```

---

## 2026-05-14 - Live bottleneck audit 20260514_104416

Input:

```text
Artifacts: .output/results/live_anomaly_runs/20260514_104416
Duration: 500 live_cycle_summary rows, 3317 observed health seconds, 0 positions.
```

Timing:

```text
scheduler_cycle_seconds: sum 2312s, p50 1.98s, p90 13.16s, p95 17.89s, max 42.30s
signal_scan_seconds: sum 1054s, p50 1.02s, p95 7.34s, max 16.06s
cache_flush_seconds: sum 904s, p50 0.00s, p90 9.47s, p95 13.19s, max 31.88s
order_reconcile_seconds: sum 344s, mostly periodic orphan reconciliation.
```

Main bottleneck:

```text
Non-forced live OHLCV cache flush writes up to 20 symbol/timeframe parquet shards synchronously in the decision loop.
Each shard calls save_incremental: read existing parquet, merge, write tmp, validate tmp by reading it, then replace.
This preserves cache integrity but causes 10-30s stalls; many long cycles are dominated by cache_flush_seconds, not signal logic.
```

WS/readout:

```text
ws_aggtrade_frame_read: covered 4386, partial 211, not_subscribed 1.
aggTrade network calls 305; backfill reads 212; backfilled rows 155,799.
The remaining REST backfill is real work, but it is not the largest pure hot-loop stall compared with cache flushing.
```

Safe action:

```text
P202 reduces default live_ohlcv_cache_flush_max_symbol_timeframes from 20 to 4.
This does not change signal selection, entry/exit logic, cache correctness, or shutdown persistence: forced shutdown still flushes all pending shards.
Expected effect: lower p90/p95 cycle stalls from parquet writes, at the cost of a slightly larger in-memory pending write queue.
```

---

## 2026-05-14 - Live health/code review after P202

Input:

```text
Current local code and latest live artifacts, especially .output/results/live_anomaly_runs/20260514_120027.
```

Findings:

```text
20260514_120027 shows improved cache flush stalls after the lower shard cap: cache_flush_seconds p95 ~1.25s, max ~4.55s versus previous 20260514_104416 p95 ~13.19s, max ~31.88s.
WS ticker is mostly primary and aggTrade reads are mostly covered: ws_aggtrade_frame_read covered 604, partial 22, pending 0.
No trades opened and category_selected remains 0. Current category_rejected reasons are dominated by prior_fast_fade filter cache unavailability/start gaps, not mark/OI failure.
Code review found that aggTrade subscription coverage was optimistic before exchange ACK and position amount parsing could ignore info.positionAmt when normalized contracts were zero.
```

Action:

```text
P203 proposed: mark WS aggTrade subscriptions active only after ACK and parse Binance info.positionAmt when normalized contracts are zero.
Do not claim live edge from these runs: they validate infrastructure health and rejection reasons only.
```

Action P204:

```text
Implemented shared live aggTrade REST backfill optimization.
The runner now reuses REST aggTrade raw ranges across cycles, coalesces small missing ranges, and pads fetch windows to reduce future small REST calls.
Validation passed: compileall, run-anomaly-live --help, process-cache smoke, and coalesced-gap smoke.
Next live readout should compare REST call count and scan latency, not PnL.
```

---

## 2026-05-13 - Short WS live artifact audit and P185

Input:

```text
Artifacts: uploaded 20260513_180525
Rows: 1 live_cycle_summary, 0 positions, 0 top-growth snapshots
Observed source health: ticker WS DNS failure; aggTrade WS target_count=0/subscribed_count=0
```

Conclusion:

```text
This run does not validate live WebSocket health or edge. It validates only that the old loop could keep running blind when the required ticker radar was unavailable.
The next latency target should treat the outer loop as a scheduler tick, not a full-universe scan. WebSocket ingestion is continuous, but signal evaluation remains closed-candle/batch gated.
```

Action:

```text
P185 proposed: refuse blind ticker-radar startup and make WS aggTrade REST backfill explicitly bounded, defaulting to strict WS coverage.
```
---

## 2026-05-13 - P186 rebuild on current ZIP

Input:

```text
Current code ZIP contains P185 and partial scheduler knobs, but P186 did not apply cleanly.
User requested that the cycle-time log report WebSocket-relevant health instead of ambiguous cycle timing.
```

Conclusion:

```text
In WS-live, the decision heartbeat is the relevant timing metric. Legacy batch coverage is secondary and should not be the primary operator status line. Market discovery should come from ticker WS/radar; inactive round-robin is only an explicit diagnostic/legacy budget.
```

Action:

```text
P186 rebuilt against the current ZIP: event-driven inactive-scan default, typed ticker/aggTrade per-cycle stats, and WebSocket-focused live status output.
```

## P211 — PROPOSED — DANGER runner/fader pre-pump context study
Goal: test whether traded pump events can be separated into runner/fader contexts before anomaly start using 30m/1h/2h/6h price/volume/trade-count/OI/derivatives features.
Method: use `anomaly_timestamp_ms` as exclusive feature anchor to avoid leakage; label outcomes from closed trades; compare runner vs fader feature distributions.
Artifacts: `runner_fader_prepump_context.csv`, `runner_fader_prepump_feature_separation.csv`, label/status summaries.
Guardrail: offline research only; no live filter until 30d+ walk-forward validation.
Commit: UNKNOWN.

## P212 — PROPOSED — DANGER default runner/fader prepump analysis in backtest
Goal: make every normal anomaly backtest produce runner/fader pre-pump HTF-context evidence by default, so separability is reviewed with the same run artifacts as trades/PnL.
Method: after `anomaly_trades.csv` is written, compute 30m/1h/2h/6h context features strictly before `anomaly_timestamp_ms`; write explicit run status if context build fails.
Guardrail: this remains offline analysis only. Do not use it as a live filter until a 30d+ walk-forward split proves stable separation without symbol/month leakage.
Commit: UNKNOWN.

## 2026-05-14 - P217 proposed live warm-watch scoring experiment

Status: PROPOSED
Commit: UNKNOWN

```text
Experiment: after a 30d P212 runner/fader prepump artifact is available, enable prepump_warm_watch_scoring in dry live using runner_fader_prepump_feature_separation.csv as the profile. Measure only scheduler impact: warm_watch_marked/updated/promoted ordering, later top_growth overlap, flow_radar false positives, and latency. Do not treat the score as an entry filter until walk-forward evidence shows stable separation outside the training period.
```

## 2026-05-15 — P245 candidate queue pressure validation

Goal: verify that latency pressure is reduced by removing stale/weak radar/warm-watch backlog instead of repeatedly deferring it.

Expected evidence:

```text
live_cycle_summary.candidate_queue_status is ok/trimmed/pressure_no_drop, not absent
candidate_queue_dropped_pressure_count and candidate_queue_expired_backlog_stale_count are non-zero only during queue pressure
warm_watch_precise_deferred_latency_sla decreases versus the previous 5h live run
latency_sla_status=breached share decreases or becomes explained by active/high-score candidates
closed-hour missed_pump_visibility can show if a dropped/expired candidate later became a top mover
```

Guardrail: do not tune stale/drift/RR or order safety based on this patch. If top movers are being dropped, adjust queue scoring/priority, not execution guards.

## 2026-05-15 — P246 adaptive precise budget validation

Goal: verify that the live loop spends precise-scan capacity on the freshest strongest candidates under pressure, instead of widening the stale backlog.

Expected evidence:

```text
adaptive_precise_budget_status is uncapped during normal load and breached/queue_pressure/active_priority only when justified
adaptive_precise_budget_radar_slots drops to 1-3 under pressure
latency_sla_status=breached share decreases or becomes tied to high-priority active/radar candidates
warm_watch_precise_deferred_latency_sla decreases versus the previous 5h run
missed_pump_visibility shows whether capped-out radar candidates later became top movers
```

Guardrail: do not tune stale/drift/RR/actual-risk guards from this run. If top movers were consistently outside the adaptive cap, improve radar scoring/freshness priority before increasing scan width.

## 2026-05-15 - P237 proposed live session top-growth status

Input:

```text
The 5h live run created empty closed-hour top-growth/missed-pump files because live trading does not run standalone universe-wide top-growth collection.
```

Conclusion:

```text
Do not fill closed-hour top-growth artifacts with ticker-derived approximations. For operator awareness, track session top movers from live ticker snapshots with first-seen-in-session baselines and explicit source/status. For missed-pump audit, still run `run-anomaly-top-growth` against closed 1h candles and the live_events.csv from the run.
```
---

## 2026-05-15 — Next live validation after P239/P240

Run a supervised live smoke after applying startup context backfill and retryable dependency handling.

Expected artifact changes:

```text
category_rejected should contain final market/category failures only
signal_scan_retryable_dependency_blocked should contain unavailable prior_fast_fade context, if any
delayed_replay_queue should not be filled by dependency-blocked decisions
selected signals should still require an ok prior_fast_fade count before entry
```

Do not loosen flow thresholds until closed-hour top-growth / missed-pump visibility is populated for the same run.


## 2026-05-15 — P241 live context priority validation

After applying P241, validate that rolling context snapshot budget is spent on near-term decision symbols before cold universe maintenance.

Expected evidence:

```text
symbol_context_snapshot_updated.priority_reason_counts includes retryable_dependency_blocked when such cases exist
same-symbol retryable prior_fast_fade blocks should either resolve to ok context or expire by stale/TTL, not disappear as category_rejected
round_robin_symbols_count remains non-zero when no hot priority backlog exists
```


## 2026-05-15 — P242 live closed-hour top-growth visibility validation

Goal: verify that live produces closed-hour missed-pump evidence without running the standalone command and without using ticker-derived approximations.

Expected evidence:

```text
live_cycle_summary.live_top_growth_status transitions idle -> processing -> completed
top_growth/top_growth_index.csv gets one row per audited closed hour
top_growth/top_growth_status_YYYYMMDD_HH0000_UTC.csv has status/reason for every live-universe symbol
top_growth/missed_pump_visibility_YYYYMMDD_HH0000_UTC.csv uses the same run live_events.csv as visibility_source
```

Guardrail: do not use session_top_growth.csv as a missed-pump audit source; it is operator UI from ticker snapshots only.

## 2026-05-15 — P243 live top-growth guardrail validation

Goal: confirm closed-hour top-growth audit gives missed-pump visibility without becoming a hidden live-load source.

Expected evidence:

```text
`python main.py run-anomaly-live --help` has no `live-top-growth` flags
live_cycle_summary shows `skipped_latency_sla` instead of processing when latency SLA gates optional work
when SLA is OK, live_top_growth processes bounded chunks and eventually writes top/status/visibility files
```

Do not tune top-growth thresholds from CLI during live. Treat changes to threshold/limit/quota as code-reviewed policy changes.

## 2026-05-15 — P244 discovery loosen/data-dependency validation

Goal: verify that fewer situations are rejected for non-market reasons while execution safety stays strict.

Expected evidence:

```text
run config shows min_quote_ratio_start=4.0 and min_trade_ratio_start=4.0
run config shows max_entry_price_drift_pct=0.003
category_rejected should contain reject_mark_basis_below_min/reject_oi_below_min only when values are actually computed and below threshold
signal_scan_retryable_dependency_blocked should contain reject_mark_basis_unavailable, reject_oi_unavailable, and taker-buy missing/invalid cases
delayed replay should not queue retryable dependency cases as final all_categories_rejected
```

Compare against closed-hour top-growth/missed-pump artifacts before loosening any execution guard.

## 2026-05-16 — P252 startup/live smoke

After P252, rerun live with the same command that previously failed after startup context backfill. Expected result: no `NameError: selection is not defined`; the first live cycle should append `live_cycle_summary` with candidate queue fields populated from runner state. The startup backfill status line should include current local time on every per-symbol refresh.

## 2026-05-16 — P254 live heartbeat status validation

Expected operator heartbeat shape:

```text
Соединение
Время 6ч 32м 14с          Пульс 5.2с                 Данные Ok
```

If data is degraded, `Данные` should briefly show the actual source of degradation, for example `Тикер REST`, `Поток REST`, `Поток gapREST`, `Поток ждёт`, `Поток подписка`, `Кеш REST`, or `Кеш gap`. These are visibility labels only; no fallback is hidden as `Ok`.

## 2026-05-16 — Startup context readiness validation

Next live run after P255 should confirm that startup prints a readiness line after 72h context snapshot generation, writes `symbol_context_startup_readiness`, and refuses real-orders startup if ready symbol/snapshot ratios are below threshold. Evaluate this before reading PnL or signal counts.


## 2026-05-15 — P247 dependency retry cooldown validation

Goal: verify that retryable data-dependency blocks stop creating repeated precise-scan load while still remaining auditable and bounded by stale timeout.

Expected evidence:

```text
signal_scan_dependency_retry_scheduled appears after retryable dependency blocks
dependency_retry_cooldown_active_count is visible in live_cycle_summary
dependency_retry_cooldown_skipped_cycle increments under repeated dependency waits
candidate_expired_dependency_timeout appears only when dependency stayed unavailable until stale timeout
latency_sla_status=breached and warm_watch_precise_deferred_latency_sla decrease versus the previous overloaded run
```

Guardrail: do not convert unavailable dependencies into pass-by-default. If dependency timeouts dominate, fix the dependency producer/cache coverage; do not weaken execution safety.


## 2026-05-15 — P248 live initial-risk diagnostics

Purpose: distinguish true setup deterioration from stop-anchor geometry when live rejects a candidate because initial long risk is non-positive.

Expected artifact change: `reject_entry_below_initial_stop` replaces the generic `reject_invalid_initial_risk` live event and includes `stop_source`, `risk_side`, `previous_stop`, `decision_ema20`, and `stop_above_entry_pct`.

Validation focus: after the next live run, count whether non-positive risk is mostly `ema20`, `structural`, or tie-driven. Do not change stop logic until this distribution is known.

## 2026-05-15 — P249 cooldown accounting validation

Goal: verify that dependency retry cooldown is auditable at per-symbol scan-summary level.

Expected evidence:

```text
signal_scan_dependency_retry_scheduled appears for retryable dependency blocks
repeated scans during cooldown show dependency_retry_cooldown_skipped_count > 0
skipped_not_due_count no longer absorbs dependency cooldown waits
```

This does not change trading behavior; it only fixes diagnostic attribution from the P247 review.

## 2026-05-15 — P250 startup backfill visibility smoke

After applying P250, run live from a cold or partial OHLCV cache and verify that startup context backfill does not appear stuck: the console should rewrite one status line for every symbol with current symbol and ETA. This is UI/observability only; success criteria are status freshness and unchanged `symbol_context_startup_backfill_completed` artifact fields.

---

## 2026-05-16 — Live/backtest parity audit from live.zip + bt.zip

Input:

```text
Live run: run-anomaly-live --confirm-real-orders --delayed-replay-enabled true --danger-continue-after-order-position-errors
Backtest run: latest 2d anomaly backtest artifacts
```

Findings:

```text
The old 2d backtest is not live-parity-valid for runner categories: live and backtest had separate runner thresholds, and trades were not clearly separable between live-priority categories and discovery fallback.
Backtest candidate errors were caused locally by materialized entry-cache metadata columns existing but containing no non-null aggregation_source_timeframe/aggregation_version values; this is not an exchange fetch failure.
Delayed replay divergence on selected live signals was caused by replay recomputing immutable live snapshots under setup_source=delayed_replay_immutable_live_snapshot, which disabled the forming_htf pace normalization used by the original live decision.
Synthetic OHLCV buckets in live represent missing no-trade buckets from aggTrade-derived subminute frames; they must be explicit row/data-quality provenance and must not count as real flow/hold evidence.
Derivatives context source of truth for a live decision is the frozen live decision context; historical backtest may use cache context, but mismatches must be treated as context/data parity issues, not as proof that live or backtest was right.
```

Next:

```text
Apply P264, rerun the same 2d/live-window backtest, then compare anomaly_trades.csv by pump_category_family and category_selected vs selected_terminal_outcome in live_events.csv.
```

## 2026-05-16 — P266 backtest parity artifact validation

Goal: verify that the next backtest can explain live/backtest mismatches before reading PnL.

Expected evidence:

```text
anomaly_trades.csv skipped rows contain pump_category_id, pump_category_family, pump_category_source, and pump_category_contract.
anomaly_context_parity_report.csv exists and contains pre_context_intent, in_pre_context_universe, final_pump_category_id, final_pump_category_family, trade_status, trade_skip_reason, mark/oi status, and context_parity_status.
Execution guard skips such as tp1_already_reached_before_market_entry can be grouped by live_priority vs discovery.
Rows that remain outside the pre-context universe are explicitly `not_requested_pre_context_filtered`, not confused with exchange/cache failure.
```

Guardrail: discovery remains enabled as a separate fallback family for research; do not blend discovery PnL into live-priority PnL.

## 2026-05-16 — Discrete signal missed observability

Goal: measure how often live receives a valid discrete signal snapshot but rejects the actual order because the current executable price already made the setup unsafe.

Expected evidence:

```text
discrete_signal_snapshot_entry_missed appears before the matching execution reject
selected_terminal_outcome remains execution_rejected
Telegram says: "вход пропущен" and names the current reject reason
No orders are submitted from this diagnostic event
```

Decision rule: if these events are frequent and concentrated on 5s/15s entry TFs, consider a separate event-driven/partial-candle experiment. Do not weaken executable price guards in the current live path.


---

## 2026-05-16 — P263 live order lifecycle unit-test harness

Input:

```text
Run artifact: live.zip / 20260516 real-order diagnostic run
Problem: selected signals reached entry, but every attempted position failed initial stop visibility and was closed as unprotected exposure.
```

Result:

```text
P263 proposes deterministic unit tests around the actual live private lifecycle paths rather than isolated helper tests.
The fake exchange drives `_maybe_open_position()` through entry fill, exchange position delta, stop creation/verification, artifact ledger writing, and no-monitor-thread open handling.
It also reproduces the stop-not-visible failure mode from the live run and asserts reduce-only cleanup plus `LiveOrderPositionIntegrityError`.
Monitor coverage includes verified stop exit and TP1 partial exit followed by BE stop replacement, old-stop cancel, and verified stop close.
```

Validation:

```bash
python -m unittest tests.test_live_order_lifecycle -v
python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Next:

```text
Run these tests against the local tree. If they pass, add narrower tests around CCXT/Binance stop-order payload normalization and `-2013 Order does not exist` classification before changing live stop verification code.
```


## 2026-05-16 — P267 replay/context honesty validation

Goal: verify that live/replay/backtest artifacts preserve data provenance instead of silently treating filled gaps or stale OI as healthy evidence.

Expected evidence after applying P267:

```text
delayed replay decision snapshots include `synthetic_ohlcv_bucket` in entry_rows when live filled missing OHLCV buckets.
`reject_insufficient_real_entry_buckets` appears when total entry buckets are enough only because synthetic rows were inserted.
`anomaly_context_parity_report.csv` includes OI cache/load/asof fields: oi_cache_status, oi_fetch_or_load_status, oi_cache_min/max timestamps, oi_asof_timestamp, oi_age_ms.
Context mismatches involving OI can be classified as stale/unavailable cache rather than mixed with mark-price context failures.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# Then rerun the same short backtest and inspect anomaly_context_parity_report.csv columns/statuses.
# Then run a short live/delayed replay smoke and inspect delayed_replay_queue.jsonl entry_rows for synthetic_ohlcv_bucket.
```

Guardrail: discovery remains unchanged and separate; do not judge discovery edge from this patch.

## 2026-05-16 — P268 real-order smoke validation

Goal: verify the real Binance USD-M order boundary used by live trading, not the fake exchange lifecycle tests.

Expected command:

```powershell
.venv\Scripts\python.exe main.py run-live-order-smoke --symbol <SYMBOL/USDT:USDT> --notional-usdt 12 --max-notional-usdt 25 --stop-distance-pct 0.05 --confirm-real-order-smoke
```

Expected evidence:

```text
entry_fill_verified
stop_created
stop_verified
stop_cancelled
cleanup_reduce_only_fill_verified
final_exchange_snapshot with exchange_position_amount=0 and smoke_open_orders_seen=0
```

Failure interpretation:

```text
ExchangeOrderNotFound during stop lookup means Binance answered that the stop order is absent.
ExchangeConnectivityError means transport/API retry exhaustion.
Any failed smoke must preserve artifacts and attempt reduce-only cleanup.
```

Guardrail: do not use this command as a strategy entry. It is an exchange-boundary smoke only.
## 2026-05-16 — P269 Binance conditional stop smoke follow-up

Goal: validate that live protective stops are managed through the same Binance conditional/algo boundary where the UI-visible stop actually exists.

Expected command after applying P269:

```powershell
.venv\Scripts\python.exe main.py run-live-order-smoke --symbol EDEN/USDT:USDT --notional-usdt 12 --max-notional-usdt 25 --stop-distance-pct 0.05 --confirm-real-order-smoke
```

Expected evidence:

```text
stop_created with a Binance algo id/clientAlgoId-normalized id
stop_verified.source = open_algo_orders_order_id OR open_algo_orders_client_order_id OR algo_client_order_id_lookup
stop_cancelled succeeds through cancel_stop_order/algoOrder
final_exchange_snapshot: exchange_position_amount=0, smoke_open_orders_seen=0, smoke_algo_open_orders_seen=0
```

Failure interpretation:

```text
If open_algo_orders_seen > 0 at final snapshot, cleanup is still incomplete.
If stop creation succeeds but openAlgoOrders cannot see it, capture raw smoke artifacts; do not fall back to trusting create response.
```

## 2026-05-16 — P270 real position management smoke

Goal: exercise the real Binance protected-position management path after P269 proved initial algo stop visibility.

Expected command after applying P270:

```powershell
.venv\Scripts\python.exe main.py run-live-order-smoke --symbol EDEN/USDT:USDT --notional-usdt 12 --max-notional-usdt 25 --stop-distance-pct 0.05 --replacement-stop-distance-pct 0.025 --close-position-before-stop-cancel --confirm-real-order-smoke
```

Expected evidence:

```text
entry_fill_verified
stop_verified with source=open_algo_orders_* or algo_client_order_id_lookup
stop_replacement_created
stop_replacement_verified with source=open_algo_orders_* or algo_client_order_id_lookup
old_stop_cancelled_after_replacement
stop_replacement_post_cancel_snapshot with old_still_open=false and new_still_open=true
cleanup_reduce_only_fill_verified
stop_cancelled for the replacement stop
final_exchange_snapshot with exchange_position_amount=0, smoke_open_orders_seen=0, smoke_algo_open_orders_seen=0
```

Guardrail: this smoke is an exchange-boundary validation command, not a strategy entry. It must refuse replacement distances that are not closer than the initial stop distance.
## 2026-05-16 — P271 exchange-normalized stop trigger precision

Observed from real management smoke `20260516_200655`:

```text
initial stop was created and visible through Binance algo open orders, but verification failed on raw float vs exchange-normalized trigger price:
expected=0.035643999999999995
actual=0.03564
```

Interpretation:

```text
This is not a missing stop and not a management-state failure. It is an overly strict price comparison against a Binance-normalized trigger price.
```

Next validation after P271: rerun the same management smoke command and inspect `live_order_smoke_events.csv` for both initial and replacement stop verification plus final `smoke_algo_open_orders_seen=0`.

## 2026-05-16 — P272 smoke summary stop-id provenance

Observed from successful management smoke `20260516_201316`:

```text
Events correctly record the initial stop id and replacement stop id, but `live_order_smoke_summary.json` reports `stop_order_id` as the replacement id after `_stop_order_id` is updated to the active replacement stop.
```

Interpretation:

```text
This is an artifact-only provenance bug. The real position management lifecycle passed: initial stop verified, replacement stop verified, old stop cancelled, position closed reduce-only, replacement stop cancelled, final ordinary/algo orders zero.
```

Expected after P272:

```text
summary.stop_order_id = initial stop id
summary.active_stop_order_id = currently active/last managed stop id
summary.replacement_stop_order_id = replacement stop id when replacement mode is used
```

## 2026-05-17 — P274 TP1 limit / LTF monitor validation

Expected smoke after applying P274:

```powershell
.venv\Scripts\python.exe main.py run-live-order-smoke --symbol <LIQUID_SYMBOL>/USDT:USDT --notional-usdt 12 --max-notional-usdt 25 --stop-distance-pct 0.05 --confirm-real-order-smoke
```

Additional evidence required from the next strategy live smoke:

```text
position_stop_order_verified
position_tp1_limit_order_verified
position_monitor_waiting_first_candle before the first closed entry-timeframe candle when applicable
no TP1 market close created from candle high
tp1_limit_exit_filled only after exchange fill can be reconciled
position_tp1_limit_order_cancelled when a position exits before TP1
final ordinary/algo open orders = 0 after cleanup
```

## 2026-05-17 - P276 live lifecycle unit validation

Input:

```text
Request: check live for errors, especially position management, and check backtest/live parity.
Scope: code audit of current live order path, position monitor, TP1/stop lifecycle, and anomaly backtest exit model; unit lifecycle smoke via tests.test_live_order_lifecycle.
```

Finding:

```text
Real live bug found: after entry + initial stop verification, TP1 limit creation/verification failure closed exchange exposure reduce-only but did not cancel the already-created initial stop. This could leave an orphan conditional stop after cleanup.
Parity gap remains: live TP1 is exchange-side reduce-only limit fill; backtest TP1 is candle-high simulation. Backtest TP1 outcomes are optimistic until compared against live order-fill artifacts.
```

Validation:

```bash
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Result:

```text
5/5 lifecycle tests passed after updating the fake exchange contract and adding TP1-failure orphan-stop regression coverage.
```

## 2026-05-17 - P277 conservative TP1 / pre-context crash validation

Input:

```text
User reported anomaly-lab crash before the latest live-safety patches:
ValueError: candidates missing required columns: ['mark_close_vs_decision_close_basis']
User also asked to fix optimistic TP1 bias in backtest/live parity.
```

Finding:

```text
The crash is independent of P276. Pre-context universe construction stripped derivative requirements, but left `red_flag_profile` set; `build_anomaly_signals()` reapplied the profile and required mark-basis before derivatives context enrichment.
Backtest TP1 was optimistic versus live because it counted candle high touching TP1 as fill, while live now needs exchange-side limit fill evidence.
```

Patch result:

```text
Pre-context config now clears red_flag_profile after category overrides are applied, preventing pre-enrichment mark/OI requirements from being reintroduced.
Backtest TP1 fill model is conservative_limit_proxy: exact TP1 touch is not filled; trade-through is required; same-candle TP1/SL conflict is stop-first.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py tests/test_live_order_lifecycle.py
```

## 2026-05-17 - anomaly_lab category/session review

Input:

```text
Artifact root: .output/results/anomaly_lab
Pairs: 1m/5s, 1m/15s, 5m/30s
Period: recent 30d artifact window
Exit model: conservative_limit_proxy present in anomaly_trades.csv
```

Data quality:

```text
Trade-count evidence is real, not proxy: candidate rows report trade_count_proxy_used=false.
Levels trade-count source is cached_ohlcv.number_of_trades.
Entry trade-count source is mostly cached_1s_aggregated_to_5s/15s/30s.number_of_trades, with small cached_ohlcv fallback.
Context parity is not perfectly clean: live-priority closed trades include ok rows plus some requested_context_missing_or_bad / oi_context_stale_asof rows. Strict live-parity reads must filter context_parity_status=ok before claiming edge.
```

Result:

```text
All closed trades: n=2436, winrate=52.3%, avg_trade=+0.400%, median=+0.085%, top5 dependency=4.1%.
Live-priority categories: n=490, winrate=72.9%, avg_trade=+1.302%, median=+0.839%, positive-day share=91.7%, worst day=-3.8%, top5 dependency=10.5%.
Discovery fallback: n=1946, winrate=47.1%, avg_trade=+0.173%, median=-0.101%, worst day=-43.0%. Discovery is a research pool, not live-ready.
runner_oi_confirmed: n=147, winrate=76.2%, avg_trade=+1.860%, median=+1.347%.
runner_flow: n=147, winrate=73.5%, avg_trade=+1.476%, median=+1.217%.
runner_balanced: n=143, winrate=75.5%, avg_trade=+1.028%, median=+0.529%, strong daily stability but higher top-tail dependency.
runner_reclaim: n=53, winrate=54.7%, avg_trade=+0.014%; not live-ready.
```

Metric read:

```text
Best decision-time separator is mark_close_vs_decision_close_basis. Positive/high basis strongly separates winners; worst quintile is negative expectancy and best quintile is high winrate/high avg trade.
Helpful but weaker: initial_risk_pct not too small, start_range_pct_ratio_to_baseline high, setup_elapsed_fraction later/more formed, start_verticality_score moderate/high.
Fade/exhaustion markers: high start_trade_ratio_per_abs_return, high start_quote_ratio_per_abs_return, high prior_spike_count_72h, prior_up_down_whipsaw_to_impulse_range, and late entry_delay_candles.
Do not use outcome columns such as MFE/MAE/gross_return as filters; they are post-entry labels only.
```

Runner/fader discovery read:

```text
Runners are united by pre-pump expansion: stronger 30m/1h/2h pre price range/return, positive mark-close pct change, and on 1m/5s rising trade-count/quote-volume slope.
Faders are united by weak pre-return/range or late heavy flow without comparable price continuation, especially on 5m/30s where large late quote/trade size looks more like exhaustion.
Absolute mark price differences must not become filters because symbol price scale contaminates them; use pct/ratio features only.
```

Session read:

```text
Asia 00-07 UTC and EU 07-13 UTC are cleaner than US 13-21 UTC and late 21-24 UTC.
runner_oi_confirmed is strongest in EU/Asia, acceptable but weaker in US, and too sparse/weak late.
runner_flow is good in Asia/EU, weaker in US.
runner_balanced is best in EU, acceptable in Asia/US, too sparse late.
runner_reclaim is negative in US and weak overall.
```

Tuning implication:

```text
Do not hard-optimize from this single 30d run. Small candidate hardening is justified only as a next grid/ablation:
runner_oi_confirmed: require positive mark basis and avoid tiny initial risk; test start_range_pct_ratio_to_baseline / setup_elapsed_fraction soft gates.
runner_flow: require positive mark basis, stronger impulse range, and cap prior whipsaw.
runner_balanced: convert to quiet-runner profile with positive mark basis, non-tiny range, cap trades-per-return and prior spike count.
runner_reclaim: disable from live or keep shadow-only until EU/Asia anti-exhaustion gates prove edge out of sample.
Discovery: keep separate; do not blend discovery PnL into live-priority metrics.
```

Next:

```text
Run a strict parity ablation on the same artifacts: filter context_parity_status=ok, split by category/session/TF, and test only decision-time gates above. Promote no threshold until it improves avg trade, median, positive-day share, and top-trade dependence simultaneously.
```

## 2026-05-17 - P279 live-first tuning decision

Decision:

```text
Apply a conservative subset of the anomaly_lab tuning before minimum-size live collection.
Default live categories become runner_oi_confirmed, runner_flow, runner_balanced.
runner_reclaim is excluded from default live, not deleted.
```

Tuned contract:

```text
runner_oi_confirmed: mark basis >= 0.002, range expansion >= 6.0, initial risk >= 1.0%, prior whipsaw <= 0.60, prior spikes <= 30, prior fast fades <= 1.
runner_flow: mark basis >= 0.0015, range expansion >= 8.0, initial risk >= 1.0%, trade-effort-per-return <= 1800, prior whipsaw <= 0.50, prior spikes <= 30, prior fast fades <= 1.
runner_balanced: mark basis >= 0.002, range expansion >= 5.0, initial risk >= 0.8%, trade-effort-per-return <= 1500, prior whipsaw <= 0.60, prior spikes <= 20, prior fast fades <= 1.
runner_reclaim explicit-only: start_trade_ratio <= 10, range expansion <= 10.5, initial risk <= 3.2%, prior spikes <= 5.
```

Artifact sanity check:

```text
On current closed anomaly_lab trades, this approximate tuned set keeps about 185 live-priority trades versus 490 before, with higher avg/median and lower discovery-like noise. This is not a deployable performance estimate because it is a single 30d artifact and not strict live-fill parity.
```

Live readout:

```text
For the 12 USDT live run, judge only real fills: selected category, context parity/dependency status, exchange entry fill, TP1 limit fill, stop updates, closed PnL, and orphan-order cleanup. Do not annualize the backtest sum as account return.
```

## 2026-05-17 - P280 startup context stale-tail validation

Question:

```text
The 72h startup context can take long enough that the first live decisions see a 15-30m trailing context gap.
```

Decision:

```text
Do not accept stale prior-spike/prior-fast-fade context after startup. Treat the stale tail as retryable dependency and let the rolling symbol-context snapshot priority queue refresh active/radar/retryable symbols.
```

Expected live evidence:

```text
If startup took too long, early blocked rows can show reason=symbol_context_snapshot_tail_stale with ignored_tail_ms > symbol_context_snapshot_fresh_ms.
Those rows should be signal_scan_retryable_dependency_blocked, not final all-categories rejects.
Subsequent symbol_context_snapshot_updated rows should include retryable_dependency_blocked in priority_reason_counts before the symbol can be selected.
```

## 2026-05-17 - P281 pre-pump liquidity review

Question:

```text
Can high start flow ratios be fake on very illiquid coins, e.g. a 3k USDT/day coin where one small print makes x100 momentary volume?
```

Findings from current anomaly_lab:

```text
All closed trades: baseline trade-count is mildly positive for return (Spearman +0.074) and win flag (+0.103). Baseline quote-volume is weaker but still positive.
Live-priority trades: absolute pre-pump activity matters more. baseline_trade_daily_proxy Spearman +0.182, pre_1h_trade_count_sum +0.236, pre_1h_quote_volume_sum +0.188.
Very thin pre_1h quote volume is weak: in live-priority, 30k-100k pre_1h quote volume averaged about +0.54%, while 1m-10m averaged about +1.94%.
For baseline daily quote proxy, live-priority 100k-300k averaged about +0.49%, 300k-1m about +0.88%, 1m-10m about +1.46%, and >10m about +1.51%.
The worst ratio artifact is not high ratio alone; it is high trade/quote effort per unit of price movement. start_trade_ratio_per_abs_return remains strongly negative, especially in broad discovery.
```

Decision:

```text
Add a soft absolute-liquidity floor to the shared live/backtest runner contract: min_baseline_quote_daily_proxy >= 300k USDT/day proxy.
Do not set 1m as the floor yet because 300k-1m still had positive live-priority expectancy and useful frequency.
Keep trade-effort-per-return caps from P279; they address the stronger exhaustion/fake-flow signal.
```

P281 post-filter readout on current 30d artifacts:

```text
This is a post-filter approximation on existing anomaly_trades.csv, not a full rerun.
1m/15s P281 default live: n=68, WR=83.82%, net PnL sum=+189.79%, avg=+2.79%, median=+1.81%.
1m/5s P281 default live: n=42, WR=85.71%, net PnL sum=+64.52%, avg=+1.54%, median=+1.75%.
5m/30s P281 default live: n=53, WR=75.47%, net PnL sum=+142.08%, avg=+2.68%, median=+1.86%.
Combined P281 default live: n=163, WR=81.60%, net PnL sum=+396.38%, avg=+2.43%, median=+1.81%.
Interpret net PnL sum as summed trade returns, not account return.
```

## 2026-05-17 - P282 live universe liquidity policy

Question:

```text
Can live immediately discard low-liquidity coins, while still letting coins enter later if liquidity arrives?
```

## 2026-05-18 - live startup context backfill bottleneck audit

```text
Question: why does live data collection before cycles take tens of minutes, and should it repeat until all symbols are current to now-15m?
Artifacts: .output/results/live_anomaly_runs/20260518_051811, 20260518_070057, 20260518_074930.
Observed startup context duration: about 1889s, 1858s, and 1806s respectively. The stage is symbol_context_startup_backfill_* before ticker_radar_startup_ready; ticker radar itself is sub-second after context readiness.
Workload: 533 symbols, context_timeframes 5m and 1m, 1066 fetched symbol-timeframes, 1599 snapshots. Ready ratios were 100%, so the wait buys full context readiness.
Main bottleneck: live context backfill writes small tail updates through ParquetStorage.save_incremental(), which reads/merges/sorts/rewrites the full parquet file per symbol/timeframe. This is wasteful for live tails and explains why even small added-row counts still cost roughly 30 minutes.
Repeating the full stage until every coin reaches now-15m is not useful with the current sequential architecture: the pass itself takes longer than the target freshness margin, so the target moves forward while the pass runs. It may converge only if the pass becomes much faster.
Decision: apply P293 delta writes for live context cache flushes. This preserves mandatory startup context/readiness and changes only cache write mechanics.
Next validation: compare next live symbol_context_startup_backfill_completed elapsed_seconds and phase timings against the ~1800s baseline; inspect live_ohlcv_cache_flushed.storage_mode=delta.
```

## 2026-05-18 - live run 20260518_070057 startup crash

```text
Question: did P290 break live startup?
Artifact directory: .output/results/live_anomaly_runs/20260518_070057.
Observed failure: run-anomaly-live crashed at 09:32 local during startup ticker radar validation with NameError: _HOUR_MS is not defined.
Root cause: session top snapshot used _HOUR_MS, but anomaly_micro_live.py defines HOUR_MS.
Decision: apply P291. This is a code bug, not evidence about strategy/filter quality.
Validation after fix: direct LiveSessionTopTracker snapshot smoke passed; broader compile/tests passed.
Next validation: restart the same live command and confirm no startup NameError.
```

## 2026-05-18 - live run 20260518_051811 data-readiness/stability audit

```text
Question: can live miss trades because data is temporarily incomplete, and is the displayed stability percentage consistent with REST/cache messages?
Artifact: .output/results/live_anomaly_runs/20260518_051811.
Window: 2026-05-18 05:18:12Z to 2026-05-18 06:40:58Z, 26227 live_events rows, 0 category_selected, 0 position_opened.
Flow quality: ws_aggtrade_frame_read was fully covered in this artifact: 2872 reads, connected, missing_range_count=0, backfilled_rows=0, result_rows=ws_rows=529476. Current evidence does not show aggTrade underfetch causing missed entries.
Data-readiness risk: signal_scan_empty_ohlcv=15, signal_scan_retryable_dependency_blocked=13, candidate_expired_dependency_timeout=13, reject_insufficient_real_entry_buckets=3. Empty OHLCV was handled as normal no_signal in code before P290, so it could consume a candle before cache fill caught up.
Stability interpretation: the old "Стабильность" percentage was cumulative from process start, while "Кеш REST" meant OHLCV cache fill/prefetch. Therefore 99.9% stability with REST text was not necessarily contradictory, but the display was misleading.
Decision: apply P290. Keep trade/category filters unchanged; fix only retry semantics for empty OHLCV and session-scoped operator metrics.
Next validation: run the next live and group signal_scan_empty_ohlcv by retry_policy plus live_cycle_summary by ws_health_scope/top_window_label.
```

## 2026-05-17 - pump-leg TP1 0.75R vs 1.0R no-overlap readout

```text
Question: if SL remains unchanged, what happens when full TP1 is set to entry + 1.0 * (entry - pump_leg_bottom), rounded up by the current market-number rules, instead of 0.75R from pump_leg_bottom?
Artifact basis: .output/results/anomaly_lab/portfolio_exit_replay_stop_basis, live_priority, context_parity=ok, same-symbol overlap rejected.
RR against current actual SL: 0.75R pump-leg TP has median RR 0.914, p25 0.784, p75 0.989, only 23.6% >= 1.0. 1.0R pump-leg TP has median RR 1.219, p25 1.045, p75 1.319, 79.5% >= 1.0.
Max positions 1: full_box_0p75r sum +203.3%, WR 88.6%, PF 10.24, top15 share 46.7%, break-even top cut 63.8%; full_box_1p0r sum +205.4%, WR 80.0%, PF 5.64, top15 share 54.7%, break-even top cut 45.7%.
Max positions 2: full_box_0p75r sum +240.5%, WR 90.0%, PF 10.03, top15 share 41.8%, break-even top cut 65.4%; full_box_1p0r sum +256.2%, WR 82.3%, PF 6.19, top15 share 47.7%, break-even top cut 48.5%.
Max positions 3: full_box_0p75r sum +254.0%, WR 90.5%, PF 10.54, top15 share 39.6%, break-even top cut 66.4%; full_box_1p0r sum +271.9%, WR 82.5%, PF 6.36, top15 share 45.3%, break-even top cut 49.6%.
Conclusion: 1.0R from pump_leg_bottom fixes most psychological RR<1 discomfort without raising the SL and slightly improves summed return in this artifact, but it materially lowers hit rate/PF and increases top-tail dependence versus 0.75R. It is still much safer than raising SL solely to force formal RR>=1.
```

## 2026-05-17 - LTF red-flag exit probe

```text
Question: what if there is no TP and the trade exits after entry when the latest LTF candle shows seller pressure?
Artifact written: .output/results/anomaly_lab/portfolio_exit_replay_stop_basis/redflag_exit_probe.csv.
Method: exploratory replay on no-overlap accepted live_priority/context-ok trades. Uses cached post-entry LTF OHLCV, initial SL first, otherwise exits at close of the first candle matching red-flag rule, else falls back to saved current exit. This is not yet live parity and ignores order-book/slippage.
Rules tested: red candle; red close in lower half; red body >= 50% of range; red candle with taker_buy_quote_share < 45%; red lower-half candle with taker_buy_quote_share < 45%.
Max positions 1: full_box_0p75r +203.3%, full_box_1p0r +205.4%. Best red-flag variant was red_body50 at +104.7%; red_lower_taker45 was +104.4%. Red-flag top15 share stayed very high: ~77-86%.
Max positions 2: full_box_0p75r +240.5%, full_box_1p0r +256.2%. Best red-flag variant was red_body50 at +119.5%; red_lower_taker45 was +116.5%.
Max positions 3: full_box_0p75r +254.0%, full_box_1p0r +271.9%. Best red-flag variant was red_body50 at +125.2%; red_lower_taker45 was +122.0%.
Conclusion: pure no-TP red-flag exit is too early/noisy on this artifact. It roughly halves summed return versus full TP and does not solve top-tail dependence. Keep it as a possible runner-management research idea after TP, not as the primary exit.
```

## 2026-05-17 - no-TP exit strategy balance probe

```text
Artifact written: .output/results/anomaly_lab/portfolio_exit_replay_stop_basis/no_tp_exit_strategy_probe.csv.
Method: no TP order, full position size, initial SL first, then post-entry cached LTF candle exits. Fallback is full position at the saved structural-trail exit price. This is exploratory and not exact live parity because saved structural exits were produced under the old TP1+runner model.
Baselines, max positions 3: saved current partial +248.6%, WR 78.8%, top15 59.1%; full_at_saved_exit_no_tp +311.6%, WR 73.7%, top15 66.2%; full_box_0p75r +254.0%, WR 90.5%, top15 39.6%; full_box_1p0r +271.9%, WR 82.5%, top15 45.3%.
Best no-TP profit candidates, max positions 3: giveback70_after100leg +306.1%, WR 77.4%, PF 6.38, top15 64.2%; close_below_ema9_after75leg +302.1%, WR 79.6%, PF 8.03, top15 61.6%; giveback70_after75leg +300.2%, WR 83.2%, PF 8.71, top15 61.7%.
Best no-TP balance candidate, max positions 3: red_lower_taker45_after75leg +267.0%, WR 86.1%, PF 8.12, top15 44.9%, break-even top cut 54.7%. It is close to full_box_1p0r by sum and top-tail, with higher WR but lower sum.
Conclusion: the strongest no-TP variants can beat fixed full TP on summed return, but they bring back heavy top-tail dependence around 62-66%. The only no-TP variant with a reasonable balance is red_lower_taker45_after75leg: arm only after price has reached +0.75 leg from entry, then exit on a red LTF candle closing in the lower half with taker-buy share < 45%. It is a research candidate, not a better default than fixed full TP yet.
```

## 2026-05-17 - no-TP trailing stop strategy probe

```text
Artifact written: .output/results/anomaly_lab/portfolio_exit_replay_stop_basis/no_tp_trailing_strategy_probe.csv.
Method: no TP order, full position size, initial SL first, then dynamic LTF trailing stops after price reaches +0.75 or +1.0 pump leg. Tested EMA9/EMA20 stops, previous/last lows, swing2 low, chandelier ATR stops, giveback stops, and red-pressure exit. Trades are allowed to continue up to 8h after entry; remaining open probes close at last cached candle. This is exploratory replay, not exact live parity.
Max positions 3 baselines: full_box_0p75r +254.0%, WR 90.5%, PF 10.54, top15 39.6%; full_box_1p0r +271.9%, WR 82.5%, PF 6.36, top15 45.3%; saved current partial +248.6%, WR 78.8%, PF 4.58, top15 59.1%.
Profit leader: giveback70_after100leg +365.9%, WR 80.3%, PF 7.75, top15 75.5%. High profit but too tail-dependent.
EMA20 after100leg: +332.8%, WR 75.2%, PF 6.58, top15 63.2%. Strong sum, still tail-heavy.
Chandelier3ATR after100leg: +333.1%, WR 78.8%, PF 7.09, top15 59.2%. Better than EMA20 but still materially tail-dependent.
Chandelier2ATR after100leg: +290.1%, WR 80.3%, PF 6.35, top15 50.7%. Best trailing-only balance among tested variants, but still less robust than fixed full TP.
Conclusion: trailing-only can raise summed return, but the extra return mostly comes from reintroducing runner tail dependence. If a no-TP trailing variant is needed, the least bad candidate is arm at +1.0 leg then trail by peak - 2 ATR(14) on LTF candles. Current fixed full TP remains cleaner and more robust for live default.
```

Decision:

## 2026-05-20 - live2 20260520_112804 stability triage

```text
Artifact: .output/results/live2_anomaly_runs/20260520_112804
Observation: WS/execution/writer were healthy: aggTrade/ticker/mark ready, user-data/execution ready, artifact writer had no backpressure/errors, no integrity errors/orders. Bottlenecks were market-watch completeness: ~15.5k decisions with ~3.8k deadline_missed, prior_context_status_counts had hundreds of stale selected symbols, and aggTrade gap diagnostics were visible but not fatal.
Root cause found in code: real-trade candles were only moved to closed by the next trade, so a burst candle followed by silence could miss the 750ms deadline despite being a valid ended bucket. Prior-context runtime refresh was active/radar scoped and too slow for the selected universe. Signal evaluation used raw status fields, so stale OI/prior context could still look ok to categories.
Patch: P352 applied locally. Next run should validate lower deadline_missed share, lower prior_context stale count after one refresh cycle, and no rise in prior_context total_errors or artifact writer queue pressure.
```

```text
Yes. Use a cheap universe gate on the implicit exchange symbol list: REST 24h ticker quoteVolume >= 300k USDT at startup, then refresh every 12h by default.
This should reduce 72h context and scan load before trading starts. It must not replace category-level baseline liquidity, because a current 24h ticker can include the pump itself while P281 uses pre-pump closed-kline baseline.
```

Expected live evidence:

```text
live_symbol_universe_liquidity_filter should show startup output_count, removed_symbols, excluded_sample, and later cycle added_symbols/removed_symbols.
If Binance ticker fetch fails, status must be refresh_failed_keep_previous.
If the threshold is too high, frequency loss should appear as lower output_count before signal selection, not as worse category rejection.
```

## 2026-05-19 - live latency follow-up plan after 20260519_084702

```text
Artifact: 20260519_084702 live run, before P304 immediate lane.
Observation: batch selection is already cheap, but hot queues stay populated and repeated scans of symbols such as XAG/ZEC/HYPE/XAU consume scan time with weak-flow/mark-basis rejects. Optional top-growth/context/cache work still runs in cycles with hot queue.
Decision: P305 should be validated as a scheduling/load patch after P304, not as an entry-logic change. Expected improvement is lower signal_scan_seconds tail and fewer repeated scans of the same rejected symbols. It should not block immediate danger-flow candidates.
```

## 2026-05-19 - live quality trend windows

```text
Reason: operator metrics were too jumpy during unstable internet; a single current p95/max did not distinguish transient spikes from sustained degradation.
Change planned in P306: terminal heartbeat now shows rolling Сеть 1м/5м/15м, Лаг 1м/5м/max5, and queue/cycle 5m context. Artifacts get live_quality_window_summary every minute and quality_* fields in each live_cycle_summary.
Expected evidence: use the rolling windows, not a single screen, to decide whether the run is suitable for latency conclusions. If 1m is bad but 5m/15m recover, it was a transient; if 5m/15m stay bad, the run is degraded.
```

## 2026-05-19 - P311 live2 v0 skeleton acceptance plan

```text
Purpose: create the first separate live2 runtime without touching live1 trading behavior.
Acceptance for P311: `run-anomaly-live2` starts, creates `.output/results/live2_anomaly_runs/<run_id>/`, writes `live2_events.csv`, `live2_status.json`, `live2_symbol_state.csv`, and keeps `new_entries_allowed=false` because market-data/signal/execution are explicit TODO gates.
Not accepted yet: no claim about latency improvement, signal coverage, profitability, or order safety. Those require later WS ticker, aggTrade ring-buffer, deadline-engine, signal-adapter, and execution patches.
Next experiment after P311: add ticker WS ingestion into `SymbolStateStore` and prove ticker events update a single per-symbol state record instead of creating candidate queues.
```


## 2026-05-19 - P312 live2 ticker-ingestion acceptance plan

```text
Purpose: validate the first real market-data component of live2 without enabling signal or execution.
Acceptance for P312: `run-anomaly-live2` connects to Binance futures all-ticker WS, updates one mutable SymbolState per symbol/market id, writes ticker_status_counts into `live2_status.json`, and records ticker rows in `live2_symbol_state.csv`. There must be no warm/radar queue, no candidate pressure drop, and no REST ticker fallback in this path.
Not accepted yet: no statement about flow evidence, executable signals, latency edge, or profitability. Ticker data is discovery/priority context only; actual pump-flow decisions still require aggTrade/candle coverage in later patches.
Next experiment after P312: add aggTrade WS shards and in-memory micro-candle ring buffers, then prove closed 5s/15s buckets are available without REST backfill in the signal hot path.
```


## 2026-05-19 - P313 live2 aggTrade/candle-ring acceptance plan

```text
Purpose: validate that live2 can build subminute flow candles directly from Binance aggTrade WS without queues or hot REST repair.
Acceptance for P313: with explicit symbols, `run-anomaly-live2` starts combined aggTrade shards, updates one SymbolState per symbol, increments aggtrade_update_count, writes candle coverage for 5s/15s/30s/1m, and records gaps/out-of-order trades as diagnostics rather than synthetic candles.
Not accepted yet: no executable signal, no strategy edge, no deadline-engine proof, no real order safety. Market-data is still not enough to enable entries.
Next experiment after P313: add the deadline engine and deterministic internal stress events to prove every actionable state ends in selected/rejected/data_not_ready/deadline_missed/no_capacity/expired without candidate queues or pressure drops.
```


## 2026-05-19 - P315 live2 deadline-engine acceptance plan

```text
Purpose: prove live2 can end actionable market-data states with explicit verdicts instead of warm/radar queues or pressure drops.
Acceptance for P315: after P311-P314 are applied, `run-anomaly-live2` runs a DeadlineEngine over in-memory 5s candle rings, emits `deadline_decision` events for diagnostic actionable buckets, and updates per-symbol decision counters/verdict latency. Since real SignalEngine is still TODO, on-time actionable buckets must be rejected as `rejected_signal_engine_todo`, not selected or traded. Late buckets must be `deadline_missed`; degraded coverage must be `data_not_ready`.
Not accepted yet: no real strategy signal, no entry guard, no order placement, no fill/stop safety, and no edge/profitability claim.
Next experiment after P315: adapt current strategy/category signal logic into a pure `SignalEngine.evaluate(SymbolState)` contract that performs no network/disk IO and produces selected/rejected reasons from the same deadline cycle.
```

## 2026-05-19 - P316 live2 signal-adapter acceptance plan

```text
Purpose: connect live2 deadline decisions to a real, pure signal adapter without enabling orders.
Acceptance for P316: after P311-P315 are applied, `run-anomaly-live2` emits `deadline_decision` events where on-time actionable 5s buckets are evaluated by `Live2SignalEngine`. The adapter must not do REST/cache/file IO, must use one SymbolState and in-memory candle rings, must include shared category contract metadata, and must explicitly reject unavailable derivative-context categories instead of masking them with fallback values.
Not accepted yet: no executable entry guard, no real order placement, no actual fill, no verified stop, no TP/BE position supervision, and no profitability claim. `new_entries_allowed=false` remains mandatory.
Next experiment after P316: add executable-entry guards for stale signal, live-price drift, TP1 already touched, and RR collapsed before any execution code can submit an order.
```

## 2026-05-19 - P317 live2 entry-guard acceptance plan

```text
Purpose: prevent live2 from ever handing a stale, drifted, TP-touched, or RR-collapsed selected signal to future execution.
Acceptance for P317: after P311-P316 are applied, `run-anomaly-live2` records entry-guard fields inside `deadline_decision` events whenever `Live2SignalEngine` selects a signal. The guard must use only stream state, reject stale signal age, excessive live price drift, TP1 already touched, RR collapsed, and missing live price/risk levels. It must not do REST/cache/file IO and must not place orders.
Not accepted yet: no real exchange order placement, no pre-position exchange check, no actual fill, no verified stop, no TP/BE position supervision, and no profitability claim. `new_entries_allowed=false` remains mandatory.
Next experiment after P317: implement real ExecutionEngine boundary with pre-entry exchange position check, order submit, actual fill verification, stop submit, and stop visibility verification.
```

## 2026-05-19 - P318 live2 execution-boundary acceptance plan

```text
Purpose: install the strict exchange boundary that must exist before any live2 real order placement.
Acceptance for P318: after P311-P317 are applied, `run-anomaly-live2` performs startup `fetch_live_account_preflight`, exposes execution readiness in `live2_status.json`, and for any selected signal with accepted entry guard calls `fetch_symbol_position_amount` before returning an execution verdict. Existing exchange positions must produce `rejected_existing_exchange_position`; flat symbols must produce `rejected_execution_order_placement_not_implemented` until actual fill and verified stop placement are added.
Not accepted yet: no order submit, no actual fill verification, no stop visibility verification, no TP/BE position supervision, and no profitability claim. `new_entries_allowed=false` remains mandatory.
Next experiment after P318: implement a real order lifecycle atomically: pre-position check -> market order submit with client id -> actual fill verification -> initial stop submit -> stop visibility verification -> protected position state.
```

## 2026-05-19 - P320 live2 fast decision-loop acceptance plan

```text
Purpose: remove the hidden latency bug where DeadlineEngine decisions were only evaluated on the 5s heartbeat cadence.
Acceptance for P320: after P311-P319 are applied, `run-anomaly-live2` must run deadline cycles every `decision_loop_interval_seconds` (default 100ms), while heartbeat/status/symbol-state artifacts stay on the slower heartbeat cadence. `live2_status.json.runtime_gate_status` and `runtime_gate_update` events must show market-data readiness, decision-latency health, artifact-writer health, and exact no-new-entries reasons. Decision-latency readiness must degrade after repeated deadline misses or loop-budget overruns and recover only after clean windows.
Not accepted yet: no real order placement, no verified fill/stop path, no TP/BE position supervision, and no edge/profitability claim.
Next experiment after P320: short live2 smoke for 60-120 seconds; check that `deadline_cycle.max_latency_ms` is not tied to heartbeat cadence, heartbeat events arrive every ~5s, and runtime gates explain why entries remain disabled.
```

## 2026-05-19 - P321 live2 verified-entry acceptance plan

```text
Purpose: prove that live2 can move from accepted entry guard to a protected exchange position without fake fills or unverified stops.
Acceptance for P321: after P311-P320 are applied, any live2 order attempt must go through pre-entry exchange position check, `create_market_order_with_fill`, post-entry exchange position delta verification, `create_stop_market_order`, and `fetch_stop_order_by_client_order_id` visibility verification. A successful execution must emit `deadline_decision` with verdict `selected`, entry fill fields, stop fields, and a protected position in `execution_status`. If stop visibility fails after an actual fill, live2 must emit `position_integrity_error`, attempt emergency reduce-only close, and keep new entries disabled.
Not accepted yet: TP1 partial exit, BE stop replacement, final close verification, stale open-order reconciliation, Telegram safety notifications, and full unattended live2 operation. P322 must implement position supervision before increasing universe/notional.
```

## 2026-05-19 - P322 live2 position-supervisor acceptance plan

```text
Purpose: ensure a live2 position protected by P321 does not remain unmanaged after entry.
Acceptance for P322: after P311-P322 are applied, a protected position must be visible in `execution_status.protected_positions`; when stream price reaches TP1, supervisor must submit a reduce-only TP1 close, record verified exchange fill, submit and verify a breakeven replacement stop, cancel the old stop, and keep the updated position in the registry. If exchange position becomes flat, supervisor must emit `position_final_close_verified` and remove the registry row. If the current stop is not visible while exchange exposure remains, supervisor must emit `position_integrity_error`, attempt emergency reduce-only close, and disable further execution.
Not accepted yet: exact stop-trigger fill reconstruction, restart reconciliation, Telegram critical alerts, and full stress hardening under WS reconnects/CPU pressure. P323 should harden runtime coverage/reconnect/latency degradation before any broader live exposure.
```

## 2026-05-19 - P323 live2 runtime coverage hardening acceptance plan

```text
Purpose: make live2 operator/runtime behavior robust under WS reconnects, stale coverage, and CPU/decision-loop pressure before broadening real-order exposure.
Acceptance for P323: after P311-P323 are applied, `run-anomaly-live2` must expose ticker and aggTrade connect/reconnect/disconnect counters, emit `market_data_coverage_update` on coverage/gate changes, keep `market_data_ready=false` immediately when ticker/aggTrade source coverage is stale or disconnected, and recover only after `market_data_recovery_windows` clean cycles. `runtime_gate_status` must expose decision-loop overrun count/max elapsed ms and market-data clean/degraded window counters.
Not accepted yet: Telegram critical alerts, restart reconciliation, exact stop-fill reconstruction, and load/stress validation across a broad universe. P324 should add operator Telegram safety messages or restart/order reconciliation depending on the next live2 smoke result.
```

## 2026-05-19 - P326 live2 grid-log acceptance plan

```text
Purpose: make run-anomaly-live2 operator-visible during smoke tests without changing trading logic.
Acceptance for P326: after P311-P326 are applied, heartbeat output must print a compact v1-style grid with sections Соединение, Рынок, Торговля, Контроль. The grid must show stream/shard health, universe/candle coverage, decision/deadline counters, protected positions, TP1/final close counters, runtime gate reason, artifact-writer queue, and integrity risk count. It must not be the source of truth; CSV/JSON artifacts remain authoritative.
Next validation: run live2 for 60-120 seconds and confirm the grid matches live2_status.json values while not increasing decision-loop overrun count.
```

## 2026-05-19 - P327 live2 warmup/backoff acceptance plan

```text
Purpose: remove cold-start blindness and reconnect hammering before broader live2 smoke tests.
Acceptance for P327: after P311-P327 are applied, startup must emit startup_aggtrade_warmup_starting/completed events, live2_status.json must expose startup_warmup details, SymbolState rings must remain bounded by max_closed_candles_per_timeframe, and ticker/aggTrade WS status must expose backoff_attempt/last_backoff_delay_seconds. Simulated stale/no-message WS receive must set watchdog_stale and reconnect through exponential backoff rather than a fixed tight retry loop.
Not accepted yet: restart/open-order reconciliation and exact stop-trigger fill reconstruction. Next live2 smoke should verify warm-up completes without decision hot-path REST and that no entries are allowed during WS stale/reconnect coverage.
```

## 2026-05-19 - P328 live2 startup visibility acceptance plan

```text
Purpose: make live2 operator-visible from the first seconds of run-anomaly-live2 and keep the grid as a single repaintable status block.
Acceptance for P328: after P311-P328 are applied, `run-anomaly-live2` should immediately print a preparation line, then update one startup status block through preflight/ticker/universe/warm-up/aggTrade stages. Once running, the grid should overwrite its previous block in an interactive terminal instead of appending heartbeat spam. Telegram must not emit entries-enabled/entries-disabled notifications; these states must remain visible in the grid, live2_events.csv, and live2_status.json. Default auto universe should be capped at 600 with zero liquidity/trade-count minimum unless the operator passes stricter CLI values.
Next validation: run live2 for 2-5 minutes without legacy flags, confirm that universe size is close to the exchange USDT futures universe rather than ~115, and inspect `universe_selected` rejected counts to understand any remaining exclusions.
```

## 2026-05-19 - P316 live2 signal-adapter acceptance plan

```text
Purpose: connect live2 deadline decisions to a real, pure signal adapter without enabling orders.
Acceptance for P316: after P311-P315 are applied, `run-anomaly-live2` emits `deadline_decision` events where on-time actionable 5s buckets are evaluated by `Live2SignalEngine`. The adapter must not do REST/cache/file IO, must use one SymbolState and in-memory candle rings, must include shared category contract metadata, and must explicitly reject unavailable derivative-context categories instead of masking them with fallback values.
Not accepted yet: no executable entry guard, no real order placement, no actual fill, no verified stop, no TP/BE position supervision, and no profitability claim. `new_entries_allowed=false` remains mandatory.
Next experiment after P316: add executable-entry guards for stale signal, live-price drift, TP1 already touched, and RR collapsed before any execution code can submit an order.
```
## 2026-05-26 - Long continuation search from bare HTF anomaly artifacts, structural SL only

Current commit: UNKNOWN.

Analyzed `.output/results/bare_htf_short_discovery_30d_1m_5s` for long continuation after closed 1m HTF anomaly with post-close 5s confirmation. The first exploratory fixed-percent SL variants are considered invalid for strategy conclusions after review: fixed 1%/1.5% stops are not graphically/structurally justified and must not be used as evidence for Pump Awakening edge.

New artifacts were written under `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/`:
- `long_structural_grid_summary.csv`
- `long_structural_rule_search_selected.csv`
- `long_structural_best_live_filtered_trades.csv`
- `long_structural_data_quality_summary.csv`

Execution contract: entry at next 5s open after 4/6/12/24 closed post-HTF candles, adverse 5 bps entry/exit slippage, 4 bps fee per side, stop-first same-candle ordering, cap1 live-filtered portfolio summaries. Data quality was acceptable for flow fields in this artifact set: real cached `number_of_trades` and real cached/1s-aggregated `quote_volume`, no trade-count proxy rows in candidate set.

Structural SL results: local/recent post-close lows are not broadly good without filtering; broad local-stop grids are negative. The only broad positive structural model is the wide stop under the HTF anomaly low. Best broad row: wait4, RR2.0, stop under HTF anomaly low, 622 trades / 210 symbols / 31 days, winrate 42.4%, avg +0.266%, median -0.369%, sum +165.2%, PF 1.24, top5 positive share 22.4%. This is not clean enough as a final strategy because median trade is negative and many exits are time exits.

Best honest category candidates after applying category rule before cap1:
- wait4, RR2.0, HTF-low SL, `ltf4_ret>0 & prior_spikes<=3`: 358 trades / 180 symbols / 31 days, winrate 44.7%, avg +0.507%, median -0.180%, sum +181.5%, PF 1.53, top5 positive share 25.0%.
- wait4, RR2.0, HTF-low SL, `ltf4_ret>0 & red<=50`: 409 trades / 177 symbols / 31 days, winrate 44.7%, avg +0.406%, median -0.268%, sum +165.9%, PF 1.36, top5 positive share 30.2%.
- wait12, RR2.5, HTF-low SL, `ltf12_ret>0 & prior_spikes<=3`: 336 trades / 186 symbols / 31 days, winrate 44.9%, avg +0.458%, median -0.272%, sum +153.8%, PF 1.46, top5 positive share 29.3%.

Local structural stops can be made positive only by momentum filters such as `ltf4_ret>0.3%`, but the profile is worse: median remains deeply negative, stop rate is high, and top-positive dependence rises. Example: wait4, RR2.5, post-close-window-low SL, `ltf4_ret>0.3%`: 419 trades / 167 symbols, winrate 39.1%, avg +0.366%, median -0.724%, sum +153.2%, PF 1.44, top5 positive share 36.9%. This looks more like runner-tail harvesting with tight structural stops than a clean setup.

Research verdict: long continuation is more promising than short fading on this artifact set, but not proven live-ready. The honest candidate is not "fixed SL"; it is a continuation trade after HTF anomaly with SL under HTF anomaly low, optionally filtered by early post-close 5s continuation and low prior spike count. Remaining risk: 30d/snapshot dependence, negative median trade, many time exits, broad result depends on accepting a wide HTF structural stop, and live2 parity still needs an explicit implementation of the same structural stop contract and entry timing.

## 2026-05-26 - Long quality-over-frequency structural filter search

Current commit: UNKNOWN.

Follow-up question: can we sacrifice trade count to reduce top dependency and improve winrate, median and total sum while keeping structural SL only?

Artifact: `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/long_structural_htflow_quality_limited.csv`.

Result: yes, the best current quality candidate is wait6/RR1.5 with HTF anomaly low structural stop and rule `ltf6_ret>0.5% & risk 1.5-5%`. This waits 6 closed 5s candles after HTF close, requires post-close continuation of at least +0.5%, and avoids both too-tight/noisy and too-wide structural risk.

Metrics: 215 trades / 132 symbols / 30 days, WR 53.5%, avg +0.785%, median +0.391%, sum +168.8%, PF 1.78, top5 positive share 19.9%, max-symbol positive share 5.0%, first half +68.9%, second half +99.9%. Compared with broad wait4/RR2 HTF-low row, this reduces trades 622 -> 215, raises WR 42.4% -> 53.5%, median -0.369% -> +0.391%, sum +165.2% -> +168.8%, and top5 share 22.4% -> 19.9%.

More conservative/high-winrate variant: wait6/RR1.0 with the same `ltf6_ret>0.5% & risk 1.5-5%` rule: 233 trades / 132 symbols / 30 days, WR 56.7%, avg +0.503%, median +0.598%, sum +117.2%, PF 1.51, top5 positive share 17.1%, max-symbol positive share 4.3%. This is cleaner but gives up total expectancy.

Interpretation: the improvement is coherent and not just a top-trade filter: it uses only entry-time-visible features, improves both halves, and lowers top concentration. Still not proven out-of-sample; this should be implemented as a minimal honest long-discovery rule and validated on another period/cache snapshot before live.

## 2026-05-26 - Long candidate nature audit

Current commit: UNKNOWN.

Question: whether the wait6 `ltf6_ret>0.5% & risk 1.5-5%` entry is just a dumb momentum rule or a proxy for pump nature.

Artifacts:
- `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/long_quality_candidate_nature_features.csv`
- `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/long_quality_candidate_nature_summary.csv`
- `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/long_quality_candidate_nature_bins.csv`
- `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/long_quality_candidate_nature_rule_checks.csv`

Interpretation: `ltf6_ret>0.5%` is a mechanical proxy, not the final explanation. The underlying nature appears to be post-anomaly acceptance: after the closed 1m HTF anomaly, price does not immediately fade back through the anomaly close, keeps a meaningful structural invalidation under HTF low, and continues upward for 30s without requiring a single-print blowoff.

Evidence:
- Base candidate: 215 trades / 132 symbols / 30 days, WR 53.5%, median +0.391%, sum +168.8%, PF 1.78, top5 share 19.9%.
- Wins and losses have similar median `ltf6_ret` (~0.80-0.87%), so the raw 30s return alone does not explain outcome.
- Distributed flow matters: top one 5s quote-volume share 25-40% was best among broad bins (109 trades, WR 56.0%, median +0.55%, sum +115.1%); single-print concentration >75% was bad (9 trades, WR 22.2%, median -0.89%).
- Moderate taker share is better than extreme buyer aggression: `conf_taker_share` 45-55% produced high-quality outcomes; `taker<=55%` rule had 104 trades, WR 61.5%, median +1.18%, PF 2.23, but higher top share (30.9%).
- Late-volume chase is worse than early burst then acceptance: `last3_quote_share<=50%` had 131 trades, WR 56.5%, median +0.44%, sum +125.7%; `prior_spikes<=3 & last3_quote_share<=50%` had 100 trades, WR 64.0%, median +1.08%, sum +133.0%, PF 3.39, but top share rose to 28.6%.
- Prior pump history matters: `prior_spikes<=3` had 163 trades, WR 57.1%, median +0.48%, sum +149.6%, PF 2.17, top share 21.6%; higher prior-spike buckets degraded median and stop rate.

Research conclusion: the entry should not be described as "buy after +0.5% in 30s". It should be described as "buy post-anomaly acceptance": closed HTF flow anomaly, no immediate fade, distributed/non-single-print 5s confirmation, controlled structural risk to HTF low, and preferably clean prior history. The raw `ltf6_ret` threshold is only the cheapest measurable trigger for this acceptance.

Daily grouping artifact for the `prior_spikes<=3 & last3_quote_share<=50%` nature refinement was written to `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/long_quality_prior3_last3le50_by_day.csv`. It contains 100 cap1-filtered trades / 78 symbols, total +132.96%, WR 64.0%, median +1.08%.

## 2026-05-26 - live2 post_htf_rolling_20260526_090002 readout

Current commit: UNKNOWN.

Run: `.output/results/live2_anomaly_runs/post_htf_rolling_20260526_090002`.

Confirmed from artifacts:
- BAS was a live2-managed long selected as `runner_balanced`, filled near 0.0241794, closed by TP1/full close near 0.024423 after about 8.5 minutes, gross positive before fees.
- CGPT was a live2-managed long selected as `runner_oi_confirmed`, filled near 0.0251 and finally closed near flat after about 97 minutes. It had strong LTF push but weak post-HTF anomaly acceptance, so it is an example of stale/stalled flow risk.
- SKYAI has rejects/dependency/deadline events in this run but no artifact-confirmed selected/fill/protected-position lifecycle, so it is not a valid live2 trade sample from this folder.

Key limitation: these trades were not selected by `post_htf_acceptance_long`, so they cannot prove that the new rolling post-HTF category itself is late. They do prove that the current live2 runner path needs post-entry stale-flow management.

Runtime limitation: artifacts were very large (`live2_events.csv` about 4.6 GB and `live2_near_misses.csv` about 5.1 GB) with many deadline/backlog events. Before interpreting late entries too strongly, artifact volume and scheduler pressure need their own fix.

Follow-up implemented: P406 adds closed-post-fill 5s early-exit management for flow exhaustion, seller pressure, and OI-up/no-progress stalls. Next experiment should run live2 forward and inspect early-exit artifacts against TP1/final-close outcomes.

## 2026-05-28 - HTF/LTF runner-vs-noise 7d separation readout

Current commit: 736cd41b, dirty worktree observed.

Run analyzed: `.output/results/htf_ltf_runner_discovery_7d`.

Additional local analysis artifacts:
- `.output/results/htf_ltf_runner_discovery_7d/noise_separation_analysis/valid_label_profile_summary.csv`
- `.output/results/htf_ltf_runner_discovery_7d/noise_separation_analysis/candidate_validlabel_decisiontime_runner_lift_top.csv`
- `.output/results/htf_ltf_runner_discovery_7d/noise_separation_analysis/candidate_30s_validlabel_shape_rules.csv`
- `.output/results/htf_ltf_runner_discovery_7d/noise_separation_analysis/selected_30s_shape_rules.csv`
- `.output/results/htf_ltf_runner_discovery_7d/noise_separation_analysis/selected_trades_decisiontime_net_return_top.csv`

Important correction: subminute profiles have many candidates with missing LTF/future-label windows. They must not be counted as confirmed non-runners. After restricting to `future_label_status=ok` and `htf_ltf_status=ok`, valid labeled base rates are: `5m_30s` 154 rows / 17 runners / 11.0%; `3m_30s` 188 rows / 22 runners / 11.7%; `1m_15s` 266 rows / 32 runners / 12.0%; `5m_1m` 2526 rows / 101 runners / 4.0%.

Candidate-level separation on valid 30s labels points to real participation and non-single-print structure, not just generic dormancy: top quintile `htf_ltf_number_of_trades >= ~6105` gives 19/69 runners (27.5%); `htf_ltf_quote_top1_share <= ~0.262` gives 17/68 runners (25.0%); `prior_spike_next_decay50_share <= ~0.371` gives 18/68 runners (26.5%). High absolute baseline/dormancy volume and moderate rather than extreme dormancy-to-anomaly ratios also lifted runner labels.

This is not yet an edge claim. The selected 30s live-filtered streams are only marginally positive (`5m_30s` 46 closed, avg +0.053%, median -0.191%; `3m_30s` 57 closed, avg +0.098%, median -0.215%) and are highly top-dependent (`top20pct_positive_share` about 0.89). `5m_1m` and `1m_15s` selected streams are negative. Built-in broad entry-window rules remain negative across profiles.

Working interpretation: current runner labels are mostly separated from noise by "large real crowd participation that stays distributed and historically does not collapse immediately", while the current executable/trailing trade model still enters too much late/noisy flow. Strict `dormancy_ok` by itself reduced runner rate in this 7d set, so it may be too strict or may be selecting dead illiquid names rather than usable awakening; do not remove the dormancy concept, but re-test it as a graded liquidity/dormancy band instead of a binary positive proof.

Next best experiment: run a focused validation on another period using only valid-label rows and a predeclared 30s candidate family: real LTF trade-count/quote-volume participation floor, distributed top1 flow cap, prior-spike sustain history, moderate anomaly ratio/no-chase guard, and explicit entry replay PnL. Treat future runner label as evaluation only.


## 2026-05-29 - P446 direct LTF fetch audit

Hypothesis: rolling discovery for `3m/30s` and `5m/30s` does not need a stored 1s intermediate cache. True aggTrades can be fetched for targeted windows and aggregated directly to trusted 30s cache without changing signal timing or feature availability.

Protocol: after P446, rerun 45d and inspect `htf_ltf_runner_targeted_ltf_fetch.csv`/materialize artifacts for `data_source=binance_futures_aggTrades_direct_to_target_ltf`, `intermediate_1s_cache=False`, and selected trades with `entry_timestamp_ms >= decision_available_timestamp_ms`.


## 2026-05-29 - P450 live2 data completeness audit

Current commit: UNKNOWN.

Result: live2 rolling signal path still avoids future/outcome fields, but incomplete startup 1m history could corrupt or suppress C/A/S context. P450 turns this into an explicit data dependency: paginated 48h 1m warmup, zero-volume candle continuity, and recent-contiguous-history validation before marking a symbol warmed. Next live smoke must inspect startup_htf_baseline_warmup_completed errors for incomplete_1m_htf_baseline and confirm rolling_1m_history_not_ready disappears only for symbols with complete enough history.

P450 also blocks rolling live C/A/S decisions when selected 30s confirmation or rolling HTF candles contain aggTrade-id gaps, so incomplete websocket/rest trade candles become data dependencies instead of tradable signals.


## 2026-05-29 - P451 live rolling tolerance audit

Protocol after P451: in live artifacts, separate selected trades by `confirm_30s_aggtrade_gap_quality` and `rolling_htf_30s_aggtrade_gap_quality`. Do not mix tolerated-gap trades with clean trades when evaluating live edge. Watch counts of `*_aggtrade_gap_above_tolerance` reasons; frequent rejects mean the stream quality is insufficient for real-flow trading, not that the entry filter is bad.

## 2026-05-29 - P452 live2 rolling data repair audit

Hypothesis: quiet periods should be represented by exchange-reported 1m zero-volume klines, not by missing in-memory candles. P452 should reduce false `rolling_1m_history_not_ready` during live rolling C/A/S without allowing decisions on stale or incomplete context.

Protocol: after applying P452, run live2 in minimal-risk/scouting mode and inspect `live_events.csv` / deadline decisions for `rolling_1m_context_rest_repair_ready`, `rolling_1m_context_rest_repair_incomplete`, selected C/A/S signals, and 30s gap tolerance fields. Selected signals must still have no future/outcome-derived fields and must pass entry guard from current live price.

## 2026-05-29 - P454 live2 audit backpressure validation protocol

Hypothesis: live2 can keep product-grade strategic audit even when high-volume raw deadline/near-miss rows exceed bounded CSV budgets.

Protocol after P454: run live2 for at least one closed hour. Check that `live2_deadline_summary.csv` contains full-run counts for routine data readiness/dependency blockers, `live2_near_miss_summary.csv` and `live2_near_miss_examples.csv` keep updating after raw `live2_near_misses.csv` reaches budget, and `live2_events.csv` still contains every selected/entry_guard/execution/integrity decision. Stop during a top-growth scan once and verify `top_growth_index.csv` records `completion_status=partial`, `processed_count`, `remaining_count`, `top_file`, and `status_file`.


## 2026-05-30 - P458 live2 NameError sweep

Status: PROPOSED. Current commit: UNKNOWN.

Fixes the remaining live2 undefined global found by a sweep after the flat-stop recovery crash: `Live2PositionSupervisor.run_cycle()` referenced `_dict(...)` when classifying final-close reasons, but this helper was not defined or imported in `position_supervisor.py`. The patch adds a local typed `_dict()` helper and does not change position-management logic, exchange calls, fills, stops, or PnL recovery.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Additional static sweep used for live2: import all `research_tools.anomaly_live2.*` modules and inspect function bytecode for unresolved `LOAD_GLOBAL` / `LOAD_NAME`; after this patch no unresolved live2 globals remain.

## 2026-05-30 - P459 live2 post-crash validation protocol

Hypothesis: removing heavy routine payloads and REST repair from the decision hot path improves live2 latency without creating hidden data holes, because WS candles remain the source of live flow and missing rolling context remains visible until background official-1m maintenance fills it.

Protocol after P459: run live2 for 60-90 minutes. Accept only if `market_data_ready_for_entries=true`, WS stale shard count remains 0, `rolling_context_maintenance.total_errors=0`, `symbol_status_counts.in_position == execution_status.open_protected_positions`, selected/entry/execution rows remain present in events after budget pressure, and top-growth status has normal `ok`/`below_threshold` rows for the closed hour. If `data_dependency_not_ready` remains high, split it by 30s aggTrade gaps vs true 1m maintenance lag before touching strategy thresholds.

## 2026-05-30 - P460 rolling 1m continuity validation protocol

Hypothesis: high `rolling_1m_history_not_ready` after P459 is caused by recent 1m continuity gaps behind a fresh WS-built tail, not by broken sockets or insufficient ring capacity.

Protocol after P460: run live2 for 60-90 minutes. Accept only if `rolling_1m_maintenance_recent_contiguous_count` is populated in `live2_symbol_state.csv`, maintenance has `total_errors=0`, and `signal_engine.dependency_reason_counts` no longer clusters around tiny histories like `0/348`, `1/348`, `1/540`. If `rolling_1m_history_not_ready` persists, inspect symbols whose contiguous count is below 720 and check whether REST responses are missing candles or maintenance scheduling is too slow.


## 2026-05-30 - P461 live2 maintenance concurrency validation protocol

Hypothesis: the P460 rolling 1m maintenance model is correct, but its status/currentness read path must use a locked state-store snapshot because websocket candle closing and official-kline maintenance can mutate `ring.closed` concurrently.

Protocol after P461: run live2 for 60-90 minutes. Accept only if the process does not crash with deque mutation, `rolling_1m_maintenance_recent_contiguous_count` continues to update, maintenance errors stay near zero, and the operator console no longer prints the full multi-line grid on every heartbeat in non-inline terminals. If rolling 1m dependency rejects return, inspect maintenance scheduling/rate limits rather than reintroducing unlocked deque reads. Also check that runtime-gate refresh does not call maintenance target discovery directly; `active_target_symbols` should update from worker cycles.

## 2026-05-30 - P463 live2 operator/runtime validation protocol

Hypothesis: live2 can keep the all-symbol P462 deadline fix while becoming safer operationally: non-critical Windows snapshot locks should not disable entries, startup should be materially shorter from overlapping independent REST phases, top-growth should complete a closed-hour audit in a small number of worker loops instead of one symbol per heartbeat, and console output should be warmup-progress-only before startup and grid-only after startup.

Protocol after P463: run live2 for at least one closed hour. During runtime, copy or zip the active run directory once to reproduce file-lock pressure. Accept only if `artifact_writer_status.noncritical_error_count` may rise but `critical_error_count=0`, `artifact_writer_ready=true`, and `new_entries_allowed` is not blocked by snapshot write failures. Check `top_growth_index.csv` reaches `completion_status=completed` for the closed hour, top/status CSV rows include completion metadata, startup duration is lower than the previous ~29 minutes, and console stdout does not print non-grid runtime logs after warmup.
