# MTF Trend Strategy

## Strategy Overview
The project implements a multi-timeframe breakout system. OHLCV data is searched for tight ranges, and breakouts are evaluated using volume, cumulative volume delta (CVD), open interest (OI) and funding to produce trade signals with defined entry, stop and profit targets【F:utils/breakout_signals.py†L3-L8】. A simple backtesting engine loads CSV data, finds tight range clusters and applies the breakout logic bar by bar【F:backtester.py†L3-L8】.

## Requirements
- Python 3.11+
- Libraries listed in `requirements.txt`:
  - PyYAML
  - python-dotenv
  - schedule
  - ccxt
  - pandas
  - matplotlib
  - requests【F:requirements.txt†L1-L7】

## Setup
1. Clone this repository and create a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`.
3. Populate `config.yaml` with exchange credentials and desired symbols, or set the `API_KEY` and `API_SECRET` environment variables【F:config.yaml†L1-L16】.
4. (Optional) Set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` for daily summaries.
5. Fetch initial data: `python utils/ohlcv_fetcher.py --symbol BTC/USDT --limit 1000`.

## Module Overview
- **backtester.py** – runs a bar-by-bar simulation using `find_tight_range_clusters` and `evaluate_breakout` and writes results to `trades.csv`【F:backtester.py†L3-L8】【F:backtester.py†L25-L26】.
- **utils/range_clusters.py** – finds clusters of consecutive small-range candles based on ATR to supply breakout levels【F:utils/range_clusters.py†L3-L31】.
- **utils/breakout_signals.py** – evaluates whether price breaks above/below the most recent range with supporting volume, CVD, OI and funding filters, returning `Signal` objects【F:utils/breakout_signals.py†L3-L8】【F:utils/breakout_signals.py†L66-L111】.
- **utils/ohlcv_fetcher.py** – downloads OHLCV candles via ccxt with retry logic and can update all symbols listed in the configuration【F:utils/ohlcv_fetcher.py†L1-L10】【F:utils/ohlcv_fetcher.py†L51-L66】.
- **utils/futures_trader.py** – wraps ccxt futures APIs to submit market or limit-maker orders, manage TP/SL exits and log trades【F:utils/futures_trader.py†L3-L8】.
- **utils/risk.py** – `RiskManager` enforces per-trade risk, total open risk, daily drawdown and consecutive loss limits【F:utils/risk.py†L11-L29】.
- **utils/trade_logger.py** – appends executed trades to `trades.csv` and provides daily win rate, average RR and equity change summaries【F:utils/trade_logger.py†L3-L30】【F:utils/trade_logger.py†L33-L64】.
- **main.py** – orchestrates live trading: collects data, screens for setups, applies trend filters, checks risk and sends daily summaries via Telegram【F:main.py†L18-L32】【F:main.py†L72-L94】.

## Backtesting
1. Ensure CSV files for symbols exist in `data/raw_data`. Use the fetcher to gather data if needed.
2. Run the backtester: `python backtester.py --symbol ETHUSDT --start 2024-05-01 --end 2024-07-01`【F:backtester.py†L10-L14】.
3. Review `trades.csv` and the printed statistics.

## Live Trading
1. Confirm `config.yaml` contains symbols and data paths, and environment variables or config provide API keys【F:config.yaml†L1-L28】.
2. Start the bot: `python main.py`. Data is collected every five minutes and a daily equity/risk summary is sent at midnight【F:main.py†L127-L129】.
3. The `RiskManager` checks account balance and halts trading if risk limits or drawdown thresholds are exceeded【F:utils/risk.py†L32-L43】【F:utils/risk.py†L111-L118】.

## Risk Warnings
This code is for educational purposes only. Trading futures or cryptocurrencies carries significant risk; you can lose more than your initial investment. Backtested performance does not guarantee future results. Review and adjust the risk limits in `RiskManager` and understand exchange rules before trading live.
