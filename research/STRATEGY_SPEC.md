## 2026-05-28 - Targeted subminute data contract for runner discovery

For `run-htf-ltf-runner-discovery`, subminute profiles must not require rebuilding the whole universe at 1s. The valid data path is targeted: identify stricter HTF anomaly seeds from closed HTF candles, fetch true aggTrade 1s only from the HTF anomaly start through the maximum entry-confirmation and position-follow horizon, materialize the requested LTF, and then run strict no-gap replay. This is a data-availability step, not a future-label or entry filter. The default seed gate is cost-control and research hygiene, not a proven live category.

Zero-trade subminute candles may be materialized only when aggTrade coverage metadata proves the entire bucket was fetched. They must be marked as zero-trade covered buckets; missing coverage remains a gap/missing-data status.

## 2026-05-27 - Strict HTF/LTF discovery replay requirement

HTF/LTF runner discovery evidence is valid only when the LTF path used for confirmation, future labels and post-entry replay is continuous at the configured LTF step. A trade must not be carried across missing subminute candles. If the stop/trailing stop is hit before a later gap, that closed trade is valid; otherwise a pre-exit gap or incomplete hold window is a skip/missing-data outcome, not a time exit. Flow confirmation must use real `quote_volume` and real `number_of_trades`; synthetic `close * volume` quote-volume is not acceptable for runner/fader conclusions.

## 2026-05-26 - Live2 current-OI baseline contract

Live2 must not use one OI number for three meanings. Current-OI supervision uses distinct baselines:

- `pump_start_current_oi_*`: first valid current-OI snapshot live2 actually saw while the symbol was active/radar. This is the closest honest live approximation of pump-awakening OI; it is not backfilled.
- `signal_current_oi_*`: latest current-OI snapshot present at selected signal time / pre-entry guard.
- `entry_current_oi_*`: current-OI snapshot fetched only after actual fill and verified initial stop.

Management may early-exit after MFE + stalled high + exhausted/seller flow if current OI falls by the fixed supervisor threshold from any of these baselines. Artifacts must expose which baseline was comparable and which reason fired. If a baseline is missing/stale/not-ok, the decision must mark it non-comparable instead of substituting another OI source.

# Anomaly Strategy Spec

Compact current strategy spec for the anomaly-first trading system.

---

## 1. Core idea

The system trades only after detecting an abnormal market wake-up and classifying whether the anomaly is likely organic and still executable.

```text
abnormal activity -> nature/category check -> controlled continuation -> executable entry -> managed exit
```

The first question is the nature of the anomaly. Entry logic is secondary and must not smuggle in future information or visual hindsight.

---

## 2. Required anomaly evidence

A candidate must show enough live-available evidence:

```text
quote_volume expansion
trade_count expansion
price displacement
verticality / directional intent
retention after impulse
limited whipsaw before impulse
acceptable initial risk
```

If real quote-volume or trade-count is unavailable, conclusions about tape/flow/organic behavior are limited and the candidate should be rejected or marked explicitly degraded.

For `bare_htf_short_fader` research, the default closed-HTF anomaly gate is intentionally stricter than the generic flow gate before expensive 1s LTF data is fetched:

```text
start_quote_ratio >= 10
start_trade_ratio >= 8
htf_close_open_return >= 1.5%
```

These are discovery cost-control and signal-quality gates, not proven trading categories. They can be raised for fewer/stronger events or lowered only for narrow forensic runs.

For `run-htf-ltf-runner-discovery` research, the runner label is deliberately separated from the entry rule:

```text
HTF anomaly label: next-hour high >= +10% from HTF close
clean runner label: +10% hit before breaking the HTF anomaly low
entry rule: only closed LTF confirmation known at decision time
execution: next LTF open after confirmation close
exit: structural SL + structural trailing, no TP
```

The tool is meant to learn runner nature: dormancy, smooth pre-pump price/OI growth, sustained quote-volume and real trade-count expansion, and whether structural lows survive. Future labels must not be used as entry filters inside the same replay.

---

## 3. Category / nature checks

The system should separate at least:

```text
organic wake-up
thin-liquidity spike
exhausted blow-off
news/one-print jump
choppy fake-out
late continuation
```

Category labels are research contracts. They must be backed by observable features, not visual preference.

Current live fake-pump context contract:

```text
lookback: 24h
accepted prior fast-fade / fake-pump events: <= 2
active/retryable stale-tail policy: fetch missing levels-TF OHLCV suffix, update cache/context snapshot, then re-check category once
live2 maintenance: REST bootstrap closed 5m 24h context, then roll forward from live aggTrade-derived closed 5m candles
live2 gap policy: aggTrade-id discontinuity inside a real closed 5m candle is diagnostic, not alone a hard context invalidation; stale/missing context still blocks category acceptance
```

Current live/backtest category parity contract:

```text
live-priority prior spike/fade gates use closed 5m candles over the effective 24h context window
missing or insufficient prior 5m context is a category rejection, not zero prior spikes
mark premium/basis is diagnostic until historical tick-level mark replay exists; live mark WS basis must not be a category-only blocker that the backtest cannot replay without lookahead
1m/5s pair-mode quote/trade baselines must come from the same real aggTrade-derived entry cache used for entry flow when that cache is available
event studies should build targeted 1s/5s cache windows around selected/rejected live events and top movers, not a full universal second cache
```

The suffix refresh is data-quality plumbing, not a signal by itself. It only prevents an otherwise active/retryable candidate from expiring because the recent context tail was missing from cache.

Compatibility note:

```text
Some artifact fields and CLI knobs still use legacy names such as prior_fast_fade_count_72h / max_prior_fast_fade_count_72h. For the current live-priority contract, their effective lookback is 24h and artifacts should expose prior_context_lookback_hours=24 where practical.
Backtest/live parity must use the same effective 24h prior-context window when those legacy fields feed live-priority category decisions.
```

---

## 4. Entry logic

Entry must be executable without future information.

Allowed research entry families:

```text
market after confirmed wake-up
break_box_high
pullback_box_fraction
```

Every entry test must preserve:

```text
known signal timestamp
known entry price rule
known stop rule
timeout
fees/slippage assumptions
```

Backtest execution latency contract:

```text
default market entry remains next LTF candle open after the decision (`market_entry_latency_candles=1`)
default anomaly-lab raw simulation may use wide `max_open_positions=1000` to preserve category-discovery material
every anomaly-lab run must also write a final live-like portfolio-filter artifact with `max_open_positions=1`, preserving skipped would-have trades and skip reasons
live-edge conclusions must use the live-filtered artifacts, not the wide raw `anomaly_trades.csv`
same-symbol overlap and global cap are evaluated at actual simulated entry time in both raw simulation and final live filter
default anomaly-lab fill model applies adverse long-entry slippage 0.0005 and adverse long-exit slippage 0.0005 in addition to fees
trade artifacts must expose raw entry/exit prices, slippage-adjusted fill prices, fill model labels, and portfolio state at entry
optional latency mode has one public flag: `--latency true`
for the current live2 1m/5s contract, latency mode compares 0ms versus 5000ms extra executable-price stress by default
latency mode must never fabricate 1s fills; missing 1s cache should be backfilled from real aggTrades during backtest, and failed backfill remains an execution skip
latency-grid artifacts are research stress tests, not a live trading rule by themselves
every anomaly-lab run must write anomaly_backtest_honesty_report.csv; all 28 checklist nodes should be ok/0 failures before treating a run as usable for edge analysis
```

Live execution contract:

```text
signal_entry_price/time != actual_fill_price/time
actual fill must come from exchange order/trade payloads
no candle/ticker-derived synthetic fill for ledger/PnL
live ledger must write scan/guard provenance (`source_scan_mode`, `danger_cold_coverage_source`, `entry_position_guard_source`) from the opened position object
every actionable live2 entry attempt must write artifact timing from bucket close -> signal evaluation -> entry guard -> runtime gate -> execution call, and execution must write exchange-step timing for pre-position fetch, entry order/fill, post-position fetch, stop submit, and stop visibility verification
every post-actionable live2 signal that is not selected must be visible in `live2_near_misses.csv` with blocker stage/reasons and key flow/context fields; this artifact is diagnostic only and must not loosen live entries
live2 post-HTF acceptance long is an enabled trading category: rolling 60s HTF anomaly from 12 closed 5s candles, then exactly 6 closed 5s candles after that rolling HTF close, then entry may be selected only from the post-close 5s decision close
post-HTF acceptance long uses structural SL from the rolling closed HTF anomaly low with a 5 bps buffer; TP1 is 1.5R from signal entry to that structural stop; no fixed percent SL is allowed for this category
startup HTF baseline may be loaded from raw Binance 1m klines with real quote/trade-count fields; full-market 75m 5s aggTrade backfill is not required for live startup
post-HTF acceptance long must reject if OI context is ok, OI is rising over the 3x5m window, and 15m price context is falling
post-HTF acceptance artifacts must include `category_id=post_htf_acceptance_long` plus `post_htf_acceptance_artifact_mode`, source-flow velocity fields, and the post_htf metric set so forward live trades and near-misses can be isolated
no stale signal order after freshness window; default live2 freshness is 5000ms to match the backtest next-5s-candle market-entry model
prescan decisions that arrive after max_signal_age_ms must emit reject_stale_decision_latency, distinct from execution-guard reject_stale_signal
no order if TP1 is already reached or RR collapsed at live price
BE/TP/PnL are computed from actual fill, not signal close
TP1 is a full-position limit at actual_entry + 0.75 * (actual_entry - pump_leg_bottom)
initial SL remains max(pump_leg_bottom - structural buffer, EMA20), so TP1 risk basis and SL risk basis are deliberately separate
pre-fill live2 entry guard uses the same signal TP1/SL basis as backtest: drift <= 0.4%, RR to signal TP1 >= 0.70, TP1 not touched before entry
live2 signal math must use the same forming setup segment as the backtest for quote/trade setup ratios, whipsaw, effort-per-return, taker-share delta, flow_hold, and range/baseline ratios; no 5s-scaled baseline fallback may make a tradable signal before the real 60x1m baseline is available
single-bucket actionability thresholds are the live hot-path candidate gate: a closed bucket must cross quote-volume, trade-count, or absolute-return actionability before the full signal engine runs; weak real-trade buckets remain `market_quiet_non_actionable` diagnostics, not trading candidates. Backtest-parity broad scanning belongs in offline diagnostics/summary artifacts, not the live execution path.
closed-bucket last-trade freshness is a flow-continuation/fade condition, not data availability. If a threshold-actionable bucket's latest trade is stale at decision time, live2 must emit `flow_freshness_reject` rather than `data_not_ready`.
rolling 1m context gaps may be repaired only by official exchange 1m klines with explicit source labels and then the same continuity contract must be rechecked; no synthetic zero-volume candles or silent fallback are allowed.
```

Live scheduling contract:

```text
live2 contract starts as a separate `run-anomaly-live2` runtime, not a flag on live1; generation 0 may keep execution unimplemented, but must expose this as `todo_not_implemented` readiness gates and must keep `new_entries_allowed=false` until market-data, signal, exchange boundary, position supervisor, and execution gates are true
live2 ticker WS is discovery/state context only: it may update one mutable SymbolState per symbol, but it must not create warm/radar queues, perform REST ticker fallback in the hot path, or act as sufficient pump-flow evidence without aggTrade/candle coverage
live2 aggTrade WS is the first allowed hot-path flow source: it may build in-memory 5s/15s/30s/1m candle rings from real trades only, but must not REST-backfill missed buckets, synthesize flat candles, or enable entries before deadline signal and execution gates exist
ended live2 real-trade candles may be finalized by wall clock after the bucket close, without waiting for the next trade; this creates no synthetic candles and preserves idle-gap diagnostics on the next real trade
live2 prior 24h context polling covers the selected universe with active/actionable/open-position symbols prioritized and oldest-polled fairness; stale/missing prior context is a data dependency block, not an ok context and not a strategy reject
live2 prior 24h context is maintained by startup REST closed-5m bootstrap plus live aggTrade WS closed-5m rolling append; repeated full-window REST polling is only a repair path for missing/non-ok context, not the normal freshness mechanism
live2 prior-context rolling append may tolerate minor intra-5m aggTrade id gaps up to max(5 missing ids, 10% of observed trades); tolerated and rejected gaps must be exposed in status and symbol-state artifacts, and gaps above tolerance must block context as `ws_gap_exceeds_tolerance`
live2 category features must stay in the same units as the shared backtest contract: baseline quote liquidity uses a daily proxy from live setup pace, prior-whipsaw is compared to a live forming-setup range rather than a single 5s micro range, and signal stop/risk uses the forming setup low available at decision time
live2 shared-category evaluation must first build a backtest-like forming setup for the active 1m/5s contract: cumulative 5s entry candles inside the current 1m setup, default 4 closed 5s confirmation candles, quote/trade pace ratios versus setup baseline, and forming setup high-low for range/risk/prior-whipsaw. If live uses a temporary scaled 5s baseline before enough closed 1m baseline exists, artifacts must label that baseline source explicitly.
live2 must not add a stricter "last 5s candle must be green" requirement unless the backtest adds the same rule. Price confirmation is setup-level: price_retention, verticality and hold_count over the forming setup segment.
CLI, command, and runtime config defaults must stay aligned for prior_context_stale_ms, prior_context_symbol_cooldown_seconds, and prior_context_max_symbols_per_cycle because launched live is the source of truth, not dataclass defaults alone
live2 full-rewrite audit artifacts (status, diagnostics summary, symbol state) must be atomic temp-file replacements; a transient empty/truncated audit file is an observability failure
selected hot symbol -> scan all due configured TF sets -> then move to next symbol
noticed radar symbols have a fixed hot-lane before warm bulk/cold coverage; active/opening symbols remain protected first
symbols that already exceed the 24h prior fake-pump / fast-fade threshold are quarantined before warm/radar hot-lane promotion until enough excess fast-fade timestamps age out of the 24h window; they remain visible to ticker/top-growth audit
warm watch is a small bounded score-ranked queue, not an unbounded holding pen; low-rank overflow must be emitted as candidate_dropped_latency_pressure
top-score warm symbols may promote into the hot lane even while optional work is SLA-gated, and this must be explicit in artifacts
waiting hot symbols may prefetch due subminute entry gap debt, but only with explicit hot_waiting_prefetch_* artifacts/reasons
after the critical scan/order path, top waiting immediate-danger or high-score radar/warm symbols may run bounded priority aggTrade prefetch even when the queue is not idle; selected signals and open/opening positions still take precedence
cold universe discovery is explicit DANGER budget, not implicit heavy work
ticker-radar promotion can add watch symbols but cannot itself open trades
in subminute WS-live, cold coverage is an adaptive idle/audit scanner: it is hard-off for open/opening positions and active-due symbols, soft-reduced by active-waiting symbols, and scaled by WS health, scheduler heartbeat EWMA, and REST/cache pressure
inactive_scan_slots_per_cycle=0 means active/radar-only scan and must be visible in artifacts
live artifacts must expose class-specific candidate latency for active, immediate-danger, ticker-radar, and warm-watch queues; precise scan summaries must include origin first_seen/promote lag so <=5s claims are directly auditable
operator heartbeat must report scheduler timing plus ticker/aggTrade health and cold coverage score/gate reason, not ambiguous batch/full-cycle timing
operator heartbeat must expose potential-anomaly processing delay from live latency SLA samples (`Задержка p95`, `max`, radar/warm queue count) so missed entries can be tied to scheduler delay instead of guessed after the fact
```

Live data-access contract:

```text
read local parquet first
fetch only missing OHLCV / aggTrade-derived ranges
subminute missing ranges must fetch through final candle end, not only final candle open
decision frames must include closed candles only
write fetched live rows with provenance/version
live parquet writes may be buffered, but buffered/flushed/failed counts must be visible
live startup/reprepare context cache tail writes and normal live OHLCV flushes may use delta parquet files if all ParquetStorage reads merge base+delta transparently and preserve timestamp dedupe
mandatory startup/reprepare context backfill may perform bounded catch-up passes to close freshness lag accumulated during a long all-symbol pass; this must not skip required symbols/timeframes
remaining cache gaps must emit artifacts
tiny open-tail aggTrade gaps may be ignored in prefetch-to-now only when they are below the explicit tiny-tail threshold and are reported as tail_gap_ignored; actual closed decision-frame reads remain strict
cache gaps are not valid zero-signal evidence
empty setup/entry OHLCV in live is a retryable dependency, not a normal no_signal that may consume the LTF decision before cache fill catches up
WS ticker radar required for subminute live must be healthy before startup continues
WS aggTrade missing coverage must be explicit; unbounded REST backfill is not a default live path
OI and prior-context category dependencies must use stale-aware effective statuses. Raw ok context older than its configured stale window is a data dependency miss, not acceptable signal evidence.
```

Live operator/session metric contract:

```text
WS stability shown in heartbeat/artifacts is scoped to the current session metric window, not process lifetime.
Session top-growth baseline uses the same metric window, not a rolling 6h window.
Asia+Europe starts at Asia start; Europe starts at Asia+Europe start; Europe+America starts at Europe start; America starts at Europe+America start; America+Asia starts at America start.
OHLCV REST/cache-fill labels must not be interpreted as aggTrade flow degradation.
```

Diagnostics/parity contract:

```text
top-growth missed-pump audit must preserve concrete precise-stage reject events/reasons before using generic no-actionable-signal labels
backtest context parity must split discovery_only, live_priority_pass, live_priority_reject_reason, and strict_live_replay_enter so discovery trades are not confused with honest live-priority entries
```



---

## 4A. Timeframe contract

The strategy uses separate setup and execution timeframes.

```text
setup_timeframe / HTF:
- dormancy baseline;
- abnormal quote-volume and trade-count expansion;
- price expansion and retention at setup level;
- exhaustion / category checks;
- initial risk box context.

entry_timeframe / LTF:
- does not re-prove the whole pump thesis;
- confirms the setup is still alive and executable;
- checks activation hold, path/verticality, freshness, drift/RR and live price guards;
- supplies the executable decision timestamp for market-entry proxy and live order guards.
```

Live may create a setup before the HTF candle closes by aggregating already closed LTF candles inside the current HTF bucket. This must be marked as `setup_source=forming_htf_from_entry_tf` with `setup_elapsed_fraction` and `setup_closed_entry_candles`. Backtest parity mode must use the same contract and write `feature_contract=htf_setup_ltf_entry_v1`.

For forming HTF candidates, `setup_available_timestamp_ms` is the LTF decision candle availability timestamp, not the full HTF candle close. The full HTF close may be recorded separately as `setup_full_available_timestamp_ms` for audit only. This prevents both directions of self-deception: using unseen HTF data is forbidden, and rejecting an otherwise available forming setup because the full HTF candle has not closed is also not live/backtest parity.

---

## 4B. Proposed short/fader research contract

Short/fader research must start from bare closed HTF anomalies, not from long-selected categories. Long continuation categories are not valid short categories.

```text
event:
- closed HTF anomaly candle N with real quote-volume/trade-count expansion and price displacement;
- no short entry decision before HTF N is closed;
- post-anomaly analysis window: first 60 minutes after HTF close;
- LTF tape before and after HTF close may be used as context, but the executable trigger must occur after HTF close.
```

The current best short hypothesis is not "short every anomaly". It is:

```text
prior crowding/fade context
+ post-close LTF short pressure
+ still-executable structural stop
+ full RR2.0-2.5 target
```

Candidate context filters:

```text
prefer:
- prior_fast_fade_count / prior_spike_count elevated over the recent context window;
- prior_spike_count_72h >= 10 or prior_fast_fade_count_72h >= 3 as research starting points;
- weaker first 12x5s after HTF close, especially ltf12_ret < 0;
- real trade-count and quote-volume sources, not proxy flow.

avoid:
- converting discovery/runner long buckets into short buckets;
- OI/mark-only fader rules without LTF price/tape pressure;
- entries where the HTF anomaly is still printing clean continuation higher and no failure is visible.
```

Executable LTF triggers to validate first:

```text
failed_new_high:
- after HTF close, price probes above the HTF high or local post-close high;
- it fails to hold and closes back below the relevant high;
- red/weak close confirms rejection.

taker_fade_red:
- recent 4x5s window shows red pressure;
- taker-buy share is weak versus recent flow;
- close is below HTF close or below the post-close midline.
```

Initial short risk/exit contract:

```text
entry: next LTF open after the trigger candle, with adverse slippage and fees;
stop: structural stop above max(HTF high, trigger/rejection high) plus small range buffer;
target: full-position fixed RR2.0-2.5, with RR2.5 the current best research anchor;
max hold: within the post-anomaly hour unless separately proven;
do not default to partial close, BE, or trailing SL: current focused checks show they dilute the few large winners.
```

Acceptance criteria before live:

```text
validate on a larger period/universe;
read only cap-1/live-filtered results for tradability;
require enough trades to judge top dependency;
report top5/top15 contribution, per-symbol/month/session distribution, and skip/reject funnel;
no live shorting until this is implemented as a separate fader contract with honest artifacts.
```

Implementation note:

```text
Local mode `--pair-collection-mode bare_htf_short_fader` implements this as research-only feature_contract=bare_htf_short_fader_v1. By default it is a wide discovery artifact mode: post-close short-pressure triggers are included broadly, prior crowding/fade is annotation rather than a required gate, and RR exit grid is disabled unless explicitly requested. Default discovery triggers are failed_new_high, taker_fade_red, close_below_htf_close, close_below_post_mid, lower_high_close_down, effort_no_progress, and pullback_without_recovery. It writes `bare_htf_short_*` artifacts, compact post-close LTF path slices, and heuristic decay-category research labels. It uses adverse short-side slippage, structural stop, fixed RR target, max hold, and cap-1 live filter. It does not enable live shorts.
```

---

## 5. Exit logic

Current research exit families:

```text
default live-ready exit:
- TP1 basis: pump_leg_bottom / decision_box_low
- TP1 multiple: 0.75R from that basis
- TP1 size: 100% of position
- no runner by default
- backtest execution path includes the entry candle and applies stop-first handling on same-candle stop/TP ambiguity
- delayed market-entry proxy rejects if TP1 was reached before the delayed fill
```

```text
structural_trail
ema20_close
ema20_negative_pnl_be_escape
```

Exit evaluation must report average trade, winrate, tail dependence, monthly distribution and dependence on top outliers before claiming edge.

---

## 6. Data-quality contract

No silent fallback for core evidence:

```text
quote_volume missing != close * volume substitute
number_of_trades missing != trades/trade_count alias substitute
open_interest missing/stale != neutral context
empty cache window != valid zero-signal result
```

Unknown or degraded source must become an explicit status/reason.

---

## 7. Current invalid assumptions

Do not assume:

```text
anomaly means continuation
large candle means organic demand
higher future high means executable edge
visual setup means live-available setup
single run means stable edge
```

The research objective is to break weak anomaly categories cheaply before optimizing parameters.


---

## Live executable-entry contract

A selected live signal is not automatically tradable. Before any market order, live must reject the signal when:

```text
signal age after candle close > max_signal_age_ms
live price is invalid
live price has already reached signal TP1
actual live risk is invalid or too wide
absolute live-price drift from signal entry > max_entry_price_drift_pct; default 0.004 = 0.4%
RR from live price to signal TP1 < min_executable_rr_to_signal_tp1
```

These rejects are trading decisions and must be visible in artifacts. Telegram may notify the operator, but artifact rows remain the source of truth.

---

## Live position-management contract

After entry fill, live must not assume the position is safely managed unless exchange state confirms it:

```text
initial stop id visible in open orders
stop side/type/reduceOnly/amount/stopPrice verified
TP1 partial exit fill resolved from exchange order/trade payload
position monitor OHLCV available repeatedly enough to manage BE/trail
integrity errors written as artifacts and surfaced to Telegram
```

If stop/fill/monitor state is unknown, the run must emit an explicit artifact reason. It must not count synthetic TP1/BE/PnL as reliable edge evidence.

Backtest market entries remain a proxy, not real exchange fills. Artifacts must label the execution model and export skipped-entry reasons, especially stale/non-executable/price-drift/RR-collapse reasons.


---

## 1h overhead-level context diagnostic

Overhead levels are context, not an entry trigger. They may support a long-continuation hypothesis only when they are above current price and still leave enough target room.

A valid 1h overhead level must satisfy:

```text
at least 3 valid high-based touches in the same price band
each counted touch is at least 6h after the previous counted touch
each valid touch has a meaningful bounce after touch
a source pivot/high candle has no close above its high in the previous 12h
level is above current price
level is not pierced by later wick/high
level is not a held broken level
symbol/level context is not a clear downtrend pseudo-resistance
```

The diagnostic metric records:

```text
distance_pct
valid_touch_count
max_reaction_pct
median_valid_reaction_pct
recent_move_pct
reaction_to_recent_move_ratio
context = bullish_target / danger_ceiling / overhead_level
```

A touch is valid only when the candle high is near the level while the candle body remains below the touch band. A candle is not allowed to seed a level when any close in the previous 12h is above that candle high. Body/interior range intersections and later wick/high pierces are rejected instead of being rescued by a later bounce.


---

## Live discovery latency contract

Live may skip expensive signal construction for decisions that are already older than `max_signal_age_ms`, but the skip is still an audit decision and must be written to artifacts, e.g. `reject_stale_signal` with a prescan stage.

Top-growth snapshots are research/audit data, not trading signals. They should run through an explicit standalone command so they do not compete with live discovery and execution guards for API budget.

---

## Live ticker-radar scheduling contract

Ticker radar is allowed only as a scheduling priority layer:

```text
ticker snapshot delta -> bounded watch promotion -> normal closed-kline deep scan -> normal signal/risk/execution guards
```

It must not:

```text
open a trade
replace quote_volume / number_of_trades evidence from closed klines
prune or permanently skip cold-universe symbols
hide missing ticker fields behind proxy volume
consume normal round-robin inactive slots without explicit operator configuration
```

Ticker-derived rows are diagnostic/scheduler artifacts only. Final signal validity still depends on the existing closed-kline flow, category, risk and execution checks.

Cold coverage must remain visibly labeled as DANGER. It is useful for parity/audit discovery and radar-quality measurement, not as a proven production edge. If cold coverage increases `scheduler_cycle_seconds`, `signal_scan_seconds`, REST aggTrade calls/fetched span, or pending WS/cache gaps, it must self-throttle or switch off before active/radar execution quality is harmed.

---

## Trade chart review artifact

Trade screenshots are diagnostic artifacts, not trading signals. The canonical trade chart should show:

```text
top: LTF trade-window candles with entry/exit/risk/TP annotations
second: HTF candles for the same local trade window
third: normalized quote-volume and number_of_trades line curves
bottom: independent 1h context covering the last 4 days through the candle that contains the main chart end
```

The 1h context panel may draw strict overhead levels from the hourly-level diagnostic, but those levels must be discovered only from the same displayed 4-day 1h window. They must be unlabeled chart context only and must not be treated as an entry/exit rule unless a separate strategy patch wires them into the decision path and updates backtest/live parity.

The lower flow panel is shape/timing context: quote volume and trade count are each normalized to 0-100 within the displayed trade window and are not comparable as absolute magnitudes.

Live open/close Telegram charts use the same four-panel renderer after verified fill/stop creation. Because the trade is not closed yet, open charts must not draw completed risk/reward rectangles; they show TP1 and SL as horizontal levels instead. Telegram is operator UI only: `live_events.csv` and live position state remain the audit source of truth.

---

## Telegram operator-message style

Telegram is operator UI only; audit truth remains in `live_events.csv`, ledger files, exchange state, and chart artifacts.

Style contract:

```text
symbol-specific messages use one deterministic animal emoji per compact symbol
service/startup/pause messages use 🚧
service/error messages use ⚠️
error payloads and integrity reasons are rendered as Telegram code
TF/context lines are rendered as Telegram code
```

The symbol emoji must be algorithmic, not per-symbol hardcoded and not Python `hash()` based. It is derived from a stable digest of the compact symbol string so the same symbol maps to the same animal across runs.

---

## Live audit failure visibility

Live audit truth must not depend on console output or Telegram delivery. Important operator-path failures and fallbacks must be visible in `live_events.csv`, including:

```text
network_degraded / network_recovered
telegram_async_send_failed
telegram_sync_send_failed
telegram_photo_send_failed
telegram_open_chart_failed / telegram_open_chart_missing_id / telegram_open_text_fallback / telegram_open_text_missing_id
telegram_close_photo_sent / telegram_close_photo_failed / telegram_close_photo_missing_id / telegram_close_text_fallback
unprotected_entry_position_read_failed / unprotected_entry_position_amount_invalid / unprotected_entry_no_exchange_exposure
unprotected_entry_reduce_only_exit_failed / unprotected_entry_reduce_only_exit_filled
reject_stop_cooldown
```

These events are diagnostic/audit artifacts only. They must not change signal selection, entry execution, stop placement, TP/SL math, or position monitoring.

---

## HTF/LTF live-entry contract

The current live/backtest contract is:

```text
HTF/forming HTF = setup quality
LTF = entry permission
exchange fill = risk/PnL source of truth
```

Intended anomaly TF sets are:

```text
5m/30s
1m/15s
1m/5s
```

The anomaly-lab CLI default must run all three intended TF sets when no timeframe flags are provided. Single-pair runs are explicit override/debug mode only, via `--timeframe`, `--setup-timeframe`, or `--entry-timeframe`.

Default multi-TF backtests should collect setup candidates in a symbol-major pass: for a selected symbol, evaluate all intended TF sets before moving to the next symbol. Per-pair artifacts stay separated, but symbol/frame reads should not be repeated once per TF set.

For a pair such as `5m/30s`, live does not wait for the 5m candle to close. It aggregates closed 30s buckets inside the current 5m bucket and evaluates setup flow on the forming HTF candle. Forming HTF flow is judged by pace-normalized quote-volume and trade-count ratios against the closed HTF baseline, plus a raw-progress floor so very small early bursts are not treated as a real wake-up.

LTF confirmation should not re-prove the whole pump thesis. It confirms that the entry is still executable: activation hold, path/verticality, freshness, price drift, TP already reached, RR collapse and actual exchange fillability.

TP1 is a management milestone, not proof that market room exists. The default TP1 target is the nearest higher round market number above the old 1R target. The round step is derived from current price and movement size so the level is psychologically/operationally cleaner without jumping to an unrelated far-away target.

Optional red-flag profiles are research/backtest filters only until proven on longer data. They may use only pre-decision/live-available fields such as mark basis, OI interaction, context freshness, taker-buy share delta and effort-per-return. They must not use `future_*`, `outcome_label`, `exit_reason`, `tp1_hit`, MFE/MAE or realized return fields.

Runner-oriented research should prioritize early features available at decision time:

```text
mark basis versus decision close
mark/contract context momentum before decision
quote/trade effort per unit of price displacement
hold ratio / retention shape inside the LTF confirmation segment
recent same-symbol spike density and time since prior spike
```

Recent prior spikes are not automatically invalid. They can indicate an active theme. The red flag to test is serial failed or overcrowded wake-ups, not any prior attention.

Runner category profiles are research filters, not final production rules:

```text
runner_balanced = positive mark basis >= 10bp + capped quote/trade extremeness + capped effort per return + capped start taker-buy share delta + no prior mature fast-fade in 72h
runner_reclaim = runner_balanced + reclaim-like candle shape with lower wick present and upper wick not excessive
runner_flow = runner_balanced + at least one confirmation candle with flow hold
runner_oi_confirmed = runner_balanced + mark basis >= 30bp + OI 3x5m change > 0.3%
```

`runner_oi_confirmed` is the current strongest live-entry category candidate from the partial 30d artifacts. Live can enforce the OI and mark-basis parts directly; the prior mature fast-fade rule remains fully proven in backtest artifacts only until live has persisted enough same-symbol outcome history.

These profiles may be used in backtest and shadow-live diagnostics. They should not be enabled for real orders until validated on complete executable coverage and live shadow/replay parity.

Live entry-lag diagnostics:

```text
first_executable_entry_timestamp_ms = decision_timestamp_ms + entry_tf_ms
entry_lag_ms = live_fill_or_check_timestamp_ms - first_executable_entry_timestamp_ms
entry_lag_ltf_candles = floor(entry_lag_ms / entry_tf_ms)
entered_late_vs_first_executable = entry_lag_ltf_candles >= 1
```

Lag diagnostics are not trade filters by themselves. They are used to decide whether live missed the early anomaly execution window or whether a signal was still executable after drift/RR checks.

Live scheduler scan-gap diagnostics:

```text
previous_live_scan_closed_timestamp_ms = last closed LTF candle this symbol/TF was scanned through
first_unscanned_decision_timestamp_ms = previous_live_scan_closed_timestamp_ms + entry_tf_ms
live_scan_gap_ltf_candles = max(0, (current_signal_decision_timestamp_ms - previous_live_scan_closed_timestamp_ms) / entry_tf_ms - 1)
```

This measures the N -> N+5 scheduler problem directly. It does not claim that every skipped candle had a valid trade; it shows how many closed LTF decisions live did not evaluate before the current detected signal.

When `live_scan_gap_ltf_candles > 0`, live may emit `missed_entry_replay_probe` from a background thread. The probe replays skipped LTF decision candles from the already loaded OHLCV window and reports the first skipped candle where the same live filter would have selected a signal. It must not block order placement. The probe starts from the earliest skipped candle and reports `probe_truncated=true` if `signal_scan_backfill_candles` prevented checking the full skipped interval.

Live scan budgeting:

```text
ticker_radar = cheap all-symbol wake-up from exchange tickers
precise scan = expensive active/radar scan that may fetch subminute aggTrade tails
max_precise_scan_symbols_per_cycle = optional cap for active + ticker-radar precise scans
```

The precise cap must never drop active symbols. If active symbols use the whole budget, ticker-radar watch symbols wait and must be visible in artifacts.

Live default universe excludes a static list of obvious high-cap majors because the anomaly strategy targets early runner potential, not large-cap continuation. This filter is intentionally not a market-cap oracle and must be logged in artifacts. Explicit `--symbols` bypass the filter.

Live cache and subminute data policy:

```text
Subminute OHLCV is derived from Binance aggTrades.
Cycle-local aggTrade raw cache may reuse overlapping raw time ranges, but missing intervals must still be fetched or reported as cache gaps.
Live may also reuse explicit REST aggTrade backfill ranges from short-lived process memory and may coalesce/pad nearby missing ranges to reduce request count.
This cache is a data-access optimization only; it must not convert uncovered windows into zero-signal evidence.
Live OHLCV cache writes may be deferred for speed; graceful shutdown/error paths force a full flush.
```

Deferred cache writes must not be interpreted as missing data if in-memory frames already cover the current decision window. Persistent cache loss after a hard kill is a speed/cache durability issue, not a trading-decision fallback.

Reactive live migration rule:

```text
Data ingestion and scheduling may become event-driven.
Anomaly decision logic must stay synchronous over explicit OHLCV/context windows until shadow parity proves otherwise.
```

Ticker radar ingestion is abstracted behind `LiveTickerSnapshotSource`. The current source is REST `fetch_tickers`; future WS ticker sources must publish the same normalized `ExchangeTickerSnapshot` contract and source id in diagnostics.

Live scheduler selection is represented by `LiveSymbolBatchSelection`. Every selected symbol must have a scan reason such as `precise_active`, `precise_ticker_radar`, or `inactive_deferred_subminute`. Future reactive schedulers may change selection priority, but must keep explicit waiting/deferred/drop diagnostics.

Current live ticker radar source defaults to Binance USD-M futures `!ticker@arr` WebSocket. When this source is enabled, REST ticker fallback is forbidden; stale/not-ready stream state must pause radar promotions and be visible as `ticker_radar_failed`.

Current live subminute tape source defaults to Binance USD-M futures `@aggTrade` WebSocket for active and ticker-radar watch symbols. REST `aggTrades` is allowed only as explicit missing-range backfill when WS rows do not honestly cover the requested interval or an aggregate trade id gap is detected. Those backfills must be visible in `ws_aggtrade_frame_read`; they are not a silent fallback and should be treated as a data-health signal.

For live WS aggTrade coverage, a connected active subscription is treated as covering quiet no-trade intervals; the source does not require a trade at both edges of every requested window. Pre-subscription history and detected aggregate trade id gaps remain uncovered until explicit backfill covers them.

Backtest/live parity rule:

```text
For forming HTF from LTF entry sets, backtest candidate collection must evaluate every closed LTF decision candle inside the active HTF setup after the confirmation minimum.
The first raw pump-flow candidate must not consume the whole setup before OI, mark-basis, category, and execution filters run.
runner_oi_confirmed requires fresh OI and fresh derivatives/mark context as part of the profile; accepting stale context is not an allowed optional mode for this production-style category.
```

Backtest pump-category overlay:

```text
The broad backtest pass is the discovery layer. It should not be confused with a production entry category.
Each broad signal/trade is tagged with the strongest profile it also satisfies: runner_oi_confirmed, runner_flow, runner_reclaim, runner_balanced, or discovery.
Only runner_oi_confirmed is currently the live-entry candidate category. Other tags are research buckets until they show stable, non-overfit edge across enough trades and periods.
```

Live category policy after P279:

```text
Test live may scan runner_oi_confirmed, runner_flow, and runner_balanced by default.
runner_reclaim remains a supported explicit/shadow category, but is removed from the default live set because the current 30d anomaly_lab artifact showed near-zero average trade, weak session stability, and negative US-session behavior.
Discovery is never a live-entry category.
Category priority is TF-specific:
- 5m/30s: runner_flow, runner_oi_confirmed, runner_balanced
- 1m/15s: runner_oi_confirmed, runner_flow, runner_balanced
- 1m/5s: runner_oi_confirmed, runner_flow, runner_balanced
Live category contract is shared_pump_category_contract_v1_live_overlay_v6: live/backtest both require category-specific mark-basis, minimum range expansion, minimum initial risk, prior-whipsaw cap, prior-spike cap, and prior-fast-fade exclusion where configured. If prior-spike/fast-fade, mark, or OI context is temporarily unavailable/stale, the decision is not consumed; live retries until the decision becomes stale or receives a final reject/selected category. Discovery remains backtest-only and is never a live-entry category.

After P407, live2 runner categories no longer use calendar-minute setup windows. `runner_oi_confirmed`, `runner_flow`, and `runner_balanced` evaluate a trailing contiguous rolling 60s setup built from the last 12 closed 5s candles ending at the decision candle. Baseline remains closed 1m history ending before the rolling setup window. Artifacts must expose `live_setup_alignment=rolling_60s_5s_step` and `live_setup_calendar_aligned=false`.

After P412, the same runner categories must also pass a rolling runner-shape gate before entry. The 12 closed 5s setup is split into first 30s and second 30s; quote volume, `number_of_trades`, and range must be expanded versus baseline, the second half must accelerate versus the first half, second-half return must be non-negative, and top1 quote-volume share must not dominate the setup. Live2 near-miss artifacts expose `live_setup_runner_shape_*`; backtest candidate rows expose matching `runner_shape_*` fields and category profiles apply the same thresholds.

DANGER cold coverage policy: subminute ticker-radar live has a small default precise inactive-symbol budget of 5 symbols per cycle, labeled DANGER in code and artifacts, but it is idle/health gated. It may run only when cumulative WS health is strictly above 95% and there are no active symbols, no opening position, and no open position. When gated off, artifacts must show inactive_cold_coverage_gate_reason and no full-universe cold-cycle estimate should be displayed as active coverage. This improves parity/audit coverage but increases API/WS aggTrade pressure and is not a proven production edge. Set inactive_scan_slots_per_cycle=0 to disable cold coverage completely.
```

### DANGER live discovery instrumentation after P210

```text
DANGER local entry-position guard may skip the pre-entry exchange position fetch and rely on in-process open/opening symbol state. This is allowed only with startup exchange-position cleanup and post-fill exchange position verification. It is not compatible with multiple simultaneous live processes or manual external positions on the same account.

DANGER cheap flow radar may use Binance all-ticker quote-volume and trade-count deltas as a scheduler watch signal only. It is not a trading signal and cannot replace kline/aggTrade evidence inside the confirmed runner category contract.

Wider rolling micro-cache means a longer WS aggTrade buffer for active/radar/watch/current cold symbols. It must not subscribe to the full universe, and missing pre-subscription history remains explicit coverage debt/backfill/pending state.

Cold coverage value must be judged from artifacts: cold_scanned_symbols, cold_evaluated_timeframe_count, cold_retryable_dependency_count, cold_signal_count, cold_order_attempt_count, scheduler latency, REST/cache pressure, and whether cold found symbols before ticker radar.
```

### Warm-watch scheduler gate after P213

```text
Cheap ticker/flow radar is scheduler input only. By default it must not push a symbol directly into expensive precise scan.

Radar candidate -> warm-watch TTL -> micro-cache target -> precise radar watch only after repeated qualifying observations.

Warm-watch promotion requires:
- quote-volume delta still rising;
- flow-radar candidates keep positive trade-count delta;
- price delta stays inside the configured non-fade/non-chase window;
- artifacts expose marked/updated/promoted/rejected/expired states.

Warm-watch symbols may be subscribed in the bounded WS aggTrade micro-cache target set, but there is still no full-universe micro-tape subscription and no trade entry from cheap radar alone.
```

### Live discovery loosen policy after P244

```text
Live discovery sensitivity may be increased only on market-shape/category filters, not on execution safety. P244 lowers/loosens early-flow and shape gates to reduce false negatives while preserving stale-signal, live-price-drift, TP1-already-reached, RR, actual-risk, order/fill, and position-integrity guards.

Missing or invalid context for taker-buy, mark basis, OI, or prior-fast-fade is not evidence that the market failed a filter. It is a retryable data dependency. The decision must not be consumed until the dependency resolves, receives a real final market reject, produces a selected category, or expires by stale guard.
```

---

## 7. Shared runner category contract

```text
Live-priority runner categories are a shared contract, not separate live/backtest constants.
Backtest selection order: try shared live-priority categories in the same timeframe-specific priority order as live; if none match and the base discovery signal still matches, enter as discovery.
Every backtest trade must carry pump_category_id, pump_category_family, pump_category_is_live_rule, pump_category_contract and pump_category_source so live-rule trades and discovery fallback trades can be separated without inference.
Synthetic live OHLCV buckets are explicit data provenance. They may preserve elapsed no-trade time, but they are not real flow/hold evidence.
P279 v6 contract is intentionally live-first: it reduces frequency to favor positive mark-basis, meaningful impulse range, non-tiny initial risk, lower prior whipsaw/spike history, and less poor trade-effort-per-return. P281 v7 adds a minimum absolute baseline liquidity floor of 300k USDT/day proxy so tiny symbols cannot qualify only through inflated relative flow ratios. It should be judged on live statistics and strict-parity backtests, not on blended discovery PnL.
```

### Live universe liquidity gate after P282

```text
The default live exchange universe is prefiltered before startup context/scanning by Binance REST 24h ticker quoteVolume >= 300k USDT.
The filter refreshes every 12h by default so symbols can enter later when current liquidity appears.
Explicit --symbols bypass this universe gate.
If the ticker source fails or the filter would produce an empty universe, live keeps the previous universe and writes a live_symbol_universe_liquidity_filter artifact event.
This is only a universe-cost/data-quality gate. The trade decision still needs the shared anomaly category contract, including P281 min_baseline_quote_daily_proxy from closed-kline baseline data.
```

### Legacy live1 position management after P274

```text
TP1 in legacy live1 is an exchange-side reduce-only limit sell for the planned partial size. Live must not infer a TP1 hit from candle high and then submit a market close at a later price.
After confirmed TP1 fill, the remaining protective stop moves to breakeven and structural trailing uses closed candles from the signal entry timeframe, not a hardcoded 1m frame.
Before the first closed post-fill entry-timeframe candle exists, the monitor is waiting for structural context; this is not an OHLCV integrity error while the exchange stop and TP1 orders are already placed.
```

### Live2 position management after P408

```text
Live2 default position size is 12 USDT notional. TP1 is still active, but TP1 closes 50% of the exchange position by default, not 100%.
Live2 TP1 close uses an exchange reduce-only close with verified actual fill; it must not infer a close from candle high alone.
After partial TP1, live2 must submit and verify a replacement stop for the remaining exchange amount before accepting the lifecycle transition. Only then may it cancel and verify removal of the old initial stop.
The remaining 50% stays supervised as `tp1_partial_protected_stop_verified`: early-exit logic can still full-close it, and structural trailing may replace the stop using closed post-fill 5s structure.
If replacement stop creation/visibility, old-stop cancellation, or post-close position state is inconsistent, live2 emits a strict position-integrity error and blocks further entries.
Each protected position stores 5m OI context, current-OI baselines, and selected source-flow velocity so later OI/flow exits can compare current state to the anomaly/signal/entry context. Current OI must come from Binance `/fapi/v1/openInterest` and stay separate from 5m history fields.
After P413, live2 has no default portfolio-level position cap: `execution_max_open_positions=0` means unlimited protected positions. Per-symbol duplicate protection remains mandatory. A positive `--execution-max-open-positions` may be used to restore a hard local cap.
```

### Live2 post-entry early exit after P408

```text
Live2 may full-close a protected long before TP1 only after the entry fill and initial stop have already been verified. The early-exit decision uses only closed 5s candles whose open time is after the exchange entry fill timestamp, so it cannot use the entry candle or pre-fill tape as post-entry evidence.

Default minimum hold is 6 closed 5s candles. The early exit is meant for position management, not entry proof: it can close when buyer flow has clearly faded, seller pressure appears after some MFE, OI rises while price stops progressing, current OI falls from pump-start/signal/entry current-OI baselines while flow is exhausted after MFE, or the position stalls without progress long enough to make the original pump-flow thesis stale.

The close must be a reduce-only exchange close with verified fill, verified flat exchange position, and verified cancellation/removal of the old initial stop. Artifacts must expose `position_early_exit_full_close_verified` and the trigger reason. Telegram remains operator UI only; artifacts remain source of truth.

Structural trailing is allowed only for the post-TP1 remainder and uses recent closed post-fill 5s lows; it must replace and verify the stop through the exchange before mutating local protected-position state.

This rule must not become a fee-churning micro-scalper. It is a conservative exit/trail from stale flow, not a repeated partial-close system.

For exchange-triggered stop exits, live2 must not invent a candle/ticker fill. When the exchange position is flat and the protected stop is gone, the supervisor recovers realized PnL only from matching private user-data `ORDER_TRADE_UPDATE` events. If that fill is unavailable, artifacts must mark `realized_pnl_status=unavailable` and Telegram must print `PNL: n/a` rather than a fake zero.
```

---

## 6. Backtest pair-mode contracts

Backtest pair modes must not be mixed when making edge claims.

```text
forming
```

The existing forming mode evaluates LTF decisions inside the current HTF candle. It is only edge-valid when LTF coverage was available for the watched universe before the decision; targeted event-window coverage is diagnostic-only for early-entry claims.

```text
post_htf_close_ltf_confirmation
```

The post-HTF-close mode first waits for the HTF setup candle to close. It may then inspect the complete LTF segment inside that now-closed HTF candle as a confirmation/filter, plus a left LTF context window before the anomalous HTF candle for rejection/context features such as prior whipsaw. Entry must be no earlier than the HTF close and must still pass drift, TP-before-entry and RR guards. This mode tests late continuation after HTF confirmation; it does not prove early intra-HTF LTF edge.

```text
post_htf_close_ltf_forward_confirmation
```

The post-HTF-forward mode first waits for HTF setup candle N to close and pass setup interest. It then searches LTF confirmation only after N closes, starting in the next HTF window. LTF left context before N may be used for rejection/context, but entry is tied to the forward LTF decision candle and must not be placed retroactively inside N. This is a late-confirmation research mode, not current live2 parity.

## Structural Stop Contract For Research Edge Claims

Fixed-percent SL is not a valid Pump Awakening edge claim by itself. A stop may use a small execution/microstructure buffer, but the invalidation level must be a level visible on the chart and available at decision time.

Valid research stop families:
- HTF anomaly low/high after the HTF candle is closed.
- Post-close LTF confirmation-window swing low/high.
- Recent closed LTF swing low/high from the confirmation window.

Invalid for strategy proof:
- Fixed 1% / 1.5% / N% stop without a structural level.
- Stop levels selected from future candles after entry.
- Stop levels inferred from a later chart state that live would not have known.

For the current long-continuation research, the most promising structural candidate is long after a closed upward HTF anomaly and post-close 5s continuation, with long invalidation below the HTF anomaly low. This is not yet a live-ready rule; live and backtest must share the same entry timing, structural stop source, stale/drift/RR guards, and actual-fill risk base before any edge claim.

## Long Post-Anomaly Acceptance Hypothesis

The long continuation candidate should not be framed as buying a fixed short-term return threshold. The threshold is only a measurable proxy for acceptance.

The intended nature:
- closed HTF flow anomaly after dormancy;
- no immediate post-close rejection/fade;
- early LTF continuation that remains structurally invalidated by the HTF anomaly low;
- flow distributed across several 5s candles rather than one isolated print;
- moderate taker-buy pressure is acceptable, but extreme buyer chase is not required and may be lower quality;
- repeated prior spikes/fades reduce trust in the same continuation.

Candidate implementation should expose these fields separately in artifacts: HTF structural stop source, post-close LTF return, post-close low versus HTF close, top 5s quote-volume concentration, last-half quote-volume share, taker-buy quote share, prior-spike count, risk percent, and reject reason.

## HTF/LTF Runner Discovery Backtest Contract

`run-htf-ltf-runner-discovery` is a research/discovery tool for learning which HTF anomaly shapes become early runners. It is not a live signal contract by itself.

The operator command is intentionally fixed-profile:

```text
python main.py run-htf-ltf-runner-discovery --days N
```

The command must run exactly these TF sets unless code is deliberately changed:
- `5m_30s`: HTF anomaly on 5m, LTF confirmation/replay on 30s;
- `5m_1m`: HTF anomaly on 5m, LTF confirmation/replay on 1m;
- `5m_15s`: HTF anomaly on 5m, LTF confirmation/replay on 15s.

No timeframe, symbol, threshold, output, risk, or trailing flags should be required for the standard discovery run. Profile outputs are written under `htf_ltf_runner_discovery_<days>d/<profile>/`.

Terminal progress must stay compact: one rewritten progress line per profile with processed count, percent, current symbol, and ETA, followed by one final summary line. Per-symbol diagnostics belong in artifacts, not stdout.

The discovery run must also write fixed early-entry windows for runner-vs-fader research:

```text
5m_30s windows: 2, 4, 6, 8 closed 30s candles after HTF close
5m_1m windows: 1, 2, 3, 4 closed 1m candles after HTF close
5m_15s windows: 4, 8, 12, 16 closed 15s candles after HTF close
decision time: last closed LTF candle in the window
entry model: next LTF open plus adverse slippage
future labels: evaluation only, never entry filters
```

These windows are not a parameter grid for live tuning. They are a bounded research lens to test whether volume/trade-count sustain, decay under 50%, current spike size versus prior same-symbol spikes, price acceptance, OI behavior, and structural-low survival separate runners from noise.

Required truth boundaries:
- Candidate construction may use only closed HTF history, dormancy/pregrowth history, cached OI rows available by decision time, and LTF candles inside the already closed HTF anomaly candle.
- Live nature/category fields such as `setup_nature` may use only live-available candidate-time features. Future low-break and runner outcomes must remain separate evaluation labels.
- `runner_10pct_next_hour`, `future_max_return_pct`, and anomaly-low-break fields are future labels for evaluation only. They must not be used as entry filters.
- Entry replay starts only after closed forward LTF confirmation, enters at the next LTF open with adverse slippage, and rejects stale/drift/excess-risk cases before simulating.
- Stop must be structural: anomaly low and closed confirmation-window lows with a small buffer. Position management closes 50% at TP1=0.75R, then manages the remaining 50% with structural trailing stop or max-hold time exit. Early-exit conditions are telemetry only unless deliberately re-enabled as a separate experiment.
- Broad runs should default to cache-only 5m/1m when subminute cache is not already materialized. The command must not trigger exchange candle or seconds-data downloads during the backtest.

Artifacts must expose:
- candidate rows with dormancy, smooth pregrowth, actual OI status/change, HTF quote/trade ratios, HTF-internal LTF distribution/acceleration, runner labels, and anomaly-low-break labels;
- candidate rows are scoped to HTF anomaly gate rows only. The funnel/run_config must expose scanned HTF row count and pre-artifact rejected row count so speed optimizations do not hide the reject base;
- entry-window rows with decision/availability/entry timestamps, no-future-label flags, prior 24h spike context, LTF volume/trade decay and sustain fields, OI-at-decision fields, structural stop source, drift/risk fields, and execution skip reasons;
- entry-window TP1-partial-plus-structural-trailing trade rows and rule-score tables for raw and same-symbol-filtered scopes, including volume-sustain and fader-decay families;
- daily summary rows for selected trades and entry-window trades, raw and same-symbol-filtered, so positive-day share and day-level concentration are visible by default;
- signal rows with decision time, decision availability time, next-open entry, structural stop source, drift and initial-risk fields;
- raw and live-filtered trade rows with TP1=0.75R 50% partial close plus structural trailing outcomes;
- live-filtered trade rows must reject only same-symbol overlap. They must not cap simultaneous positions across different symbols;
- candidate rule scores for runner-label lift without using future labels as entry filters;
- trade rule scores with net-PnL winrate, average/median/sum return, MFE/MAE, runner-label shares, and top20 positive-PnL dependency;
- a research shortlist that ranks rule candidates as research-only and points to the next validation step.

## Live2 current-OI position-management baseline

For live2 managed positions, current open interest is not inferred from candles and is not read through private client fields. After the actual entry fill is known and the initial stop has been verified visible, live2 may fetch a current-OI snapshot through the typed exchange boundary and persist it on the protected position as the entry current-OI baseline.

During supervision, OI-down early-exit logic compares fresh current-OI snapshots from the live OI poller against that protected entry current-OI baseline. Historical 5m OI remains useful context for setup/anomaly state and artifacts, but it is not the baseline for the current-OI-down exit trigger. Missing current-OI status must stay explicit in artifacts; it must not be silently replaced by 5m historical OI.


## Live rolling runner category contract

Live entries are based on the same family as the rolling runner discovery backtest:

```text
closed 30s candle
-> rolling HTF seed from closed 30s candles, 5m/30s or 3m/30s
-> baseline/dormancy/pregrowth from event-rolling HTF-width chunks built from closed 1m candles fully before the rolling HTF window
-> first closed 30s confirmation window that matches fixed C/A/S priority
-> live entry guard
-> actual exchange fill
-> verified stop
```

The active category priority is fixed before outcomes are known:

```text
C_balanced_flow_acceptance -> A_resonance_prior_spike -> S_7d_5m30_strict
```

Missing baseline/rolling context is a hard `data_dependency_not_ready` state, not a fallback to another signal model. Live position sizing uses account balance and stop distance so that planned risk is 2% per trade, with total protected open risk capped at 8% unless changed deliberately.

## Live rolling 1m context maintenance

Live 30s decision candles and entry flow remain WS aggTrade-derived. Official Binance 1m klines may be used only for closed historical rolling context: baseline, dormancy, pregrowth, prior-spike continuity, and zero-volume minute representation. Maintenance klines must carry an explicit source label (`binance_futures_klines_maintenance_rest_1m_rolling_context`) and must not replace current partial buckets or live entry flow. REST maintenance is bounded/background-only; it is not allowed to block signal/entry hot path or hide data-quality failures.
