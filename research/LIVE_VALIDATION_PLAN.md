# Live Validation Plan: Anomaly Wake-Up Micro-Live

Status: IMPLEMENTATION STARTED
Date: 2026-05-09  
Branch: codex/pno-anomaly-continuation-lab  
Mode: micro-live first, not paper-first  
Purpose: live validation with real micro fills, full audit trail and strict safety.

Implementation note 2026-05-09:

```text
First strict REST-only implementation is run-anomaly-live.
It writes live_events.csv and live_positions.csv under results/live_anomaly_runs/<timestamp>.
It requires --confirm-real-orders plus filled Binance and Telegram env values.
It does not substitute missing flow/OI data; missing data is a reject reason.
Protective stop placement is mandatory: if stop placement fails after entry, the runner immediately sends a reduce-only market close and raises.
Shared state is protected by one simple RLock; order placement and network calls are not run while holding that lock except for final in-memory state mutation.
Initial live stop is max(previous structural stop, EMA20), matching the research backtest after P106.
```

---

## 1. Goal

Build a simple, robust live loop for the anomaly wake-up strategy:

- scan all futures symbols cheaply;
- actively watch only fresh wake-up candidates;
- trade only the controlled organic wake-up category;
- keep full artifacts for later research;
- send useful Telegram messages through two separate Telegram bots;
- survive network/API issues without killing the process;
- avoid hidden fallbacks.

This is not a claim that the edge is proven. It is a micro-live validation loop with real execution.

---

## 2. Live vs Backtest Differences We Must Accept

- Live uses only closed candles for signal confirmation.
- Execution is real: spread, delay, partial fill and slippage exist.
- The scan queue can see inactive symbols late.
- Context data can be unavailable or delayed.
- OI/long-short context is live/30d only; no free full-year validation exists.
- Telegram, file IO and chart rendering are operational tasks, not part of signal truth.
- REST is the only market/exchange transport in v1; websocket is out of scope for this implementation.

Therefore every decision must be persisted locally before or immediately after action.

---

## 3. Modes

### 3.1 Scan Mode

Cheap pass over all enabled symbols.

Loads:

- latest closed 1m candles;
- enough rolling history for baseline metrics;
- quote_volume;
- number_of_trades;
- taker_buy_quote_volume if present in klines.

Does not load expensive derivatives context until a symbol becomes a candidate.

Output states:

- inactive;
- candidate;
- watch;
- cooldown;
- disabled.

### 3.2 Watch Mode

Active tracking for symbols with confirmed wake-up context.

Loads additionally:

- 5m candles;
- OI 5m;
- mark/index/premium;
- funding if relevant;
- long-short/taker long-short where available;
- optional trades/aggTrades around event if needed.

Watch mode has TTL and exits if the setup loses context.

### 3.3 Position Mode

Real micro-live position.

Handled by a dedicated position worker so main scan loop continues.

Position worker responsibilities:

- exchange reconciliation;
- stop/TP/trailing decisions;
- red-flag checks;
- position state persistence;
- close chart generation;
- Telegram close event enqueue.

---

## 4. Pump Categories

### 4.1 Controlled Organic Wake-Up

Only category eligible for real entry in v1.

Expected traits:

- verticality is strong;
- close holds near high;
- trade and quote expansion are present but not absurd;
- quote/trade is not a rare-large-print signature;
- taker buy share is not weak;
- next candles hold;
- OI confirms if OI is used;
- no large immediate impulse-down.

### 4.2 Thin Large-Print Pump

Default no-trade.

Traits:

- low trade count;
- high quote_volume / number_of_trades;
- zero-range sleep artifacts;
- jumpy candles;
- weak start taker buy share;
- CVX-like impulse-up plus stronger impulse-down.

### 4.3 Exhaustion / Violent Pump

Default no-trade.

Traits:

- extreme trade/quote expansion;
- bad effort per return;
- close fails to hold;
- immediate deep pullback;
- flow fades quickly.

### 4.4 Derivatives-Led Squeeze

Can support controlled organic wake-up, but is not enough by itself.

Traits:

- OI up + price up;
- fresh OI;
- mark/premium context does not contradict;
- taker context supports flow.

---

## 5. OI Freshness Rule

For live, OI is considered usable if:

```text
oi_timestamp_ms >= now_ms - 5 minutes - small_clock_buffer
```

The latest received OI value is current until a new one arrives.

Do not kill a setup because OI is a little older than an ideal polling cadence. Kill only if:

- OI is older than the configured 5m freshness window;
- OI source has an explicit error;
- OI schema is invalid;
- OI is required by config and missing entirely.

No proxy is allowed for OI.

---

## 6. Symbol Queue

Single authoritative scheduler owns symbol state.

States:

- inactive;
- candidate;
- watch;
- position;
- cooldown;
- disabled.

Cycle policy:

```text
active = candidate + watch + position
inactive = enabled symbols not active/cooldown/disabled

if active_count == 0:
  check next 20 inactive

if active_count <= 3:
  check all active + next 7 inactive

if active_count <= 10:
  check all active + next 3 inactive

if active_count > 10:
  check active only
```

Position checks are not performed only by the main loop. They also have dedicated workers.

Trade concurrency:

```text
max_open_positions = 3
```

---

## 7. REST and Rate Limits

Transport policy:

```text
REST only in v1
```

No websocket dependency in the first implementation. The code should still hide exchange access behind interfaces so websocket can be added later without changing strategy logic.

Use simple token buckets by source:

- candles;
- OI;
- derivatives context;
- orders/account;
- Telegram.

Priority:

1. order placement / close / reconciliation;
2. open position supervision;
3. active watch;
4. candidate refresh;
5. inactive scan;
6. chart rendering and non-urgent Telegram.

No global sleep that blocks everything.

If rate budget is low:

- keep positions supervised;
- degrade inactive scan;
- postpone non-critical context.

---

## 8. Threading Architecture

Keep it simple: threads plus queues, no complex shared mutable logic.

Recommended components:

```text
MainSupervisor
  owns SymbolStateStore
  owns active/inactive queues
  schedules scan/watch tasks

MarketDataWorkerPool
  fetches candles/context through RequestScheduler
  returns immutable snapshots

PositionWorker per open position
  manages one symbol position
  has priority over scan/watch

OrderWorker
  serializes exchange order operations
  prevents concurrent conflicting order calls

TelegramEventsWorker
  async queue for watch on/off, skips, errors, network state
  rate-limited and deduplicated

TelegramPositionsWorker
  async queue for open, close, stop move, position emergency
  higher priority than events bot
  rate-limited and deduplicated

ArtifactWriter
  serializes CSV/JSON/chart writes
```

Shared state rule:

```text
only MainSupervisor mutates symbol states
only PositionWorker mutates its position state through events
only OrderWorker talks to trading endpoints
ArtifactWriter is append-only
```

Use `queue.Queue`, `threading.Thread`, `threading.Event`, `Lock` only around small state transitions.

Avoid:

- nested locks;
- shared pandas frames between threads;
- blocking Telegram/chart work in trading path;
- multiple threads placing orders for the same symbol.

---

## 9. Non-Blocking Events

Tracking, entries, exits, stop moves and Telegram must not block each other.

Priority path:

```text
signal decision -> risk check -> order request -> exchange response -> persist position -> enqueue Telegram
```

Telegram can lag seconds, not minutes. If Telegram is down:

- log once;
- keep a pending queue;
- retry with backoff;
- never block order/position management.

Chart generation on close:

- create close event immediately;
- enqueue chart rendering;
- send chart when ready;
- if chart fails, send close message with `chart_status=failed` and persist error.

---

## 10. Telegram Bots, Threads and Cooldowns

Use two separate Telegram bots:

```text
events bot:
  TELEGRAM_EVENTS_BOT_TOKEN
  TELEGRAM_EVENTS_CHAT_ID
  watch on/off, skips, red flags, data/network errors

positions bot:
  TELEGRAM_POSITIONS_BOT_TOKEN
  TELEGRAM_POSITIONS_CHAT_ID
  position opened/closed, stop moved, TP1, emergency close
```

Current `.env` check on 2026-05-09 did not find Telegram-like variable names. Do not print token values in logs.

Message style:

- Russian;
- concise;
- human-friendly;
- Markdown parse mode;
- starts with exactly one emoji plus one space;
- no more than one emoji per message;
- important sections use bold headings;
- no raw debug dumps.

Telegram threading:

- every watch session stores `watch_message_id`;
- every position stores `open_message_id`;
- watch-off messages reply to the corresponding watch-on message;
- position close, TP1, stop move and emergency messages reply to the corresponding open-position message;
- if the stored parent message is missing or Telegram rejects reply, send a normal message and persist `reply_fallback_used=true`;
- persist all Telegram message ids in artifacts.

Deduplicate per event key:

```text
event_key = symbol + event_type + reason
```

Suggested cooldowns:

- watch_on same symbol/category: 30 minutes;
- watch_off same symbol/reason: 30 minutes;
- repeated data warning: 30 minutes;
- network-down notice: until recovery or 30 minutes;
- stop moved: only material moves, not every tiny tick.

Position opened/closed messages are never suppressed.

---

## 11. Position and Trade Limits

Per-symbol stop cooldown:

```text
if symbol has >= 2 stop_loss exits in last N hours:
  symbol_state = cooldown
  no new entries
```

Default proposal:

```text
N = 12 hours
symbol_stop_limit = 2
cooldown_after_limit = 12 hours
```

Other limits:

- max open positions: 3;
- max active watch symbols: configurable;
- max daily realized loss;
- max consecutive losses globally;
- no entry if existing position/order mismatch exists.

---

## 12. Sessions

Trade all sessions.

Each entry message must include session by Europe/Belgrade time:

```text
Asia: 00:00-08:00
Europe: 08:00-16:00
US: 16:00-24:00
```

Session is an audit/risk field, not a hard filter in v1.

Persist:

- `session_name`;
- `entry_local_time`;
- `entry_utc_time`.

---

## 13. Risk and Sizing

Required:

```text
risk_per_trade = 5% deposit
min_notional = 12 USDT
```

Formula:

```text
risk_amount = equity * 0.05
unit_risk = entry_price - stop_price
qty_by_risk = risk_amount / unit_risk
notional_by_risk = qty_by_risk * entry_price
final_notional = max(notional_by_risk, 12 USDT)
effective_risk = final_qty * unit_risk
effective_risk_pct = effective_risk / equity
```

Safety:

```text
if effective_risk_pct > max_effective_risk_pct:
  skip entry
```

Default proposed cap:

```text
max_effective_risk_pct = 8%
```

This needs confirmation before code.

---

## 14. Unified Position Ledger

Maintain one canonical file for open and closed positions:

```text
live_positions.csv
```

One row per position, updated by `position_id`.

Required fields:

- position_id;
- symbol;
- status: opening/open/closing/closed/error;
- category;
- session_name;
- entry_signal_timestamp_ms;
- entry_order_id;
- entry_price_expected;
- entry_price_filled;
- qty;
- notional;
- initial_stop;
- active_stop;
- tp1;
- tp1_fraction;
- risk_usdt_expected;
- risk_pct_expected;
- risk_usdt_effective;
- risk_pct_effective;
- opened_at_utc;
- closed_at_utc;
- exit_reason;
- exit_order_id;
- exit_price_filled;
- realized_pnl_usdt;
- realized_pnl_pct;
- realized_r;
- max_favorable_excursion;
- max_adverse_excursion;
- red_flags_at_entry;
- red_flags_at_exit;
- context_snapshot_path;
- chart_path;
- watch_message_id;
- open_message_id;
- close_message_id;
- notes.

Append event history separately:

```text
live_position_events.csv
```

Do not rely on Telegram history as the ledger.

---

## 15. Artifact Layout

Each live run writes to a separate directory:

```text
.output/results/live_anomaly_runs/YYYYMMDD_HHMMSS/
  live_config.json
  live_events.csv
  live_symbol_states.csv
  live_watch_sessions.csv
  live_positions.csv
  live_position_events.csv
  live_orders.csv
  live_context_status.csv
  live_errors.csv
  live_telegram_messages.csv
  context_snapshots/
  charts/
  logs/
```

Open and closed positions remain in the same `live_positions.csv`.

---

## 16. Network Resilience

Network/API failures should degrade the bot, not crash it.

Policy:

- on first network outage, log one warning and send one Telegram warning if possible;
- enter `network_degraded` state;
- pause new entries;
- continue supervising known exchange positions through retries;
- retry with exponential backoff capped at a sane interval;
- on recovery, log one recovery event and reconcile exchange state.

No spam loop.

Do not mark missing data as valid. Use statuses:

```text
network_unavailable
exchange_timeout
rate_limited
stale
missing
schema_error
```

---

## 17. Entry Message Template

Human-friendly Russian message, not dry debug output:

```text
Открыл SYMBOL long.
Сессия: Europe.
Категория: controlled organic wake-up.

Вход: 1.2345
Стоп: 1.2100
TP1: 1.2590
Размер: 12.4 USDT
Риск: 0.62 USDT / 5.0%

Почему вход есть:
- памп удержался после стартовой свечи;
- OI свежий и растёт;
- taker buy не слабый;
- quote/trade без признака редких крупных принтов.

Что настораживает:
- объём выше обычного, но не экстремальный;
- рынок сейчас Asia, исторически хуже по медиане.
```

Open message should include:

- category;
- score;
- session;
- entry/stop/TP1;
- expected risk;
- OI freshness;
- key strengths;
- key weaknesses.

---

## 18. Close Message Template

```text
Закрыл SYMBOL.
Причина: trailing_stop.

PnL: +0.42 USDT / +3.1%
R: +0.8R
Держали: 18 минут

Коротко: TP1 взяли, стоп подтянулся, движение выдохлось у локального хая.
```

Attach chart when ready.

---

## 19. Red Flags

Red flags can disable watch or force exit depending on severity.

Watch-off red flags:

- hold lost;
- midpoint lost;
- thin large-print category;
- exhaustion category;
- OI stale beyond allowed 5m;
- candle data stale;
- zero-range / illiquid artifact.

Position-exit red flags:

- exchange position mismatch;
- hard stop reached;
- data stream stale while position is open;
- active context invalidated severely;
- manual kill switch.

Position exits must go through OrderWorker.

---

## 20. Implementation Phases

### Phase 1: Infrastructure Skeleton

- run directory;
- config;
- symbol queue;
- state store;
- artifact writer;
- Telegram worker with cooldown;
- network degraded state.

No orders yet, but architecture must be ready for real orders.

### Phase 2: Micro-Live Orders

- risk sizing;
- OrderWorker;
- exchange reconciliation;
- max 3 open positions;
- real entry/close;
- `live_positions.csv`.

### Phase 3: Watch and Category Logic

- controlled organic wake-up;
- thin large-print rejection;
- exhaustion rejection;
- OI freshness rule;
- session tagging.

### Phase 4: Position Worker

- per-position worker;
- structural trail;
- TP1 partial;
- stop move events;
- close chart.

### Phase 5: Review Tools

- daily summary;
- category performance;
- skipped-signal reasons;
- Telegram chart review;
- export ZIP for analysis.

---

## 21. Critical Implementation Rules

- Place orders only from OrderWorker.
- Main loop never blocks on Telegram or chart rendering.
- All exchange responses are persisted.
- Every skip has a reason.
- Every watch on/off has a reason.
- Every network degradation logs once and retries quietly.
- No OI proxy.
- No current open candle as closed data.
- No hidden fallback from live context to stale cache.

---

## 22. Open Decisions

Before code:

1. `max_effective_risk_pct`: proposed 8%.
2. `N hours` for two-stop symbol cooldown: proposed 12h.
3. Max open positions: decided 3.
4. Max active watch symbols: proposed 10.
5. Whether missing OI means no-trade or watch-only. Proposed: if config requires OI, no real entry.
6. Telegram config source.
7. Transport: decided REST-only for v1.

---

## 2026-05-09 category/Telegram update

```text
Live should not silently widen the best balanced candidate.
Default category order is balanced_market, then mild_market as the frequency-preserving alternative from the same research window.
Every selected position must record category_id/category_label in artifacts, logs and Telegram.
If an earlier category rejects and a later category passes, the rejection chain must remain visible.
Stop-move Telegram updates should edit the first stop message; edit/send failures are explicit live_events rows, not fallback spam.
```
