# mtf-trend

CLI-проект для загрузки данных, бектеста и отчетов по торговым стратегиям.

## README-структура

В проекте используется 3 README-файла:

1. `README.md` (этот файл) — общий запуск, инфраструктура, launcher/CLI.
2. `strategy/breakout/README.md` — стратегия `retest` (включая alias `breakout`).
3. `strategy/bee_bite/README.md` — стратегия `bee_bite`.

Если нужна стратегия-специфика, в первую очередь смотри README внутри соответствующей папки стратегии.

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
```
> Таймфреймы для сборки кэша задаются в коде конфигурации (`config/fetch_config.py`, поле `FetchConfig.timeframes`),
> а не через параметры CLI и не через `.env`.

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

Если нужно ограничить набор для анализа по ликвидности, можно добавить `--top-n`:

```bash
--mode analyze-cache --top-n 50
```

Для `analyze-cache` отбор по `--top-n` выполняется по среднему объёму свечей (`volume`) на `levels_tf` (старший ТФ).
Чтобы сразу строить графики сделок с точкой входа, TP1, TP2, SL и финалом сделки, добавь `--plot true` (изображения сохраняются в `cache/results/trade_plots`, можно переопределить через `--output-dir`).
Приоритеты такие:
- если одновременно переданы `--symbols` и `--top-n`, ранжирование выполняется только внутри списка `--symbols`;
- если `--symbols` не переданы, ранжирование по `--top-n` выполняется по всем символам из кэша.

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
--mode make-report --input ./.output/results/strategy/retest/results.csv --output ./cache/results/report.json
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
- проверка качества кэша;
- построение дневных уровней;
- построение ретестов.

### 2) Запуск конкретного режима через параметры

```bash
python launcher.py --mode fetch-cache --top-n 100 --days 30
# или без CoinGecko:
python launcher.py --mode fetch-cache --top-n 100 --days 30 --ignore-coingecko
python launcher.py --mode update-cache --top-n 100 --days 7
python launcher.py --mode update-cache --top-n 100 --days 7 --ignore-coingecko
python launcher.py --mode analyze-cache --symbols BTC/USDT ETH/USDT
python launcher.py --mode analyze-cache --top-n 50
python launcher.py --mode analyze-cache --top-n 50 --plot true
python launcher.py --mode analyze-cache --symbols BTC/USDT ETH/USDT --plot-from-results --results-input ./cache/results/results.csv
python launcher.py --mode analyze-cache --plot-from-results --results-input ./cache/results/strategy/retest/results.csv --id 1156 --strategy retest
python launcher.py --mode analyze-cache --top-n 50 --strategy retest
python launcher.py --mode analyze-cache --top-n 50 --strategy bee_bite
python launcher.py --mode analyze-cache --symbols BTC/USDT --strategy bee_bite --plot-from-results --results-input ./cache/results/results.csv
python launcher.py --mode make-report --input ./results/results.csv --output ./results/report.json
python launcher.py --mode check-quality --symbols BTC/USDT ETH/USDT --output ./results/quality_report.json
python launcher.py --mode plot-daily-levels --symbols BTC/USDT ETH/USDT --levels-tf 1d --entry-tf 15m --output-dir ./results/charts --limit 300
python launcher.py --mode plot-retests --symbols BTC/USDT ETH/USDT --levels-tf 1d --entry-tf 15m --output-dir ./results/charts --limit 100
python launcher.py --mode clear-cache
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
    { "mode": "analyze-cache", "symbols": ["BTC/USDT", "ETH/USDT"], "plot": true },
    { "mode": "make-report", "output": "./results/report.json" },
    { "mode": "check-quality", "output": "./results/quality_report.json" },
    {
      "mode": "plot-daily-levels",
      "symbols": ["BTC/USDT", "ETH/USDT"],
      "levels_tf": "1d",
      "entry_tf": "15m",
      "output_dir": "./results/charts",
      "limit": 300
    },
    {
      "mode": "plot-retests",
      "symbols": ["BTC/USDT"],
      "levels_tf": "1d",
      "entry_tf": "15m",
      "output_dir": "./results/charts",
      "limit": 100
    }
  ]
}
```

Запуск:

```bash
python launcher.py --config run_config.json
```



### Выбор стратегии (`retest` / `bee_bite`)

По умолчанию используется `retest`. Можно задать через `.env`:

Ограничение: `bee_bite` — long-only (SHORT-входы не поддерживаются).

```env
STRATEGY_ID=bee_bite
```

Или переопределить на конкретный запуск через CLI (имеет приоритет над `.env`):

```bash
python main.py run-backtest --strategy retest
python main.py run-backtest --strategy bee_bite
```

`--plot-from-results` автоматически читает нужные колонки под выбранную стратегию:
- `retest` — поля `lookback`, `volume_mult`, ...
- `bee_bite` — поля `bite_lookback`, `bite_volume_mult`, ... (`bite_volume_mult` реально участвует во входе: фильтр аномального объёма в `SEEK_PUMP`).

Опционально можно выбрать конкретную комбинацию через `--id`:
- сначала ищется точное совпадение в колонках `id` / `combination_id` / `rank`;
- если таких колонок нет — `--id` трактуется как 1-based номер строки в CSV.

`--strategy breakout` поддерживается как alias для `retest`.

Для `bee_bite` доступны профиль, сетка и runtime-режимы:
- `--bee-bite-profile {A,B,C}` — фиксированный baseline-профиль;
- `--bee-bite-grid {baseline,expanded,research}` — baseline (узкий), expanded (широкий) или research (максимально широкий) вокруг baseline;
- `--bee-bite-reclaim-mode {strict,balanced,aggressive}` — меняет reclaim-offset и лимит ожидания reclaim в **барах 15m** внутри движка;
- `--bee-bite-retest-mode {confirmation,immediate}` — выбирает механику входа (подтверждение или мгновенный вход после reclaim);
- `--bee-bite-cooldown-hours` и `--bee-bite-max-age-range-hours` задаются в **часах**, затем внутри портфельного движка переводятся в бары 15m; в `backtest_results.csv` сохраняются как `bite_cooldown_hours`/`bite_max_age_range_hours` (старые алиасы CLI сохранены для совместимости).

Переменные окружения для `bee_bite`:

```env
BEE_BITE_PROFILE=A
BEE_BITE_GRID_MODE=baseline
BEE_BITE_COOLDOWN_HOURS=8
BEE_BITE_MAX_AGE_RANGE_HOURS=12
```

### Фиксация конца периода загрузки (повторяемые прогоны)

Можно зафиксировать «текущий момент» для `fetch-data` и `update-cache`, чтобы получать повторяемые выборки:

```env
FETCH_ANCHOR_TIMESTAMP_MS=1738367999000
```

Тогда параметр `--days` будет отсчитываться назад именно от `FETCH_ANCHOR_TIMESTAMP_MS`, а не от текущего времени.

Дополнительно можно переопределить это значение через CLI (приоритет выше env):

```bash
python main.py fetch-data --top-n 100 --days 30 --end-timestamp-ms 1738367999000
python main.py update-cache --top-n 100 --days 7 --end-timestamp-ms 1738367999000
```

Все временные метки — только `int` unix ms от биржи, без преобразований.

### Правила контракта времени

- **Вход:** принимаем только `int` unix ms, полученные от биржи.
- **Хранение:** сохраняем только `int` unix ms от биржи, без преобразований.
- **Сравнение:** любые операции по времени (сортировка, фильтры, окна) выполняются только по `int` unix ms.

## Базовые CLI-команды (старый способ)

```bash
python main.py fetch-data --top-n 100 --days 30
python main.py fetch-data --top-n 100 --days 30 --ignore-coingecko
python main.py update-cache --top-n 100 --days 7 --ignore-coingecko

# При --ignore-coingecko команда автоматически использует bootstrap mode (без фильтра ликвидности),
# если кэш объёмов ещё не прогрет; после прогрева применяется cache-liquidity mode.
python main.py run-backtest --top-n 50
python main.py run-backtest --symbols BTC/USDT ETH/USDT --top-n 2
python main.py make-report

# Для run-backtest отбор по --top-n идёт по среднему объёму свечей (volume) на levels_tf.
# Приоритеты: с --symbols ранжирование только внутри переданного списка,
# без --symbols — по всем символам, найденным в кэше.
python main.py check-quality
python main.py clear-cache
```

## Параметры retest-стратегии

По умолчанию грид `retest_window_hours` для перебора параметров: `12, 24, 36, 48` (в часах).

## Сценарии запуска Bee Bite

Узкий baseline (один валидный профиль):

```bash
python main.py run-backtest --strategy bee_bite --bee-bite-profile A --bee-bite-grid baseline --top-n 50
```

Широкая controlled-сетка вокруг профиля:

```bash
python main.py run-backtest --strategy bee_bite --bee-bite-profile A --bee-bite-grid expanded --top-n 100
python main.py run-backtest --strategy bee_bite --bee-bite-profile C --bee-bite-grid research --top-n 120
```
