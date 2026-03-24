## Инструкция для AI-агента

Репозиторий снова рабоче-мультистратегийный, но активное R&D-направление сейчас одно: `post_pump_absorption`.

### Актуальная структура

- `cli/` - CLI-команды
- `config/` - загрузка конфигурации
- `data/` - сбор, хранение и проверка данных
- `strategy/bee_bite/` - старая стратегия и ее stage-review инструменты
- `strategy/post_pump_absorption/` - новый рабочий эксперимент для частых входов после пампа
- `strategy/common/` - общие утилиты
- `simulation/` - симуляция позиций
- `vectorbt_runner/` - backtest runner и подготовка данных

### Текущий приоритет

Основная стратегия для новых итераций: `post_pump_absorption`.

Ее текущая гипотеза:

- ищем свежий памп;
- после него ищем post-pump range;
- работаем только с нижней зоной этого диапазона;
- главный триггер - buy-side aggression плюс ответ цены;
- `OI` полезен, но не обязателен;
- `sweep` полезен, но не обязателен;
- в текущей реализации стратегия работает только на `1m`, `3m`, `5m`;
- в текущей реализации это single-timeframe стратегия: `levels_tf == entry_tf`;
- ключевые окна стратегии хранятся в минутах и нормализуются в бары под текущий ТФ;
- локальный stop ставится за локальную структуру или микро-базу, а не за весь боковик;
- базовые цели - `range_mid` и `range_high`;
- задача текущей итерации - получить много валидных входов, а затем отсекать шум статистически.
- current implementation locks the range on candles before the trigger candle; trigger candles must not move `range_high` or `TP` levels at decision time.
- current implementation refines the detected pump to the local peak before the post-pump scan begins.
- current implementation supports sequential re-entries inside one post-pump regime after the previous trade is closed.
- current implementation requires usable taker-flow data; if taker data is absent or unusable, diagnostics must report `missing_taker_data` instead of silently returning zero trades.
- current implementation stores per-trade metadata (`setup_type`, entry position in range, aggression strength, stop width, `MFE/MAE`, target hits) and uses it in backtest analytics.
- current implementation has a dedicated one-command research runner: `run-ppa-research`.
- `run-ppa-research` is the preferred entry point for PPA analysis; it must run all required micro timeframes (`1m`, `3m`, `5m` by default), export per-timeframe raw results, and build consolidated CSV/JSON/Markdown reports plus PNG charts in one root directory.
- research artifacts for `post_pump_absorption` must stay strategy-specific and must not be forced through the old bee_bite-only `make-report` model.
- `fetch-data` and `update-cache` must support explicit `--symbols`, `--timeframes` and `--skip-open-interest`; this is the supported way to build `1m` cache without OI.
- `launcher.py` must expose the PPA flow as a first-class mode instead of keeping a `bee_bite only` interface.

### Что считать устаревшим

- Нельзя тянуть новую логику в `bee_bite` как еще один reclaim-режим.
- Нельзя возвращать репозиторий к допущению "в рабочем коде есть только bee_bite".
- Нельзя делать `OI` обязательным gate для `post_pump_absorption`.
- Нельзя делать обязательным `sweep/reclaim` для `post_pump_absorption`.
- Нельзя снова строить edge вокруг редкого дальнего `TP2` от хая пампа.

### Правила для правок

1. Новые изменения по поиску частых post-pump входов должны идти в `strategy/post_pump_absorption/`.
2. `bee_bite` сохраняется как историческая стратегия и набор review-команд; stage-обзоры по ней не удалять без явного запроса.
3. Для `post_pump_absorption` приоритет у двух сетапов:
   - `LSB` (`local structure break`)
   - `MBB` (`micro base breakout`)
4. Если меняется гипотеза новой стратегии, сначала обновляется этот файл и/или README стратегии, потом код.
5. Для первой итерации предпочтительнее простая per-symbol логика, чем сложный portfolio ranking.
6. Приоритет метрик для новой стратегии:
   - число входов,
   - hit-rate до `range_mid`,
   - hit-rate до `range_high`,
   - `MAE/MFE`,
   - и только потом общий `PnL`.

### Актуальные пользовательские точки входа

- `python main.py run-backtest --strategy post_pump_absorption`
- `python main.py run-backtest --strategy post_pump_absorption --ppa-profile balanced`
- `python main.py run-ppa-research --ppa-profile balanced`
- `python main.py run-backtest --strategy bee_bite`
- `python main.py make-report`
- `python main.py check-quality`
- `python main.py clear-cache`

### Важное разграничение

- `post_pump_absorption` - основная стратегия для новых экспериментов и реализации.
- `bee_bite` - сохраняем, поддерживаем, но не используем как основу для новых идей про bottom-entries после пампа.
