# Hourly Asia Pump Research

`hourly_asia_pump` is the active research flow on this branch.

## Hypothesis

- impulsive pumps often begin exactly at `XX:00`
- the strongest cases happen during the Asia session
- many of these pumps stay alive long enough to detect the impulse and join the continuation
- the key measurement is not only the trigger candle itself, but also the total extension before the move retraces by `50%` or more

## What The Research Exports

- parameter grid summary across `1m`, `3m`, `5m`
- selected-profile event table with full per-pump characteristics
- per-timeframe common-pattern summary
- per-symbol summary for the selected profile
- Markdown report for manual review

## Main Command

```bash
python main.py run-hourly-pump-research
```

Useful variants:

```bash
python main.py run-hourly-pump-research --selection-profile balanced
python main.py run-hourly-pump-research --selection-profile strict --timeframes 1m 5m
python main.py run-hourly-pump-research --symbols BTC/USDT ETH/USDT SOL/USDT
```

## Detection Shape

The research flow looks only at candles that:

- start exactly at minute `00`
- belong to the Asia session in UTC
- close green
- stand out by range, body, volume and local breakout

For each detected event it measures:

- trigger candle statistics
- breakout context
- maximum extension before a `50%+` retrace
- continuation potential after the trigger candle closes
- survival time before the move loses half of its extension
