# Anomaly Strategy Research

Repository for researching and validating an anomaly-first trading system: detect market wake-up events, classify their nature, and only then test executable continuation logic.

The active source tree is anomaly-only. Retired strategy artifacts are not part of the runtime, CLI, strategy factory, or current research contract.

## What the project does

- downloads and updates local Binance futures cache;
- validates cache quality, including quote volume, trade count, OI gaps and stale series;
- runs anomaly continuation research backtests from cached data;
- runs strict REST-only anomaly micro-live validation;
- stores compact project memory in `research/*.md`.

## Current strategy axis

The system researches anomaly wake-up behavior:

```text
abnormal activity -> nature/category check -> controlled continuation -> executable entry -> managed exit
```

The focus is not a generic pullback pattern. The focus is whether the anomaly is organic, tradable, exhausted, manipulated, too thin, too late, or not actionable.

## Quick start

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
. .venv\Scripts\Activate.ps1
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

## Data preparation

For anomaly research/live, keep at least `1m` cache. Add `5m` and derivatives context when testing OI/funding-aware filters.

```bash
python main.py fetch-data --top-n 500 --days 30 --timeframes 1m 5m --with-derivatives-context
python main.py update-cache --top-n 500 --days 7 --timeframes 1m 5m --with-derivatives-context
python main.py check-quality
```

## Anomaly lab

```bash
python main.py run-anomaly-lab --days 31 --timeframe 1m --run-entry-grid true
```

Common knobs:

```text
--min-quote-ratio-start
--min-trade-ratio-start
--min-price-retention
--min-verticality-score
--min-oi-change-pct-3x5m
--exhaustion-profile
--entry-method
--exit-rule
```

## Anomaly micro-live

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 1
```

Live mode is intentionally strict:

- REST-only v1;
- no silent data fallback;
- real orders require `--confirm-real-orders`;
- protective stop placement is mandatory;
- missing flow/OI/context becomes an explicit reject/status reason.

## Research memory

Before non-trivial changes, check:

```text
research/RESEARCH_STATE.md
research/STRATEGY_SPEC.md
research/PATCH_LOG.md
research/EXPERIMENT_LOG.md
```

Rules:

```text
any patch -> update research/PATCH_LOG.md
status/risk/next step changed -> update research/RESEARCH_STATE.md
strategy meaning changed -> update research/STRATEGY_SPEC.md
experiment/run plan changed -> update research/EXPERIMENT_LOG.md
```

## Data quality

Always distinguish real fields from proxies:

```text
number_of_trades
quote_volume
volume
open_interest
taker_buy_volume
taker_buy_quote_volume
```

Do not make strong conclusions about anomaly nature when trade-count or quote-volume data is missing, stale, proxied, or unavailable at decision time.
