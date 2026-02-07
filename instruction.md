## **ИНСТРУКЦИЯ ДЛЯ AI-АГЕНТА: СИСТЕМА БЕКТЕСТИНГА ТОРГОВЫХ СТРАТЕГИЙ**

### **КОНЦЕПЦИЯ ПРОЕКТА**

Создать модульную CLI-систему для бектестирования торговых стратегий на фьючерсных рынках криптовалют с учетом сложных правил управления позицией (SL, TP1, TP2, BE).

---

### **1. АРХИТЕКТУРА И СТРУКТУРА ПРОЕКТА**

#### **Базовая структура:**

```
project/
├── .env
├── main.py
├── config.py
├── constants.py
├── cli/
│   ├── __init__.py
│   ├── commands.py
│   └── parser.py
├── domain/
│   ├── abstract/
│   │   ├── __init__.py
│   │   ├── exchange_client.py
│   │   ├── market_data_client.py
│   │   └── position_simulator.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── candle.py
│   │   ├── level.py
│   │   ├── breakout_event.py
│   │   ├── retest_event.py
│   │   ├── trade_signal.py
│   │   ├── position.py
│   │   ├── trade_result.py
│   │   ├── symbol_info.py
│   │   └── data_quality_issue.py
│   ├── enums/
│   │   ├── __init__.py
│   │   ├── timeframe.py
│   │   ├── level_type.py
│   │   ├── sl_mode.py
│   │   ├── trade_result_type.py
│   │   ├── exchange.py
│   │   ├── order_type.py
│   │   ├── position_side.py
│   │   └── data_quality_severity.py
│   └── value_objects/
│       ├── __init__.py
│       ├── price.py
│       ├── volume.py
│       └── percentage.py
├── data/
│   ├── exchanges/
│   │   ├── __init__.py
│   │   └── ccxt_futures_client.py
│   ├── storage/
│   │   ├── __init__.py
│   │   └── parquet_storage.py
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── market_data_fetcher.py
│   │   ├── ohlcv_fetcher.py
│   │   └── oi_fetcher.py
│   ├── clients/
│   │   ├── __init__.py
│   │   └── coingecko_client.py
│   └── quality/
│       ├── __init__.py
│       ├── data_validator.py
│       ├── gap_detector.py
│       ├── deduplicator.py
│       ├── time_alignment.py
│       └── oi_aligner.py
├── strategy/
│   ├── __init__.py
│   ├── base_strategy.py
│   └── breakout/
│       ├── __init__.py
│       ├── breakout_strategy.py
│       ├── config.py
│       └── indicators/
│           ├── __init__.py
│           ├── level_detector.py
│           ├── volume_analyzer.py
│           └── volatility_calculator.py
├── simulation/
│   ├── __init__.py
│   ├── position_simulator.py
│   ├── order_processor.py
│   └── trade_classifier.py
├── vectorbt_runner/
│   ├── __init__.py
│   ├── data_preparer.py
│   └── backtest_runner.py
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   ├── validators.py
│   └── formatters.py
├── cache/
└── logs/
```

---

### **2. КОНФИГУРАЦИЯ И ОКРУЖЕНИЕ**

#### **Файл `.env`:**

```
BINANCE_API_KEY=ключ
BINANCE_SECRET_KEY=секрет
COINGECKO_API_KEY=ключ
LOG_LEVEL=INFO
CACHE_DIR=./cache
TIMEZONE=Europe/Belgrade
MAX_CONCURRENT_REQUESTS=10
COMMISSION_RATE=0.0004
SLIPPAGE=0.0005
```

#### **Файл `config.py`:**

- Все конфигурационные классы как датаклассы
- Группировка: FetchConfig, StrategyConfig, SimulationConfig, BacktestConfig
- Настройки временных зон, комиссий, спредов
- Пути к данным и кэшам

#### **Файл `constants.py`:**

- Числовые и строковые константы
- Поддерживаемые таймфреймы: M1, M5, M15, M30, H1, H4, D1, W1
- Лимиты API, таймауты
- Коды ошибок, магические числа как константы

---

### **3. СЛОЙ DOMAIN - АБСТРАКЦИИ**

#### **3.1. `domain/abstract/exchange_client.py`:**

- Интерфейс для работы с биржами через CCXT
- Методы: fetch_ohlcv, fetch_open_interest, get_futures_symbols
- Абстракция для работы только с фьючерсами

#### **3.2. `domain/abstract/market_data_client.py`:**

- Интерфейс для получения капитализации с CoinGecko
- Методы: get_market_cap, get_top_coins_by_market_cap

#### **3.3. `domain/abstract/position_simulator.py`:**

- Интерфейс для симулятора позиций
- Методы: process_candle, open_position, close_position, update_stop

---

### **4. СЛОЙ DOMAIN - МОДЕЛИ**

#### **Каждый класс в отдельном файле:**

**`domain/models/candle.py`:**

- Датакласс Candle: timestamp, open, high, low, close, volume, open_interest
- Все поля строго типизированы
- Валидация значений (неотрицательные объемы)

**`domain/models/level.py`:**

- Датакласс Level: price, level_type, formation_time, lookback, shadow_ratio

**`domain/models/breakout_event.py`:**

- Датакласс BreakoutEvent: level, breakout_time, breakout_price, volume_before, volume_after, oi_value

**`domain/models/retest_event.py`:**

- Датакласс RetestEvent: breakout_event, retest_time, retest_price, volume_retest, oi_retest

**`domain/models/trade_signal.py`:**

- Датакласс TradeSignal: entry_price, entry_time, stop_loss, take_profit_1, take_profit_2, position_side, symbol

**`domain/models/position.py`:**

- Датакласс Position: entry_price, entry_time, size, stop_loss, take_profit_1, take_profit_2, tp1_done, sl_moved_to_be

**`domain/models/trade_result.py`:**

- Датакласс TradeResult: entry_price, exit_price, entry_time, exit_time, result_type, pnl, pnl_percent

**`domain/models/symbol_info.py`:**

- Датакласс SymbolInfo: symbol, market_cap, daily_volume, is_active

**`domain/models/data_quality_issue.py`:**

- Датакласс DataQualityIssue: symbol, timeframe, issue_type, severity, timestamp, description

---

### **5. СЛОЙ DOMAIN - ПЕРЕЧИСЛЕНИЯ**

#### **Каждый enum в отдельном файле:**

**`domain/enums/timeframe.py`:**

- Timeframe: M1, M5, M15, M30, H1, H4, D1, W1

**`domain/enums/level_type.py`:**

- LevelType: SUPPORT, RESISTANCE

**`domain/enums/sl_mode.py`:**

- SLMode: RETEST_EXTREME, LEVEL, BREAKOUT_EXTREME

**`domain/enums/trade_result_type.py`:**

- TradeResultType: SL, BE, TP1_BE, TP2

**`domain/enums/exchange.py`:**

- Exchange: BINANCE, BYBIT, OKX

**`domain/enums/order_type.py`:**

- OrderType: MARKET, LIMIT

**`domain/enums/position_side.py`:**

- PositionSide: LONG, SHORT

**`domain/enums/data_quality_severity.py`:**

- DataQualitySeverity: INFO, WARNING, ERROR, CRITICAL

---

### **6. СЛОЙ DATA - РЕАЛИЗАЦИИ**

#### **6.1. `data/exchanges/ccxt_futures_client.py`:**

- Реализация ExchangeClient только для фьючерсов
- Использование CCXT с автоматическим лимитом запросов
- Поддержка Binance Futures, Bybit Futures, OKX Futures

#### **6.2. `data/storage/parquet_storage.py`:**

- Хранение данных в Parquet с инкрементальным обновлением
- Организация: `{символ}/{таймфрейм}/data.parquet`
- Кэширование капитализации

#### **6.3. `data/fetchers/market_data_fetcher.py`:**

- Координация загрузки OHLCV, OI, капитализации
- Параллельная загрузка с учетом лимитов
- Логирование прогресса

#### **6.4. `data/clients/coingecko_client.py`:**

- Реализация MarketDataClient для CoinGecko
- Кэширование капитализации на 24 часа

#### **6.5. `data/quality/` - слой контроля качества данных:**

- `data_validator.py` - проверка целостности данных
- `gap_detector.py` - обнаружение пропущенных свечей
- `deduplicator.py` - удаление дубликатов по времени
- `time_alignment.py` - выравнивание временных меток по UTC
- `oi_aligner.py` - синхронизация OI с временными метками свечей

---

### **7. СЛОЙ STRATEGY**

#### **7.1. `strategy/base_strategy.py`:**

- Абстрактный базовый класс
- Методы: prepare_data, generate_events, validate_config

#### **7.2. `strategy/breakout/breakout_strategy.py`:**

- Реализация стратегии пробоя уровней
- Логика: определение уровней → пробои → ретесты → сигналы
- Конфигурируемые параметры из config.py

#### **7.3. `strategy/breakout/indicators/`:**

- Детекторы уровней, анализаторы объема, калькуляторы волатильности
- Каждый индикатор в отдельном классе

---

### **8. СЛОЙ SIMULATION - СИМУЛЯТОР ПОЗИЦИЙ**

#### **8.1. `simulation/position_simulator.py`:**

- Stateful симулятор позиций (Вариант A из требований)
- Обработка каждой свечи с учетом OHLC
- Правила приоритетов: SL-first (консервативный подход)
- Учет комиссий и проскальзывания в BE
- Управление частичным закрытием (TP1 → 50%, остаток → BE → TP2)

#### **8.2. `simulation/order_processor.py`:**

- Обработка ордеров в рамках симуляции
- Логика: market order по open следующей свечи
- Учет спреда и комиссий

#### **8.3. `simulation/trade_classifier.py`:**

- Классификация результата сделки: SL, BE, TP1_BE, TP2
- Определение достижения целей с учетом приоритетов

---

### **9. СЛОЙ VECTORTB_RUNNER**

#### **9.1. `vectorbt_runner/data_preparer.py`:**

- Преобразование результатов симуляции в формат для vectorbt
- Создание DataFrames с колонками для метрик

#### **9.2. `vectorbt_runner/backtest_runner.py`:**

- Запуск vectorbt для расчета метрик
- Генерация сетки параметров (5,832 комбинации)
- Сохранение результатов в CSV

---

### **10. СЛОЙ CLI - КОМАНДНЫЙ ИНТЕРФЕЙС**

#### **10.1. Команды:**

```
fetch-data      # Загрузка данных с бирж и CoinGecko
update-cache    # Обновление кэша (капитализация, инкрементальные данные)
run-backtest    # Запуск бектеста с текущими настройками
make-report     # Генерация отчета для нейросети
check-quality   # Проверка качества данных
```

#### **10.2. `cli/parser.py`:**

- Парсинг аргументов командной строки
- Валидация входных параметров
- Маршрутизация к соответствующим командам

#### **10.3. `cli/commands.py`:**

- Реализация каждой команды как отдельный класс/функция
- Логирование начала и окончания выполнения
- Обработка ошибок

---

### **11. СЛОЙ UTILS**

#### **11.1. `utils/logger.py`:**

- Формат: `14:30:00 Загружено 300 монет.`
- Уровни: DEBUG, INFO, WARNING, ERROR
- Запись в файл с ротацией
- Цветное отображение в консоли (только для INFO и выше)

#### **11.2. `utils/validators.py`:**

- Валидация конфигурации
- Проверка API ключей
- Валидация диапазонов параметров

#### **11.3. `utils/formatters.py`:**

- Форматирование чисел, процентов, временных меток
- Преобразование между форматами данных

---

### **12. КЛЮЧЕВЫЕ ТРЕБОВАНИЯ К РЕАЛИЗАЦИИ**

#### **12.1. Работа только с фьючерсами:**

- Все символы должны быть фьючерсами USDT-M
- Проверка через CCXT: `exchange.has['fetchOHLCV']` и `exchange.has['fetchOpenInterest']`
- Исключение спотовых пар на этапе загрузки

#### **12.2. Управление временем:**

- Все datetime объекты с таймзоной Europe/Belgrade
- Конвертация в UTC для хранения
- Единая точка настройки таймзоны в config.py

#### **12.3. Контроль качества данных:**

- Обнаружение и логирование пропущенных свечей
- Выравнивание OI по временным меткам свечей
- Дедупликация данных
- Валидация: отрицательные цены, нулевые объемы, аномальные спреды

#### **12.4. Симуляция позиций:**

- Stateful симулятор с обработкой каждой свечи
- Правила приоритетов: SL-first для консервативности
- BE = entry_price + комиссии + проскальзывание
- Частичное закрытие: 50% на TP1, остаток со стопом на BE
- Результаты: SL, TP1_BE, TP2

#### **12.5. Кэширование капитализации:**

- Загрузка с CoinGecko при первом запуске fetch-data
- Хранение в Parquet в кэше

#### **12.6. Логирование:**

- Формат: `ЧЧ:ММ:СС Сообщение`
- Пример: `14:30:00 Загружено 300 монет.`
- Без эмодзи, без лишних символов
- Прогресс для длительных операций

#### **12.7. Типизация и структура:**

- Строгая типизация (как в Kotlin)
- Каждый класс в отдельном файле
- Датаклассы вместо словарей
- Приватные методы в #region Private
- Енамы для всех типизированных строк

---

### **13. ПОТОК ВЫПОЛНЕНИЯ**

#### **13.1. Команда `fetch-data`:**

1. Загрузка списка топ N монет по капитализации (CoinGecko)
2. Фильтрация: только фьючерсы USDT-M
3. Параллельная загрузка OHLCV данных через CCXT
4. Загрузка Open Interest для каждого символа
5. Применение контроля качества:
   - Дедупликация
   - Обнаружение пропусков
   - Выравнивание OI
   - Конвертация времени в UTC
6. Сохранение в кэш (Parquet)
7. Кэширование капитализации

#### **13.2. Команда `update-cache`:**

1. Проверка актуальности кэша капитализации
2. Инкрементальная загрузка новых свечей
3. Обновление данных за последние N дней
4. Повторный контроль качества

#### **13.3. Команда `run-backtest`:**

1. Загрузка данных из кэша
2. Запуск стратегии для подготовки событий
3. Генерация сетки параметров (5,832 комбинации)
4. Для каждой комбинации:
   - Симуляция позиций через PositionSimulator
   - Классификация результатов сделок
   - Расчет метрик
5. Сохранение результатов в CSV

#### **13.4. Команда `make-report`:**

1. Загрузка результатов бектеста
2. Фильтрация: минимум 30 сделок, PF > 1.0
3. Сортировка по Profit Factor
4. Агрегация статистики по параметрам
5. Генерация компактного отчета для нейросети

#### **13.5. Команда `check-quality`:**

1. Проверка целостности всех данных в кэше
2. Обнаружение пропусков, дубликатов, аномалий
3. Генерация отчета о качестве данных
4. Рекомендации по исправлению

---

### **14. ПРАВИЛА СИМУЛЯЦИИ (КРИТИЧЕСКИ ВАЖНО)**

#### **14.1. Открытие позиции:**

- Вход: market order на open следующей свечи после сигнала
- Цена входа: open + спред + проскальзывание
- Комиссия: 0.05% за вход и 0.05% за выход

#### **14.2. Приоритеты обработки свечи (для LONG):**

1. Если high ≥ TP2 → TP2 (полное закрытие)
2. Если high ≥ TP1 и TP1 не взят ранее:
   - Закрыть 50% позиции по TP1
   - Переместить SL оставшейся части в BE
   - Отметить tp1_done = True
3. Если low ≤ SL → SL (полное закрытие)
4. Если low ≤ BE и tp1_done = True → TP1_BE

#### **14.3. Определение BE:**

- BE = entry_price + комиссия_входа + комиссия_выхода + проскальзывание
- Для SHORT: BE = entry_price - комиссия_входа - комиссия_выхода - проскальзывание

#### **14.4. Спорные ситуации (одна свеча задела несколько уровней):**

- Консервативный подход: SL-first
- Если свеча задела и SL, и TP → считается SL
- Если свеча задела и BE, и TP2 → считается BE

---

### **15. ФОРМАТ ВЫХОДНЫХ ДАННЫХ**

#### **15.1. Результаты бектеста (CSV):**

```
lookback,volume_mult,retest_window,retest_zone,min_rr,sl_mode,tp2_mult,
profit_factor,pnl_percent,win_rate,trades_count,max_dd,
sl_count,be_count,tp1_be_count,tp2_count
```

#### **15.2. Отчет для нейросети (JSON):**

```json
{
  "summary": {
    "total_combinations": 5832,
    "profitable_combinations": 1850,
    "best_pf": 2.15
  },
  "optimal_ranges": {
    "lookback": [13, 21],
    "volume_multiplier": [1.5, 2.0]
  },
  "trade_results_distribution": {
    "SL": 35,
    "BE": 15,
    "TP1_BE": 30,
    "TP2": 20
  }
}
```

#### **15.3. Логи выполнения:**

```
14:30:00 Начало загрузки данных.
14:31:23 Загружено 300 монет с Binance Futures.
14:32:45 Применен контроль качества: исправлено 15 пропущенных свечей.
15:00:12 Завершена симуляция для 1000 комбинаций параметров.
15:02:34 Сгенерирован отчет с топ-10 комбинациями.
```

---

### **16. ДЛЯ AI-АГЕНТА**

Создать систему, которая:

1. **Следует строгой архитектуре** с четким разделением слоев
2. **Использует CLI** с командами: fetch-data, update-cache, run-backtest, make-report, check-quality
3. **Работает только с фьючерсами** через CCXT
4. **Включает слой контроля качества** данных
5. **Реализует stateful симулятор позиций** с правилами SL, TP1, TP2, BE
6. **Кэширует капитализацию** с CoinGecko
7. **Использует строгую типизацию** и датаклассы
8. **Логирует в указанном формате** без эмодзи
9. **Обрабатывает время** в Europe/Belgrade с конвертацией в UTC
10. **Генерирует отчеты** для анализа нейросетью

Каждый компонент должен быть максимально независимым, заменяемым и следовать принципам чистой архитектуры.
