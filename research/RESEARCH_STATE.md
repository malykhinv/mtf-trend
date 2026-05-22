# Anomaly Research State

## 2026-05-22 - P378 aggTrade/event cache full-bucket guard

```text
Current patch status: P378 PROPOSED / UNKNOWN commit.
Question: audit p.5 aggTrade/event-derived cache for critical lookahead/data leakage only and patch if needed.
Finding: aggTrade REST/event windows can start or end at arbitrary milliseconds. The old aggregation accepted buckets by `bucket_timestamp <= end_timestamp_ms`, so a final bucket could contain only the early part of a second/5s window while being saved as a closed OHLCV/flow candle. Targeted event backfills are especially exposed because their edges are not guaranteed to align with 1s/5s boundaries.
Change: aggTrade-to-OHLCV aggregation now requires full source-window coverage for every emitted bucket and records candle availability plus aggTrade coverage metadata. 1s->subminute materialization uses that metadata and drops derived buckets whose complete interval is not covered. Aggregation version strings were bumped for both direct 1s backfill and materialized subminute caches.
Residual risk: existing caches generated before P378 may already contain partial edge buckets and are not cleaned automatically by this patch. Regenerate affected 1s cache and derived subminute caches before making parity/edge claims from aggTrade data. This does not audit OI/derivatives publication lag.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py, plus direct checks that a full 1s bucket is kept and an edge-partial 1s bucket is dropped.
```

## 2026-05-22 - P377 OHLCV fetch normalization availability guard

```text
Current patch status: P377 PROPOSED / UNKNOWN commit.
Question: audit p.4 Binance OHLCV normalization for critical lookahead/data leakage only and patch if needed.
Finding: Binance klines and generic CCXT OHLCV fetches can include a still-forming candle when the fetch end is current time, or include a candle whose open time is <= historical end_timestamp_ms even though its final high/low/close/volume/quote_volume/number_of_trades/taker_buy values were not available at that as-of cutoff. Saving that row poisons the cache; later runs may treat partial flow as final.
Change: Binance normalization now drops rows using raw kline close_time + 1 > min(end_timestamp_ms, fetch_time_ms). The final fetch frame also enforces timestamp + timeframe_ms <= availability cutoff. M10 aggregation from M5 data applies the same cutoff to avoid partial target buckets.
Residual risk: this does not solve historical exchange publication lag beyond candle close, and does not audit OI/derivatives context. Existing caches that already contain partial rows need refetch/overwrite for affected newest candles if they were produced before P377.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py, plus direct Binance kline normalizer check that drops a row not closed by the cutoff. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P376 OHLCV cache availability guard

```text
Current patch status: P376 PROPOSED / UNKNOWN commit.
Question: audit p.3 OHLCV cache loader for critical lookahead only and patch if needed.
Finding: cached OHLCV rows use exchange candle open time in `timestamp`. The row's high/low/close/volume/quote_volume/number_of_trades are only fully known after candle close. The previous backtest window slicing used `timestamp <= end_timestamp_ms`, so an explicit as-of cutoff inside a candle could include data from a candle that was not closed yet.
Change: attach explicit availability columns to ParquetStorage OHLCV loads; slice closed and pair candidate input windows by `available_timestamp_ms <= end_timestamp_ms`; propagate setup/decision availability timestamps into candidates. Lower-TF aggregation also carries availability timestamps and excludes partial target buckets at the end boundary.
Residual risk: this is not a full exchange publication-lag model and does not fix OI/derivatives context availability; those are later audit nodes. Historical all-cache runs with no explicit cutoff remain final-data backtests, now with clearer timestamp semantics.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P375 universe/symbol-scope guard

```text
Current patch status: P375 PROPOSED / UNKNOWN commit.
Question: audit p.2 universe/symbol selection for critical backtest lookahead/survivorship only and patch if needed.
Finding: the critical p.2 issue is not per-candle future data; it is universe contamination. A historical run with --end-timestamp-ms and no explicit --symbols scanned the current local cache snapshot, which is not an as-of historical listing universe. Reused candidates also had no symbol-scope contract, so a subset-universe candidate CSV could be silently treated as the current requested universe.
Change: run-anomaly-lab now blocks historical cache-snapshot universe unless explicitly overridden with --allow-cache-snapshot-universe true. Backtest outputs record universe_symbol_scope, normalized requested symbols, survivorship_bias_risk and historical_listing_snapshot_available=false in run_config.csv plus anomaly_universe_contract.csv. Reused candidates must now prove the same universe scope/symbol set. Explicit symbol filtering is normalized across closed/pair collectors and coverage artifacts.
Residual risk: this does not create a true historical listing universe. For a clean all-market historical test, provide an explicit as-of universe/symbol snapshot or accept the override as cache-snapshot biased.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P374 reused candidate lookahead guard

```text
Current patch status: P374 PROPOSED / UNKNOWN commit.
Question: audit p.1 CLI/config for critical backtest lookahead only and fix what is critical.
Finding: the critical p.1 issue is --reuse-candidates-dir. It could load anomaly_candidates.csv from a previous run without verifying the candidate collection contract, symbol scope, timeframe pair or end timestamp. That lets a run that claims one window/config consume candidates collected under another, including candidates from after the requested end timestamp.
Change: reused candidates now require sibling run_config.csv, exact match of critical collection config, required audit columns, valid decision timestamps, and row-level filtering to current timeframe/contract/window/symbols.
Residual risk: this does not address non-critical p.1 issues such as in-sample grid selection or execution realism; those belong to later nodes. Reuse artifacts from older runs without run_config.csv must be regenerated.
Validation: python -m compileall data/exchanges research_tools cli constants.py main.py. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P373 live2 trade parity and targeted event cache

```text
Current patch status: P373 APPLIED locally / UNKNOWN commit.
Question: compare live2 trades INJ (run 20260521_164122) and BEAT (run 20260521_194030) with same-period category-only backtests, avoid a full 1s cache, and fix any live2/backtest choke or display bugs.

Finding: the old comparison was not trustworthy. BEAT was rejected live at 2026-05-21T22:11:30Z only by mark_basis_below_category_min, then selected later at 2026-05-21T22:11:47Z after mark WS basis improved. Historical backtest cannot reproduce that tick-level mark basis from closed mark klines without lookahead or stale under-entry, so mark basis was an unfair trading gate. INJ was also missed/shifted by mixing live aggTrade-derived entry counts with raw kline setup trade-count baselines in the backtest.

Change: use targeted aggTrade windows around interesting live events, not a full-second cache; fix bounded aggTrade pagination; add availability-aware derivatives context with 1m mark as-of timestamps; remove mark basis from live-priority category blockers while keeping diagnostics; compute prior spike/fade context from closed cached 5m candles like live2; compute setup quote/trade baselines from the same 5s aggTrade cache in 1m/5s pair mode. Live2 grid active/trading rows and stop-trigger settle handling were fixed at the same time.

Result after targeted backfill/materialized 5s and fixed4 backtests: runner_oi_confirmed sees both INJ and BEAT. INJ appears at decision 2026-05-21T17:36:30Z / entry 17:36:35, while live entered later at 17:36:42.752Z fill 5.211. BEAT appears at decision 2026-05-21T22:11:30Z / entry 22:11:35, while old live entered later at 22:11:47.668Z fill 0.8475. The BEAT delay was mostly the old mark-basis category gate, not proof that the market was untradeable.

Residual risk: fixed4 is an event-window parity audit, not an edge proof. Backtest still uses next-bar proxy execution and cannot claim exact live fill prices. The next validation is a new live2 run on P373: selected/rejected artifacts should show no mark-basis trading rejects, Active should be current/session-seen, Trading should show run-level orders/positions, and a normal stop fill should not leave runtime gates disabled.
```

## 2026-05-21 - P372 live2 stage state store hotfix

```text
Current patch status: P372 PROPOSED / UNKNOWN commit. GitHub branch head checked before patch: 2e9f38269a32546247fe22bff794cd3d8b4b5f4f.
Incident: after applying P371 locally, `run-anomaly-live2` crashed during startup with `AttributeError: 'SymbolStateStore' object has no attribute 'stage_symbol_counts'` in `AnomalyLive2Runner._market_data_status()`.
Cause: runner/status-grid path referenced the new stage counter, but the state-store boundary was missing from the runtime code. Because `SymbolState` uses `slots=True`, the stage timestamp fields must also exist explicitly; otherwise the next decision path could fail when deadline code assigns `stage0_passed_ms` etc.
Change: add `LIVE2_STAGE_LABELS`, explicit `stage0_passed_ms`..`stage5_passed_ms` fields, export them into symbol-state artifacts, and implement `SymbolStateStore.stage_symbol_counts()`. Keep `actionable_symbol_counts` backward-compatible but source it from stage0 threshold crossings.
Trading impact: none. Diagnostics/status only.
Validation: compileall plus direct `SymbolStateStore.stage_symbol_counts()` smoke.
```

## 2026-05-21 - P371 live2 stage-aware active grid

```text
Current patch status: P371 PROPOSED / UNKNOWN commit. GitHub branch head checked before patch: f12455f264f63432af8b64abed9f433532a0390b.
Question: `Активные 564/579` is misleading because it counts ordinary real-trade buckets, not symbols that passed meaningful strategy stages.
Finding: the previous active metric used `actionable_since_ms`, and P370 intentionally let every real 5s trade bucket reach the signal engine for backtest parity. That made operator UI interpret passive liquidity as active opportunity.
Change: live2 now keeps TTL stage flags: stage0 threshold-crossed bucket, stage1 signal selected, stage2 entry guard checked, stage3 guard accepted, stage4 execution attempted, stage5 position opened/protected. The grid renders stage0/1/2 and stage3/4/5 counts. Backward-compatible `actionable_symbol_counts` now reports stage0 counts, not parity-only buckets.
Trading impact: none. The decision path still evaluates parity buckets; only diagnostics and status-grid semantics change.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; status-grid smoke asserts `stage0/1/2 1/0/0`.
```

## 2026-05-21 - P370 deeper live2/backtest parity repair

```text
Current patch status: P370 APPLIED locally / UNKNOWN commit.
Question: make live2 match backtest as closely as possible excluding network/CPU latency, and check for logic/math/substituted-value errors.
Finding: P369 aligned entry execution, but live2 still used several lookalike fields differently from backtest. The live whipsaw cap used 24h prior context, while backtest uses the 60 setup-candle baseline. Live effort-per-return divided by the final 5s return, while backtest divides by the whole forming setup return. Live taker/flow-hold was trailing stream-native, while backtest uses the confirmation segment. Live also had a pre-signal single-bucket actionability gate that could skip a cumulative backtest candidate before the signal engine saw it. Finally, live still allowed a 5s-scaled baseline fallback before 60 closed 1m baseline candles existed.
Change: live2 now computes whipsaw, quote/trade effort per return, taker share/delta, and flow_hold from the same forming 1m/5s confirmation segment and 60x1m baseline used by backtest. Live quote/trade setup ratio constants are aligned to the current backtest CLI defaults, 5.0/5.0. The 5s-scaled trading baseline fallback was removed; live returns data_dependency_not_ready until the real 1m baseline exists. Any real 5s trade bucket reaches the signal engine for parity, even if the old actionability display thresholds are not crossed. Computed-feature missing values now reject like backtest masks instead of being reported as external data dependencies.
Residual parity risk: live candle rings are built from real aggTrades and do not synthesize zero-trade candles. If the historical cache contains exchange kline zero-volume candles, baseline medians can still differ. This is honest and visible rather than hidden by fallback.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py tests\test_anomaly_continuation_lab.py`; `python -m compileall data\exchanges research_tools cli constants.py main.py`; `git diff --check`.
```

## 2026-05-21 - P369 live2/backtest execution parity audit

```text
Current patch status: P369 APPLIED locally / UNKNOWN commit.
Question: check live2/backtest parity carefully.
Finding: after P367/P368, candidate shape was much closer, but execution parity still had two live-overfilters and one risk-model mismatch. Live2 entry guard accepted only 2000ms signal age and RR>=0.95, while the backtest market model enters on the next 5s entry candle and uses min_market_rr_to_signal_tp1=0.70. Live2 also built signal stop/TP1 from decision_box_low and 1R, while backtest uses initial_stop=max(box_low - 0.05*box_range, decision EMA20) and signal TP1=rounded(entry + 0.75*(entry - box_low)).
Change: live2 signal construction now uses the backtest stop/TP1 model for selected signals and entry-guard RR checks. Live2 entry guard defaults are now max_signal_age_ms=5000 and min_rr_to_tp1=0.70. The backtest latency stress default/grid now uses 0ms and 5000ms to match the live entry freshness window. Near-miss artifacts expose live_setup_decision_ema20, stop buffer, and tp1_r.
Residual parity risk: live2's runner_flow flow_hold remains stream-native rather than bit-identical to the backtest confirmation-segment calculation. It is not currently the known live-overfilter, but it should be checked in the next live near-miss funnel.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; compileall on touched files.
```

## 2026-05-21 - P368 remove live1 monolith and tighten parity audit

```text
Current patch status: P368 APPLIED locally / UNKNOWN commit.
Question: can the old 19k-line live1 monolith be removed, and are there other live2/backtest filtering mismatches?
Change: removed research_tools/anomaly_micro_live.py and the run-anomaly-live CLI command. The still-used aggTrade cache aggregation helpers moved to research_tools/anomaly_aggtrade_cache.py, and standalone run-anomaly-top-growth now uses live2 top-growth plumbing. The live1-only lifecycle test file was removed with the live1 runner.
Parity finding: another live2-overfilter remained after P367. Live2 rejected any final 5s decision candle that was not green, but backtest does not require the last confirmation candle to be green; it requires setup-level price_retention, verticality and hold_count across the forming setup. Live2 now removes the single-5s upward gate and applies the backtest setup-level confirmation filters instead, with near-miss live_setup_* diagnostics.
Residual parity risk: runner_flow flow_hold is not bit-identical; live uses trailing no-lookahead 5s hold while backtest's field name is next_n but is computed on the confirmation segment available at decision. This is not currently an overfilter relative to backtest, but future run artifacts should compare runner_flow rejects separately.
Validation: `python -m compileall data\exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py tests\test_anomaly_continuation_lab.py`; `python main.py run-anomaly-top-growth --help`; CLI help no longer lists run-anomaly-live.
```

## 2026-05-21 - P367 live2/backtest candidate parity repair

```text
Current patch status: P367 APPLIED locally / UNKNOWN commit.
Question: why did post-P365 live2 run 20260521_124514 still have selected_count=0?
Finding: live2 still choked before entry guard. The run had 123388 decisions, selected_count=0, execution/orders=0, and rejects dominated by stream_candle_is_not_upward_price_confirmation=67516 and prior_whipsaw caps about 50k per live-priority category. Top-growth showed real hourly movers (BSB +14.0% and +17.5%, EDEN +11.9%, FIDA +10.6%), so "dead market" is false. Root cause: live2 applied shared category caps to a single 5s actionable bucket, while backtest builds a forming 1m setup from 5s entry candles after the default 4 confirmation candles. This made prior_whipsaw/range/risk materially stricter in live than in backtest.
Change: live2 signal features now build a backtest-like 1m/5s forming setup before category evaluation. Category gates use cumulative setup quote/trade pace, forming setup range/risk, and prior-whipsaw divided by the forming setup range. Near-miss artifacts now expose live_setup_* parity diagnostics and feature initial risk. The grid `Активные` count now uses current TTL / unique active symbols since the current session metric start.
Risk: medium. This aligns live2 with the existing backtest candidate contract, but it means default live2 category evaluation waits for the same 4x5s confirmation horizon as the backtest before shared categories can accept. If the operator target is truly pump-start-to-order <5s, the backtest contract itself must be changed and revalidated; live2 should not fake faster parity.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; `python -m compileall research_tools\anomaly_live2 cli\commands.py cli\parser.py constants.py main.py`; `python -m compileall data\exchanges research_tools cli constants.py main.py`.
```

## 2026-05-21 - post-P365 live2 choke audit

```text
Current patch status: no additional trading-logic patch after P365.
Question: are current live2 zero-selected symptoms still caused by dumb blockers?
Finding: current code no longer shows the confirmed unit/scale blockers fixed by P365. The old run 20260521_110133 cannot validate the new funnel because it predates P365. Remaining gates are strict but strategy-level: upward price confirmation, mark premium, OI delta, prior fake-pump caps, range expansion, and no-lookahead flow hold. Changing them without a post-P365 near-miss/top-growth comparison would be threshold loosening, not root-cause repair.
Next validation: restart live2 on current head and inspect live2_near_misses.csv, decision_funnel, selected_count, entry_guard counts, and top_growth mismatch. If selected_count remains zero, the next patch should be category/threshold research-driven, not a live hotfix.
```

## 2026-05-21 - P365 live2 signal feature contract parity

```text
Current patch status: P365 APPLIED locally / UNKNOWN commit.
Question: are the zero selected signals in run 20260521_110133 healthy strictness or bugs that choke everything?
Finding: not fully healthy. The run had real activity, but live2 compared a 5s baseline quote value directly to min_baseline_quote_daily_proxy=300000 and used a single 5s candle range as the prior-whipsaw denominator. Those units do not match the backtest/category contract and can make all categories unreachable. Initial risk also used the current 5s low instead of a decision-box low, making min_initial_risk_pct likely unreachable if earlier filters passed.
Change: live2 now computes baseline_quote_daily_proxy from the 5s baseline pace, and uses a recent closed-live decision box for prior-whipsaw range and initial stop/risk. Thresholds and execution safety were not loosened.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
Next validation: restart live2 and require near-miss/decision artifacts to show whether remaining blockers are now real OI/mark/category rejections, entry_guard drift/RR/stale rejections, or actual exchange execution.
```

## 2026-05-21 - P364 live2 session-scoped operator grid

```text
Current patch status: P364 APPLIED locally / UNKNOWN commit.
Question: make grid-log numbers session-scoped where appropriate, show four session growth tops, and replace dot spacer rows.
Change: the live2 grid now uses session-scoped decision/execution/runtime counters derived by subtracting a baseline at the session metric window boundary. First session after process start counts from live start; later session rollovers reset the baseline. Session top tracker limit is four, and grid separators are full-width underscore lines.
Trading impact: none. This changes operator display semantics only; cumulative forensic counters remain in events/diagnostics.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
```

## 2026-05-21 - P363 live2 active-symbol grid truth

```text
Current patch status: P363 APPLIED locally / UNKNOWN commit.
Question: why did run 20260521_110133 show 0 active symbols after 50+ minutes?
Finding: it was a metric bug, not a market fact. Live2 processed about 67m of market-runtime, 41053 decisions, 40981 signal evaluations, and 0 selected signals. However, the grid counted only state.status=actionable. The deadline engine writes actionable_since_ms for every actionable bucket but immediately sets status back to watching after the verdict, so current status actionable stays zero by design.
Change: the grid now renders `Активные current/seen` using actionable_since_ms within the radar TTL and total symbols ever actionable in the run. This makes the operator view reflect active recent anomaly symbols instead of a transient internal enum.
Trading impact: none. It does not loosen filters; selected_count remains the count of category-accepted entry signals, and entry_guard/execution are unchanged.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
```

## 2026-05-21 - P362 live2 near-miss artifacts

```text
Current patch status: P362 APPLIED locally / UNKNOWN commit.
Question: why did current live2 show zero active/selected symbols despite running for much longer on the terminal?
Finding: in run 20260521_103438, persisted diagnostics showed about 11m of market-runtime after startup/context warmup, not full process wall-clock. The run had 9129 deadline decisions, selected_count=0, entry_guard total_checked=0, and execution total_execute_calls=0, so the choke point was signal contract before entry/execution.
Change: live2 writes live2_near_misses.csv for post-actionable non-selected decisions. This makes "market was quiet vs filters cut too hard" directly inspectable by symbol, stage, blocker reasons, prior whipsaw/spike/fade context, OI/mark status, flow metrics, and full event JSON.
Trading impact: none. The patch adds audit only; no category threshold, runtime gate, entry guard, exchange order, fill, stop, or position lifecycle behavior changed.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
Next validation: restart live2 on this commit and require live2_near_misses.csv to fill while live2_events.csv still records every deadline_decision; if artifact_writer_status rejected_count/error_count rises, new entries must remain disabled rather than silently losing audit.
```

## 2026-05-21 - P361 live2 operator grid semantics

```text
Current patch status: P361 APPLIED locally / UNKNOWN commit.
Question: live2 terminal grid did not match requested semantics; `Активные 0/578` mixed current actionable entry candidates with universe size.
Change: live2 status grid now uses four-column `◆` sections with dot separator rows and renders `Аномалии` as total actionable decisions while `Активные` is current actionable / total selected entry signals. Context, latency, market, session top, and trading rows are kept compact and operator-facing only.
Validation: `python -m compileall research_tools/anomaly_live2/status_grid.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
```

## 2026-05-21 - P360 live2 entry attempt timing artifacts

```text
Current patch status: P360 APPLIED locally / UNKNOWN commit.
Question: can live2 prove where time is spent for each attempted entry, and are recent live runs blocked by dumb entry blockers?
Change: live2 deadline_decision events now include `entry_attempt_timing` for actionable buckets, with signal evaluation, entry guard, runtime gate, and execution call timestamps/durations. Execution results now include internal `execution_timing`: pre-position fetch, market order submit/fill, post-position fetch, stop submit, stop visibility verification, and emergency close timing when applicable.
Run check: latest usable full run 20260521_044228 had 0 selected signals, 0 entry guard checks, 0 execution calls, 0 orders, and 0 integrity errors. It was not blocked by order/execution plumbing; rejects were signal-contract filters dominated by non-upward price confirmation and prior whipsaw/spike/fast-fade category caps.
Risk: artifact-only hot-path additions use in-memory timestamps and JSON event fields; no entry thresholds, fallback data, fill model, or stop logic changed.
```

## 2026-05-21 - P359 anomaly-lab latency grid

```text
Current patch status: P359 APPLIED locally / UNKNOWN commit.
Question: how to run 40d anomaly-lab without latency simulation, and what latency grid should be used when enabled?
Change: normal `run-anomaly-lab` still does not run hidden 1s latency stress unless `--latency true` is passed. When latency stress is enabled, default grid is now `0ms, 2000ms`. The 2000ms value is aligned with live2 `entry_guard_max_signal_age_ms`, so it models the largest signal age live2 should still execute.
Validation: `python -m compileall research_tools/anomaly_strategy_backtest.py cli/commands.py cli/parser.py`.
```

## 2026-05-20 - P358 live2 top-growth audit off hot path

```text
Current patch status: P358 APPLIED locally / UNKNOWN commit.
Question: is anything left before launching after P357?
Change: top-growth audit no longer calls exchange REST from the main heartbeat/decision loop. The runner starts a single daemon worker for bounded audit chunks and drains completion events from the main loop.
Trading impact: none. This closes the main remaining concern from P357: audit visibility should not become a latency bottleneck for live entries.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: start live2 and watch `decision_loop_overrun_count`, `total_deadline_missed`, `total_deadline_expired_backlog`, `entry_stream_ready`, and `top_growth_audit.status` for the first hour.
```

## 2026-05-20 - P357 live2 closed-hour top-growth audit

```text
Current patch status: P357 APPLIED locally / UNKNOWN commit.
Question: can live2 prove what hourly pumps happened during the run even when it made no trades?
Change: live2 now owns a bounded top-growth audit task. It writes closed 1h exchange-candle `top_growth/` artifacts inside the run root: index, capped top rows, and full per-symbol status rows. The task is not a signal source and does not use ticker/session snapshots as a fallback.
Trading impact: none directly. This improves missed-pump visibility and data-quality audit only. It may add low-rate REST load on heartbeat; default is one symbol per heartbeat to avoid moving the latency bottleneck into the decision loop.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: run live2 across one UTC hour close and require `top_growth/top_growth_index.csv`, `top_growth_*.csv`, and `top_growth_status_*.csv` to appear; heartbeat diagnostics should show `top_growth_audit.status=processing/completed` without decision latency degradation.
```

## 2026-05-20 - P356 live2 entry-stream gate and backlog/context diagnostics

```text
Current patch status: P356 APPLIED locally / UNKNOWN commit.
Question: can live2 stop losing entries to global WS flaps, reconnect backlog, and over-strict prior-context id-gap invalidation without sweeping failures under the rug?
Change: runtime market-data readiness now requires selected universe + aggTrade readiness + live decision watermark, not global ticker+aggTrade+mark all at once. Ticker/mark global readiness remains in artifacts; mark is now checked stale-aware per symbol inside the signal dependency contract. Deadline processing is fresh-first and old reconnect/backlog buckets become explicit `deadline_expired_backlog` instead of competing with still-enterable buckets. Prior-context live 5m roll-forward tolerates aggTrade-id gaps as diagnostic, with `total_ws_5m_gap_above_tolerance_tolerated` preserving the evidence.
Trading impact: fewer false global no-entry windows and fewer stale backlog decisions on the hot path. No entry can pass with stale required per-symbol mark/OI/prior context; stale context remains `data_dependency_not_ready`. No fill/stop/TP logic changed.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: restart live2 and require `entry_stream_ready=true` during ticker/mark global flaps when aggTrade is fresh, `total_deadline_missed` near zero for fresh buckets, old reconnect bursts visible as `total_deadline_expired_backlog`, `total_ws_5m_gap_rejected=0`, and above-tolerance gap counts visible rather than silently absent.
```

## 2026-05-20 - P355 live2 session trading percent

```text
Current patch status: P355 APPLIED locally / UNKNOWN commit.
Change: live2 now tracks runtime-gate allowed/blocked seconds per current crypto session metric window and renders the operator header as `Торговля N%`. The window resets on the same metric_start_ms used by session top-growth, so the percentage describes the current session, not whole process uptime.
Validation: `.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_live2\runner.py research_tools\anomaly_live2\status_grid.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
```

## 2026-05-20 - P354 live2 rolling prior-context maintenance

```text
Current patch status: P354 APPLIED locally / UNKNOWN commit.
Question: can live2 keep Ctx stale near zero cheaply from existing WebSockets without masking data holes?
Change: prior 24h context remains REST-bootstrapped from closed 5m OHLCV at startup, then rolls forward from live aggTrade-derived closed 5m candles. Full-window runtime REST repoll is no longer the normal freshness mechanism for symbols with an ok rolling buffer. Minor intra-5m aggTrade id gaps are tolerated up to max(5 ids, 10% observed trades) and recorded; larger gaps mark `ws_gap_exceeds_tolerance` and block context until repair. Symbol-state artifacts include last live 5m context open/close, appended/tolerated/rejected counts, missing id count, and tolerance.
Trading impact: stricter and cheaper context freshness. No signal thresholds, entry guards, fills, stops, TP, or position lifecycle changed. Stale/missing/rejected prior context still becomes `prior_24h_context_not_ready`; the patch changes maintenance, not acceptance.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; `.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py`.
Next validation: restart live2 and require `prior_context.maintenance_mode=startup_rest_bootstrap_plus_live_ws_5m_rolling_append`, rising `total_ws_5m_candles_appended`, low/zero `total_ws_5m_gap_rejected`, and no sustained `Ctx stale` for hot symbols after startup.
```

## 2026-05-20 - P353 live2 prior-context launch wiring and artifact durability

```text
Current patch status: P353 APPLIED locally / UNKNOWN commit.
Question: post-P352 live2 run 20260520_122522 showed deadline misses largely fixed, but prior 24h context remained materially stale/not_seen and some decisions stayed blocked by prior_24h_context_not_ready. Root cause: CLI parser/command defaults still injected the old P337/P351 values (15m stale, 5m cooldown, 4 symbols/cycle), overriding the widened AnomalyLive2Config defaults. A separate audit durability issue was observed: full-rewrite artifacts such as live2_symbol_state.csv could be momentarily truncated while the async writer rewrote them in place.
Change: CLI parser defaults and command fallbacks now match runtime config (20m stale, 10m cooldown, 10 symbols/cycle). The prior-context startup event now labels the real selected-universe active-priority poll scope. Status, diagnostics summary, and symbol-state full rewrites are atomic temp-file replacements with fsync before replace. Writer failures still surface through artifact_writer_status and can disable new entries; no fallback data is introduced.
Trading impact: stricter visibility and better context freshness after restart. Signals still reject/block on stale or missing prior context; this patch makes the intended poller budget actually reach launched live2 and prevents audit snapshots from disappearing during rewrite. No category thresholds, execution guards, fill, stop, TP, or position lifecycle changed.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; `.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: restart live2 on this commit, then require prior_context stale/not_seen counts to trend down after one 20m window, `total_data_dependency_not_ready` from prior_24h_context_not_ready to stop accumulating at the old rate, and artifact_writer_status error/rejected counts to remain zero.
```

## 2026-05-20 - P352 live2 market-watch stability patch

```text
Current patch status: P352 APPLIED locally / UNKNOWN commit.
Question: current live2 run 20260520_112804 is stable at WS/execution level but still has market-watch holes: many `deadline_missed`, prior-context stale counts across the selected universe, and signal-side stale context could still be treated as ok.
Change: live2 closes ended real-trade candles by wall clock inside the decision loop, without synthetic candles or REST/backfill. This removes the dependency on a later trade to make the previous 5s bucket visible to the deadline engine. Prior-context runtime refresh now covers the whole selected universe with active/actionable symbols prioritized and oldest-first fairness, defaulting to 10 symbols/10s, 600s cooldown, and 20m stale. Signal features now use effective stale-aware OI/prior-context statuses while preserving raw status fields in artifacts.
Trading impact: stricter and more timely. Potential entries are less likely to be lost as deadline_missed after a burst goes quiet; stale derivative/prior context can no longer pass category gates as ok. No thresholds, fill, stop, TP, or execution safety are loosened.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: restart live2 on this commit only when ready, then require `total_deadline_missed` share and prior_context stale count to trend down while artifact writer backpressure and prior_context total_errors stay near zero.
```

## 2026-05-20 - P351 live2 event-driven deadline and aggTrade/OI diagnostics

```text
Current patch status: P351 PROPOSED / UNKNOWN commit.
Question: live2 run 20260520_103312 had stable WS/execution infrastructure but frequent decision deadline misses and useless `ok_with_gaps` aggTrade diagnostics; OI startup prewarm existed but runtime OI refresh fairness let alphabetically early symbols recycle before the tail of the universe.
Change: deadline evaluation is now event-driven by aggTrade dirtiness instead of scanning every selected symbol every 50-100ms, and the live decision watermark is cached once per cycle. Fast runtime gates no longer build full per-symbol count summaries every loop; full counts stay on heartbeat/status writes. aggTrade status now separates `ok_active`, `ok_idle_no_trades`, `gap_missing_expected_bucket`, and `stale`. Runtime OI polling rotates by oldest poll time inside priority buckets, so selected-universe refresh cannot starve later symbols. Defaults align OI stale with 5m OI cadence and full-universe refresh, with explicit startup OI prewarm retained before live entries.
Trading impact: latency/audit bugfix only. No signal threshold, fill, stop, TP, or fallback logic is loosened. Missing data still blocks via dependency/status paths.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `launcher.py` is absent in this zip, so the broader hygiene command with launcher cannot be run literally.
Next validation: rerun live2 and require deadline_missed share to collapse, `live_aggtrade_status_counts` to split across ok_active/ok_idle/gap/stale instead of all ok_with_gaps, and OI stale counts to trend down after one refresh cycle.
```

## 2026-05-20 - P349 live2 aggTrade shard stale_ms wiring

```text
Current patch status: P349 PROPOSED / UNKNOWN commit.
Question: fresh live2 startup run 20260520_090142 failed after aggTrade WS startup with all four shards reporting `AttributeError: _Live2AggTradeWsShard object has no attribute stale_ms`.
Change: Live2AggTradeWsSource now passes its configured `aggtrade_stale_ms` into every `_Live2AggTradeWsShard`, and the shard validates/stores it before using it for receive timeouts and stale watchdog checks.
Trading impact: bugfix only. This does not loosen readiness: all aggTrade shards still need fresh live WS payload before live2 can enter the main loop.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; rerun live2 startup and require the previous AttributeError to disappear.
Next validation: inspect the next `aggtrade_ws_startup_failed` event if startup still fails; it should now expose the real Binance/network/payload blocker instead of the local AttributeError.
```

## 2026-05-20 - P342 live2 hard startup/execution safety

```text
Current patch status: P342 PROPOSED / UNKNOWN commit.
Question: after P331-P341, live2 still had a few ambiguous runtime states: it could keep running with failed execution preflight, missing private user-data stream, or too-small auto-universe; Binance listenKey keepalive/close calls sent an unnecessary listenKey parameter despite the USD-M endpoints documenting no request parameters; and a protected position that became exchange-flat outside the TP1 path could be removed from the local registry without proving the old stop was gone.
Change: live2 now treats execution preflight failure, user-data stream startup failure, and auto-universe below minimum as startup failures. The startup event explicitly labels live2 as real-orders-only/no dry-run. Binance USD-M listenKey keepalive/close calls now call their documented no-parameter endpoints. When the exchange position is flat, the supervisor verifies the protected stop is already gone or cancels/verifies it gone before removing the protected position.
Trading impact: stricter and less ambiguous. Live2 either has the mandatory execution/user-data/universe prerequisites or stops. It will not silently keep a flat local state while a reduce-only conditional stop remains visible.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic flat-position orphan-stop smoke.
Next validation: run live2 smoke and require either clean startup with user-data ready + universe >= floor, or immediate explicit startup failure event/reason.
```

## 2026-05-20 - P341 live2 full-TP1 position contract

```text
Current patch status: P341 PROPOSED / UNKNOWN commit.
Question: live2 supervisor still implemented the older TP1 partial-close + BE-stop runner lifecycle, while the current live2 strategy contract needs TP1 to close the whole position and avoid runner remainder complexity.
Change: live2 TP1 close fraction is now exactly 1.0; config validation rejects partial fractions; protected positions default to 100% TP1; supervisor submits a reduce-only full-position TP1 close, requires exchange position flat afterwards, cancels/verifies gone the old initial stop, removes the protected registry row, and emits `position_tp1_full_close_verified`. If the full close does not flatten the exchange position or the old stop cannot be cancelled, live2 emits a strict position integrity error. No BE stop is created after TP1.
Trading impact: simpler and stricter live2 exit lifecycle. TP1 is now terminal for the position. Runner/BE-stop behavior is deliberately removed from live2 default, not kept as optional fallback.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic full-TP1 close smoke.
Next validation: live2 smoke after P331-P341; verify `position_tp1_full_close_verified`, `total_tp1_closes`, `total_final_closes`, old stop cancellation artifacts, and no `position_tp1_filled_be_stop_verified` on the new path.
```

## 2026-05-20 - P339 live2 flow-hold/taker confirmation contract

```text
Current patch status: P339 PROPOSED / UNKNOWN commit.
Question: current live2 categories include flow-hold / next-taker-buy confirmation fields, but P338 correctly exposed them as not ready. Live2 needs a non-lookahead contract for those fields before `runner_flow` can be evaluated honestly.
Change: signal evaluation now computes flow-hold from trailing closed live WS 5s candles at or before the decision candle. It exposes `flow_hold_status`, reason, count, window, quote/trade/taker-buy totals, taker share mean/last/delta, and maps legacy `min_next_taker_buy_quote_share` to `live_confirmed_taker_buy_quote_share`. No future buckets, hot-path IO, REST fallback, or zero substitution are used.
Trading impact: `runner_flow` can now become computable when live aggTrade candles provide enough closed pre-entry flow. Missing baseline/live candles still produce `data_dependency_not_ready`; weak confirmed flow remains a real strategy reject. Mark/OI/24h prior dependencies are unchanged.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic closed-live-flow signal smoke.
Next validation: live2 smoke after P331-P339; inspect `deadline_decision.signal_features.flow_hold_*`, `live_confirmed_taker_buy_quote_share`, and ensure no category uses future candles or startup REST candles as live flow hold.
```

## 2026-05-20 - P338 live2 dependency-aware signal gate

```text
Current patch status: P338 PROPOSED / UNKNOWN commit.
Question: current live2 category evaluation mixed missing data dependencies with strategy rejects and also risked accepting categories while some contract fields were unavailable.
Change: signal evaluation now classifies missing required mark/OI/24h prior/baseline/derived category inputs as `data_dependency_not_ready`, keeps real threshold failures as `rejected_signal_contract`, writes dependency/reject reason arrays into deadline events, adds dependency counters to deadline status, diagnostics summary, symbol state, and the terminal grid. There is no fallback or zero substitution.
Trading impact: stricter and more honest. Live2 can now show that a signal was not decidable because data dependencies were not ready, instead of counting it as a strategy reject. Current categories that require not-yet-final flow-hold/taker-delta handling can remain blocked by dependency status until that data contract is completed.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic missing-dependency signal smoke.
Next validation: live2 smoke after P331-P338; inspect `deadline_decision.verdict=data_dependency_not_ready`, `signal_dependency_reasons`, `live2_status.json.decision_status.total_data_dependency_not_ready`, and `live2_diagnostics_summary.json.decision_funnel.signal_dependency_funnel`.
```

## 2026-05-20 - P337 live2 24h prior context poller

```text
Current patch status: P337 PROPOSED / UNKNOWN commit.
Question: provide the prior fake-pump/spike/whipsaw context required by current live2 categories without using 72h or hot-path fallback.
Change: live2 now starts an active/radar-only prior-context poller that fetches closed 5m OHLCV over an exact 24h window, computes prior spike count, prior fast-fade count, and prior whipsaw legs, stores source/status/coverage fields per SymbolState, exposes context counts in status/grid/diagnostics, and evaluates legacy category `*_72h` fields from explicitly labelled 24h live context. Missing/empty/invalid context remains `prior_24h_context_not_ready`; no zero fallback is introduced.
Trading impact: category acceptance is stricter and more honest for prior-context-required categories. Market-data stream readiness, order placement, fills, stops, TP/BE and execution guards are unchanged. This patch also fixes the P336 OI poller status snapshot copy so OI diagnostics can be read without constructing from its `ready` view field.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic 24h prior-context snapshot/poller smoke.
Next validation: live2 smoke with small explicit universe; require `prior_context_poller_starting`, `prior_context_status_counts`, grid `24h ctx`, and category rejects to move from missing prior context to real `prior_24h_context_not_ready` / count-threshold rejects / accepted when context is ready.
```


## 2026-05-20 - P334 live2 planned WS rotation lifecycle

```text
Current patch status: P334 PROPOSED / UNKNOWN commit.
Question: prevent Binance USD-M market WebSocket sessions from relying on server-side 24h disconnects and make WS lifecycle/audit data explicit.
Change: live2 ticker and aggTrade WS sources now accept `ws_connection_max_age_seconds` (default 84600s = 23h30m), track per-connection start/age/max-age, close/error details, and planned rotation counters. When the configured age is reached, the source closes the WS intentionally and reconnects immediately without consuming exponential backoff. AggTrade shard readiness still requires fresh applied payload. No signal/category/order/fill/stop/TP logic changed.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic short-lifetime rotation smoke.
Next validation: run live2 smoke long enough to see normal payloads, then a short test with `--ws-connection-max-age-seconds 2` and confirm planned rotations increment without reconnect storm or new-entry false readiness.
```
# Anomaly Research State

## 2026-05-20 - P335 live2 markPrice WS context

```text
Current patch status: P335 PROPOSED / UNKNOWN commit.
Question: current default live2 categories need mark-vs-decision basis, while live2 had no markPrice data source.
Change: live2 now runs a routed Binance USD-M `!markPrice@arr@1s` WS source for the selected universe, stores mark/index/funding fields in SymbolState, gates market-data readiness on fresh markPrice payloads, writes mark diagnostics/status counts, and evaluates `mark_close_vs_decision_close_basis` from real mark price in the signal adapter. No mark fallback is introduced.
Trading impact: no order/fill/stop/TP behavior changed. Market-data readiness is stricter: ticker + live aggTrade + markPrice are required. OI and prior 24h context are still missing by design and remain next patches.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic mark-price payload smoke.
Next validation: run a short live2 smoke and confirm `mark_price_ws.ready=true`, `mark_price_status_counts.ok > 0`, and `stream_coverage_ready` only when ticker, aggTrade, and markPrice are all fresh.
```

## 2026-05-20 - P333 live2 transition-only market-data diagnostics

```text
Current patch status: P333 PROPOSED / UNKNOWN commit.
Question: stop live2 from spamming hundreds of thousands of market-data coverage events while preserving post-mortem truth.
Change: live2 now emits `market_data_coverage_transition` only when the semantic market-data state changes. The dedup key excludes rolling clean/degraded windows and reconnect counters, which remain in status/summary artifacts instead of event spam. A new `live2_diagnostics_summary.json` exposes event-type counts, market-data transition counts, runtime-gate transition counts, allowed/blocked gate seconds, reconnect summary, decision funnel, and execution funnel.
Trading impact: none. No strategy thresholds, categories, WS ingestion, order placement, fills, stops, TP/BE, or runtime gates are loosened. This is audit volume control and diagnostic truthfulness only. Prior-context planning remains 24h.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic artifact-writer diagnostics smoke.
Next validation: short live2 smoke after P331-P333; require `live2_events.csv` to contain transitions/heartbeats/decisions rather than per-cycle `market_data_coverage_update` spam, while `live2_diagnostics_summary.json` contains reconnect counters and gate durations.
```

## 2026-05-20 - P332 live2 warmup/live watermark separation

```text
Current patch status: P332 PROPOSED / UNKNOWN commit.
Question: prevent startup REST warmup from masquerading as live flow or creating stale live decisions.
Change: live2 now stores startup REST aggTrade and live WS aggTrade counters separately, labels candle source composition, exposes live/startup counts in status artifacts, and gives the deadline engine a live aggTrade watermark. Closed buckets before the first valid live WS payload are consumed as pre-live buckets without `deadline_decision` events, so warmup history cannot become fake `deadline_missed`.
Trading impact: no strategy thresholds, order placement, fills, stops, or category logic changed. This is market-data truthfulness and decision-gating only.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic warmup/live watermark smoke.
Next validation: short live2 smoke after P331+P332; require `candle_coverage_counts.startup_warmup_only` during REST warmup, transition to `live_ready` only after WS payloads, and no startup burst of `deadline_missed`.
```

## 2026-05-19 - P324 live2 Telegram operator safety messages

```text
Status: PROPOSED / UNKNOWN commit.
Live2 now has its own Telegram operator notifier. The command requires the same TELEGRAM_* env contract as live1, sends startup/status degradation messages to the events channel, sends verified entry / TP1-BE / final-close messages to positions, and sends strict critical alerts for position/order integrity errors. Symbol names are rendered as Coinglass links. Telegram remains notification-only; live2_events.csv/live2_status.json remain source of truth.
Next: smoke run live2 with tiny universe and verify telegram_message_sent/telegram_*_failed events plus actual rendered messages before adding restart/open-order reconciliation.
```

## 2026-05-19 - P320 live2 fast decision loop gates

```text
Status: PROPOSED / UNKNOWN commit.
Live2 deadline decisions now run on a dedicated fast loop (`decision_loop_interval_seconds`, default 0.1s) instead of waiting for the 5s heartbeat. Heartbeat/status/symbol-state writes remain periodic through the async artifact writer. Runtime gates now expose stream coverage readiness, decision-latency degradation/recovery, artifact-writer readiness, exchange boundary readiness, and the exact no-new-entries reason.
Next: implement verified real order lifecycle only after live2 proves low deadline misses and stable runtime gates under a short smoke run.
```

## 2026-05-19 - P319 live2 bounded async artifact writer

```text
Status: PROPOSED / UNKNOWN commit.
Live2 audit writes now use a bounded background writer queue. Deadline/signal cycles enqueue audit jobs instead of doing blocking CSV/JSON disk writes on the hot path. Writer queue health, rejected enqueue count, and IO errors are exposed through live2_status/heartbeat data; artifact_writer_ready becomes false if the queue fills or the writer errors, which keeps new entries disabled rather than trading without safe audit.
Next: runtime hardening for reconnect/coverage/latency degradation gates before enabling real order placement.
```

Compact project memory. Detailed rules live in Project Instructions.

## 2026-05-20 - P336 live2 active/radar OI poller

```text
Current patch status: P336 PROPOSED / UNKNOWN commit.
Question: provide real open-interest context for live2 categories without polling the whole universe or substituting missing OI with zero.
Change: live2 now starts an active/radar-only 5m open-interest poller after universe/WS startup. It polls only watching/actionable/in-position or recently live-flow symbols, computes real 3x5m OI change from exchange OI history, stores OI fields/source/status per SymbolState, exposes status counts/grid/diagnostics, and lets `runner_oi_confirmed` evaluate `min_oi_change_pct_3x5m` from those fields. Missing/empty/invalid OI remains explicit `oi_context_not_ready` or source status; no silent fallback is introduced.
Trading impact: no thresholds, prior-24h context, entry guards, order placement, fills, stops, TP/BE, or runtime market-data readiness are loosened. OI is a category dependency, not a global stream-coverage gate in this patch.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic open-interest snapshot/poller smoke.
Next validation: live2 smoke with small explicit universe; require `open_interest_poller_starting`, OI status counts in heartbeat/status/grid, and OI-required category rejects to move from unavailable-generation reason to real `oi_context_not_ready` / `oi_change_3x5m_below_category_min` / accepted when data is ready. Prior-context planning remains 24h.
```

## 2026-05-20 - P331 live2 aggTrade market routed WS readiness

```text
Status: PROPOSED / UNKNOWN commit.
Live2 aggTrade combined streams now use the Binance USD-M Futures routed `/market/stream?streams=` endpoint. Shards are not ready on TCP connect alone: readiness requires a fresh applied aggTrade payload. Reconnect backoff is reset only after the first valid payload of the current connection, and endpoint URL length / close / exception / pre-first-payload failure diagnostics are exposed in live2_status and the terminal grid. This changes only live2 market-data infrastructure, not strategy filters or order logic. Prior-context planning should use 24h, not 72h.
Next: apply P331, run a 2-3 minute live2 smoke without changing strategy parameters, and require aggTrade rows_applied > 0, shards_connected == shards_total, no pre-first-payload reconnect storm, and new_entries_allowed staying false until real payload readiness.
```

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P130-P176 present in uploaded ZIP / UNKNOWN commit; P177/P178/P179/P180 applied locally by user / UNKNOWN commit; P181/P184/P185 present in uploaded ZIP / UNKNOWN commit; P186 proposed; P189/P190/P192/P205/P206/P207/P208/P209 applied/proposed status UNKNOWN from prior memory; P213-P217 applied locally in uploaded ZIP / UNKNOWN commit; P218 proposed; P219/P220/P221 applied locally / UNKNOWN commit; P222 proposed; P223/P224/P225/P226/P227/P228/P229 applied locally by user / UNKNOWN commit; P230 proposed
Last active patch: P336 proposed live2 active-symbol OI poller
Updated: 2026-05-20
```

## 2026-05-19 - P315 live2 deadline verdict engine

```text
Current patch status: P315 PROPOSED / UNKNOWN commit.
Question: add the next live2 layer after ticker-selected aggTrade universe, prioritizing speed/reliability and proving candidates cannot disappear into a queue.
Change: add `Live2DeadlineEngine`, which reads already-built in-memory 5s candle rings, treats threshold-crossing closed buckets as diagnostic actionable buckets, and gives each processed actionable bucket an explicit verdict before/after the configured deadline. Generation 0 still has no real SignalEngine, so successful on-time actionable buckets end as `rejected_signal_engine_todo`; degraded buckets become `data_not_ready`; late buckets become `deadline_missed`. Decision counters, verdict reasons, and latency are written to `live2_status.json`, `live2_events.csv`, and `live2_symbol_state.csv`.
Trading impact: no real orders, signal thresholds/categories, execution guards, fills, stops, TP, BE, or live1 behavior are changed. New entries remain disabled because SignalEngine/ExecutionEngine are still TODO.
Validation: P311->P314 were applied first, then P315 applied on top; `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic deadline-cycle smoke confirmed one actionable 5s bucket gets a bounded-time `rejected_signal_engine_todo` verdict and state counters update.
Next validation: run `.venv\Scripts\python.exe main.py run-anomaly-live2` for 60-120 seconds and check `deadline_decision` events, `decision_status.last_cycle`, no candidate-drop fields, and no hot REST/backfill fields.
```

## 2026-05-19 - P314 live2 ticker-selected startup universe

```text
Current patch status: P314 PROPOSED / UNKNOWN commit.
Question: remove the manual `--symbols` dependency from live2 market-data startup without adding REST discovery or hot-path fallback.
Change: `run-anomaly-live2` now starts all-ticker WS first, selects a startup universe from already-received ticker state when explicit symbols are absent, marks `universe_selected/rank/reason` in `live2_symbol_state.csv`, and then starts aggTrade shards for the selected symbols. Default auto universe is top USDT futures by 24h quote volume/trade count, capped at 240 symbols with a 300k USDT minimum 24h quote-volume. Explicit symbols still bypass liquidity pruning.
Trading impact: no signal thresholds, categories, order placement, fills, stops, TP, BE, or PnL logic changed. New entries remain disabled because SignalEngine/ExecutionEngine are still TODO. This patch only improves live2 market-data startup speed/reliability and removes manual-symbol-only aggTrade coverage.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic selector smoke confirmed top-liquidity selection and symbol-state universe marking.
Next validation: apply P314 after P313 and run `.venv\Scripts\python.exe main.py run-anomaly-live2`; check `universe_selected`, aggTrade shard count, `live2_status.json.market_data_status.universe`, and no hot REST fields.
```

## 2026-05-19 - P310 prior fake-pump quarantine

```text
Current patch status: P310 APPLIED locally / UNKNOWN commit.
Question: prevent symbols that already exceeded the 24h prior fake-pump / fast-fade threshold from entering warm/radar hot lanes and creating latency before category rejection.
Change: after startup symbol-context snapshots are ready, live pre-populates a scheduler quarantine for symbols with prior_fast_fade_count_24h > 2. Before each ticker-radar promotion cycle the quarantine is refreshed from current snapshots and checked before warm/radar enqueue. Quarantine expiry is not a fixed TTL: it is computed from the excess fast-fade timestamps, releasing when enough events age out of the 24h lookback (`oldest_excess_fast_fade_ts + 24h + 1ms`). Quarantined symbols remain in ticker/top-growth universe and are visible through artifacts.
Trading impact: no signal thresholds, category math, executable-entry guards, order placement, fills, stops, TP, BE, or PnL logic changed. This can reduce hot-lane opportunities on symbols with repeated fake fades; it is intentional scheduler quarantine, not permanent universe removal.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: next live smoke should show `prior_fake_pump_quarantine_started/refresh`, `candidate_quarantined_prior_fake_pumps`, lower warm/radar queue pressure, and no disappearance from top-growth/session ticker visibility.
```

## 2026-05-19 - P309 strict warm cap and class latency

```text
Current patch status: P309 APPLIED locally / UNKNOWN commit.
Question: before the next live run, strengthen the post-startup delivery path without flags/fallbacks and make the <=5s latency claim directly auditable.
Change: warm backlog pressure cap is reduced from 12 to 8 and the pressure keep floor from 8 to 6; warm waiting selection is score-first after immediate-danger priority. Live now writes per-class candidate latency fields for active, immediate_danger, ticker_radar, and warm_watch into symbol_batch_selected/live_cycle_summary. signal_symbol_scan_summary now includes scan_origin, origin score, immediate-danger flag, first_seen/promoted timestamps, first_seen_to_scan_ms, and promote_to_scan_ms.
Trading impact: no signal thresholds, categories, executable-entry guards, order placement, fill, stop, TP, BE, or PnL logic changed. This only tightens scheduler backlog and improves latency auditability.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: next live smoke should show warm_watch_symbol_count lower, class latency fields populated, and immediate_danger/ticker_radar first_seen/promote-to-scan distributions directly measurable from signal_symbol_scan_summary.
```

## 2026-05-19 - P308 hot-waiting priority prefetch

```text
Current patch status: P308 APPLIED locally / UNKNOWN commit.
Question: post-startup live latency review of 20260519_112206 showed active and immediate-danger scans are fast, but hot waiting candidates often lack prepared subminute aggTrade coverage and fall back to REST during precise scan.
Change: after the critical scan/order path, live may prefetch due subminute aggTrade gap debt for up to 2 top waiting radar/warm symbols that are immediate-danger flow or score >= 8.0, even when the queue is not idle or latency SLA is already breached. Signal/order/position work still blocks this optional prefetch. Prefetch now ignores tiny open-tail gaps <=250ms instead of making a REST call for them.
Trading impact: no signal thresholds, categories, executable-entry guards, order placement, fill, stop, TP, BE, or PnL logic changed. This is scheduler/data-readiness only.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: short live smoke; require `hot_waiting_priority_prefetch_cycle` events under queue pressure, lower `aggtrade_rest_gap_prefetch` inside hot scans, `tail_gap_ignored` for tiny open-tail gaps, and first_seen/promote->precise scan p95 closer to <=5s.
```

## 2026-05-19 - P307 live heartbeat quality marks

```text
Current patch status: P307 PROPOSED / UNKNOWN commit.
Question: make the live operator grid show whether rapidly changing connection/latency/pulse/queue values are good, warning, or bad without changing live logic.
Change: heartbeat values now include compact quality marks: `✓` good, `!` warning, `×` bad, `?` unavailable/unknown. Marks are added to stability, pulse, data source, guard status, rolling network windows, latency windows, queue, q5p95, and cycle p95.
Trading impact: none. This is display-only; no scheduler, candidate, scan, guard, order, fill, stop, Telegram, or artifact semantics are changed.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: live smoke; confirm the heartbeat remains compact and marks match operator thresholds during WS/gapREST changes.
```

## 2026-05-19 - P304 immediate danger-flow precise lane

```text
Current patch status: P304 PROPOSED / UNKNOWN commit.
Question: cold symbols with extreme ticker/flow spikes wait in warm/radar queues, sometimes requiring a second observation or getting deferred/dropped under latency pressure.
Change: extreme real-flow ticker candidates now bypass ordinary warm-watch observation/defer/drop policy: first observation promotes them to radar with `danger_flow_immediate_promoted`, up to two such symbols get reserved precise slots beyond the normal adaptive radar cap, and queue pressure refuses to drop them before first precise scan. The precise scan uses `precise_immediate_danger_flow` scan_mode for audit.
Trading impact: no category thresholds, entry guard, drift/RR/stale guard, order placement, fill, stop, TP, or PnL logic changes. This only changes how fast an already-detected extreme-flow candidate reaches the existing precise signal path.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: short live smoke; require `danger_flow_immediate_promoted` -> `symbol_batch_selected` scan mode `precise_immediate_danger_flow` -> `signal_symbol_scan_summary` latency under 1-2s for fresh extreme spikes, and no pressure drop before first scan.
```

## 2026-05-19 - P301 live hot-path idle-only optional work

```text
Current patch status: P301 PROPOSED / UNKNOWN commit.
Question: reduce live latency between suspicious anomaly detection and actionable precise decision without changing trading filters or order semantics.
Change: critical live cycle now selects/scans/opens first. Bounded hot-waiting aggTrade prefetch is no longer executed inside batch selection; it runs only after critical scan/open and only when the loop is idle enough. Periodic orphan-order reconcile is deferred whenever positions/opening symbols/active symbols/radar/warm candidates or latency SLA pressure are present. Precise cold coverage is hard-gated whenever active/radar/warm candidates exist. Prior fast-fade/prior-spike counting now uses exact bisect over sorted snapshot timestamps instead of repeatedly scanning the whole timestamp list.
Trading impact: no category thresholds, drift/RR/stale guards, fill handling, stop/TP placement, or TP/SL math changed. This is scheduler/optional-work latency hygiene only.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py` passed. The broader project standard command with `launcher.py` cannot run in this uploaded ZIP because `launcher.py` is absent.
Next validation: short live smoke; require `batch_select_seconds p95 < 0.25s`, `order_reconcile_status=deferred_hot_path` when queues are non-empty, `hot_waiting_prefetch_policy` not blocking due hot scans, and `inactive_cold_coverage_gate_reason=hot_candidate_queue_not_empty` during active radar/warm pressure.
```

## 2026-05-18 - P300 live potential-anomaly latency grid

```text
Current patch status: P300 PROPOSED / UNKNOWN commit.
Question: make the live operator grid show how late potential anomaly candidates are being processed.
Change: live heartbeat `Контроль` now has a second row with `Задержка p95`, `max`, and `Очередь`, using existing latency SLA due-scan p95/max samples and the current radar+warm queue count. `live_cycle_summary` also writes explicit `potential_anomaly_latency_p95_seconds`, `potential_anomaly_latency_max_seconds`, `potential_anomaly_latency_samples`, and `potential_anomaly_queue_count` aliases so the grid is auditable in CSV.
Trading impact: none; scheduler, candidate TTL/drop policy, drift/RR/stale guards, order path, fills, stops, and Telegram behavior are unchanged.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; inline heartbeat smoke confirmed the new latency row renders.
Next validation: run a short live smoke and compare terminal `Задержка p95/max/Очередь` with `live_cycle_summary.potential_anomaly_latency_*` and existing `latency_sla_*` fields.
```

## 2026-05-18 - P299 rejected market-entry audit fields

```text
Current patch status: P299 APPLIED locally / UNKNOWN commit.
Question: make backtest artifacts preserve the actual delayed market-entry price/timestamp and computed drift/RR for execution-guard rejections.
Change: `_resolve_signal_entry` now returns a small reject-audit payload for delayed market-entry guards and `simulate_long_signal` writes it into skipped rows. The patch does not change signal selection, entry thresholds, fills, stops, PnL, or latency behavior.
New skipped-row fields: rejected_market_entry_timestamp_ms, rejected_market_entry_timestamp_utc, rejected_market_entry_price, market_entry_drift_pct, market_entry_abs_drift_pct, max_market_entry_drift_pct, market_entry_rr_after_latency, min_market_rr_to_signal_tp1, signal_tp1_price, actual_market_risk_at_signal_stop.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic `_resolve_signal_entry` market_entry_price_drift smoke confirmed rejected price/timestamp/drift audit fields.
Next validation: rerun anomaly lab with latency=true and drift 0.004; compare saved skipped rows for drift bands 0.003-0.004 before treating the looser guard as beneficial.
```

## 2026-05-18 - P298 executable entry drift guard 0.4%

```text
Current patch status: P298 PROPOSED / UNKNOWN commit.
Question: raise the live executable entry drift guard from 0.3% to 0.4%.
Change: introduce DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT = 0.004 and use it for live max_entry_price_drift_pct plus market/latency backtest max_market_entry_drift_pct. This keeps the execution model comparable instead of loosening live only.
Queue/TTL note: active symbols expire by active_symbol_ttl_ms=60s, ticker radar watches by ticker_radar_watch_ttl_ms=120s, warm-watch entries by warm_watch_ttl_ms=10m, and pressure control can drop low-ranked/stale radar/warm candidates with explicit candidate_dropped_latency_pressure or candidate_expired_backlog_stale artifacts.
Validation: compileall passed for data/exchanges, research_tools, cli, constants.py, main.py; parser smoke shows live/lab drift defaults = 0.004. Direct cli.commands import was not used as validation because this sandbox lacks pyarrow.
Next validation: short live smoke; compare reject_entry_price_drift count and discrete_signal_snapshot_entry_missed outcomes against previous 0.003 baseline.
```

## 2026-05-18 - P297 noticed-symbol decision speed patch

```text
Current patch status: P297 APPLIED locally / UNKNOWN commit.
Question: implement the decision-speed plan from live run 20260518_090759 for coins noticed by the bot, without changing drift guard semantics.
Implemented fixed code policy with no new CLI flags: latency SLA threshold lowered from 15s to 12s; breached/pressure adaptive radar slots raised to 3; candidate pressure now keeps a smaller score-ranked queue (top keep 8, radar cap 18, warm cap 12); top-score warm symbols (score >= 8.0) can promote to the radar hot lane even while optional work is SLA-gated, with latency_sla_hot_lane_override artifacts.
Added bounded hot waiting prefetch: after batch selection, up to 3 waiting radar/warm symbols prefetch due subminute entry gap debt and emit hot_waiting_prefetch_cycle plus aggtrade_rest_gap_prefetch with hot_waiting_* reason/scan_mode. This does not fabricate coverage; pending or over-budget gaps remain explicit.
Active/opening symbols still have first priority and are never dropped by precise budget. Prescan stale decisions now emit reject_stale_decision_latency, separate from execution-guard reject_stale_signal.
Validation: compileall passed for research_tools/anomaly_micro_live.py. Broader tests pending in this turn.
Next validation: short live smoke; inspect symbol_batch_selected hot_waiting_prefetch_count, latency_sla_status, candidate_queue_* totals, radar->precise latency, reject_stale_decision_latency, and no hidden fallback in aggtrade_rest_gap_prefetch coverage_pending/backfilled rows.
```

## 2026-05-18 - P294 live startup catch-up and latency backtest grid

```text
Current patch status: P294 APPLIED locally / UNKNOWN commit.
Question: implement the safe speedups and add a backtest mode that can model live execution delay using 1s cache.
Live cache speedups:
1) startup/reprepare symbol-context backfill remains mandatory, but after the initial pass it now performs bounded catch-up passes to a target no fresher than max(15m, effective snapshot freshness). This closes the gap that can accumulate while the initial all-symbol pass is running, without skipping any symbols/TFs and without an infinite loop.
2) all live OHLCV cache flushes now use delta parquet writes, not only symbol_context_* flushes. ParquetStorage reads already merge base+delta with timestamp dedupe, so this avoids repeated full-file rewrites in normal live cycles.
Backtest latency:
`run-anomaly-lab --latency true` keeps the existing market entry candle model, then internally runs a 10s 1s-cache execution delay plus the hidden 0/7/10/15s latency grid. The 10s grid point reuses the primary latency run instead of simulating twice. The grid writes `anomaly_latency_grid_summary.csv` so latency sensitivity is visible before changing live guards.
If 1s cache is missing for a latency symbol/window, the backtest now backfills the needed 1s window from Binance futures aggTrades during simulation and persists it as delta parquet. Missing/failed backfill still remains an execution skip; no synthetic fills are fabricated.
The run now writes `anomaly_timing_summary.csv` and prints final market metrics plus per-stage timings.
Validation: compileall passed for live/backtest/CLI; run-anomaly-lab --help shows only `--latency`; synthetic latency smoke entered at next-bar-open + 10s on 1s data.
Next validation: run a targeted live-window anomaly-lab with `--latency true` and compare `anomaly_latency_grid_summary.csv` closed_trades/avg_net_return/skip_reason:market_entry_price_drift across hidden delay values.
```

## 2026-05-18 - P293 live context cache delta writes

```text
Current patch status: P293 APPLIED locally / UNKNOWN commit.
Question: why does live startup data collection take tens of minutes, and can it be fixed without skipping any required context/readiness work?
Latest live artifacts show startup context backfill is the bottleneck: 533 symbols x 2 context TF = 1066 OHLCV windows, then 1599 snapshots. Runs 20260518_051811/070057/074930 spent about 30-31 minutes before ticker radar/live cycles. Ticker radar itself was sub-second after context readiness.
The safe bottleneck is cache writing, not the context requirement: live tail updates were using ParquetStorage.save_incremental(), which reads the whole symbol/TF parquet, merges, sorts, validates, and rewrites the full file even when only a small tail was fetched. This repeats for 1000+ symbol/TF files.
P293 adds a ParquetStorage delta layer. load/load_window transparently merge base data.parquet plus delta/*.parquet with timestamp dedupe, while live symbol_context_* cache flushes write small delta parquet files instead of rewriting base parquet. Offline canonical fetchers still use full save_incremental().
Startup context readiness remains mandatory and unchanged. The patch changes cache storage mechanics and adds startup phase timings to symbol_context_startup_backfill_completed.
Expected next validation: restart live and compare symbol_context_startup_backfill_completed elapsed_seconds/fetch_phase_seconds/flush_seconds/snapshot_seconds/cache_write_storage_mode=delta against prior ~1800s baseline.
```

## 2026-05-18 - P292 live heartbeat/session-top label cleanup

```text
Current patch status: P292 APPLIED locally / UNKNOWN commit.
Operator-only cleanup after P290/P291: the heartbeat label is back to "Стабильность" while the value remains session-scoped, and the visible session-top label no longer prints "с начала сессии".
The session metric window logic is unchanged; only the operator/artifact label string is hidden to keep the live log compact.
Validation: compileall passed for research_tools/anomaly_micro_live.py; inline heartbeat smoke confirmed "Стабильность" is present and "WS сессия"/"с начала сессии" are absent.
Next validation: restart live and confirm the heartbeat shows "Стабильность" and session-top rows do not append "с начала сессии".
```

## 2026-05-18 - P291 live session metric NameError fix

```text
Current patch status: P291 APPLIED locally / UNKNOWN commit.
User live command failed during startup ticker radar validation at .output/results/live_anomaly_runs/20260518_070057 with NameError: _HOUR_MS is not defined.
Root cause: P290 used _HOUR_MS in anomaly_micro_live.py, while this module defines HOUR_MS. The failure happened before live cycles started, inside session top artifact snapshot.
Fix: replace _HOUR_MS with HOUR_MS in session top elapsed-hour and WS session sample pruning calculations.
Operator output cleanup: runner.shutdown now finishes the live status line, and run-anomaly-live calls shutdown before LiveStartupError/generic exception propagation, so errors should print on a fresh line instead of appending to a status message.
Validation: compileall passed for research_tools/anomaly_micro_live.py and cli/commands.py; anomaly continuation lab tests passed 13/13; live order lifecycle unittest passed 5/5; direct LiveSessionTopTracker snapshot smoke passed.
Next validation: restart run-anomaly-live and confirm startup passes ticker radar validation and any future exception appears on a separate terminal line.
```

## 2026-05-18 - P290 live data-readiness retry and session metrics

```text
Current patch status: P290 APPLIED locally / UNKNOWN commit.
Artifact reviewed: .output/results/live_anomaly_runs/20260518_051811.
The latest live run had 0 category_selected/position_opened, but ws_aggtrade_frame_read was fully covered: 2872 reads, connection_status=connected, missing_range_count=0, backfilled_rows=0, ws_rows=result_rows=529476. The 99.9% stability plus "Кеш REST" was mostly a label/window issue: REST there meant OHLCV cache fill, not degraded aggTrade flow.
Real skip risk found: empty setup/entry OHLCV returned normal no_signal and could mark the LTF decision closed before the cache filled. P290 makes this a retryable dependency with explicit setup_empty/entry_empty/retry_policy fields, so it does not consume the candle until data is available or the signal goes stale.
Stability is now session-scoped instead of cumulative process-since-start. live_cycle_summary keeps ws_health_pct/ws_health_observed_seconds/ws_health_healthy_seconds for the current session metric window and adds cumulative fields separately.
Session top growth no longer uses rolling 6h. It uses the same session metric baseline: Asia+Europe from Asia start, Europe from Asia+Europe start, Europe+America from Europe start, America from Europe+America start, America+Asia from America start.
Operator label cleanup: heartbeat stability value is session-scoped; OHLCV cache statuses are "OHLCV REST"/"OHLCV gap" to avoid confusing REST cache fills with flow degradation.
Validation: compileall passed for research_tools/anomaly_micro_live.py. Broader tests pending in this turn.
Next validation: next live run should show ws_health_scope=session_metric_window and no normal no_signal closeout for signal_scan_empty_ohlcv rows.
```

## 2026-05-18 - P289 live run 20260517_200159 and context label/parity cleanup

```text
Current patch status: P289 APPLIED locally / UNKNOWN commit.
Artifact reviewed: .output/results/live_anomaly_runs/20260517_200159.
The run was operationally alive from 2026-05-17 20:02Z to 2026-05-18 04:38Z. No live_internal_error/data-integrity/order error event was found; startup preflight and position cleanup were OK; live_positions.csv stayed empty because no order path was reached.
Funnel: 10425 signal_symbol_scan_summary rows, 23082 evaluated TFs, signal_count=0, category_selected=0, order_attempt=0, position_opened=0. Delayed replay processed 758 all-category-rejected cases and strict_replay_would_enter=false for all 758.
Main pre-signal rejects: reject_weak_start_flow=13043, reject_setup_too_early=8567, reject_insufficient_real_entry_buckets=66, reject_entry_below_initial_stop=170, reject_invalid_tp1_pump_leg_bottom_risk=23.
Main category bottleneck after candidates reached categories: 2274 category_rejected rows, dominated by reject_mark_basis_below_min=2062 across runner_oi_confirmed/runner_flow/runner_balanced. No category_selected happened.
Data quality improved versus the prior live run: startup context readiness was 100% after backfill and after safe live reprepare, ws_aggtrade_frame_read was active, and top-growth visibility could attribute missed movers to concrete precise/category rejects.
Residual issue: 269 retryable dependency blocks still expired on reject_prior_fast_fade_filter_unavailable, despite 320 symbol_context_snapshot_tail_refreshed events. Treat this as context freshness/contract observability to monitor, not proof of an executable missed trade.
Logical cleanup: live prior context already uses SYMBOL_CONTEXT_PRIOR_LOOKBACK_HOURS=24. P289 removes hardcoded 72ч labels/history_days from live startup/reprepare logs/artifacts and makes backtest legacy *_72h category fields use the same 24h effective window for live/backtest parity. Field names remain legacy-compatible.
Validation: compileall passed for live/backtest/CLI/tests; anomaly continuation lab tests passed 13/13; live order lifecycle unittest passed 5/5 after updating full-TP1 expectations.
Next validation: the next short live should show context 24h in terminal/Telegram/status artifacts and symbol_context_startup_backfill_* prior_context_lookback_hours=24.
```

## 2026-05-18 - targeted backtest correction for live run 20260517_200159

```text
Correction to prior wording: delayed replay showing strict_replay_would_enter=false does not mean an offline backtest over the period will find nothing.
Targeted anomaly-lab over 37 live-relevant symbols for 2d ending 2026-05-18 04:38Z found 82 signals total and 60 signals inside/post-startup live window.
Inside/post-startup live window: 60 trades rows, 16 closed non-overlap trades, all discovery family, 0 live_priority trades. Closed discovery results were weak: summed trade returns -11.6%, avg -0.73%, WR 43.8%, TP1 hit 50%.
Strict parity report for the live window: live_priority_pass=false for all 841 rows, category_parity_class was discovery_only=60 and not_in_pre_context_universe=781; strict_live_replay_enter=false for all rows. The 60 discovery-only rows had context_parity_status=oi_context_stale_asof, so they are not clean live-priority evidence.
Live saw many of the same timestamps but rejected them by the live category contract, mostly reject_mark_basis_below_min or retryable prior-fast-fade dependency timeout. This explains why live did not open although offline discovery backtest had hindsight trades.
Conclusion: do not say "backtest shows nothing" for this period. Say "targeted backtest shows only weak discovery/hindsight trades, not current live-priority executable entries."
Next validation if zero live trades persist: run a wider full-universe live-window lab or targeted strict live-priority ablation to test whether mark-basis/context dependency gates are too restrictive.
```

## 2026-05-17 - P288 live exit rule update

```text
Current patch status: P288 APPLIED locally / UNKNOWN commit.
TP1 is now a full-position target at entry + 0.75 * (entry - pump_leg_bottom). In backtest, pump_leg_bottom is decision_box_low. In live, pump_leg_bottom is the selected entry segment low.
Initial SL remains max(pump_leg_bottom - stop_buffer_range_fraction * impulse_range, EMA20). This means TP1 risk basis is intentionally different from SL risk basis.
Live actual-fill path recomputes TP1 from the actual exchange fill price and signal pump_leg_bottom, places the TP1 limit for 100% of the filled amount, and records tp1_target_basis/tp1_basis_risk/tp1_r/tp1_fraction in artifacts.
Backtest defaults are tp1_r=0.75, tp1_fraction=1.0, min_market_rr_to_signal_tp1=0.70. Live constants are LIVE_TP1_R=0.75 and LIVE_TP1_FRACTION=1.0.
Validation: compileall passed for anomaly backtest/live and CLI; defaults smoke confirmed backtest/live TP1 basis/r/fraction.
Next validation: run a short live with tiny notional and require category_selected/position_opened artifacts to show tp1_target_basis=pump_leg_bottom, tp1_r=0.75, tp1_fraction=1.0, and tp1_order_amount equal to filled position amount.
```

## 2026-05-17 - P287 exit portfolio replay tool

```text
Current patch status: P287 APPLIED locally / UNKNOWN commit.
Added research_tools/anomaly_exit_portfolio_replay.py, a standalone artifact-level no-overlap portfolio replay for saved anomaly_lab trades.
The tool reads anomaly_trades.csv and anomaly_context_parity_report.csv from each TF run, filters family/context parity, enforces max concurrent positions and same-symbol overlap policy, and writes summary/trades/reviewed/daily/top-tail CSVs.
Supported exit models include current, full_tp1, and generic tp1_<pct>_rest_<R>r with optional _be fallback.
Validation run on latest anomaly_lab with live_priority, context_parity=ok, max_positions=1, models current,tp1_25_rest_1p5r,tp1_50_rest_1p5r,full_tp1 wrote .output/results/anomaly_lab/portfolio_exit_replay.
Initial strict no-overlap readout: 105 accepted trades; current sum +187.8%, avg +1.79%; full_tp1 sum +190.7%, avg +1.82%, lower top15 share 60.3% versus current 71.3%, but slightly worse max daily drawdown.
Next validation: compare max_positions=1/2/3 and review skipped/reviewed rows before promoting an exit rule.
```

## 2026-05-17 - P286 missed-pump/category parity diagnostics

```text
Current patch status: P286 APPLIED locally / UNKNOWN commit.
Top-growth missed-pump visibility now records first/last precise-scan reject event, first/last reject reason, and top precise reject reason counts before falling back to generic precise_scanned_no_actionable_signal_or_untracked_reject.
This should make live artifacts say whether a missed pump was rejected by weak start flow, setup too early, dependency timeout, empty/fetch data issue, or another precise-stage reject before category/execution.
Backtest context parity report now includes category_parity_class, live_priority_pass, discovery_only, live_priority_reject_reason, strict_live_replay_enter, and strict_live_replay_enter_reason.
This is diagnostics/parity only; it does not loosen category thresholds or change live order execution.
Next validation: rerun a short live/top-growth audit and one anomaly-lab export, then group missed_pump_visibility.csv by not_scanned_reason/top_precise_reject_reasons and anomaly_context_parity_report.csv by category_parity_class/live_priority_reject_reason.
```

## 2026-05-17 - P285 active context suffix refresh

```text
Current patch status: P285 APPLIED locally / UNKNOWN commit.
For active/open/retryable symbols, a stale prior-fake-pump context tail no longer immediately blocks category evaluation. Live now performs a targeted levels-TF OHLCV suffix refresh through the cache-backed fetch path, recomputes that symbol's context snapshot, and re-checks the category once.
The prior fake-pump lookback used by live symbol_context_snapshot / _live_prior_fast_fade_72h is reduced from 72h to 24h. Schema names still contain 72h for compatibility, but live emits prior_context_lookback_hours=24 in the result payload.
The shared live category contract is bumped to shared_pump_category_contract_v1_live_overlay_v8 and allows max_prior_fast_fade_count_72h=2 for runner_oi_confirmed, runner_flow, runner_reclaim, runner_balanced. This means up to 2 prior fast-fade/fake-pump events in the 24h lookback are accepted.
30d broad candidate check on latest .output/results/anomaly_lab: old prior_fast_fade_72h>1 affected 23.2% of 1m/5s candidates, 15.3% of 1m/15s, 20.1% of 5m/30s. New 24h>2 would affect 16.2%, 9.2%, 12.4%. The new policy releases about 30.3%, 40.0%, 38.4% of old fast-fade rejects respectively.
Next validation: run a short live and require symbol_context_snapshot_tail_refreshed events for active/retryable stale-tail cases, fewer candidate_expired_dependency_timeout rows, and no increase in exchange/order errors.
```

## 2026-05-17 - live run 20260517_122732 artifact review

```text
Current artifact review: .output/results/live_anomaly_runs/20260517_122732/1.zip.
Current patch status: P284 APPLIED locally / UNKNOWN commit.
Live process health looks operationally OK: no live_internal_error/live_data_integrity_error, account preflight OK, startup position cleanup found zero positions, live_positions.csv is empty because no order attempt happened.
The bot did not open trades because no signal reached category_selected: 3465 signal_symbol_scan_summary rows, 7517 evaluated TFs, signal_count=0, category_selected=0, order_attempt_total=0.
Main pre-signal rejects: reject_weak_start_flow=4251 and reject_setup_too_early=2822. These are mostly early/forming setup checks and weak real trade-count expansion versus the strict min_quote/min_trade pace=4 contract.
Real data path is mostly healthy: ws_aggtrade_frame_read covered=7516/7517, cache reads filled/hit dominate, and top-growth audit saw EDEN before/during the pump with radar/warm/precise scan active.
Real issue: symbol_context_snapshot rolling updates are starved by latency SLA. Startup backfill took 2347s and was partial; after live start only 118 partial_budget updates happened while 759 snapshot cycles were skipped. This produced 344 retryable category dependency blocks and 338 dependency timeouts from symbol_context_snapshot_tail_stale.
Do not loosen category thresholds first. Fix/validate context freshness throughput and delayed replay visibility first, then decide whether the strict start-flow contract is rejecting real EDEN-like continuation too early.
P284 lets retryable/active/open symbols get a tiny priority context snapshot refresh even when optional snapshot work is gated by latency SLA. It does not loosen anomaly category thresholds and does not make ticker/warm radar tradable by itself.
Next validation: rerun live with delayed replay enabled, then inspect symbol_context_snapshot_updated latency_sla_priority_override, category_selected, signal_scan_retryable_dependency_blocked, candidate_expired_dependency_timeout, and EDEN/top-growth visibility before touching trading filters.
```

## 2026-05-17 - targeted backtest check for live window

```text
Targeted cache refill: EDEN/Q/PTB OHLCV 1m/5m and derivatives context updated cleanly to 2026-05-17 15:36Z. Historical aggTrade REST backfill over a 24h window failed for EDEN and Q with Binance 400 Internal error: 1, while PTB succeeded. Existing/materialized subminute cache was then overwritten for 5s/15s/30s.
Live-like anomaly-lab over EDEN/Q/PTB for 2026-05-16 15:36Z..2026-05-17 15:36Z found discovery-fallback trades only on PTB, not EDEN/Q.
Discovery fallback results after subminute overwrite: 1m/5s produced 26 signals / 4 closed non-overlap trades, avg net -0.68%, TP1 hit 0%; 1m/15s produced 2 closed trades, avg net -0.68%, TP1 hit 50%; 5m/30s produced 0 trades.
Live-priority category checks on 1m/15s produced 0 trades for runner_oi_confirmed, runner_flow, runner_balanced. runner_oi_confirmed/runner_balanced rejected the single post-filter candidate by mark_basis_below_min; runner_flow had no post-filter signal.
Conclusion: a backtest can show hindsight discovery trades in this window, but current live category contract would not honestly open them. This supports fixing data/context freshness and category diagnostics before loosening live categories.
```

## 2026-05-17 - P277 applied locally

```text
Current patch status: P277 APPLIED locally / UNKNOWN commit.
Backtest TP1 is no longer counted on a mere candle-high touch. `simulate_long_signal()` now labels `tp1_fill_model=conservative_limit_proxy`, requires trade-through (`high > tp1_price`) for TP1 fill, and records exact touches as `touched_not_filled_conservative`.
Same-candle TP1/SL conflict remains conservative: stop is processed first and such cases are labeled `ambiguous_intrabar_stop_first`.
The run crash `candidates missing required columns: ['mark_close_vs_decision_close_basis']` was not caused by P276; it was a pre-context universe bug. `_strip_derivative_context_requirements()` now also clears `red_flag_profile` after category overrides are already applied, so `build_anomaly_signals()` does not reintroduce mark/OI requirements before context enrichment.
Expect TP1 hit-rate, winrate, and expectancy to drop versus old candle-high backtests. Treat that as improved honesty, not strategy deterioration by itself.
Next validation: rerun the failed anomaly-lab command and inspect `anomaly_trades.csv` for `tp1_fill_model`, `tp1_fill_status`, and lower/changed TP1 hit distribution.
```

## 2026-05-17 - anomaly_lab review state

```text
Current artifact review: .output/results/anomaly_lab, pairs 1m/5s + 1m/15s + 5m/30s.
TP1 conservative fields are present in trades, so this artifact is after P277 or equivalent.
Data quality is usable: trade_count_proxy_used=false, real number_of_trades and quote_volume sources are present. Context parity is still not perfectly strict; live-priority rows include some requested_context_missing_or_bad and oi_context_stale_asof, so final live-edge claims should use context_parity_status=ok.
Live-priority categories are meaningfully better than discovery: n=490, WR 72.9%, avg +1.30%, median +0.84%, positive-day share 91.7%. Discovery remains broad/noisy: n=1946, WR 47.1%, avg +0.17%, median -0.10%, worst day -43%.
runner_oi_confirmed and runner_flow are strongest. runner_balanced is stable but more top-tail dependent. runner_reclaim is not live-ready.
Best decision-time hardening candidate is positive/high mark_close_vs_decision_close_basis, then minimum non-tiny initial risk, stronger start_range_pct_ratio_to_baseline, lower trades/quote per abs return, lower prior_spike_count_72h / prior whipsaw.
Session effect matters: Asia/EU are cleaner; US is weaker and has worse day risk; late session is sparse/tail-dependent.
Next best step: strict parity ablation on current artifacts with context_parity_status=ok and category/session/TF split before changing live category thresholds.
```

## 2026-05-17 - P279 live-first category state

```text
Current patch status: P279 APPLIED locally / UNKNOWN commit.
Default live categories are now runner_oi_confirmed, runner_flow, runner_balanced. runner_reclaim is still supported but no longer default because current artifact evidence was weak and session-sensitive.
Category contract id: shared_pump_category_contract_v1_live_overlay_v6.
New shared contract fields now enforced in both backtest and live: min_start_range_pct_ratio_to_baseline, min_initial_risk_pct, category max_initial_risk_pct, max_prior_up_down_whipsaw_to_impulse_range, max_prior_spike_count_72h.
The hardening targets decision-time runner/fader separators: positive mark basis, meaningful range expansion, non-tiny initial risk, lower prior whipsaw/spike history, and lower poor trade-effort-per-return.
Expected effect: fewer live trades, higher median/avg quality if the 30d anomaly_lab relationship survives live execution. Treat this as a live-statistics collection policy, not proof of hundreds of percent monthly account returns.
Next validation: run real live at position_notional_usdt=12 and inspect category_rejected distributions, selected categories, context dependency timeouts, actual exchange fills, TP1 limit fills, and closed-trade PnL.
```

## 2026-05-17 - P280 startup context freshness state

```text
Current patch status: P280 APPLIED locally / UNKNOWN commit.
Slow 72h startup context backfill can leave the first live cycles with a trailing context gap. This does not stale the actual signal OHLCV or exchange order path, but it can stale prior_spike/prior_fast_fade category evidence.
P280 makes that gap explicit: if decision_timestamp_ms is more than symbol_context_snapshot_fresh_ms after the snapshot effective_cache_end_timestamp_ms, live reports symbol_context_snapshot_tail_stale and blocks the category as a retryable dependency.
This is the cheap/safe fix before live collection: do not run a second full 72h startup pass; let priority rolling context refresh catch up active/radar/retryable symbols.
Next validation: after live startup, inspect signal_scan_retryable_dependency_blocked for symbol_context_snapshot_tail_stale, then confirm symbol_context_snapshot_updated priority_reason_counts includes retryable_dependency_blocked and later selected signals have fresh prior context.
```

## 2026-05-17 - P281 baseline liquidity state

```text
Current patch status: P281 APPLIED locally / UNKNOWN commit.
Current anomaly_lab review supports an absolute-liquidity floor: live-priority returns improve with higher baseline trade-count / pre_1h trade-count / pre_1h quote-volume, while extremely thin pre-volume buckets are weak.
The bot should not rely on relative start_quote_ratio/start_trade_ratio alone because one print on a low-volume symbol can create a fake x100 flow ratio.
Category contract id: shared_pump_category_contract_v1_live_overlay_v7.
Runner categories now require min_baseline_quote_daily_proxy=300k USDT/day proxy, computed from baseline_quote_volume_median and setup timeframe in both live and backtest.
Do not raise the floor to 1m yet: 300k-1m baseline daily proxy still showed positive live-priority expectancy and useful frequency.
Next validation: in live_events.csv, monitor reject_low_baseline_quote_daily_proxy counts and compare selected trades' baseline_quote_daily_proxy distribution against closed PnL.
```

## 2026-05-17 - P282 live liquidity universe state

```text
Current patch status: P282 APPLIED locally / UNKNOWN commit.
Live can now reject low-liquidity symbols before the expensive 72h context and scan loop when the universe comes from the exchange default symbol list.
The default startup/refresh gate is Binance 24h ticker quoteVolume >= 300k USDT, refreshed every 12h. Explicit --symbols bypass this universe gate so targeted tests are not silently pruned.
If the ticker request fails or the filter would empty the live universe, live keeps the previous universe and writes live_symbol_universe_liquidity_filter with refresh_failed_keep_previous or empty_keep_previous.
This is a scan/context cost and data-quality guard, not a substitute for the P281 category-level min_baseline_quote_daily_proxy filter.
Next validation: start live with position_notional_usdt=12 and inspect live_symbol_universe_liquidity_filter output_count/removed_symbols before judging signal frequency.
```

## 2026-05-17 - P283 live terminal grid state

```text
Current patch status: P283 APPLIED locally / UNKNOWN commit.
The live status grid must be a single pinned terminal block: ordinary logs/warnings clear the current grid, print the message, then repaint the latest grid as the last output.
P283 routes Python warnings through the status logger during the live loop, clears multi-line status blocks with ANSI clear-from-top-to-bottom, and removes the pandas synthetic_ohlcv_bucket FutureWarning source.
Next validation: run live in PowerShell and confirm live_symbol_universe/status updates do not leave duplicate Соединение/Рынок blocks and warning lines appear above the repainted grid.
```

## 2026-05-17 - P276 applied locally

```text
Current patch status: P276 APPLIED locally / UNKNOWN commit.
Live safety audit found one real TP1-limit transition risk: after a verified entry and initial stop, failure to create/verify the exchange-side TP1 limit closed exposure reduce-only but did not cancel the already-created initial stop.
P276 cancels that initial stop after successful reduce-only cleanup and records either position_initial_stop_cancelled_after_tp1_failure or position_initial_stop_cancel_after_tp1_failure_failed.
Lifecycle tests now match the current live contract: verified stop + verified TP1 limit on open, TP1 fill reconciliation from exchange order fill, BE stop replacement, and stop/TP1 cleanup.
Live/backtest parity remains not exact for exits: backtest still models TP1 by candle high, while live requires an exchange-side reduce-only limit fill. Treat backtest TP1 as optimistic until a parity report compares live order-fill outcomes against candle outcomes.
Next validation: run the real minimal live-order smoke after P274/P276 and require final ordinary/algo open orders = 0; then run a short strategy live smoke and inspect position_tp1_limit_order_verified, tp1_limit_exit_filled, position_tp1_limit_order_cancelled, and the new TP1-failure cleanup event absence/presence.
```

The project direction is anomaly-first: anomaly nature/category research, anomaly continuation backtests, and strict REST-only micro-live validation.

---

## 2. Active system thesis

```text
abnormal activity -> nature/category check -> controlled continuation -> executable entry -> managed exit
```

The edge is not assumed. The current task is to identify which anomaly categories are tradable, which are exhausted/fake/thin/late, and which should be rejected before expensive backtests.

---

## 3. Reliability priority

```text
data availability > leakage safety > execution realism > edge stability > parameter optimization
```

No strong conclusion about anomaly nature without real trade-count and quote-volume evidence available at decision time.

---

## 4. Current code status

```text
Active CLI:
fetch-data
update-cache
run-anomaly-lab
run-anomaly-live
run-anomaly-top-growth
run-hourly-levels
check-quality
clear-cache
```

Runtime strategy path:

```text
research_tools/anomaly_config.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
research_tools/charting.py
research_tools/hourly_levels.py
```

---

## 5. Open risks

1. Anomaly live/backtest logic still needs robustness evaluation across months and regimes.
2. Micro-live execution has real slippage, spread, partial fills and operational failure modes.
3. OI/derivatives context availability can limit category classification.
4. The 2026-05-11 NVDA micro-live position is audit-invalid for edge/PnL: stale signal execution mixed signal close with later live order timing.
5. P156 fixes the Linux `main.py` startup blocker by importing `ctypes.windll` only on Windows.
6. Historical local artifacts may contain stale compiled files; they are ignored by git and should be deleted locally.
7. Closed-hour top-growth snapshots are now populated by default inside live through P242 incremental closed-1h exchange-candle audit. The live loop processes a bounded number of symbols per cycle and writes `top_growth_index.csv`, per-hour top/status files, and `missed_pump_visibility*.csv` from the same run's `live_events.csv`. P237/P238 session-top heartbeat remains ticker-snapshot operator UI only and is not used as a closed-hour audit fallback.
8. Active symbols whose latest closed levels candle was already scanned are now kept visible as `active_waiting_*` in batch artifacts and should not consume OHLCV scan slots until a new closed candle exists.
9. Ticker radar is scheduling-only: it can add bounded extra watch scans, but cannot remove symbols from round-robin, cannot open trades, and cannot replace closed-kline flow evidence.
10. Operator commands now live in root `COMMANDS.md`; the shared command baseline is 30 days via `DEFAULT_COMMANDS_BASE_DAYS`.
11. P167 changes live scheduling/performance, not entry filters: selected hot symbols are scanned across all due TF sets in one pass, and production-fast live can set `--inactive-scan-slots-per-cycle 0` to rely on active state plus ticker radar instead of heavy cold round-robin aggTrade work.
12. P168 changes live data access: live now reads parquet first, fetches only missing ranges, writes fetched rows with provenance, and emits cache gap events instead of treating incomplete cache as a silent no-signal condition.
13. P169 fixes live cache candle boundaries: subminute missing ranges fetch through the final candle end, parquet first-load reads only the requested window, and decision frames exclude accidental non-closed cached candles.
14. P170 buffers live cache writes so hot-loop decisions are not blocked by parquet rewrites; buffered/flushed/failed rows are explicit live artifacts.
15. P177 fixes the live OHLCV cache concat path that emitted pandas `FutureWarning` when empty cache placeholders were concatenated with fetched candles; it does not change signal or trading logic.
16. P178 proposes per-cycle raw aggTrades range reuse for S30/S15/S5 live scans and status logging as `batch/full` cycle time with local closed-trade PnL only.
17. P179 proposes deferring inactive subminute `aggTrades` scans until ticker-radar or active state, with startup refusal instead of fallback when ticker radar is unavailable.
18. P180 fixes live startup/first-cycle status artifacts after P178/P179: local closed-trade PnL is `0.0` until the first finalized closed trade, while non-finite local PnL counters still raise integrity errors.
19. P181 changes live operator status/artifact numbering only: it displays `цикл batch/full-cycle` so batch ticks are not confused with completed universe passes.
20. P185 proposes strict WS live health: subminute live refuses blind ticker-radar startup, and WS aggTrade precise scans no longer perform unbounded REST backfill by default.
21. P186 proposes event-driven WS scheduler semantics: subminute+ticker-radar live defaults to zero implicit inactive scan slots, and the operator heartbeat reports ticker/aggTrade health instead of ambiguous batch/full-cycle timing.
22. P188 proposes WS live missed-entry hardening: bounded initial aggTrade backfill is default for radar-promoted symbols, all-missing ticker radar becomes network degradation, max-position rejects remain retryable within the same decision candle, and monitor internal errors are integrity errors.
23. P189 proposes live aggTrade REST gap prefetch planning: precise active/radar symbols coalesce due S30/S15/S5 entry WS coverage gaps per symbol before signal evaluation, populate the WS buffer once, and expose prefetch/backfill/pending counts in `live_cycle_summary`. No new flags are introduced; oversized gaps remain explicit coverage-pending, not stale-entry fallback.
24. P190 proposes idempotent live order placement with deterministic client order ids and pre-stop exposure cleanup.
25. P192 proposes live account-mode preflight plus close-only startup exchange-position cleanup: unsupported hedge mode blocks startup, and any pre-existing live-universe exchange position is reduce-only closed and verified flat before the live loop.
26. P205 proposes live/backtest category parity cleanup: backtest category attribution now follows live TF priority with discovery fallback for artifacts, live does not add discovery as tradable category, and live prior-fast-fade 72h context uses levels-timeframe historical OHLCV instead of unavailable subminute entry cache.
27. P219 fixes a real live crash from run `20260514_201044`: `_live_client_order_id()` used `re` without importing it, so the first real GWEI entry attempt stopped before order submission. Fatal internal/data-integrity errors now use synchronous Telegram delivery and emit `telegram_sync_send_failed` if Telegram itself fails.
28. P220 fixes a live safety gap found during the last-15-commit review: an exception from entry order/fill resolution before `LivePosition` creation now tracks the symbol and immediately attempts reduce-only cleanup of any real unprotected exposure.
29. P221 fixes a second post-entry runtime blocker found in the live health review: `append_position()` no longer references undefined scan-mode/guard locals and the live ledger schema now includes those diagnostics.
30. P223/P224 add an opt-in idle-only delayed replay auditor: live captures category_selected/category_rejected/execution-rejected anomaly decisions into delayed_replay artifacts, processes them only when idle, recomputes frozen-decision signals from cache-only OHLCV, and can send TG when replay finds an ignored entry.
31. P225 adds a frozen LiveSignal snapshot fallback for execution-rejected runner signals when exact cache-only recomputation is blocked by intentionally disabled mark/OI exchange-context fetch.
32. P226 proposes evidence labeling for delayed replay: strict candle recompute and frozen live-signal snapshot are separated in result columns, mismatch labels, and Telegram wording so snapshot fallback is not presented as strict backtest-like proof.
33. P227-P229 harden delayed replay final-decision capture and immutable decision snapshots; P230 proposes removing the separate Telegram switch so important replay alerts follow `--delayed-replay-enabled true`.

---

## 6. Next best step

After the 2026-05-16 stop-visibility live run, do not tune entry filters first. Run the P263 fake-exchange lifecycle tests, then add narrower CCXT/Binance stop payload and order-not-found tests before changing live stop verification code.

```bash
python -m unittest tests.test_live_order_lifecycle -v
python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Expected readout: all four lifecycle tests pass. They must prove: successful entry writes `position_stop_order_verified` + `position_opened`; invisible initial stop raises `LiveOrderPositionIntegrityError` and writes `unprotected_entry_reduce_only_exit_filled`; monitor stop exit writes `stop_exit_filled` + `position_closed`; TP1 path writes `tp1_partial_exit_filled`, replaces stop to BE, cancels old stop, then closes on verified stop fill.

Latest next step after P167:

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20 --inactive-scan-slots-per-cycle 0 --ticker-radar-watch-batch-size 10 --scan-hot-timeframes-per-symbol true --live-ohlcv-cache-enabled true --live-ohlcv-cache-write-enabled true --live-ohlcv-cache-flush-interval-seconds 10 --live-ohlcv-cache-max-buffer-rows 5000
```

Read `live_events.csv` first: `ticker_radar_snapshot`, `ticker_radar_promoted`, `symbol_batch_selected`, `signal_symbol_scan_summary`, `live_ohlcv_cache_read`, `live_ohlcv_cache_gap`, `live_ohlcv_cache_buffered`, and `live_ohlcv_cache_flush_summary` must prove that the loop is active/radar driven, cache-backed, fast, closed-candle aligned, and not silently skipping diagnostics.


---

## 7. Current audit note

P130 compiled and addressed the main live-execution bug, but second review found one remaining realism issue: entry drift must be absolute, not only positive. P131 fixes that and adds Telegram event notifications for stale/non-executable selected signals.

---

## 8. Current audit note — P132

P132 is proposed after P130/P131. Entry selection is no longer the only audit risk: position protection and exit accounting must also be strict. Live now treats unknown stop/fill/monitor data as explicit artifact events and integrity errors instead of temporary noise. Backtest artifacts now export skip-reason distribution so reduced trade count is explainable.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# fake-exchange smoke: stop verification failure, TP1 verified fill, repeated empty monitor OHLCV
```

---

## 9. Current audit note — P133

P133 is Telegram wording only. It does not change live trading decisions, fills, stop verification, PnL calculation, or backtest logic. Telegram becomes concise; `live_events.csv` remains the detailed source of truth for blocked-entry and integrity details.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# Telegram smoke: blocked-entry, integrity, open, close and stop-update messages render with clickable symbol links.
```

---

## 10. Current audit note — P134

P134 changes only live scheduling, not signal filters, fill verification, stop logic or PnL. The fixed `inactive_batch_with_active=7` behavior was wrong for live execution: active now means open/opening positions plus symbols with recent high-stage pump/signal state. Each cycle scans `active + (symbol_batch_size - active_count)` inactive symbols, live artifacts record why a symbol became active or left the active set, and selected signals blocked only by max-open-position capacity are not consumed until they expire or can be retried.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# smoke: mark two active symbols with symbol_batch_size=20; next batch must contain both plus 18 inactive symbols.
```


---

## 11. Current audit note — P137

P137 is diagnostics only. It repairs the partially applied hourly-level scanner: the module is restored and the CLI command is wired. It adds `run-hourly-levels` for offline/manual review of 1h overhead levels. Touches are valid only when followed by a meaningful bounce; wick-only marks and clear downtrend pseudo-levels are rejected by default.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```


---

## 12. Current audit note — P138

P138 is diagnostics-only. It prevents hourly-level chart generation from spamming matplotlib missing-glyph warnings for symbols containing non-ASCII characters. CSV outputs still keep original symbols; only chart titles and file stems are converted to ASCII-safe text when needed.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```


---

## 13. Current audit note — P139

P139 is diagnostics-only. It adds visible progress/ETA logging to `run-hourly-levels` so long all-cache chart runs are operator-observable instead of appearing stuck after the start line. It does not change level detection rules, live trading, backtest entries, exits, stops or PnL.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --progress-every-symbols 5 --progress-min-seconds 5
```


---

## 14. Current audit note — P140

P140 is diagnostics-only. It makes `run-hourly-levels` closer to human chart review: repeated candles near the same price no longer count as independent touches unless price first resets away from the level, chart levels start at the first valid touch, close duplicate levels are capped, and pierced/spiked-through resistance is rejected by default.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --reject-pierced-levels true --max-level-pierce-pct 0.015 --max-levels-per-symbol 4
```


---

## 15. Current audit note — P141

P141 is diagnostics-only. It speeds up `run-hourly-levels` by reducing parquet payloads, trimming source candles before 1h aggregation, and replacing pandas row/copy loops in level validation with numpy scans. Use `--fast-source-trim false` only when an exact full-history aggregation comparison is needed.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --fast-source-trim true
```


---

## 16. Current audit note — P142

P142 is diagnostics/chart-output only. It removes the `tight_layout()` solver from hourly-level charts and uses explicit subplot margins because the chart intentionally draws price tags outside the right edge of the price panel. This removes the warning and avoids a small per-chart layout cost.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --fast-source-trim true
```


---

## 17. Current audit note — P143

P143 is live-performance/audit cleanup. It does not relax stale-signal safety, does not change signal thresholds, and does not add dynamic universe pruning. Live scan now deduplicates OHLCV fetches by `levels_timeframe`, records stale backfilled decisions before expensive signal build as `reject_stale_signal` with `stage=prescan`, and moves top-growth collection to standalone `run-anomaly-top-growth`.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --confirm-real-orders --max-cycles 30 --symbol-batch-size 20
python main.py run-anomaly-top-growth --top-growth-min-return-pct 0.10 --top-growth-limit 5
```


---

## 18. Current audit note — P144

P144 keeps the live universe fixed and does not add dynamic pruning. It reduces repeated REST work by remembering the last closed candle scanned per symbol and levels timeframe. Active symbols remain operator-visible every cycle, but unchanged active symbols move to `active_waiting_symbols` and free their slot for inactive rotation. This should improve cold-universe traversal without weakening stale-signal guards or hiding stale/empty-data artifacts.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --days 30 --symbol-batch-size 20
```


---

## Current audit note — P146

P146 is a safety cleanup for the P145 ticker-radar scheduler. It does not change strategy thresholds or execution guards. It avoids oversized ticker queries, prevents radar-waiting symbols from wasting inactive slots, and clears ticker-radar watch state once a symbol becomes a real active symbol.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --days 30
```

Watch `ticker_radar_watch_cleared`, `ticker_radar_waiting_count`, `inactive_count`, `effective_scan_count`, cycle time and `ticker_radar_failed`.

---

## Current audit note — P147

P147 is operator UX/default wiring only. It adds root `COMMANDS.md` as the single copy-paste command file and centralizes the shared 30-day command baseline in `constants.py`.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py fetch-data --help
python main.py run-anomaly-lab --help
python main.py run-anomaly-live --help
python main.py run-hourly-levels --help
```

Known caveat:

```text
P156 removes the Linux `ctypes.windll` startup blocker; keep `python main.py --help` in verification.
```

Operational rule:

```text
Copy commands from COMMANDS.md; change the 30-day baseline in constants.py instead of expanding command lines.
```
---

## Current audit note — P148

P148 is live console UX only. Routine heartbeat status now updates one terminal line in-place for the default interactive console runner, while real event/error/position/Telegram failure logs first terminate that status line and remain permanent sequential logs. The old `live: цикл ...s` label is superseded by P201's prefix-free heartbeat.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known caveat:

```text
External/non-interactive loggers keep the old sparse heartbeat cadence to avoid writing every cycle to file-like logs.
```


---

## Current audit note — P149

P149 is diagnostics-only. `run-hourly-levels` now counts only high-based 1h touches: the candle high must be near the level, the candle body must remain below the touch band, counted touches must be separated by at least 6h, and pierced levels are rejected strictly by default. This should reduce false overhead levels drawn through candle bodies or already wicked-through highs.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --min-touches 3 --min-touch-spacing-hours 6 --reject-pierced-levels true --max-level-pierce-pct 0.0
```

Known effect:

```text
Level count should drop versus P148; compare charts before treating the reduced output as a strategy signal.
```

---

## Current audit note — P150

P150 is diagnostics-only. `run-hourly-levels` now rejects a pivot/high source candle when any close in the previous 12h is above that candle high. This prevents building overhead levels from candles whose price was already accepted/reclaimed shortly before the supposed resistance touch.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --level-source-close-lookback-hours 12
```

Known effect:

```text
Level count can drop again versus P149; rejected cases should be those with a source high below a close from the previous 12h.
```


---

## Current audit note — P151

P151 is artifact-only. Trade charts now use three panels: top trade-window candles with execution annotations, middle independent 1h context for the last 7 fully closed days before entry with unlabeled strict hourly levels, and bottom normalized quote-volume/trade-count line curves. Trading/backtest execution logic is unchanged.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --days 30
```

Known effect:

```text
The lower panel compares normalized shapes only: orange quote volume and green number_of_trades are each scaled to their own chart-window maximum.
```

---

## Current audit note — P152

P152 is live operator UX and chart-artifact only. After a verified entry fill and stop placement, live now attempts to render the canonical trade chart for the opening Telegram message. P163 supersedes the older three-panel layout: current charts are `LTF -> HTF -> volume/trades -> 1h`, the bottom 1h context spans 4 days through the candle that contains the main chart end, and chart levels are searched only inside that visible 4-day context window. Open charts replace risk/reward shaded rectangles with TP1/SL horizontal lines. Text-only Telegram remains a fallback.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known effect:

```text
A real opened position now performs one additional H1 context fetch and Telegram photo upload; failures are recorded as chart_render_failed, chart_context_fetch_failed, or telegram_open_chart_failed and should not block position monitoring. Any levels drawn in the 1h context panel must have been discovered from the same visible 4-day context, not older hidden history.
```

---

## Current audit note — P154

P154 is Telegram operator UI only. Symbol-specific messages now use a deterministic animal emoji derived from compact symbol text, so open/stop/close/blocked/error messages for the same symbol share one stable emoji across process restarts. Service messages use fixed `🚧`/`⚠️` icons, timeframe/context lines are monospace, and error payloads are monospace.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known effect:

```text
Telegram text appearance changes only; live signal, order, stop, chart, and artifact semantics are unchanged.
```

---

## Current audit note — P155

P155 is live audit-only. Network degradation/recovery, async Telegram failures, open/close Telegram photo fallbacks, and stop-cooldown signal rejects are now explicit `live_events.csv` events instead of being visible only through console output or implicit fallback behavior.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known effect:

```text
No signal/order/risk behavior changes. live_events.csv should become more verbose around Telegram delivery and temporary API/network degradation.
```

---

## Current audit note — P156

P156 is live safety/audit cleanup. Linux CLI startup no longer imports `ctypes.windll` at module import time, so `python main.py -h` can run on Linux. Live monitor no longer converts externally closed or stop-closed positions with unresolved exit fill into normal `position_closed` rows with proxy PnL. Such cases now end as `position_exit_unresolved`, write ledger `status=exit_unresolved`, keep realized PnL blank, and notify events as an integrity/audit problem.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-anomaly-live --help
# fake-exchange smoke: exchange amount zero before monitor decision -> position_exit_unresolved, no realized PnL
# fake-exchange smoke: stop triggered but fetch_order_fill fails -> position_exit_unresolved, no realized PnL, stop cooldown recorded
```

Known effect:

```text
Closed-position counts still advance because exchange exposure is gone, but edge/PnL analysis must use only ledger rows with status=closed and verified exit fill.
```


---

## Current audit note — P158

P158 is proposed on top of P156; P157 was not applied. It changes live/backtest signal architecture from fake timeframe labels to a two-stage model:

```text
forming HTF setup from closed LTF buckets -> LTF entry permission -> live exchange guards
```

Live no longer waits for the full HTF candle to close. For `5m/30s`, the default `confirmation_candles=4` means the earliest setup decision is after 4 closed 30s buckets, about 2 minutes. HTF/forming-HTF flow remains the source of setup quality; LTF is used for entry freshness, activation hold and execution path quality, not for re-proving the whole pump thesis.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 30
python main.py run-anomaly-live --help
```

Known effect:

```text
Backtest artifacts with feature_contract=htf_setup_ltf_entry_v1 are not comparable to old closed_setup_tf_v1 results. Pair-aware backtest needs cached entry timeframe data; missing 30s/15s/5s cache is a data availability problem, not a strategy result.
```

---

## Current audit note — P159

P159 supersedes the standalone P158 patch when applied as the combined HTF/LTF live-health patch on top of P156. Live keeps the intended two-stage model:

```text
forming HTF setup from closed LTF buckets -> LTF entry permission -> live exchange guards -> actual fill / verified stop
```

Fixes added after P158:

```text
forming HTF quote/trade checks use pace-normalized ratios plus a raw-progress floor
TP1 = nearest higher round market number above the old 1R TP1
transient setup/entry fetch failures no longer consume the scan slot
sub-minute pair-aware backtest requires explicit historical entry-TF cache
```

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 30
```

Known effect:

```text
TP1 hit-rate and RR guard behavior can change because TP1 is no longer exactly 1R. Treat P159 results as a new feature_contract-era run; do not compare PnL directly to old closed_setup_tf_v1 or raw-P158 runs.
```

---

## Current audit note — P160

P160 is cleanup-only on top of P159. It removes retired closed-HTF live signal builders and unused backtest helpers so the maintained path is unambiguous:

```text
forming HTF setup from closed LTF buckets -> LTF entry permission -> live exchange guards
```

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
```

Known effect:

```text
No trading behavior should change. If a future patch needs closed-HTF-only logic, it should be reintroduced explicitly with matching live/backtest artifacts instead of editing dead legacy methods.
```

---

## Current audit note — P161

P161 fixes a backtest runtime blocker in the market-entry execution path. `_resolve_signal_entry()` already had a module-local safe divide helper named `_safe_divide_value`, but the drift/RR guard used the stale `_safe_divide` name and crashed before skip reasons or trades could be written.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 30
# launcher.py is absent in the uploaded ZIP
```

Known effect:

```text
No trading semantics should change. The previous run stopped before evaluating market-entry drift/RR guards; after P161 it should either simulate trades or emit normal skip reasons such as market_entry_price_drift / market_entry_rr_collapsed.
```

---

## Current audit note — P162

P162 fixes live terminal PnL accounting. Normal `position_closed` rows now calculate the terminal PnL leg from the verified remaining amount, not from original entry size when `remaining_amount == 0`. TP1 monitoring also treats a disappeared material residual position after a partial TP1 fill as unresolved exit evidence, not as a normal close.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic: remaining_amount=0 terminal close does not add position.amount PnL
# synthetic: partial TP1 fill + exchange amount zero -> position_exit_unresolved, blank realized PnL
```

Known effect:

```text
Live PnL can decrease versus old artifacts because previously double-counted or proxy-counted terminal size is no longer included. Treat older live ledger rows around TP1/full-close residuals as audit-suspect until revalidated.
```

---

## Latest anomaly-lab readout

```text
Artifact: .output/results/anomaly_lab
Config: 1m/1m, feature_contract=closed_setup_tf_v1, 7-day window
Observed entries: 2026-05-04 13:48 UTC -> 2026-05-11 08:32 UTC
```

Verdict:

```text
1095 closed trades across 476 symbols, but aggregate edge is negative:
avg net return = -0.0760%
sum net return = -83.20%
win rate = 45.57%
positive days = 2 of 8
```

This run rejects any claim of stable edge for the tested `closed_setup_tf_v1` setup. Signal frequency is high, but expectancy, median trade, and day-level consistency are weak.

Current best next research step:

```text
P163 makes quote-volume / trade-count provenance explicit in fresh anomaly-lab exports.
Next compare the same window against the pair-aware HTF/LTF contract before tuning filters.
```

Same-window P163 comparison result:

```text
1m/1m closed_setup_tf_v1:
closed trades = 1105
avg net return = -0.0568%
median net return = -0.1794%
sum net return = -62.73%
positive days = 1 of 8

5m/1m htf_setup_ltf_entry_v1:
closed trades = 423
avg net return = -0.0521%
median net return = -0.2407%
sum net return = -22.05%
positive days = 4 of 8
```

Interpretation:

```text
HTF/LTF reduces trade count and aggregate damage, and improves day-level distribution, but it still does not produce positive expectancy. Do not claim edge. The next useful research step is not parameter tuning; first inspect why TP1/trail winners fail to overcome stop-loss mass and whether anomaly category separation can reject the losing wake-up types.
```

Correction:

```text
5m/1m was only a cheap proxy and is not an intended trading TF set. Do not use it as the trading-grid result.
```

Latest intended TF-set result:

```text
Data source: cached 1s OHLCV aggregated in memory to 30s/15s/5s entry frames.
Provenance labels: cached_1s_aggregated_to_<tf>.number_of_trades / quote_volume.

5m/30s:
closed trades = 17
avg net return = +0.1281%
sum net return = +2.18%
win rate = 52.94%
TP1 hit rate = 47.06%

1m/15s:
closed trades = 44
avg net return = +0.5832%
sum net return = +25.66%
win rate = 70.45%
TP1 hit rate = 65.91%

1m/5s:
closed trades = 37
avg net return = +0.0793%
sum net return = +2.94%
win rate = 54.05%
TP1 hit rate = 51.35%

Combined:
closed trades = 98
avg net return = +0.3140%
sum net return = +30.78%
win rate = 61.22%
TP1 hit rate = 57.14%
```

Reliability note:

```text
This is promising but not yet "big and confident plus": only 98 trades, only 3 active UTC days, and the best TF set is 1m/15s with 44 trades. Treat patterns as candidate grid hypotheses, not proven production filters.
```

Most useful observed pattern:

```text
Winners are concentrated in clean/high-retention/large-ticket setups, especially when mark is positive versus decision close. Fast-fade labels and mark-discount/flat + oi_down_price_up + clean/high-retention/large-ticket setups are weak.
```

Leakage/bottleneck status:

```text
P164 audit found no direct use of future_* / outcome / exit-result fields in executable anomaly signal filters.
Those fields are still exported for diagnostics and must stay forbidden for grid filters.
The run command used --days 7, but the 98 closed true-TF trades occurred only on 2026-05-04..2026-05-06 UTC.
That density is high enough to start cutting with red-flag hypotheses, but the filters are not proven until tested on a longer 1s-backed window.
Main runtime bottleneck is repeated full-symbol 1s parquet reads + in-memory aggregation/scanning for each TF set; materialized 5s/15s/30s caches are the highest-value speed patch.
```

P165 status:

```text
Implemented materialized 1s-derived 5s/15s/30s caches, entry-cache coverage artifacts, searchsorted LTF slicing, and explicit red-flag profiles.
The "7 day" true-TF sample is confirmed to be only 3 active executable-data days because subminute/1s coverage ends on 2026-05-06T11:40Z while the requested end was 2026-05-11T08:36Z.
Same-window cautious red flags improved combined true-TF result to 36 closed trades, avg +0.9198%, sum +33.11%, WR 83.33%, TP1 77.78%.
This is a candidate filter set, not a proven grid. Next step: fetch/repair 1s coverage through the requested end and rerun base vs cautious with unchanged thresholds.
```

Dormancy note:

```text
Current anomaly "sleep" is only a local 60 setup-candle baseline, not a 24h/72h dormancy rule.
Quick same-artifact check did not support rejecting all symbols with any recent spike; 1-4 prior same-symbol candidates in 72h were not bad.
High serial density looked weaker: prior_72h_candidates >= 5 had avg -0.0214% and WR 46.15% on 26 trades.
Next diagnostic patch should add prior_spike_count_24h/72h, prior_fast_fade_count_24h/72h and time_since_prior_spike before using this as a filter.
```

P166 14d runner-study status:

```text
Added aggTrades -> 1s backfill command, render_charts switch, and recent-spike diagnostics.
Binance futures kline endpoint cannot fetch 1s; any 1s backfill must use aggTrades.
Full 14d all-symbol 1m/5s did not complete within 1 hour, so current 5s evidence is active-symbol subset only.
Best full-universe result is 1m/15s cautious: 62 closed, avg +1.4642%, sum +90.78%, WR 80.65%, TP1 77.42%, runner 75.81%.
Runner separators in the full base sample: positive mark basis, strong mark context momentum, low quote/trade effort per return, and mid-range hold ratio.
Next step: do targeted aggTrades backfill for candidate-bearing symbols/windows, then rerun 1m/5s full without changing thresholds.
```

P171 command-contract status:

```text
`python main.py run-anomaly-lab --days N` now means the full working anomaly TF set: 5m/30s, 1m/15s, 1m/5s.
The old 1m/1m mode is no longer implicit; it only runs when explicitly requested with `--timeframe 1m` or an equivalent single-pair override.
Multi-run artifacts are separated by pair under the output directory and indexed by anomaly_lab_timeframe_runs.csv.
Next readout should compare all three per-pair summaries from the same end timestamp; do not mix old root-level 1m/1m artifacts with new per-pair outputs.
```

2026-05-13 indexed anomaly-lab readout:

```text
Analyzed .output/results/anomaly_lab indexed subdirs only: 5m_30s, 1m_15s, 1m_5s.
Closed trades: 98 across only 3 active UTC days, combined avg +0.3140%, median +0.2478%, sum +30.78%, WR 61.22%, TP1 57.14%.
Best set remains 1m/15s: 44 closed, avg +0.5832%, sum +25.66%, WR 70.45%, TP1 65.91%.
Reliability is not live-ready: entry cache coverage has symbols_covering_end=0 and entry caches end around 2026-05-11 08:35Z while requested end is 2026-05-13; no complete 7-day executable window.
Combined result becomes negative without top 10 winners, so current edge is candidate evidence, not proven stability.
Most promising live-available filter family: positive mark basis/context momentum + flow_hold>=1 + cap extreme effort/quote/trade ratios + reject serial/fast-fade recent context.
Critical live-prep item: add a paper/live shadow run that logs selected TF arbitration, cache coverage, would-enter/would-skip reasons, and exchange fillability before real order launch.
```

P172 backtest orchestration status:

```text
Default multi-TF anomaly lab no longer walks symbols separately for 5m/30s, 1m/15s and 1m/5s.
It now collects candidates in one symbol-major pass, reading each symbol/timeframe frame once and evaluating all eligible TF pairs before moving to the next symbol.
Per-pair artifacts remain separated under the same indexed output directories.
Next validation on a real run: compare candidate/signal/trade counts against the prior indexed run at a fixed end timestamp; any difference must be explained before interpreting PnL changes.
```

P173 runner-category replay status:

```text
Current .output/results/anomaly_lab is not a complete 3-TF run: 5m_30s and 1m_15s are complete, 1m_5s has no candidate/trade artifacts because the command was interrupted.
The completed run is 30 requested days with 26 active trade days, but executable subminute coverage is partial and symbols_covering_end=0, so it is not full-universe production evidence.
Baseline completed pairs: 567 closed, avg +0.5950%, median +0.3274%, sum +337.38%, WR 56.79%, TP1 54.14%, top10 dependency 42.26%.
Runner-balanced replay from existing candidates: 124 closed, avg +1.7739%, median +1.3985%, sum +219.97%, WR 80.65%, TP1 75.81%, active days 25, top10 dependency 46.56%.
Runner-balanced rules are in-sample category hypothesis: mark basis >= 10bp, quote/trade effort caps, start taker-buy delta cap, prior fast-fade 72h cap.
Next proof step: rerun runner_balanced on fixed-end complete 1s/subminute coverage including 1m/5s, then compare out-of-sample or later-window replay before live.
```

P174 OI-confirmed live-category status:

```text
OI is not optional noise. In the latest completed-pair artifacts, moderate positive OI confirmation improves the runner category, while extreme OI > 5% is too sparse and bad in-sample.
Runner-balanced + OI 3x5m > 0.3% + mark basis >= 30bp replay:
5m/30s: 15 closed, avg +2.574%, median +1.717%, sum +38.61%, WR 100.00%, TP1 100.00%
1m/15s: 21 closed, avg +3.014%, median +2.710%, sum +63.29%, WR 80.95%, TP1 76.19%
Combined: 36 closed, avg +2.831%, median +1.885%, sum +101.90%, WR 88.89%, TP1 86.11%, top10 dependency 75.58%
Live default category is now runner_oi_confirmed. Missing/stale mark or OI context is a reject, not neutral.
Residual risk: the category is selective, in-sample, missing 1m/5s, and live cannot yet fully match backtest prior_fast_fade_count_72h at startup.
```

P175 live-lag diagnostic status:

```text
Live now records first executable entry timestamp and lag from that timestamp to order submit/fill.
entered_late_vs_first_executable means fill lag >= 1 entry LTF candle.
Live also records scheduler scan gap: previous closed LTF candle seen for that symbol/TF, first unscanned decision timestamp, and skipped LTF candle count before the current detected signal.
If a selected signal has a scan gap, live starts a background missed_entry_replay_probe over the already loaded candle window and writes the first skipped LTF candle where the same live category filter would already have selected a signal.
Reject events for stale/drift/RR collapse include the same lag fields, so shadow-live can separate missed timing from bad signal quality.
The scan-gap counters add no exchange calls. The replay probe is non-blocking; for OI/mark-confirmed categories it may fetch OI/mark context in the background and must be monitored separately from order latency.
```

P176 live honesty/budget status:

```text
missed_entry_replay_probe now probes skipped LTF decisions from the earliest missed candle. If the skipped window is larger than signal_scan_backfill_candles, the event is marked probe_truncated and reports unprobed_newer_decision_count.
No-signal status in a truncated probe is no_prior_signal_found_in_probed_prefix, not a claim about the whole skipped interval.
Live exposes --signal-scan-backfill-candles so context/API budget can be reduced for scarce-limit sessions.
Residual limit risk remains: runner_oi_confirmed replay checks can request OI/mark context in the background. This is diagnostic-only but should be run with a small cap during real test live.
```

P177 live latency status:

```text
Current live evidence shows full-universe cold pass around 10-12.5 minutes, but inactive symbols are mostly deferred and cheap; expensive time is concentrated in precise_ticker_radar/precise_active scans that fetch subminute aggTrade tails.
Adding another per-symbol "cheap" 1m/5m wake-up is not clean yet because it can become a second hidden OHLCV scan path. The clean first step is attribution plus explicit precise-scan budgeting.
live_cycle_summary now records ticker_radar_seconds, batch_select_seconds, signal_scan_seconds, open_signal_seconds, order_reconcile_seconds, and cache_flush_seconds.
Live exposes max_precise_scan_symbols_per_cycle. Active symbols are not dropped; only ticker-radar watch symbols beyond the remaining budget wait and are logged.
```

P178 live universe status:

```text
Default live universe now excludes a conservative static high-cap major list without calling external market-cap sources.
This is not a hidden edge filter: live writes live_symbol_universe_filter with excluded symbols and counts.
Explicit --symbols are not filtered, so manual tests remain exact.
Risk: static high-cap exclusion introduces selection bias and should not be back-justified as data-derived cap ranking.
```

20260513_164045 live timing readout:

```text
Run had 69 cycle summaries, 563-symbol universe, and predates the high-cap exclusion patch.
No trades opened and no category_selected events; this is a latency/bottleneck sample only.
Latency split: signal_scan_seconds avg 10.161s (~62.5% of batch time), cache_flush_seconds avg 4.977s (~30.6%), ticker_radar_seconds avg 0.454s, order_reconcile occasional ~6.5s spikes.
Precise ticker-radar scans cost p50 ~2.0s, p95 ~5.7s, max 9.75s per symbol; inactive deferred scans are near-zero.
Next speed work should target subminute aggTrade tail fetch count and cache flush overhead before adding another OHLCV wake-up path.
```

P179 live speed patch status:

```text
Implemented bounded cache flush and partial aggTrade raw cache reuse.
Default live cache flush changed from 10s/5k rows to 30s/50k rows, with at most 20 symbol/timeframe shards flushed per non-forced cycle.
Forced shutdown/error/max-cycle flush still drains all pending shards.
AggTrade cycle cache now subtracts already covered raw intervals and fetches only missing time ranges, instead of requiring one cached range to cover the whole request.
Flush diagnostics now separate failed_symbol_timeframes from deferred_symbol_timeframes.
Next live timing comparison should check cache_flush_seconds, deferred_symbol_timeframes, aggtrade_network_calls, aggtrade_cache_hits, and live_ohlcv_cache_gap.
```

P180 interrupt handling status:

```text
Ctrl+C is now handled at the command wrapper and top-level main boundary.
Expected behavior: no traceback, exit code 130, short message that the command was stopped by the user.
Live's internal KeyboardInterrupt cleanup remains the preferred path when the interrupt lands inside the live loop.
```

P181 reactive rollout status:

```text
Reactive migration plan: keep anomaly decision logic synchronous; make ingestion/scheduling event-driven through narrow data-source interfaces.
Step 1 completed: ticker radar now reads through LiveTickerSnapshotSource. Default source is RestLiveTickerSnapshotSource, so behavior remains REST-backed.
ticker_radar_snapshot and ticker_radar_failed now include source id. This is the first provenance hook for future WS ticker shadow/primary mode.
Next step should be a LiveScheduler/scan-reason seam or WS ticker shadow source, not WS aggTrade trading data yet.
```

P182 reactive rollout status:

```text
Step 2 completed: current live batch selection is now represented as LiveSymbolBatchSelection.
Behavior remains rest_round_robin_scheduler with the same active/radar/inactive composition.
symbol_batch_selected now includes scheduler_source and scan_reason_by_symbol, preparing for a future event-driven scheduler without changing anomaly evaluation.
No symbols should be silently dropped by future scheduler modes; queued/waiting reasons must stay explicit.
```

P183 reactive rollout status:

```text
Step 3 completed: live ticker radar now defaults to Binance WS !ticker@arr through BinanceWsAllTickerSnapshotSource.
No REST fallback is used while live_ws_ticker_enabled=true. If WS is not ready/stale/broken, ticker_radar_failed is emitted with source=binance_ws_all_ticker and radar promotions pause.
REST ticker source remains available only through explicit --live-ws-ticker-enabled false.
This changes scheduling/wake-up transport only; anomaly signal logic, subminute candles, OI/mark, and order path remain unchanged.
Next live validation must inspect ticker_radar_failed, ticker_radar_snapshot source, radar promotion counts, and whether WS not-ready/stale causes unacceptable blind periods.
```

P184 reactive rollout status:

```text
Step 4 implemented: subminute precise scan now has a primary Binance WS aggTrade buffer for active/ticker-radar watch symbols.
REST aggTrade is no longer the primary subminute path while live_ws_aggtrade_enabled=true; it is used only as explicit missing-range backfill with ws_aggtrade_frame_read diagnostics.
WS aggregate trade id gaps are treated as data holes and must be backfilled before a requested interval is considered covered.
Local validation passed compile/smoke, but the current environment still cannot resolve fstream.binance.com, so real WS freshness/throughput is UNKNOWN until a live run on the trading host produces connected ws_aggtrade events.
Next validation should compare aggtrade_network_calls and ws_aggtrade_frame_read.status over a 10-30 minute live sample with real DNS/connectivity.
```

P187 operator heartbeat status:

```text
P187 is PROPOSED against P186 current code.
It restores a compact human live status line while keeping P186 detailed WebSocket diagnostics in artifacts.
Expected console style is superseded by P201: `5.0s · 99.4% · аномалии 104 · активно 2/6 · позиции 1/2 · PNL 4.60% · ticker ok · flow ok`.
Routine WS counters stay out of the operator heartbeat; only short WS issue suffixes are appended when attention is required.
No trading logic, signal filters, order path, fill/stop handling, or PnL accounting changes.
```

P189 ticker-radar degraded source status:

```text
Status: PROPOSED, commit UNKNOWN.
The Windows live start showed WS DNS failure for fstream.binance.com before the first cycle.
Patch P189 keeps WS ticker as primary but allows explicit REST ticker-radar degradation when the primary WS ticker source fails.
The degradation is visible through ticker_radar_primary_source_failed, ticker_radar_source_degraded, ticker_radar_snapshot.source_status=degraded_rest_fallback, and live_cycle_summary.ticker_radar_status.
No OHLCV/aggTrade full-scan fallback is restored; if both WS and REST ticker sources fail, or if all ticker snapshots are missing, live refuses/pauses instead of hiding the problem.
Next validation: run live again and inspect live_events.csv for ticker_radar_source_degraded plus nonzero ticker_radar_startup_ready.ok_count.
```

### 2026-05-13 - P190 proposed: default WS aggTrade backfill budget widened

Status: PROPOSED. Commit: UNKNOWN.

Reason: after P189, degraded REST ticker discovery can start live when WS ticker DNS is broken, but the 300000 ms aggTrade backfill cap can still miss the last fresh 5m/30s setup scan when WS aggTrade is unavailable/cold. P190 raises the default cap to 360000 ms and exposes degraded ticker status in the console line.

Validation target: inspect `live_events.csv` for `ticker_radar_source_degraded`, `ticker_radar_snapshot.source_status=degraded_rest_fallback`, `ws_aggtrade_frame_read.backfill_max_ms=360000`, low `signal_entry_ws_aggtrade_pending` count, and acceptable `signal_scan_seconds` / `aggtrade_network_calls`.


### 2026-05-13 - P191 proposed: distinguish aggTrade flow connecting vs REST degraded vs pending

Status: PROPOSED. Commit: UNKNOWN.

Reason: the operator line `WS: flow подключается` can persist for minutes when Binance aggTrade WS is down, but current logic may still evaluate entries through explicit bounded REST aggTrade backfill. The previous UI state is too ambiguous for live safety.

Validation target: after applying P191, inspect console and `live_events.csv`: `WS: flow REST` means degraded REST backfill is covering requested flow windows; `WS: flow pending` means entries can be missed; `live_cycle_summary.ws_aggtrade_effective_source` must match the console state.


### 2026-05-13 - P192 proposed: fix aiohttp WS DNS resolver path and expose DNS in operator line

Status: PROPOSED. Commit: UNKNOWN.

Reason: live artifacts showed REST ticker discovery working but both aiohttp WebSockets failing with `ClientConnectorDNSError: Could not contact DNS servers`. The next clean fix is to force aiohttp WS connections through `ThreadedResolver` / OS `getaddrinfo`, then surface the remaining DNS cause directly in the inline live status.

Validation target: after applying P192, run live and check whether `WS: ticker REST/dns` disappears. If it remains, run OS-level DNS checks for `fstream.binance.com`; code is then reporting a real local DNS/network problem rather than masking it.


## 2026-05-13 — P193 proposed: Binance futures WS market route

Current commit: UNKNOWN.

A post-P192 live smoke no longer shows DNS failures, but ticker WS remains `connected` without messages and discovery falls back to REST. The likely current root cause is stale unrouted Binance futures WebSocket URLs for market streams. Proposed P193 routes ticker and aggTrade streams through `/market`, matching Binance's migrated USD-M futures WebSocket structure.

Next check: run a short live smoke and verify ticker discovery uses `binance_ws_all_ticker`, aggTrade effective source is mostly `ws`, and REST degraded events disappear except during real transport failures.


## 2026-05-13 — P194 proposed: WS ticker startup seed and anomaly heartbeat counter

Current commit: UNKNOWN.

A post-P193 live smoke showed ticker discovery on the primary WS route, but the all-ticker cache warmed from roughly 100/525 to 524/525 over the first seconds. Proposed P194 seeds the WS ticker cache once from REST at startup with explicit `rest_startup_seed.*` labels, avoiding the initial blind spot without hiding the source. The inline heartbeat should stop using audit row count as `события` and instead show cumulative detected ticker-radar anomaly promotions.

Next check: run a short live smoke and verify `ticker_radar_startup_seeded`, low/no initial `ticker_radar_missing_fields` spam, `аномалии` in the console, and transition from `primary_seeded_rest` to primary WS after real stream updates.

## 2026-05-14 - live health 20260513_202255

Status: analyzed.

```text
Ticker discovery health is good: startup REST seed is explicit, then primary WS snapshots are ok for 525/525 symbols with no ticker failures.
Trading funnel is alive but produced no trades: 1,626 ticker promotions, 14,281 due/evaluated timeframe rows, 0 fetch failures, 612 category rejects, 0 category_selected.
Main no-trade reason is strict runner_oi_confirmed filtering, especially mark basis below 0.3% (561/612 category rejects). OI rejected only 3 cases.
aggTrade WS health is the weak point: source stayed connected, but reads were partial/stale/not_subscribed rather than covered, causing 14,278 explicit REST gap-backfill reads and 18,357 aggTrade REST network calls.
This is honest data handling, not hidden fallback, but it means the WS migration has not yet delivered the expected speed/coverage improvement.
Next patch should improve WS coverage accounting around no-trade edge intervals, id-gap holes, subscription warm ranges, and the small not_subscribed race.
```

## 2026-05-14 - P195 live health patch

Status: PROPOSED.

```text
Implemented the first fix for 20260513_202255 health issues.
aggTrade WS coverage now follows active subscription coverage instead of first/last trade edges, so no-trade edge intervals should stop triggering REST backfill.
True aggregate trade id gaps remain strict holes and still require explicit backfill.
Backfill ranges, including empty REST responses, extend coverage so the same historical pre-subscription window is not repeatedly fetched.
Command-level KeyboardInterrupt now calls runner.shutdown(reason=command_keyboard_interrupt) before the common wrapper prints the human stop message.
Next validation: run 10-30 minutes live and compare ws_aggtrade_frame_read.status covered/partial/stale/not_subscribed, aggtrade_network_calls, ws_aggtrade_backfill_reads, signal_scan_seconds, live_ohlcv_cache_gap, signal_scan_empty_ohlcv.
```

## 2026-05-14 - Backtest/live parity risk from uploaded ZIP

Status: ANALYZED. Commit: UNKNOWN.

```text
Code parity is not yet proven. Live and backtest duplicate the forming-setup signal path.
Main suspected correctness issue is backtest-side: first raw candidate inside an HTF setup consumes that setup/cooldown before later OI/mark/category/entry filters run, while live can still accept a later LTF decision in the same setup.
runner_oi_confirmed needs strict as-of freshness parity for OI and mark basis; live rejects stale context, backtest must not accept stale context under the same production profile.
Next step should be a parity harness/report over the exact latest live window after 2d cache warmup, not a generic PnL comparison.
```

## 2026-05-14 - P196 applied locally: stricter backtest/live parity

Status: PROPOSED. Commit: UNKNOWN.

```text
Backtest forming HTF from LTF collection now keeps all valid LTF decision candidates inside a setup instead of consuming the setup on the first raw pump-flow candidate.
runner_balanced now rejects stale derivatives context when mark basis is part of the profile; runner_oi_confirmed now forces oi_status=ok.
Live category_selected diagnostics now include selected OI-change and mark-basis value/status/age so live/backtest parity can compare accepted rows, not only rejects.
Expected impact: more candidate rows, potentially more late-in-setup signals, and stricter rejection of stale OI/mark context. This should make the backtest less cosmetically optimized and closer to live.
Next validation: run a 2d cache-backed parity sample after the current live run and compare live window decision/reject/select rows before treating PnL as meaningful.
```

## 2026-05-14 - P197 applied locally: broad category statistics

Status: PROPOSED. Commit: UNKNOWN.

```text
Current confirmed live-candidate category remains runner_oi_confirmed only.
Backtest now supports broad discovery while tagging each signal/trade by the strongest matching profile: runner_oi_confirmed, runner_flow, runner_reclaim, runner_balanced, or discovery.
The new anomaly_profitability_by_category.csv artifact is intended to find candidate categories without suppressing broad trades.
Next validation should inspect category counts and expectancy after the 2d parity run; do not promote a category to live until it survives wider-period robustness checks.
```

## 2026-05-14 - P198 applied locally: backtest candidate speed prescreen

Status: PROPOSED. Commit: UNKNOWN.

```text
The 30d broad run became slow because P196 correctly evaluates all LTF decision candles inside each forming setup, especially 1m/5s.
P198 adds rolling baseline medians and a cheap cumulative quote/trade flow prescreen before expensive row construction. It uses the same thresholds as the row builder and should only skip rows that would have returned None.
This is a speed optimization, not a fallback or strategy change. Remaining larger speedups should come from vectorized event detection and/or parallel symbol collection after parity is validated.
```

## 2026-05-14 - P199 applied locally: live category expansion

Status: PROPOSED. Commit: UNKNOWN.

```text
Live now defaults to runner_oi_confirmed, runner_flow, runner_reclaim, and runner_balanced; discovery remains research-only and is not traded.
Category priority is TF-specific based on the 30d broad category table.
Artifacts include category_contract=live_category_overlay_v1_no_prior_fast_fade to make the current known gap explicit: live does not yet enforce the backtest 72h prior_fast_fade exclusion.
Next validation: short shadow/live run and inspect category_selected / position_opened category ids, per-TF selected category mix, and whether expanded categories increase low-quality entries.
```

## 2026-05-14 - P200 proposed: live prior_fast_fade parity filter

Status: PROPOSED. Commit: UNKNOWN.

```text
Live now applies max_prior_fast_fade_count_72h for runner_oi_confirmed, runner_flow, runner_reclaim, and runner_balanced instead of merely documenting the gap.
The filter is computed from local cached candidate history: the 72h lookback start and internal candles must be covered, but the trailing cache lag before the live decision is ignored and exposed as ignored_tail_ms / effective_cache_end_timestamp_ms.
Unavailable filter data rejects the category with reject_prior_fast_fade_filter_unavailable; positive prior fast-fade history rejects with reject_prior_fast_fade_72h.
Next validation: short shadow/live run and inspect category_rejected/category_selected payloads for prior_fast_fade_count_72h, ignored_tail_ms, effective_cache_end_timestamp_ms, and whether serial fast-fade symbols disappear without making all categories unavailable.
```

## 2026-05-14 - P201 proposed: live WS healthy percentage

Status: PROPOSED. Commit: UNKNOWN.

```text
Live heartbeat now includes cumulative `соединение N%`, measured by wall-clock time between health samples.
A cycle is healthy only when ticker radar is primary WS and flow WS, if it has targets, is connected/subscribed without coverage pending or REST backfill.
Network-connectivity waits are sampled as unhealthy time.
Normal ticker-radar `not_due` cycles now inherit the last attempted ticker-radar health state instead of being counted as unhealthy.
`ticker_radar_status=ok` from `binance_ws_all_ticker` now maps to primary WS health; REST `ok` does not.
Expected startup bootstrap is counted as healthy for the first 180 seconds: `ticker seed` and bounded startup `flow gap REST` do not force the connection percentage to begin at 0%.
Non-bootstrap REST fallback, flow pending, subscription mismatch, and network errors still count as unhealthy connection time.
AggTrade subscription mismatch counts as unhealthy only when subscribed targets are fewer than requested targets; extra still-active old subscriptions are overhead, not a lost connection.
Heartbeat format is now `{seconds}s · {connection_pct}% · аномалии N · активно current/seen · позиции open/closed · PNL X% · {connection_status}`, with no `live` prefix.
Heartbeat metric columns are compact width 9 and right-aligned; connection status is always non-empty and left-flowing without fixed padding.
Connection status is split into healthy labels (`ticker ok`, `ticker ok · flow ok`) and degraded labels (`ticker seed`, `ticker REST`, `ticker нет`, `flow pending`, `flow REST`, `flow подписка`, `flow gap REST`).
Heartbeat single-line rendering now uses ANSI clear-line instead of padding-only carriage return, and the line is highlighted when at least one position is open.
The same health fields are written to live_cycle_summary for artifact validation.
This is diagnostics/operator UX only; trading logic is unchanged.
Next validation: short live smoke and verify heartbeat stays on one line, changes color with an open position, and live_events.csv has ws_healthy/ws_health_reason/ws_health_pct changing when ticker seed, REST fallback, flow pending, or flow REST backfill occurs.
```

## 2026-05-14 - P202 proposed: reduce live cache flush stalls

Status: PROPOSED. Commit: UNKNOWN.

```text
Longest recent live run analyzed: .output/results/live_anomaly_runs/20260514_104416.
It ran 500 cycles, 3317 observed health seconds, 0 positions.
Main safe bottleneck was synchronous parquet cache flush in the hot loop: cache_flush_seconds summed ~904s, p95 ~13.2s, max ~31.9s.
P202 lowers default non-forced live_ohlcv_cache_flush_max_symbol_timeframes from 20 to 4.
This is infrastructure/runtime only: signal selection, categories, entry/exit, stops, and forced shutdown persistence are unchanged.
Next validation: run 10-15 minutes live and compare cache_flush_seconds p90/p95, remaining_rows, pending_rows_before_flush, live_ohlcv_cache_flush_summary count, and memory pressure.
```

## 2026-05-14 - P203 proposed: live subscription/position safety hardening

Status: PROPOSED. Commit: UNKNOWN.

```text
Live code review found two correctness risks.
First, aggTrade WS subscriptions were considered active immediately after sending SUBSCRIBE, before Binance ACK; this could overstate WS coverage for newly promoted symbols or after subscription errors.
Second, position amount parsing could return zero when CCXT normalized contracts were absent/zero but Binance info.positionAmt was present.
P203 tracks pending WS subscribe/unsubscribe request ids and only marks symbols subscribed after ACK. It also uses info.positionAmt when normalized contracts are zero.
Trading thresholds, category selection, entry/exit formulas, and cache policy are unchanged.
Next validation: compile, then short live smoke and inspect ws_aggtrade_subscription_target, not_subscribed/covered transition, ws_aggtrade_frame_read partial/backfill rows, and any position amount readouts during a controlled position/order smoke.
```

## 2026-05-14 - P204 proposed: live aggTrade REST gap backfill optimization

Status: PROPOSED. Commit: UNKNOWN.

```text
Live REST aggTrade backfill now has a shared process-memory raw range cache and coalescing planner.
Both WS uncovered gaps and non-WS subminute raw fetches reuse the same 20m TTL cache.
Nearby missing ranges are coalesced and padded by 60s within the requested scan window, so one REST request can serve multiple TF consumers and later cycles.
This is data-access/runtime only: missing coverage remains explicit, max WS backfill budget still gates whether a signal is evaluated, and no strategy thresholds or execution rules changed.
Next validation: run 10-15 minutes live and compare aggtrade_network_calls, aggtrade_process_cache_hits, aggtrade_coalesced_missing_ranges, aggtrade_rest_fetched_ms, ws_aggtrade_backfill_reads, signal_scan_seconds, and signal_entry_ws_aggtrade_pending_count.
```


## 2026-05-14 - Live execution reliability state after P190 proposal

Status: PROPOSED. Commit: UNKNOWN.

```text
P190 targets the main remaining execution reliability gap: ambiguous state-changing create_order retries and pre-stop exposure cleanup.
Normal successful order placement adds no extra REST calls. Reconciliation only runs after an ambiguous transport/order-mutation failure. Validation/exchange errors remain hard failures, not network fallbacks. The next reliability gaps to reach 10/10 are startup recovery for existing exchange positions/local ledger, explicit account-mode preflight, and optional exchange-native TP protection if TP1 miss risk becomes material.
```

---

## 2026-05-14 - P206 shutdown reconcile scope

Status: PROPOSED. Commit: UNKNOWN.

```text
Ctrl+C shutdown should no longer look frozen before cleanup starts. The first interrupt records live_shutdown_started and logs the bounded forced-reconcile scope. Forced orphan-order reconciliation on shutdown/max-cycles is scoped to symbols with exchange order activity during the current live run, plus currently open/opening active symbols, instead of fetching open orders for every symbol in the live universe. This changes shutdown/runtime behavior only; signal selection, category logic, order entry, fills, stops, and exits are unchanged.
Next validation: run live, stop with one Ctrl+C, and verify live_events.csv contains live_shutdown_started and orphan_order_reconcile_started with symbols_to_check equal to the number of run-trade symbols, not the whole universe.
```

## 2026-05-14 - P207 proposed DANGER: retryable dependencies and cold coverage

Status: PROPOSED. Commit: UNKNOWN.

```text
Why 72h exists: max_prior_fast_fade_count_72h=0 is a red-flag exclusion inherited by the confirmed runner categories. It tries to avoid symbols that recently produced a fast fade after an apparent pump setup. It requires enough historical context to prove absence of those prior fast fades; after P205 this context is levels-timeframe history, not 72h of 5s/15s/30s executable tape.

P207 changes live mechanics in three places. First, taker-buy share parity now matches backtest by averaging only valid taker/share rows with quote_volume > 0 while preserving explicit invalid-data rejects when there are no valid rows. Second, temporary category data dependencies such as prior-fast-fade coverage, mark context unavailable/stale, or OI context unavailable/stale no longer consume the decision candle; live retries the same decision until it becomes stale or receives a final reject/selected category. Third, DANGER default cold coverage adds 5 precise inactive symbols per cycle for subminute live, clearly labeled in artifacts and capped by max_precise_scan_symbols_per_cycle if configured. Set inactive_scan_slots_per_cycle=0 to disable cold coverage.

Next validation: run a short dry/shadow live and inspect signal_scan_retryable_dependency_blocked counts, repeated decision timestamps until stale/final, valid_taker_share_rows/total_taker_share_rows in taker rejects, symbol_batch_selected scan modes, and API pressure metrics before any long real-order run.
```


## 2026-05-14 - P208 proposed: health-gated cold coverage

Status: PROPOSED. Commit: UNKNOWN.

```text
P208 narrows the P207 DANGER cold coverage behavior. Cold coverage now runs only when the cumulative WS health percentage is strictly above 95% and the runner has no active symbols, no opening positions, and no open positions. When health is <=95% or active/position state exists, cold coverage is gated off and artifacts record inactive_cold_coverage_gate_reason plus health/threshold fields. The old heartbeat label "DANGER обход ~Ns" meant estimated full-universe cold coverage cycle time; P208 replaces it with an explicit cold full-cycle label only when cold coverage is actually running, otherwise cold off <reason>.

Next validation: short live smoke and check symbol_batch_selected/live_cycle_summary for inactive_cold_coverage_gate_reason=ws_health_below_95pct during startup, then DANGER cold coverage only after health >95% and idle state.
```


## 2026-05-14 - P209 DANGER adaptive cold coverage controller

Status: PROPOSED. Commit: UNKNOWN.

```text
Cold coverage is now an adaptive idle/audit scanner rather than a fixed slot count. It is hard-off for open/opening positions and active-due symbols, soft-reduced by active-waiting symbols, and scored by WS health, scheduler heartbeat EWMA, and REST/cache pressure. This should improve missed-symbol/parity diagnostics during healthy idle periods without competing with active execution, but it must be monitored by cold score, selected slots, scheduler_cycle_seconds, aggtrade_network_calls, REST fetched span, and pending coverage gaps.
```

## 2026-05-14 - P210 DANGER local guard / flow radar / micro-cache metrics

Status: PROPOSED. Commit: UNKNOWN.

```text
P210 keeps discovery improvements explicit and measurable. Live entry now uses local open/opening symbol memory as the pre-entry duplicate guard instead of fetching exchange position amount before every signal; startup cleanup and post-fill exchange position verification remain the safety boundary. The all-ticker WS stream already contains trade-count and quote-volume deltas, so DANGER cheap flow radar can promote early flow-only watch symbols without REST. WS aggTrade rolling buffers are widened for active/radar/watch/current cold symbols only, not for the full universe. Cold coverage usefulness is measured by cold scanned/evaluated/retryable/signal/order counters in live_cycle_summary. Next validation: 24h live, then compare cold_before_radar_count manually from ticker_radar_promoted source, cold_signal_count/order attempts, scheduler latency, and post-fill mismatch events.
```

## P211 proposed state
- Added proposed offline DANGER experiment for runner/fader pre-pump separability.
- Current commit: UNKNOWN.
- Next check: run on 30d backtest artifacts and inspect whether pre-anomaly HTF features separate runner/fader without symbol/month leakage.

## P212 proposed state
- P211 standalone runner/fader prepump study is now proposed as a default backtest artifact integration, not only a manual script.
- Backtest artifacts should include `runner_fader_prepump_context.csv`, feature separation, label/status summaries, run config, and `runner_fader_prepump_run_status.csv`.
- Current commit: UNKNOWN.
- Next validation: run 30d backtest and inspect runner/fader feature separation before considering any live filter.

## P214 applied locally state
- Live now maintains `symbol_context_snapshot.csv` as a cache-only rolling context table.
- Prior-fast-fade category checks read the prepared snapshot instead of fetching/computing 72h context inside precise scan.
- Missing/stale snapshots remain explicit retryable category dependencies; there is no synchronous context fallback during precise scan.
- Current commit: UNKNOWN.
- Next validation: run a short dry live and inspect `symbol_context_snapshot.csv`, `symbol_context_snapshot_updated`, `symbol_context_snapshot_*` fields in `live_cycle_summary`, and `reject_prior_fast_fade_filter_unavailable` reasons.


## P215 applied locally state
- Top-growth snapshots now have a mandatory missed-pump visibility artifact shape.
- Standalone top-growth can join against a live run with `--visibility-events-csv path/to/live_events.csv` and writes radar/warm/precise/category/execution visibility columns.
- Missing or invalid live event input remains explicit in `visibility_source_status` and `not_scanned_reason`; no live visibility is fabricated.
- Current commit: UNKNOWN.
- Next validation: run top-growth for a completed hour with a real live_events.csv and inspect whether top movers show the first missing stage clearly.


## P216 applied locally state
- Live now has a latency SLA controller for optional scan expansion.
- Active and already-promoted radar precise scans remain protected; optional cold coverage is cut to zero when active/radar due-scan p95 breaches the configured SLA.
- Warm-watch candidates that reached the precise threshold are deferred with explicit `warm_watch_precise_deferred_latency_sla` events while SLA is breached, not silently dropped.
- Current commit: UNKNOWN.
- Next validation: run short dry live and inspect `latency_sla_status`, `latency_sla_due_scan_p95_seconds`, cold gate reason, and `warm_watch_deferred_count` before widening cold/warm budgets.

## P217 applied locally state
- Live can optionally use a validated P212 `runner_fader_prepump_feature_separation.csv` as a warm-watch priority scorer only.
- The scorer is disabled by default and requires an explicit profile CSV; missing or unstable profiles fail startup instead of silently disabling.
- `symbol_context_snapshot.csv` contract is now v2 and can include cache-only spot/flow prepump features for 30m/1h/2h/6h windows.
- The score is bounded/additive and only affects warm/radar priority ordering; it does not reject categories, entries, or execution.
- Current commit: UNKNOWN.
- Next validation: run a 30d backtest with P212 artifacts, enable scoring in a dry live with the feature separation CSV, then compare warm_watch_precise_promoted symbols against later top_growth without using the score as an entry filter.


## P218 proposed state
- Live context snapshot maintenance is now optional work after the critical scan path, not before batch selection/precise scan.
- Snapshot updates are SLA-gated, wall-clock budgeted, and cursor advancement is tied to processed symbols only.
- Snapshot freshness uses an effective runtime window derived from universe refresh throughput, with configured freshness as a minimum.
- Warm-watch aggTrade micro-cache subscriptions are capped by score/recency; active, opening, current batch, and promoted radar targets remain uncapped by this warm cap.
- Current commit: UNKNOWN.
- Next validation: short dry live and inspect `symbol_context_snapshot_skipped`, `symbol_context_snapshot_updated`, `ws_aggtrade_subscription_target`, and `live_cycle_summary` for budget/SLA/cap fields before changing scan budgets.

## P231 proposed state
- Live heartbeat duplication was traced to inline status rendering: long heartbeat strings can wrap in PowerShell/narrow terminals, while the logger cleared only the current physical row.
- The status logger now tracks rendered row count and clears all rows occupied by the previous heartbeat before drawing the next one.
- This is console-output-only; live trading, delayed replay, Telegram, artifacts, and scan logic are unchanged.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in the same PowerShell terminal and confirm the heartbeat line is replaced instead of concatenated.


## P224 proposed state
- Delayed replay now performs cache-only frozen-decision recomputation instead of only artifact outcome auditing.
- It captures category selected/rejected decisions plus execution rejects and reports whether the frozen decision would select/enter under the signal builder.
- It still cannot see symbols/windows that live never scanned; those remain a separate scheduler/top-growth visibility problem.
- Replay does not fetch missing mark/OI context; unavailable exchange context is an explicit replay reject to avoid stealing live resources.
- Current commit: UNKNOWN.
- Next validation: run short dry live with `--delayed-replay-enabled true --delayed-replay-delay-seconds 60 --delayed-replay-min-idle-seconds 10`, then inspect `delayed_replay_results.csv`, `live_events.csv`, and Telegram events for any `*_replay_would_enter` mismatch.


## P226 proposed state
- Delayed replay decision recompute still uses only cached windows ending at decision_timestamp_ms; outcome windows start after decision and are labeled as post-decision.
- Frozen live-signal snapshot fallback is not equivalent to strict backtest-like recompute. P226 separates evidence with strict_recompute_signal and frozen_signal_snapshot_used.
- Telegram keeps the alert but uses different wording for snapshot fallback, with a caveat that strict candle recompute was blocked by disabled mark/OI fetch.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect result rows where recompute_source=frozen_live_signal_snapshot and verify no *_replay_would_enter mismatch is emitted for those rows.

## P227 proposed state
- Delayed replay now queues final live decisions instead of every intermediate category-profile reject.
- A no-signal anomaly is represented by one `all_categories_rejected` case after the category loop completes, so prior profile rejects inside a later selected signal cannot create false “live ignored entry” alerts.
- Execution rejects now include duplicate/opening symbol, stop cooldown, not-yet-closed signal, and stale signal in the delayed replay queue.
- Strict cache-only recompute and frozen live-signal snapshot evidence are separated in result columns and operator alert kind.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect that `delayed_replay_queue.jsonl` has no raw `category_rejected` source cases and that TG alerts distinguish `strict_replay_ignored_entry` from `frozen_signal_snapshot_only`.


## P228 proposed state
- Delayed replay result CSV now preserves `recompute_source`, so strict cache-only recompute rows and frozen live-signal snapshot rows can be separated without reading live_events.json details.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect `delayed_replay_results.csv` for both `recompute_status` and `recompute_source`.

## P229 proposed state
- Delayed replay now prefers immutable live decision snapshots over later cache reconstruction when the snapshot is available.
- Snapshot replay captures baseline/setup/entry rows through `decision_timestamp_ms` plus frozen mark/OI/prior-fast-fade context, so replay can recompute without REST fetch and without depending on cache that became fuller after the live decision.
- Cache-only delayed replay remains a fallback only for older/no-snapshot cases.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect `delayed_replay_queue.jsonl` for `decision_snapshot_json`, then verify `delayed_replay_results.csv` rows show `recompute_source=immutable_live_decision_snapshot_recompute` and `decision_snapshot_status=ok`.


## P226 proposed state
- Delayed replay decision recompute still uses only cached windows ending at decision_timestamp_ms; outcome windows start after decision and are labeled as post-decision.
- Frozen live-signal snapshot fallback is not equivalent to strict backtest-like recompute. P226 separates evidence with strict_recompute_signal and frozen_signal_snapshot_used.
- Telegram keeps the alert but uses different wording for snapshot fallback, with a caveat that strict candle recompute was blocked by disabled mark/OI fetch.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect result rows where recompute_source=frozen_live_signal_snapshot and verify no *_replay_would_enter mismatch is emitted for those rows.


## P233 proposed state
- Live heartbeat is now a fixed-width five-line operator block with rows `LIVE`, `FEED`, `PUMP`, `RPLY`, and `RISK`.
- Delayed replay backlog is shown as `RPLY pnd <n>` or `RPLY pnd off` without changing replay logic.
- Inline status clearing counts explicit newline rows and wrapped terminal rows to avoid duplicated-looking PowerShell output.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in PowerShell and confirm the block redraws cleanly with one blank line before it.


## P234 proposed state
- Live heartbeat is now a Russian grouped operator block: `Соединение`, `Рынок`, `Торговля`, `Контроль`.
- The block shows runtime, stability, ticker/flow state, anomaly events, active symbols, positions, orders, delayed replay backlog and coverage/guard state with fixed-width cells.
- `Replay` is shown as `Повтор`; cold coverage is shown as `Покрытие`.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in PowerShell and confirm the multiline block redraws cleanly and does not wrap/duplicate.


## P235 proposed state
- Russian live heartbeat grouping is refined: `Рынок` shows runtime, anomaly events and active-symbol load; `Торговля` shows PNL, positions and orders.
- This is console-output-only; delayed replay, Telegram, artifacts, scan and order logic are unchanged.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in PowerShell and confirm the block redraws cleanly with the new grouping.

## P236 proposed state
- Live retry warnings from the exchange client are routed through the live status logger, so they terminate the inline heartbeat before printing and appear as highlighted alert lines instead of being glued to the status block.
- `ExchangeConnectivityError` handling now prints the network/API failure as a highlighted alert with a blank line before it, records the first degraded reason, and keeps retrying the Telegram degraded alert every 60 seconds while the outage persists.
- On recovery, live writes `network_recovered` with the original reason and sends a Telegram recovery note, so a DNS-wide outage that also blocks Telegram still leaves a later operator notification path.
- Current commit: UNKNOWN.
- Next validation: run a short live smoke with blocked DNS/API and confirm console separation, `network_degraded_telegram_alert_enqueued`, `telegram_async_send_failed` if Telegram is unreachable, and Telegram delivery/recovery notification once connectivity returns.

## 2026-05-15 — P245 proposed

P245 addresses the live backlog loop directly: radar/warm-watch candidates are no longer allowed to accumulate indefinitely and keep latency SLA breached. When the queue is under pressure, live keeps the strongest candidates, expires stale backlog, and drops weak tail with explicit `candidate_dropped_latency_pressure` / `candidate_expired_backlog_stale` events.

No execution safety was loosened: stale, drift, RR, actual-risk, position, fill and stop guards are unchanged. No new CLI flags were added; queue limits are code-reviewed policy constants.

Current commit: UNKNOWN.
Next validation: run a short live smoke and inspect `candidate_queue_*` fields in `live_cycle_summary` / `symbol_batch_selected`, plus `candidate_dropped_latency_pressure`, `candidate_expired_backlog_stale`, `warm_watch_precise_deferred_latency_sla`, and `latency_sla_status` counts.

## 2026-05-15 — P246 proposed

P246 adds an internal adaptive precise-scan budget on top of P245. During latency/backlog/runtime pressure, active/opening symbols keep priority and radar precise scans are capped to the strongest fresh candidates instead of scanning a wider stale queue.

No execution guards are loosened. No new CLI flags are introduced. Current commit: UNKNOWN.

Next validation: run a short live smoke and compare `adaptive_precise_budget_status`, `adaptive_precise_budget_radar_slots`, `latency_sla_status`, `warm_watch_precise_deferred_latency_sla`, and `candidate_queue_*` versus the previous 5h run.

## 2026-05-15 — P239 proposed

Live prior-fast-fade context must not depend only on a post-scan optional snapshot. P239 proposes default startup backfill + startup snapshot computation for 72h+baseline levels-timeframe context, with explicit tiny-gap tolerance (`min_coverage_ratio=0.995`, `max_gap_candles=2`) and no subminute context backfill. Commit: UNKNOWN.
---

## 2026-05-15 — P240 proposed

After P239, unavailable prior-fast-fade context should be rare, but it must not be treated as a market/category rejection when it still happens. P240 keeps such decisions retryable: no `category_rejected` artifact and no delayed-replay final-reject case are emitted for retryable category dependencies; `signal_scan_retryable_dependency_blocked` carries the blocked categories and context reason.

Next check: run a short live smoke and verify that `reject_prior_fast_fade_filter_unavailable` no longer dominates `category_rejected`; remaining unavailable context appears as retryable dependency until stale/expiry.


## 2026-05-15 — P241 proposed

Rolling symbol-context snapshots now prioritize symbols that can unblock near-term decisions: open/active symbols first, then retryable dependency requests, then ticker-radar/warm-watch symbols, then normal universe round-robin. This keeps live context maintenance cache-only but stops spending the tiny post-scan budget mostly on random inactive symbols while a hot symbol is waiting for prior-fast-fade context.

Current commit: UNKNOWN.
Next validation: after P239-P241, run a short live smoke and inspect `symbol_context_snapshot_updated.priority_reason_counts`, `signal_scan_retryable_dependency_blocked`, and whether repeated prior-fast-fade unavailable cases for the same hot symbols resolve before stale/expiry.


## 2026-05-15 — P242 proposed

Live closed-hour top-growth/missed-pump visibility is no longer only a standalone post-run command. P242 proposes a default-on incremental live audit that scans closed 1h exchange candles in bounded per-cycle chunks and writes top/status/visibility artifacts into the current run directory using the same `live_events.csv` as visibility evidence.

Current commit: UNKNOWN.
Next validation: run live across at least one UTC hour close; verify `top_growth_index.csv` gets a row, `top_growth_status_*.csv` contains all live-universe symbols, and `missed_pump_visibility*.csv` is populated when top movers cross the threshold.

## 2026-05-15 — P243 proposed

P242 needed one correction before live smoke: closed-hour top-growth should not add new CLI flags or run during latency pressure. P243 makes the audit always-on, fixed-policy and latency-gated: no `--live-top-growth-*` options, no ticker fallback, no processing while optional scans are blocked by SLA.

Current commit: UNKNOWN.
Next validation: run live across an hour close and verify `live_cycle_summary.live_top_growth_status=skipped_latency_sla` during pressure, then `processing/completed` only when optional work is allowed.

## 2026-05-15 — P244 proposed

P244 loosens only discovery/market-shape gates and data-dependency handling, not execution safety. Live defaults become less likely to reject borderline early pump candidates: start flow 4x/4x, retention 65%, verticality 0.20, prior whipsaw cap 0.75, runner prior-fast-fade cap 1, taker-buy delta cap 0.35, and slightly lower runner mark/OI thresholds. `max_entry_price_drift_pct` remains 0.003.

Unavailable taker-buy, mark-basis, and OI context are no longer final category rejects; they are retryable dependencies until context appears or the decision becomes stale. P239 startup-context switches are removed from CLI/config plumbing so startup context backfill is default-on policy rather than an operator toggle.

Current commit: UNKNOWN.
Next validation: run a short live smoke and inspect that category_rejected is dominated by real market reasons, while `signal_scan_retryable_dependency_blocked` carries data-context issues without consuming decisions.

## 2026-05-16 — P252 proposed

P252 fixes a runtime `NameError: selection is not defined` in `live_cycle_summary`: P245/P246 wrote candidate queue/adaptive precise fields using the local `selection` variable after the batch-selection scope had ended. The patch stores those values on runner state when the batch is selected and uses the state fields in cycle summary. It also adds current local `HH:MM:SS` to the inline `контекст 72ч` startup backfill status line.

Current commit: UNKNOWN.
Next validation: restart live and verify startup status updates as `контекст 72ч · HH:MM:SS · кеш N/total · SYMBOL · ETA ...`, then confirm the first `live_cycle_summary` writes without NameError.

## 2026-05-16 — P253 proposed startup visibility note

Observed live startup can sit silently after `контекст 72ч · кеш 535/535` because the main heartbeat starts only after startup cache flush, symbol-context snapshot computation/write, ticker radar seed/validation, and entry into the first live loop. P253 proposes status-only instrumentation for those startup stages. Current commit: UNKNOWN.

## 2026-05-16 — P256 proposed startup visibility note

The startup `контекст 72ч · запись кеша` stage was observed to look stalled after the 72h fetch loop completed. P256 keeps the blocking healthy-start policy, but reports forced cache flush progress per symbol/timeframe with ETA and adds ETA to startup snapshot computation/readiness status. Current commit: UNKNOWN. Next validation: restart live and confirm `запись кеша N/total · SYMBOL TF · ETA ...` updates during the formerly silent flush stage.


## 2026-05-16 — P257 proposed state

- 72h context preparation keeps the healthy-start policy but flushes OHLCV cache writes in bounded chunks during backfill instead of accumulating one large final write.
- Live context reprepare is state-based and safe-gated: it can run only with zero active symbols, zero open positions, zero opening symbols, and zero tracked order-reconcile symbols. Unsafe cases are deferred with explicit artifacts.
- If real-orders context remains below readiness thresholds after a safe reprepare, live stops safely rather than continuing with degraded context.
- Current commit: UNKNOWN.
- Next validation: restart live and verify startup `запись кеша` appears in small chunks during backfill; during a long run, inspect `live_context_reprepare_deferred/started/completed` events only when context readiness degrades.


## 2026-05-15 — P247 proposed

P247 adds dependency retry cooldown on top of P245/P246. Retryable data-dependency blocks no longer re-enter precise scan every cycle while waiting for context/taker/mark/OI evidence. Cooldown symbols are prioritized by the context snapshot refresher, due-scan latency ignores intentional cooldown waits, and stale timeout emits `candidate_expired_dependency_timeout` instead of silently looping.

No execution guards are loosened. No new CLI flags are introduced. Current commit: UNKNOWN.

Next validation: run a short live smoke and inspect `signal_scan_dependency_retry_scheduled`, `candidate_expired_dependency_timeout`, `dependency_retry_cooldown_*`, `signal_scan_retryable_dependency_blocked`, `latency_sla_status`, and `warm_watch_precise_deferred_latency_sla`.


## 2026-05-15 — P248 proposed

P248 makes live initial-risk rejects explicit: `reject_entry_below_initial_stop` now records whether the computed stop came from EMA20, the structural low-based stop, or a tie, plus the stop-above-entry distance. It does not change stop calculation or allow any fallback stop substitution.

Current commit: UNKNOWN.
Next validation: run a live smoke and inspect `reject_entry_below_initial_stop` rows for `stop_source`, `risk_side`, `previous_stop`, `decision_ema20`, and `stop_above_entry_pct`.

## 2026-05-15 — P249 proposed

Review after P245-P248 found one artifact-accounting issue in P247: active dependency retry cooldowns were skipped before the local scan-summary cooldown counter, so a cooldown wait could appear as generic `skipped_not_due_count`. P249 corrects the summary classification so cooldown pressure is measurable as `dependency_retry_cooldown_skipped_count`.

Current commit: UNKNOWN.
Next validation: in the next live smoke, check that repeated dependency waits increase `dependency_retry_cooldown_skipped_count` / `dependency_retry_cooldown_skipped_cycle` rather than hiding under `skipped_not_due_count`.

## 2026-05-15 — P250 proposed

P250 changes only the operator display for default-on 72h startup context backfill. Instead of logging `контекст 72ч · кеш 1/535 · ok ... · ошибки ...` and then staying visually quiet until every 50 symbols, live now refreshes the status line for every symbol with the current symbol and ETA. Fetch failures still go to `live_events.csv`; the status line no longer shows misleading ok/error counters.

Current commit: UNKNOWN.
Next validation: start live with a cold/partial cache and verify the single startup line updates on every symbol as `контекст 72ч · кеш N/total · SYMBOL · ETA ...`.


## 2026-05-16 — P258 proposed state

- Mandatory 72h context preparation/reprepare now has Telegram start and finish notifications in addition to terminal/artifact events.
- Notifications are events-channel only and do not affect readiness decisions, cache writes, trading filters, or live execution safety.
- Current commit: UNKNOWN.
- Next validation: restart live and confirm Telegram shows `Контекст 72ч: подготовка включена` before the 72h context phase and `Контекст 72ч готов` or `Контекст 72ч не готов` after the readiness verdict.

## 2026-05-16 — P259 proposed

P259 adjusts only the operator heartbeat display. The connection block now shows stability, pulse, and data health; runtime moved back to the market block and is counted from live-loop start after mandatory 72h context preparation, not from process startup. Session top movers are rendered to 0.1%. The live status logger now clears the previous multi-line heartbeat before warnings/ordinary log messages, so an error does not leave a stale grid above the alert.

Current commit: UNKNOWN.
Next validation: run live until the first warning/retry message and verify the old heartbeat is cleared before the alert, then the next heartbeat renders once.

## 2026-05-16 — P261 proposed

P261 tightens live stop verification after the SKYAI stop visibility halt. Stops are still not trusted from the create response: verification checks open orders by order id/clientOrderId, then uses the typed exchange clientOrderId lookup before declaring the stop confirmed. Stop integrity failures now carry the affected symbol into terminal log, `live_data_integrity_error` artifact row, and synchronous Telegram halt notification.

Current GitHub head checked before patch: c0b5dfd970dabc5383cd6f493a688f41e29ba517.
Current patch status: APPLIED in GitHub head c0b5dfd970dabc5383cd6f493a688f41e29ba517.
Next validation: run a synthetic stop-verification smoke for delayed open-orders visibility, clientOrderId-only lookup confirmation, and unresolved stop halt.

## 2026-05-16 — P262 proposed

P262 adds an explicit dangerous diagnostic flag `--danger-continue-after-order-position-errors`. Default live behavior remains strict. With the flag enabled, order/position integrity failures are written to `live_order_position_integrity_error`, reported synchronously to Telegram, live cache is flushed when inside the loop, and startup/live execution continues instead of returning code 3 where possible.

Current GitHub head checked before patch: c0b5dfd970dabc5383cd6f493a688f41e29ba517.
Current patch status: PROPOSED / not applied.
Next validation: run one strict synthetic stop/order failure and one danger-mode synthetic failure to verify strict halt vs Telegram+continue behavior.

---

## 2026-05-16 — Proposed parity state after P264

```text
Current patch status: P264 APPLIED locally / UNKNOWN commit.
Backtest PnL before P264 is not live-category parity-valid because runner category thresholds were duplicated and different from live.
After P264, runner category thresholds/priority must come from research_tools/anomaly_category_contract.py in both live and backtest.
Discovery remains allowed only as an explicit backtest fallback and must be separable through pump_category_family=discovery vs live_priority.
Delayed replay must preserve the original live setup_source from immutable snapshots to avoid changing forming-HTF pace ratios during replay.
```

## 2026-05-16 — Proposed parity state after P266

```text
Current patch status: P266 APPLIED locally / UNKNOWN commit.
P266 keeps discovery as the backtest fallback but makes skipped rows carry the same category metadata as closed rows.
Backtest derivatives context fetching is widened before final category checks so runner candidates are not excluded from mark/OI enrichment simply because mark/OI was not loaded yet.
`anomaly_context_parity_report.csv` becomes the first artifact to inspect when a live-selected timestamp is missing from backtest, has `outside_pre_context_signal_universe`, or loses category metadata on an execution guard skip.
```

## 2026-05-16 — P265 proposed

P265 adds live observability for discrete signal misses. If the signal snapshot itself was executable, but the current live executable price fails the execution guard (`TP1 already reached`, price drift, invalid risk, wide risk, or collapsed RR), live still rejects the order but records `discrete_signal_snapshot_entry_missed` and sends a specific Telegram message.

Current patch status: APPLIED locally / UNKNOWN commit.
Next validation: run a short live smoke and count `discrete_signal_snapshot_entry_missed` versus normal execution rejects. If this dominates, evaluate lower-latency partial-candle/event-driven research separately; do not open by stale snapshot price.

## 2026-05-16 — P267 proposed

```text
Current patch status: P267 APPLIED locally / UNKNOWN commit.
P267 is a data-quality/parity patch only. It does not change discovery selection, runner thresholds, stop-order handling, or PnL logic.
Delayed replay snapshots must preserve `synthetic_ohlcv_bucket` so replay does not turn synthetic no-trade buckets into real flow evidence.
Live/replay setup confirmation must require enough real entry buckets; synthetic filled buckets may maintain time/price continuity but must not satisfy confirmation count.
Backtest context parity now reports OI cache/load/asof freshness separately from mark-price context so SYS-like disagreements can be traced to cache coverage/staleness instead of a generic context failure.
P263 lifecycle tests are referenced in research memory but `tests/` is absent from the current uploaded ZIP; treat P263 validation as not present in this ZIP unless tests are supplied separately.
```

## 2026-05-16 — P268 proposed

```text
Current patch status: P268 APPLIED locally / UNKNOWN commit.
P268 adds a real Binance USD-M order lifecycle smoke command. It is not a strategy signal and does not weaken live execution safety.
The command requires `--confirm-real-order-smoke`, refuses non-flat symbols or pre-existing open orders, opens one minimal market long, verifies the reduce-only STOP_MARKET via open orders/clientOrderId lookup, then cancels the stop and closes reduce-only by default.
Binance `-2013 Order does not exist` is now classified as `ExchangeOrderNotFound`, not as connectivity retry exhaustion, so stop visibility diagnostics can distinguish “order absent” from network/API failure.
Artifacts: `live_order_smoke_events.csv` and `live_order_smoke_summary.json` under the selected output directory.
```
## 2026-05-16 — P269 proposed

```text
Current patch status: P269 APPLIED locally / UNKNOWN commit.
The real Binance smoke showed a UI-visible protective stop that ordinary `fetch_open_orders`/`fetch_order`/`cancel_order` could not see or cancel.
Treat Binance protective stops as conditional/algo orders: create through `fapiPrivatePostAlgoOrder`, verify through `fapiPrivateGetOpenAlgoOrders`, and cancel through `fapiPrivateDeleteAlgoOrder`. Ordinary order lookup remains only a legacy secondary path.
After P269, the next real-order smoke must prove: `stop_verified.source` is `open_algo_orders_*` or `algo_client_order_id_lookup`, `stop_cancelled` succeeds, and final snapshot has both `smoke_open_orders_seen=0` and `smoke_algo_open_orders_seen=0`.
```

## 2026-05-16 — P270 proposed

```text
Current patch status: P270 APPLIED locally / UNKNOWN commit.
P270 extends the existing real-order smoke with an optional management lifecycle instead of adding strategy behavior: initial algo stop -> closer replacement algo stop -> old stop cancel verification -> reduce-only close while replacement stop remains active -> replacement stop cancel -> final ordinary/algo order sweep.
The old quick smoke remains unchanged unless `--replacement-stop-distance-pct` is supplied.
Next validation: run the management smoke at minimal notional and inspect `live_order_smoke_events.csv` for `stop_replacement_verified`, `old_still_open=false`, `new_still_open=true` before close, and final `smoke_algo_open_orders_seen=0`.
```
## 2026-05-16 — P271 proposed

```text
Current patch status: P271 APPLIED locally / UNKNOWN commit.
The first P270 management smoke failed before replacement because stop verification compared the raw requested floating stop price to Binance's normalized algo trigger price strictly enough to reject one valid price-precision truncation: 0.035643999999999995 -> 0.03564.
P271 keeps strict stop verification but compares trigger price with exchange-normalized precision tolerance instead of treating one displayed price unit as integrity failure.
Next validation: rerun the same management smoke and require `stop_verified`, `stop_replacement_verified`, old stop removed, replacement stop present until close, then final flat/no ordinary or algo orders.
```

## 2026-05-16 — P272 proposed

```text
Current patch status: P272 PROPOSED, commit UNKNOWN.
P271 management smoke passed the real Binance lifecycle, but the smoke summary artifact reused `_stop_order_id` after replacement and therefore reported the replacement id as `stop_order_id`.
P272 is artifact-only: `stop_order_id` remains the initial protective stop id, `replacement_stop_order_id` remains the replacement id, and `active_stop_order_id` records the last/current managed stop. No order placement, verification, cancellation, or strategy behavior changes.
Next validation: rerun compile/unit/help checks, then on the next management smoke confirm the summary ids match the event stream.
```

## 2026-05-17 — P274 proposed

```text
Current patch status: P274 PROPOSED, commit UNKNOWN.
Position management now treats TP1 as an exchange-side reduce-only limit order, not a candle-high-triggered market close.
The live monitor uses the signal entry timeframe for structural trailing and treats the pre-first-closed-LTF-candle interval as waiting, not data integrity failure.
Next validation must be a minimal real-order lifecycle smoke that verifies: stop order visible, TP1 limit order visible, TP1 cancel/cleanup works when position exits before TP1, and no ordinary/algo orphan orders remain.
```

## 2026-05-17 — P275 proposed

```text
Current patch status: P275 PROPOSED, commit UNKNOWN.
The live terminal heartbeat should behave as one pinned multi-section status grid. When any ordinary log or alert appears, the runner clears the grid, prints the message, and immediately repaints the latest grid so the operator view always ends with the current heartbeat. This is display-only and does not affect live execution, artifacts, orders, or Telegram.
Next validation: run a short live session in an interactive PowerShell terminal and confirm one self-updating grid containing Соединение/Рынок/Торговля/Контроль stays at the bottom after startup logs and any warning/error lines.
```

## 2026-05-19 — P303 proposed

```text
Current patch status: P303 PROPOSED, commit UNKNOWN.
The 20260519_064747 live run closed BAS with exchange position amount zero but wrote exit_unresolved because the monitor finalized unresolved before trying known TP1/stop order fill recovery. P303 keeps the strict no-synthetic-PnL rule but attempts exchange-fill recovery first.
Operator heartbeat Orders previously displayed orphan cancel delta, not active protection order count; P303 changes it to a cheap local protective-order count.
Next validation: run a short real-order live/smoke and require entry_order_submit_started -> entry_fill_verified -> stop/TP verified, and if position becomes flat externally, either position_external_exit_fill_recovered -> position_closed or a detailed position_external_exit_fill_recovery_failed -> position_exit_unresolved.
```

## 2026-05-19 — P305 proposed

```text
Current patch status: P305 PROPOSED, commit UNKNOWN.
P305 targets the post-P304 bottleneck seen in 20260519_084702: repeated scans of the same weak-flow/mark-basis rejects and optional top-growth/context/cache work running while hot candidates exist.
It adds a hard hot-idle policy for optional work and a symbol-level reject cooldown after repeated weak-flow or mark-basis rejects. Immediate danger-flow precise scans bypass this cooldown so a fresh extreme spike is not suppressed.
Next validation: run live after P304+P305 and require live_cycle_summary to show top_growth/context skipped_hot_path while queue>0, cache_flush skipped_hot_path except emergency, reject_cooldown_started/skipped metrics for repeated weak rejects, and no drop in immediate_danger_flow scan coverage.
```

## 2026-05-19 — P306 proposed

```text
Current patch status: P306 PROPOSED, commit UNKNOWN.
Short live screens can show latency jumping from ~1s to ~19s because the heartbeat previously displayed a single current SLA sample/max without rolling context. P306 adds rolling 1m/5m/15m/run quality windows to the heartbeat and artifacts, so a bad spike can be separated from sustained network/queue degradation.
Next validation: run live for 20-30 minutes and inspect live_quality_window_summary plus live_cycle_summary quality_* fields. A good run should have 5m/15m WS health stable >95-98%, 5m latency p95 near target, and isolated max spikes visible without making the whole run look broken.
```

## 2026-05-19 live OI guard follow-up

- Current head: UNKNOWN (ZIP snapshot, no git metadata).
- BAS live entry showed runner_flow can accept taker-buy/price spikes while OI context is weak/negative.
- Next patch status: P302 mandatory live current-OI short-cover guard proposed, no runtime flags.

## 2026-05-19 - P311 live2 v0 runtime skeleton

```text
Current patch status: P311 PROPOSED / UNKNOWN commit.
Question: start a separate `run-anomaly-live2` runtime next to the existing live without copying the old monolith or adding shadow/dry-run semantics.
Change: add `research_tools/anomaly_live2/` with typed config/contracts/state/artifact writer/runner, register CLI command `run-anomaly-live2`, and create isolated artifacts under `.output/results/live2_anomaly_runs/<run_id>/`. Generation 0 keeps market-data, signal, exchange boundary, position supervisor, and execution as explicit `todo_not_implemented` readiness gates; `new_entries_allowed=false`.
Trading impact: no real orders, signal thresholds, category math, guard logic, existing live1 scheduler, fills, stops, TP, BE, or PnL are changed. This is the first live2 runtime shell only; the command name is real-live oriented, but order placement is intentionally not implemented yet.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: run `python main.py run-anomaly-live2`, stop with Ctrl+C, and confirm `live2_events.csv`, `live2_status.json`, and `live2_symbol_state.csv` are created with TODO readiness gates and no order attempts.
```


## 2026-05-19 - P312 live2 all-ticker WS state ingestion

```text
Current patch status: P312 PROPOSED / UNKNOWN commit.
Question: add the next live2 layer after the runtime skeleton, following the deadline-driven/no-queue plan.
Change: `run-anomaly-live2` now starts Binance futures `!ticker@arr` WS ingestion and mutates one `SymbolState` per configured symbol, or per Binance market id when no explicit symbols are passed. State records now expose ticker market id, first/last seen timestamps, update count, last price, 24h quote volume, 24h trade count, 24h price-change pct, source, status, and reason. Artifacts include ticker status counts and market-data status.
Trading impact: no real orders, signal decisions, category thresholds, execution guards, fills, stops, TP, BE, or live1 behavior are changed. `market_data_ready_for_entries=false` remains mandatory because aggTrade/candle coverage is still TODO; new entries remain forbidden.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: run `python main.py run-anomaly-live2 --symbols BTC/USDT:USDT ETH/USDT:USDT` for 30-60 seconds and confirm `ticker_ws_startup_status`, `live2_heartbeat.market_data_status.ticker_ws.ready`, and `live2_symbol_state.csv` ticker fields update without any candidate queue/drop fields.
```


## 2026-05-19 - P313 live2 aggTrade WS candle rings

```text
Current patch status: P313 PROPOSED / UNKNOWN commit.
Question: add the next live2 layer after all-ticker state ingestion, prioritizing speed and reliability.
Change: `run-anomaly-live2` now starts Binance futures combined aggTrade WS shards for explicit `--symbols`, normalizes each trade, and mutates the same per-symbol `SymbolState` with real trade flow plus in-memory 5s/15s/30s/1m candle rings. Missing buckets are counted as gaps; they are not synthetic-filled. Artifacts expose aggTrade status counts, candle coverage counts, shard status, and candle summaries in `live2_symbol_state.csv`/`live2_status.json`.
Trading impact: no real orders, signal decisions, category thresholds, execution guards, fills, stops, TP, BE, or live1 behavior are changed. `market_data_ready_for_entries=false` remains because signal/execution are still TODO; no entry is allowed.
Important limitation: P313 does not invent a live2 universe manager. Until that exists, aggTrade coverage requires explicit `--symbols`; no-symbol mode remains ticker-only and writes an explicit reason.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: run `python main.py run-anomaly-live2 --symbols BTC/USDT:USDT ETH/USDT:USDT` for 60-120 seconds and confirm aggTrade shard readiness, nonzero aggtrade_update_count, 5s/15s closed candle counts, and zero hot REST/backfill fields.
```

## 2026-05-19 — P316 proposed

```text
Current patch status: P316 PROPOSED, commit UNKNOWN.
Live2 now has a stream-only SignalEngine adapter connected to the deadline engine. It uses in-memory aggTrade candles and the shared pump category contract subset available from live stream features, with no network/disk IO in evaluation. Categories requiring derivative/OI/mark context are rejected explicitly as unavailable in generation 0.
New entries remain forbidden: execution, executable-entry guards, exchange position precheck, actual fill, verified stop, and position supervisor are still TODO.
Next validation: run `run-anomaly-live2` after P311-P316 and inspect `deadline_decision` events. On strong buckets, verdicts should be `selected` only when stream baseline/category checks pass; otherwise `rejected_signal_contract`, `data_not_ready`, or `deadline_missed` must explain the reason.
```

## 2026-05-19 — P317 proposed

```text
Current patch status: P317 PROPOSED, commit UNKNOWN.
Live2 now has an executable-entry guard layer connected after stream signal selection. It rejects selected signals when the signal is stale, current stream price drift is too high, TP1 is already touched before execution, RR to TP1 collapsed, or live stream price/risk levels are unavailable. The guard is stream-only: no REST/cache/file IO and no order placement.
New entries remain forbidden: execution, exchange position precheck, actual fill, verified stop, and position supervisor are still TODO. `new_entries_allowed=false` remains mandatory.
Next validation: run `run-anomaly-live2` after P311-P317 and inspect `deadline_decision` events. If any stream signal becomes selected, entry guard fields must show either `accepted` or a concrete `rejected_entry_guard` reason; no late/drifted/TP-touched signal may proceed toward execution.
```

## 2026-05-19 — P318 proposed

```text
Current patch status: P318 PROPOSED, commit UNKNOWN.
Live2 now has a strict execution boundary connected after entry-guard acceptance. Startup performs Binance/account preflight through the typed exchange client; accepted signals perform pre-entry `fetch_symbol_position_amount` before any future order placement can exist. Existing exchange positions are rejected explicitly; flat symbols end at `rejected_execution_order_placement_not_implemented` until verified fill and verified stop are implemented.
New entries remain forbidden: no market order, no actual fill, no stop order, no TP/BE position supervisor. `new_entries_allowed=false` remains mandatory.
Next validation: run `run-anomaly-live2` after P311-P318 and inspect `execution_preflight`, `deadline_decision.execution_*`, and `live2_status.json.execution_status`. There must be no order attempts; accepted guard decisions must never proceed without exchange position precheck.
```

## 2026-05-19 — P321 proposed

```text
Current patch status: P321 PROPOSED, commit UNKNOWN.
Live2 now has the first verified real-order entry lifecycle. A selected stream signal with accepted entry guard may proceed only if runtime gates allow entries, exchange preflight is ready, symbol is flat on the exchange, and live2 protected-position capacity is available. Execution uses deterministic client ids, verifies actual market-order fill, checks exchange position delta, places a reduce-only initial stop, verifies the stop through the Binance stop/algo-order boundary, and records a protected local position. If a fill occurs but stop/position integrity fails, live2 attempts emergency reduce-only close and disables further entries.
Still not complete: TP1 partial close, BE stop move, stop replacement verification, final close reconciliation, Telegram operator messages, and full position supervisor. Do not treat P321 as production-ready for unattended trading until P322 position supervision is implemented and smoke-tested.
Next validation: apply P311-P321, run compileall, then run `run-anomaly-live2 --symbols <one liquid symbol>` only with minimal account exposure and inspect `deadline_decision.execution_*`, `live2_status.json.execution_status.protected_positions`, and exchange UI for actual fill plus visible initial stop. Stop visibility failure must produce `position_integrity_error` and emergency close attempt.
```

## 2026-05-19 — P322 proposed

```text
Current patch status: P322 PROPOSED, commit UNKNOWN.
Live2 now has a verified PositionSupervisor for positions created by P321. It manages only registered protected positions with actual entry fill and visible current stop. It can perform TP1 reduce-only partial close from exchange fill, move the remaining stop to breakeven after verifying the new stop, cancel the old stop, and remove the position only after exchange position amount is flat.
Still not complete: dedicated stop-trigger fill lookup, stale open-order reconciliation across restart, Telegram operator messages, and broader runtime/coverage hardening. Do not increase notional/universe until P322 has been smoke-tested against real exchange behavior.
Next validation: run `run-anomaly-live2 --symbols <one liquid symbol>` with minimal notional, inspect `position_tp1_filled_be_stop_verified`, `position_final_close_verified`, and `position_integrity_error` events, and compare them with exchange UI/open conditional orders.
```

## 2026-05-19 — P323 proposed

```text
Current patch status: P323 PROPOSED, commit UNKNOWN.
Live2 runtime coverage is hardened around WS health and decision-loop pressure. Ticker and aggTrade sources now expose connect/reconnect/disconnect counters and thread liveness. The runner emits `market_data_coverage_update` when source/gate status changes, keeps market-data readiness false immediately on stale/disconnected coverage, and only re-enables the market-data gate after configured clean recovery windows. Runtime gate status now includes market-data clean/degraded windows and decision-loop overrun counters.
Trading impact: no signal threshold, order sizing, fill, stop, TP1/BE, or live1 behavior is changed. New entries become stricter under WS reconnect/stale coverage and recover only after clean windows.
Still not complete: Telegram critical/operator messages, restart/open-order reconciliation, exact stop-trigger fill reconstruction, and full stress/load validation.
Next validation: run `run-anomaly-live2 --symbols <one liquid symbol>` for 2-5 minutes, interrupt/reconnect network if possible, and inspect `market_data_coverage_update`, `runtime_gate_update`, `live2_status.json.market_data_status.ws_health`, and `runtime_gate_status.decision_loop_overrun_count`. Entries must remain disabled during stale/reconnect periods and recover only after clean windows.
```

## 2026-05-19 - Live2 audit after P324

Current commit: UNKNOWN.

P325 proposed after patch-stack audit. Main finding: P311-P324 compile, but run-anomaly-live2 had startup/import and safety-contract issues that should be fixed before real smoke.

## 2026-05-19 - P326 live2 grid-log state

Current commit: UNKNOWN.

P326 proposed after P325. Live2 gets a v1-style terminal grid log so the operator can see connection/market/trading/control health without opening JSON/CSV. This is UI-only; artifacts remain source of truth and trading behavior is unchanged.

## 2026-05-19 - P327 live2 warmup/backoff state

Current commit: UNKNOWN.

P327 proposed after P326. Live2 now has startup-only aggTrade REST warm-up into bounded in-memory candle rings and exponential reconnect backoff/watchdog restarts for ticker/aggTrade WebSockets. This does not reintroduce hot REST fallback: signal decisions still use only already-hydrated stream state and must reject/degrade when coverage is stale.

## 2026-05-19 - P328 live2 startup/operator visibility state

Current commit: UNKNOWN.

P328 proposed after P327. Live2 no longer stays silent during startup: the terminal shows stage progress for preflight, ticker, universe selection, warm-up, and aggTrade WS before the first heartbeat grid. The heartbeat grid is now rendered through a v1-style repaintable console logger instead of printing a new block every heartbeat. Runtime gate flips remain in artifacts/grid, but Telegram no longer sends “new entries enabled/disabled” messages. Default auto-universe is broadened to max 600 symbols with no 24h quote/trade-count minimum, because the previous 300k quote-volume filter could shrink live2 to roughly 100-150 symbols while v1 covered 500+.


## 2026-05-19 - P329 live2 universe floor state

Current commit: UNKNOWN.

P329 proposed after P328. Live2 default auto-universe now uses `universe_min_quote_volume_24h=30_000` instead of `0`. This filters dead/dust symbols while preserving broad 500+ style coverage. Trading logic, real-order lifecycle, stop/TP handling, Telegram behavior, and runtime gates are unchanged.

## 2026-05-19 - P330 live2 startup universe snapshot state

Current commit: UNKNOWN.

P330 proposed after P329. Live2 auto-universe selection no longer depends on the first partial `!ticker@arr` WebSocket payload. At startup it hydrates ticker/liquidity fields once through the exchange startup ticker snapshot boundary, then selects the 30k+ quote-volume universe from that broad snapshot plus any live WS updates. The REST snapshot remains startup-only and is not available to signal/decision hot path. A minimum auto-universe guard is added so live2 does not silently proceed with a tiny auto universe such as 90 symbols.

## 2026-05-19 — P316 proposed

```text
Current patch status: P316 PROPOSED, commit UNKNOWN.
Live2 now has a stream-only SignalEngine adapter connected to the deadline engine. It uses in-memory aggTrade candles and the shared pump category contract subset available from live stream features, with no network/disk IO in evaluation. Categories requiring derivative/OI/mark context are rejected explicitly as unavailable in generation 0.
New entries remain forbidden: execution, executable-entry guards, exchange position precheck, actual fill, verified stop, and position supervisor are still TODO.
Next validation: run `run-anomaly-live2` after P311-P316 and inspect `deadline_decision` events. On strong buckets, verdicts should be `selected` only when stream baseline/category checks pass; otherwise `rejected_signal_contract`, `data_not_ready`, or `deadline_missed` must explain the reason.
```

## 2026-05-20 - P340 live2 private user-data stream state

Current commit: UNKNOWN.

P340 proposed after P339. Live2 now starts a Binance USD-M private user-data stream through a typed exchange boundary: create listenKey, connect to `/private/ws/<listenKey>`, keep listenKey alive, rotate before 24h, and emit user-data execution events into artifacts. `new_entries_allowed` now requires `user_data_stream_ready=true` in addition to market data, decision latency, artifact writer, exchange preflight, position supervisor, and execution gates. Raw listenKey is not written to artifacts.

Next validation: apply P331-P340, run compileall, then run a short live2 smoke with minimal account exposure. `live2_status.json.execution_status.user_data_stream.ready` must become true before any entry can be allowed; user stream disconnect/keepalive failure must flip entries off with `user_data_stream_not_ready`.

## 2026-05-20 - P343 live2 hard market-data startup state

Current commit: UNKNOWN.

P343 proposed after P342. Live2 now treats missing mandatory ticker, aggTrade, or markPrice WS readiness at startup as a hard startup failure. This removes the ambiguous long-running blocked mode for dead market data while preserving runtime gates for later disconnects/recovery after a successful startup.

Next validation: apply P331-P343, run compileall, then run a short live2 smoke. Startup must fail quickly with `ticker_ws_startup_failed`, `aggtrade_ws_startup_failed`, or `mark_price_ws_startup_failed` if a mandatory stream has no valid payload within its startup wait. If startup succeeds, later runtime disconnects should still be handled by normal gates/transitions.

## 2026-05-20 - P344 live2 reconnect backoff state

Current commit: UNKNOWN.

P344 proposed after P343. Additional audit found that markPrice WS and private user-data stream used `Live2ReconnectBackoff.next_delay()`, but the shared helper exposes `next_delay_seconds()`. Compileall cannot catch this because the path is runtime-only after reconnect/error. P344 fixes both call sites so markPrice/private WS threads can recover instead of dying on the first reconnect.

Next validation: apply P331-P344, run compileall, then run a short live2 smoke and verify reconnect/error paths do not produce `AttributeError: 'Live2ReconnectBackoff' object has no attribute 'next_delay'`.

## 2026-05-20 - P345 live2 startup context prewarm state

Current commit: UNKNOWN.

P345 proposed after P344. Live2 is still not a true all-seeing runtime if OI and 24h prior context are lazy-loaded only after a symbol becomes active/radar, because the first 5s impulse can be classified as `data_dependency_not_ready` before context arrives. P345 adds startup prewarm for OI and strict 24h prior context across the selected universe before runtime entries begin. Runtime context refresh remains active/radar-only. No fallback or hot-path REST is added.

Next validation: apply P331-P345, run compileall, then run a short live2 smoke. Startup should show OI/24h prewarm progress and `live2_status.json.market_data.startup_context_prewarm`. If startup is too slow, tune source-side request pacing deliberately; do not revert to lazy-only context for real-order mode.

## 2026-05-20 - P346 live2 missing prior-context module state

Current commit: UNKNOWN.

P346 proposed after a failed live2 startup showed `ModuleNotFoundError: No module named 'research_tools.anomaly_live2.market_data.prior_context'`. The earlier P345 prewarm patch referenced `Live2PriorContextPoller`, but the module file was missing from the applied patch stack. P346 adds the missing module so live2 can import and proceed to startup preflight.

Next validation: apply P346, run compileall, then run `run-anomaly-live2` again. If import succeeds, continue debugging from the next concrete startup/runtime error.


## 2026-05-20 - P347 live2 user-data startup event writer state

Current commit: UNKNOWN.

P347 proposed after a failed live2 startup showed `AttributeError: 'AnomalyLive2Runner' object has no attribute '_write_user_data_stream_starting_event'`. The private user-data stream startup path called an audit helper that was missing from the applied patch stack. P347 adds that helper so startup can proceed to listenKey creation and private WS readiness checks.

Next validation: apply P347, run compileall, then run `run-anomaly-live2` again. If startup passes this point, continue debugging from the next concrete startup/runtime error.


## 2026-05-20 - P348 live2 aggTrade startup blocker diagnostics state

Current commit: UNKNOWN.

P348 proposed after live2 reached strict market-data startup and failed with `live2 aggTrade WS startup failed: partial_or_connecting`. That error is too generic for post-mortem. The patch keeps aggTrade fail-fast strict, increases the default startup wait from 10s to 60s for multi-shard startup, and adds `reason` plus `readiness_blockers` to aggTrade status so the next failure identifies the exact shard-level blocker.

Next validation: apply P348, run compileall, then run `run-anomaly-live2` again. If it still fails, inspect `aggtrade_ws_startup_failed.data.aggtrade_ws.readiness_blockers` in `live2_events.csv` / status instead of guessing.

## 2026-05-20 - P351 live2 selected-universe OI refresh state

Current commit: UNKNOWN.

P351 proposed after a successful live2 market-data startup showed `OI ! 72/578` and `OI stale 503` after ~17 minutes. Root cause: P345 prewarmed OI for the selected universe, but runtime refresh remained active/radar-only, so most prewarmed passive symbols expired after the 180s stale window. P351 makes runtime OI refresh the selected universe continuously, active-first, and aligns OI freshness to 5m historical OI cadence plus the full-universe refresh rate.

Next validation: apply P351, run compileall, then run live2 long enough for one full OI refresh cycle. Expected status after several minutes: OI ready should climb toward selected universe size, OI stale should fall sharply, OI err should remain low. If OI err rises or Binance rate-limit symptoms appear, reduce `oi_max_symbols_per_cycle` deliberately rather than reverting to lazy-only OI.

## 2026-05-20 - P350 live2 operator status state

Current commit: UNKNOWN.

P350 proposed after a live2 smoke reached running market-data state. It fixes operator-facing status semantics only: the grid `Время` now starts at market monitoring instead of startup/prewarm, `Позиции` shows open/session-total so a fresh run is 0/0 instead of 0/max-capacity, v1-style session top movers are shown between Market and Trading, and the ambiguous `Дедлайн` label is renamed to `Опоздало`.

Next validation: apply P350, run compileall, then restart live2 and confirm the grid shows market-monitoring runtime, fresh-run positions 0/0, session top movers, and no trading-path behavior changes.
