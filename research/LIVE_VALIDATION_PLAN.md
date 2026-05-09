# Live Validation Plan: Anomaly Wake-Up

Status: PROPOSED  
Date: 2026-05-09  
Branch: codex/pno-anomaly-continuation-lab  
Purpose: минимальный live/paper+micro-real режим для проверки edge без заглядывания в будущее.

---

## 1. Цель

Запустить не "боевого робота", а live validation loop:

- находить controlled wake-up пампы;
- разделять пампы по природе;
- включать активное слежение только для актуальных кандидатов;
- торговать минимальным размером, если включён real mode;
- сохранять полный audit trail по каждому решению;
- не маскировать отсутствие данных fallback-ами.

Ключевой принцип:

```text
сначала честная live-валидация гипотезы, потом масштабирование
```

---

## 2. Режимы работы

### 2.1 Scan mode

Дешёвый проход по всем инструментам.

Что грузим:

- последние 1m свечи;
- rolling baseline для quote_volume / number_of_trades;
- минимум данных для stage0/stage1 wake-up;
- без дорогого derivatives context, пока нет подтверждённого кандидата.

Задача:

- быстро понять, есть ли ранний wake-up;
- не тратить лимиты на спящие монеты;
- не создавать позиции.

### 2.2 Watch mode

Активное слежение по монете, где wake-up уже подтверждён.

Триггер входа в watch:

- есть актуальный stage wake-up;
- есть удержание после начального импульса;
- нет явных red flags;
- данные имеют нужное качество.

Что догружаем:

- 1m/5m свечи;
- OI 5m;
- funding, mark/index/premium;
- long-short / taker long-short, если доступны в 30d/live окне;
- при необходимости trades/aggTrades вокруг события.

Watch mode должен иметь TTL. Если setup стареет или контекст ломается, монета выходит из watch.

### 2.3 Position mode

Открытая позиция.

Задача:

- сопровождать позицию;
- проверять stop/TP/trailing/BE;
- проверять red flags;
- слать Telegram-события;
- сохранять график закрытия и realized PnL.

---

## 3. Категории пампов

Параметры должны быть зафиксированы до live-теста. Нельзя вручную переоптимизировать по ходу.

### 3.1 Controlled Organic Wake-Up

Основной long-кандидат.

Признаки:

- заметная verticality, но без хаотичного range expansion;
- start_close_position_in_range высокий;
- start_trade_ratio умеренный, не экстремальный;
- quote/trade не выглядит как редкий крупный принт;
- taker buy share не слабый;
- удержание следующих свечей есть;
- OI растёт, если OI доступен;
- post-start pullback не уничтожает импульс.

Решение:

```text
eligible for entry
```

### 3.2 Thin Large-Print Pump

Опасный тип, похожий на CVX.

Признаки:

- мало сделок;
- высокий quote_volume / number_of_trades;
- много zero-range свечей до события;
- рваные 1m свечи;
- taker buy на стартовой свече слабый или перекошенный;
- большой impulse up, затем резкий impulse down.

Решение:

```text
default no-trade; watch only for research
```

### 3.3 Exhaustion / Violent Pump

Импульс сильный, но edge long-continuation сомнительный.

Признаки:

- очень высокий trade_ratio / quote_ratio;
- плохой effort per return;
- close не удерживается у high;
- сильный откат сразу после старта;
- next candles теряют поток.

Решение:

```text
no-trade или отдельный short-research, не смешивать с long edge
```

### 3.4 Derivatives-Led Squeeze

Можно торговать только при честной доступности контекста.

Признаки:

- OI up + price up;
- mark/index/premium не противоречат движению;
- taker long-short подтверждает поток;
- нет перегретого red flag.

Решение:

```text
eligible only when derivatives context is fresh
```

---

## 4. Очередь обхода символов

Нужна единая priority queue.

Состояния:

- `inactive`;
- `candidate`;
- `watch`;
- `position`;
- `cooldown`;
- `disabled`.

Базовый цикл:

```text
active_set = watch + position + fresh_candidate
inactive_queue = all other enabled symbols

each cycle:
  check all active_set first
  then check N inactive symbols
```

Пример политики:

```text
if active_count == 0:
  scan 20 inactive symbols

if active_count <= 3:
  scan all active + 7 inactive

if active_count <= 10:
  scan all active + 3 inactive

if active_count > 10:
  scan active only, inactive scan degraded
```

Частоты:

- `position`: каждые 5-15 секунд или websocket/user-data event;
- `watch`: каждые 15-30 секунд;
- `candidate`: каждые 30-60 секунд;
- `inactive`: best-effort round-robin, примерно 3-10 минут на полный universe в зависимости от лимитов.

Важно: inactive scan не должен блокировать active supervision.

---

## 5. Rate-limit стратегия

Предпочтительный порядок:

1. REST только для bootstrap и gap-fill.
2. Websocket для live candles/ticker/user-data, если возможно.
3. Batch/multi-symbol endpoints, где Binance их даёт.
4. Дорогой derivatives context только после входа в watch.
5. Per-source token bucket, а не sleep по всему процессу.

Компоненты:

- `RateLimitBudget`;
- `RequestScheduler`;
- `SymbolQueue`;
- `ContextFetcher`;
- `CacheWriter`.

Правило:

```text
нет запроса без budget check
```

Если лимит истощён:

- position checks имеют приоритет;
- watch checks имеют второй приоритет;
- inactive scan откладывается.

---

## 6. Вход в watch mode

Минимальный контракт:

```text
symbol
stage
timestamp_ms
category
category_score
wake_up_metrics
data_quality_status
watch_started_at
watch_ttl_ms
red_flags=[]
```

Причины входа:

- `controlled_wakeup_candidate`;
- `derivatives_squeeze_candidate`;
- `research_watch_only`.

Telegram:

```text
Включаю слежение: SYMBOL
Категория: controlled organic wake-up
Почему интересно: ...
Что настораживает: ...
Что должно случиться для входа: ...
```

---

## 7. Выход из watch mode

Причины:

- `setup_expired`;
- `hold_lost`;
- `midpoint_lost`;
- `context_missing`;
- `red_flag_thin_large_print`;
- `red_flag_exhaustion`;
- `data_quality_failure`;
- `position_opened`;
- `manual_disabled`.

Telegram:

```text
Снимаю SYMBOL со слежения.
Причина: ...
Коротко: импульс не удержался / контекст устарел / появились крупные редкие принты.
```

---

## 8. Entry logic для live validation

Пока не менять на много вариантов.

Default:

```text
category = controlled organic wake-up
entry_method = market at confirmed decision close
exit_rule = structural_trail
hold >= 2
OI3 > 5%, если OI доступен
no red flags
```

Если OI недоступен:

- не подставлять proxy;
- либо no-trade;
- либо отдельный режим `no_oi_research_only`, без реального ордера.

---

## 9. Risk and position sizing

Параметры:

```text
risk_per_trade = 5% deposit
min_notional = 12 USDT
```

Формула:

```text
risk_amount = deposit_equity * 0.05
unit_risk = entry_price - stop_price
position_qty = risk_amount / unit_risk
notional = position_qty * entry_price
```

Если `notional < 12 USDT`:

```text
position_notional = 12 USDT
effective_risk_pct must be recalculated
```

Если из-за min notional фактический риск становится слишком большим:

```text
skip real order; allow paper only
```

Предлагаемый hard cap:

```text
effective_risk_pct <= 8-10% deposit
```

Нужен отдельный параметр, обсуждаемый до реализации.

---

## 10. Position management

Default:

- initial stop below decision box;
- TP1 partial;
- stop to BE after TP1;
- structural trailing;
- red flag exit can close earlier.

Red flag exits:

- data quality failure during active position;
- exchange position mismatch;
- sudden context invalidation;
- strong loss of hold;
- hard risk/stop breach;
- manual kill-switch.

EMA20 exits:

- оставить как optional research exit;
- не default, потому что текущий 30d sample ухудшился по winrate/median.

---

## 11. Telegram events

Обязательные события:

1. Watch on.
2. Watch off.
3. Position opened.
4. Position closed.
5. Error / data-quality failure.
6. Daily summary.

Open message:

```text
Открыл SYMBOL long.
Категория: controlled organic wake-up.
Вход: ...
Стоп: ...
Риск: ... USDT / ...%
TP1: ...
Почему вход не случайный: ...
Слабые места: ...
Контекст: OI, taker, quote/trade, session.
```

Close message:

```text
Закрыл SYMBOL.
Причина: trailing_stop / stop_loss / red_flag / manual.
PnL: +X.XX USDT / +Y.YY%
R: ...
Что произошло после входа: ...
```

Close message должен прикладывать chart PNG.

---

## 12. Audit artifacts

Каждый live run пишет:

```text
live_events.csv
live_symbol_states.csv
live_watch_sessions.csv
live_positions.csv
live_orders.csv
live_context_status.csv
live_errors.csv
charts/
```

Нельзя хранить только Telegram как источник истины.

---

## 13. Safety

Обязательные kill-switch:

- max daily loss;
- max consecutive losses;
- max open positions;
- max active watches;
- exchange unavailable;
- stale candles;
- position/order mismatch;
- missing required context;
- unexpected schema/data source.

Любой fallback должен быть явным состоянием:

```text
status = unavailable_by_exchange_limit | missing_frame | stale | no_before | schema_error
```

Никаких proxy для OI/long-short/taker long-short.

---

## 14. Implementation phases

### Phase 1: Paper live supervisor

- symbol queue;
- scan/watch state machine;
- no real orders;
- Telegram watch on/off;
- artifacts.

### Phase 2: Signal categories and scoring

- controlled organic;
- thin large-print;
- exhaustion;
- derivatives-led;
- red flag reasons.

### Phase 3: Position simulator in live loop

- paper entries/exits;
- charts on close;
- daily summary;
- compare with anomaly-lab backtest logic.

### Phase 4: Micro-real trading

- real order adapter;
- min 12 USDT notional;
- risk cap;
- exchange reconciliation;
- emergency close.

### Phase 5: Evaluation

- compare paper vs real fills;
- category-level expectancy;
- session-level stability;
- false positive review;
- decide whether to continue, reduce, or stop.

---

## 15. Open decisions before code

1. Real orders сразу или сначала paper-only на 3-7 дней?
2. Effective risk cap when 12 USDT minimum notional exceeds 5% risk.
3. Max simultaneous positions.
4. Max watch symbols.
5. Whether OI missing means no-trade or paper-only.
6. Telegram credentials/config path.
7. Whether to use websocket in Phase 1 or REST-first with a clean abstraction.

---

## 16. Recommended first implementation

Do first:

```text
paper-only live supervisor with queue + watch state machine + Telegram + artifacts
```

Do not start with:

```text
real orders
multi-profile optimization
short strategy
hard session filter
```

Reason: current edge is promising but top-tail dependent and validated only on one 30-day window.
