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
