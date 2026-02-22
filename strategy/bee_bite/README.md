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
  - `bite_max_retest_depth` — порог стабильности range в ATR (`stability_threshold` в `_freeze_range`), диапазон `(0, 2]`;
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

## Приоритет порога стабильности range

- В `_freeze_range` всегда используется `params.bite_max_retest_depth` как главный порог стабильности (`max(p10_last6)-min(p10_last6) <= bite_max_retest_depth * atr_bg`).
- Профиль A/B/C задаёт только дефолт этого параметра в `BEE_BITE_PROFILE_BASELINES` (`A=0.20`, `B=0.25`, `C=0.30`).
- Таким образом, `bite_max_retest_depth` — реальный рабочий параметр (не псевдо-поле в выводе).

Основные модули реализации:
- `strategy/bee_bite/bee_bite_strategy.py`
- `strategy/bee_bite/engine.py`
- `strategy/bee_bite/trade_plan.py`
- `strategy/bee_bite/config.py`


## Влияние HTF-уровней на входы

В `BeeBiteEngine._run_fsm` HTF-уровни (`level_high/level_low`) теперь участвуют в FSM явно:

- **SEEK_PUMP**: направление сетапа фильтруется текущим HTF-контекстом.
  - `LONG` допускается только если цена пробоя `<= current level_low`.
  - `SHORT` допускается только если цена пробоя `>= current level_high`.
  - Если HTF-уровней нет (режим `generate_events()`), фильтр отключается.
- **SEEK_RANGE**: после фиксации диапазона проверяется привязка к HTF-якорю.
  - Для `LONG` внутри диапазона должен находиться `level_low`.
  - Для `SHORT` внутри диапазона должен находиться `level_high`.
  - При несоответствии диапазон отбрасывается, FSM возвращается в `IDLE`.
- **RANGE_LOCKED / BREAK_ACTIVE**: прокол и reclaim считаются относительно объединённой границы `reclaim_reference`:
  - `LONG`: `max(range_low, level_low)` (или `range_low`, если HTF отсутствует).
  - `SHORT`: `min(range_high, level_high)` (или `range_high`, если HTF отсутствует).
  - Для входа требуется прокол за диапазон и за `reclaim_reference`, затем закрытие/reclaim обратно через `reclaim_reference` с микро-офсетом.

Это делает логику мультитаймфрейма строгой, а single-TF режим (без `higher_levels`) оставляет в прежнем рабочем виде без обязательных HTF-фильтров.

## Классификация исходов сделок

- При срабатывании `time-exit` **до достижения TP1** (`PROFILE_TIME_EXIT_HOURS_NO_TP1`) итоговый `result_type`
  определяется по знаку `pnl` независимо от того, совпал ли бар выхода с `limit` (в т.ч. на последнем допустимом баре):
  - отрицательный `pnl` → `SL`;
  - около нуля → `BE`;
  - положительный `pnl` → `TIME_EXIT_PROFIT`.
- Сделки после достижения TP1 продолжают использовать существующие исходы (`TP1_BE` / `TP2`) по правилам trade-plan.

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
