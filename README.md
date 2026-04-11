# PNO

Этот репозиторий сфокусирован только на стратегии `PNO`.

## Что внутри

- загрузка и обновление фьючерсного кэша Binance
- бэктест `PNO`
- stage-диагностика `PNO` (`1 -> 5`)
- исследовательские артефакты, графики и контекст по сделкам

## Быстрый старт

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

Минимальный `.env`:

```env
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
LOG_LEVEL=INFO
CACHE_DIR=./.output/cache
RESULTS_DIR=./.output/results
```

## Основные команды

Обновить кэш:

```bash
python main.py fetch-data --top-n 100 --days 30 --timeframes 5m 1m --skip-open-interest
python main.py update-cache --days 7 --timeframes 1m --skip-open-interest
python main.py update-cache --days 7 --timeframes 5m
```

Запустить PNO-бэктест:

```bash
python main.py run-backtest --strategy pno --levels-tf 5m --entry-tf 1m --days 30
python main.py run-backtest --strategy pno --levels-tf 5m --entry-tf 1m --days 30 --plot true
python main.py run-backtest --strategy pno --levels-tf 5m --entry-tf 1m --days 30 --pno-entry-confirmation-mode close_above
```

Запустить stage-диагностику:

```bash
python main.py pno-stage s1 --levels-tf 5m --entry-tf 1m --days 30
python main.py pno-stage s4 --levels-tf 5m --entry-tf 1m --days 30 --output-dir .output/results/ad_hoc/pno_stage4_last30d
python main.py pno-stage s5 --levels-tf 5m --entry-tf 1m --days 30 --output-dir .output/results/ad_hoc/pno_stage5_last30d
```

Служебные команды:

```bash
python main.py check-quality
python main.py clear-cache
```

## Структура результата

- `strategy/pno/results.csv` — комбинации и метрики
- `strategy/pno/trades.csv` — сделки
- `pno_diagnostics/charts` — итоговые графики сделок
- `pno_diagnostics/stage_reviews` — stage `passed/rejected`
- `pno_diagnostics/research_context` — исследовательские CSV
