# Инструкция по реализации торгового бота «аптики»

## Цели

Минимальный, быстрый, строго типизированный бот, который торгует «аптики»: видит давление по стакану, находит крупную устойчивую плотность, входит в сторону давления, ставит стоп за плотность, тащит стоп за перестановками, выходит при исчезновении стены или нейтрализации давления. Одна биржа на запуск, расширяемая архитектура под другие.

---

## Технологии и принципы

- **Язык:** Python 3.11+
- **Обмен с биржами:**
  - **Маркет‑данные (WS):** нативные WebSocket‑апи бирж (Binance, Bybit) через собственные лёгкие клиенты (минимум задержек, строгая типизация).
  - **Торговля/аккаунт (REST/WS):** собственные адаптеры к официальным эндпоинтам. **CCXT** допускается только как тонкий слой в одном адаптере для ордеров/баланса при необходимости быстрого старта. Приоритет — собственные обёртки.
- **Слои:** `domain/` (бизнес‑логика), `data/` (биржи, Telegram, логи), `utils/` (математика, время, формат).
- **Строгая типизация:** `mypy --strict`. Везде `dataclass | Enum | TypedDict` (TypedDict — только на границе парсинга внешних JSON).
- **Без рефлексии/угадалок:** никаких `hasattr/getattr/isinstance/try import`.
- **Маппинг всего входящего:** ответы бирж → свои модели. В домене — никаких словарей.
- **Нет магии:** все строки/числа — в `config/config.py`, ключи из `.env`.
- **Логи:** простой русский, `ЧЧ:ММ:СС Сообщение`.
- **Telegram:** уведомления только **после** событий.
- **Время:** `datetime` c таймзоной **Europe/Belgrade**.
- **Оптимизация:** одна активная монета в фокусе, короткие методы, кольцевые буферы, минимум аллокаций.

---

## Дерево проекта

```
app.py
config/
  config.py
  secrets.py
domain/
  models.py
  book.py
  detectors.py
  strategy.py
  execution.py
data/
  exchanges/
    base.py
    binance.py
    bybit.py
  telegram.py
  logger.py
utils/
  mathx.py
  timez.py
  formatting.py
```

---

## Конфигурация (`config/config.py`)

Только здесь — все константы/строки.

### Общее

- `EXCHANGE = "binance" | "bybit"`
- `PROFILE = "auto" | "T" | "A" | "L"`  # авто или явный профиль (Top/Alt/Listing)
- `LOOP_INTERVAL_MS = 100`
- `ODR_SMOOTH_SAMPLES = 3`
- `RECENT_BAND_S = 5`
- `RECENT_BAND_S_VOL_BOOST = 8`
- `VOL_SPIKE_MULT = 2.0`

### Обороты (отбор монет)

- `TURNOVER_1M_TOP_USD = 700_000`
- `TURNOVER_1M_ALT_USD = 120_000`
- `TURNOVER_1M_LISTING_USD = 250_000`
- `MARKET_SCAN_INTERVAL_S = 30`  # периодический перескан всего рынка

### Давление ODR

- `ODR_IN_SHORT = 3.1`
- `ODR_IN_LONG = 0.322`
- `ODR_NEUTRAL_LOW = 0.70`
- `ODR_NEUTRAL_HIGH = 1.45`
- `ODR_NEUTRAL_HOLD_MS = 800`
- `FOCUS_PRE_ODR_SHORT = 2.0`
- `FOCUS_PRE_ODR_LONG = 0.50`

### Плотности (стены)

- `WALL_ABS_TOP_USD = 600_000`
- `WALL_ABS_ALT_USD = 120_000`
- `WALL_ABS_LISTING_USD = 200_000`
- `WALL_REL_MULT = 3.5`
- `WALL_PERSIST_S_TOP = 2.0`
- `WALL_PERSIST_S_ALT = 2.0`
- `WALL_PERSIST_S_LISTING = 3.0`

### Перетяжка/выход

- `WALL_SHIFT_MIN_TICKS = 1`
- `WALL_SHIFT_HOLD_S = 0.8`
- `WALL_VANISH_DROP = 0.50`
- `WALL_VANISH_GRACE_MS = 600`

### Баланс/маржа/стопы

- `POSITION_FRACTION = 0.10`
- `POSITION_MIN_USDT = 10`
- `BALANCE_REFRESH_H = 1`
- `BALANCE_SOURCE = "available_balance" | "wallet_balance"`
- `MARGIN_MODE = "isolated" | "cross"`
- `LEVERAGE = 10`
- `STOP_TRIGGER = "mark_price" | "last_price"`

### Binance торговля

- `BINANCE_NEW_CLIENT_ORDER_ID_PREFIX = None | "..."`
- `BINANCE_MARKET_REDUCE_ONLY = False`
- `BINANCE_STOP_CLOSE_POSITION = False`

### Фокус/дефокус

- `DEFOCUS_TIMEOUT_S = 30`

### Фандинг/килл‑свитч

- `FUNDING_BLOCK_S = 30`
- `KS_MAX_STOPS_PER_MIN = 3`
- `KS_MAX_DRAWDOWN_FRAC = 0.005`
- `KS_BLOCK_MIN = 5`

### Telegram

- `TG_CHAT_ID = ...`
- `TG_SILENT = False`
- `TG_UPTICK_COOLDOWN_MIN = 5`  # кулдаун только на «обнаружен аптик»

Секреты в `.env`: `API_KEY`, `API_SECRET`, `TG_BOT_TOKEN` и др. Загружаются в `config/secrets.py`.

---

## Модели (`domain/models.py`)

`@dataclass(frozen=True)` и `Enum`.

- `Exchange`, `MarginMode`, `BalanceSource`, `StopTrigger`
- `SymbolFilters`
- `Candle`
- `Trade`
- `Side = {BID, ASK}`
- `OrderBookLevel`
- `OrderBookSnapshot`, `OrderBookUpdate`
- `Wall`
- `Pressure`
- `Signal = {NONE, LONG, SHORT}`
- `Position`
- `ExecutionReport`
- `LogLine`

---

## Ордербук (`domain/book.py`)

- Отсортированные массивы уровней на сторону + индекс цены→позиция на окне recent band
- Для уровня храним: `first_seen_ts`, `last_update_ts`, `min_qty_seen`, `max_qty_seen`, `qty`, `price`, `notional`
- Кольцевой буфер последних `last` для расчёта `recent band`
- Итераторы по recent band и поиск ближайшей стены на стороне стопа

---

## Детекторы (`domain/detectors.py`)

- `compute_odr(book, last_price, tick_size) -> Pressure` (веса `1/(1+ticks)`; сглаживание — медиана 3)
- `check_if_has_pressure(pressure, want_long) -> bool`
- `check_if_price_recent(level_price, recent_low, recent_high) -> bool`
- `check_if_is_large_wall(level, abs_usd_min, rel_mult_min, median_level_qty, persist_s, now) -> bool`
- `check_if_has_near_wall(book, side_stop) -> Wall | None`
- `check_if_has_opposite_wall(book, side_move, our_wall) -> bool`

---

## Адаптеры бирж (`data/exchanges/*.py`)

### Интерфейсы

- `IExchangeData`:
  - `fetch_symbol_filters() -> SymbolFilters`
  - `fetch_orderbook_snapshot() -> OrderBookSnapshot`  
  - `stream_depth() -> Iterator[OrderBookUpdate]`
  - `stream_book_ticker() -> Iterator[BestBidAsk]`
  - `stream_trades() -> Iterator[Trade]`
  - `stream_kline_1m() -> Iterator[Candle]`
  - Heartbeat/timeout: событие тишины при отсутствии данных дольше порога  
- `IExchangeTrade`:
  - `get_balance(source: BalanceSource) -> float`  
  - `set_leverage(leverage: int, margin_mode: MarginMode) -> None`  
  - `place_market(side, qty) -> ExecutionReport`
  - `place_stop_market(side, stop_price, qty, trigger: StopTrigger) -> None`  

### Ресинк и логирование

- Снапшот книги с `last_update_id`, применение diffs по последовательности. На разрыв — мгновенный ресинк.
- Логи:
  - Событие: `ЧЧ:ММ:СС Ресинк книги. Причина: пропуск последовательности.`

### Heartbeats/Reconn/Backpressure

- Ping/pong или серверный heartbeat
- Таймаут тишины по каждому стриму: `5 * LOOP_INTERVAL_MS`; по таймауту — перезапуск стрима, при неудаче — `RESYNC`
- Очереди приёма: при переполнении — `RESYNC` с логом

---

## Логирование (`data/logger.py`)

Формат: `ЧЧ:ММ:СС Сообщение`. 

---

## Telegram (`data/telegram.py`)

- Уведомления после событий
- Кулдаун на **сообщение «обнаружен аптик»** по символу: не чаще, чем раз в `TG_UPTICK_COOLDOWN_MIN` минут (дефолт 5). Если в эти 5 минут была торговля, перетяжка стопа или потеря аптика — сообщения **разрешены**. 

---

## Стратегия (`domain/strategy.py`)

Состояния: `SCANNING`, `FOCUSED`, `IN_POSITION`, `RESYNC`.

### SCANNING

- Каждые `MARKET_SCAN_INTERVAL_S` обновлять список монет по `turnover_1m`/`trades_1m`; динамически управлять подписками WS (добавлять активные, снимать неактивные).
- При достижении пред‑порогов ODR (`FOCUS_PRE_ODR_SHORT/LONG`) и наличии стены в recent band — `FOCUSED` на символ.

### FOCUSED

- Каждые 100–150 мс: ODR (медиана 3), ближайшая стена на стороне стопа, отсутствие встречной ≥ нашей.
- При `ODR_IN_*` и валидной стене — вход.
- Если 30 с нет признаков аптика — дефокус в `SCANNING`.
- Уведомление «обнаружен аптик» с кулдауном.

### IN_POSITION

- Вход: market **10% баланса**, но **≥ 10 USDT**; сразу stop‑market за стену на 1 тик с `STOP_TRIGGER`.
- Перетяжка: перестановка стены ≥1 тик и удержание ≥0.8 с → перенос стопа.
- Выход: стена исчезла (−50% и нет восстановления 600 мс) или ODR нейтрален `0.70–1.45` ≥ 800 мс.

### RESYNC

- На пропуск последовательности, тишину, переполнение очередей — `RESYNC`.
- Действия: остановить сигналы, получить снапшот, применить diffs, вернуться в предыдущее состояние.
- Логи событий ресинка + ежечасная сводка.

---

## Исполнение (`domain/execution.py`)

- На старте: `set_leverage(LEVERAGE, MARGIN_MODE)`
- Баланс: использовать `get_balance(BALANCE_SOURCE)`
- Размер позиции: `max(POSITION_FRACTION * balance, POSITION_MIN_USDT)` с округлением к `lotSize`
- Цены округлять к `tickSize`, стопы с `STOP_TRIGGER`

---

## Утилиты (`utils/`)

- `mathx.py`: медиана трёх, веса ODR, округления к шагам, расчёт размера позиции
- `timez.py`: `now_belgrade()`, конвертация штампов биржи в tz‑aware
- `formatting.py`: формат чисел для лога

---

## Запуск (`app.py`)

1. Загрузить `config`, `secrets`
2. Инициализировать адаптер биржи, получить `SymbolFilters`, установить `LEVERAGE`, `MARGIN_MODE`
3. Получить снапшот книги и запустить WS
4. Главный цикл: обновить данные → recent band → FSM (`SCANNING`/`FOCUSED`/`IN_POSITION`/`RESYNC`) → торговые действия → логи → Telegram (после)

---

## Производительность

- Доменные модели и массивы вместо словарей
- Кольцевые буферы recent band и ODR
- Маппинг JSON → модели явными функциями
- Динамический скан рынка каждые `MARKET_SCAN_INTERVAL_S`
- Короткие методы, без дублирования, минимум аллокаций

---

## Жёсткие правила стиля и запреты (обязательные)

- НИКАКИХ ТЕСТОВ.
- НИКАКИХ README.
- НИКАКИХ КОММЕНТАРИЕВ В КОДЕ.
- НИКАКИХ СЛОВАРЕЙ В ДОМЕНЕ И БИЗНЕС-ЛОГИКЕ; на границе парсинга JSON допускается только `TypedDict`.
- НИКАКИХ `hasattr`, `getattr`, `isinstance`, `try import` и прочей рефлексии.

## Именование и типизация

- Все переменные — существительные или `has_*` / `is_*`.
- Все функции/методы — глаголы, например: `check_if_has_pressure(...) -> bool`, `check_if_has_near_wall(...) -> bool`.
- Строгая типизация ВСЕГО: параметры, возвращаемые значения, локальные переменные, структуры хранения.
- Для внешних схем использовать `TypedDict`, во всём остальном — `@dataclass` и `Enum`.
- Все временные значения — `datetime` с таймзоной Europe/Belgrade.

## Ограничение по бирже

- Биржа выбирается в `config.EXCHANGE` и **в рамках одного запуска работает только ОДНА биржа**.
