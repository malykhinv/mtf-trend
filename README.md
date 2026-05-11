# PNO Strategy Research

Репозиторий для исследования и проверки PNO-стратегии `pump -> pullback -> reclaim`.

PNO здесь не считается готовой торговой системой. Это исследовательский стенд для проверки гипотезы:

```text
real pump -> active high -> healthy pullback -> BOS / level reclaim -> close_above -> executable entry -> TP1 active high -> runner
```

Главный принцип работы:

```text
сначала качество данных и диагностика, потом edge, только потом оптимизация параметров
```

## Что делает проект

- загружает и обновляет локальный Binance futures cache;
- запускает PNO backtest на кэшированных данных;
- строит stage diagnostics по pipeline Stage1..Stage5;
- экспортирует rejected/passed review rows, research context и position charts;
- сравнивает entry timeframe sets для проверки latency входа;
- хранит рабочую память исследования в `research/*.md`.

## Что PNO пытается поймать

PNO ищет не любой откат после пампа, а момент, когда откат после реального импульса ломается вверх.

Pipeline:

```text
Stage1: Pump
Stage2: High Pullback
Stage3: Valid Pullback
Stage4: Level / BOS Setup
Stage5: Position
```

Primary entry mode:

```text
close_above
```

Недостаточно:

```text
wick touch
high >= level
покупка падающего отката без reclaim
```

TP1 — active high. Если active high уже достигнут до executable entry, setup часто invalid.

## Быстрый старт

Python: `>=3.10`.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Windows PowerShell:

```powershell
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

## Подготовка данных

Для текущих PNO TF-set нужны `5m`, `1m`, `30s`, `15s`, `5s`.

Пример первичной загрузки:

```bash
python main.py fetch-data --top-n 500 --days 30 --timeframes 5m 1m 30s 15s 5s --skip-open-interest
```

Пример инкрементального обновления:

```bash
python main.py update-cache --top-n 500 --days 7 --timeframes 5m 1m 30s 15s 5s --skip-open-interest
```

Проверка качества кэша:

```bash
python main.py check-quality
```

## PNO backtest

Текущие backtest TF-set:

```text
5m / 30s
5m / 15s
1m / 5s
```

Запуск всех PNO TF-set одним прогоном:

```bash
python main.py run-backtest --strategy pno --pno-all-tf-pairs --days 3 --top-n 500 --pno-category-mode discovery --collect-diagnostics true --plot-rejected true
```

Запуск одной пары:

```bash
python main.py run-backtest --strategy pno --levels-tf 5m --entry-tf 30s --days 3 --top-n 500 --pno-category-mode discovery --collect-diagnostics true --plot-rejected true
```

Быстрый lightweight-прогон без полной диагностики:

```bash
python main.py run-backtest --strategy pno --pno-all-tf-pairs --days 3 --top-n 50 --light-run true
```

## Stage diagnostics

Один stage:

```bash
python main.py pno-stage s4 --levels-tf 5m --entry-tf 30s --top-n 100 --pno-category-mode discovery --output-dir .output/results/ad_hoc/pno_stage4
```

Cumulative stages through Stage5:

```bash
python main.py pno-stage through5 --levels-tf 5m --entry-tf 30s --top-n 100 --pno-category-mode discovery --output-dir .output/results/ad_hoc/pno_through5
```

Stage presets:

```text
s1..s5 / stage1..stage5      one stage
t1..t5 / through1..through5  cumulative 1..N
```

## Артефакты

Обычные результаты:

```text
.output/results/strategy/pno/results.csv
.output/results/strategy/pno/positions.csv
```

Диагностика:

```text
pno_diagnostics/stage_reviews/
pno_diagnostics/research_context/
pno_diagnostics/charts/
```

Для saved backtest run используется отдельный run root:

```text
.output/results/backtest_runs/<timestamp>_pno/
```

Графики по сохранённому run можно перестроить:

```bash
python main.py plot-backtest --run-dir .output/results/backtest_runs/<timestamp>_pno
```

## Research memory

Перед нетривиальными изменениями смотреть:

```text
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Правило:

```text
любой patch -> обновить research/PATCH_LOG.md
изменился статус или следующий шаг -> обновить research/RESEARCH_STATE.md
изменился смысл стратегии -> обновить research/STRATEGY_SPEC.md
изменился experiment/run plan -> обновить research/EXPERIMENT_LOG.md
```

## Data quality

Для каждого run проверять:

```text
trade_count_proxy_used
levels_trade_count_source
entry_trade_count_source
levels_quote_volume_source
entry_quote_volume_source
number_of_trades / trades / trade_count
quote_volume  # real USDT quote notional
taker_buy_volume
taker_buy_quote_volume
```

PNO flow не использует `close*volume` как замену `quote_volume`: если real USDT quote notional отсутствует, символ должен получить явный `missing_required_market_data`.

## Оценка результата

При `positions = 0` или малом числе позиций не оценивать прибыльность. Анализировать только:

```text
funnel
reject reasons
near-miss cases
data quality
```

Минимальные ориентиры перспективной PNO-кандидатуры:

```text
50+ positions/year
winrate > 0.40
average position > +1.0%
помесячно преимущественно положительно
нет зависимости от 1-5 top positions
нет lookahead/leakage
нет явной переоптимизации
```

Если результат выглядит слишком сильным, сначала искать leakage, stale cache, selection bias или зависимость от хвостовых позиций.

## Sanity checks

После code/config patch:

```bash
python -m compileall domain/enums data/exchanges strategy/pno cli constants.py main.py
```

Перед тяжёлым full run:

```bash
python main.py check-quality
python main.py run-backtest --strategy pno --pno-all-tf-pairs --days 3 --top-n 20 --light-run true
```
