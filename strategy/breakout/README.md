# Breakout strategy

Документация по стратегии `breakout` (alias: `retest`).

## Как запустить

Через `launcher.py`:

```bash
python launcher.py --mode analyze-cache --top-n 50 --strategy breakout
python launcher.py --mode analyze-cache --plot-from-results --results-input ./cache/results/strategy/retest/results.csv --id 1156 --strategy retest
```

Через `main.py`:

```bash
python main.py run-backtest --strategy breakout --top-n 50
```

## Поддержка `--plot-from-results`

Для `breakout`/`retest` из CSV читаются breakout-колонки параметров (например, `lookback`, `volume_mult` и связанные с ними поля).

## Параметры стратегии

По умолчанию в гриде используется `retest_window_hours`: `12, 24, 36, 48`.

Основные модули реализации:
- `strategy/breakout/breakout_strategy.py`
- `strategy/breakout/pending_breakout.py`
- `strategy/breakout/pending_retest.py`
- `strategy/breakout/config.py`
