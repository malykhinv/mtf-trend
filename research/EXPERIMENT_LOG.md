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
