# Anomaly Research State

Compact project memory. Detailed rules live in Project Instructions.

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P130-P144 proposed locally on top of ZIP-derived source
Last active patch: P144 skip unchanged live signal rescans
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
5. `main.py` still has a known Linux `ctypes.windll` import issue; intentionally not fixed in the current cleanup stack.
6. Historical local artifacts may contain stale compiled files; they are ignored by git and should be deleted locally.
7. Top-growth snapshots are now exported only by standalone `run-anomaly-top-growth`; live trading loop must not spend REST/API budget on top-growth side work.
8. Active symbols whose latest closed levels candle was already scanned are now kept visible as `active_waiting_*` in batch artifacts and should not consume OHLCV scan slots until a new closed candle exists.

---

## 6. Next best step

Apply P144 and run a short live latency smoke before adding ticker-radar promotion:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20
```

Check `symbol_batch_selected` for `active_waiting_count`, `reject_stale_signal` for zero/low counts, and fetch/cycle speed before touching signal thresholds.


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
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20
```
