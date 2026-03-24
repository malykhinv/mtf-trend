# mtf-trend

Active R&D strategy: `post_pump_absorption`.

## What This Repo Does

- fetches and updates futures market cache
- runs backtests on cached data
- builds research artifacts for `post_pump_absorption`
- keeps `bee_bite` review and postmortem tooling available
- validates cache quality

## Quick Start

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

Minimal `.env`:

```env
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
LOG_LEVEL=INFO
CACHE_DIR=./.output/cache
RESULTS_DIR=./.output/results
STRATEGY_ID=post_pump_absorption
ENTRY_TIMEFRAME=3m
LEVELS_TIMEFRAME=3m
```

## Main Commands

Build or refresh cache:

```bash
python main.py fetch-data --top-n 100 --days 30 --timeframes 3m 5m
python main.py update-cache --top-n 100 --days 7 --timeframes 3m 5m
python main.py update-cache --symbols BTC/USDT ETH/USDT --timeframes 1m --skip-open-interest
```

Run the main research flow:

```bash
python main.py run-ppa-research --ppa-profile balanced
python main.py run-ppa-research --top-n 80 --ppa-profile strict
python main.py run-ppa-research --symbols BTC/USDT ETH/USDT SOL/USDT
```

Run one timeframe manually:

```bash
python main.py run-backtest --strategy post_pump_absorption --entry-tf 1m --levels-tf 1m
python main.py run-backtest --strategy post_pump_absorption --entry-tf 3m --levels-tf 3m
python main.py run-backtest --strategy post_pump_absorption --entry-tf 5m --levels-tf 5m
```

Legacy and support commands:

```bash
python main.py run-backtest --strategy bee_bite --top-n 50
python main.py review-stage1 --plot-limit 20
python main.py review-stage2 --plot-limit 20
python main.py review-stage3 --plot-limit 20
python main.py postmortem-stage4
python main.py check-quality
python main.py clear-cache
```

## Release Path For PPA

Preferred user path:

```bash
python main.py run-ppa-research --ppa-profile balanced
```

This command:

- runs `post_pump_absorption` on `1m`, `3m`, `5m` by default
- saves raw results for each timeframe
- exports per-symbol diagnostics for the best combination of each timeframe
- builds one consolidated research package with:
  - `summary_by_timeframe.csv`
  - `summary_by_timeframe.json`
  - `combined_results.csv`
  - `all_best_trades.csv`
  - `symbol_summary.csv`
  - `diagnostics_by_symbol.csv`
  - `diagnostics_summary_by_timeframe.csv`
  - `research_report.md`
  - `charts/*.png`

## Launcher

The simplified launcher supports both strategy backtests and the PPA research flow:

```bash
python launcher.py --mode ppa-research --ppa-profile balanced
python launcher.py --mode analyze-cache --strategy post_pump_absorption --entry-tf 3m --levels-tf 3m
python launcher.py --mode fetch-cache --timeframes 1m 3m 5m --skip-open-interest
```

## Notes

- `post_pump_absorption` currently works only in single-timeframe mode: `levels_tf == entry_tf`
- supported PPA timeframes are `1m`, `3m`, `5m`
- `1m` data can be fetched without open interest via `--skip-open-interest`
- `make-report` is part of the older `bee_bite` reporting flow; for PPA use `run-ppa-research`

## Strategy Docs

- PPA strategy: [strategy/post_pump_absorption/README.md](/C:/Users/Ascf/PycharmProjects/mtf-trend-2/strategy/post_pump_absorption/README.md)
- Bee bite strategy: [strategy/bee_bite/README.md](/C:/Users/Ascf/PycharmProjects/mtf-trend-2/strategy/bee_bite/README.md)
- Current agent instruction: [instruction.md](/C:/Users/Ascf/PycharmProjects/mtf-trend-2/instruction.md)
