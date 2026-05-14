# Anomaly Research State

Compact project memory. Detailed rules live in Project Instructions.

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P130-P176 present in uploaded ZIP / UNKNOWN commit; P177/P178/P179/P180 applied locally by user / UNKNOWN commit; P181/P184/P185 present in uploaded ZIP / UNKNOWN commit; P186 proposed
Last active patch: P189 live aggTrade REST gap prefetch planner
Updated: 2026-05-14
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
7. Top-growth snapshots are now exported only by standalone `run-anomaly-top-growth`; live trading loop must not spend REST/API budget on top-growth side work.
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

---

## 6. Next best step

Apply P189 on top of the uploaded ZIP, then run a short WS-live smoke and inspect `live_events.csv`:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected readout: startup still refuses blind ticker radar from P185; `live_cycle_summary.scheduler_cycle_seconds` is the decision heartbeat; ticker fields show source/status/ok/missing/promoted counts; all-missing required ticker radar becomes `network_degraded`; precise active/radar symbols emit `aggtrade_rest_gap_prefetch` when S30/S15/S5 WS gaps exist, then later frame reads should mostly be WS/cache hits; oversized gaps still become `coverage_pending`. If diagnostic cold coverage is needed, set `--inactive-scan-slots-per-cycle` explicitly.

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
Reactive migration plan: keep PNO decision logic synchronous; make ingestion/scheduling event-driven through narrow data-source interfaces.
Step 1 completed: ticker radar now reads through LiveTickerSnapshotSource. Default source is RestLiveTickerSnapshotSource, so behavior remains REST-backed.
ticker_radar_snapshot and ticker_radar_failed now include source id. This is the first provenance hook for future WS ticker shadow/primary mode.
Next step should be a LiveScheduler/scan-reason seam or WS ticker shadow source, not WS aggTrade trading data yet.
```

P182 reactive rollout status:

```text
Step 2 completed: current live batch selection is now represented as LiveSymbolBatchSelection.
Behavior remains rest_round_robin_scheduler with the same active/radar/inactive composition.
symbol_batch_selected now includes scheduler_source and scan_reason_by_symbol, preparing for a future event-driven scheduler without changing PNO evaluation.
No symbols should be silently dropped by future scheduler modes; queued/waiting reasons must stay explicit.
```

P183 reactive rollout status:

```text
Step 3 completed: live ticker radar now defaults to Binance WS !ticker@arr through BinanceWsAllTickerSnapshotSource.
No REST fallback is used while live_ws_ticker_enabled=true. If WS is not ready/stale/broken, ticker_radar_failed is emitted with source=binance_ws_all_ticker and radar promotions pause.
REST ticker source remains available only through explicit --live-ws-ticker-enabled false.
This changes scheduling/wake-up transport only; PNO signal logic, subminute candles, OI/mark, and order path remain unchanged.
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
