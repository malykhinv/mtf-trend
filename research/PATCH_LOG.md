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
