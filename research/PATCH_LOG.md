# Anomaly Patch Log

Compact active patch log for the anomaly-first source tree. Retired strategy history was removed from active research memory in P129 to avoid stale contracts controlling current work.

| ID | Title | Status | Files | Type | Purpose | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| P320 | Add live2 fast decision loop gates | PROPOSED | `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research_tools/anomaly_live2/artifacts.py`, `research/*` | live2-runtime/latency | Split deadline decisions from heartbeat writes: live2 now runs the DeadlineEngine on a fast 100ms loop while heartbeat/status/symbol-state artifacts stay periodic. Add runtime gates for market-data coverage, decision-latency degradation/recovery, artifact-writer readiness, and explicit no-new-entries reasons. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic fast-loop/degraded-gate smoke |
| P319 | Add live2 bounded async artifact writer | PROPOSED | `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-audit/latency | Move live2 event/status/symbol-state disk writes behind a bounded single-thread artifact writer queue. Hot signal/deadline code no longer performs blocking CSV/JSON writes; writer health/backpressure is surfaced in artifacts and flips the artifact readiness gate off so new entries remain disabled if audit is unsafe. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic bounded-writer smoke |
| P315 | Add live2 deadline verdict engine | PROPOSED | `research_tools/anomaly_live2/deadline.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-deadline/latency | Add a deadline engine that reads already-built in-memory 5s candles, marks diagnostic actionable buckets, and gives every processed actionable bucket an explicit verdict (`rejected_signal_engine_todo`, `data_not_ready`, or `deadline_missed`) without candidate queues, pressure drops, REST fetches, signal strategy, or order placement. Decision counters and latency fields are written into live2 artifacts. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic deadline-cycle smoke |
| P314 | Add live2 ticker-selected startup universe | PROPOSED | `research_tools/anomaly_live2/market_data/universe.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/config.py`, `cli/*`, `research/*` | live2-market-data/universe | Select a startup aggTrade universe from already-received `!ticker@arr` state when `--symbols` is not provided. Starts aggTrade shards for the selected top USDT futures by 24h quote volume/trade count, marks universe rank in symbol-state artifacts, and keeps REST out of the signal hot path. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic universe-selection smoke |
| P313 | Add live2 aggTrade WS candle rings | PROPOSED | `research_tools/anomaly_live2/market_data/*`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-market-data/latency | Start explicit-symbol Binance futures aggTrade combined WS shards, normalize trades, and update per-symbol in-memory 5s/15s/30s/1m candle rings from real trades only. No REST backfill, no synthetic buckets, no candidate queues, no signal decisions, and no order placement. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P312 | Add live2 all-ticker WS state ingestion | PROPOSED | `research_tools/anomaly_live2/market_data/*`, `research_tools/anomaly_live2/state.py`, `research_tools/anomaly_live2/artifacts.py`, `research_tools/anomaly_live2/runner.py`, `research_tools/anomaly_live2/config.py`, `research/*` | live2-market-data | Start Binance futures `!ticker@arr` ingestion inside `run-anomaly-live2`; update exactly one mutable `SymbolState` per symbol/market id with last price, 24h quote volume, trade count, source/status/reason, and write ticker status counts into live2 artifacts. No candidate queues, REST ticker fallback, signal decisions, or order placement. Full market-data readiness remains false until aggTrade/candle coverage exists. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P311 | Add live2 v0 runtime skeleton | PROPOSED | `research_tools/anomaly_live2/*`, `cli/parser.py`, `cli/commands.py`, `research/*` | live2-architecture/runtime | Add `run-anomaly-live2` as a separate deadline-driven runtime skeleton with its own artifacts (`live2_events.csv`, `live2_status.json`, `live2_symbol_state.csv`). V0 is a real-live command name, not shadow/dry-run; market-data, signal, and execution are explicit TODO readiness gates and new entries are forbidden until implemented. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P310 | Prior fake-pump scheduler quarantine | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/scheduler-data-quality | Pre-populate and refresh a hot-lane quarantine from symbol-context snapshots for symbols with `prior_fast_fade_count_24h > 2`. The release time is computed from the excess fast-fade timestamps, not a fixed TTL: quarantine lasts until enough events age out of the 24h lookback. Blocks warm/radar promotion before those symbols create queue pressure, while keeping them in ticker/top-growth visibility. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P309 | Strict warm cap and class latency audit | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/scheduler-diagnostics | Reduce warm backlog cap from 12 to 8 and pressure keep floor from 8 to 6; rank warm waiting by immediate-danger then score. Add per-class latency fields for active/immediate_danger/ticker_radar/warm_watch to `symbol_batch_selected` and `live_cycle_summary`, and add scan origin first_seen/promote lag fields to `signal_symbol_scan_summary`. No signal/category/execution guards changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P308 | Hot-waiting priority prefetch | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/data-readiness | After the critical scan/order path, prefetch due subminute aggTrade gap debt for up to 2 top waiting radar/warm symbols when they are immediate-danger flow or score >= 8.0, even if the queue is not idle or latency SLA is breached. Signals/opening positions still block it. Ignore tiny open-tail gaps <=250ms in the prefetch path and emit `aggtrade_rest_gap_prefetch.status=tail_gap_ignored` instead of making a REST call. No signal/category/execution guards changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P307 | Mark live heartbeat quality values | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux | Add inline quality marks to live heartbeat values so connection, pulse, data source, guard, rolling latency, queue, and cycle health are readable without mentally mapping thresholds. Display-only change; scheduler, orders, guards, artifacts, and trading logic are unchanged. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P304 | Add immediate danger-flow precise lane | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-latency/scheduler | Bypass ordinary warm-watch observation/defer/drop policy for extreme real-flow ticker spikes. Immediate danger-flow candidates are promoted to radar on first observation, get up to two reserved precise slots above the normal adaptive cap, are sorted ahead of ordinary radar/warm candidates, and are not pressure-dropped before first precise scan. Signal/category/execution guards are unchanged. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P301 | Make live optional work idle-only | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance/scheduler | Move hot-waiting aggTrade prefetch out of batch selection and run it only after critical scan/open when the loop is idle enough; defer periodic orphan-order reconcile whenever positions/active/radar/warm candidates or latency SLA pressure exist; hard-gate precise cold coverage whenever hot candidates exist; count prior fast-fade/spike timestamps with exact bisect over cached snapshot timestamps. No trading thresholds or order semantics changed. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; full standard command including `launcher.py` not run because this ZIP has no `launcher.py` |
| P300 | Show potential-anomaly latency in live grid | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux/diagnostics | Add a second Control row to the live heartbeat showing potential-anomaly processing delay from the existing latency SLA samples: `Задержка p95`, `max`, and radar/warm `Очередь`. Also add explicit `potential_anomaly_latency_*` aliases to `live_cycle_summary` so the terminal value is auditable in CSV without changing scheduler, guards, or order logic. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; inline heartbeat smoke |
| P299 | Audit rejected market-entry execution price | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_strategy_backtest.py`, `research/*` | backtest-diagnostics | Preserve rejected delayed market-entry timestamp, price, drift, RR-after-latency, TP1 price, and risk for execution-guard skips so `market_entry_price_drift`, `market_entry_rr_collapsed`, and `tp1_already_reached_before_market_entry` can be audited and drift threshold changes can be compared honestly. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic `_resolve_signal_entry` drift-reject smoke checked rejected price/timestamp/drift fields |
| P298 | Raise executable entry drift guard to 0.4% | PROPOSED | `constants.py`, `cli/*`, `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | live/backtest-execution | Centralize executable entry drift default as `DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT = 0.004` and use it for live `max_entry_price_drift_pct` plus market/latency backtest `max_market_entry_drift_pct`, preserving live/backtest parity. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; parser default smoke for live/lab = 0.004 |
| P297 | Speed up noticed-symbol live decisions | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance/scheduler | Add a fixed hot-lane for noticed radar/warm symbols, shrink score-ranked warm backlog, allow explicit high-score warm promotion under SLA pressure, prefetch bounded hot waiting subminute gap debt, lower decision SLA to 12s, and split prescan stale decisions into `reject_stale_decision_latency`. Drift guard semantics unchanged. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py cli\\commands.py cli\\parser.py main.py`; `.venv\\Scripts\\python.exe -m pytest tests\\test_anomaly_continuation_lab.py -q`; `.venv\\Scripts\\python.exe -m unittest tests.test_live_order_lifecycle -v` |
| P296 | Fix latency validation command in research state | APPLIED locally / UNKNOWN commit | `research/RESEARCH_STATE.md`, `research/PATCH_LOG.md` | docs/research-memory | Align the next validation note with the simplified anomaly-lab CLI: use public `--latency true`, not removed grid flags. | `rg -n "run-latency-grid|latency-grid-ms|latency-ms" research/RESEARCH_STATE.md research/STRATEGY_SPEC.md cli/parser.py main.py` |
| P295 | Reframe active anomaly docs away from legacy strategy wording | APPLIED locally / UNKNOWN commit | `research/*` | docs/research-memory | Remove misleading legacy strategy wording from active anomaly live/backtest project memory. Legacy `strategy/pno` code/tests remain untouched because removing that historical module is a separate high-risk cleanup. | Checked active research notes for stale legacy strategy phrases. |
| P294 | Live cache catch-up and latency backtest grid | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `cli/*`, `research/*` | live-performance/research-execution | Keep mandatory startup context backfill, add bounded catch-up to close accumulated freshness lag, write all live OHLCV flushes as delta parquet, and make `--latency true` run hidden 1s-cache 10s execution delay plus a cheaper 0/7/10/15s latency grid that reuses the primary 10s simulation. Missing 1s latency windows are backfilled from Binance aggTrades during backtest and timing/market summaries are printed at the end. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py cli\\commands.py cli\\parser.py data\\storage\\parquet_storage.py`; `.venv\\Scripts\\python.exe main.py run-anomaly-lab --help`; synthetic latency smoke |
| P289 | Review latest live run and align 24h context labels/parity | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `cli/parser.py`, `tests/*`, `research/*` | diagnostics/parity/live-ux | Record live run 20260517_200159 review; remove hardcoded `72ч` from live context startup/reprepare labels and artifact `history_days`; make backtest legacy `*_72h` category fields use the same 24h effective prior-context window as live while keeping column names compatible; update lifecycle tests for full TP1. | `.venv\\Scripts\\python.exe -m pytest tests\\test_anomaly_continuation_lab.py -q`; `.venv\\Scripts\\python.exe -m unittest tests.test_live_order_lifecycle -v`; `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py research_tools\\anomaly_exit_portfolio_replay.py cli\\parser.py cli\\commands.py constants.py main.py tests\\test_anomaly_continuation_lab.py tests\\test_live_order_lifecycle.py` |
| P290 | Correct live-window backtest interpretation | APPLIED locally / UNKNOWN commit | `research/*` | research-correction | Record targeted anomaly-lab over live run 20260517_200159 showing that offline backtest does find weak discovery trades, but zero live-priority entries. Correct the prior overstatement that backtest would show nothing. | Analysis-only; artifact root `.output\\results\\anomaly_live_window_20260517_200159_targeted` |
| P288 | Full TP1 at 0.75R from pump leg bottom | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_strategy_backtest.py`, `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | exit/live-ready | Make TP1 a full-position exit at `entry + 0.75 * (entry - pump_leg_bottom)` using `decision_box_low` / live segment low as pump leg bottom. Keep SL based on max(structural buffered stop, EMA20). | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_strategy_backtest.py research_tools\\anomaly_micro_live.py cli\\commands.py cli\\parser.py cli constants.py main.py`; defaults smoke prints backtest/live TP1 basis/r/fraction |
| P287 | Add anomaly exit portfolio replay tool | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_exit_portfolio_replay.py`, `research/*` | diagnostics/portfolio-replay | Add a runnable artifact-level no-overlap portfolio replay for saved anomaly_lab trades with exit models `current`, `full_tp1`, and TP1-fraction plus fixed-R remainder variants. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_exit_portfolio_replay.py`; `.venv\\Scripts\\python.exe research_tools\\anomaly_exit_portfolio_replay.py --lab-dir .output\\results\\anomaly_lab --families live_priority --context-parity ok --max-positions 1 --same-symbol-overlap reject --models current,tp1_25_rest_1p5r,tp1_50_rest_1p5r,full_tp1 --output-dir .output\\results\\anomaly_lab\\portfolio_exit_replay` |
| P286 | Explain missed pumps and category parity | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | diagnostics/parity | Replace generic top-growth missed-pump reasons with concrete precise-scan reject events/reasons and add context parity columns for `discovery_only`, `live_priority_pass`, `live_priority_reject_reason`, `strict_live_replay_enter`. | `.venv\\Scripts\\python.exe -m compileall -q research_tools\\anomaly_micro_live.py research_tools\\anomaly_strategy_backtest.py cli constants.py main.py` |
| P285 | Active tail-stale context suffix refresh and 24h fake-pump window | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_category_contract.py`, `research/*` | live-data-quality/category | For active/retryable symbols whose prior fake-pump context is stale, fetch only the missing levels-TF OHLCV suffix, update cache/snapshot, and re-check the category. Reduce live prior fake-pump lookback from 72h to 24h and allow up to 2 prior fast-fade events. | `.venv\\Scripts\\python.exe -m compileall -q research_tools/anomaly_micro_live.py research_tools/anomaly_category_contract.py cli constants.py main.py` |
| P284 | Refresh priority context under latency pressure | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live-data-quality | Let open/active/retryable-dependency symbols run a tiny cache-only symbol-context snapshot refresh even when optional context snapshots are gated by latency SLA, so candidates do not expire only because prior-fast-fade context stayed stale. | `python -m compileall -q research_tools/anomaly_micro_live.py cli constants.py main.py` |
| P240 | Treat unavailable category context as retryable dependency | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-diagnostics | Stop emitting `category_rejected` / delayed replay final-reject cases when prior-fast-fade context is unavailable; keep the decision unconsumed and record `signal_scan_retryable_dependency_blocked` with blocked categories/details. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P239 | Startup context backfill and gap tolerance | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-data-quality | Backfill and compute 72h+baseline prior-fast-fade context on live startup by default using levels timeframes only, and accept only tiny internal OHLCV gaps under explicit coverage limits. | `python -m compileall data/exchanges research_tools cli constants.py main.py` |
| P123 | Decouple anomaly runtime helpers | PROPOSED | `research_tools/anomaly_config.py`, `research_tools/charting.py`, anomaly live/backtest modules | cleanup | Move timeframe validation and chart helpers into anomaly-owned/common modules. | `python -m compileall research_tools/anomaly_config.py research_tools/charting.py research_tools/anomaly_micro_live.py research_tools/anomaly_strategy_backtest.py` |
| P124 | Isolate retired strategy CLI imports | PROPOSED | `cli/commands.py`, `cli/parser.py`, `config/*`, `strategy/factory.py` | cleanup | Stop startup/config/parser paths from importing retired strategy modules. | `python -m compileall cli/commands.py cli/parser.py config strategy main.py` |
| P125 | Prune unused launcher and dead constants | PROPOSED | `launcher.py`, `constants.py`, docs | cleanup | Remove unused wrapper and dead sizing constants. | `python -m compileall constants.py cli config research_tools main.py` |
| P126 | Remove stale historical pytest marker | PROPOSED | `pyproject.toml`, `research/*` | cleanup | Remove a test marker for a test suite absent from the ZIP source. | `git grep historical_marker_name` equivalent returns empty. |
| P127 | Neutralize old sizing names | PROPOSED | `constants.py`, `config/*`, runtime modules | cleanup | Rename old sizing defaults to neutral position defaults. | `git grep old_sizing_prefix` equivalent returns empty. |
| P128 | Remove retired strategy active path | PROPOSED | `strategy/`, `cli/`, `config/`, `README.md`, `research/*` | deletion | Remove retired strategy source, diagnostics, CLI, config and factory path. | `python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py` |
| P129 | Purge retired strategy history from active memory | PROPOSED | `README.md`, `research/*.md` | cleanup | Remove stale historical strategy references from current project docs/logs. | `git grep -i retired_strategy_token -- .` returns empty for tracked files. |
| P209 | DANGER adaptive cold coverage controller | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-performance | Scale default/explicit precise cold coverage by WS health, scheduler heartbeat EWMA, active-waiting load and REST/cache pressure; hard-off for positions and active-due symbols. | `python -m compileall data/exchanges research_tools constants.py main.py` |
| P213 | Warm-watch scheduler gate | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-scheduler | Insert warm-watch between cheap ticker/flow radar and precise scan; subscribe warm symbols to micro-cache but promote to precise only after continuing flow and non-chase/non-fade checks. | `python -m compileall data/exchanges research_tools cli constants.py main.py` |
| P218 | Budget optional context and warm micro-cache | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance | Move symbol context snapshot maintenance after the critical scan path, SLA/budget it, use effective freshness based on universe throughput, and cap warm-watch aggTrade targets without capping active/radar. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help | grep -E "warm-watch-aggtrade|symbol-context-snapshot-max"` |
| P219 | Fix live order-id NameError and fatal Telegram alert | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-reliability | Import `re` for deterministic live client order ids and send fatal live/internal-integrity errors synchronously to Telegram, recording `telegram_sync_send_failed` if delivery itself fails. | `python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py`; live smoke with one cycle or synthetic `_live_client_order_id` call. |
| P220 | Protect unresolved entry-order failures | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-safety | Track the symbol before market entry submission and close any actual unprotected exchange exposure if entry order/fill resolution raises before a `LivePosition` and stop are created. | `python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py`; synthetic fill-resolution failure should emit `unprotected_entry_reduce_only_exit_filled`. |
| P221 | Fix live ledger scan-mode fields | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-reliability | Store `source_scan_mode`, cold-coverage marker and entry-position guard source on `LivePosition` and include them in `live_positions.csv` schema instead of referencing undefined writer locals after an entry opens. | `python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py`; synthetic `LiveArtifactWriter.append_position()` smoke. |
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
| P167 | Symbol-first live multi-TF scan | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance | For selected live symbols, scan all due TF sets before moving to the next symbol, cache setup/entry fetches within the batch, and add an explicit cold round-robin slot cap so fast live can prioritize active/radar symbols without hiding skipped work. | `python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py`; `python main.py run-anomaly-live --help`. |
| P168 | Cache-backed live OHLCV fetch | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance/data-quality | Live reads local parquet first, fetches only missing OHLCV/aggTrade-derived ranges, writes fetched rows with provenance, keeps a process-memory frame cache, and emits explicit cache read/gap artifacts. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help`. |
| P169 | Fix live cache candle boundary reads | PROPOSED | `data/storage/parquet_storage.py`, `research_tools/anomaly_micro_live.py`, `research/*` | bugfix/live-performance | Read only requested parquet windows, fetch subminute missing ranges through the full final candle, pass aggTrades endTime on paged requests, and exclude non-closed cached candles from live decision frames. | `python -m compileall data/storage/parquet_storage.py research_tools/anomaly_micro_live.py`; `python main.py run-anomaly-live --help`. |
| P170 | Buffer live OHLCV cache writes | PROPOSED | `research_tools/anomaly_micro_live.py`, `cli/*`, `research/*` | live-performance/data-quality | Buffer live-fetched OHLCV rows in memory, flush parquet writes by interval/row cap or on shutdown/error, and emit buffered/flushed/failed artifacts instead of rewriting parquet during every fetch. | `python -m compileall data/exchanges data/storage research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help`. |
| P181 | Number live batches within full symbol cycles | PROPOSED | `research_tools/anomaly_micro_live.py`, `research/*` | live-operator-ux | Show operator status as `cycle batch/full-cycle` so batch ticks are not confused with full universe passes. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; `python main.py run-anomaly-live --help`. |

## P219 — Fix live order-id NameError and fatal Telegram alert

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-15
Commit: UNKNOWN

### Reason

The real `run-anomaly-live --confirm-real-orders` run `20260514_201044` selected GWEI as `runner_reclaim` and reached the live entry path, then crashed before order submission with `NameError: name 're' is not defined` in `_live_client_order_id()`. The fatal error Telegram notification was queued asynchronously and the process shut down immediately, so no Telegram failure/success artifact appeared and the operator did not receive an alert.

### Change

- Add the missing `re` import used by deterministic live client order id generation.
- Add `TelegramDispatcher.send_critical_sync()` for fatal live/data-integrity errors.
- Record `telegram_sync_send_failed` if the synchronous critical Telegram send fails.
- Use the synchronous path for `live_internal_error` and `live_data_integrity_error`.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Low. This is an import/runtime reliability fix and a fatal-error notification path. Signal filters, category selection, order sizing, stop/TP math, and position monitoring are unchanged.

## P220 — Protect unresolved entry-order failures

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-15
Commit: UNKNOWN

### Reason

Reviewing the last 15 live commits found a safety gap in the real entry path. If Binance accepted a market entry but `create_market_order_with_fill()` raised while resolving the fill, live had not yet created `LivePosition`, had not placed the initial stop, and had not tracked the symbol for forced reconcile. A generic internal-error exit could therefore leave real exposure outside the local position ledger.

### Change

- Track the symbol for order reconciliation before submitting the market entry.
- Wrap entry order/fill resolution.
- On any exception, call `_close_unprotected_entry_exposure()` with the requested entry amount and a deterministic unresolved entry id before re-raising.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py
# synthetic: create_market_order_with_fill raises after exchange position appears -> reduce-only close is attempted
```

### Risk

Low-to-medium. This only changes failure handling after an entry order/fill exception. In the normal filled path, sizing, signal selection, stop/TP math, and position monitoring are unchanged.

## P221 — Fix live ledger scan-mode fields

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-15
Commit: UNKNOWN

### Reason

The live health review found a second real entry-path crash after P219. `LiveArtifactWriter.append_position()` attempted to write `source_scan_mode`, `danger_cold_coverage_source`, and `entry_position_guard_source`, but those values were neither in `LIVE_LEDGER_COLUMNS` nor available as locals inside the writer. A successful entry that got past order id creation, fill validation, and stop creation could therefore fail while appending `live_positions.csv`.

### Change

- Add the three scan/guard diagnostic fields to the live ledger schema.
- Persist those fields on `LivePosition` when the entry is opened.
- Make `append_position()` read the diagnostics from `position` instead of undefined writer locals.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli constants.py main.py
```

### Risk

Low. This changes only live ledger diagnostics and prevents a post-entry writer crash. Order selection, sizing, fill validation, stop creation, TP and monitor logic are unchanged.

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
- Rename the heartbeat text to a cycle-duration label; this was later superseded by P201's prefix-free heartbeat.
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

Status: APPLIED locally / UNKNOWN commit
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

---

## P156 — Stop unresolved live exits from producing proxy PnL

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Live could remove an already-closed exchange position from monitoring while still writing it as a normal `position_closed` row with PnL calculated from a proxy stop/last-known price when the actual exit fill was not recovered. That makes edge/PnL review look more certain than the exchange evidence supports. The same patch also fixes the Linux CLI startup blocker caused by importing `ctypes.windll` at module import time.

### Change

- Import `ctypes.windll` only inside the Windows console-encoding branch so `python main.py -h` works on Linux.
- Add ledger terminal status `exit_unresolved` for live positions whose exchange exposure is gone but actual exit fill is unknown.
- Replace proxy-PnL finalization for external zero-position and unresolved stop-fill cases with `position_exit_unresolved` artifacts and blank realized PnL fields.
- Keep stop cooldown accounting when the stop appears to have closed the exchange position but the stop fill cannot be recovered.
- Preserve verified-fill close behavior for TP1/full stop exits.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-anomaly-live --help
# fake-exchange smoke: external zero amount -> position_exit_unresolved, ledger status=exit_unresolved, no realized PnL
# fake-exchange smoke: stop trigger + fetch_order_fill failure -> position_exit_unresolved, ledger status=exit_unresolved, no realized PnL
```

### Risk

Medium audit semantics change: some rows previously counted as normal closed trades now become non-PnL terminal rows. Trading entry logic, verified stop placement, TP1 behavior, and verified stop-fill close logic are unchanged.


---

## P158 — Split HTF setup from LTF live entry

Status: SUPERSEDED by P159 combined patch
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The previous live code used the configured `levels_timeframe/entry_timeframe` pair mostly as a label: the signal was built on the higher timeframe only. P157 was intentionally not applied because making the entry timeframe the whole signal source would reject valid HTF flow wake-ups whenever micro candles were noisy. The strategy needs a two-stage contract instead: HTF/forming-HTF detects the pump wake-up, LTF confirms that the entry is still executable.

### Change

- Live scans every closed `entry_timeframe` candle, not every closed `levels_timeframe` candle.
- Live builds a forming HTF setup candle from closed LTF candles inside the current HTF bucket.
- HTF/forming-HTF owns dormancy baseline, quote-volume/trade-count anomaly, exhaustion checks, initial risk box and setup context.
- LTF owns freshness, entry activation hold, verticality/path confirmation and final live execution guards.
- Sub-minute live frames keep zero-flow buckets visible so no-trade periods do not disappear from the signal path.
- Backtest gains pair-aware `--setup-timeframe` / `--entry-timeframe` mode with `feature_contract=htf_setup_ltf_entry_v1`.
- Backtest forms HTF setup rows from historical entry-timeframe candles and simulates entries/exits on the entry timeframe.
- `entry_delay_candles` is now calculated from the actual simulation frame step.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic pair-aware backtest smoke: 5m setup + 30s entry creates one forming_htf_from_entry_tf_backtest candidate after 4 closed entry candles
```

### Risk

Medium. This changes signal timing and candidate construction. It should increase comparability between live and backtest, but historical results from older single-timeframe backtests are not directly comparable to `htf_setup_ltf_entry_v1` runs. Requires cache for the chosen entry timeframe; without it, pair-aware backtest records explicit read errors instead of pretending comparability.

---

## P159 — HTF/LTF live health and rounded TP1 target

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

P158 fixed the fake timeframe-label problem but left four live-health issues: forming HTF flow was compared to full HTF baseline without pace normalization, transient entry/setup fetch failures consumed the scan slot, pair-aware sub-minute backtest could silently depend on missing historical entry-TF cache, and TP1 remained a raw 1R level rather than the next usable round number above 1R.

### Change

- TP1 is now the nearest higher round market number above the previous 1R TP1, with the rounding step derived from current price and movement size.
- Live pre-order RR/TP-already-reached guards use the rounded TP1, and verified-fill position management recomputes TP1 from the actual fill then rounds it again.
- Forming HTF setup flow uses pace-normalized quote/trade ratios while also requiring a raw-progress floor so tiny early microbursts do not pass as HTF wake-ups.
- Live scan timestamps are marked only after setup and entry data fetch/build completes; transient fetch failures are visible but retryable on the next cycle.
- Pair-aware backtest applies the same pace-normalized forming HTF ratio contract.
- Pair-aware sub-minute backtest now explicitly errors with `missing_subminute_entry_cache:<tf>` / `requires_historical_aggtrades_cache` when no historical entry-TF cache exists instead of pretending comparability.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic: 1R TP is rounded upward to the next movement-sized 1/2/5 grid level
# synthetic: forming 5m from 30s uses quote/trade pace ratios plus raw-progress floor
# synthetic: setup/entry fetch failure does not advance _last_signal_scan_closed_at
```

### Risk

Medium. TP1 is now usually farther than exactly 1R, so TP1 hit-rate may drop while RR/chase checks become more meaningful. Pace-normalized forming HTF can admit earlier setups, but the raw-progress floor is meant to prevent single-bucket noise from passing. Historical results before P159 are not directly comparable.

---

## P160 — Remove retired closed-HTF live/backtest code

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

After P159, the active live path is `forming HTF setup from closed LTF buckets -> LTF entry permission`. The older closed-HTF signal builder remained in `anomaly_micro_live.py` but was no longer reachable. Backtest also retained two unused helper functions from the older single-timeframe/grid path. Leaving these around increases the risk that future patches edit inactive code or reintroduce closed-HTF semantics by mistake.

### Change

- Remove unused live methods `_build_recent_signals()` and `_build_signal_at_start()`.
- Remove unused Telegram/category formatting helpers `_detail_float()` and `_format_category_rejection_summary()`.
- Remove unused backtest helpers `_resolve_signal_stop()` and `_entry_grid_signal_universe()`.
- Keep active HTF/LTF signal construction, category rejection events, order/fill/stop handling, TP1 rounding and pair-aware backtest logic unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
```

### Risk

Low. This is deletion-only cleanup of functions with no runtime call sites in the current P159 path. No signal thresholds, entry/exit rules, order handling, stop handling, TP1 math, or backtest execution semantics are changed.

---

## P161 — Fix anomaly market-entry safe divide helper

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Market-entry backtest execution crashed during `simulate_anomaly_trades()` because `_resolve_signal_entry()` called the old helper name `_safe_divide`, while this module defines and uses `_safe_divide_value`.

### Change

- Replace the two stale `_safe_divide(...)` calls in market-entry drift/RR guards with the existing local `_safe_divide_value(...)` helper.
- Keep market-entry execution semantics unchanged: drift remains absolute, TP1-before-entry and RR-collapse guards still use next-bar-open proxy prices.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP
# synthetic smoke: market entry path calls _resolve_signal_entry without NameError
```

### Risk

Low. This is a direct NameError fix in the executable backtest path; no thresholds, signal filters, TP/SL logic, live order logic, or data-quality fallback behavior change.

---

## P162 — Fix live terminal PnL amount accounting

Status: PROPOSED
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Live terminal PnL could double-count size when a position had `remaining_amount == 0`. `_finalize_position()` fell back to the original `position.amount`, so a TP1/full-close path could add a second terminal PnL leg on top of already realized PnL. A second related audit issue existed in the TP1 monitor branch: if the TP1 reduce-only fill was only partial but the exchange position became zero, the disappeared remainder had no verified exit fill and should not be written as a normal closed trade with proxy PnL.

### Change

- `_finalize_position()` now uses the verified remaining amount by default and accepts an explicit terminal `exit_amount`; it never falls back from zero remaining amount to the original entry amount.
- `position_closed` events now include terminal exit amount/price and realized PnL before the terminal leg for audit.
- TP1 monitoring now tracks previous remaining amount, TP1 filled amount and expected remaining amount.
- If TP1 fill is confirmed but a material residual position disappears without a verified fill, live records `tp1_remaining_exit_unresolved` and finalizes as `position_exit_unresolved` with blank realized PnL instead of fabricating a normal closed trade.
- If TP1 genuinely closes the whole remaining amount, `_finalize_position(..., exit_amount=0.0)` records only the already verified realized PnL.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic smoke still needed: remaining_amount=0 terminal close must not add position.amount PnL
# synthetic smoke still needed: partial TP1 fill + exchange amount zero -> position_exit_unresolved, blank realized PnL
```

### Risk

Low-to-medium audit semantics change. Some rows that previously looked like normal profitable/loss-making closes can become `exit_unresolved` when the residual exchange exposure disappears without a verified fill. That is intentional; trading entry/selection logic is unchanged.

---

## P163 - Export anomaly flow provenance and rework trade-chart stack

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The latest `anomaly_lab` artifact could not prove quote-volume / trade-count provenance from exported CSVs, which blocks strong conclusions about anomaly nature at artifact-review level. The trade chart also needed an operator-driven layout correction: visible HTF context, bottom `1h` panel, 4-day level-search window, Russian panel labels, and less merged candle rendering on dense windows.

### Change

- Export explicit anomaly flow provenance fields into candidate/signal/trade artifacts:
  - `trade_count_proxy_used`
  - `levels_trade_count_source`
  - `entry_trade_count_source`
  - `levels_quote_volume_source`
  - `entry_quote_volume_source`
- Keep provenance explicit as cached OHLCV native fields; no proxy fallback is introduced.
- Rebuild canonical anomaly trade charts as:
  - LTF panel
  - HTF panel
  - normalized volume/trades panel
  - bottom 1h context panel
- Make 1h context end at the hour candle that contains the main chart end, use a 4-day window, and search chart levels only inside that same 4-day window.
- Render Russian watermark-style panel names in the upper-left background of each panel.
- Narrow candle bodies adaptively as panel density increases.
- Fetch 4-day 1h context for both live open and close charts.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --help
# synthetic/render smoke: canonical chart contains LTF/HTF/flow/1h panels and saves successfully
# artifact check: provenance columns exist in anomaly_candidates.csv / anomaly_signals.csv / anomaly_trades.csv
```

### Risk

Low for trading logic: artifact/export/chart behavior only. Medium for visual artifact expectations: chart layout and chart aspect ratio change materially, so chart consumers should not assume the old three-panel geometry.

---

## P164 - Handle empty subminute anomaly signal sets

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

Running the intended subminute TF sets (`5m/30s`, `1m/15s`, `1m/5s`) exposed a diagnostics bug when entry-timeframe cache is absent. The pair-aware collector can return no valid signal rows, but derivative-context fetch setup still tried to select `symbol` and `decision_timestamp_ms` from an empty no-column signals frame.

### Change

- If post-filter signals are empty or lack the signal-universe columns, use an empty `symbol/decision_timestamp_ms` frame.
- Let the existing no-signal derivative context path write empty fetch status instead of crashing.
- Reduce trade-chart watermark label font size from 28 to 18.
- For pair-aware subminute backtests, use cached `1s` OHLCV as an in-memory source for `5s/15s/30s` entry frames when exact entry-timeframe cache directories are absent.
- Mark entry provenance as `cached_1s_aggregated_to_<tf>` when that fallback is used.
- Use the same `1s -> target subminute` aggregation path during trade simulation, not only during candidate construction.

### Validation

```bash
python -m compileall research_tools/anomaly_strategy_backtest.py
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 7
python main.py run-anomaly-lab --setup-timeframe 1m --entry-timeframe 15s --days 7
python main.py run-anomaly-lab --setup-timeframe 1m --entry-timeframe 5s --days 7
```

### Risk

Low/medium: diagnostics-only, but fresh subminute backtests may now run from `1s` cache instead of failing on absent aggregated `5s/15s/30s` cache directories. It does not aggregate from `1m`, and provenance labels distinguish the `1s` aggregation path.

---

## P165 - Materialize subminute anomaly cache and add red-flag profiles

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

True anomaly TF-set runs were too slow and the 7-day result was misleading: the setup cache covered the requested end timestamp, but executable `1s`/derived subminute cache ended on 2026-05-06 11:40 UTC while the requested end was 2026-05-11 08:36 UTC. That made the run effectively a 3-active-day sample. The first red-flag filters also needed to be explicit and audit-visible, not hidden in post-analysis.

### Change

- Add `materialize-anomaly-subminute-cache` CLI command.
- Materialize honest `1s -> 5s/15s/30s` parquet caches with aggregation provenance columns.
- Preserve provenance in anomaly artifacts when exact subminute cache is materialized from `1s`.
- Add `entry_cache_coverage.csv` to anomaly-lab artifacts so requested start/end coverage is visible.
- Replace repeated boolean LTF slicing in pair candidate collection with timestamp `searchsorted`.
- Add optional `red_flag_profile` values:
  - `none`
  - `cautious`
  - `strict`
- Add explicit pre-decision red-flag filters for mark basis, OI-down + mark discount, stale/missing context, taker-buy delta and trade/quote effort per return.
- Add `anomaly_red_flag_summary.csv` over the pre-red-flag signal universe.
- Correct intended anomaly TF-set config from `5m/15s` to `1m/15s`.

### Validation

```bash
python -m compileall research_tools/anomaly_strategy_backtest.py research_tools/anomaly_config.py cli/parser.py cli/commands.py
python main.py materialize-anomaly-subminute-cache --timeframes 5s 15s 30s --overwrite true --output .output/results/anomaly_subminute_cache_materialization_p165.csv
python main.py run-anomaly-lab --days 7 --setup-timeframe 5m --entry-timeframe 30s --end-timestamp-ms 1778488560000 --output-dir .output/results/anomaly_lab_p165_5m30s_cautious --run-entry-grid false --red-flag-profile cautious
python main.py run-anomaly-lab --days 7 --setup-timeframe 1m --entry-timeframe 15s --end-timestamp-ms 1778488560000 --output-dir .output/results/anomaly_lab_p165_1m15s_cautious --run-entry-grid false --red-flag-profile cautious
python main.py run-anomaly-lab --days 7 --setup-timeframe 1m --entry-timeframe 5s --end-timestamp-ms 1778488560000 --output-dir .output/results/anomaly_lab_p165_1m5s_cautious --run-entry-grid false --red-flag-profile cautious
```

### Risk

Medium research risk: `cautious`/`strict` profiles are not production-proven and are currently fit from a very small 3-active-day sample. Defaults remain unchanged. Low data-risk: derived caches are explicitly marked as `1s` aggregation and do not fabricate missing post-2026-05-06 executable data.

---

## P166 - Add aggTrade backfill and runner/dormancy diagnostics

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-12
Commit: UNKNOWN

### Reason

The intended 14-day anomaly study needs honest executable subminute data. Binance futures kline API rejects `1s` with `Invalid interval`, so `update-cache --timeframes 1s` cannot be used for this. Recent-spike / dormancy and early-runner separation also needed first-class artifact columns instead of ad hoc notebook logic.

### Change

- Add `backfill-anomaly-aggtrade-cache` CLI command to build true `1s` OHLCV from Binance futures `aggTrades`.
- Add chunk skipping for already covered `1s` windows and write a backfill manifest.
- Add `--render-charts true/false` to anomaly-lab so metric sweeps can skip PNG rendering.
- Add recent-spike diagnostics to pair-aware candidates/signals/trades:
  - `prior_spike_count_24h/72h`
  - `prior_fast_fade_count_24h/72h`
  - `prior_big_move_count_24h/72h`
  - `prior_spike_density_72h`
  - `time_since_prior_spike_ms/hours`
- Prior fast-fade / big-move counts only include matured prior spikes whose diagnostic forward window ended before the current decision.

### Validation

```bash
python -m compileall cli/commands.py cli/parser.py research_tools/anomaly_strategy_backtest.py
python main.py backfill-anomaly-aggtrade-cache --help
python main.py backfill-anomaly-aggtrade-cache --symbols COIN/USDT:USDT HOOD/USDT:USDT --days 1 --end-timestamp-ms 1778488560000 --chunk-hours 24
python main.py materialize-anomaly-subminute-cache --timeframes 5s 15s 30s --overwrite true
python main.py run-anomaly-lab --days 14 --setup-timeframe 5m --entry-timeframe 30s --end-timestamp-ms 1778488560000 --render-charts false
python main.py run-anomaly-lab --days 14 --setup-timeframe 1m --entry-timeframe 15s --end-timestamp-ms 1778488560000 --render-charts false
```

### Risk

Medium. The aggTrade backfill is honest but can be very slow for a large active universe. Full `1m/5s` 14-day universe did not complete in one hour; use active-symbol subset results only as directional evidence.

## P167 - Symbol-first live multi-TF scan

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

REST-only live should not spend heavy subminute aggTrade work evenly across cold symbols when the goal is early runner capture. Once a symbol is selected by active state or ticker radar, all configured TF sets should be checked together so `5m/30s`, `1m/15s`, and `1m/5s` do not wait on separate outer loops.

### Change

- Add `scan_hot_timeframes_per_symbol=true` as the default live scan mode.
- Add `inactive_scan_slots_per_cycle` as an explicit optional cap for cold round-robin scans; `0` means active/radar-only scanning.
- Cache setup and entry frames inside a live batch so shared setup windows are not refetched for the same symbol.
- Export `signal_symbol_scan_summary` and add scan-mode/cold-slot fields to `symbol_batch_selected`.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: setting `--inactive-scan-slots-per-cycle 0` deliberately stops cold round-robin discovery and relies on ticker radar plus active state. This is suitable for fast production-style live only if ticker snapshots are healthy; artifacts now show the chosen cap and effective scan count.

## P168 - Cache-backed live OHLCV fetch

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P167 reduces which symbols are scanned, but selected symbols still re-requested full setup/entry windows from the exchange. REST-only live should reuse the same honest parquet cache as research where possible, fetch only exact missing ranges, and make any remaining cache gap explicit.

### Change

- Wire `cache_dir` into `LiveAnomalyConfig`.
- Add `--live-ohlcv-cache-enabled` and `--live-ohlcv-cache-write-enabled` CLI flags.
- Route live setup and entry frame fetches through a parquet-backed provider.
- Load cached frames once per process and reuse them from memory across cycles.
- For missing ranges, fetch only those ranges from exchange OHLCV or Binance futures `aggTrades`, then write one incremental parquet batch with `live_cache_source`, target timeframe and version.
- Emit `live_cache_config`, `live_ohlcv_cache_read`, and `live_ohlcv_cache_gap` events.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Medium: `save_incremental` rewrites the target parquet file, so live cache writes should be watched during high-churn runs. Remaining gaps are not hidden; they emit `live_ohlcv_cache_gap` and can still lead to existing empty/missing-signal rejects.

## P169 - Fix live cache candle boundary reads

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Review of P168 found a candle-boundary risk: live cache missing ranges are represented by candle start timestamps, but subminute `aggTrades` must be fetched through the end of the final candle. Reading whole parquet files on first use was also unnecessary work for large caches.

### Change

- Add `ParquetStorage.load_window_result()` with parquet timestamp filters and safe full-load fallback.
- Live cache first-load now reads only the requested closed-candle window.
- Missing range fetches now request `missing_end + timeframe_ms - 1`, so the last subminute candle is complete.
- Paged `aggTrades` calls keep `endTime`, reducing overshoot beyond the requested window.
- Live decision frames return only closed cached candles up to `expected_end_ms`; accidental current/partial cached rows are excluded.
- `live_ohlcv_cache_read` now reports expected/window end timestamps.

### Validation

```bash
python -m compileall data/storage/parquet_storage.py research_tools/anomaly_micro_live.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: parquet filter support depends on the installed parquet engine; fallback preserves behavior by loading and filtering in memory. The main remaining speed limit is `save_incremental`, which still rewrites the target parquet file when live writes fetched rows.

## P170 - Buffer live OHLCV cache writes

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P168/P169 made live cache-backed and closed-candle aligned, but `save_incremental` still rewrites the target parquet file. Calling it during each selected-symbol fetch can slow the live loop exactly when hot symbols need fast follow-up scans.

### Change

- Add `--live-ohlcv-cache-flush-interval-seconds` and `--live-ohlcv-cache-max-buffer-rows`.
- Buffer fetched live OHLCV rows in process memory after they are already merged into the decision frame cache.
- Flush buffered parquet writes after the interval/cap, and force flush on max-cycles, keyboard interrupt, data-integrity stop and internal-error stop.
- Emit `live_ohlcv_cache_buffered`, `live_ohlcv_cache_flushed`, `live_ohlcv_cache_flush_failed`, and `live_ohlcv_cache_flush_summary`.
- If memory cache does not cover a wider later request, read that window from parquet before going to the exchange.

### Validation

```bash
python -m compileall data/exchanges data/storage research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: a hard process crash can lose rows still in the write buffer, but trading decisions use the in-memory fetched frame immediately and the missing cache rows can be re-fetched. Flush failures are kept visible and failed rows remain pending for the next flush attempt.

## P171 - Run anomaly lab across working TF sets by default

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

`python main.py run-anomaly-lab --days 7` silently inherited the legacy parser default `--timeframe 1m`, so the command ran `1m/1m` instead of the current working anomaly TF sets.

### Change

- Remove the hidden `--timeframe 1m` parser default.
- Treat no explicit timeframe flags as a multi-run over `5m/30s`, `1m/15s`, and `1m/5s`.
- Keep explicit `--timeframe`, `--setup-timeframe`, or `--entry-timeframe` as single-pair override mode.
- Write multi-run outputs into per-pair subdirectories and emit `anomaly_lab_timeframe_runs.csv`.

### Validation

```bash
.venv\Scripts\python.exe -m compileall cli\commands.py cli\parser.py research_tools\anomaly_strategy_backtest.py research_tools\anomaly_config.py main.py
.venv\Scripts\python.exe -c "from cli.parser import build_parser; p=build_parser(); a=p.parse_args(['run-anomaly-lab','--days','7']); print(a.timeframe, a.setup_timeframe, a.entry_timeframe)"
```

Monkeypatched smoke confirmed default pairs:

```text
5m/30s
1m/15s
1m/5s
```

### Risk

Low/medium: the default command now does roughly three backtests instead of one, so runtime and chart generation cost increase. This is intended because the old default result was misleading; explicit `--timeframe 1m` still allows the legacy single run.

## P172 - Collect anomaly TF sets in one symbol-major pass

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P171 made the default anomaly lab run all working TF sets, but it still launched three independent backtests. That meant the backtest walked the symbol universe once per TF pair instead of checking all TF sets while the symbol frames were already loaded.

### Change

- Add `collect_pair_anomaly_rows_for_configs()` for symbol-major candidate collection across multiple `AnomalyBacktestConfig` objects.
- Read each symbol/timeframe frame once per symbol pass, then evaluate all eligible TF pairs for that symbol.
- Let `run_anomaly_strategy_backtest()` accept `precollected_candidates`, preserving existing per-pair artifact writing, signal filtering, trade simulation, grids and charts.
- Change default `run-anomaly-lab` multi-TF mode to precollect candidates once, then run per-pair artifact pipelines from those precollected rows.
- Add `collection_mode` to `anomaly_lab_timeframe_runs.csv`.

### Validation

```bash
.venv\Scripts\python.exe -m compileall cli\commands.py cli\parser.py research_tools\anomaly_strategy_backtest.py main.py
```

Smoke checks:

```text
CLI default calls collect_pair_anomaly_rows_for_configs once with 5m/30s, 1m/15s, 1m/5s, then runs three per-pair artifact pipelines with precollected candidates.
Direct single-config comparison on ATH/USDT:USDT 1m/15s over 7 days produced the same candidate decision timestamp in single and multi collectors.
```

### Risk

Medium: candidate collection order changed for default multi-TF runs. The per-pair downstream logic is unchanged, but the next full run should compare candidate/signal counts against the prior indexed run for the same end timestamp before using profitability deltas.

## P173 - Add runner category filters and candidate reuse replay

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The latest anomaly-lab directory had completed 5m/30s and 1m/15s artifacts, but 1m/5s was interrupted. Recollecting all candidates took hours, while filter iteration only needs the existing enriched `anomaly_candidates.csv` files.

### Change

- Add red-flag profiles: `runner_balanced`, `runner_reclaim`, `runner_flow`.
- Add filter fields for flow hold, 72h prior spike/fade counts, lower wick and upper wick shape.
- Extend red-flag summaries to include quote/trade ratio caps and effort-per-return caps.
- Add `--reuse-candidates-dir` to `run-anomaly-lab`; it replays filters from existing candidate artifacts and skips missing pair artifacts explicitly.
- Reused candidate mode avoids refetching derivatives context because existing candidates are already enriched.

### Validation

```bash
.venv\Scripts\python.exe -m compileall cli\commands.py cli\parser.py research_tools\anomaly_strategy_backtest.py main.py
.venv\Scripts\python.exe main.py run-anomaly-lab --help
.venv\Scripts\python.exe main.py run-anomaly-lab --days 30 --reuse-candidates-dir .output\results\anomaly_lab --output-dir .output\results\anomaly_lab_reuse_runner_balanced_fast --red-flag-profile runner_balanced --render-charts false
```

### Result

Replay used completed pairs only:

```text
5m/30s runner_balanced: 42 closed, avg +1.6435%, median +1.5601%, sum +69.03%, WR 83.33%, TP1 78.57%
1m/15s runner_balanced: 82 closed, avg +1.8407%, median +1.2530%, sum +150.94%, WR 79.27%, TP1 74.39%
Combined: 124 closed, avg +1.7739%, median +1.3985%, sum +219.97%, WR 80.65%, TP1 75.81%, active days 25
```

### Risk

Medium/high: this is an in-sample replay on partial executable coverage and excludes the interrupted 1m/5s pair. Treat it as a strong category hypothesis, not proven production edge.

## P174 - Align live entry category with OI-confirmed runner replay

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The previous live defaults traded `balanced_market,mild_market`, while the strongest recent backtest category was a runner-oriented replay. OI is materially useful in the latest artifacts, but the useful zone is moderate confirmation, not the old live default `OI > 5%`.

### Change

- Add backtest red-flag profile `runner_oi_confirmed`.
- Add live pump category `runner_oi_confirmed` and make it the live default category.
- Live `runner_oi_confirmed` requires `OI 3x5m > 0.3%`, mark basis >= 30bp, quote/trade ratio caps, quote/trade effort caps and start taker-buy delta cap.
- Live mark basis is fetched from Binance mark-price context as-of the decision timestamp; missing/stale mark context is a category reject, not a fallback.
- Live OI requirement is category-level; the global live OI default is now unset unless a category or CLI flag requires it.

### Validation

```bash
.venv\Scripts\python.exe main.py run-anomaly-lab --days 30 --reuse-candidates-dir .output\results\anomaly_lab --output-dir .output\results\anomaly_lab_reuse_runner_oi_confirmed --red-flag-profile runner_oi_confirmed --render-charts false
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py research_tools\anomaly_strategy_backtest.py cli\parser.py cli\commands.py
```

Replay used completed pairs only:

```text
5m/30s runner_oi_confirmed: 15 closed, avg +2.574%, median +1.717%, sum +38.61%, WR 100.00%, TP1 100.00%
1m/15s runner_oi_confirmed: 21 closed, avg +3.014%, median +2.710%, sum +63.29%, WR 80.95%, TP1 76.19%
Combined: 36 closed, avg +2.831%, median +1.885%, sum +101.90%, WR 88.89%, TP1 86.11%, top10 dependency 75.58%
```

### Risk

High: this is very selective, in-sample, excludes interrupted 1m/5s, and top10 dependency is high. Live still cannot perfectly enforce backtest `prior_fast_fade_count_72h` until it has persisted enough same-symbol signal outcomes.

## P175 - Add live entry lag diagnostics

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Live can arrive after the first closed-candle executable entry that the backtest model would have used. This must be measured without slowing the trading path.

### Change

- Add `first_executable_entry_timestamp_ms`, `entry_lag_ms`, `entry_lag_ltf_candles`, `entered_late_vs_first_executable`, `entry_order_submit_lag_ms`, and `entry_order_submit_lag_ltf_candles` to `live_positions.csv`.
- Add `previous_live_scan_closed_timestamp_ms`, `first_unscanned_decision_timestamp_ms`, and `live_scan_gap_ltf_candles` to distinguish order-entry lag from symbol-scheduler scan lag.
- Add non-blocking `missed_entry_replay_probe`: when a selected signal has a scan gap, a background thread replays the skipped LTF decision candles from the already loaded OHLCV window and emits the first earlier candle where the same live category filter would have selected a signal.
- Add the same fill-lag fields to `position_opened` events.
- Add entry-lag diagnostics to execution guards/reject events such as stale signal, entry drift and RR collapse.
- The diagnostic uses only existing signal timestamps, order submission timestamp, fill timestamp and the already-fetched live price. It performs no extra exchange request.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py
Inline smoke: synthetic 1m/15s scan-gap replay found the first prior signal at the 4th skipped 15s candle and reported a 6-LTF-candle lag to the current signal.
```

### Risk

Low: this is diagnostic-only. Existing live ledger readers that assume the exact old column set may need to tolerate the added columns.

## P176 - Make missed-entry replay probe honest and budget-tunable

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The missed-entry probe must not understate live scheduler lag when the skipped window is larger than the probe cap, and API/context limits are scarce.

### Change

- Replay skipped LTF decisions from the earliest missed candle, not from the newest skipped tail.
- Add `candidate_decision_count_total`, `probe_truncated`, and `unprobed_newer_decision_count` to probe events.
- If no prior signal is found in a truncated prefix, emit `no_prior_signal_found_in_probed_prefix` instead of implying that the full skipped window was checked.
- Expose live `--signal-scan-backfill-candles` so the probe budget can be lowered without code edits.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
```

### Risk

Low/medium: this is diagnostic-only, but OI/mark-confirmed categories can still spend context calls inside the background probe. Use a lower `--signal-scan-backfill-candles` when exchange limits are tight.

## P177 - Live latency accounting and precise-scan budget

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The current live loop can report a 10+ minute full-universe cycle while the expensive work is concentrated in active/radar precise scans. We need honest timing attribution before adding more wake-up logic, and a clean budget control that does not hide active symbols.

### Change

- Add stage timings to `live_cycle_summary`: ticker radar, batch selection, signal scan, open-signal handling, order reconcile, and cache flush seconds.
- Add optional `max_precise_scan_symbols_per_cycle`.
- Active symbols are never dropped by the cap. The cap only limits how many ticker-radar watch symbols are promoted into expensive precise scans in the current cycle; overflow remains in `ticker_radar_waiting_symbols`.
- Expose CLI `--max-precise-scan-symbols-per-cycle`.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
```

### Risk

Low for diagnostics, medium for cap usage: too low a cap can delay radar-watch symbols. This is explicit in artifacts via waiting counts/symbols, not hidden.

## P178 - Static high-cap live universe exclusion

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

PNO is looking for early runner potential. Large-cap majors rarely provide the 20%+ runner profile and still consume ticker/universe slots. We need a no-external-source way to remove obvious majors without pretending this is a market-cap oracle.

### Change

- Add a conservative static high-cap base list for live default universe: BTC, ETH, BNB, SOL, XRP, DOGE, ADA, TRX, LINK, AVAX, LTC, BCH, DOT.
- Apply it only to the default exchange USDT-swap universe.
- Do not filter explicit `--symbols`; manual symbol tests remain exact.
- Emit `live_symbol_universe_filter` with input/output counts and excluded symbols.
- Expose `--exclude-default-high-cap-symbols` to disable the filter.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
```

### Risk

Medium: this is a subjective static universe choice, not a data-derived market-cap filter. It improves focus and speed but creates selection bias; excluded symbols must stay visible in artifacts.

## P179 - Live cache flush and aggTrade tail cost reduction

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Timing run `20260513_164045` showed signal scan and cache flush as the dominant live bottlenecks. The expensive scan path repeatedly touches subminute aggTrade tails; cache flush can block a cycle for 10-15 seconds.

### Change

- Expand the static live high-cap exclusion list with additional obvious majors.
- Change live OHLCV cache write defaults to flush less often and with a larger buffer: 30 seconds and 50k rows.
- Add `live_ohlcv_cache_flush_max_symbol_timeframes` default 20 to bound non-forced flush work per cycle.
- Keep forced shutdown/error/max-cycle flush behavior as full flush.
- Split flush summary into `failed_symbol_timeframes` and `deferred_symbol_timeframes` so deferred work is not mislabeled as failed.
- Improve cycle-local aggTrade raw cache from full-range-only hits to partial interval coverage: only missing raw time intervals are fetched, then cached rows are deduped and sorted before aggregation.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\parser.py cli\commands.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: aggTrade missing-range subtraction and dedupe passed.
```

### Risk

Medium: fewer cache flushes means a hard process kill can lose more recently fetched cache rows, but trading diagnostics/events are still written immediately and the cache is forced on graceful shutdown. Partial aggTrade cache must be monitored with `aggtrade_cache_hits`, `aggtrade_network_calls`, and `live_ohlcv_cache_gap`.

## P180 - Human KeyboardInterrupt handling

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Ctrl+C during live or another CLI command should stop cleanly without a Python traceback.

### Change

- Catch `KeyboardInterrupt` in the common command wrapper and return code 130 with a short user-facing message.
- Catch `KeyboardInterrupt` at top-level `main()` as a final guard for interrupts outside command wrappers.

### Validation

```bash
.venv\Scripts\python.exe -m compileall main.py cli\commands.py research_tools\anomaly_micro_live.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
```

### Risk

Low. Existing live-loop cleanup still handles graceful live interruption first; this patch covers interrupts that escape that loop.

## P181 - First reactive seam: live ticker snapshot source

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Reactive live should be introduced through narrow data-source seams, not by rewriting anomaly decision logic. The first safe seam is ticker radar ingestion because it is scheduling-only and does not directly decide trades.

### Change

- Add `LiveTickerSnapshotSource` protocol.
- Add `RestLiveTickerSnapshotSource` implementation backed by current exchange `fetch_ticker_snapshots`.
- Inject the source into `AnomalyMicroLiveRunner`; default behavior remains REST-backed.
- Add ticker radar `source` to `ticker_radar_snapshot` and `ticker_radar_failed` events.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\commands.py cli\parser.py main.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: fake ticker source promoted a symbol and wrote source=fake_ticker_source in ticker_radar_snapshot.
```

### Risk

Low: no trade decision logic changes. Future WS ticker source can be plugged into the same protocol and compared in shadow before becoming primary.

## P182 - Second reactive seam: live scheduler batch selection

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Reactive ingestion should change how symbols become scan candidates without changing anomaly evaluation. The scheduler needs an explicit selection contract and scan-reason provenance before WS/event-driven sources are introduced.

### Change

- Add `LiveSymbolBatchSelection` data structure.
- Split current `_next_symbol_batch` into selection plus event emission.
- Preserve current REST/ticker-radar/round-robin behavior under `scheduler_source=rest_round_robin_scheduler`.
- Add `scan_reason_by_symbol` to `symbol_batch_selected` diagnostics.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\commands.py cli\parser.py main.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: scheduler selection returns the same inactive batch and writes scheduler_source/scan_reason_by_symbol.
```

### Risk

Low: this is a structural seam only. Future scheduler implementations must not silently drop symbols; waiting/dropped reasons must be explicit in `symbol_batch_selected`.

## P183 - WebSocket ticker radar source without REST fallback

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The next reactive step is to make all-symbol ticker wake-up event-driven. This must not silently fall back to REST, because hidden transport fallback would mask stream gaps and latency problems.

### Change

- Add `BinanceWsAllTickerSnapshotSource` backed by Binance USD-M futures `!ticker@arr`.
- Make WebSocket ticker source the live default via `live_ws_ticker_enabled=True`.
- Add `--live-ws-ticker-enabled`, `--live-ws-ticker-stale-ms`, and `--live-ws-ticker-startup-wait-seconds`.
- If WebSocket ticker is not ready, stale, malformed, or disconnected, ticker radar emits `ticker_radar_failed` with `source=binance_ws_all_ticker`; no REST fallback is used.
- Keep explicit REST ticker source available only when `--live-ws-ticker-enabled false`.
- Add `aiohttp` dependency for WebSocket transport.

### Validation

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_micro_live.py cli\commands.py cli\parser.py main.py
.venv\Scripts\python.exe main.py run-anomaly-live --help
Inline smoke: not-ready WS source raises explicit ws_ticker_not_ready.
Inline smoke: WS all-ticker payload normalizes to ExchangeTickerSnapshot with source=binance_ws_all_ticker.
```

### Risk

Medium/high: while WS ticker is unhealthy, radar promotions stop instead of falling back to REST. This is intentional honesty. Live diagnostics must monitor `ticker_radar_failed`, `source`, and stale/not-ready reasons before reducing REST cold audit further.

## P177 - Fix live OHLCV cache concat warning

Status: APPLIED locally / UNKNOWN commit
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Live cache fill could concatenate an empty cache placeholder with freshly fetched candles. Pandas 2.2 emits `FutureWarning` for this pattern, and future dtype inference could change.

### Change

- Add a typed empty OHLCV cache frame schema.
- Add `_concat_cached_ohlcv_frames` that prepares inputs and excludes empty cache placeholders before `pd.concat`.
- Use the helper for live cache memory merge, fetched candle merge, and buffered cache flush.
- Keep live signal, entry, stop and PnL logic unchanged.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low: cache-frame assembly only. Empty cache placeholders are skipped before concat, while non-empty candles still go through the same timestamp dedupe/sort preparation.


## P178 - Reduce live aggTrades duplicate fetches and expose honest cycle timing

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Live batch scans can spend most of the cycle in repeated Binance `aggTrades` REST calls for the same symbol/time window across S30/S15/S5 entry frames. Operator status also showed only the last batch duration, not the effective full universe rotation time, and did not expose local closed-trade PnL.

### Change

- Add a per-cycle raw aggTrades range cache. If a later S15/S5 request is covered by an earlier S30/S15 raw window for the same symbol, reuse the raw rows and aggregate locally to the requested timeframe.
- Keep closed-candle OHLCV output and signal logic unchanged: the same raw trades are aggregated through the existing `_aggregate_aggtrades_to_ohlcv_frame` path.
- Emit `live_cycle_summary` with batch seconds, full-symbol-cycle seconds, active symbol count, local closed PnL percent, and aggTrades request/cache-hit/network-call counts.
- Change operator status to `цикл batch/full · открыто N · активно N · закрыто N · PNL X%`.
- Track PnL only from locally finalized closed positions; no exchange balance/PnL fetch is used for the status line.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

### Risk

Low/medium: raw aggTrade rows are reused only within the same live cycle and only when the cached raw range fully covers the requested window. This should reduce duplicate REST calls without changing signal thresholds, but live smoke must confirm `aggtrade_network_calls < aggtrade_requests` and no increase in cache gaps/stale rejects.

## P179 - Defer inactive subminute aggTrades until ticker-radar or active state

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

Cold inactive symbols can dominate live cycle time by fetching S5/S15/S30 entry frames through Binance `aggTrades` before any cheap wake-up evidence exists. A fallback back to full inactive subminute scans would reintroduce the same latency and stale-entry problem, so the scheduler must make discovery explicit instead of hiding the old behavior behind a fallback.

### Change

- Inactive round-robin symbols defer subminute entry pairs (`S5`/`S15`/`S30`) and do not call `_fetch_chart_frame` / `fetch_binance_agg_trades` for those pairs.
- Precise subminute scans remain enabled for active symbols, opening/open positions through active scheduling, and ticker-radar watch symbols.
- No silent fallback to full inactive `aggTrades` scans exists.
- Startup validation refuses subminute live pairs when `ticker_radar_enabled=false` or `ticker_radar_watch_batch_size < 1`, because that would make inactive discovery blind after deferring subminute scans.
- Deferred inactive pairs are not marked as scanned; once ticker radar promotes the symbol or the symbol becomes active, the latest due precise entry candle is still evaluated.
- Add diagnostics: `inactive_subminute_scan_policy`, `scan_mode`, `subminute_entry_scan_allowed`, `inactive_visit_symbols`, `precise_scan_symbols`, `deferred_inactive_subminute_pairs`, and per-symbol `skipped_inactive_subminute_count`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: inactive S5/S30 defers without fetching, ticker-radar S5 fetches, ticker_radar_enabled=false with subminute pairs raises LiveStartupError.
```

### Risk

Medium and explicit: with subminute entry pairs, cold inactive discovery depends on ticker radar before precise `aggTrades` evaluation. This is intentional to protect live latency. If missed top-growth artifacts show radar is too strict, adjust ticker-radar thresholds/watch batch size; do not restore full inactive subminute `aggTrades` scans.

## P180 - Keep live cycle summary JSON finite before first closed trade

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

After P178/P179, the live status line writes local closed-trade PnL into `live_cycle_summary`. Before the first closed trade, closed notional is zero, and the old `_safe_divide` path produced `NaN`. `append_event(..., allow_nan=False)` correctly rejected that as a live-data integrity error.

### Change

- Make `_closed_pnl_pct_total` return `0.0` when there are no locally finalized closed trades yet.
- Keep integrity strict: non-finite local PnL counters still raise `LiveDataIntegrityError` instead of being written to artifacts.
- Do not fetch balance/PnL from the exchange and do not change trading logic.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: no closed trades -> closed_pnl_pct=0.0 and live_cycle_summary JSON is allow_nan=False compliant.
```

### Risk

Low: this only fixes local status/artifact accounting before the first closed trade. Real closed-trade PnL remains based on `_finalize_position` counters.

## P181 - Number live batches within full symbol cycles

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

After P178, the inline live status showed `batch_seconds/full_symbol_cycle_seconds`, but the word `цикл` still looked like the outer while-loop tick. Operators need to know both the current batch number inside the full round-robin pass and which full pass over the symbol universe is running.

### Change

- Track `batch_in_full_cycle` and `full_symbol_cycle` separately from the existing outer loop `cycle`.
- Reset batch numbering to 1 when a full inactive round-robin pass completes.
- Change the inline status to `цикл batch/full-cycle · batch_seconds/full_symbol_cycle_seconds · ...`.
- Add the same counters to `live_cycle_summary` and `symbol_batch_selected` artifacts.
- Do not change scheduling, signal selection, data fetching, orders, or PnL accounting.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: batch counter starts at 1, increments per selected batch, and resets after cursor completes a full symbol-universe pass.
```

### Risk

Low: operator/artifact numbering only. The counter is tied to the inactive round-robin cursor, so active/radar-only loops without inactive progress should be interpreted as scheduler ticks, not completed full-universe coverage.

## P184 - DANGER: WS aggTrade source for subminute precise scan

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The live loop already avoids full cold subminute scans, but precise active/radar scans still pay repeated REST `aggTrades` tail fetches. The next clean reactive step is a primary WS aggTrade buffer for watched symbols, with explicit gap diagnostics and no silent REST fallback.

### Change

- Add `BinanceWsAggTradeBuffer` using Binance USD-M combined streams and dynamic `@aggTrade` subscriptions for active/ticker-radar watch symbols.
- Default `run-anomaly-live` to `live_ws_aggtrade_enabled=true`.
- Subminute precise scan reads from WS rows first; uncovered ranges are explicitly REST-backfilled and logged as `ws_aggtrade_frame_read`, not hidden.
- Detect stream holes by aggregate trade id gaps and keep those time ranges missing until explicit backfill covers them.
- Add `ws_aggtrade_subscription_target`, `ws_aggtrade_subscription_seconds`, and live cache provenance for WS-backed explicit backfill.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py main.py
python main.py run-anomaly-live --help
# inline smoke: WS aggTrade id gap is detected, backfill coverage removes the gap.
# short live max-cycles=1: startup/artifacts ok; local DNS could not resolve fstream.binance.com, so no real WS tape validation occurred in this environment.
```

### Risk

Medium. WS coverage cannot prove a quiet symbol had zero trades without an exchange heartbeat, so empty/partial intervals remain explicit backfills. This is honest but means first-cycle radar symbols can still pay REST cost until the WS buffer warms. Real validation must inspect `ws_aggtrade_frame_read.status`, `missing_ranges`, `backfill_ranges`, and `aggtrade_network_calls`.

## P195 - DANGER: live WS aggTrade coverage and shutdown health

Status: PROPOSED
Date: 2026-05-14
Commit: UNKNOWN

### Reason

Live run `20260513_202255` showed connected ticker and aggTrade WebSockets, but aggTrade reads were almost always `partial`/`stale`, causing REST gap backfill on nearly every precise scan. The old coverage rule used first/last trade timestamps, so harmless no-trade edge intervals were treated as data holes. Ctrl+C could also be caught by the command wrapper before the runner's internal cleanup path.

### Change

- Track per-symbol WS coverage as subscription-active time, not only first/last trade timestamps.
- Advance coverage while the combined WS connection remains alive, so quiet subscribed intervals are not repeatedly REST-backfilled.
- Keep aggregate trade id gaps as explicit holes until REST backfill covers them.
- When explicit backfill covers a historical range, extend the coverage start/end accordingly, including empty backfills.
- Reduce the aggTrade WS receive timeout to make subscription changes sync faster.
- After setting target symbols, wait briefly for the WS thread to apply subscriptions to reduce `not_subscribed` race reads.
- Add command-level KeyboardInterrupt cleanup: if an interrupt escapes `runner.run()`, force live cache flush and close WS sources before re-raising to the common wrapper.

### Validation

```bash
python -m compileall research_tools/anomaly_micro_live.py cli/commands.py cli/parser.py main.py
# inline smoke: no-trade covered interval is covered; pre-subscription history needs backfill; empty backfill extends coverage; id-gap still creates a hole.
git diff --check
```

### Risk

Medium. The patch assumes that once Binance confirms/keeps a subscribed combined stream alive, absence of aggTrade messages for that symbol means no trades, not missing data. This is the intended WS contract; id gaps and pre-subscription history remain strict. Next live run must verify `covered` reads increase and REST backfills drop materially without new `signal_scan_empty_ohlcv` or cache gaps.

## P185 - Bound WS aggTrade backfill and fail fast on blind ticker radar

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The short WS live artifacts showed a dangerous blind mode: ticker radar could be unavailable while the loop still emitted cycles with no real discovery. Separately, WS aggTrade precise scans can still spend a full slow cycle in REST backfills when the WS buffer has not yet covered the requested subminute window. That makes a nominal WebSocket run behave like a delayed REST scan.

### Change

- Refuse live startup when subminute entry pairs require ticker radar but the configured ticker source is not ready/healthy.
- Add `live_ws_aggtrade_max_backfill_ms` / `--live-ws-aggtrade-max-backfill-ms` with default `0`.
- When a WS aggTrade precise scan has uncovered ranges larger than that explicit budget, do not REST-backfill; emit `ws_aggtrade_frame_read.status=coverage_pending`, emit `signal_entry_ws_aggtrade_pending`, and leave the signal unscanned for a later covered pass.
- Keep REST backfill possible only by explicitly raising the budget; it is visible through `missing_total_ms`, `backfill_max_ms`, `backfill_skipped`, and `backfill_ranges`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py launcher.py
python main.py run-anomaly-live --help
# live smoke: with WS connected, first promoted symbols should show coverage_pending until the buffer covers the requested interval; cycle time should not be dominated by REST aggTrade backfill.
```

### Risk

Medium and intentional: strict default WS coverage can skip fresh radar symbols until the WS buffer has enough history, especially for 5m/30s windows. If that misses too many opportunities, set a small explicit backfill budget instead of restoring unbounded REST backfill.

## P186 - Make WS scheduler heartbeat and diagnostics event-driven

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The current ZIP has P185 and partial scheduler knobs, but subminute WS-live still treats `symbol_batch_size` as implicit inactive market discovery. The operator status also prints ambiguous cycle/full-cycle timing, which keeps the old polling mental model alive and hides the actual WebSocket health signals.

### Change

- Default subminute+ticker-radar live to zero implicit inactive round-robin slots; inactive scans run only when `inactive_scan_slots_per_cycle` is explicitly configured or in legacy non-subminute mode.
- Keep `symbol_batch_size` as a legacy inactive scan cap outside the WS event-driven default; it is no longer presented as WebSocket market discovery.
- Add typed per-cycle ticker-radar and aggTrade subscription stats to `live_cycle_summary`.
- Replace the inline status from `цикл ...s/full...` with WebSocket-relevant heartbeat diagnostics: scheduler seconds, ticker radar status/coverage/promotions, aggTrade connection/target/subscribed counts, precise vs inactive scan counts, and legacy coverage only when relevant.
- No change to signal filters, entry guards, order/fill/stop logic, or PnL accounting.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout: `symbol_batch_selected.inactive_scan_slots_source=default_ws_event_driven_subminute`, `inactive_count=0` by default for subminute WS-live, `live_cycle_summary.scheduler_cycle_seconds` exists, and the console status reports ticker/aggTrade health instead of ambiguous cycle/full-cycle timing.

### Risk

Low/medium and intentional. Cold inactive scans stop pretending to be WebSocket discovery. If ticker radar misses movers, fix ticker WS health/thresholds or set an explicit diagnostic inactive budget; do not restore hidden polling discovery through `symbol_batch_size`.

## P187 - Restore compact human live heartbeat

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

P186 exposed useful WebSocket counters, but putting them directly into the inline operator heartbeat made the console noisy and hard to read. Routine console status should show only the live tempo and business-level state; detailed WS diagnostics remain in `live_cycle_summary`.

### Change

- Replace verbose inline `scheduler/ticker/aggTrade/scan/coverage` text with a compact line:

```text
5.0s · 99.4% · аномалии 104 · активно 2/6 · позиции 1/2 · PNL 4.60% · ticker ok · flow ok
```

- Track `live_events.csv` rows in `LiveArtifactWriter` and expose the count as `events_written` for the operator heartbeat.
- Append only short exception suffixes when something requires attention, e.g. `WS: ticker нет`, `WS: flow подключается`, `WS: flow подписка`, legacy `обход ~...s`, or cancelled orphan orders.
- Do not remove detailed per-cycle WebSocket diagnostics from `live_cycle_summary`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low. This is operator UI and event counting only. Trading logic, signal filters, order path, fill/stop handling, and PnL accounting are unchanged.

## P188 - Prevent hidden WS-live missed-entry paths

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

The uploaded ZIP can still miss actionable live entries without an obvious operator-level failure: freshly promoted ticker-radar symbols require subminute aggTrade history from the setup bucket, max-position rejects are not consumed but also are not re-scanned until the next candle, and position monitor threads classify every unexpected exception as a temporary handling error.

### Change

- Default `live_ws_aggtrade_max_backfill_ms` / `--live-ws-aggtrade-max-backfill-ms` to `300000` so radar-promoted symbols can repair the initial bounded WS coverage gap instead of always waiting for the buffer to accumulate history.
- Keep the backfill explicit in existing artifacts: `ws_aggtrade_frame_read` still records missing ranges, `backfill_max_ms`, `backfill_skipped`, `backfill_ranges`, and row counts.
- Runtime ticker radar now emits `ticker_radar_snapshot.status` and raises `ExchangeConnectivityError` if all snapshots are unusable while subminute inactive discovery depends on ticker radar.
- `reject_max_positions` now re-enables scanning of the same unconsumed decision candle until it is stale/free-slot executable, and writes `signal_scan_retry_enabled`.
- Position monitor threads now treat only `ExchangeConnectivityError` as temporary network degradation; unexpected exceptions become `position_monitor_internal_error` + `position_integrity_error` instead of being hidden as temporary noise.

### Validation

```bash
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
# smoke: radar-promoted fresh symbol should show bounded backfill instead of persistent coverage_pending when missing_total_ms <= 300000
# smoke: max_open_positions reject should emit signal_scan_retry_enabled and retry before signal stales
# smoke: injected monitor ValueError should emit position_monitor_internal_error and position_integrity_error, not position_monitor_error
```

### Risk

Medium. Default live may use bounded REST aggTrade repair for hot radar symbols, increasing API cost. This is intentional and visible; set `--live-ws-aggtrade-max-backfill-ms 0` for strict WS-only diagnostics.

## P189 - Add explicit REST ticker-radar degraded source for WS DNS failures

Status: PROPOSED
Date: 2026-05-13
Commit: UNKNOWN

### Reason

A real Windows live start stopped before the first cycle because the Binance futures WebSocket host could not be resolved:
`ClientConnectorDNSError: Cannot connect to host fstream.binance.com`. Strict startup refusal was honest, but operationally too brittle because ticker-radar discovery already has a normalized REST ticker snapshot source that can be used without restoring hidden OHLCV/aggTrade full-universe scans.

### Change

- Keep WS `!ticker@arr` as primary ticker-radar source when `live_ws_ticker_enabled=true`.
- If the primary WS ticker source fails at startup or during a ticker-radar cycle, try `RestLiveTickerSnapshotSource` as an explicit degraded ticker-radar source.
- Emit `ticker_radar_primary_source_failed`, `ticker_radar_source_degraded`, and, if needed, `ticker_radar_fallback_source_failed` events with source ids, exception details, and snapshot ok/missing counts.
- Refuse startup / pause the live loop if both WS and REST ticker-radar sources fail, or if the chosen source returns all snapshots missing.
- Do not restore hidden OHLCV/aggTrade full-universe scans; fallback is ticker snapshots only and remains visible in `ticker_radar_snapshot.source_status=degraded_rest_fallback`.

### Validation

```bash
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
```

Expected live smoke with broken WS DNS but working REST API: startup continues, `ticker_radar_source_degraded` is written, `ticker_radar_startup_ready.source=rest_fetch_tickers`, and `live_cycle_summary.ticker_radar_status=degraded_rest_fallback`. If REST/DNS is also broken, startup still fails instead of scanning with fake data.

### Risk

Medium. REST ticker polling is slower and less reactive than WS ticker pushes, so discovery latency can increase during degraded mode. This is preferable to pretending WS is healthy or silently restoring full inactive subminute scans.

## P190 - proposed - widen default WS aggTrade backfill budget for 5m/30s live

Status: PROPOSED. Commit: UNKNOWN.

Context:
- After P189, a broken Binance WS ticker can be replaced by explicit degraded REST ticker radar, but the default WS aggTrade backfill budget stayed at 300000 ms.
- For the default 5m/30s pair, the last valid scan of the previous 5m forming setup can request slightly more than 300000 ms of aggTrade history while the signal is still fresh.
- With WS aggTrade unavailable or cold, that path can emit `coverage_pending` and miss the executable window even though REST aggTrade could have repaired a bounded gap.

Changes:
- Raise the default `live_ws_aggtrade_max_backfill_ms` / `--live-ws-aggtrade-max-backfill-ms` to 360000.
- Keep the behavior explicit through `ws_aggtrade_frame_read.backfill_max_ms`, `backfill_ranges`, `backfill_skipped`, and `signal_entry_ws_aggtrade_pending`.
- Show degraded ticker discovery in the operator status line as `WS: ticker REST` instead of only relying on CSV artifacts.

Risk:
Medium: degraded WS runs may perform more REST aggTrade work on radar-promoted symbols. This is bounded and visible, but cycle latency must be watched through `signal_scan_seconds` and `aggtrade_network_calls`.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Live smoke expectation: with WS ticker DNS broken but REST available, startup continues in degraded REST mode; fresh 5m/30s radar-promoted scans should backfill bounded gaps up to 360000 ms instead of `coverage_pending` at the end of the setup window.


## P191 - proposed - make aggTrade WS degraded execution visible in live status

Status: PROPOSED. Commit: UNKNOWN.

Context:
- A live run stayed for several minutes on the operator line `WS: flow подключается` while ticker discovery had already been moved to explicit degraded REST mode.
- That string conflates three materially different states: WS is still warming up, WS is unavailable but bounded REST aggTrade backfill is covering requested flow windows, or WS/REST coverage is pending and entries can be missed.

Changes:
- Add per-cycle counters for WS aggTrade REST backfill reads, backfilled rows, not-connected backfill reads, and coverage-pending reads.
- Add `live_cycle_summary.ws_aggtrade_effective_source` with explicit values such as `rest_backfill_degraded`, `uncovered_ws_pending`, `ws_with_rest_gap_backfill`, or `ws_*_no_entry_read`.
- Replace the long-running generic status suffix `WS: flow подключается` with more diagnostic operator states: `WS: flow REST`, `WS: flow pending`, `WS: flow нет`, or `WS: flow gap REST`.

Risk:
Low. Trading logic and order execution are unchanged. The patch only makes the existing degraded aggTrade path and coverage holes visible at cycle/operator level.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Live smoke expectation: if aggTrade WS cannot connect but bounded REST aggTrade reads are covering entry windows, the console should show `WS: flow REST` and `live_cycle_summary.ws_aggtrade_effective_source=rest_backfill_degraded`; if coverage still exceeds the backfill budget, it should show `WS: flow pending` and emit `signal_entry_ws_aggtrade_pending`.


## P192 - proposed - force aiohttp WS to use OS DNS resolver and show WS DNS label

Status: PROPOSED. Commit: UNKNOWN.

Context:
- A live run showed both Binance ticker and aggTrade WebSockets failing with `ClientConnectorDNSError: Could not contact DNS servers` while REST ticker fetches continued to work.
- That points to the aiohttp WebSocket DNS resolver path rather than a generic exchange/data outage.
- The operator line only showed `WS: ticker REST` / `WS: flow REST`, so the root transport cause was buried in `live_events.csv`.

Changes:
- Force aiohttp WebSocket sessions to use `aiohttp.ThreadedResolver()` via an explicit `TCPConnector`, matching the OS `getaddrinfo` path used by normal REST clients instead of any c-ares/async resolver path that can fail independently.
- Add short WS error labels (`dns`, `timeout`, `ssl`, `proxy`, `connect`) to `live_cycle_summary`.
- Append those labels to the inline operator status, for example `WS: ticker REST/dns` or `WS: flow REST/dns`.

Risk:
Low/medium. Trading logic is unchanged. The patch changes WebSocket transport resolver selection and diagnostics only. If the OS itself cannot resolve `fstream.binance.com`, the status will still show `/dns` and the environment must be fixed; no silent success is introduced.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Live smoke expectation: if the previous failure came from aiohttp's async DNS resolver, WS ticker/flow should connect and the `/dns` degraded suffix should disappear. If local DNS/network still cannot resolve `fstream.binance.com`, the console should explicitly show `WS: ticker REST/dns` or `WS: flow REST/dns` and artifacts should keep the full exception.


## P193 — route Binance futures WS market streams to /market (PROPOSED)

Status: PROPOSED. Commit: UNKNOWN.

Reason: after P192, live artifacts show DNS is no longer the blocker: WS transport reaches `connected`, but all-ticker stays `ws_ticker_not_ready;status=connected;last_message_at_ms=` and ticker discovery degrades to REST. Binance USD-M futures docs moved regular market streams such as `!ticker@arr` and `<symbol>@aggTrade` to the routed `/market` WebSocket endpoint; legacy unrouted `/ws` and `/stream` paths can connect but stop pushing market streams after the migration deadline.

Changes:
- Change all-market ticker URL from `wss://fstream.binance.com/ws/!ticker@arr` to `wss://fstream.binance.com/market/ws/!ticker@arr`.
- Change dynamic aggTrade combined connection from `wss://fstream.binance.com/stream` to `wss://fstream.binance.com/market/stream`.
- Keep ThreadedResolver and explicit REST degraded fallback; no silent fallback is added.

Validation to run after apply:
- `python -m compileall data/exchanges research_tools cli constants.py main.py`
- Small live smoke: expect `ticker_radar_startup_ready.source=binance_ws_all_ticker`, no repeated `ticker_radar_source_degraded`, and `ws_aggtrade_effective_source=ws` after subscription warm-up.


## P194 — seed WS ticker startup cache and show anomaly count in operator line (PROPOSED)

Status: PROPOSED. Commit: UNKNOWN.

Reason: after P193, ticker WS is healthy but the all-ticker stream warms gradually, leaving the first live seconds with partial universe coverage. The console also showed `события`, which was only the audit-row count and could be mistaken for detected anomalies.

Changes:
- Add a one-shot REST ticker startup seed for the Binance WS ticker cache when subminute ticker-radar discovery is required.
- Mark seeded snapshots with `rest_startup_seed.*` source labels and `source_status=primary_seeded_rest` / `WS: ticker seed` until real WS updates replace them.
- Emit `ticker_radar_startup_seeded` or `ticker_radar_startup_seed_failed` in `live_events.csv`; failed seed does not fake success and live still relies on primary WS or explicit degraded fallback.
- Replace inline `события <audit rows>` with `аномалии <detected ticker-radar promotions>` and store `detected_anomalies_total` in `live_cycle_summary`.

Validation to run after apply:
- `python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py`
- `python -m compileall data/exchanges research_tools cli constants.py main.py`
- Small live smoke: expect `ticker_radar_startup_seeded.seeded_count` close to universe size, no startup burst of `ticker_radar_missing_fields`, and console heartbeat like `... · аномалии 0 · ...`; during the first seed-backed cycle `ticker_radar_snapshot.source_status=primary_seeded_rest`, then normal WS cycles should return to `primary` once ticker messages arrive.


## P196 - proposed - strict backtest/live parity for forming setup decisions

Status: PROPOSED. Commit: UNKNOWN.

Context:
- Uploaded ZIP review found that backtest and live use duplicated forming-setup paths.
- The highest-confidence mismatch was backtest-side: the first raw candidate inside an HTF setup consumed that setup/cooldown before later OI/mark/category/execution filters ran.
- Live can keep evaluating later closed LTF decision candles in the same HTF setup, so the backtest could miss valid later runner_oi_confirmed entries.

Changes:
- `research_tools/anomaly_strategy_backtest.py` now collects every valid LTF decision candidate inside a forming HTF setup instead of stopping after the first raw pump-flow candidate.
- `research_tools/anomaly_micro_live.py` now writes selected category OI-change and mark-basis status/value/age into `category_selected`, not only into reject rows.
- Candidate rows now include `setup_decision_index` and `candidate_collection_policy=all_ltf_decisions_per_setup` for auditability.
- `runner_balanced` now rejects stale derivatives context when it requires mark basis, matching live's fresh mark-basis fetch.
- `runner_oi_confirmed` now forces `require_oi_status_ok=True` in addition to OI-change and mark-basis thresholds.

Risk:
Medium. Candidate counts can rise materially, especially on 1m/5s. This is expected and more honest; execution overlap is still resolved in trade simulation. The next run must inspect candidate/signal/trade counts and runtime.

Validation:
```
python -m py_compile research_tools/anomaly_strategy_backtest.py research_tools/anomaly_micro_live.py
python -m compileall research_tools cli constants.py main.py
```


## P197 - proposed - broad backtest category overlay

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The only currently live-candidate category is `runner_oi_confirmed`.
- Backtest should still run broad discovery signals so potentially profitable categories are not hidden by a single strict production profile.
- Category statistics need to be separated by bucket instead of inferred from separate narrowed runs.

Changes:
- Add `pump_category_id`, `pump_category_rank`, `pump_category_matches`, and `pump_category_source` to signal/trade context columns.
- Add a backtest overlay classifier that tags broad signals with the strongest matching profile in this order: `runner_oi_confirmed`, `runner_flow`, `runner_reclaim`, `runner_balanced`, then `discovery`.
- Add `anomaly_profitability_by_category.csv` with closed-trade stats per category.

Risk:
Medium. Category overlay runs several profile filters over the same candidate table, so signal-filter time increases. This is deliberate and keeps the primary trade stream broad.

Validation:
```
python -m py_compile research_tools/anomaly_strategy_backtest.py
python -m compileall research_tools cli constants.py main.py
```


## P198 - proposed - cheap flow prescreen for broad forming-setup backtest

Status: PROPOSED. Commit: UNKNOWN.

Context:
- After P196, 30d all-symbol/all-TF backtest became very slow because forming HTF from LTF collection evaluates every closed LTF decision candle inside every setup.
- The expensive row builder was being called even for decision candles that would immediately fail the same raw/paced quote/trade flow thresholds.

Changes:
- Add rolling setup baseline medians and an in-loop cumulative LTF quote/trade prescreen in `_collect_symbol_pair_rows`.
- The prescreen uses the same baseline medians, elapsed fraction, raw ratio thresholds, and pace thresholds as `_build_pair_candidate_row`.
- Only decision candles that can pass the existing flow gate proceed to baseline slicing, aggregation, feature construction, future labels, and category overlay.

Risk:
Low/medium. The prescreen must remain mathematically identical to the row-builder flow gate. It should reduce work without changing accepted candidates.

Validation:
```
python -m py_compile research_tools/anomaly_strategy_backtest.py
python -m compileall research_tools cli constants.py main.py
```


## P199 - proposed - enable profitable live categories with TF priorities

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The 30d broad run showed profitable buckets beyond `runner_oi_confirmed`, especially `runner_flow`.
- Live should test these categories explicitly while preserving category attribution in Telegram and artifacts.

Changes:
- Add live-supported `runner_flow`, `runner_reclaim`, and `runner_balanced` categories alongside `runner_oi_confirmed`.
- Default live `--pump-categories` is now `runner_oi_confirmed,runner_flow,runner_reclaim,runner_balanced`.
- Add TF-specific category priority:
  - `5m/30s`: `runner_flow`, `runner_oi_confirmed`, `runner_reclaim`, `runner_balanced`
  - `1m/15s`: `runner_oi_confirmed`, `runner_flow`, `runner_reclaim`, `runner_balanced`
  - `1m/5s`: `runner_oi_confirmed`, `runner_flow`, `runner_balanced`, `runner_reclaim`
- Add flow-hold and reclaim wick checks to live category filtering.
- Emit `category_contract=live_category_overlay_v1_no_prior_fast_fade`, category id/label, OI/mark values, flow hold, and wick metrics in selected-category artifacts.

Risk:
Medium. Live category contract is explicit because live does not yet enforce the backtest 72h prior_fast_fade exclusion. This is not a fallback, but a known contract gap that must be measured in shadow/live parity.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

## P200 - proposed - enforce live prior_fast_fade with trailing cache lag ignored

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The expanded live categories inherit the backtest `max_prior_fast_fade_count_72h=0` exclusion.
- The first live implementation only added a cache-based calculator and still used a strict window ending at the live decision timestamp, which would reject most live candidates when the OHLCV cache lags by minutes or hours.
- Ignoring the whole filter would admit serial fast-fade symbols; requiring full tail coverage would make the filter practically unusable in live.

Changes:
- Replace strict cache-window loading for this filter with contiguous-from-start loading that allows only a trailing cache lag.
- Keep start-of-window and internal cache gaps blocking; these produce `reject_prior_fast_fade_filter_unavailable`.
- Actually apply `max_prior_fast_fade_count_72h` inside live category selection before mark/OI work.
- Emit `reject_prior_fast_fade_72h` when cached mature prior candidates contain too many fast fades.
- Add prior-fast-fade status/count/coverage/tail metadata to `category_selected` and category rejection payloads.
- Bump `category_contract` to `live_category_overlay_v3_prior_fast_fade_cache_tail_ignored`.

Risk:
Medium. The live filter can be slightly less strict than a perfectly up-to-date backtest during cache lag because the trailing unavailable interval is ignored. This is intentional and visible; it is not a silent fallback. Start coverage and internal gaps remain hard rejects.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py launcher.py
python main.py run-anomaly-live --help
```

## P201 - proposed - live WS healthy time percentage

Status: PROPOSED. Commit: UNKNOWN.

Context:
- The inline live heartbeat showed the current WS issue label, but not how much wall-clock time the session was actually WS-healthy versus degraded by REST seed/fallback, flow backfill, pending coverage, or network waits.
- Operator needs a compact health percentage in the heartbeat line.

Changes:
- Track cumulative wall-clock seconds between health samples, split into WS-healthy and observed seconds.
- A cycle is counted healthy only when ticker radar is `primary` WS and, when flow WS has targets, aggTrade WS is connected, fully subscribed, has no coverage pending, and did not use REST backfill.
- Network-connectivity waits are sampled as unhealthy time.
- Add `соединение N%` to the live heartbeat.
- Add `ws_healthy`, `ws_health_reason`, `ws_health_pct`, `ws_health_observed_seconds`, and `ws_health_healthy_seconds` to `live_cycle_summary`.
- Preserve the last attempted ticker-radar health state across `not_due` cycles so the percentage does not read as 0% between normal ticker-radar intervals.
- Treat `ticker_radar_status=ok` from `binance_ws_all_ticker` as healthy primary WS, while REST `ok` remains non-WS health.
- Count expected startup bootstrap as healthy for the first 180 seconds: `ticker seed` and bounded startup `flow gap REST` do not force the health percentage to begin at 0%.
- Keep non-bootstrap REST fallback, flow pending, subscription mismatch, and network errors as unhealthy connection time.
- Count aggTrade subscription mismatch as unhealthy only when subscribed targets are fewer than requested targets; extra still-active old subscriptions are overhead, not a lost connection.
- Render heartbeat as `{seconds}s · {connection_pct}% · аномалии N · активно current/seen · позиции open/closed · PNL X% · {connection_status}` without the `live` prefix.
- Align heartbeat metric columns to compact width 9 with right-aligned values; connection status is a non-empty left-flowing suffix without fixed padding.
- Render healthy connection status as `ticker ok` / `ticker ok · flow ok`, and degraded status as `ticker seed`, `ticker REST`, `ticker нет`, `flow pending`, `flow REST`, `flow подписка`, or `flow gap REST`.
- Restore single-line heartbeat rendering with ANSI clear-line instead of padding-only carriage return, so PowerShell does not keep duplicate heartbeat rows.
- Highlight the heartbeat line when at least one position is open.
- Remove the `live:` prefix from console live-run messages.

Risk:
Low. Diagnostics/operator UX only; no signal, entry, stop, exit, or sizing logic changes.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

## P202 - proposed - reduce live cache flush hot-loop stalls

Status: PROPOSED. Commit: UNKNOWN.

Context:
- Longest recent live run `.output/results/live_anomaly_runs/20260514_104416` had 500 cycles and 0 positions.
- Cycle time was dominated by `signal_scan_seconds` (~1054s total) and `cache_flush_seconds` (~904s total).
- Non-forced cache flush was writing up to 20 symbol/timeframe parquet shards synchronously in the decision loop; p95 flush was ~13.2s and max was ~31.9s.

Changes:
- Lower default `live_ohlcv_cache_flush_max_symbol_timeframes` from 20 to 4 in config, CLI parser, and command fallback.
- Forced shutdown/error/max-cycle flush remains unlimited and persists all pending shards.

Risk:
Low for trading logic. Signal selection, categories, entry/exit, stops and cache correctness are unchanged. Runtime can carry a larger in-memory pending write queue if fetched rows arrive faster than the smaller flush budget can persist them.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py cli/parser.py cli/commands.py
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

## P203 - proposed - harden live WS subscription ACK and position amount parsing

Status: PROPOSED. Commit: UNKNOWN.

Context:
- Live review found that Binance aggTrade subscriptions were treated as active immediately after sending `SUBSCRIBE`, before the exchange ACK arrived.
- That can make `wait_for_targets` and `read_rows` report subscribed/covered too early, especially during a subscription error or a just-promoted ticker-radar symbol.
- Position amount parsing also preferred CCXT `contracts`/`side` before Binance `info.positionAmt`; if `contracts` is missing/zero while `positionAmt` is present, live can misread an open position as flat.

Changes:
- Track pending aggTrade subscribe/unsubscribe request ids and move symbols into the subscribed coverage set only after the WebSocket ACK payload is received.
- Clear pending subscription state on reconnect and on subscription errors.
- Parse `info.positionAmt` before side-based return when normalized contracts are zero.

Risk:
Low/medium. Trading thresholds are unchanged. Freshly promoted symbols may use explicit REST backfill for a little longer until WS ACK arrives, but this is more honest than claiming unacknowledged WS coverage.

Validation:
```
python -m py_compile research_tools/anomaly_micro_live.py data/exchanges/ccxt_futures_client.py
python -m compileall data/exchanges research_tools cli constants.py main.py
```

## P204 - proposed - coalesce and reuse live aggTrade REST gap backfills

Status: PROPOSED. Commit: UNKNOWN.

Context:
- WS aggTrade coverage is now mostly healthy, but every explicit gap/backfill can still become a separate REST `aggTrades` request.
- Cycle-local raw cache avoids only repeated reads inside one cycle; adjacent windows across later cycles or multiple small WS holes can still hit REST repeatedly.
- The goal is to reduce REST call count and latency without treating missing data as a zero-signal condition.

Changes:
- Add process-memory REST aggTrade raw range cache with 20 minute default TTL.
- Coalesce uncovered aggTrade ranges and apply 60s default padding within the requested scan window, so one REST fetch can satisfy nearby 5s/15s/30s consumers and later cycles.
- Route both WS missing-range backfill and non-WS subminute raw fetches through the same cache/planner.
- Add CLI knobs: `--live-aggtrade-rest-cache-ttl-ms` and `--live-aggtrade-rest-cache-padding-ms`.
- Add live_cycle_summary counters: `aggtrade_process_cache_hits`, `aggtrade_coalesced_missing_ranges`, and `aggtrade_rest_fetched_ms`.
- Add per-read diagnostics: cache missing range count and actual network backfill range count.

Risk:
Medium operationally. The patch should reduce repeated REST work, but padding can fetch extra rows for hot symbols. It does not relax WS coverage rules, signal filters, entry guards, stops, exits, or PnL accounting.

Validation:
```
.venv/Scripts/python.exe -m compileall data/exchanges research_tools cli constants.py main.py
.venv/Scripts/python.exe main.py run-anomaly-live --help
# smokes: process cache avoids second overlapping REST fetch; adjacent gaps coalesce into one padded request.
```

---

## P189 — Live aggTrade REST gap prefetch planner

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Reduce REST churn and improve live stability by coalescing due subminute S30/S15/S5 WS aggTrade gaps per precise symbol before signal evaluation.
```

Changes:

```text
- Adds a mandatory per-symbol subminute entry gap prefetch step for precise active/radar scans.
- Reads WS coverage for all due subminute entry ranges, merges missing ranges, and performs one bounded cached REST backfill pass before timeframe evaluation.
- Populates the WS aggTrade buffer from the coalesced backfill so later S30/S15/S5 frame reads do not re-open the same REST debt.
- Adds `aggtrade_rest_gap_prefetch` events and cycle summary counters for requested ranges, missing ranges, backfill ranges, fetched rows, and pending oversized gaps.
- Keeps oversized gaps as explicit coverage-pending; no stale signal fallback and no feature flag.
```

Verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout:

```text
live_events.csv shows aggtrade_rest_gap_prefetch for active/radar symbols with WS gaps; subsequent ws_aggtrade_frame_read events for the same symbol/time window should have fewer network backfill ranges. live_cycle_summary exposes aggtrade_gap_prefetch_* counters. Oversized missing ranges remain coverage_pending.
```

## 2026-05-14 - P190 proposed: idempotent live order placement and pre-stop exposure cleanup

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
data/exchanges/ccxt_futures_client.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Remove ambiguous state-changing REST retries from live order placement and close any verified post-entry exposure before raising pre-stop integrity errors.
```

Changes:

```text
- Live market and STOP_MARKET order placement now requires a deterministic client order id.
- create_order is sent once; only ambiguous transport/order-mutation failures are reconciled by client order id.
- Non-ambiguous exchange validation errors are not retried and are not relabeled as network errors.
- Entry, TP1, stop, and protective reduce-only exits all use explicit client order ids in live artifacts.
- Pre-stop entry integrity failures read the actual exchange position and either record no exposure or send a verified reduce-only close before raising.
- No feature flags, no silent fallback, no candle-price fill substitution, and no trading logic threshold changes.
```

Verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout:

```text
position_opened, position_stop_order_verified, tp1_partial_exit_filled, and unprotected_entry_reduce_only_exit_filled events include client_order_id. A synthetic ambiguous create_order transport failure should reconcile by client order id instead of submitting a duplicate order. A synthetic fill/position mismatch before initial stop should emit an unprotected_entry_* event before the live runner stops with LiveDataIntegrityError.
```

## 2026-05-14 - P192 proposed: live account preflight and close-only startup position cleanup

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
data/exchanges/ccxt_futures_client.py
data/exchanges/ccxt_types.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Refuse unsupported live account mode and start real-order live from a clean exchange-position state by closing any pre-existing position explicitly before the live loop.
```

Changes:

```text
- Adds Binance USD-M account-mode preflight before real-order live startup.
- Hedge mode is a hard startup error; the live runner requires one-way position mode.
- Reads exchange positions for the live universe before ticker startup and signal scanning.
- Any non-flat startup position is closed with a reduce-only market order using an explicit client order id.
- The close is verified by reading the post-close exchange position amount.
- After a verified startup close, orphan open orders for the symbol are cancelled.
- Startup blocks if a pre-existing position cannot be closed and verified.
- No restore path, no ledger-based adoption, no feature flag, no silent fallback.
```

Verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Expected smoke readout:

```text
live_events.csv contains live_account_preflight_ok before ticker startup. If the account is in hedge mode, startup stops with live_account_preflight_failed. If a live-universe symbol has a pre-existing exchange position, startup emits startup_position_cleanup_started, startup_position_closed, startup_position_cleanup_finished, then starts only after the position is verified flat and orphan orders are cancelled.
```


## 2026-05-14 - P205 proposed: live/backtest category parity and 72h prior-fast-fade context

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make backtest category attribution match live TF priority while keeping discovery as backtest-only fallback, and stop live runner categories from being rejected just because 72h subminute entry cache is unavailable.
```

Changes:

```text
- Backtest category annotation now uses the same TF priority order as live: 5m/30s starts with runner_flow, 1m/15s starts with runner_oi_confirmed, and 1m/5s tries runner_balanced before runner_reclaim.
- Backtest still falls back to discovery when no live category profile matches, so artifacts show whether a trade came from a confirmed live category or backtest-only discovery.
- Live tradable categories remain unchanged; discovery is not added to live.
- Live prior-fast-fade 72h filter now computes historical context from the levels timeframe and can fetch/fill that bounded OHLCV window, instead of requiring 72h of subminute entry timeframe aggTrade cache.
- Category reject artifacts preserve the real reject reason in reason/category_reject_reason and move nested detail reason into coverage_reason/detail_reason, so prior-fast-fade coverage failures are no longer mislabeled.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
Backtest anomaly_trades.csv / anomaly_signals.csv should contain pump_category_source=backtest_live_priority_overlay_v1_discovery_fallback and pump_category_id showing runner_* or discovery. Live category_rejected rows should keep reason=reject_prior_fast_fade_filter_unavailable when coverage is unavailable and include coverage_reason for the underlying context issue. With normal 1m/5m cache coverage, prior_fast_fade_filter_status should be ok instead of failing because 5s/15s/30s cache does not span 72h.
```

Risk:

```text
Low/medium. Live category eligibility can increase where the only previous blocker was missing subminute 72h history. This is intentional because prior-fast-fade is historical symbol context, not a requirement for 72h of executable subminute entry cache. It does not make discovery tradable in live and does not change order/fill/stop logic.
```

## 2026-05-14 - P206 proposed: bounded shutdown reconcile and visible graceful stop

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make Ctrl+C shutdown observable immediately and avoid a forced full-universe open-order reconcile on exit.
```

Changes:

```text
- Ctrl+C now writes live_shutdown_started and logs the beginning of graceful shutdown before reconcile/flush/close work starts.
- Forced orphan-order reconciliation on Ctrl+C/max-cycles now checks only symbols that had exchange order activity in the current run, plus currently open/opening active symbols.
- Symbols are tracked after startup close orders, live entry fills, and emergency unprotected-entry reduce-only exits.
- Forced reconcile writes orphan_order_reconcile_started with the bounded scope size.
- A second Ctrl+C during cleanup writes live_shutdown_forced and exits with code 130 after closing live sources.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
On the first Ctrl+C, console immediately prints graceful shutdown with reconcile_symbols=N and live_events.csv contains live_shutdown_started. If no orders were placed in this run, forced orphan reconcile checks zero symbols instead of the whole universe. If one symbol had a live entry, forced reconcile checks that symbol only.
```

## 2026-05-14 - P207 proposed DANGER: live retryable dependencies and cold coverage

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Keep live category rejects honest while reducing false missed entries from temporary data dependencies and making limited cold coverage explicit.
```

Changes:

```text
- Live taker-buy share now matches the backtest contract: mean taker_buy_quote_volume / quote_volume is computed only over rows with finite taker value and quote_volume > 0. If no valid rows exist, the reject remains explicit and artifacts include valid/total row counts.
- Category dependency rejects for prior-fast-fade coverage, mark context, and OI context are treated as retryable when the blocking reason is data availability/staleness, not a real below-threshold value.
- Retryable dependency rejects no longer consume the decision candle or mark the timeframe as scanned; live will retry until the signal goes stale or reaches a final non-retryable reject/selection.
- Added signal_scan_retryable_dependency_blocked artifacts with retryable reasons and contract id.
- DANGER: subminute ticker-radar live now has a small default precise cold-coverage budget of 5 inactive symbols per cycle. This is deliberately labeled DANGER in constants, scheduler source, scan mode, live_cache_config, symbol_batch_selected, and live_cycle_summary.
- Setting inactive_scan_slots_per_cycle=0 explicitly disables cold coverage and returns to active/radar-only scanning.
- If max_precise_scan_symbols_per_cycle is configured, DANGER cold coverage is capped by remaining precise budget after active and ticker-radar symbols.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
live_events.csv should show category_contract=live_category_overlay_v5_retryable_dependencies_cold_coverage. Sparse entry segments with zero-volume synthetic buckets should no longer produce reject_invalid_taker_buy_share if at least one valid quote-volume row exists. Temporary mark/OI/prior-fast-fade unavailable rejects should emit signal_scan_retryable_dependency_blocked and the same decision timestamp can be retried until stale. symbol_batch_selected should show scheduler_source=DANGER_ws_event_driven_plus_precise_cold_coverage by default, inactive_scan_slots_source=DANGER_default_precise_cold_coverage_subminute, and cold symbols should have scan mode precise_DANGER_cold_coverage. Set inactive_scan_slots_per_cycle=0 to disable this DANGER mode.
```

Risk:

```text
High / DANGER. Cold coverage intentionally increases live workload and REST/WS aggTrade pressure, and can change which symbols reach precise subminute evaluation. This is for parity/audit discovery coverage, not a proven production improvement. Monitor signal_scan_seconds, aggtrade_network_calls, ws_aggtrade_coverage_pending_count, rate-limit/API errors, and scheduler health before using real orders for long runs.
```


## 2026-05-14 - P208 proposed DANGER: health-gated idle cold coverage

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Keep P207 cold coverage explicit, but prevent it from competing with active trading work or running during degraded WS health.
```

Changes:

```text
- DANGER cold coverage now requires cumulative WS health strictly above 95%.
- DANGER/explicit precise cold coverage is gated off when any active symbol, opening position, or open position exists.
- symbol_batch_selected and live_cycle_summary now record inactive_cold_coverage_gate_reason, health percentage at selection, threshold, and active/position blockers.
- The heartbeat no longer prints the ambiguous Russian "DANGER обход ~Ns" when cold coverage is gated off; it prints cold off <reason>, and only prints a cold full-cycle estimate when cold coverage actually selected inactive symbols.
- Setting inactive_scan_slots_per_cycle=0 still disables cold coverage completely.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
With WS health <=95% or active/open/opening positions present, symbol_batch_selected should show scheduler_source=ws_event_driven_scheduler_cold_coverage_gated, inactive_count=0, inactive_cold_coverage_gate_reason populated, and no precise_DANGER_cold_coverage symbols. Only after health >95% and no active/open/opening positions should scheduler_source become DANGER_ws_event_driven_plus_precise_cold_coverage with inactive cold symbols.
```

Risk:

```text
Medium / DANGER-limited. Cold coverage becomes less aggressive and may miss cold discoveries during early startup, degraded WS periods, or while a setup/position is active. This is intentional: cold coverage is for idle parity/audit discovery, not for competing with live trade management.
```


## 2026-05-14 - P209 proposed DANGER: adaptive cold coverage controller

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Keep cold coverage useful as an idle parity/audit scanner, but make it self-throttle before it steals latency or REST/cache budget from live/radar/active work.
```

Changes:

```text
- DANGER cold coverage is now adaptive, not a fixed 5-slot gate.
- Open/opening positions hard-disable cold coverage.
- Active symbols due for scan hard-disable cold coverage; active waiting symbols reduce the adaptive score instead of forcing an all-or-nothing gate.
- Adaptive score = WS-health factor * scheduler-heartbeat speed factor * active-waiting factor * REST/cache load factor.
- WS health ramps from 95.0% to 99.5%; heartbeat EWMA ramps from 8s slow to 2s fast; REST/cache pressure EWMA is driven by aggTrade network calls, REST fetched time span, and pending WS/cache gaps.
- Cold coverage runs only when score >= 0.30, and selected slots scale from 1 up to the configured/default hard cap.
- Artifacts now expose inactive_cold_coverage_adaptive_score plus health/speed/active/load factors, pressure EWMA, active due/waiting counts, base slots, and max slots.
- Heartbeat shows DANGER cold <slots> score <score> only when cold coverage actually selected symbols; otherwise it shows cold off <reason>.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
With open/opening positions: cold off open_or_opening_position_present.
With active due symbols: cold off active_due_symbols_present.
With slow heartbeat, low WS health, active waiting soft-cap, or high REST/cache pressure: cold off <specific reason> or fewer DANGER cold slots.
When idle, fast, healthy, and low-pressure: symbol_batch_selected shows precise_DANGER_cold_coverage symbols and inactive_cold_coverage_adaptive_score >= 0.30.
```

Risk:

```text
Medium / DANGER-controlled. The patch can increase cold universe coverage during very healthy idle periods, which can increase discovery and parity-audit coverage, but it is not proof of PnL edge. Monitor scheduler_cycle_seconds, signal_scan_seconds, aggtrade_network_calls, aggtrade_rest_fetched_ms, ws_aggtrade_coverage_pending_count, cold score and selected slots before real-order use.
```

## 2026-05-14 - P210 proposed DANGER: local guard, flow radar, wider micro-cache metrics

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Move more live discovery work into explicit DANGER observability without increasing exchange-position preflight latency or silently scanning the full universe.
```

Changes:

```text
- DANGER local entry-position guard now uses in-process open/opening symbol state before entry instead of fetching exchange position amount before every signal. Startup cleanup plus post-fill position verification remain required; post-fill mismatch closes only the new fill amount and raises integrity error.
- DANGER ticker flow radar uses existing all-ticker WS quote-volume and trade-count deltas to promote early flow-only watch symbols before the normal price-delta radar threshold, without REST calls.
- WS aggTrade rolling buffer default increases to 60 minutes, but subscriptions remain limited to active/ticker-radar/watch/current cold symbols; there is no full-universe micro-tape subscription.
- Cold coverage usefulness is now measurable in live_cycle_summary: cold scanned symbols, due/evaluated TFs, retryable dependencies, selected signals, order attempts, and totals.
- Heartbeat prints selected cold slots and adaptive score only when DANGER cold actually runs.
```

Validation:

```bash
python -m compileall data/exchanges research_tools constants.py main.py
```

Expected smoke readout:

```text
Check live_cache_config for DANGER local guard, DANGER flow radar thresholds, and widened active/radar-only micro-cache policy. In ticker_radar_snapshot, danger_flow_radar_candidate_count/promoted_count should stay visible. In live_cycle_summary, cold_* counters show whether cold coverage actually produces candidates/signals/orders or only burns latency.
```

Risk:

```text
High / DANGER-labeled. Flow radar can increase false positive watch symbols; widened WS buffers increase memory; local pre-entry guard assumes startup exchange-position cleanup and single live process. Do not run multiple live processes. Monitor post-fill position mismatch, scheduler_cycle_seconds, ws target counts, memory, and cold counters before judging edge.
```

## P211 — PROPOSED — DANGER runner/fader pre-pump context study
- Adds `research_tools/runner_fader_prepump_context.py` offline experiment.
- Labels closed backtest trades as runner/fader/mixed and computes only pre-anomaly HTF context on 30m/1h/2h/6h windows.
- Writes feature separation artifacts to test whether runners/faders are distinguishable before pump start.
- Commit: UNKNOWN.

## P212 — PROPOSED — DANGER backtest prepump runner/fader artifacts by default
- Supersedes standalone-only P211 by wiring the runner/fader pre-pump context study into `run_anomaly_strategy_backtest` after `anomaly_trades.csv` is written.
- Backtest now writes `runner_fader_prepump_*.csv` by default in the same artifact directory, using `anomaly_timestamp_ms` as the exclusive feature anchor and 30m/1h/2h/6h HTF context windows.
- Adds `runner_fader_prepump_run_status.csv` so missing cache/read failures are visible without deleting the primary backtest artifacts.
- Adds CLI knobs: `--write-prepump-context`, `--prepump-context-timeframe`, `--prepump-context-windows`, `--prepump-context-min-coverage-ratio`.
- Commit: UNKNOWN.

## 2026-05-14 - P213 applied locally: warm-watch scheduler gate

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
```

Intent:

```text
Stop sending every cheap radar hit directly into expensive precise scan. Keep cheap all-symbol visibility, hold suspicious symbols in a bounded warm-watch layer, widen micro-cache only for those symbols, and scan precisely only when the next radar observation still shows rising flow without fade/chase.
```

Changes:

```text
- Adds warm-watch config/CLI: enabled flag, TTL, min observations, and price-delta window.
- Ticker/flow radar candidates now become warm-watch rows first; precise radar watch is created only after repeated qualifying observations.
- Warm-watch rejects explicit fade/chase/not-rising cases and writes warm_watch_rejected artifacts.
- WS aggTrade subscription targets include active + warm-watch + precise-radar + current batch symbols, not the full universe.
- symbol_batch_selected and live_cycle_summary expose warm-watch waiting/marked/promoted/rejected counts.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Expected smoke readout:

```text
live_cache_config shows warm_watch_* settings. ticker_radar_snapshot shows warm_watch_marked_count first, then warm_watch_promoted_count only after continuing qualifying radar observations. symbol_batch_selected shows warm_watch_waiting_count while those symbols are cached but not precise-scanned. ticker_radar_promoted appears after warm_watch_precise_promoted, not on the first cheap radar hit.
```

Risk:

```text
Medium. This deliberately adds one radar-observation delay before precise scan, so it can miss one-shot pumps; the tradeoff is lower precise-scan latency pressure and better pre-pump visibility. It does not change entry/category logic.
```
## 2026-05-14 - P214 applied locally: rolling symbol context snapshot

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Move expensive prior-fast-fade/HTF context work out of the precise scan path. Live keeps a cache-only rolling symbol_context_snapshot.csv and category checks consume the prepared in-memory snapshot.
```

Changes:

```text
- Adds symbol context snapshot config/CLI: enabled flag, interval, symbols per cycle, and freshness SLA.
- Adds symbol_context_snapshot.csv artifact with per-symbol/per-timeframe baseline medians, cache coverage, available prior spike timestamps, and prior fast-fade timestamps.
- The rolling updater reads local parquet cache only; it does not fill missing context via REST during snapshot refresh.
- _live_prior_fast_fade_72h now reads the prepared snapshot and filters event timestamps relative to the current decision timestamp, avoiding lookahead.
- Missing/stale/unavailable snapshots are explicit retryable category dependencies instead of hidden synchronous fallback fetches.
- live_cycle_summary and live_events expose snapshot update status, counts, and output file.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep symbol-context
```

Expected smoke readout:

```text
live_cache_config shows symbol_context_snapshot_policy=cache_only_rolling_table_no_precise_scan_context_fetch. live_cycle_summary shows symbol_context_snapshot_status/updated/failed counts. symbol_context_snapshot.csv exists even before any trades. Category rejects caused by missing context show symbol_context_snapshot_missing/stale or context=<cache reason>, and precise scan no longer calls the old 72h context fetch path.
```

Risk:

```text
Medium. Early live cycles can reject runner categories as retryable until snapshots are populated and fresh. This is intentional latency protection, not a trading-filter change. If cache is stale or absent, the artifact will show unavailable snapshots instead of silently fetching context inside precise scan.
```


## 2026-05-14 - P215 applied locally: missed-pump visibility artifact

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make top-growth pumps auditable against live scheduler visibility instead of manually guessing whether radar, warm-watch, precise scan, category, or execution guard missed them.
```

Changes:

```text
- Adds missed_pump_visibility.csv plus per-hour missed_pump_visibility_YYYYMMDD_HH0000_UTC.csv in top_growth artifacts.
- Adds optional --visibility-events-csv for standalone top-growth runs to join top movers against a live run's live_events.csv.
- Visibility rows expose radar_promoted, flow_radar_promoted, warm_watch, precise_scanned, category_rejected, execution_rejected, position_opened, first timestamps, and not_scanned_reason.
- top_growth_index.csv now records the per-hour visibility file.
- Missing/invalid live_events input is explicit in visibility_source_status/reason; no synthetic visibility is inferred.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-top-growth --help | grep visibility
```

Expected smoke readout:

```text
run-anomaly-top-growth writes top_growth/missed_pump_visibility.csv and a per-period missed_pump_visibility_*.csv. With --visibility-events-csv pointing to a live run, top movers get first radar/warm/scan/reject timestamps and a not_scanned_reason that points to radar, warm-watch, precise scan, category, execution, or missing visibility input.
```

Risk:

```text
Low/medium. This is artifact-only and does not change signal selection or orders. The first version depends on live_events.csv event coverage; if precise/category paths do not emit enough events for a symbol, the artifact will show untracked/no-actionable rather than pretending to know.
```


## 2026-05-14 - P216 applied locally: latency SLA optional scan controller

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Protect active/radar reaction latency before expanding visibility with optional warm precise promotions or cold coverage.
```

Changes:

```text
- Adds latency_sla_controller config/CLI with active+radar due-scan p95 threshold and min sample count.
- Computes due-scan latency as now minus the close time of the latest unscanned entry candle, not candle start time.
- Keeps active and already-promoted radar precise scans eligible even when SLA is breached.
- Gates optional precise cold coverage to zero when active/radar due-scan p95 breaches SLA.
- Defers warm-watch-to-precise promotion while SLA is breached and emits warm_watch_precise_deferred_latency_sla instead of silently dropping the candidate.
- Exposes latency SLA status, p95/max/sample count, threshold, reason, and warm_watch_deferred_count in live events and cycle summaries.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep latency-sla
```

Expected smoke readout:

```text
live_cache_config shows latency_sla_policy and threshold. symbol_batch_selected/live_cycle_summary show latency_sla_status. Under backlog, optional cold coverage has gate_reason=latency_sla_due_scan_p95_above_threshold and warm candidates emit warm_watch_precise_deferred_latency_sla instead of becoming ticker_radar_watch.
```

Risk:

```text
Medium. This can reduce discovery breadth during latency spikes by design. It should not suppress active/radar reaction scans or alter entry/category/order logic. If the SLA is set too low for the chosen TF/host, warm precise promotion and cold coverage may stay mostly off; artifacts will show the reason.
```

## 2026-05-14 - P217 applied locally: prepump runner/fader warm-watch scoring

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/runner_fader_prepump_context.py
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Use P212 runner/fader pre-pump context only as a warm-watch priority scorer after explicit 30d validation, not as an entry filter or trade veto.
```

Changes:

```text
- Adds public compute_spot_prepump_window_features() helper so live and P212 share the same spot/flow pre-pump feature names.
- Extends symbol_context_snapshot.csv to v2 with cache-only prepump spot/flow feature JSON for configured windows.
- Adds optional --prepump-warm-watch-scoring-enabled and profile CSV controls.
- Loads runner_fader_prepump_feature_separation.csv only when explicitly enabled; invalid/missing/unstable profiles fail startup instead of falling back.
- Applies the profile only as a bounded additive warm-watch/radar priority score adjustment.
- Emits prepump_warm_watch_* score/status fields in warm-watch and ticker-radar artifacts.
- Does not change category selection, execution guards, entries, exits, stops, or order logic.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep prepump
```

Expected smoke readout:

```text
With scoring disabled, live behavior is unchanged except for new dormant config fields. With scoring enabled and a valid 30d feature separation CSV, live_cache_config shows profile status/feature count, symbol_context_snapshot.csv includes prepump_spot_features_json, and warm_watch/ticker_radar events include prepump_warm_watch_scoring_status plus score adjustment. No entry/category reject reason should come from prepump scoring.
```

Risk:

```text
Medium. This can change which warm-watch candidates get precise scan first, so it affects discovery priority. It intentionally cannot block a trade directly. The current live feature subset is cache-only spot/flow features; OI/derivatives prepump features remain offline-only until a typed rolling context source is added.
```

## 2026-05-14 - P218 proposed: budget optional context and warm micro-cache

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-14

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make P213-P217 operationally safe under load: active/radar reaction latency must win over optional context upkeep and warm micro-cache breadth.
```

Changes:

```text
- Moves symbol_context_snapshot maintenance after ticker, batch selection, WS subscription, precise scan, order open, reconcile, and cache flush.
- Gates symbol_context_snapshot updates by the same active/radar latency SLA used for optional scans.
- Adds a per-cycle wall-clock budget for cache-only context snapshot work.
- Advances the context snapshot cursor only for processed symbols, so budgeted cycles do not silently skip unprocessed universe slices.
- Uses an effective snapshot freshness window based on universe size, symbols_per_cycle, and update interval, while keeping the configured value as the minimum.
- Caps warm-watch aggTrade WS targets by score/recency without capping active symbols, opening symbols, current batch, or already-promoted radar watches.
- Emits explicit context budget/freshness and warm-watch target/drop counts in live artifacts.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E 'warm-watch-aggtrade|symbol-context-snapshot-max'
```

Expected smoke readout:

```text
live_cycle_summary should show symbol_context_snapshot_seconds after the critical scan path and symbol_context_snapshot_cycle_budget_seconds. Under active/radar backlog, symbol_context_snapshot_skipped should appear with skipped_latency_sla. ws_aggtrade_subscription_target should include warm_watch_aggtrade_target_cap, target_count, and dropped_count.
```

Risk:

```text
Low/medium. Optional context snapshots may refresh more slowly under load by design, but the artifact now states this directly. Active/radar scans and order logic are unchanged. Warm-watch micro-cache breadth is bounded, so a very noisy radar can delay flow detail for lower-score warm symbols.
```

## 2026-05-15 - P222 proposed: live synthetic bucket diagnostics and terminal ledger provenance

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Keep the existing closed-candle entry aggregation behavior, but make synthetic zero-bucket repair visible in artifacts and preserve scan/guard provenance on terminal ledger rows.
```

Changes:

```text
- Adds synthetic missing OHLCV bucket counting before `_fill_missing_ohlcv_buckets()` fills entry segments.
- Emits `entry_segment_synthetic_ohlcv_buckets` when a live forming-setup scan uses one or more synthetic zero buckets.
- Adds synthetic bucket count to missed-entry replay probe diagnostics when a prior missed signal is found.
- Writes `source_scan_mode`, `danger_cold_coverage_source`, and `entry_position_guard_source` to closed/exit-unresolved live ledger rows, not only open rows.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

Expected smoke readout:

```text
If an entry segment has missing closed buckets, live_events.csv contains `entry_segment_synthetic_ohlcv_buckets` with synthetic_bucket_count and entry_segment_bucket_count. Closed or exit-unresolved live_positions.csv rows keep the same source_scan_mode / guard provenance as the open row.
```

Risk:

```text
Low. This is diagnostic/audit only. It does not change signal thresholds, order logic, position guard behavior, fills, stops, or exits.
```

## 2026-05-15 - P231 proposed: clear wrapped live heartbeat rows

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Fix duplicated/concatenated live heartbeat output in narrow terminals where the inline status line wraps across multiple physical rows.
```

Changes:

```text
- Tracks how many terminal rows the last inline live status occupied.
- Clears every occupied row before drawing the next heartbeat instead of clearing only the current row.
- Keeps normal log messages ordered by finishing the open status line before printing them.
- Does not change live scan, delayed replay, order, Telegram, or artifact logic.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Console-output-only change. It fixes terminal rendering when status messages exceed the terminal width; trading and audit data paths are unchanged.
```


## 2026-05-15 - P223 applied locally: idle-only delayed replay audit for live anomalies

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Capture every live category-level anomaly decision for delayed postmortem without stealing critical resources from live execution.
```

Changes:

```text
- Adds --delayed-replay-enabled and budget/idle/delay flags to run-anomaly-live.
- Captures category_selected and category_rejected live anomaly decisions into delayed_replay/delayed_replay_queue.jsonl.
- Processes queued cases only after the critical live cycle path and only when active symbols, opening symbols, and open positions are all zero for the configured idle window.
- Uses cache/process-memory data only for delayed outcome checks; it does not fetch missing data, write orders, or mutate live state.
- Writes delayed_replay_results.csv, delayed_replay_summary.csv, and live_status.json with the explicit contract delayed_replay_v1_live_event_frozen_decision_cache_only_idle.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
With --delayed-replay-enabled true, category_selected/category_rejected events enqueue delayed replay cases. While live has active/opening/open positions, delayed_replay_summary.csv says live_not_idle or idle_window_too_short. Once idle and the delay has elapsed, delayed_replay_results.csv records audit-only frozen-decision rows using cache-only outcome data or an explicit cache_only_* insufficiency status.
```

Risk:

```text
Low/medium. Trade logic and order execution are unchanged. The replay is intentionally cache-only and artifact-based, so it may report insufficient replay data instead of forcing REST/network work. It is not yet a full backtest recomputation; recompute_status says this explicitly to avoid fake would-enter claims.
```

## 2026-05-15 - P224 applied locally: frozen-decision delayed replay recompute and Telegram alert

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make delayed replay answer whether the same frozen decision candle would produce an entry under the live/backtest-style signal builder, while keeping replay cache-only, idle-only, and visible to the operator when live did not open.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v2_frozen_decision_recompute_cache_only_idle_tg.
- Adds --delayed-replay-telegram-enabled true/false to run-anomaly-live.
- Recomputes each queued case from cached closed OHLCV windows up to the original decision timestamp, without using future candles and without fetching missing exchange context.
- Captures execution guard rejects such as stale/drift/TP1-reached/RR-collapsed/max-positions as replay cases, not only category selected/rejected cases.
- Writes recompute_status, would_select_signal, would_enter_under_frozen_decision, recomputed prices/category, reject reasons, data status, mismatch_type, and telegram_notified to delayed_replay_results.csv.
- Sends an events-channel Telegram message when replay recomputes an entry for a case where live did not open/select the entry.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py launcher.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
With --delayed-replay-enabled true, replay rows no longer say not_recomputed_artifact_frozen_decision_snapshot. They show recomputed_signal/recomputed_no_signal or an explicit cache-only insufficiency status. If a live rejected/execution-rejected case recomputes to an entry, delayed_replay_results.csv has mismatch_type live_rejected_replay_would_enter or live_execution_rejected_replay_would_enter and a Telegram events message says Replay found the entry.
```

Risk:

```text
Medium. This adds more replay computation, but only after the existing idle gate and within existing per-cycle budgets. It deliberately disables synchronous mark/OI exchange-context fetches during replay, so categories requiring unavailable derivatives context may produce explicit replay_exchange_context_fetch_disabled rejects instead of optimistic entries.
```

## 2026-05-15 - P225 applied locally: delayed replay frozen signal snapshot fallback

Status: APPLIED locally / UNKNOWN commit
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from silently missing execution-rejected runner signals when cache-only recomputation cannot refetch mark/OI exchange context.
```

Changes:

```text
- Adds a typed parser for the frozen `LiveSignal.to_json()` snapshot already stored in delayed replay cases.
- Adds `recompute_source` to delayed replay results.
- If cache-only recomputation reaches `replay_exchange_context_fetch_disabled` but the original live signal snapshot is present, records the signal as `frozen_live_signal_snapshot_exchange_context_unavailable` instead of `recomputed_no_signal`.
- Keeps the no-network rule: replay still does not fetch mark/OI context and the result source explicitly says when a frozen live signal snapshot was used.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
For execution-rejected cases with a stored signal_json, delayed_replay_results.csv can now show recompute_status=frozen_live_signal_snapshot_exchange_context_unavailable and recompute_source=frozen_live_signal_snapshot instead of suppressing the case as replay_no_signal when mark/OI fetch is intentionally disabled. TG notification still fires only for live non-selected/non-opened cases where would_enter_under_frozen_decision=true.
```

Risk:

```text
Low/medium. This is audit-only and does not change live trading. It uses a frozen live signal snapshot only when exact cache-only recomputation is blocked by intentionally disabled exchange-context fetch, so the row remains honest about source.
```



## 2026-05-15 - P226 proposed: label delayed replay evidence source

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from overstating frozen live-signal snapshot fallback as a strict backtest-like recomputation, and make post-decision outcome data explicit.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v3_evidence_labeled_cache_only_idle_tg.
- Adds strict_recompute_signal and frozen_signal_snapshot_used result columns so recomputed candle evidence and live snapshot evidence are separated.
- Splits mismatch labels: frozen snapshot cases now use *_frozen_signal_snapshot instead of *_replay_would_enter.
- Adds decision_data_end_timestamp_ms and outcome_is_post_decision to make the no-lookahead boundary visible in artifacts.
- Changes Telegram wording for snapshot fallback from "Replay found entry" to "Replay raised frozen signal" and includes the recompute source/caveat.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
Strict cache-only candle recompute entries have strict_recompute_signal=true and mismatch_type *_replay_would_enter. Frozen snapshot fallback entries have frozen_signal_snapshot_used=true, strict_recompute_signal=false, and mismatch_type *_frozen_signal_snapshot; Telegram does not call them strict replay-found entries.
```

Risk:

```text
Low. Audit/artifact/Telegram wording only; live trading logic, order placement, fills, stops, and scan thresholds are unchanged.
```

## 2026-05-15 - P227 proposed: delayed replay final-decision queue hardening

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from treating intermediate category-profile rejects as ignored live entries, while keeping audit coverage for final no-signal decisions and important execution rejects.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v4_final_decision_evidence_labeled_cache_only_idle_tg.
- Stops enqueueing a delayed replay case from each intermediate category_rejected event.
- Enqueues one final all_categories_rejected case only when the category loop ends without a selected signal.
- Adds delayed replay capture for reject_symbol_position_already_active, reject_stop_cooldown, reject_signal_not_closed_yet, and reject_stale_signal.
- Splits result semantics into strict_replay_would_enter, snapshot_signal_available, and operator_alert_kind; would_enter_under_frozen_decision is true only for strict cache-only candle recompute.
- Keeps frozen live-signal snapshot alerts labeled separately as frozen_signal_snapshot_only instead of strict replay-found entries.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low/medium. Audit-only; no order placement, live trading thresholds, fills, stops, or scanner selection are changed. The main behavior change is fewer false-positive replay queue cases from intermediate category-profile rejects.
```


## 2026-05-15 - P228 proposed: delayed replay result source column

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make delayed replay evidence source visible in the results CSV instead of computing it and then dropping it through csv extrasaction=ignore.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v5_result_source_visible_cache_only_idle_tg.
- Adds recompute_source to DELAYED_REPLAY_RESULTS_COLUMNS so strict cache-only recompute and frozen live-signal snapshot rows remain distinguishable in delayed_replay_results.csv.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Artifact schema/contract only; no live scan, order, fill, stop, or replay decision logic changes.
```

## 2026-05-15 - P229 proposed: immutable delayed replay decision snapshot

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make delayed replay recompute from the exact live decision inputs captured at signal/reject time when available, instead of relying first on later cache state.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v6_immutable_decision_snapshot_idle_tg.
- Captures an immutable live decision snapshot for delayed replay cases: baseline rows, setup row, entry rows through decision_timestamp_ms, and frozen mark/OI/prior-fast-fade context.
- Stores the snapshot on queued delayed replay cases and remembers it in memory so later execution rejects for the same decision reuse the same snapshot.
- Recomputes delayed replay from the immutable snapshot first; cache-only reconstruction remains a fallback when no snapshot exists.
- Allows replay recompute to use frozen live mark/OI/prior-fast-fade context without REST/network fetch.
- Adds decision_snapshot_status and decision_snapshot_source to delayed_replay_results.csv.
- Removes the duplicate recompute_source column introduced by the previous schema patch.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
python - <<'PY'
from research_tools.anomaly_micro_live import DELAYED_REPLAY_RESULTS_COLUMNS
from collections import Counter
assert not [k for k, v in Counter(DELAYED_REPLAY_RESULTS_COLUMNS).items() if v > 1]
PY
```

Risk:

```text
Medium-low. Audit-only and delayed-replay-only; no live order/fill/stop logic changes. Queue rows become larger because they carry compact candle snapshots, but only when delayed replay is explicitly enabled.
```


## 2026-05-15 - P226 proposed: label delayed replay evidence source

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Prevent delayed replay from overstating frozen live-signal snapshot fallback as a strict backtest-like recomputation, and make post-decision outcome data explicit.
```

Changes:

```text
- Upgrades delayed replay contract to delayed_replay_v3_evidence_labeled_cache_only_idle_tg.
- Adds strict_recompute_signal and frozen_signal_snapshot_used result columns so recomputed candle evidence and live snapshot evidence are separated.
- Splits mismatch labels: frozen snapshot cases now use *_frozen_signal_snapshot instead of *_replay_would_enter.
- Adds decision_data_end_timestamp_ms and outcome_is_post_decision to make the no-lookahead boundary visible in artifacts.
- Changes Telegram wording for snapshot fallback from "Replay found entry" to "Replay raised frozen signal" and includes the recompute source/caveat.
```

Validation:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Expected smoke readout:

```text
Strict cache-only candle recompute entries have strict_recompute_signal=true and mismatch_type *_replay_would_enter. Frozen snapshot fallback entries have frozen_signal_snapshot_used=true, strict_recompute_signal=false, and mismatch_type *_frozen_signal_snapshot; Telegram does not call them strict replay-found entries.
```

Risk:

```text
Low. Audit/artifact/Telegram wording only; live trading logic, order placement, fills, stops, and scan thresholds are unchanged.
```

## 2026-05-15 - P230 proposed: delayed replay Telegram follows enablement

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Remove the separate delayed-replay Telegram switch. Delayed replay is an operator-audit mode: when it is enabled and finds a meaningful mismatch, the Telegram alert path should be part of that mode instead of controlled by a second flag.
```

Changes:

```text
- Removes --delayed-replay-telegram-enabled from run-anomaly-live.
- Removes delayed_replay_telegram_enabled from MicroLiveConfig and CLI config construction.
- Records delayed_replay_telegram_policy=enabled_with_delayed_replay in live config artifacts.
- Sends delayed replay operator alerts whenever delayed replay is enabled and operator_alert_kind is produced.
- Artifact writing remains independent from Telegram delivery; queue/results/summary are still written even if Telegram send fails.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
python main.py run-anomaly-live --help | grep delayed-replay-telegram && exit 1 || true
```

Risk:

```text
Low. Audit/TG-control contract only. Live trading, order placement, fills, stops, scan thresholds and delayed replay decision logic are unchanged.
```


## 2026-05-15 - P233 proposed: fixed-width live status block

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Replace the long inline live heartbeat with a fixed-width operator status block that keeps replay backlog visible without wrapping into duplicated-looking terminal lines.
```

Changes:

```text
- Renders live heartbeat as a five-line fixed-width block: LIVE, FEED, PUMP, RPLY, RISK.
- Uses four-character main labels and three-character field labels.
- Adds a blank line before the block for visual separation.
- Keeps delayed replay backlog visible as RPLY/pnd, with off when replay is disabled.
- Updates inline status clearing to count explicit newline rows as well as terminal wrapping.
- Leaves trading, delayed replay decisions, Telegram and artifact logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Console rendering only; no live execution or strategy behavior changes.
```


## 2026-05-15 - P234 proposed: Russian grouped live status block

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Replace the four-letter technical live status block with a Russian operator block grouped by Connection, Market, Trading and Control semantics while keeping delayed replay backlog visible.
```

Changes:

```text
- Renders the live heartbeat as sections: Соединение, Рынок, Торговля, Контроль.
- Uses fixed-width space-padded cells with title and value on the same line.
- Shows live runtime as Время instead of only per-cycle seconds.
- Renames Replay to Повтор and cold coverage to Покрытие.
- Keeps delayed replay backlog visible as Повтор <pending>/<total> or Повтор Выкл.
- Leaves trading, scan, delayed replay decision, Telegram and artifact logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep delayed-replay
```

Risk:

```text
Low. Console rendering only; no live execution or strategy behavior changes.
```


## 2026-05-15 - P235 proposed: regroup Russian live status metrics

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Move active-symbol load into the Market block and move PNL into the Trading block so the Russian live heartbeat grouping matches operator semantics.
```

Changes:

```text
- Market now renders: Время, События, Активные.
- Trading now renders: PNL, Позиции, Ордера.
- Leaves heartbeat clearing, delayed replay, Telegram, artifacts and trading logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. Console rendering only; no live execution or strategy behavior changes.
```

## 2026-05-15 - P236 proposed: live network error visibility and Telegram retry

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
data/exchanges/ccxt_futures_client.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make live network/API failures visible as real operator alerts instead of letting retry warnings stick to the inline status block, and keep Telegram alert attempts alive while network degradation persists.
```

Changes:

```text
- Adds a typed retry logger boundary to CcxtFuturesClient so live can route retry warnings through the live status logger without touching private client internals.
- Adds highlighted live console alerts with a leading blank line for exchange retry exhaustion and network/API degraded state.
- Replaces one-shot async Telegram network-degraded notification with periodic enqueue retry while degraded.
- Records `network_degraded_telegram_alert_enqueued` artifacts for operator-audit visibility.
- Sends a Telegram recovery note with the original degradation reason when connectivity returns.
- Leaves trading, signal selection, order placement and position management logic unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python - <<'PY'
from research_tools.anomaly_micro_live import _LiveRetryLogger, _LiveStatusLogger
rows = []
status = _LiveStatusLogger(rows.append)
retry = _LiveRetryLogger(status)
retry.warning('Источник не ответил на «%s». Попытка %s/%s.', 'x', 3, 3)
status.alert('⚠️ сеть/API недоступны, жду восстановления.\nПричина: test')
assert rows[0].startswith('\n⚠️ Источник не ответил')
assert rows[1].startswith('\n⚠️ сеть/API недоступны')
PY
```

Risk:

```text
Low-to-medium. Console/artifact/Telegram alert path only; no trading decisions change. During a network outage the code retries Telegram enqueue every 60 seconds, but TelegramDispatcher cooldown still prevents repeated successful sends with the same key.
```

## P245 - proposed - live candidate queue pressure controller

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Stop old/weak radar and warm-watch candidates from keeping latency SLA permanently breached. The live loop should keep the strongest fresh candidates, expire stale backlog, and record explicit pressure/drop events instead of deferring the same tail forever.
```

Changes:

```text
- Adds an internal candidate queue pressure controller with no CLI flags.
- Under latency pressure or oversized radar/warm queues, keeps the strongest candidates and trims stale/weak tail.
- Emits candidate_dropped_latency_pressure and candidate_expired_backlog_stale events with source, score, flow fields, and latency SLA payload.
- Adds candidate_queue_* counters to symbol_batch_selected and live_cycle_summary.
- Treats candidate drop/expiry events as visibility evidence for closed-hour missed-pump audit.
- Does not loosen stale/drift/RR/actual-risk/order safety and does not add REST work to the scan path.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "candidate-queue|queue-pressure|live-top-growth|symbol-context-startup"  # expected: no output
```

Risk:

```text
Medium. This intentionally discards low-priority backlog under pressure, which should reduce false SLA breaches but can drop a weak candidate before precise scan. The event trail is explicit, and top-growth/missed-pump visibility can audit whether dropped candidates later became top movers.
```

## P246 - proposed - adaptive precise scan budget

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent live from spending the same precise-scan capacity during normal and overloaded states. Under backlog, active symbols keep priority and radar precise scans are reduced to the freshest/highest-score tail instead of expanding stale due-scan latency.
```

Changes:

```text
- Adds an internal adaptive precise-scan budget with no new CLI flags.
- Active/opening symbols are never dropped by the adaptive cap.
- Under latency SLA breach, radar precise scans are limited to the top 1 candidate for the cycle.
- Under queue pressure or active-symbol pressure, radar precise scans are limited to the top 2 candidates for the cycle.
- Under runtime/cache pressure, radar precise scans are limited to the top 3 candidates for the cycle.
- Adds adaptive_precise_budget_* fields to symbol_batch_selected and live_cycle_summary.
- Keeps execution safety, discovery filters, order/fill/stop logic and REST fetch behavior unchanged.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "adaptive-precise|precise-budget"  # expected: no output
```

Risk:

```text
Medium. This intentionally scans fewer radar candidates per cycle during pressure. The tradeoff is deliberate: fresher top candidates are preferable to scanning a wider stale queue. New artifact fields make the cap auditable.
```

## 2026-05-15 - P237 proposed: live session top-growth status from ticker snapshots

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Show the operator current session top movers in the live heartbeat without spending extra REST/OHLCV budget or faking closed-hour top-growth artifacts.
```

Changes:

```text
- Tracks session top growth from the already-required ticker snapshots only.
- Uses the first usable ticker price seen inside the current UTC session as the baseline; if live starts mid-session, the baseline is explicitly the first available live price, not a reconstructed/fallback session open.
- Adds `top_growth/session_top_growth.csv` with one-minute evidence snapshots, including status/reason rows when no usable top exists.
- Extends the human heartbeat with a final session block such as `Азия` / `ALCH 74%    MEW 48%    HBAR 42%`.
- Leaves closed-hour `top_growth_index.csv` and `missed_pump_visibility.csv` semantics unchanged; those still require the standalone `run-anomaly-top-growth` command against closed 1h candles.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP; include it locally if your checkout has it.
```

Risk:

```text
Low-to-medium. Operator UI and artifacts only. The numbers are session-live ticker growth, not closed-hour OHLCV top-growth, and are labeled by source/status instead of being used as trading signals.
```

## 2026-05-15 - P238 proposed: align live session top-growth columns

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Keep the live heartbeat session-top row visually aligned with the three fixed-width operator status columns.
```

Changes:

```text
- Formats the live session top-growth row as three fixed-width cells instead of joining symbols with ad-hoc spacing.
- Pads missing top cells without inventing extra symbols or fallback growth values.
- Leaves session-top source semantics unchanged: ticker-snapshot baseline only, no OHLCV/REST fallback.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP; include it locally if your checkout has it.
```

Risk:

```text
Low. Operator UI formatting only; no trading logic, artifacts, or filters change.
```


## 2026-05-15 - P241 proposed: prioritize hot symbols in rolling context snapshot

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the budgeted rolling symbol-context snapshot spend its limited time on symbols that can actually affect near-term live decisions, especially symbols blocked by retryable prior-fast-fade context dependency.
```

Changes:

```text
- Adds an in-memory TTL queue for symbol-context priority requests.
- Marks symbols blocked by retryable category dependencies as priority context symbols.
- Rolling context snapshot priority order is now open positions / active symbols, retryable dependency requests, ticker radar, warm watch, then round-robin universe coverage.
- Adds `priority_reason_counts` to `symbol_context_snapshot_updated` events for auditability.
- Keeps context snapshot cache-only during live cycles; no synchronous REST/precise fetch is introduced in the hot path.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
# launcher.py is absent in the uploaded ZIP; include it locally if your checkout has it.
```

Risk:

```text
Low-to-medium. This changes context maintenance priority, not entry thresholds. Round-robin coverage still advances only for non-priority processed symbols, so hot-symbol retries should not starve context cursor accounting.
```

## 2026-05-15 - P242 proposed: incremental live closed-hour top-growth visibility

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make live runs populate closed-hour top_growth_index.csv and missed_pump_visibility artifacts without ticker-derived approximations and without blocking the hot scan path with a full-universe one-shot REST sweep.
```

Changes:

```text
- Adds default-on incremental live top-growth audit for closed 1h exchange candles.
- Processes only a bounded number of symbols per live cycle and writes the same top/status/visibility files as the standalone top-growth command when the hour scan completes.
- Uses live_events.csv from the same run as the visibility source.
- Keeps session-top heartbeat separate from closed-hour top-growth research artifacts; no ticker fallback is used for closed-hour audit.
- Adds CLI controls for enabling/disabling live top-growth audit, threshold, limit, per-cycle symbol budget, max cycle budget, and fetch spacing.
- Adds live_cycle_summary fields for the current top-growth audit status/progress.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "live-top-growth"
```

Risk:

```text
Medium. This adds exchange OHLCV reads during live, but bounded incrementally by symbols_per_cycle/max_cycle_seconds and separated from trading decisions. It does not change entry filters or order logic.
```

## 2026-05-15 - P243 proposed: make live top-growth always-on and latency-gated

Status: PROPOSED
Commit: UNKNOWN
Date: 2026-05-15

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Remove operator-facing live top-growth toggles/knobs and make the closed-hour audit run only under the existing live latency gate, with fixed conservative quotas when the live loop is not clearly idle.
```

Changes:

```text
- Removes the new `--live-top-growth-*` CLI flags and command plumbing from P242.
- Keeps live closed-hour top-growth audit default-on with internal constants instead of runtime operator switches.
- Skips top-growth processing when latency SLA blocks optional work.
- Uses the normal quota only when latency SLA is OK; otherwise uses a conservative quota for low-evidence/insufficient-sample states.
- Keeps closed-hour audit source strict: exchange 1h candles only, no ticker/session fallback.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "live-top-growth"  # expected: no output
```

Risk:

```text
Low-to-medium. This preserves P242 artifact visibility but prevents the audit from competing with delayed signal scans during latency pressure. It also removes new operator knobs, so quota changes now require code review instead of ad-hoc CLI changes.
```

## P244 - proposed - discovery live loosen + retryable data dependencies

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Reduce false live non-entries without weakening execution safety: loosen discovery-oriented market filters modestly, keep entry price drift unchanged, and classify unavailable taker-buy/mark/OI context as retryable data dependency instead of final market/category rejection.
```

Changes:

```text
- Live discovery defaults: start flow 5x -> 4x, min retention 70% -> 65%, verticality 0.25 -> 0.20, prior whipsaw cap 0.60 -> 0.75.
- Runner categories: prior_fast_fade cap 0 -> 1, taker-buy delta cap 0.25 -> 0.35, mark/OI confirmation thresholds slightly loosened.
- max_entry_price_drift_pct stays 0.003; late-entry safety is not loosened.
- Missing/invalid taker-buy context, unavailable mark basis, and unavailable OI now become retryable dependency rows with emit=false for category_rejected.
- Real computed mark/OI below threshold remain final market/category rejects.
- Removes P239 operator-facing startup/gap-tolerance flags; startup context backfill is always-on policy, tiny-gap tolerance remains code-reviewed constants.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "symbol-context-startup|symbol-context-snapshot-min|symbol-context-snapshot-max-gap|live-top-growth"  # expected: no output
```

Risk:

```text
Medium. This intentionally increases discovery sensitivity and may create more selected candidates. It does not loosen stale/drift/RR/actual-risk/order safety. New live validation must compare category_selected, signal_scan_retryable_dependency_blocked, execution rejects, and closed-hour missed-pump visibility before any further parameter changes.
```

## P252 - proposed - fix live cycle selection accounting and timestamp startup backfill status

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the runtime NameError caused by live_cycle_summary referencing the local symbol-batch selection outside its scope, and make the startup 72h context backfill status line show the current local clock time on every symbol update.
```

Changes:

```text
- Stores candidate-queue pressure fields from the latest symbol batch selection on runner state, like existing latency/adaptive budget fields.
- Reads those runner-state fields in live_cycle_summary instead of the out-of-scope selection variable.
- Adds HH:MM:SS to the inline 72h context backfill status line.
- Adds no CLI flags and no trading-logic changes.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "candidate-queue|queue-pressure|startup-backfill-time"  # expected: no output
```

Risk:

```text
Low. This fixes event-summary accounting and operator display only. Candidate selection, precise-scan budget, dependency cooldown, backfill, cache writes, and execution guards are unchanged.
```

## 2026-05-16 — P253 proposed

Startup after the 72h context backfill had silent stages before the first live heartbeat: forced cache flush, startup snapshot computation/writing, ticker radar seed/validation, and entering the first live cycle. P253 adds inline status updates with the current clock time for these stages so the operator can see where startup is spending time. It changes display/status only; backfill, cache, ticker source policy, selection, and trading logic are unchanged.

Current commit: UNKNOWN.
Next validation: restart live and confirm startup progresses through `контекст 72ч · запись кеша`, `контекст 72ч · снимок N/total`, `контекст 72ч · запись snapshot`, `тикеры · стартовый снимок`, `тикеры · проверка радара`, then the normal live heartbeat appears after the first cycle summary.

## 2026-05-16 — P254 proposed

Live heartbeat operator UI now shows the connection group as `Время / Пульс / Данные`. `Пульс` is the last scheduler-cycle duration. `Данные` is a compact health label derived from real data-path evidence: ticker REST fallback, aggTrade REST gap backfill, subscription/coverage wait, cache REST fill, or remaining cache gaps. No trading logic, retry policy, or execution guard is changed.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "heartbeat|data-status|пульс"  # expected: no output
```

Risk: low. Display and live_cycle_summary diagnostics only. The new status label must not be used as a trading condition.

## 2026-05-16 — P255 proposed

Startup 72h context preparation now ends with an explicit readiness verdict before the first live cycle. The verdict counts fully ready symbols, partial symbols, unavailable symbols, ready snapshots, tolerated-gap snapshots, missing snapshots, and top unavailable reasons. Real-orders live is refused when ready symbol/snapshot ratios are below internal reviewed thresholds, rather than starting with a mostly blind prior-fast-fade context. No CLI flags are added, and retryable dependency remains only a residual safety path.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "startup-readiness|readiness-threshold|context-ready"  # expected: no output
```

Risk: medium-low. The patch can intentionally stop real-orders startup when context coverage is poor. Trading filters and execution guards are unchanged.

## 2026-05-16 — P256 proposed

Startup 72h context preparation progress is made fully observable for the heavy post-backfill stages. The forced cache flush now reports per symbol/timeframe progress with local clock time and ETA instead of one silent `запись кеша` line. Symbol-context snapshot computation also reports ETA per symbol, and final snapshot/readiness lines finish with explicit ETA status. This is operator-visibility only: cache write policy, context readiness thresholds, trading filters, and execution guards are unchanged.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "startup-cache-flush-eta|context-snapshot-eta"  # expected: no output
```

Risk: low. Display and progress callback only. The flush still writes through the same storage path; the callback must not be used as trading state.

## 2026-05-16 — P257 proposed

Startup/reprepare context preparation no longer accumulates the whole 72h OHLCV write buffer and then performs one heavy final cache flush. During the 72h backfill loop it now flushes cache writes in bounded symbol/timeframe chunks, with the same inline `HH:MM:SS + ETA` progress format as the rest of preparation. The final flush remains as a safety drain for any remaining buffered rows.

Live context reprepare is state-based, not scheduled. It only starts when symbol-context readiness is degraded or stale and the runner is safe: zero active symbols, zero open positions, zero opening symbols, and zero tracked order-reconcile symbols. If unsafe, it writes `live_context_reprepare_deferred`; if safe, it writes explicit `live_context_reprepare_started/completed` events and terminal progress. If real-orders context is still below readiness thresholds after a safe reprepare, live stops safely with a data-integrity error instead of continuing with blind context.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "reprepare|переподготов|context-reprepare|startup-flush-chunk"  # expected: no output
```

Risk: medium. Cache writes now happen in smaller chunks during startup/reprepare, reducing the final silent flush, but this changes write timing. It does not change trading filters, signal construction, order/fill/stop logic, or CLI flags.

## 2026-05-16 — P258 proposed

Mandatory 72h context preparation now sends concise Telegram events when the mandatory 72h preparation/reprepare starts and when the readiness verdict is known. The messages follow the existing events-channel style and do not change preparation policy, readiness thresholds, cache writes, trading filters, or execution guards.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "context-preparation-telegram"  # expected: no output
```

Risk: low. Notification-only patch. Telegram failures remain non-blocking and are already recorded through the dispatcher error path.


## P247 - proposed - dependency retry cooldown

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent retryable data-dependency blocks from immediately re-entering precise scan every cycle. A missing context/taker/mark/OI dependency should wait briefly for the dependency updater or expire by the existing stale guard, not create a self-sustaining backlog.
```

Changes:

```text
- Adds an internal retry cooldown for retryable dependency blocks, with no new CLI flags.
- Schedules retryable dependency cases for a short cooldown bounded by the entry timeframe and stale timeout.
- Skips active cooldowns in due-scan latency calculations so intentional dependency waits do not breach latency SLA.
- Prioritizes cooldown symbols in rolling symbol_context_snapshot refresh.
- Emits signal_scan_dependency_retry_scheduled and candidate_expired_dependency_timeout artifacts.
- Adds dependency_retry_cooldown_* counters to live_cycle_summary and signal_symbol_scan_summary.
- Keeps pass-by-default forbidden: dependency unavailable still blocks entry until it becomes checkable or the signal expires.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "dependency-retry|retry-cooldown|dependency-cooldown"  # expected: no output
```

Risk:

```text
Medium-low. Retryable dependency candidates are intentionally not rescanned every cycle, which reduces load but can delay a newly ready dependency by up to the internal cooldown. Stale timeout still prevents late entries, and artifacts show scheduled retries plus dependency timeouts.
```


## P248 - proposed - entry-below-stop diagnostics

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make invalid initial-risk live rejects diagnosable without changing stop logic. When the computed long stop is at/above entry, artifacts must show whether the active stop came from EMA20 or the structural low-based stop instead of emitting a generic invalid_initial_risk row.
```

Changes:

```text
- Replaces the generic live event reject_invalid_initial_risk with reject_entry_below_initial_stop.
- Adds risk_side, stop_source, previous_stop, decision_ema20, and stop_above_entry_pct to event details.
- Keeps the same safety behavior: the setup is rejected; no alternate stop is substituted.
- Adds both old and new event names to precise-scan visibility so older/live mixed artifacts remain readable.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "entry-below-stop|initial-risk-diagnostics"  # expected: no output
```

Risk:

```text
Low. Artifact naming/details change only; trade selection, stop calculation, and execution guards are unchanged.
```

## P249 - proposed - dependency cooldown scan-summary accounting

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the P247 accounting path where dependency-retry cooldown skips could be counted as generic skipped_not_due in per-symbol scan summaries. Cooldown waits must stay visible as dependency cooldown, not as ordinary not-due scheduling noise.
```

Changes:

```text
- When a timeframe scan is skipped because a dependency retry cooldown is active, signal_symbol_scan_summary increments dependency_retry_cooldown_skipped_count instead of skipped_not_due_count.
- Keeps the cooldown gating behavior unchanged; this is artifact accounting only.
- Adds no CLI flags and no trading-logic changes.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "candidate-queue|queue-pressure|adaptive-precise|precise-budget|dependency-retry|retry-cooldown|dependency-cooldown|entry-below-stop|initial-risk-diagnostics"  # expected: no output
```

Risk:

```text
Low. The patch changes only per-symbol summary classification for an already-skipped cooldown scan. It does not change candidate selection, dependency retry timing, stale guards, or execution logic.
```

## P250 - proposed - startup context backfill ETA status

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the default-on 72h startup context backfill observable while it is running. The operator status line should refresh for every symbol with ETA instead of appearing stuck on the first progress message, and the status must not show misleading ok/error counters.
```

Changes:

```text
- Replaces the every-50-symbol startup backfill logger line with an inline status update for every symbol.
- Shows current symbol and ETA based on completed-symbol throughput.
- Removes ok/error counters from the operator status line; detailed fetch failures remain in live_events.csv and completion artifacts.
- Adds no CLI flags and no trading-logic changes.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help | grep -E "startup-backfill-eta|context-backfill-progress"  # expected: no output
```

Risk:

```text
Low. Console/status output only. Backfill coverage, fetch behavior, failure events, cache flushing, snapshot computation, and execution guards are unchanged.
```

## 2026-05-16 — P259 proposed

P259 adjusts only the operator heartbeat display. The connection block now shows stability, pulse, and data health; runtime moved back to the market block and is counted from live-loop start after mandatory 72h context preparation, not from process startup. Session top movers are rendered to 0.1%. The live status logger now clears the previous multi-line heartbeat before warnings/ordinary log messages, so an error does not leave a stale grid above the alert.

Current commit: UNKNOWN.
Next validation: run live until the first warning/retry message and verify the old heartbeat is cleared before the alert, then the next heartbeat renders once.

## 2026-05-16 — P261 applied — c0b5dfd970da — stop verification exchange lookup and critical halt TG

Files:

```text
data/exchanges/ccxt_types.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Make initial/replacement stop protection verification strict against Binance/CCXT eventual consistency: a created stop is not trusted from the create response alone, and live halt messages must explicitly show the affected symbol.
```

Changes:

```text
- Keeps the 5 x 0.5s stop visibility verification loop.
- Matches open orders by exchange order id and clientOrderId.
- If open orders do not show the stop, verifies through the typed exchange boundary `fetch_order_by_client_order_id`.
- Rejects terminal stop statuses and still validates side/type/reduceOnly/amount/stopPrice.
- Adds `symbol` to stop integrity exceptions, terminal log, live_events.csv and synchronous Telegram halt message.
- Adds the client-order lookup method to the CCXT protocol contract.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# synthetic smoke: fetch_open_orders empty for first 1-4 checks then stop visible -> position_stop_order_visibility_delayed
# synthetic smoke: fetch_open_orders empty but fetch_order_by_client_order_id returns NEW/STOP_MARKET/reduceOnly -> position_stop_order_client_lookup_confirmed
# synthetic smoke: stop unresolved after 5 checks -> live_data_integrity_error with symbol and Telegram Live остановлен message
```

Risk:

```text
Medium-low. Touches live stop verification and halt reporting only. It does not change signal selection, entry guards, order sizing, TP/SL math or PnL. A stop that cannot be confirmed by open orders or clientOrderId lookup still halts live unless P262 danger continue mode is explicitly enabled.
```

## 2026-05-16 — P262 proposed — danger continue after order/position errors

Files:

```text
cli/parser.py
cli/commands.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Allow long data-collection live runs to continue after order/position integrity failures only when the operator explicitly enables a dangerous diagnostic mode.
```

Changes:

```text
- Adds `--danger-continue-after-order-position-errors`, default false.
- Keeps strict halt behavior by default.
- Classifies order/position flow failures with `LiveOrderPositionIntegrityError`.
- In danger mode, writes `live_order_position_integrity_error`, sends synchronous Telegram, flushes live cache when inside the loop, and continues startup/live execution.
- Records the selected policy in the live config artifact event.
- Applies the same danger policy to startup exchange-position cleanup failures.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
# strict smoke: without flag, injected stop verification failure returns code 3.
# danger smoke: with flag, injected stop/order/position failure writes live_order_position_integrity_error, sends TG, and continues to the next cycle.
```

Risk:

```text
High when enabled. The flag intentionally keeps live running after order/position integrity failures, including startup exchange-position cleanup failures, so it is for data collection / diagnostics, not normal protected trading. Default behavior remains strict.
```

## 2026-05-17 — P273 proposed — restore live helper dataclasses after category extraction

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
```

Intent:

```text
Fix `NameError: name 'WsAggTradeReadResult' is not defined` in live by restoring non-category helper dataclasses used by WS aggTrade, OI, mark-basis, and raw aggTrade cache paths. These containers are separate from the shared pump category contract.
```

Validation:

```bash
| P264 | Share live/backtest category contract and parity labels | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_category_contract.py`, `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | parity/data-quality | Move runner category thresholds/priority into one shared contract used by live and backtest; keep discovery as explicit backtest fallback; add live-vs-discovery category family artifacts; stop materialized-cache metadata gaps from crashing candidate collection; preserve original forming setup source in delayed replay; add selected terminal outcome events; mark synthetic OHLCV buckets explicitly. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P266 | Add backtest context parity artifacts and skipped category metadata | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_strategy_backtest.py`, `research/*` | parity/diagnostics | Preserve pump category metadata on every skipped trade row, including execution-guard rejects; widen the derivatives pre-context universe with live-priority category intents before final mark/OI checks; add `anomaly_context_parity_report.csv` so context request/coverage/final-signal/trade outcome can be audited without mixing discovery with live-priority rules. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |

| P265 | Track discrete signal missed after live execution guard | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research/*` | live observability | When a selected signal would have been executable at its discrete signal snapshot price but is rejected at current live price, write `discrete_signal_snapshot_entry_missed` and format the existing order-blocked Telegram as a missed discrete entry instead of a generic reject. Does not allow stale/discrete execution. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |
| P267 | Preserve replay synthetic provenance and OI cache freshness | APPLIED locally / UNKNOWN commit | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_continuation_lab.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | data-quality/parity | Keep `synthetic_ohlcv_bucket` through delayed replay snapshots; reject live/replay signals when real entry buckets are fewer than confirmation candles; preserve existing synthetic flags when filling gaps; add OI cache/load/asof freshness fields to backtest context parity artifacts; align research status for P264-P266 as applied locally in the uploaded ZIP. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py` |

| P268 | Add real Binance order lifecycle smoke command | APPLIED locally / UNKNOWN commit | `research_tools/live_order_smoke.py`, `data/exchanges/ccxt_futures_client.py`, `domain/exceptions.py`, `cli/parser.py`, `cli/commands.py`, `research/*` | live safety/exchange-boundary | Add an explicit real-order smoke command that opens one minimal Binance USD-M market long, verifies reduce-only STOP_MARKET visibility through open orders and clientOrderId lookup, then cancels the stop and closes reduce-only by default; classify Binance `-2013 Order does not exist` as `ExchangeOrderNotFound` instead of connectivity retry exhaustion. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `python main.py run-live-order-smoke --help` |

## 2026-05-16 — P263 proposed — live order lifecycle unit tests

Files:

```text
tests/test_live_order_lifecycle.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add deterministic unit coverage for the live real-order lifecycle without changing trading logic: entry order, exchange position delta, initial stop verification, unprotected exposure cleanup, stop exit handling, and TP1 -> BE stop replacement.
```

Changes:

```text
- Adds a fake exchange that exercises the same `_maybe_open_position()` and `_monitor_position()` live paths used by real orders.
- Covers successful entry with verified initial STOP_MARKET reduce-only order and ledger/event artifacts.
- Covers the observed live failure class: entry filled, initial stop never visible, then reduce-only cleanup and `LiveOrderPositionIntegrityError`.
- Covers monitor stop-exit finalization with verified stop fill.
- Covers TP1 partial reduce-only fill, stop replacement to break-even, old-stop cancel, then verified stop close.
```

Validation:

```bash
python -m unittest tests.test_live_order_lifecycle -v
python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Risk:

```text
Low. Test-only patch. It does not change live trading logic, exchange client behavior, signal selection, sizing, TP/SL math, Telegram formatting, or artifacts outside test execution.
```
| P269 | Use Binance conditional algo stop-order boundary | APPLIED locally / UNKNOWN commit | `data/exchanges/ccxt_futures_client.py`, `data/exchanges/ccxt_types.py`, `research_tools/anomaly_micro_live.py`, `research_tools/live_order_smoke.py`, `research/*` | live safety/exchange-boundary | Create, verify, cancel, reconcile, and smoke-test protective stops through Binance USD-M conditional/algo endpoints (`algoOrder`/`openAlgoOrders`) instead of ordinary order APIs; keep ordinary order lookup only as a legacy secondary path; final smoke checks include conditional/algo open orders. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `python main.py run-live-order-smoke --help` |

| P270 | Exercise real position management smoke | APPLIED locally / UNKNOWN commit | `research_tools/live_order_smoke.py`, `cli/parser.py`, `cli/commands.py`, `research/*` | live safety/exchange-boundary | Extend the real Binance smoke with an optional management lifecycle: create the initial algo stop, create and verify a closer replacement stop, cancel the old stop after replacement, close the position reduce-only while the replacement stop is still open, cancel remaining smoke orders, and assert final flat/no ordinary or algo orders. Existing quick smoke behavior is unchanged unless `--replacement-stop-distance-pct` is provided. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py`; `python -m unittest tests.test_live_order_lifecycle -v`; `python main.py run-live-order-smoke --help` |
| P271 | Tolerate exchange-normalized stop trigger precision | APPLIED locally / UNKNOWN commit | `research_tools/live_order_smoke.py`, `research_tools/anomaly_micro_live.py`, `research/*` | live safety/exchange-boundary | Treat Binance algo `triggerPrice`/`stopPrice` as exchange-normalized to tick/price precision when verifying protective stops, so a valid rounded price such as `0.035643999999999995 -> 0.03564` does not fail stop verification. Still rejects missing, non-finite, or more-than-one-displayed-price-unit mismatches. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py`; `python -m unittest tests.test_live_order_lifecycle -v`; `python main.py run-live-order-smoke --help` |

| P272 | Preserve initial and active stop ids in smoke summary | PROPOSED | `research_tools/live_order_smoke.py`, `research/*` | live safety/artifacts | Keep the initial protective stop id in `stop_order_id` after a replacement, add `active_stop_order_id` for the currently managed stop, and keep `replacement_stop_order_id` separate so `live_order_smoke_summary.json` no longer overwrites the initial stop with the replacement id. Trading logic and exchange calls are unchanged. | `python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py`; `python -m unittest tests.test_live_order_lifecycle -v`; `python main.py run-live-order-smoke --help` |

## 2026-05-17 — P274 proposed — exchange-side TP1 limit and LTF position monitor

Files:

```text
research_tools/anomaly_micro_live.py
data/exchanges/ccxt_futures_client.py
domain/abstract/exchange_client.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make live position management exchange-side for TP1 and LTF-consistent for structural trailing.
On entry, create and verify a reduce-only TP1 limit sell for half the position after the verified stop exists.
The monitor no longer triggers TP1 from candle high; it only reconciles the TP1 order fill and moves the remaining stop to BE after confirmed TP1 fill.
Structural trail now reads the signal entry timeframe, including 30s/15s/5s aggTrade-derived frames, instead of hardcoded 1m.
Before the first closed post-fill LTF candle, empty OHLCV is recorded as position_monitor_waiting_first_candle, not an integrity error.
Telegram open message is shortened to signal price, signed entry drift, TP/SL with matching decimals, and compact TF/category/session context.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium/high until real-order smoke confirms Binance accepts and exposes the new reduce-only limit TP order through ordinary open orders and that TP fill reconciliation moves the stop to BE without leaving orphan TP/stop orders.
```

## 2026-05-17 — P275 proposed — pin live heartbeat grid as terminal tail

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Fix the terminal live heartbeat lifecycle. The multi-section grid is now a single cached status block: status updates replace the previous grid in place, ordinary logs and red alerts first erase the grid, print the event/error, then repaint the latest grid so the heartbeat is always the last terminal element. The heartbeat no longer starts with a leading blank line, which keeps row counting and clearing aligned for the full multi-section block.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. Console-output lifecycle only. It does not change trading logic, Telegram, exchange orders, artifacts, or signal selection. In non-interactive output, status blocks remain plain periodic logs because in-place terminal repaint is not available.
```

## 2026-05-17 - P276 applied locally - TP1-failure stop cleanup and lifecycle parity tests

Files:

```text
research_tools/anomaly_micro_live.py
tests/test_live_order_lifecycle.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix the live order-flow failure path introduced by exchange-side TP1 limits. If entry and initial stop are verified but TP1 limit creation/verification fails, live now closes the exchange exposure reduce-only and then cancels the already-created initial stop instead of leaving an orphan conditional stop after cleanup.
Update the fake-exchange lifecycle tests to match the current live contract: entry requires verified stop + verified TP1 limit; monitor reconciles TP1 from exchange fill, not candle high; stop/TP1 cleanup is asserted.
```

Validation:

```bash
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Risk:

```text
Low/medium. Patch touches only the TP1-order failure cleanup path after a verified initial stop. Normal signal selection, entry guards, stop math, TP1 math, position sizing, and successful position management are unchanged. The new behavior reduces orphan-order risk after a failed TP1 limit setup.
```

## 2026-05-17 - P277 applied locally - conservative TP1 backtest and pre-context mark-basis fix

Files:

```text
research_tools/anomaly_strategy_backtest.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Reduce optimistic TP1 bias in backtest/live parity by treating TP1 as a conservative exchange-limit proxy: TP1 fills only on candle trade-through (`high > tp1_price`), exact touch is labeled but not filled, and same-candle SL/TP1 conflict remains stop-first.
Fix the anomaly-lab crash where pre-context signal universe construction reintroduced runner profile mark/OI requirements before context enrichment and raised `candidates missing required columns: ['mark_close_vs_decision_close_basis']`.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_anomaly_continuation_lab.py tests/test_live_order_lifecycle.py
```

Risk:

```text
Medium for reported historical metrics. TP1 hit-rate, winrate, and expectancy can drop because old candle-high TP1 fills were optimistic. Signal selection and live order handling are unchanged.
```

## 2026-05-17 - P278 local research memory update - anomaly_lab review

Files:

```text
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Record the detailed anomaly_lab category/session/metric review in compact project memory. This is analysis-only and does not change signal selection, live execution, backtest logic, configs, or tests.
```

Validation:

```text
No code validation required. Reviewed current .output/results/anomaly_lab artifacts and updated research memory only.
```

Risk:

```text
Low. The only risk is overinterpreting a single 30d artifact; the recorded next step explicitly requires strict context_parity_status=ok ablation before threshold changes.
```

## 2026-05-17 - P279 applied locally - live-first category hardening

Files:

```text
research_tools/anomaly_category_contract.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Harden the default live category contract before running real 12 USDT live collection.
runner_reclaim is removed from the default live category set but remains supported for explicit/shadow runs.
shared_pump_category_contract_v1_live_overlay_v6 adds category-specific minimum range expansion, minimum initial risk, prior-whipsaw cap, prior-spike cap, and tighter trade-effort-per-return gates where the anomaly_lab review showed cleaner runners.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Medium. Frequency will drop versus v5 and some valid winners can be filtered. The change is intentional for minimum-size live collection: prefer fewer cleaner trades over broad discovery-like exposure. Do not interpret old discovery/backtest totals as account-return promises.
```

## 2026-05-17 - P280 applied locally - stale startup context tail guard

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Prevent the first live cycles after a slow 72h startup backfill from accepting prior-spike / prior-fast-fade category evidence with a stale trailing context gap.
If the snapshot effective cache end is older than the signal decision by more than symbol_context_snapshot_fresh_ms, live returns symbol_context_snapshot_tail_stale as retryable dependency instead of treating the old 72h snapshot as usable.
The new P279 prior-spike unavailable reason is also retryable, so affected symbols are prioritized by the rolling symbol-context refresher instead of being consumed as final rejects.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Low/medium. Very slow startup can cause early candidates to wait for context refresh instead of trading immediately. This is intentional: stale prior-spike/fade context should block as a retryable dependency, not pass by default.
```

## 2026-05-17 - P281 applied locally - baseline liquidity floor

Files:

```text
research_tools/anomaly_category_contract.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
tests/test_anomaly_continuation_lab.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Prevent tiny baseline liquidity from passing live runner categories only because a small print creates a large relative flow ratio.
shared_pump_category_contract_v1_live_overlay_v7 adds min_baseline_quote_daily_proxy=300k USDT/day proxy to runner categories.
Backtest and live compute the same proxy from baseline_quote_volume_median and setup timeframe.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Low/medium. This can filter some early microcap runners. The threshold is intentionally 300k, not 1m, because the 300k-1m bucket still had positive live-priority expectancy in the current artifact.
```

## 2026-05-18 - P293 applied locally - live context cache delta writes

Files:

```text
data/storage/parquet_storage.py
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Keep mandatory live startup context collection/readiness intact, but remove the wasteful full parquet rewrite from live tail context updates.
```

Implementation:

```text
ParquetStorage now supports per-symbol/timeframe delta parquet files under delta/*.parquet.
load() and load_window_result() transparently merge base data.parquet with delta files and dedupe by timestamp, so downstream code sees complete data.
Live symbol_context_* cache flushes use save_incremental_delta() and emit storage_mode=delta in live_ohlcv_cache_flushed.
symbol_context_startup_backfill_completed now records fetch_phase_seconds, flush_seconds, final_flush_seconds, snapshot_seconds, snapshot_write_seconds, and cache_write_storage_mode.
Regular offline cache builders still call save_incremental() and retain canonical full-merge behavior.
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q data\storage\parquet_storage.py research_tools\anomaly_micro_live.py
inline ParquetStorage smoke: base save_incremental + delta save_incremental_delta + load/load_window timestamp dedupe passed
.venv\Scripts\python.exe -m pytest tests\test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
```

Risk:

```text
Medium. Reads now include delta files, so repeated live restarts can accumulate small delta parquet files until a canonical cache rebuild/compaction is run. This is still safer than skipping startup context; next live must verify startup elapsed_seconds drops and no context readiness degradation appears.
```

## 2026-05-18 - P292 applied locally - live heartbeat/session-top label cleanup

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
```

Intent:

```text
Keep the session-scoped live metrics from P290, but restore the operator heartbeat wording and remove the extra visible session-top suffix from live output.
```

Implementation:

```text
Heartbeat label changed back from "WS сессия" to "Стабильность".
The session metric window label is now empty, so session-top display/artifacts do not print "с начала сессии" while still using the same session baseline internally.
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_micro_live.py
inline smoke: _format_live_heartbeat contains "Стабильность" and does not contain "WS сессия" or "с начала сессии".
```

Risk:

```text
Low. Display/label-only change; data windows, filters, and execution logic are unchanged.
```

## 2026-05-18 - P291 applied locally - live session metric NameError fix

Files:

```text
research_tools/anomaly_micro_live.py
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Fix startup crash introduced by P290 session metrics and make live command errors print on a fresh terminal line.
```

Implementation:

```text
Replaced two accidental _HOUR_MS references with the module-local HOUR_MS constant.
runner.shutdown now finishes any active live status line before cache/source cleanup.
run-anomaly-live now calls runner.shutdown before LiveStartupError and generic exception exits, so the CLI exception logger starts after the status row is closed.
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_micro_live.py cli\commands.py
.venv\Scripts\python.exe -m pytest tests\test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
```

Risk:

```text
Low. This is a constant-name bug fix and terminal cleanup path; trading logic is unchanged.
```

## 2026-05-18 - P290 applied locally - live data-readiness retry and session metrics

Files:

```text
research_tools/anomaly_micro_live.py
research/RESEARCH_STATE.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Prevent live from skipping an otherwise valid LTF decision when setup/entry OHLCV is temporarily empty because cache fill has not caught up.
Make live stability and top-growth display use the current session metric window instead of process-lifetime stability and rolling 6h growth.
Clarify operator data labels so OHLCV REST cache fills are not read as aggTrade/flow degradation.
```

Implementation:

```text
signal_scan_empty_ohlcv now returns retryable_dependency=True with reason ohlcv_data_unavailable and does not consume the decision timestamp as a normal no_signal.
WS health samples are stored with wall-clock timestamps and ws_health_pct is computed from the session metric baseline. Cumulative observed/healthy seconds remain in separate artifact fields.
Session top tracker prunes/baselines from the same session metric start: Asia+Europe from Asia start, Europe from Asia+Europe start, Europe+America from Europe start, America from Europe+America start, America+Asia from America start.
Terminal heartbeat stability value is session-scoped; OHLCV cache fill/gap labels are "OHLCV REST"/"OHLCV gap".
```

Validation:

```bash
.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_micro_live.py
```

Risk:

```text
Low/medium. Trade thresholds and order path are unchanged, but retrying empty OHLCV can let a still-fresh setup be evaluated later instead of being silently skipped. Session-scoped health resets at session metric boundaries by design.
```

## 2026-05-17 - P282 applied locally - live liquidity universe filter

Files:

```text
research_tools/anomaly_micro_live.py
cli/parser.py
cli/commands.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
research/STRATEGY_SPEC.md
```

Intent:

```text
Avoid spending startup context and live scan budget on symbols with too little current market liquidity, while still allowing symbols to enter later if liquidity arrives.
Default live startup and 12h refresh use Binance REST 24h ticker quoteVolume >= 300k USDT for the implicit exchange universe.
Explicit --symbols bypass this filter. Source failures or empty filter results keep the previous universe instead of emptying live trading.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
.venv\Scripts\python.exe main.py run-anomaly-live --help | Select-String -Pattern "live-universe"
```

Risk:

```text
Low/medium. A fresh liquidity arrival can be missed until the next refresh interval. The default 12h interval is a conservative cost/freshness tradeoff; shorten it only if live artifacts show missed symbols that already had enough quoteVolume.
```

## 2026-05-17 - P283 applied locally - live terminal grid repaint

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
Keep the live terminal status grid as one pinned block and make any warning/log line appear above the latest grid.
Python warnings are now routed through _LiveStatusLogger while the live loop is running, so warnings clear/repaint the grid instead of writing into the last grid row via stderr.
Multi-line grid clearing now moves to the top of the rendered block and clears downward, which is safer for wrapped terminal rows.
The pandas synthetic_ohlcv_bucket fill warning source was removed by using nullable boolean conversion before fillna.
```

Validation:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m unittest tests.test_live_order_lifecycle -v
.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py tests\test_anomaly_continuation_lab.py tests\test_live_order_lifecycle.py
```

Risk:

```text
Low. This is terminal display and warning routing only. It does not change signal selection, order placement, or artifacts except fewer stderr warnings in the operator terminal.
```

## 2026-05-19 - P303 proposed - recover external position exits and order UI count

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
```

Intent:

```text
If the exchange position is already flat when the monitor wakes up, try to recover the terminal TP1/stop exchange fill by known order id before writing exit_unresolved. Do not infer PnL from candles or stop price.
Add explicit entry_order_submit_started and entry_fill_verified audit events, because order submit/fill timing was only indirectly visible through position_opened.
Make the terminal heartbeat Orders cell show the cheap local count of verified protective order ids for open positions instead of orphan-order cancel delta.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low/medium. The patch does not change signal selection or entry/exit order placement. It can turn future external-flat exits from unresolved into closed only when the exchange returns a real fill for the known TP1 or stop order. Binance algo stop fill recovery may still stay unresolved if the exchange endpoint does not expose a fill by algo id; that is safer than synthetic PnL.
```

## 2026-05-19 - P305 proposed - strict hot-idle optional work and reject cooldown

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
After P304 immediate danger-flow promotion, keep the hot path clear by skipping top-growth audit, symbol-context snapshots, and normal cache flush while candidate queue/active/open/opening work exists. Cache flush may only run in a limited emergency mode when the buffer is too large.
Add symbol-level reject cooldown for repeated non-retryable weak-flow / mark-basis rejections. Immediate danger-flow scans bypass the cooldown, and a selected signal clears it.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This changes live scheduling, not strategy thresholds. It can reduce repeated scan load, but may delay rechecking a symbol that improves gradually without a fresh immediate danger-flow event. Top-growth/context/cache work becomes more idle-only, so research artifacts may be delayed during sustained hot queues, not lost by design.
```

## 2026-05-19 - P306 proposed - live quality trend windows

Files:

```text
research_tools/anomaly_micro_live.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Make the live operator view stop depending on one jumpy point-in-time metric. Record rolling 1m/5m/15m/run quality windows for WS health, cycle pulse, due-scan latency, queue pressure, and REST/gapREST data status. Show these windows in the terminal heartbeat and append a live_quality_window_summary event every minute, while also adding the same quality_* fields to live_cycle_summary.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Low. This is telemetry/display/artifact-only. It does not change signal selection, scan scheduling, order placement, fills, stops, exits, or guards. The heartbeat becomes longer by two rows, but the existing pinned-status renderer already supports multi-line grids.
```

## P302 live current OI guard no flags refresh - PROPOSED

- Status: PROPOSED / commit UNKNOWN.
- Adds a mandatory live-only current-OI short-cover guard before market entry.
- Blocks real entries when live price is up but Binance current OI is down vs recent 5m OI reference, or when current OI cannot be fetched.
- Keeps the exchange raw endpoint behind `CcxtFuturesClient.fetch_current_open_interest()`; no CLI flags.

## 2026-05-19 - P316 proposed - live2 stream signal adapter

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/market_data/candles.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/artifacts.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Replace the generation-0 `rejected_signal_engine_todo` endpoint with a pure hot-path SignalEngine adapter. The adapter evaluates only already-built live2 stream candles, maps available flow/price/baseline features to the shared pump category contract subset, and explicitly rejects categories requiring unavailable derivative context instead of silently falling back.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This adds the first live2 selected signal verdicts, but it still does not place orders and `new_entries_allowed=false` remains because entry guards and execution are not implemented. The adapter is intentionally conservative and not a full dataframe backtest clone: OI/mark/prior-fast-fade context is rejected as unavailable in live2 generation 0 rather than guessed.
```

## 2026-05-19 - P317 proposed - live2 executable entry guards

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/entry_guard.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/config.py
research_tools/anomaly_live2/runner.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add the live2 executable-entry guard layer after stream signal selection, before any order path exists. Selected signals are rejected if the live stream price shows stale signal age, excessive entry drift, TP1 already touched, or collapsed RR. The guard uses only already-known stream state and performs no network/cache/file IO.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This still does not place real orders and `new_entries_allowed=false` remains mandatory. The guard only makes selected-signal verdicts safer and more auditable before P318 execution. P317 also includes the missing live2 `signal.py` module required by the P316 deadline import so the P311-P317 stack imports cleanly.
```

## 2026-05-19 - P318 proposed - live2 strict execution boundary

Files:

```text
cli/commands.py
research_tools/anomaly_live2/execution.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/artifacts.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Add the first live2 execution boundary after accepted entry guards. The boundary performs real exchange account preflight at startup and pre-entry exchange position checks for accepted selected signals. It rejects non-flat symbols and still blocks order placement as `rejected_execution_order_placement_not_implemented` until the verified-fill plus verified-stop path exists. No candle/ticker/local fallback is used for position state.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This patch introduces exchange reads into the post-guard path and startup preflight, but it deliberately does not place orders. `new_entries_allowed=false` remains because actual order submit, actual fill verification, verified stop placement, and position supervision are not implemented yet.
```

## 2026-05-19 - P316 proposed - live2 stream signal adapter

Files:

```text
research_tools/anomaly_live2/signal.py
research_tools/anomaly_live2/deadline.py
research_tools/anomaly_live2/state.py
research_tools/anomaly_live2/market_data/candles.py
research_tools/anomaly_live2/runner.py
research_tools/anomaly_live2/artifacts.py
research/PATCH_LOG.md
research/RESEARCH_STATE.md
research/EXPERIMENT_LOG.md
```

Intent:

```text
Replace the generation-0 `rejected_signal_engine_todo` endpoint with a pure hot-path SignalEngine adapter. The adapter evaluates only already-built live2 stream candles, maps available flow/price/baseline features to the shared pump category contract subset, and explicitly rejects categories requiring unavailable derivative context instead of silently falling back.
```

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Risk:

```text
Medium. This adds the first live2 selected signal verdicts, but it still does not place orders and `new_entries_allowed=false` remains because entry guards and execution are not implemented. The adapter is intentionally conservative and not a full dataframe backtest clone: OI/mark/prior-fast-fade context is rejected as unavailable in live2 generation 0 rather than guessed.
```
