# Bee Bite strategy

Документация по стратегии `bee_bite`.

## Как запустить

Через `launcher.py`:

```bash
python launcher.py --mode analyze-cache --top-n 50 --strategy bee_bite
python launcher.py --mode analyze-cache --symbols BTC/USDT --strategy bee_bite --plot-from-results --results-input ./cache/results/backtest_results.csv
```

Через `main.py`:

```bash
python main.py run-backtest --strategy bee_bite --top-n 50
```

## Профиль и сетка

- `--bee-bite-profile {A,B,C}` — выбор baseline-профиля.
- `--bee-bite-grid {baseline,expanded}` — узкая или расширенная сетка параметров.

Примеры:

```bash
python main.py run-backtest --strategy bee_bite --bee-bite-profile A --bee-bite-grid baseline --top-n 50
python main.py run-backtest --strategy bee_bite --bee-bite-profile A --bee-bite-grid expanded --top-n 100
```

- Единицы bee_bite заданы явно:
  - `bite_reclaim_limit_bars` — лимит ожидания reclaim в барах entry-TF (для портфельного режима это бары 15m);
  - `bite_cooldown_hours` и `bite_max_age_range_hours` — runtime-параметры в часах с конвертацией в бары 15m внутри `PortfolioStateEngine`.

Переменные окружения:

```env
BEE_BITE_PROFILE=A
BEE_BITE_GRID_MODE=baseline
```

Основные модули реализации:
- `strategy/bee_bite/bee_bite_strategy.py`
- `strategy/bee_bite/engine.py`
- `strategy/bee_bite/trade_plan.py`
- `strategy/bee_bite/config.py`
