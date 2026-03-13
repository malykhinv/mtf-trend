# mtf-trend

Проект переведен в режим `bee_bite` only.

## Что осталось в проекте

- загрузка и обновление кэша
- backtest стратегии `bee_bite`
- stage-1 отбор монет для `bee_bite`
- генерация отчета
- проверка качества данных
- `launcher.py` и `main.py` работают только с `bee_bite`

Удалены legacy-стратегия, ее alias и связанные legacy-команды визуализации.

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
COINGECKO_API_KEY=...
LOG_LEVEL=INFO
CACHE_DIR=./cache
RESULTS_DIR=./results
STRATEGY_ID=bee_bite
```

## Основные команды

```bash
python main.py fetch-data --top-n 100 --days 30
python main.py update-cache --top-n 100 --days 7
python main.py run-backtest --strategy bee_bite --top-n 50
python main.py make-report --input ./results/backtest_results.csv --output ./results/report.json
python main.py check-quality
python main.py clear-cache
```

Через launcher:

```bash
python launcher.py --mode fetch-cache --top-n 100 --days 30
python launcher.py --mode update-cache --top-n 100 --days 7
python launcher.py --mode analyze-cache --strategy bee_bite --top-n 50
python launcher.py --mode make-report --input ./results/backtest_results.csv --output ./results/report.json
python launcher.py --mode check-quality
python launcher.py --mode clear-cache
```

## Bee Bite

Поддерживаемые runtime-параметры:

- `--bee-bite-grid {baseline,expanded,research}`
- `--bee-bite-reclaim-mode {strict,balanced,aggressive}`
- `--bee-bite-cooldown-hours`
- `--bee-bite-max-age-range-hours`

Примеры:

```bash
python main.py run-backtest --strategy bee_bite --bee-bite-grid baseline --top-n 50
python main.py run-backtest --strategy bee_bite --bee-bite-grid expanded --top-n 100
python main.py run-backtest --strategy bee_bite --bee-bite-grid research --top-n 120
```

## Stage 1 фильтр

Для `bee_bite` отбор монет в backtest идет по правилам:

1. `24h` объем не ниже `20M USDT`
2. на `15m` был памп `20%+`
3. после пампа монета удержала не меньше `0.5` высоты пампа

Если передан `--top-n`, лимит применяется уже после stage-1 фильтра.

## Диагностика

Для `bee_bite` доступен `--plot true` и `--plot-from-results`. Вместо старых trade-plot графиков сохраняются диагностические JSON-файлы по символам.

## Примеры

- стратегия: [strategy/bee_bite/README.md](/C:/Users/Ascf/PycharmProjects/mtf-trend-2/strategy/bee_bite/README.md)
- контрольные кейсы: [examples/bee_bite_checkpoints.csv](/C:/Users/Ascf/PycharmProjects/mtf-trend-2/examples/bee_bite_checkpoints.csv)
