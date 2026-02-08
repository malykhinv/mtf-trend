# mtf-trend

CLI-проект для загрузки данных, бектеста и отчетов по торговым стратегиям.

## Требования

- Python **3.10+**
- `pip`

## Быстрый старт

1. Создать и активировать virtual environment:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
```

2. Установить зависимости:

```bash
pip install --upgrade pip
pip install -e .
```

3. Настроить `.env` в корне проекта (минимум):

```env
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
COINGECKO_API_KEY=...
LOG_LEVEL=INFO
CACHE_DIR=./cache
TIMEZONE=Europe/Belgrade
```

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
python launcher.py --mode update-cache --top-n 100 --days 7
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
    { "mode": "fetch-cache", "top_n": 100, "days": 30 },
    { "mode": "update-cache", "top_n": 100, "days": 7 },
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
python main.py run-backtest
python main.py make-report
python main.py check-quality
```
