## Инструкция для AI-агента

Репозиторий больше не мультистратегийный. В рабочем коде оставлена только стратегия `bee_bite`.

### Актуальная структура

- `cli/` — команды CLI
- `config/` — загрузка конфигурации
- `data/` — сбор, хранение и проверка данных
- `strategy/bee_bite/` — логика стратегии `bee_bite`
- `strategy/common/` — общие для стратегии утилиты
- `simulation/` — симуляция позиций
- `vectorbt_runner/` — backtest runner и подготовка данных

### Что считать устаревшим

Если в старых логах, артефактах или заметках встречаются legacy-названия стратегий, их нужно трактовать как исторический контекст. Они удалены из рабочего кода и не должны возвращаться в новые изменения.

### Правила для правок

1. Любые новые изменения должны поддерживать только `bee_bite`.
2. Нельзя добавлять обратно legacy-стратегию, ее alias и старые plot-команды.
3. Для отбора монет нужно опираться на stage-1 фильтр `bee_bite`.
4. Общие компоненты надо размещать в нейтральных модулях, а не в legacy-папках старой стратегии.
5. Договоренности по стадиям `bee_bite` зафиксированы в `bee_bite_stages.md`; при изменении логики стадий сначала обновляется этот контракт, потом код.

### Актуальные пользовательские точки входа

- `python main.py fetch-data`
- `python main.py update-cache`
- `python main.py run-backtest --strategy bee_bite`
- `python main.py make-report`
- `python main.py check-quality`
- `python main.py clear-cache`
- `python launcher.py --mode fetch-cache|update-cache|analyze-cache|make-report|check-quality|clear-cache`
