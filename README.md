# mtf-trend

CLI-проект для загрузки данных, бектеста и отчетов по торговым стратегиям.

## Требования

- Python **3.10+**
- `pip`

## Быстрый старт

1. Создать и активировать virtual environment:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Установить зависимости:

```bash
python -m pip install --upgrade pip
pip install -e .
```

3. Настроить `.env` в корне проекта (минимум):

```env
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
COINGECKO_API_KEY=...
COINGECKO_MIN_REQUEST_INTERVAL_SECONDS=2.1
COINGECKO_VOLUME_BATCH_SIZE=40
IGNORE_COINGECKO=false
LOG_LEVEL=INFO
CACHE_DIR=./cache
TIMEZONE=Europe/Belgrade
```

## Гайд «для чайников»: как запускать бота в PyCharm

Ниже — самый простой сценарий без терминальных «танцев с бубном». Идея: вы запускаете `launcher.py` с разными режимами.

### 0) Один раз настроить запуск в PyCharm

1. Открой проект в PyCharm.
2. Убедись, что выбран интерпретатор из `.venv`.
3. Открой **Run | Edit Configurations…**.
4. Нажми **+** → **Python**.
5. Заполни:
   - **Name**: `MTF Launcher`
   - **Script path**: `.../mtf-trend/launcher.py`
   - **Working directory**: `.../mtf-trend`
   - **Parameters**: (будешь менять под задачу, примеры ниже)
6. Сохрани конфигурацию.

Теперь тебе нужно менять только поле **Parameters** и нажимать ▶ Run.

---

### 1) Как собрать кэш

Это первый шаг: бот скачает свечи/данные и положит их в папку кэша.

В **Parameters** вставь:

```bash
--mode fetch-cache --top-n 100 --days 30
# или без CoinGecko:
--mode fetch-cache --top-n 100 --days 30 --ignore-coingecko
```

Что это значит простыми словами:
- `fetch-cache` — собрать кэш «с нуля»;
- `--top-n 100` — взять 100 монет;
- `--days 30` — загрузить последние 30 дней.

Куда сохраняется: по умолчанию в `./cache`.

---

### 2) Как проверить стратегию на кэше (бектест)

После сборки кэша запусти анализ стратегии на этих данных.

В **Parameters**:

```bash
--mode analyze-cache
```

Если хочешь проверить только конкретные монеты:

```bash
--mode analyze-cache --symbols BTC/USDT ETH/USDT
```

Результат бектеста сохраняется в CSV (по умолчанию):

`cache/results/backtest_results.csv`

---

### 3) Как обработать результат

CSV неудобно читать «глазами», поэтому делаем готовый JSON-отчет.

В **Parameters**:

```bash
--mode make-report
```

Или вручную указать вход/выход:

```bash
--mode make-report --input ./cache/results/backtest_results.csv --output ./cache/results/report.json
```

---

### 4) Как увидеть итоги

Есть 2 простых варианта:

1. **Быстро в PyCharm**
   - открой `cache/results/backtest_results.csv`
   - открой `cache/results/report.json`
   - в `report.json` смотри блоки `summary`, `optimal_parameter_ranges`, `trade_results_distribution`.

2. **Проверить качество кэша (полезно, если результаты странные)**

В **Parameters**:

```bash
--mode check-quality
```

Отчет качества: `cache/results/quality_report.json`.

---

### Супер-короткий порядок действий

1. `--mode fetch-cache --top-n 100 --days 30`
2. `--mode analyze-cache`
3. `--mode make-report`
4. Открыть `cache/results/backtest_results.csv` и `cache/results/report.json`

## Конфигурация

Публичная точка входа для конфигурации — корневой `config.py` (например, `from config import AppConfig, load_config`).
Папка `config/` сохранена как внутренняя реализация с отдельными модулями конфигурации.

## Запуск без ручного ввода CLI-команд

Добавлен `launcher.py`: можно запускать режим по параметрам или через JSON-конфиг сценария.

### 1) Интерактивный запуск (выбор режима по номеру)

```bash
python launcher.py
```

Доступные режимы:
- сбор кэша;
- обновление кэша;
- анализ кэша стратегией (бектест);
- построение отчета;
- проверка качества кэша.

### 2) Запуск конкретного режима через параметры

```bash
python launcher.py --mode fetch-cache --top-n 100 --days 30
# или без CoinGecko:
python launcher.py --mode fetch-cache --top-n 100 --days 30 --ignore-coingecko
python launcher.py --mode update-cache --top-n 100 --days 7
python launcher.py --mode update-cache --top-n 100 --days 7 --ignore-coingecko
python launcher.py --mode analyze-cache --symbols BTC/USDT ETH/USDT
python launcher.py --mode make-report --input ./results/backtest_results.csv --output ./results/report.json
python launcher.py --mode check-quality --symbols BTC/USDT ETH/USDT --output ./results/quality_report.json
```

### 3) Запуск цепочки задач через JSON-конфиг

Пример `run_config.json`:

```json
{
  "env_path": ".env",
  "continue_on_error": false,
  "tasks": [
    { "mode": "fetch-cache", "top_n": 100, "days": 30, "ignore_coingecko": true },
    { "mode": "update-cache", "top_n": 100, "days": 7, "ignore_coingecko": false },
    { "mode": "analyze-cache", "symbols": ["BTC/USDT", "ETH/USDT"] },
    { "mode": "make-report", "output": "./results/report.json" },
    { "mode": "check-quality", "output": "./results/quality_report.json" }
  ]
}
```

Запуск:

```bash
python launcher.py --config run_config.json
```

## Базовые CLI-команды (старый способ)

```bash
python main.py fetch-data --top-n 100 --days 30
python main.py fetch-data --top-n 100 --days 30 --ignore-coingecko
python main.py update-cache --top-n 100 --days 7 --ignore-coingecko
python main.py run-backtest
python main.py make-report
python main.py check-quality
```

## Параметры breakout-стратегии

По умолчанию грид `retest_window_hours` для перебора параметров: `12, 24, 36, 48` (в часах).
