# Anomaly Patch Log

Compact active patch log for the anomaly-first source tree. Retired strategy history was removed from active research memory in P129 to avoid stale contracts controlling current work.

| ID | Title | Status | Files | Type | Purpose | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| P123 | Decouple anomaly runtime helpers | PROPOSED | `research_tools/anomaly_config.py`, `research_tools/charting.py`, anomaly live/backtest modules | cleanup | Move timeframe validation and chart helpers into anomaly-owned/common modules. | `python -m compileall research_tools/anomaly_config.py research_tools/charting.py research_tools/anomaly_micro_live.py research_tools/anomaly_strategy_backtest.py` |
| P124 | Isolate retired strategy CLI imports | PROPOSED | `cli/commands.py`, `cli/parser.py`, `config/*`, `strategy/factory.py` | cleanup | Stop startup/config/parser paths from importing retired strategy modules. | `python -m compileall cli/commands.py cli/parser.py config strategy main.py` |
| P125 | Prune unused launcher and dead constants | PROPOSED | `launcher.py`, `constants.py`, docs | cleanup | Remove unused wrapper and dead sizing constants. | `python -m compileall constants.py cli config research_tools main.py` |
| P126 | Remove stale historical pytest marker | PROPOSED | `pyproject.toml`, `research/*` | cleanup | Remove a test marker for a test suite absent from the ZIP source. | `git grep historical_marker_name` equivalent returns empty. |
| P127 | Neutralize old sizing names | PROPOSED | `constants.py`, `config/*`, runtime modules | cleanup | Rename old sizing defaults to neutral position defaults. | `git grep old_sizing_prefix` equivalent returns empty. |
| P128 | Remove retired strategy active path | PROPOSED | `strategy/`, `cli/`, `config/`, `README.md`, `research/*` | deletion | Remove retired strategy source, diagnostics, CLI, config and factory path. | `python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py` |
| P129 | Purge retired strategy history from active memory | PROPOSED | `README.md`, `research/*.md` | cleanup | Remove stale historical strategy references from current project docs/logs. | `git grep -i retired_strategy_token -- .` returns empty for tracked files. |
| P130 | Strict live fills and executable market entry | PROPOSED | `data/exchanges/*`, `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `cli/*`, `research/*` | bugfix | Stop executing stale live signals; require exchange fill fields; separate signal price from actual fill; compute live PnL/BE/TP from actual fill; make market backtest enter on execution candle. | `python -m compileall data/exchanges research_tools cli constants.py main.py` |
| P131 | Live blocked-order Telegram alerts | PROPOSED | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | bugfix | Send Telegram event alerts when a selected live signal is blocked as stale/non-executable; make entry-price drift guard absolute in live and market backtest. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic stale/drift smoke. |
| P137 | Repair 1h overhead level scanner wiring | PROPOSED | `research_tools/hourly_levels.py`, `cli/*`, `research/*` | diagnostics | Restore the non-empty scanner module and register `run-hourly-levels`; detect bounce-validated 1h overhead levels and export metrics/charts. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-hourly-levels --source-timeframe 5m --days 45`. |
| P138 | Make hourly-level chart text ASCII-safe | PROPOSED | `research_tools/hourly_levels.py`, `research/*` | diagnostics | Avoid matplotlib missing-glyph warning spam by rendering non-ASCII symbols as escaped ASCII in chart titles/filenames. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; run `run-hourly-levels` on symbols with non-ASCII names. |
| P139 | Add hourly-level scan progress and ETA | PROPOSED | `research_tools/hourly_levels.py`, `cli/*`, `research/*` | diagnostics | Emit start/progress lines with processed count, elapsed time, ETA, levels, chart count and last symbol status during long all-cache hourly-level scans. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; run `run-hourly-levels` and verify progress appears before completion. |
| P140 | Humanize and de-spike hourly levels | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Draw dark bot-style charts, count retouches only after a meaningful reset away from the level, cap nearby levels per symbol, draw levels from first valid touch, and reject pierced/spiked-through resistance levels by default. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` with pierced-level guard enabled. |
| P141 | Speed up hourly-level scan and chart run | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Read only scanner-needed parquet columns, trim stale source candles before 1h aggregation, and replace expensive pandas per-row/copy checks in touch/break/pierce logic with numpy scans. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` and compare elapsed_seconds. |
| P142 | Remove hourly chart tight-layout pass | PROPOSED | `research_tools/hourly_levels.py`, `research/*` | diagnostics | Replace matplotlib `tight_layout()` with fixed subplot margins to avoid warning spam and avoid an unnecessary per-chart layout solver pass. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; run chart export and confirm no tight_layout warning. |
| P143 | Speed up live scan without hiding stale rejects | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance | Fetch each levels timeframe once per symbol per cycle, reject stale decisions before heavy signal build while preserving `reject_stale_signal` artifacts, and move top-growth export to standalone CLI. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; live smoke: `reject_stale_signal` remains visible with `stage=prescan`; `run-anomaly-live` no longer starts top-growth. |
| P144 | Skip unchanged live signal rescans | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance | Do not spend OHLCV/API budget rescanning a symbol/timeframe until a new closed levels candle exists; active symbols are checked every cycle but only due ones consume scan slots, while waiting active symbols remain visible in `symbol_batch_selected`. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic scheduler smoke: active waiting symbol is not scanned again on unchanged closed candle and frees a slot for inactive scan. |
| P148 | Inline live heartbeat status | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux | Keep routine live heartbeat status on one mutable console line while preserving real events/errors/position logs as normal sequential lines; clarify that the number is cycle duration. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; console smoke: consecutive heartbeat lines overwrite in-place, any event log first terminates the heartbeat line. |
| P149 | Strict high-based hourly levels | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Count only high-based 1h touches, enforce 6h spacing between counted touches, and reject pierced levels strictly by default. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` with `--min-touch-spacing-hours 6 --max-level-pierce-pct 0.0`. |
| P150 | Reject stale source candles for 1h levels | PROPOSED | `research_tools/hourly_levels.py`, `cli/parser.py`, `research/*` | diagnostics | Reject a pivot/high level source when any close in the previous 12h is above that candidate high. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; rerun `run-hourly-levels` with `--level-source-close-lookback-hours 12`. |
| P151 | Rework trade chart context and flow panels | PROPOSED | `research_tools/anomaly_strategy_backtest.py`, `research/*` | diagnostics | Merge quote-volume and relative trade-count into one bottom line panel, and replace the middle context with independent 1h/7d candles plus strict hourly levels. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic `render_anomaly_trade_chart` smoke. |
| P152 | Attach open-position trade chart | PROPOSED | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | live-operator-ux | Send the canonical three-panel trade chart with live open-position Telegram messages; open charts use TP1/SL lines instead of risk/reward rectangles. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic open `render_anomaly_trade_chart` smoke. |
| P153 | Limit trade-chart level discovery to 7d context | PROPOSED | `research_tools/anomaly_strategy_backtest.py`, `research/*` | diagnostics | Ensure levels drawn on trade/open charts are discovered only from the same 1h/7d context window shown in the middle panel, even if a wider context frame is supplied. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic wider-context level smoke. |

## P129 — Purge retired strategy history from active memory

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

After removing the retired source path, the active research memory still contained extensive historical entries for the previous strategy. Those entries were no longer executable and could keep steering new work toward stale assumptions.

### Change

- Rewrite `README.md` as anomaly-only project documentation.
- Rewrite `research/RESEARCH_STATE.md` around current anomaly runtime paths and risks.
- Rewrite `research/STRATEGY_SPEC.md` as the current anomaly strategy contract.
- Reset `research/PATCH_LOG.md` to a compact active patch log for the current source tree.
- Reset `research/EXPERIMENT_LOG.md` to anomaly-only experiment tracking.

### Validation

```bash
git grep -n -i -E 'retired_strategy_token|old_strategy_token|old_sizing_token' -- .
python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py
```

### Risk

Medium for project memory: detailed historical notes are intentionally removed from the active repo. Source behavior is unchanged.

## P130 — Strict live fills and executable market entry

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

NVDA live audit showed a signal price/time and execution price/time mismatch: a stale backfilled signal could be selected several minutes after its decision candle, while ledger, PnL and chart still used the signal close as entry. That makes live PnL and edge evidence invalid.

### Change

- Add typed exchange fill boundary: market entry must resolve order id, timestamp, average price and filled amount through exchange order/trade payloads.
- Reject live entries when signal is stale, live price drift is too high, TP1 is already reached, RR to signal TP1 collapsed, or exchange already has a position for the symbol.
- Ledger and chart now separate `signal_entry_price` from actual `entry_price`; TP1/BE/PnL are based on actual fill.
- Stop replacement verifies the new stop is open and does not silently ignore old-stop cancellation state.
- Market backtest no longer enters at decision close; it enters on the configured execution candle and rejects drift/RR-collapsed cases.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Medium: live will reject more signals and backtest results for `entry_method=market` will change. This is intended; old behavior was optimistic and non-executable.

## P131 — Live blocked-order Telegram alerts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

P130 blocks stale and non-executable selected live signals, but the operator would only see those decisions in `live_events.csv` unless watching artifacts. Second review also found the drift guard was one-sided: it rejected only positive drift, while a selected signal could still be bought after price moved too far below the signal entry.

### Change

- Send Telegram event-channel alerts for `reject_stale_signal` and live executability rejects: invalid live price, TP1 already reached, invalid/wide live risk, entry price drift, and RR collapsed.
- Keep artifact events as source of truth; Telegram is only an operator notification queued through the existing dispatcher.
- Change live and market-backtest entry-price drift from one-sided positive drift to absolute drift around the signal entry.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: stale signal and absolute drift reject enqueue Telegram event and create no market order
```

### Risk

Low/medium: fewer fresh-but-dislocated live entries pass, and Telegram event channel can receive more messages. Existing Telegram cooldown key is per symbol/event.

## P132 — Live position safety audit artifacts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

P130/P131 fixed stale/non-executable entry selection, but live position management could still hide operational problems: monitor integrity errors were handled like temporary network failures, TP1 partial exits used unverified fills, initial stops were accepted by id only, repeated empty monitor OHLCV had no hard failure, and market-backtest skip reasons were not exported as first-class artifacts.

### Change

- Verify initial and replacement stop orders through open-order state: id, side, stop type, reduce-only flag, amount and stop price.
- If initial stop verification fails after entry fill, attempt a verified reduce-only emergency exit and write the exact outcome to `live_events.csv`.
- Execute TP1 partial close through verified fill resolution and record actual TP1 fill price/amount/PnL in `live_events.csv`.
- Treat `LiveDataIntegrityError` in position monitor as `position_integrity_error` with Telegram alert and no generic retry masking.
- Record repeated empty monitor OHLCV as `position_monitor_empty_ohlcv`; after `max_monitor_empty_ohlcv_cycles`, escalate to integrity error.
- Export `anomaly_skip_reasons.csv`, include skip reasons in profitability summary, expose execution-guard skips in entry-grid summary, and mark market backtest execution as `next_bar_open_proxy_latency_N`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Medium: live may halt position monitoring when exchange stop/order state cannot be verified. This is intentional; unknown protection state must be visible and handled manually instead of being treated as a transient loop error.

## P133 — Human Telegram live messages

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

Live Telegram messages were technically correct but too verbose and used raw Coinglass URLs. Operator messages should be short, human-readable and use clickable symbols while `live_events.csv` remains the full audit source.

### Change

- Replace startup/error/network blocked-entry/integrity/open/close/stop Telegram text with compact human-style templates.
- Render symbols as Telegram HTML links to Coinglass instead of printing a separate raw URL.
- Keep detailed blocked-entry and integrity metadata in artifacts; Telegram only shows the concise reason and context.
- Display close location as `SL`, `BE`, `TP-` or `TP+`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low: Telegram wording changes only. Audit detail is intentionally preserved in `live_events.csv`, not repeated in every operator message.

## P134 — Active-symbol live scheduler

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The live loop treated only open positions as active. When at least one position was open it used a separate hard-coded inactive batch size, so a symbol that had just passed an important pump/signal stage could wait behind the normal round-robin instead of being rescanned on the next few cycles.

### Change

- Replace the old `inactive_batch_with_active = 7` path with one scheduler rule: `active_symbols + (symbol_batch_size - active_count)` inactive symbols.
- Add an explicit active-symbol watchlist for recent pump-flow candidates, selected entry signals, symbols waiting for a free position slot, and currently opening symbols.
- Keep open positions always active; if active symbols exceed the configured batch size, all active symbols are scanned and inactive slots become zero.
- Export scheduler state into `live_events.csv`: `active_symbol_marked`, `active_symbol_cleared`, `active_symbol_expired`, `symbol_batch_selected`, and `signal_decision_consumed`.
- Do not consume a selected signal when it is blocked only by `max_open_positions`; it remains active until the signal expires or a free slot appears.
- Add CLI knobs `--symbol-batch-size` and `--active-symbol-ttl-ms`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic scheduler smoke: two active symbols with symbol_batch_size=5 => two active + three inactive
```

### Risk

Low/medium: a burst of many active symbols can make a cycle longer because active symbols are not dropped to preserve a fixed batch cap. This is intentional; symbols with near-term action priority must not be starved by inactive round-robin scanning.


## P135 — Hourly live top-growth artifacts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

Manual review and later backtests need a reproducible record of which symbols made large hourly moves during live sessions. Keeping this only in terminal/TG is not enough, and using partial candles would make later replay ambiguous.

### Change

- Add hourly closed-1h top-growth snapshots under each live run root: `top_growth/`.
- Write one compact `top_growth_YYYYMMDD_HH0000_UTC.csv` per completed hour, capped at `top_growth_limit` rows and filtered by `top_growth_min_return_pct` (default 10%).
- Write matching `top_growth_status_YYYYMMDD_HH0000_UTC.csv` with per-symbol status/reason so missing candles/API failures are visible instead of silently reducing the universe.
- Maintain `top_growth_index.csv` for quick navigation across hourly snapshots.
- Run collection in a non-overlapping background worker so live scanning is not blocked by the hourly universe pass.
- Add CLI knobs: `--top-growth-enabled`, `--top-growth-min-return-pct`, `--top-growth-limit`, `--top-growth-fetch-spacing-seconds`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic top-growth smoke: two >=10% symbols exported, one below threshold excluded, one fetch failure visible in status CSV
```

### Risk

Low/medium: one hourly universe pass adds exchange requests. It is throttled and non-overlapping, but on a very large universe it can still add API load. If this interferes with live scans, disable it with `--top-growth-enabled false` or increase fetch spacing.

## P136 — Fix live active-symbol runtime error and stop masking internal bugs as network pauses

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The first live run after P135 failed immediately with `name 'levels_timeframe_ms' is not defined`. The error came from the active-symbol pump candidate path added in P134: `_build_signal_at_start()` used `levels_timeframe_ms` without defining it locally. The live loop then incorrectly reported the programming error as a network/API pause.

### Change

- Define `levels_timeframe_ms` inside `_build_signal_at_start()` before using it to compute signal availability.
- Import and catch `ExchangeConnectivityError` explicitly for network/API pauses.
- Stop treating arbitrary `Exception` as network/API degradation in the main live loop.
- Write unexpected internal failures as `live_internal_error` in `live_events.csv`, send a TG error, and exit with code 4.
- Make `CcxtFuturesClient._retry_exchange_call()` raise `ExchangeConnectivityError` on retry exhaustion so real exchange connectivity failures still use the pause/retry path.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low/medium: real exchange retry exhaustion still pauses. Internal code errors no longer keep the bot alive pretending the API is down; live stops instead, which is the intended safe behavior.

## P137 — Repair 1h overhead level scanner wiring

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The local tree has `research_tools/hourly_levels.py` as an empty file and the CLI registration is missing from `cli/parser.py` and `cli/commands.py`. As a result, `main.py -h` cannot show `run-hourly-levels` even though the scanner patch was expected to be present.

### Change

- Restore the full 1h overhead-level scanner implementation.
- Register `run-hourly-levels` in the parser and command handler map.
- Scan cached symbols from `5m` by default, aggregate to `1h`, and write `hourly_levels_summary.csv`, `hourly_levels_status.csv`, and review charts.
- Count a level touch only when a meaningful bounce follows; wick-only marks without reaction do not qualify.
- Reject clear downtrend symbols and downtrend pseudo-levels by default.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```

### Risk

Low: diagnostics only. It does not change live signal selection, order execution, stops, exits, or backtest trading rules.


## P138 — Make hourly-level chart text ASCII-safe

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

Some cached futures symbols contain non-ASCII characters. Matplotlib's default DejaVu Sans font cannot render those glyphs, so every saved chart can emit repeated `Glyph ... missing from font(s) DejaVu Sans` warnings during `tight_layout()` and `savefig()`.

### Change

- Render chart titles with an ASCII-safe symbol representation using unicode escapes for non-ASCII characters.
- Use the same ASCII-safe representation for chart file stems.
- Do not suppress warnings globally; avoid creating unsupported glyphs in the chart text.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```

### Risk

Low: diagnostics/chart output only. CSV keeps the original symbol value; only chart title/file stem becomes ASCII-safe when needed.

## P139 — Add hourly-level scan progress and ETA

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

`run-hourly-levels` can spend a long time scanning the full cache and saving charts, but after the initial command-start log it emits nothing until completion. That makes the operator watch an apparently idle process with no progress, ETA or current-stage feedback.

### Change

- Add visible progress logging to the hourly-level scanner: processed symbols, percent, elapsed time, ETA, levels found, symbols with levels, charts saved, last symbol and last status/reason.
- Emit a start line immediately after symbol discovery.
- Add CLI knobs `--progress-every-symbols` and `--progress-min-seconds`.
- Pass the command logger into the scanner as an explicit progress callback; no global warning/log suppression is used.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --progress-every-symbols 5 --progress-min-seconds 5
```

### Risk

Low: diagnostics only. More console/log lines during long scanner runs; level detection, live execution and backtest behavior are unchanged.

## P140 — Humanize and de-spike hourly levels

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The first hourly-level charts were visually and logically noisy: one local grind near resistance could be counted as dozens of touches, levels were drawn as infinite full-chart horizontals, nearby duplicate levels crowded the chart, and wick spikes through resistance could still leave a level marked as valid.

### Change

- Reuse the shared dark chart style from `research_tools/charting.py` and add a volume panel.
- Count a new touch only after price has moved materially away from the previous touch and then returned to the level.
- Draw each level segment from its first valid touch instead of as a full-width horizontal line.
- Cap major levels per symbol with `--max-levels-per-symbol` and de-duplicate close levels.
- Reject pierced levels by default with `--reject-pierced-levels true`: if price spikes materially above the level and closes back below the accepted-break zone, the level is treated as damaged rather than clean resistance.
- Export `pierce_count` and `max_pierce_pct` in the summary for audit when the guard is disabled.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --reject-pierced-levels true --max-level-pierce-pct 0.015 --max-levels-per-symbol 4 --progress-every-symbols 5 --progress-min-seconds 5
```

### Risk

Low/medium for diagnostics: the scanner will output fewer levels, and some previously visible wick-spiked levels will disappear. Trading/live/backtest execution logic is unchanged.

## P141 — Speed up hourly-level scan and chart run

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

`run-hourly-levels` was doing avoidable work: loading every parquet column, resampling the full cached history even when only the recent `days/lookback_bars` window is needed, and using pandas row/copy loops inside per-level touch, break and pierce checks. Chart runs then paid that cost for every scanned symbol before saving review images.

### Change

- Read only OHLCV columns used by the hourly-level scanner instead of full parquet payloads.
- Add `--fast-source-trim true` default: trim raw cached candles to the needed recent 1h review window plus buffer before aggregation.
- Replace per-row `iterrows()` touch detection with numpy candidate-index scanning while preserving the retouch re-arm rule.
- Replace break/pierce dataframe copies with numpy slices.
- Progress start line now reports whether fast source trimming is enabled.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --fast-source-trim true --progress-every-symbols 5 --progress-min-seconds 5
```

### Risk

Low/medium for diagnostics. Source pre-trim intentionally keeps a buffer before the final review window; disabling `--fast-source-trim false` restores the slower full-source aggregation path for exact forensic comparison. Trading/live/backtest execution is unchanged.


## P142 — Remove hourly chart tight-layout pass

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

After the humanized chart layout, `matplotlib` warns that the figure contains axes not compatible with `tight_layout()`. The warning is caused by right-side price tags drawn outside the axes; `tight_layout()` is also unnecessary work on every saved chart.

### Change

- Replace `fig.tight_layout()` in hourly-level chart export with explicit `fig.subplots_adjust(...)` margins.
- Reserve fixed right margin for price tags instead of asking matplotlib's layout solver to infer it.
- Do not suppress warnings globally; remove the warning source.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --fast-source-trim true
```

### Risk

Low: chart layout only. Level detection, CSV metrics, live trading and backtest behavior are unchanged.


## P143 — Speed up live scan without hiding stale rejects

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The 2026-05-11/12 micro-live run found selected signals only after they were already stale. Safety worked, but discovery was too slow: the live loop scanned the same `5m` levels timeframe separately for `5m/30s` and `5m/15s`, and top-growth snapshots competed with live REST/API budget. Stale decisions must still remain visible in artifacts.

### Change

- Group live scan work by `levels_timeframe`, so one `5m` OHLCV fetch per symbol serves both `5m/30s` and `5m/15s` entry metadata.
- Add a prescan freshness guard before `_build_signal_at_start()`: stale decisions are marked consumed and written as `reject_stale_signal` with `stage=prescan`, but the expensive signal/category build is skipped.
- Keep the execution-time freshness guard for selected signals and mark those rows with `stage=execution_guard`.
- Remove top-growth from the live loop and add standalone `run-anomaly-top-growth` CLI for closed-hour top-growth artifacts.
- Do not add dynamic universe pruning and do not relax `max_signal_age_ms`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-top-growth --top-growth-min-return-pct 0.10 --top-growth-limit 5
# live smoke: run-anomaly-live no longer emits top_growth_snapshot_started during trading loop
# artifact smoke: stale backfilled decisions still emit reject_stale_signal with stage=prescan
```

### Risk

Low/medium. Signal rules and order execution are unchanged, but live scan ordering and artifact staging change. Top-growth is now an explicit operator command rather than an automatic live side task.

---

## 17. Current audit note — P144

P144 is live-performance only. It adds a per-symbol/per-levels-timeframe closed-candle scan ledger so the runner does not re-fetch OHLCV for the same already-processed closed candle. Active symbols are still evaluated every cycle for scheduling, but if no new closed candle exists they are reported as `active_waiting_*` in `symbol_batch_selected` and their slot is released to inactive symbols. Empty OHLCV results are visible as `signal_scan_empty_ohlcv`; exchange/network exceptions are not marked as scanned and still bubble through the existing retry path.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: repeated _scan_batch on unchanged now_ms fetches each symbol/timeframe once, then fetches again after the next closed candle.
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20
```


## P145 — Ticker-radar live watch promotion

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

P143/P144 reduce redundant live work, but a full cold-universe round-robin can still discover fast wake-ups too late. The next acceleration must not prune the universe, must not trade from ticker data, and must not hide normal round-robin candidates.

### Change

- Add a typed `fetch_ticker_snapshots(...)` exchange boundary returning explicit source/status fields for ticker `last`, real 24h `quoteVolume`, and optional trade count.
- Add live ticker-radar state that compares current ticker snapshots to the previous snapshot and promotes only symbols with positive price delta plus real quote-volume delta.
- Add a separate bounded ticker-radar watch layer in scheduling. Radar watch symbols are extra scans; they do not consume normal inactive round-robin slots.
- Add artifacts: `ticker_radar_snapshot`, `ticker_radar_promoted`, `ticker_radar_missing_fields`, `ticker_radar_failed`, `ticker_radar_watch_expired`, and ticker-radar fields in `symbol_batch_selected`.
- Preserve strategy logic: radar data cannot open a position and cannot replace kline-based quote-volume/trade-count evidence.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: first snapshot does not promote; second snapshot with price+quote delta promotes one symbol; batch keeps normal inactive slots and adds the radar symbol as an extra scan.
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20 --ticker-radar-watch-batch-size 5
```

### Risk

Low/medium. It cannot remove cold symbols or create trades, but each promoted radar symbol adds extra OHLCV work. If cycle time worsens or rate-limit pressure appears, reduce `--ticker-radar-watch-batch-size` or disable with `--ticker-radar-enabled false`.


---

## P146 — Ticker-radar safety cleanup

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Post-P145 audit found three low/medium-risk scheduler issues: passing a large symbol list into `fetch_tickers` can create exchange-specific oversized requests; radar-waiting symbols could be selected again by inactive rotation and waste a cold slot; and symbols promoted to real active state kept stale radar-watch entries until TTL expiry.

### Change

- Fetch ticker snapshots through the typed boundary with one all-tickers request and local filtering, avoiding giant per-symbol ticker query parameters.
- Exclude `ticker_radar_waiting_symbols` from inactive selection so unchanged radar watches do not consume cold slots.
- Clear a symbol from ticker-radar watch when it becomes a real active symbol and emit `ticker_radar_watch_cleared`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: radar waiting symbols are not reselected as inactive; active promotion clears radar watch.
```

### Risk

Low. Trading logic is unchanged. The patch only reduces scheduler waste and makes ticker fetch safer for large universes.

---

## P147 — Add operator command runbook and 30d defaults

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Operator commands had drifted into chat history and long CLI lines. The base research/cache window also differed by command, which made copy-paste runs error-prone.

### Change

- Add root `COMMANDS.md` with short copy-paste commands for cache, quality, lab, live smoke/full live, top-growth and hourly-level diagnostics.
- Add `DEFAULT_COMMANDS_BASE_DAYS = 30` in `constants.py`.
- Use that base for `fetch-data`, `update-cache`, `run-anomaly-lab`, and `run-hourly-levels`.
- Keep strategy/live/radar thresholds unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low. This is operator UX/default wiring only. It changes default day windows to 30 days and does not change strategy thresholds, live execution guards, ticker-radar behavior, or order logic.
---

## P148 — Inline live heartbeat status

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The routine live heartbeat looked like a periodic event log, but the displayed seconds were only the latest cycle duration and the line was emitted on the old `cycle % 10` cadence. That created noisy historical status lines and made the operator read `20.3s` as a wall-clock heartbeat interval.

### Change

- Add a small live-only status logger boundary around the runner logger.
- For the default interactive console logger, render routine heartbeat status with carriage-return overwrite instead of appending a new line.
- Before any non-status live/Telegram/error/position log, terminate the current heartbeat line so important events remain sequential.
- Rename the heartbeat text to `live: цикл ...s` to make the value clearly a cycle duration.
- Keep the old sparse status cadence for external/non-interactive loggers.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# console smoke: 20s -> 19s -> 22s heartbeat updates collapse to one visible status line; an error/position log appears as a new permanent line.
```

### Risk

Low. Trading logic, artifacts, order guards, Telegram payloads and backtest behavior are unchanged. The only behavior change is console presentation of routine heartbeat status.


---

## P149 — Strict high-based hourly levels

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The 1h overhead-level scanner still accepted visually weak levels: range intersections could count as touches even when the level ran through the candle body/interior, touches could cluster too close in time, and the pierce filter allowed small wick pierces by default.

### Change

- Count touches only when the 1h candle high is near the level and the candle body remains below the touch band.
- Require at least 6h between counted touches by default via `--min-touch-spacing-hours`.
- Make pierced-level rejection strict by default: any later high above the level is counted as a pierce when `--reject-pierced-levels true`.
- Keep the existing bounce/re-arm checks; the patch tightens touch eligibility instead of post-processing chart output.
- Update the 1h overhead-level strategy spec to match the stricter diagnostic contract.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --min-touches 3 --min-touch-spacing-hours 6 --reject-pierced-levels true --max-level-pierce-pct 0.0
```

### Risk

Medium for diagnostics: fewer levels will be emitted, especially symbols where prior detections were body/interior intersections or recently repeated taps. Live trading and anomaly backtest execution are unchanged.

---

## P150 — Reject stale source candles for 1h levels

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

A pivot high can look like resistance even when the market closed above that same price shortly before it. Such a candle is not a clean source for an overhead level: the level was already accepted/reclaimed within the previous 12h.

### Change

- Add `level_source_close_lookback_hours=12` to the hourly-level scan config.
- Filter pivot/high source candles before level clustering: if any close in the previous 12h is above the candidate candle high, that candle is not allowed to seed a level.
- Expose the guard as `--level-source-close-lookback-hours` and print it in scan startup context.
- Keep the guard at pivot selection time, not as chart/output post-processing.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --level-source-close-lookback-hours 12
```

### Risk

Medium for diagnostics: fewer levels will be emitted when a later-looking pivot is actually below a recent accepted close. Live trading and anomaly backtest execution are unchanged.


---

## P151 — Rework trade chart context and flow panels

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The trade chart spent two lower panels on histogram-style volume and quote-per-trade bars, while the context panel only showed a short timeframe-compressed view tied to the same x-axis as the trade window. This made it harder to review a trade against broader 1h resistance context.

### Change

- Merge quote volume and `number_of_trades` into one bottom panel.
- Render both lower metrics as normalized line curves: quote volume in orange, relative trade count in green.
- Replace the old time-aligned context panel with an independent 1h context panel covering the last 7 fully closed days before entry.
- Draw strict `run-hourly-levels` overhead levels on the 1h context panel as unlabeled horizontal lines without legend.
- Keep trade execution/chart annotations on the upper trade-window panel only.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python - <<'PY'
# synthetic render_anomaly_trade_chart smoke
PY
```

### Risk

Low for trading logic: this is chart/artifact-only. Medium for chart review: lower flow lines are independently normalized to 0-100, so they compare timing/shape, not absolute magnitude.

---

## P152 — Attach open-position trade chart

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The operator should see the same three-panel trade context immediately when a live position opens, not only after close. The open chart must avoid implying a completed risk/reward box and should instead show active TP1/SL levels.

### Change

- Add a synchronous Telegram photo send path that returns the Telegram message id, preserving later stop/close replies to the opening message.
- Render an open-position chart after verified entry fill and stop placement.
- Reuse the canonical trade-chart renderer with an explicit 1h context frame, so the middle panel covers 7 days before entry without forcing the top trade-window fetch to load 7 days of small-timeframe data.
- For open charts, disable risk/reward rectangles and draw TP1 as a green dashed level and SL as a red dashed level.
- Keep text-only Telegram fallback if chart rendering or photo delivery fails.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic open render_anomaly_trade_chart smoke with hourly_context_frame and no risk/reward blocks
```

### Risk

Low for trading logic: the patch runs after verified open state is created and does not alter signal selection, order placement, stop placement, TP/SL math, or monitoring. Medium for live operator UX/API load: each opened position now performs one extra chart render and one H1 OHLCV context fetch before sending the Telegram open photo. Context fetch failures are recorded as `chart_context_fetch_failed`; chart/photo failures fall back to text-only Telegram.
---

## P153 — Limit trade-chart level discovery to 7d context

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The trade/open chart middle panel shows only the last 7 fully closed 1h days before entry. Any overhead levels drawn on that panel must be discovered from that exact displayed window, not from a wider source frame or cache history. Otherwise the chart can show levels whose source/touches are invisible in the panel.

### Change

- Add an explicit `_slice_trade_chart_hourly_level_context` guard before calling `find_hourly_overhead_levels`.
- Pass the entry timestamp and 7-day context length into trade-chart level discovery.
- Run hourly-level discovery only on the sliced 1h window: `[entry_hour - 7d, entry_hour)`.
- Keep the change artifact-only; live/backtest signal selection and execution are unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: a full 10d context with level touches older than 7d yields no drawn chart level
```

### Risk

Low for trading logic: chart-only. Medium for chart review: some previously drawn context levels may disappear when their confirming touches existed only outside the visible 7-day panel.
---

## P154 — Standardize Telegram text style and symbol emoji

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Telegram operator messages used mixed icon semantics and mixed plain/code formatting for timeframe/context and error payloads. Position messages also used event-specific pseudo-random nature emojis, so the same symbol could show different emojis across open/stop/close messages.

### Change

- Replace event-key nature emojis with deterministic animal emojis derived from the compact symbol string via SHA-256.
- Use the same animal emoji for every symbol-specific message: blocked order, open, stop move, close, and position integrity error.
- Use service emojis for non-symbol service messages: `🚧` for startup/network pause and `⚠️` for errors.
- Render startup timeframe pairs and symbol signal context lines as Telegram `<code>...</code>`.
- Render error details/reasons as Telegram `<code>...</code>` consistently.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python - <<'PY'
# synthetic Telegram formatter smoke: stable BTC emoji, escaped code context, service error text
PY
```

### Risk

Low for trading logic: Telegram/UI-only. Low operator UX risk: symbol emoji assignment changes once after deployment but remains stable across future runs for the same compact symbol.

---

## P155 — Make live audit failures explicit

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Several live operator/audit failures were visible only in console logs or implicit fallbacks. That is not enough for post-run review because console output can be lost and Telegram delivery is not the source of truth.

### Change

- Add `network_degraded` and `network_recovered` events to `live_events.csv`.
- Give `TelegramDispatcher` an artifact event writer and record async Telegram send failures as `telegram_async_send_failed`.
- Record generic `telegram_photo_send_failed` from the photo helper.
- Record open-message chart missing id and text fallback/missing-id events.
- Record close-message photo sent/missing/failed events and explicit text fallback.
- Record `reject_stop_cooldown` when a valid signal is consumed because the symbol is still in stop cooldown.
- Preserve existing text/chart fallback behavior; no order, signal, risk, stop, TP, or monitor logic is changed.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python - <<'PY'
# synthetic Telegram dispatcher failure callback smoke
# static grep smoke for network_degraded/network_recovered/reject_stop_cooldown/telegram_close_photo_failed
PY
```

### Risk

Low for trading logic: audit-only. Low runtime risk: Telegram failure events are written through the existing artifact writer lock. If artifact event writing itself fails inside the Telegram dispatcher, the failure is logged instead of recursively trying to write another event.
