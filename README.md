# MTF Trend Bot

This project provides tools for multi-timeframe signal discovery, backtesting, and live execution across multiple crypto derivatives exchanges.

## Configuration

All runtime configuration is stored in `settings.toml`. The most relevant options include:

- `providers` – API credentials and rate limits for each supported exchange.
- `symbols` – filters and manual overrides for the trading universe.
- `thresholds` – strategy thresholds used during backtests and live execution.
- `backtest` – options controlling historical analysis.
- `live` – configuration for the live trading runners.

## Running live trading with multiple providers

Live mode now supports concurrent execution on several exchanges. To enable it:

1. Enable live mode by setting `live.enabled = true`.
2. Configure the `live.providers` list with all providers you want to trade on, for example:
   ```toml
   [live]
   enabled = true
   providers = ["binance", "bybit"]
   timeframe = "5m"
   window = 50
   ```
3. Ensure API credentials are configured for each provider in the `[providers.<name>]` sections or via environment variables.
4. Optionally adjust symbol assignments in `[symbols.providers]` to pin specific tickers to their preferred exchange.

Each provider receives an independent processing pipeline and maintains a separate state/deposit snapshot in the Excel storage (see `storage.path`). This isolation allows the bot to track balances and open position usage for every exchange independently.

## Backtesting

Backtests use the same symbol discovery process as live trading, including per-provider assignments. Review the `[backtest]` section to adjust window length, candle limits, and timeframes.

## Storage

Trading signals, executed trades, and per-provider capital state are persisted in the Excel file configured via `storage.path`. The state sheet now records a `key` column identifying the provider/exchange associated with each capital snapshot.
