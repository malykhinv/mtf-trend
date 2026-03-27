# mtf-trend

Active R&D hypothesis on this branch: `hourly_asia_pump`.

## What This Repo Does

- fetches and updates futures market cache
- scans cached `1m`, `3m`, `5m` data for impulsive top-of-hour pumps during the Asia session
- runs a parameter grid over the detection logic
- exports detailed per-pump tables and summary research artifacts
- keeps legacy backtest tooling available, but outside the primary user path for this branch

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
```

## Main Commands

Build or refresh cache:

```bash
python main.py fetch-data --top-n 100 --days 30 --timeframes 1m 3m 5m --skip-open-interest
python main.py update-cache --top-n 100 --days 7 --timeframes 1m 3m 5m --skip-open-interest
python main.py update-cache --symbols BTC/USDT ETH/USDT --timeframes 1m 3m 5m --skip-open-interest
```

Run the active research flow:

```bash
python main.py run-hourly-pump-research
python main.py run-hourly-pump-research --selection-profile balanced
python main.py run-hourly-pump-research --timeframes 1m 3m 5m --top-n 120
python main.py run-hourly-pump-research --symbols BTC/USDT ETH/USDT SOL/USDT
```

Launcher shortcut:

```bash
python launcher.py --mode hourly-pump-research
python launcher.py --mode hourly-pump-research --selection-profile strict --timeframes 1m 5m
```

Support commands:

```bash
python main.py check-quality
python main.py clear-cache
```

## Release Path For Hourly Pump Research

Preferred user path:

```bash
python main.py run-hourly-pump-research
```

This command:

- scans `1m`, `3m`, `5m` by default
- looks only at candles that start exactly at `XX:00`
- limits the scan to the Asia session in UTC
- builds a parameter grid over impulse/range/volume/breakout filters
- measures how far each pump extends before a `50%+` retrace
- exports one consolidated research package with:
  - `grid_summary.csv`
  - `profile_summary.csv`
  - `selected_profile_events.csv`
  - `common_patterns_by_timeframe.csv`
  - `selected_profile_symbol_summary.csv`
  - `research_report.md`

## Notes

- supported research timeframes are `1m`, `3m`, `5m`
- timestamps are interpreted in UTC inside the research flow
- the active branch focus is research and event discovery, not a finished execution strategy
- legacy `post_pump_absorption` and `bee_bite` code is kept for reference, but is not the primary branch workflow

## Strategy Docs

- Hourly Asia pump research: [strategy/hourly_asia_pump/README.md](/C:/Users/Ascf/PycharmProjects/mtf-trend-2/strategy/hourly_asia_pump/README.md)
- Current agent instruction: [instruction.md](/C:/Users/Ascf/PycharmProjects/mtf-trend-2/instruction.md)
