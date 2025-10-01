
# Торговый бот (USDT, Binance/Bybit): полная инструкция и архитектура — финальная версия

> Жёсткое ТЗ. Строгая типизация, `dataclass`/`Enum`, без `getattr/hasattr/isinstance`. Таймзона — **Europe/Belgrade**.

---

## 1) Цель
Бот отслеживает свечи (1m/3m/5m/15m) на Binance Futures и Bybit Perpetual (USDT), детектирует сигналы, открывает лонг/шорт, ставит TP/SL по экстремумам сигнальной свечи, ведёт xlsx-дневник сигналов/сделок, отправляет Telegram-уведомления (только по сигналам).

---

## 2) Константы (config.py)
```python
TIMEZONE_NAME = "Europe/Belgrade"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

DEFAULT_TIMEFRAMES = (
    Timeframe.M1,
    Timeframe.M3,
    Timeframe.M5,
    Timeframe.M15,
)

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
MIN_RR = 0.0  # TODO: подобрать оптимальное значение

# Размер позиции
MIN_ORDER_USDT = 10.0
ORDER_PCT_OF_DEPOSIT = 0.05
DEPOSIT_REFRESH_MIN = 60

# Бэктест (PNL в %)
TAKER_FEE_ENTRY_PCT = 0.04
TAKER_FEE_EXIT_PCT = 0.04
BACKTEST_MIN_COVERAGE = timedelta(days=30)

# Сопровождение позиции
BREAKEVEN_TRIGGER_PCT = 0.5
BREAKEVEN_OFFSET_PCT = 0.3
TRAIL_SWING_WINDOW = 5
TRAIL_SWING_CONFIRM = 2

# Агрессия по принтам
AGGR_WINDOW_SEC = 15
AGGR_IMBALANCE_THRESHOLD = 0.62

# Анти-дубликаты
SYMBOL_COOLDOWN_SEC = 3600

# Логи
LOG_TIME_FMT = "%H:%M:%S"

# Дневник
DIARY_BATCH_SIZE = 100
DIARY_FLUSH_TIMEOUT = 600.0

# Аномалии
ANOMALY_MIN_GROWTH_PCT = 2.5
ANOMALY_MIN_ATR_MULT = 2.5
ANOMALY_MIN_RELATIVE_VOLUME = 3.0
ANOMALY_MIN_VOLUME_SPIKE = 3.0
ANOMALY_MIN_UPPER_WICK_PCT = 20.0

# Настройки API
BINANCE_API_URL = "https://fapi.binance.com"
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")
BINANCE_RECV_WINDOW = 5000
BYBIT_API_URL = "https://api.bybit.com"
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET")
BYBIT_RECV_WINDOW = 5000
BYBIT_TIMEOUT = 10.0
```

API-ключи и токены передаются через переменные окружения (например, через `.env`).

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
- Для бэктеста: `pct_to_low_break`, `pct_to_high_break`, `break_direction ∈ {-1,0,1,2}` (`BOTH=2` означает, что последующий бар выбил и high, и low, что трактуется как срабатывание стоп-лосса).

---

## 5) Пороговые параметры
- `min_green_move_pct`, `min_volume_spike`
- `min_relative_volume`, `max_relative_volume`
- `min_atr_mult`
- `min_pct_move`, `max_pct_move`
- `max_upper_wick_pct`, `max_lower_wick_pct (=50)`

---

## 6) Правила входа и аномалии

1. Сначала бар должен пройти фильтр «зелёной аномалии»:
   - `close > open`;
   - `pct_move ≥ min_green_move_pct`;
   - `atr_mult ≥ min_anomaly_atr_mult`;
   - `relative_volume ≥ min_anomaly_relative_volume` и `relative_volume ≥ min_volume_spike`.
   При выполнении условий фиксируем `Anomaly` со снапшотом порогов (`min_green_move_pct`, `min_volume_spike`, `min_relative_volume`, `min_atr_mult`).
2. Если аномалия подтверждена, строим торговый сигнал. Возможны два сценария:
   - **LONG** — одновременно:
     - `min_relative_volume ≤ relative_volume ≤ max_relative_volume`;
     - `atr_mult > min_atr_mult`;
     - `min_pct_move ≤ pct_move ≤ max_pct_move`;
     - `upper_wick_pct < max_upper_wick_pct`;
     - `lower_wick_pct < max_lower_wick_pct`.
     Уровни: `entry=c`, `tp=h`, `sl=l`.
   - **SHORT** — одновременно:
     - `relative_volume < min_relative_volume` **или** `relative_volume > max_relative_volume`;
     - `atr_mult < min_atr_mult`;
     - `pct_move > max_pct_move` **или** `pct_move < min_pct_move`;
     - `upper_wick_pct < max_upper_wick_pct`;
     - `lower_wick_pct < max_lower_wick_pct`.
     Уровни: `entry=c`, `tp=l`, `sl=h`.

   Во всех случаях сигнал формируется только при `RR ≥ MIN_RR`, где:
   - Для **LONG**: `RR = (tp - entry) / (entry - sl)`;
   - Для **SHORT**: `RR = (entry - tp) / (sl - entry)`.
   Деление на ноль блокирует создание сигнала.

Если условия лонга и шорта не выполняются, сигнал не создаётся, но аномалия записывается в дневник.

---

## 7) Размер позиции
`order_size_usdt = max(MIN_ORDER_USDT, ORDER_PCT_OF_DEPOSIT * DEPOSIT_USDT)`; `DEPOSIT_USDT` обновляем каждые `DEPOSIT_REFRESH_MIN` минут (при ошибке — лог и используем старое).

---

## 8) Сопровождение (лайв)
- **Break-even**: при достижении `BREAKEVEN_TRIGGER_PCT` (в абсолютных % от цены входа) возможен перенос стопа на `entry * (1 ± BREAKEVEN_OFFSET_PCT/100)`.
- `ExecutionService` предоставляет утилиты `should_move_to_breakeven` и `breakeven_stop`; управление трейлингом и агрессией возлагается на вызывающий код.
- `SwingDetector` представляет собой статическую утилиту: методы `detect_swing_high`/`detect_swing_low` принимают последовательность экстремумов и возвращают подтверждённый свинг по окнам `TRAIL_SWING_WINDOW` и `TRAIL_SWING_CONFIRM`.
- `PrintsAggregator` аккумулирует сделки через `add_print` и возвращает долю покупок `imbalance()` в скользящем окне `AGGR_WINDOW_SEC`.

---

## 9) Бэктест (PNL в % + комиссии тейкера)
- `pnl_gross%` через `pct_to_*` и `break_direction` (по правилам входа).
  При симуляции результата используем направление пробоя, зафиксированное
  на сигнальной свече: если пробой был только в сторону тейк-профита —
  фиксируем TP, если только в сторону стоп-лосса — SL. Когда свеча пробила
  оба экстремума (`break_direction = BOTH`) или направление отсутствует,
  консервативно считаем, что первым был задет стоп-лосс.
- `pnl_net% = pnl_gross% - TAKER_FEE_ENTRY_PCT - TAKER_FEE_EXIT_PCT`.
- Денежные расчёты в бэктесте не требуются.
- При историческом прогоне оркестратор не обращается к `ExecutionService`: сделки симулируются по экстремумам свечей, TP/SL
  считаются при достижении `high/low` и фиксируются в момент закрытия бара без моделирования агрессии или проскальзывания.

---

## 10) Анти-дубликаты
- Обрабатываем только **закрытые** бары.
- Если по закрытому бару найдена аномалия и сформирован сигнал, то для соответствующего символа повторная обработка запрещена в течение `SYMBOL_COOLDOWN_SEC` секунд (хранится в памяти оркестратора).
- В остальных случаях ограничения на повторную обработку баров отсутствуют.

---

## 11) Дневник (xlsx)
`WorkbookDiary` ведёт `signals.xlsx`, `trades.xlsx` и журнал аномалий. Запись построчная, операции защищены lock'ом.

### signals.xlsx
- `signal_id, timestamp(tz), exchange, symbol, timeframe, direction`
- Уровни: `entry_price, take_profit_price, stop_loss_price`
- `bar_id`
- Полный набор метрик бара (`BarMetrics`)
- Threshold snapshot с полями из §5.

### trades.xlsx
- `trade_id, source_signal_id`
- `timestamp_open(tz), timestamp_close(tz?)`
- `exchange, symbol, timeframe, side`
- `entry_price, take_profit_price, stop_loss_price`
- `executed_qty`
- `status ∈ {OPENED, CLOSED_TP, CLOSED_SL, CLOSED_MANUAL, CANCELLED}`
- `avg_fill_price?`, `reason_close?`, `sl_be_at?`

### anomalies.xlsx (или аналогичный лист)
- Полная копия данных бара и метрик
- Пороговый снапшот `min_green_move_pct`, `min_volume_spike`, `min_relative_volume`, `min_atr_mult`.

Публичные методы дневника: `append_signals`, `append_trades`, `append_anomalies`.

## 12) Analyzer API
`SignalAnalyzer.analyze_bar(bar, timestamp=None)` → `(Signal | None, Anomaly | None)`. Сначала оценивает аномалию, затем — возможность лонга/шорта. `ThresholdSnapshot` хранит пороги из §5, а `SignalLevels` включает `entry/tp/sl`.

## 13) Execution API
- `calc_order_size_usdt(deposit_usdt)` — выдаёт размер позиции в USDT с учётом `MIN_ORDER_USDT` и `ORDER_PCT_OF_DEPOSIT`.
- `open_trade(signal, quantity, timestamp=None)` — создаёт объект `Trade` со статусом `OPENED`.
- `close_trade(trade, reason, price, timestamp)` — возвращает закрытую копию сделки с маппингом причины в статус.
- Вспомогательные методы: `should_move_to_breakeven(entry_price, last_price, side)` и `breakeven_stop(entry_price, side)`.

## 14) Notifier
Протокол уведомителя определяет метод `send_signal(signal)`.

## 15) Market data
- `MarketDataLoader.load(request)` — итерируется по историческим барам (`HistoricalRequest`).
- `LiveDataStream.subscribe(callback)`/`close()` — стрим закрытых баров для оркестратора.

---

## 16) Логирование
Единый логгер: время `чч:мм:сс`, текст на грамотном русском. Логируем: ошибки, этапы загрузки/переподключения, сигналы, расчёт и открытие сделок, запись в дневник. Не спамим.

---

## 17) Архитектура и слои

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
- `HistoricalRequest` (датакласс запроса), абстракции `MarketDataLoader` и `LiveDataStream`.

**data.diary**
- `WorkbookDiary.append_signals/append_trades/append_anomalies`, протокол `DiaryBackend`.

**data.notifier**
- Протокол `Notifier.send_signal(signal)`.

**domain.analyzer**
- `SignalAnalyzer.analyze_bar(bar, timestamp=None) -> tuple[Signal | None, Anomaly | None]`.

**domain.swing_detector**
- `SwingDetector.detect_swing_high(highs)` / `detect_swing_low(lows)`.

**utils.prints_aggregator**
- `PrintsAggregator.add_print(trade_print)` / `imbalance()` / `clear()`.

**domain.execution_service**
- `calc_order_size_usdt`, `open_trade`, `close_trade`, `should_move_to_breakeven`, `breakeven_stop`.

**domain.orchestrator**
- `start`, `stop`, `backfill`; потоковые обработчики `_on_bar`, `_handle_bar`.

---

## 18) Приёмка
- Константы вместо магических чисел.
- Бэктест: `pnl_net% = pnl_gross% - fee_entry - fee_exit`.
- Breakeven рассчитывается через `ExecutionService`.
- Throttle — в оркестраторе, общий на бар.
- Записи — моментально, под mutex.
- Логи — единый логгер, русский текст, время `чч:мм:сс`.
