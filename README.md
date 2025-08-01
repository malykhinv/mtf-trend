# MTF Trend Bot

MTF Trend Bot — торговый бот, отслеживающий тренды на нескольких таймфреймах и открывающий нейтральные позиции при выгодной ставке финансирования.  Для запуска установите зависимости и выполните:

```bash
pip install -r requirements.txt
python main.py
```

This repository contains tools for multi-timeframe trend-following and funding arbitrage strategies.

## Installation

Install the project dependencies with [pip](https://pip.pypa.io/):

```bash
pip install -r requirements.txt
```

## Условия входа и выхода

| Проверка | Вход | Выход |
| -------- | ---- | ----- |
| Funding rate | `>= funding_rate` и положительный | `<= funding_rate` |
| Спред | `<= spread` | `>= spread` |
| Базис | `<= basis` | — |
| Ликвидность | `>= liquidity` | `<= liquidity` |
| Волатильность | `<= volatility` | `>= volatility` |
| Слиппейдж | `<= slippage` | — |

## Уведомления

Уведомления отправляются через Telegram с помощью [aiogram](https://docs.aiogram.dev/).  Для работы необходимо задать переменные окружения `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`.

## Логирование

Все сделки записываются в файл Excel `data/funding_bot_log.xlsx`, что позволяет анализировать эффективность стратегии и отслеживать историю операций.

## Автооптимизация

Модуль `ai/parameter_optimizer.py` периодически анализирует журнал сделок и подбирает новые пороги входа, что позволяет адаптировать стратегию к текущему рынку.

## Default Safety Thresholds

The bot ships with conservative defaults in [`config.yaml`](config.yaml) to guard against
unfavourable market conditions. These values represent **minimum safety thresholds** and
should be adjusted only after careful consideration:

| Threshold        | Default     | Purpose |
| ---------------- | ----------- | ------- |
| `funding_rate`   | `0.0003`    | Minimum absolute funding rate required to enter a trade |
| `basis`          | `0.5`       | Maximum spot–futures basis percentage |
| `volume`         | `20000000`  | Minimum 24h trading volume (USD) to ensure liquidity |
| `min_trade_size` | `10`        | Minimum notional value per trade |
| `spread`         | `0.005`     | Maximum allowable spread percentage |
| `slippage`       | `0.003`     | Maximum expected combined slippage across spot and futures |
| `deposit_pct`    | `0.05`      | Max fraction of total deposit allocated per trade |

These settings aim to provide a safety buffer for new users. Increase or relax them only if
you fully understand the associated risks.

