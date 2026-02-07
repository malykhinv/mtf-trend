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

## Базовые CLI-команды

```bash
python main.py fetch-data --top-n 100 --days 30
python main.py run-backtest
python main.py make-report
python main.py check-quality
```
