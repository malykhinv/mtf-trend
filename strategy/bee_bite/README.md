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


## Как определяется итоговый `top_n` в portfolio mode

1. Если передан `--top-n` в `run-backtest`, это значение прокидывается в `BeeBiteStrategy` и затем в `PortfolioEngineConfig(top_n=...)`.
2. Если `--top-n` не передан, используется профильный fallback из `BEE_BITE_PROFILE_TOP_N` (`A=5`, `B=10`, `C=20`).
3. Для CLI-значения действует runtime-валидация `validate_bee_bite_runtime()` по диапазонам профиля (`top_n_min..top_n_max`) и доп. ограничению для `expanded` (`>= 20`).

Итог: в portfolio mode источник `top_n` — сначала CLI/конфиг запуска, иначе профильный дефолт.

Основные модули реализации:
- `strategy/bee_bite/bee_bite_strategy.py`
- `strategy/bee_bite/engine.py`
- `strategy/bee_bite/trade_plan.py`
- `strategy/bee_bite/config.py`

## Входные признаки (portfolio mode)

Единый источник `oi_break_avg` закреплён как **upstream feature pipeline**:
- `PortfolioStateEngine` не пересчитывает `oi_break_avg` внутри FSM;
- поле должно быть заранее подготовлено в входном `entry_frame` для каждого символа.

### Обязательные поля

- Базовые OHLCV: `timestamp`, `open`, `high`, `low`, `close`, `volume`.
- Для score-компонента OI: `oi_break_avg`.

Для `oi_break_avg` действует строгая валидация качества на уровне `PortfolioStateEngine`:
- колонка должна присутствовать;
- не менее `32` валидных значений (`finite && > 0`);
- покрытие валидными значениями не ниже `0.90` по фрейму.

Если критерии не выполнены, символ пропускается до начала FSM. Причина попадает в diagnostics-флаг
`portfolio_score.oi_break_avg_validation.skipped_symbols`.

### Опциональные поля и fallback

- `oi_reclaim`, `taker_buy_ratio|taker_ratio`, `avg_volume_range|avg_range_volume|range_volume_avg`,
  `range_volume_zscore|volume_range_zscore|zscore_range_volume`, `spread|bid_ask_spread|effective_spread`.
- При отсутствии или плохом качестве этих полей стратегия не падает: соответствующий компонент score даёт `0` баллов,
  а причина фиксируется в `score_trace.data_quality_notes`.
