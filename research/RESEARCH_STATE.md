# Anomaly Research State

Compact project memory. Detailed rules live in Project Instructions.

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P130-P161 present in uploaded ZIP / UNKNOWN commit; P162 proposed
Last active patch: P162 live terminal PnL amount accounting
Updated: 2026-05-12
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

---

## 6. Next best step

Apply P162, then verify that terminal live PnL uses only verified remaining size and unresolved residual exits do not become normal closed trades:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
```

Synthetic/fake-exchange smoke should force `remaining_amount=0` terminal close and partial TP1 fill followed by exchange amount zero. Expected result: no fallback to `position.amount` for PnL; material missing residual fill ends as `position_exit_unresolved` with blank realized PnL fields.


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
