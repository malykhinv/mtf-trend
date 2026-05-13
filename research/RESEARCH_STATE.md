# Anomaly Research State

Compact project memory. Detailed rules live in Project Instructions.

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P130-P176 present in uploaded ZIP / UNKNOWN commit; P177 proposed
Last active patch: P177 live OHLCV cache concat warning fix
Updated: 2026-05-13
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

---

## 6. Next best step

Apply P177, then verify that live OHLCV cache fill no longer emits pandas concat `FutureWarning` when a cache window is empty before fetched candles are merged:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Synthetic/live-cache smoke should start from an empty cache window, fetch missing candles, merge them into memory cache, and produce no pandas concat `FutureWarning`.

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

P148 is live console UX only. Routine heartbeat status now updates one terminal line in-place for the default interactive console runner, while real event/error/position/Telegram failure logs first terminate that status line and remain permanent sequential logs. The heartbeat label now says `live: цикл ...s`, because the value is cycle duration, not wall-clock interval between printed lines.

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
