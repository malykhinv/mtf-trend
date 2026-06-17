# Operator commands

Copy commands from here. Defaults live in code constants; when the baseline changes, update constants instead of expanding command lines.

## Baseline

```text
Base window: 30 days
Exchange: Binance USD-M futures
Live orders: require explicit --confirm-real-orders
Ticker radar: enabled by default, scheduling-only
Top-growth: standalone command only, not part of live loop
```

## Cache

Initial 30-day load:

```bash
python main.py fetch-data
```

Incremental 30-day refresh:

```bash
python main.py update-cache
```

Specific symbols:

```bash
python main.py fetch-data --symbols BTC/USDT:USDT ETH/USDT:USDT
```

## Quality

```bash
python main.py check-quality
```

## Research backtest

Default 30-day anomaly lab:

```bash
python main.py run-anomaly-lab
```

Specific symbols:

```bash
python main.py run-anomaly-lab --symbols BTC/USDT:USDT ETH/USDT:USDT
```

## Live smoke

Default safety/latency smoke:

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60
```

Conservative ticker-radar smoke:

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --ticker-radar-watch-batch-size 2
```

Radar disabled smoke:

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --ticker-radar-enabled false
```

## Full live

```bash
python main.py run-anomaly-live --confirm-real-orders
```

## Standalone top-growth snapshot

Previous fully closed 1h candle:

```bash
python main.py run-anomaly-top-growth
```

Specific closed 1h candle:

```bash
python main.py run-anomaly-top-growth --period-start-utc 2026-05-12T04:00:00Z
```

## Hourly overhead levels

Default 30-day diagnostics:

```bash
python main.py run-hourly-levels
```

Specific symbols:

```bash
python main.py run-hourly-levels --symbols BTC/USDT:USDT ETH/USDT:USDT
```
