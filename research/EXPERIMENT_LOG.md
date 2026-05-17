# Anomaly Experiment Log

Compact active experiment log for anomaly-first research.

Retired strategy experiments were removed from active research memory in P129 because they are no longer an executable strategy contract. Preserve old external run archives separately if forensic comparison is needed.

---

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
