# MTF Trend Bot

This repository contains tools for multi-timeframe trend-following and funding arbitrage strategies.

## Default Safety Thresholds

The bot ships with conservative defaults in [`config.yaml`](config.yaml) to guard against
unfavourable market conditions. These values represent **minimum safety thresholds** and
should be adjusted only after careful consideration:

| Threshold        | Default     | Purpose |
| ---------------- | ----------- | ------- |
| `funding_rate`   | `0.0003`    | Minimum absolute funding rate required to enter a trade |
| `basis`          | `0.005`     | Minimum spot–futures basis (0.5%) |
| `volume`         | `20000000`  | Minimum 24h trading volume to ensure liquidity |
| `min_trade_size` | `10`        | Minimum notional value per trade |
| `spread`         | `0.005`     | Maximum allowable spread percentage |
| `slippage`       | `0.003`     | Maximum expected slippage per trade |
| `deposit_pct`    | `0.05`      | Max fraction of total deposit allocated per trade |

These settings aim to provide a safety buffer for new users. Increase or relax them only if
you fully understand the associated risks.

