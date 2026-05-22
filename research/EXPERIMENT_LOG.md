
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
