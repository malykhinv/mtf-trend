
# Торговый бот (USDT, Binance/Bybit): полная инструкция и архитектура — финальная версия

> Жёсткое ТЗ. Строгая типизация, `dataclass`/`Enum`, без `getattr/hasattr/isinstance`. Таймзона — **Europe/Belgrade**.

---

## 1) Цель
Бот отслеживает свечи (1m/3m/5m/15m) на Binance Futures и Bybit Perpetual (USDT), детектирует сигналы, открывает лонг/шорт, ставит TP/SL по экстремумам сигнальной свечи, ведёт xlsx-дневник сигналов/сделок, отправляет Telegram-уведомления (только по сигналам).

---

## 2) Константы (config.py)
```python
TIMEZONE = "Europe/Belgrade"
TF_LIST = ["1m","3m","5m","15m"]

# Окна метрик
VOL_WINDOW = 20
ATR_WINDOW = 14

# Thresholds (общие)
MIN_REL_VOL = 40.0
MAX_REL_VOL = 200.0
MIN_ATR_MULT = 5.0
MIN_PCT_MOVE = 5.0
MAX_PCT_MOVE = 10.0
MAX_UPPER_WICK_PCT = 100.0
MAX_LOWER_WICK_PCT = 50.0

# Размер позиции
MIN_ORDER_USDT = 10.0
ORDER_PCT_OF_DEPOSIT = 0.0005
DEPOSIT_REFRESH_MIN = 60

# Бэктест (PNL в %)
TAKER_FEE_ENTRY_PCT = 0.04   # пример
TAKER_FEE_EXIT_PCT  = 0.04   # пример

# Сопровождение позиции
BREAKEVEN_TRIGGER_PCT = 0.5
BREAKEVEN_OFFSET_PCT  = 0.3  # перенос SL за entry на 0.3%
TRAIL_SWING_WINDOW = 5
TRAIL_SWING_CONFIRM = 2

# Агрессия по принтам
AGGR_WINDOW_SEC = 15
AGGR_IMBALANCE_THRESHOLD = 0.62

# Анти-дубликаты
BAR_REPROCESS_THROTTLE_SEC = 3600  # применяется ТОЛЬКО после setup под ордер

# Логи
LOG_TIME_FMT = "%H:%M:%S"
```

Секреты и токены — в `.env`.

---

## 3) Данные и сессии
- **ccxt** для REST (маркеты, OHLCV, баланс/депозит, частично ордера) + встроенный rate-limit.
- **WS бирж**: kline (только закрытые бары), trades/agg-trades (для агрессии). Auto-reconnect с backoff. Дедуп событий.
- Лимит подписок: если пар больше, чем можно подписать, берём top-N по 24h объёму (остальные отбрасываем).

---

## 4) Метрики закрытого бара
`o,h,l,c,v`, `rng = h-l`; если `rng==0` — проценты = 0.
- `pct_move = (h - o)/o * 100`
- `relative_volume = v / median(v, VOL_WINDOW)`
- `atr_mult = (h - l) / ATR(ATR_WINDOW)`
- `upper_wick_pct = (h - max(o, c)) / rng * 100`
- `body_pct = abs(c - o) / rng * 100`
- `lower_wick_pct = (min(o, c) - l) / rng * 100` → `upper+body+lower = 100±0.01`
- Для бэктеста: `pct_to_low_break`, `pct_to_high_break`, `break_direction ∈ {-1,0,1}`.

---

## 5) Пороговые параметры
- `min_relative_volume`, `max_relative_volume`
- `min_atr_mult`
- `min_pct_move`, `max_pct_move`
- `max_upper_wick_pct`, `max_lower_wick_pct (=50)`

---

## 6) Правила входа

### SHORT — войти, если одновременно
- `relative_volume < min_relative_volume` **или** `relative_volume > max_relative_volume`
- `atr_mult < min_atr_mult`
- `pct_move > max_pct_move` **или** `pct_move < min_pct_move`
- `upper_wick_pct < max_upper_wick_pct`
- `lower_wick_pct < max_lower_wick_pct`

Уровни: `entry=c`, `tp=l`, `sl=h`.

### LONG — войти, если одновременно
- `min_relative_volume ≤ relative_volume ≤ max_relative_volume`
- `atr_mult > min_atr_mult`
- `min_pct_move ≤ pct_move ≤ max_pct_move`
- `upper_wick_pct < max_upper_wick_pct`
- `lower_wick_pct < max_lower_wick_pct`

Уровни: `entry=c`, `tp=h`, `sl=l`.

---

## 7) Размер позиции
`order_size_usdt = max(MIN_ORDER_USDT, ORDER_PCT_OF_DEPOSIT * DEPOSIT_USDT)`; `DEPOSIT_USDT` обновляем каждые `DEPOSIT_REFRESH_MIN` минут (при ошибке — лог и используем старое).

---

## 8) Сопровождение (лайв)
- **Break-even**: при `BREAKEVEN_TRIGGER_PCT` перенос `sl := entry * (1 ± BREAKEVEN_OFFSET_PCT/100)`
  - LONG → `sl = entry*(1+0.003)`, SHORT → `sl = entry*(1-0.003)`
- **Трейлинг по свингам** — отдельный класс `SwingDetector`:
  - ищет свинг-лоу/свинг-хай с окнами `TRAIL_SWING_WINDOW` и `TRAIL_SWING_CONFIRM`,
  - `ExecutionService` подтягивает SL по свингам (только в сторону уменьшения риска).
- **Агрессия принтов**: по потоку trades/agg-trades за `AGGR_WINDOW_SEC` вычисляем долю агрессивных buy/sell.
  - LONG: если доля sell ≥ `AGGR_IMBALANCE_THRESHOLD` — закрыть.
  - SHORT: если доля buy ≥ `AGGR_IMBALANCE_THRESHOLD` — закрыть.
- Частичные исполнения, отмены, проскальзывание — **логируем**.

---

## 9) Бэктест (PNL в % + комиссии тейкера)
- `pnl_gross%` через `pct_to_*` и `break_direction` (по правилам входа).
- `pnl_net% = pnl_gross% - TAKER_FEE_ENTRY_PCT - TAKER_FEE_EXIT_PCT`.
- Денежные расчёты в бэктесте не требуются.

---

## 10) Анти-дубликаты
- Обрабатываем только **закрытые** бары.
- **Throttle применяется ТОЛЬКО после возникновения setup под ордер** на конкретном `(symbol,timeframe,bar_id)`: повторная обработка этого бара не ранее, чем через `BAR_REPROCESS_THROTTLE_SEC`. На прочие бары ограничение не распространяется.

---

## 11) Дневник (xlsx)
`signals.xlsx` и `trades.xlsx` (append + upsert строки по id). Атомарно под lock. При блокировке/ошибке — лог + повтор позже.

### signals.xlsx
- `signal_id, timestamp(tz), exchange, symbol, timeframe`
- Метрики: `pct_move, relative_volume, atr_mult, upper_wick_pct, body_pct, lower_wick_pct, pct_to_low_break, pct_to_high_break, break_direction`
- Thresholds snapshot: все из §5
- Решение: `allow_long, allow_short`
- Уровни: `entry_price, tp_price, sl_price`
- `bar_id, version, notes`

### trades.xlsx
- `trade_id, source_signal_id`
- `timestamp_open(tz), timestamp_close(tz?)`
- `exchange, symbol, timeframe, side`
- `entry_price, tp_price, sl_price`
- Сопровождение: `sl_be_at(tz?), trail_params`
- `status ∈ {OPENED, CLOSED_TP, CLOSED_SL, CLOSED_MANUAL, CANCELLED}`
- Факт: `executed_qty, avg_fill_price`
- `reason_close ∈ {tp, sl, aggression, manual}`, `log_ref`

---

## 12) Логирование
Единый логгер: время `чч:мм:сс`, текст на грамотном русском. Логируем: ошибки, этапы загрузки/переподключения, сигналы, постановку ордеров, BE/трейлинг SL, закрытия, частичные/отмены, проскальзывания. Не спамим.

---

## 13) Архитектура и слои

```
bot/
  main.py
  config.py
  .env
  data/
    loader.py
    diary.py
    notifier.py
  domain/
    models/
      bar.py
      signal.py
      trade.py
      enums.py
    analyzer.py
    swing_detector.py
    execution_service.py
    orchestrator.py
  utils/
    math_ops.py
    prints_aggregator.py
    locks.py
    logging.py
```

### Интерфейсы

**main**
- `run_live()`, `run_backtest()`

**data.loader**
- `create_ccxt_clients() -> list[ExchangeClient]`   # строго типизировано, без Dict/Any
- `load_markets(exchange: Exchange) -> list[str]`
- `fetch_ohlcv(exchange: Exchange, symbol: str, timeframe: str, limit: int) -> list[Bar]`
- `subscribe_klines(exchange: Exchange, symbols: list[str], timeframe: str, on_bar_closed) -> None`
- `subscribe_trades(exchange: Exchange, symbols: list[str], on_trade) -> None`
- `fetch_deposit_usdt(exchange: Exchange) -> float`

**data.diary**
- `write_signal(signal: Signal) -> None`
- `upsert_trade(trade: Trade) -> None`

**data.notifier**
- `notify_signal(signal: Signal) -> None`

**domain.analyzer**
- `analyze_bar(bar: Bar) -> Signal | None`

**domain.swing_detector**
- `update(bar: Bar) -> None`
- `last_swing_low() -> float | None`
- `last_swing_high() -> float | None`

**utils.prints_aggregator**
- `on_trade(event) -> None`
- `imbalance_against(side: Side) -> float`  # 0..1 доля агрессоров против позиции

**domain.execution_service**
- `place_order(signal: Signal, size_usdt: float) -> Trade`
- `track_trade(trade: Trade) -> Trade`
- `close_trade(trade: Trade, reason: str) -> Trade`

**domain.orchestrator**
- `start() -> None`
- `on_bar_closed(bar: Bar) -> None`
- `process_signal(signal: Signal) -> None`

---

## 14) Приёмка
- Константы вместо магических чисел.
- Бэктест: `pnl_net% = pnl_gross% - fee_entry - fee_exit`.
- BE со сдвигом `BREAKEVEN_OFFSET_PCT`.
- Трейлинг SL через `SwingDetector`.
- Агрессия — имбаланс принтов за `AGGR_WINDOW_SEC`.
- Throttle — только после setup под ордер.
- Записи — моментально, под mutex.
- Логи — единый логгер, русский текст, время `чч:мм:сс`.
