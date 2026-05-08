# PNO Patch Log

Короткий журнал патчей. Подробности держать только если они важны для будущего анализа.

---

## 1. Статусы

```text
PROPOSED  = предложен
APPLIED   = применён
VERIFIED  = проверен compile/test/run
UNKNOWN   = обсуждался, но текущее состояние не проверено
REVERTED  = откатан
SUPERSEDED = заменён новым патчем
```

---

## 2. Индекс

| ID | Название | Статус | Файлы | Тип | Суть | Проверка |
|---|---|---|---|---|---|---|
| P001 | Stage4 dedup | UNKNOWN | `cli/commands.py` | diagnostics | Схлопнуть повторные Stage4 setup rows. | Stage4 before/after + unique setups. |
| P002 | Stage5 reject reasons | UNKNOWN | `strategy/pno/engine.py` | diagnostics | Вернуть точные причины no-trade. | Stage5 rejected reasons. |
| P003 | Trades % charts | UNKNOWN | `cli/pno_diagnostics.py` | charts | Добавить trade-count панель. | На графиках есть `Trades %`. |
| P004 | True trade-count data | PROPOSED / UNKNOWN | `constants.py`, `strategy/pno/*` | data quality | Использовать real trade-count/quote_volume. | `trade_count_proxy_used=false`. |
| P005 | Repo cleanup | APPLIED | `.gitignore`, `.run/*`, `logs/parquet-storage.log`, `utils/validators.py` | cleanup | Убрать локальные IDE/log/empty artifacts без изменения PNO-логики. | `python -m compileall strategy/pno cli constants.py` |
| P006 | PNO entry/data fetch hardening | APPLIED | `launcher.py`, `strategy/pno/pno_strategy.py` | bugfix/data quality | Зафиксировать только `close_above`, покрывать последнюю свечу aggTrades window, пагинировать live aggTrades по id. | `python -m compileall strategy/pno launcher.py` |
| P007 | PNO aggTrades helper hotfix | APPLIED | `strategy/pno/pno_strategy.py`, `research/*` | bugfix | Добавить отсутствующие helper-методы, которые вызывает P006. | `python -m compileall strategy/pno launcher.py` |
| P008 | PNO aggTrades typing/client boundary | PROPOSED | `data/exchanges/ccxt_types.py`, `data/exchanges/ccxt_futures_client.py`, `strategy/pno/pno_strategy.py` | typing/refactor | Убрать доступ PNO к private ccxt client, типизировать aggTrades payload, убрать сомнительный `id` fallback. | `python -m compileall data/exchanges strategy/pno launcher.py` |
| P009 | Add 5m/15s PNO TF set | APPLIED | `domain/enums/timeframe.py`, `strategy/pno/config.py`, `research/*` | experiment config | Добавить `15s` timeframe и включить `5m/15s` в multi-TF backtest set. | `python -m compileall domain/enums strategy/pno cli constants.py main.py launcher.py` |
| P010 | Rewrite README for PNO research workflow | APPLIED | `README.md`, `research/*` | docs | Заменить устаревший README на фактический PNO research workflow: data, 3 TF-set, diagnostics, research memory, data quality. | `python -m compileall domain/enums data/exchanges strategy/pno cli constants.py main.py launcher.py` |
| P011 | Humanize runtime logs in Russian | APPLIED | `constants.py`, `launcher.py`, `utils/retry.py`, `data/*`, `vectorbt_runner/backtest_runner.py`, `cli/commands.py`, `research/*` | logging/docs | Перевести runtime-логи на лаконичный русский: процесс, прогресс, ошибки и итоговая аналитика без служебного шума. | `python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py` |
| P012 | Fix P011 logging follow-up | APPLIED | `vectorbt_runner/backtest_runner.py`, `launcher.py`, `research/*` | bugfix/bookkeeping | Исправить лишние аргументы logger.info после P011 и синхронизировать статусы P010/P011. | `python -m compileall vectorbt_runner launcher.py` |
| P013 | Narrative runtime logs | APPLIED | `constants.py`, `utils/retry.py`, `data/*`, `vectorbt_runner/backtest_runner.py`, `cli/commands.py`, `research/*` | logging/docs | Превратить runtime-логи в связный консольный рассказ: меньше шума, больше этапов, прогресса, причин и финального смысла. | `python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py` |
| P014 | Polish console logs | APPLIED | `constants.py`, `utils/retry.py`, `cli/*`, `data/*`, `strategy/factory.py`, `vectorbt_runner/*`, `research/*` | logging/docs | Перевести оставшийся английский в консоли, убрать сухие key=value строки, добавить переносы строк в длинные сообщения. | `python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py` |
| P015 | Backtest progress and memory logs | APPLIED | `vectorbt_runner/backtest_runner.py`, `research/*` | logging/bugfix | Убрать дублирующий progress narrative при одной комбинации, добавить предупреждение о тяжёлом symbol set и понятный memory-error. | `python -m compileall vectorbt_runner research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P016 | Fix unclosed logger call | APPLIED | `vectorbt_runner/backtest_runner.py` | bugfix | Закрыть незавершённый logger.info после P015. | `python -m compileall vectorbt_runner/backtest_runner.py` |
| P017 | Fix P015 runner regression | APPLIED | `vectorbt_runner/backtest_runner.py`, `research/*` | bugfix | Вернуть потерянный вызов generate_events_portfolio и восстановить аргументы long-symbol logger. | `python -m compileall vectorbt_runner/backtest_runner.py` |
| P018 | Stale reclaim and trade-count chart fix | APPLIED | `cli/pno_diagnostics.py`, `strategy/pno/pno_strategy.py`, `research/*` | bugfix/diagnostics | Канонизировать trade-count column для графиков и отбрасывать позиции после failed reclaim уровня до финального сигнала. | `python -m compileall cli/pno_diagnostics.py strategy/pno/pno_strategy.py` |
| P019 | Clear PNO trade-data caches | APPLIED | `strategy/pno/pno_strategy.py` | memory | Сбрасывать aggTrades-derived runtime caches после обработки символа. | `python -m compileall strategy/pno/pno_strategy.py` |
| P020 | Trade/chart consistency fix | APPLIED | `cli/pno_diagnostics.py`, `strategy/pno/pno_strategy.py`, `research/*` | bugfix/diagnostics | Заполнить все trade-count aliases и считать уровень stale, если close_above случился до финального сигнала. | `python -m compileall cli/pno_diagnostics.py strategy/pno/pno_strategy.py` |
| P021 | True trade-count chart propagation | APPLIED | `strategy/pno/pno_strategy.py`, `research/*` | bugfix/diagnostics | Прокидывать реальные number_of_trades/trades/trade_count из enriched PNO frames обратно в исходные frames, которые использует chart export. | `python -m compileall strategy/pno/pno_strategy.py` |
| P022 | Stage5/results consistency fix | APPLIED | `strategy/pno/pno_strategy.py`, `vectorbt_runner/backtest_runner.py`, `research/*` | bugfix/diagnostics | Переносить stale-level Stage5 passed events в rejected и писать runtime TF в results.csv. | `python -m compileall strategy/pno/pno_strategy.py vectorbt_runner/backtest_runner.py` |
| P023 | PNO none-trades guard | APPLIED | `strategy/pno/pno_strategy.py`, `vectorbt_runner/backtest_runner.py`, `research/*` | bugfix | Вернуть list-return contract для PNO generate_events_multi_tf и не валить runner, если стратегия вернула None. | `python -m compileall strategy/pno/pno_strategy.py vectorbt_runner/backtest_runner.py` |
| P024 | Concise backtest logs | APPLIED | `vectorbt_runner/backtest_runner.py`, `research/*` | logging | Убрать лишнюю прозу из runtime backtest logs, оставить TF, progress 0..100% и итоговую сводку по позициям. | `python -m compileall vectorbt_runner/backtest_runner.py` |
| P025 | Fix concise progress checkpoint init | APPLIED | `vectorbt_runner/backtest_runner.py`, `research/*` | bugfix | Инициализировать progress checkpoint cursor перед per-symbol progress loop. | `python -m compileall vectorbt_runner/backtest_runner.py` |
| P026 | Strict backtest runtime log shape | APPLIED | `vectorbt_runner/backtest_runner.py`, `research/*` | logging | Привести runtime-логи бэктеста к строгому формату: заголовок, TF, progress, итог без лишней диагностики. | `python -m compileall vectorbt_runner/backtest_runner.py` |
| P027 | Quiet runtime logger sweep | APPLIED | `utils/logger.py`, `utils/retry.py`, `data/*`, `vectorbt_runner/backtest_runner.py`, `research/*` | logging | Пройтись по logger.* и оставить в консоли только человековажные заголовки, progress, итог, предупреждения и ошибки. | `python -m compileall utils data vectorbt_runner research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P028 | Normalize diagnostics and chart logs | APPLIED | `utils/logger.py`, `research/*` | logging | Убрать summary/grid/runner-final шум и нормализовать PNO diagnostics/charts export logs в короткий человекочитаемый статус. | `python -m compileall utils/logger.py research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P029 | Whitelist runtime log format | APPLIED | `utils/logger.py`, `research/*` | logging | Перевести runtime logging на whitelist: заголовок, отбор, TF, progress, итог, diagnostics/charts, errors. Остальной INFO/WARNING шум скрывать. | `python -m compileall utils/logger.py research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P030 | Replace runtime log filters with source logs | APPLIED | `utils/logger.py`, `cli/commands.py`, `research/*` | logging/refactor | Удалить глобальный RuntimeNoiseFilter/whitelist и исправить шумные runtime logs в конкретных источниках. | `python -m compileall utils/logger.py cli/commands.py research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P031 | Finish source-level runtime logs | APPLIED | `cli/commands.py`, `research/*` | logging/refactor | Добить оставшиеся runtime источники: wrapper start/path в debug, diagnostics/charts в нормальный человекочитаемый статус. | `python -m compileall cli/commands.py research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P032 | Normalize source-level artifact logs | PROPOSED | `cli/commands.py`, `cli/pno_diagnostics.py`, `research/*` | logging/refactor | Завершить нормализацию без костылей: public runtime status только из call-sites, detailed artifact logs в debug. | `python -m compileall cli/commands.py cli/pno_diagnostics.py research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P033 | PyCharm inspection cleanup | PROPOSED | `cli/commands.py`, `vectorbt_runner/*`, `data/*`, `strategy/*`, `simulation/*`, `launcher.py`, `main.py`, `research/*` | cleanup/typing | Исправить актуальные PyCharm inspection warnings без изменения PNO trade logic. | `python -m compileall data/exchanges data/liquidity simulation strategy/pno strategy/base_strategy.py vectorbt_runner cli constants.py main.py launcher.py` |
| P034 | Diagnostics initial progress | PROPOSED | `cli/commands.py`, `research/*` | logging/diagnostics | Печатать стартовый progress `Диагностика: 0 из N` и не пропускать checkpoints на пустых символах. | `python -m compileall cli/commands.py research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P035 | PNO lazy entry enrichment CPU fix | APPLIED | `strategy/pno/pno_strategy.py`, `research/*` | performance/bugfix | Не обогащать entry-frame через aggTrades, если enriched levels уже не даёт Stage1-кандидатов; вернуть intended stale-filter result. | `python -m compileall strategy/pno cli constants.py main.py launcher.py` |
| P036 | PNO skip full source-entry enrichment | APPLIED | `strategy/pno/pno_strategy.py`, `research/*` | performance | Для seconds-entry TF не обогащать весь 1m source entry-frame через aggTrades; оставить sparse materialization engine после Stage1. | `python -m compileall strategy/pno cli constants.py main.py launcher.py` |
| P037 | PNO skip redundant sparse Stage1 precheck | APPLIED | `strategy/pno/pno_strategy.py`, `research/*` | performance | Не делать wrapper-level fast Stage1 scan в sparse-entry режиме; engine уже делает обязательную Stage1-проверку перед materialization. | `python -m compileall strategy/pno cli constants.py main.py launcher.py` |
| P038 | Reuse PNO backtest diagnostics export cache | PROPOSED | `vectorbt_runner/backtest_runner.py`, `cli/commands.py`, `research/*` | performance/diagnostics | Кэшировать trades+diagnostics в runner и переиспользовать при artifact export после `--collect-diagnostics true`. | `python -m compileall vectorbt_runner cli strategy/pno constants.py main.py launcher.py` |
| P039 | PNO trade-count chart bars | PROPOSED | `cli/pno_diagnostics.py`, `research/*` | diagnostics/chart | Рисовать `Trades %` как exchange trade-count per candle из entry plot frame с fallback на levels frame, если entry-count отсутствует. | `python -m compileall cli/pno_diagnostics.py` |
| P040 | PNO diagnostics logging/summary fix | APPLIED | `cli/commands.py`, `cli/pno_diagnostics.py`, `research/*` | diagnostics/logging | Исправить logging TypeError в diagnostics export, предупреждать о коротком PNO окне и писать full rejection summary. | `python -m compileall cli/pno_diagnostics.py cli/commands.py` |
| P041 | PNO seconds-entry/stale-level/BE/log cleanup | PROPOSED | `cli/commands.py`, `cli/pno_diagnostics.py`, `strategy/pno/config.py`, `strategy/pno/engine.py`, `strategy/pno/pno_strategy.py`, `research/*` | bugfix/logging/risk | `15s` использует тот же sparse aggTrades path, что `30s`/`5s`; Stage5 режет уже reclaimed level до позиции; BE arm снижен до 60%; убраны лишние runtime logs. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py` |
| P043 | Human BOS obsolete-level guard | PROPOSED | `strategy/pno/engine.py`, `cli/pno_diagnostics.py`, `research/*` | bugfix/diagnostics | Убрать bypass Stage4 scoring/Stage5 decay для `human_bos`; Stage1 rejected reasons без near-threshold rows получают fallback charts. | `python -m compileall strategy/pno cli constants.py main.py launcher.py` |
| P044 | Position terminology and strict flow data | UNKNOWN in ZIP | `domain/*`, `simulation/*`, `vectorbt_runner/*`, `strategy/pno/*`, `cli/*`, `research/*` | refactor/data-quality/diagnostics | Развести exchange trades и bot positions; требовать real quote_volume USDT/trade-count; добавить diagnostics coverage и Stage5 unique setup summary. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |
| P045 | Sparse entry data-quality gate | PROPOSED | `strategy/pno/engine.py`, `cli/pno_diagnostics.py`, `research/*` | bugfix/diagnostics | Для sparse entry TF откладывать entry data-quality gate до aggTrades materialization; levels data quality остаётся ранним; пустые research CSV пишутся с колонками. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |
| P048 | Explicit trade-data enrichment failure | APPLIED locally / UNKNOWN commit | `strategy/pno/pno_strategy.py`, `research/*` | data-quality/diagnostics | `enrich_with_trade_data` возвращает typed result; провал enrichment становится явным `trade_data_enrichment_failed`, а не raw-frame fallback. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py` |
| P049 | Canonical PNO trade-count only | APPLIED locally / UNKNOWN commit | `strategy/pno/engine.py`, `strategy/pno/pno_strategy.py`, `research/*` | data-quality | PNO trading path доверяет только `number_of_trades`; legacy `trades`/`trade_count` не легализуют flow-data. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py` |
| P050 | No ticker quote-volume proxy | APPLIED locally / UNKNOWN commit | `data/exchanges/ccxt_futures_client.py`, `cli/commands.py`, `research/*` | data-quality | Не заменять missing ticker `quoteVolume` на `baseVolume * last`; proxy хранить только как ignored diagnostics metadata. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py` |
| P051 | Explicit sparse-entry materialization failure | APPLIED locally / UNKNOWN commit | `strategy/pno/engine.py`, `research/*` | data-quality/diagnostics | Sparse entry materialization возвращает typed status/reason вместо пустого OHLCV-frame fallback; причины loader/window/load/empty/insufficient bars видны в diagnostics. | `python -m compileall strategy/pno cli constants.py main.py launcher.py` |
| P052 | Explicit DataPreparer load status | APPLIED locally / UNKNOWN commit | `vectorbt_runner/data_preparer.py`, `vectorbt_runner/__init__.py`, `cli/commands.py`, `research/*` | data-quality/diagnostics | DataPreparer возвращает typed status/reason для missing symbol/file/schema/window/invalid rows; PNO backtest и check-quality больше не сводят эти причины к одинаковому empty frame. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |
| P053 | Canonical diagnostics trade-count only | APPLIED locally / UNKNOWN commit | `cli/pno_diagnostics.py`, `research/*` | diagnostics/chart | Diagnostics/charts читают только `number_of_trades`; legacy `trades`/`trade_count` маркируются как ignored, missing trade-count не рисуется как нули. | `python -m compileall cli/pno_diagnostics.py research/PATCH_LOG.md research/RESEARCH_STATE.md` |
| P054 | Per-symbol data-load artifacts | APPLIED locally / UNKNOWN commit | `cli/commands.py`, `research/*` | diagnostics/data-quality | Сохранять per-symbol/role/timeframe `data_load_status.csv` и `data_load_rejections.csv`; linked from run_context. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |
| P055 | Sparse seconds load-status propagation | PROPOSED | `strategy/pno/pno_strategy.py`, `strategy/pno/engine.py`, `research/*` | data-quality/diagnostics | Пробрасывать typed seconds/aggregated-window statuses в sparse materialization diagnostics; `sparse_entry_no_loaded_frames` больше не теряет первопричины cache/schema/window/fetch. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |
| P076 | Strict PNO generation and human_bos parity | APPLIED locally / UNKNOWN commit | `strategy/pno/pno_strategy.py`, `strategy/pno/engine.py`, `research/*` | bugfix/strategy | Не маскировать `None` как 0 positions; `human_bos` больше не получает provisional auto-valid score и Stage5 close-trigger bypass. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |
| P079 | Sparse entry audit trail and sizing | PROPOSED | `strategy/pno/engine.py`, `cli/commands.py`, `research/*` | data-quality/diagnostics | Писать run-level sparse target-entry materialization status, явно разделить source/target entry TF в data_load_status/run_context и расширить sparse pre-roll до required bars. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |
| P080 | Propagate sparse materialization diagnostics | PROPOSED / compile verified locally | `strategy/pno/pno_strategy.py`, `research/*` | data-quality/diagnostics | Не терять sparse-entry materialization context при merge category diagnostics; заполнять sparse_entry_materialization_status.csv и seconds_load_status.csv. | `python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain` |


---

## P052 — Explicit DataPreparer load status

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality / diagnostics
Trading logic changed: no; data loading failures become explicit instead of empty-frame fallbacks
Files: vectorbt_runner/data_preparer.py, vectorbt_runner/__init__.py, cli/commands.py, research/*
Commit: UNKNOWN
Base: P048/P049/P050/P051 user-applied locally; exact commit UNKNOWN
```

Проблема:

```text
DataPreparer возвращал pd.DataFrame() для разных причин: missing symbol dir, missing timeframe parquet, missing required OHLCV columns, empty requested window и invalid OHLCV rows.
PNO backtest preparation и check-quality видели это как одинаковое "нет данных", поэтому cache/schema/path failures могли выглядеть как честное отсутствие сетапов.
```

Изменение:

```text
Добавлен SymbolDataLoadResult(frame/ok/status/reason/path/missing_columns/raw_rows/prepared_rows).
load_symbol_data_result/load_symbol_data_range_result дают явный статус, а старые load_symbol_data/load_symbol_data_range оставлены только как compatibility wrappers.
PNO backtest preparation считает data_load_rejections по точным причинам и сохраняет их в run_context.json.
check-quality логирует конкретный load status вместо generic "данных нет".
```

Проверка:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Ожидаемый rerun check:

```text
Если символ пропущен из-за missing parquet/schema/window/invalid OHLCV, run_context.data_load_rejections должен показать точную причину, а не только общий missing_levels_tf/missing_entry_tf.
```

---

## P051 — Explicit sparse-entry materialization failure

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality / diagnostics
Trading logic changed: no; failed sparse materialization now stops with explicit diagnostics instead of an empty-frame fallback
Files: strategy/pno/engine.py, research/*
Commit: UNKNOWN
Base: P048/P049/P050 user-applied locally; exact commit UNKNOWN
```

Проблема:

```text
_materialize_sparse_entry_frame возвращал пустой OHLCV-shaped DataFrame при missing loader, отсутствии Stage1 fetch windows, пустых loaded windows или пустом prepared materialized frame.
Дальше engine видел это как общий insufficient_sparse_entry_data, из-за чего config/data-loader/cache failures выглядели как честный no-entry-data.
source_entry_frame передавался в метод, но сразу удалялся, создавая ложное впечатление fallback path.
```

Изменение:

```text
Sparse entry materialization возвращает SparseEntryMaterializationResult(frame/status/reason/windows_requested/windows_loaded).
Убрана передача неиспользуемого source_entry_frame: raw source entry frame больше не выглядит как допустимый fallback.
Diagnostics получает точные причины: sparse_entry_loader_missing, sparse_entry_no_stage1_windows, sparse_entry_no_loaded_frames, sparse_entry_materialized_frame_empty, sparse_entry_materialized_insufficient_bars.
```

Проверка:

```text
python -m compileall strategy/pno cli constants.py main.py launcher.py
```

Ожидаемый rerun check:

```text
Если seconds loader не подключён или не вернул окна/фреймы, Stage2 rejection должен иметь конкретный sparse_entry_* reason, а не generic insufficient_sparse_entry_data.
```

---

## P050 — No ticker quote-volume proxy

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality
Trading logic changed: yes; stricter universe/liquidity data acceptance only
Files: data/exchanges/ccxt_futures_client.py, cli/commands.py, research/*
Commit: UNKNOWN
Base: P048/P049 user-applied locally; exact commit UNKNOWN
```

Проблема:

```text
get_futures_symbols_with_liquidity_metrics заменял missing ticker quoteVolume на baseVolume * last.
Такой proxy мог пройти как quote_volume, повлиять на liquidity_score/universe selection и скрыть неверный ticker payload.
```

Изменение:

```text
quote_volume теперь берётся только из real ticker quoteVolume.
baseVolume * last считается только в quote_volume_proxy и не влияет на quote_volume/liquidity_score/selection.
При отсутствии real quoteVolume добавляются no_real_quote_volume, missing_real_quote_volume и quote_volume_proxy_available_ignored.
CLI сохраняет quote_volume_source/quote_volume_proxy в liquidity metadata для диагностики.
```

Проверка:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py
```

Ожидаемый rerun check:

```text
Ticker payload без quoteVolume, но с baseVolume/last, должен дать quote_volume=0.0, liquidity_score=0.0, quote_volume_proxy>0.0 и quality flag quote_volume_proxy_available_ignored.
```

---

## P049 — Canonical PNO trade-count only

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality
Trading logic changed: yes; stricter market-data acceptance only
Files: strategy/pno/engine.py, strategy/pno/pno_strategy.py, research/*
Commit: UNKNOWN
Base: P048 user-applied locally; exact commit UNKNOWN
```

Проблема:

```text
PNO принимал первый доступный trade-count alias из number_of_trades / trades / trade_count.
Legacy или proxy column мог выглядеть как настоящий exchange trade-count и проходить flow/tape gates.
```

Изменение:

```text
PNO engine считает real trade-count только из canonical number_of_trades.
trades/trade_count больше не выбираются как source для _resolve_trade_activity_series и не попадают в optional PNO market-data columns.
Если canonical number_of_trades отсутствует, diagnostics source label показывает missing_canonical_number_of_trades или legacy_*_ignored, а quality reason становится *_missing_canonical_number_of_trades.
Trade enrichment больше не размножает canonical count в aliases; back-propagation в исходные frames копирует только canonical trade-data columns.
```

Проверка:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py
```

Ожидаемый rerun check:

```text
Frame с trades/trade_count без number_of_trades должен получить market_data_quality_status=failed, source label legacy_*_ignored или missing_canonical_number_of_trades, и не проходить Stage1 flow gates.
```

---

## P048 — Explicit trade-data enrichment failure

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality / diagnostics
Trading logic changed: no; failed enrichment now stops with explicit diagnostics instead of raw-frame fallback
Files: strategy/pno/pno_strategy.py, research/*
Commit: UNKNOWN
```

Проблема:

```text
enrich_with_trade_data мог вернуть исходный OHLCV frame при пустом input, invalid window, пустом aggTrades window или отсутствии real trade-count.
Дальше engine видел общий missing_required_market_data, а причина fetch/cache/schema терялась.
```

Изменение:

```text
trade-data enrichment возвращает typed TradeDataEnrichmentResult.
Любой failed enrichment до engine превращается в Stage1 rejection trade_data_enrichment_failed с reason: input_frame_empty, input_frame_missing_timestamp, enrichment_window_invalid, aggtrades_unavailable, aggtrades_missing_real_trade_count, enrichment_missing_real_trade_count или enrichment_missing_quote_volume.
Raw-frame fallback больше не считается успешным enrichment.
```

Проверка:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py
```

Ожидаемый rerun check:

```text
Если aggTrades/window/schema реально недоступны, diagnostics должны показывать trade_data_enrichment_failed и конкретный trade_data_enrichment_reason, а не только missing_required_market_data.
```

---

## P045 — Sparse entry data-quality gate

```text
Status: PROPOSED
Type: bugfix / diagnostics
Trading logic changed: no; execution path/data-quality ordering changed
Files: strategy/pno/engine.py, cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
```

Проблема:

```text
5m/15s и 5m/30s sparse entry TF валидировали target entry trade-count/quote_volume до materialization из aggTrades.
Из-за этого run мог получить 508/508 missing_required_market_data на Stage1 и не проверить PNO-воронку.
```

Изменение:

```text
levels data quality проверяется до Stage1; entry data quality для sparse TF — после materialized aggTrades entry frame.
Если sparse materialization даёт мало баров или неполные trade-data fields, diagnostics пишет явный Stage2 rejection.
Пустые research_context CSV получают стабильные колонки, а не пустой файл без header.
```

Проверка:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Ожидаемый rerun check:

```text
5m/15s и 5m/30s больше не должны массово резаться как entry_missing_real_trade_count до sparse materialization.
Если entry aggTrades реально недоступны, причина должна перейти в insufficient_sparse_entry_data или missing_required_entry_market_data.
```

---

## 3. P001 — Stage4 dedup

```text
Status: UNKNOWN
Trading logic changed: no
```

Проблема:

```text
один и тот же BOS/level может попасть в Stage4 review несколько раз
```

Цель:

```text
считать unique setup, а не повторные rows
```

Ожидаемый эффект на последнем известном run:

```text
Stage4 rows: 7 → 4 unique setups
```

Риск:

```text
слишком грубый key может схлопнуть разные setups
```

---

## 4. P002 — Stage5 explicit reject reasons

```text
Status: UNKNOWN
Trading logic changed: no, если только logging
```

Проблема:

```text
Stage5 no-trade мог быть без точной причины
```

Цель:

```text
no_close_above / actual_entry_pos_too_high / net_rr_too_low / entry above TP1 / etc.
```

Риск:

```text
patch не должен менять flow control и торговые решения
```

---

## 5. P003 — Trades % charts

```text
Status: UNKNOWN
Trading logic changed: no
```

Проблема:

```text
графики показывали volume, но не trade-count
```

Цель:

```text
отдельная панель Trades %
```

Риск:

```text
нули могут означать отсутствие поля, а не отсутствие позиций
```

---

## 6. P004 — True trade-count data

```text
Status: PROPOSED / UNKNOWN
Trading logic changed: indirectly yes
```

Проблема:

```text
volume proxy вместо real number_of_trades делает flow-фильтры менее достоверными
```

Цель:

```text
использовать number_of_trades / quote_volume / taker fields, если доступны
```

Ожидаемые diagnostics:

```text
trade_count_proxy_used: false
levels_trade_count_source: number_of_trades
entry_trade_count_source: number_of_trades
levels_quote_volume_source: quote_volume
entry_quote_volume_source: quote_volume
```

Риски:

```text
неполные aggTrades
timestamp mismatch
старый Stage1 cache
изменение Stage1 pass/reject distribution
```

---

## 7. P005 — Repo cleanup

```text
Status: APPLIED
Type: cleanup
Trading logic changed: no
Files: .gitignore, .run/*, logs/parquet-storage.log, utils/validators.py
Commit: 5c5447dbf98bbddb33c5b99dcf6d17fa75b23a8b
```

Проблема:

```text
в репозитории были локальные IDE run configs, пустой log-файл и пустой неиспользуемый validators.py
```

Изменение:

```text
удалены .run stage configs, logs/parquet-storage.log и utils/validators.py
добавлены ignore rules для .run/, logs/ и *.log
.env намеренно не трогался
```

Проверка:

```text
CI/status checks отсутствуют; локально проверить python -m compileall strategy/pno cli constants.py
```

---

## 8. P006 — PNO entry/data fetch hardening

```text
Status: PROPOSED
Type: bugfix / data quality
Trading logic changed: no
Files: launcher.py, strategy/pno/pno_strategy.py
Follow-up to: code review
Supersedes: none
```

Problem:
```text
launcher.py показывал baseline_cross, хотя PNO должен входить только через close_above.
aggTrades enrichment window заканчивался на open timestamp последней свечи, а не на её закрытии.
live aggTrades fallback пагинировал по timestamp, что могло пропустить часть trades при 1000+ aggTrades в одном ms.
```

Change:
```text
оставить в launcher только close_above
расширить enrichment window до конца последней candle
перевести live aggTrades fallback на fromId-pagination и обрезать результат по исходному timestamp window
```

Lookahead:
```text
расширение окна использует только данные внутри той же свечи, которая уже есть в OHLCV-фрейме;
для backtest/diagnostics это не добавляет данных из следующих свечей.
```

Verification:
```text
python -m compileall strategy/pno launcher.py
python launcher.py --help
```

---

## 9. P007 — PNO aggTrades helper hotfix

```text
Status: PROPOSED
Type: bugfix
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P006
Supersedes: none
```

Problem:
```text
P006 добавил вызовы _resolve_agg_trade_timestamp, _resolve_agg_trade_id и _clip_agg_trades_to_window,
но сами helper-методы отсутствуют в pno_strategy.py.
```

Change:
```text
добавить helper-методы для live aggTrades fromId-pagination
унифицировать empty archive result через _empty_seconds_frame
обновить research state под текущий head
```

Verification:
```text
python -m compileall strategy/pno launcher.py
python launcher.py --help
```

---

## 10. P008 — PNO aggTrades typing/client boundary

```text
Status: PROPOSED
Type: typing / refactor
Trading logic changed: no
Files: data/exchanges/ccxt_types.py, data/exchanges/ccxt_futures_client.py, strategy/pno/pno_strategy.py
Follow-up to: P007
Supersedes: none
```

Problem:
```text
PNO provider напрямую обращается к private ccxt client internals.
aggTrades payload типизирован как dict[str, object].
fallback по generic id может скрыть неверный payload.
неожиданная схема aggTrades может падать KeyError вместо явного empty result.
```

Change:
```text
добавить CcxtAggTradePayload TypedDict
добавить wrapper methods get_market_id/fetch_binance_agg_trades в CcxtFuturesClient
перевести PNO provider на wrapper boundary
убрать generic id fallback
явно валидировать aggTrades columns перед агрегацией
```

Verification:
```text
python -m compileall data/exchanges strategy/pno launcher.py
python launcher.py --help
```

---

## 11. P009 — Add 5m/15s PNO TF set

```text
Status: APPLIED
Type: experiment config
Trading logic changed: no
Files: domain/enums/timeframe.py, strategy/pno/config.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md, research/EXPERIMENT_LOG.md
Follow-up to: E003
Supersedes: none
Commit: a4857715b556693c74c10993b74249278275ec16
```

Problem:

```text
E003 должен сравнить три TF-set, но 5m/15s не был доступен:
Timeframe.S15 отсутствовал, а PNO_BACKTEST_TIMEFRAME_PAIRS содержал только 5m/30s и 1m/5s.
```

Change:

```text
добавить Timeframe.S15
добавить (Timeframe.M5, Timeframe.S15) в PNO_BACKTEST_TIMEFRAME_PAIRS
```

Expected diagnostics:

```text
--pno-all-tf-pairs прогоняет 3 TF-set: 5m/30s, 5m/15s, 1m/5s
```

Verification:

```text
python -m compileall domain/enums strategy/pno cli constants.py main.py launcher.py
```

---

## 12. P010 — Rewrite README for PNO research workflow

```text
Status: APPLIED
Type: docs
Trading logic changed: no
Files: README.md, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P009
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
README был слишком коротким и содержал устаревшие PNO examples с 5m/1m.
Он не отражал текущий research workflow: 3 TF-set, diagnostics, research memory и data-quality checks.
```

Change:

```text
переписать README как практический PNO research guide
зафиксировать актуальные TF-set: 5m/30s, 5m/15s, 1m/5s
добавить команды data prep, multi-TF backtest, stage diagnostics, artifact layout, sanity checks
явно указать ограничения: PNO не готовая торговая система, малое число trades не оценивает прибыльность
```

Verification:

```text
python -m compileall domain/enums data/exchanges strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
docs-only; риск только в устаревании команд при будущих CLI изменениях
```

---

## 13. P011 — Humanize runtime logs in Russian

```text
Status: APPLIED
Type: logging / docs
Trading logic changed: no
Files: constants.py, launcher.py, utils/retry.py, data/*, vectorbt_runner/backtest_runner.py, cli/commands.py, research/*
Follow-up to: P010
Supersedes: none
Commit: 1a3576f9c0ab0ba4bfabd30d2bd0d529a7fdd720
```

Problem:

```text
Runtime-логи были смешаны: часть на английском, часть техническими key=value строками, часть слишком шумная для чтения во время прогона.
```

Change:

```text
перевести user-facing runtime logs на короткий русский
сохранить технические reason-коды и CSV-поля для аналитики
оставить подробную диагностику в debug, а process/status сообщения сделать читабельными
```

Verification:

```text
python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py
```

Risk:

```text
ошибки в количестве logger placeholder/arguments проявляются только в runtime, не всегда на compile
```

---

## 14. P012 — Fix P011 logging follow-up

```text
Status: PROPOSED
Type: bugfix / bookkeeping
Trading logic changed: no
Files: vectorbt_runner/backtest_runner.py, launcher.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P011
Supersedes: none
Commit: 9c2e31cb540eb044f9d9f0a671b744fcfb3426f7
```

Problem:

```text
В одном logger.info после P011 осталось больше аргументов, чем placeholder.
Также P010/P011 были уже применены, но research state продолжал считать их proposed/local diff.
```

Change:

```text
убрать лишние аргументы из long-symbol progress log
отформатировать итоговый logger.info
убрать лишний пробел в prompt launcher menu
перевести P010/P011 в APPLIED в research bookkeeping
```

Verification:

```text
python -m compileall vectorbt_runner launcher.py
python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py
```

Risk:

```text
логика бэктеста не меняется; риск ограничен форматированием логов и research bookkeeping
```

---

## 15. P013 — Narrative runtime logs

```text
Status: PROPOSED
Type: logging / docs
Trading logic changed: no
Files: constants.py, utils/retry.py, data/fetchers/*, data/exchanges/ccxt_futures_client.py, vectorbt_runner/backtest_runner.py, cli/commands.py, research/*
Follow-up to: P011/P012
Supersedes: none
Commit: ba8cd69e5cdc6ccdb36f7018c0c5d32540bf8669
```

Problem:

```text
P011 в основном перевёл логи, но не поменял ощущение консоли: она всё ещё звучала как сухой поток технических событий.
Для длинных PNO-прогонов нужна связная история: что началось, что проверяется, где рынок молчит, где есть разрывы, чем всё закончилось.
```

Change:

```text
переписать INFO/WARNING runtime logs в единый narrative style
успешные retry-сообщения перенести в DEBUG, чтобы INFO не тонул в сетевом шуме
оставить числа, прогресс и причины, но оформить их как понятные этапы прогона
сохранить технические поля и reason-коды там, где они нужны для CSV/diagnostics
```

Expected diagnostics:

```text
консоль читает прогон как последовательность глав: данные → кэш → символы → сетка → причины отсева → финал
меньше успешных API retry сообщений в INFO
warning/error остаются содержательными
```

Verification:

```text
python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --days 3 --top-n 20 --light-run true
```

Risk:

```text
логика стратегии не меняется; риск в том, что слишком художественные логи могут скрыть техническую точность, поэтому числовые поля сохранены
```

---

## 37. P034 — Diagnostics initial progress

```text
Status: PROPOSED
Type: logging / diagnostics
Trading logic changed: no
Files: cli/commands.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P031/P032
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
_export_pno_diagnostics_context_for_symbols логировал progress только внутри symbol loop.
Стартовая строка checked=0 вообще не эмитилась.
Кроме того, checkpoint стоял после continue для символов без trades/stage events/rejections, поэтому progress мог пропускаться на пустых символах.
```

Change:

```text
перед циклом печатать Диагностика: 0 из N. ETA: --ч --м --с
перенести periodic checkpoint до early-continue пустого символа
```

Verification:

---

## 16. P014 — Polish console logs

```text
Status: PROPOSED
Type: logging / docs
Trading logic changed: no
Files: constants.py, utils/retry.py, cli/*, data/*, strategy/factory.py, vectorbt_runner/*, research/*
Follow-up to: P013
Supersedes: none
Commit: 24f3ecbc857cc27bb728a848438e7c66940f7cc8
```

Problem:

```text
После P013 в консоли ещё оставались английские help/error строки и сухие сообщения вида key=value.
Пример: output_root=..., strategy_output=..., pre-rank symbols_total=...
Такие строки полезны машине, но плохо читаются человеком во время длинного прогона.
```

Change:

```text
переписать run-backtest стартовые сообщения как короткие предложения
разбить длинные многочастные сообщения на несколько строк
перевести user-facing validation/error descriptions
сохранить технические ключи только там, где они являются частью CSV/diagnostics contracts
```

Verification:

```text
python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --days 3 --top-n 20 --light-run true
```

Risk:

```text
логика стратегии не меняется; риск только в тексте консольных сообщений
```

---

## 17. P015 — Backtest progress and memory logs

```text
Status: PROPOSED
Type: logging / bugfix
Trading logic changed: no
Files: vectorbt_runner/backtest_runner.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P014
Supersedes: none
Commit: 013a68eb51e3e0c49f2c56da4bd32a422e44fc82
```

Problem:

```text
При одной grid-комбинации console progress писал одновременно "глава" и "весь путь", хотя это один и тот же прогон.
При нехватке памяти пользователь видел сырой NumPy error: Unable to allocate ...
Также по логу было не сразу ясно, что в работу ушли все 509 символов.
```

Change:

```text
для total == 1 писать общий progress без "глава/весь путь"
для total > 1 оставить chapter narrative
добавить план бэктеста: количество комбинаций и символов
предупреждать, если символов >= 200
перехватывать MemoryError в portfolio и per-symbol paths, логировать понятный русский текст и подсказку уменьшить --top-n/--days/diagnostics
```

Expected console:

```text
План бэктеста: 1 комбинаций и 509 символов.
В работу ушло 509 символов. Это тяжёлый прогон; если ожидался короткий тест, проверь --top-n.
Дошёл до символа 250/509. Прошло 0ч 5м 56с. Сейчас смотрю KERNEL/USDT:USDT.
Бэктест идёт: 250/509 символов, 49.1%. Прошло 0ч 5м 58с, осталось около 0ч 6м 11с. Последний символ: KERNEL/USDT:USDT.
Прогон остановлен: не хватило памяти.
```

Verification:

```text
python -m compileall vectorbt_runner research/PATCH_LOG.md research/RESEARCH_STATE.md
python main.py run-backtest --strategy pno --pno-all-tf-pairs --days 3 --top-n 20 --light-run true
```

Risk:

```text
логика стратегии и расчёта позиций не меняется; меняются только progress/error logs и re-raise MemoryError с русским сообщением
```

---

## 18. P016 — Fix unclosed logger call

```text
Status: APPLIED
Type: bugfix
Trading logic changed: no
Files: vectorbt_runner/backtest_runner.py
Follow-up to: P015
Supersedes: none
Commit: 013a68eb51e3e0c49f2c56da4bd32a422e44fc82
```

Problem:

```text
P015 оставил незакрытый logger.info и ломал import backtest_runner.py с SyntaxError.
```

Change:

```text
закрыть вызов logger.info
```

Risk:

```text
P016 устранил syntax error, но не восстановил потерянный portfolio_positions assignment и аргументы logger.info.
```

---

## 19. P017 — Fix P015 runner regression

```text
Status: PROPOSED
Type: bugfix
Trading logic changed: no
Files: vectorbt_runner/backtest_runner.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P015/P016
Supersedes: none
Commit: 9508a095fd10c7a628c78692fdcce6616d39c414
```

Problem:

```text
После P015/P016 BacktestRunner.run обращался к portfolio_positions до присваивания.
Long-symbol logger в multi-combo ветке также передавал один аргумент на семь placeholder.
```

Change:

```text
вернуть вызов strategy.generate_events_portfolio под MemoryError guard
восстановить все аргументы long-symbol logger
добавить single-combo long-symbol сообщение
```

Verification:

```text
python -m compileall vectorbt_runner/backtest_runner.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --days 3 --top-n 20 --light-run true
```

---

## 20. P018 — Stale reclaim and trade-count chart fix

```text
Status: PROPOSED
Type: bugfix / diagnostics
Trading logic changed: yes
Files: cli/pno_diagnostics.py, strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: MAGMA_USDT_USDT_001_sl review
Supersedes: none
Commit: a604d9ca206fbe12a9a3dd78a1140470be123e4a
```

Problem:

```text
MAGMA_USDT_USDT_001_sl показал две проблемы.
1. Trade-count panel на графике пустой, хотя diagnostics говорят, что source = number_of_trades.
2. Trade прошёл после того, как уровень уже был пробит вверх и закрыт обратно под уровнем; такой reclaim потерял актуальность.
```

Change:

```text
добавить канонический trade_count для графиков из number_of_trades/trades/trade_count
протащить trade-count columns в plot frame
после генерации PNO trades отбрасывать позиции, где между level_valid_timestamp_ms и entry_signal_timestamp_ms была свеча high > level и close < level
```

Expected diagnostics:

```text
trade-count panel заполняется при наличии number_of_trades
MAGMA-like stale reclaim trades исчезают из results/trades/charts
```

Verification:

```text
python -m compileall cli/pno_diagnostics.py strategy/pno/pno_strategy.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
пост-фильтр убирает trades после failed reclaim; если later reclaim должен считаться новым setup, его нужно создавать как новый Stage4 setup, а не переиспользовать старый уровень
```

---

## 21. P019 — Clear PNO trade-data caches

```text
Status: APPLIED
Type: memory
Trading logic changed: no
Files: strategy/pno/pno_strategy.py
Follow-up to: P018
Supersedes: none
Commit: 8b61debb608ecb7f6d08107eb9aabfbd5a792964
```

Problem:

```text
Full-universe run держал в памяти сотни aggTrades-derived windows и мог упереться в RAM.
```

Change:

```text
сбрасывать runtime/shared caches после обработки символа
оставить persistent sparse cache на диске
```

---

## 22. P020 — Trade/chart consistency fix

```text
Status: PROPOSED
Type: bugfix / diagnostics
Trading logic changed: yes
Files: cli/pno_diagnostics.py, strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P018
Supersedes: none
Commit: bc41b54a87ac411740f17536dd3429e40b2bfd46
```

Problem:

```text
В results могло быть 2 trades, а chart directory показывал только один валидный график.
Trades panel оставалась пустой, потому что разные части pipeline смотрели на разные aliases: number_of_trades / trades / trade_count.
MAGMA-like stale level не отбрасывался, если до финального сигнала уже был close_above, но не было failed close back below level.
```

Change:

```text
заполнять все aliases number_of_trades/trades/trade_count в diagnostics plot frames
заполнять те же aliases сразу после aggTrades enrichment в PNO strategy wrapper
считать уровень stale, если close_above по уровню случился до финального entry_signal_timestamp_ms
```

Verification:

```text
python -m compileall cli/pno_diagnostics.py strategy/pno/pno_strategy.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

---

## 23. P021 — True trade-count chart propagation

```text
Status: PROPOSED
Type: bugfix / diagnostics
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P020
Supersedes: none
Commit: ac6c7c505306fd2aac52b145837bd2a560c36386
```

Problem:

```text
PNO calculation used enriched copies with real aggTrades columns, but chart export received the original raw SymbolMtfFrames.
As a result the strategy saw number_of_trades, while the Trades panel on the saved chart was empty.
```

Change:

```text
after enriching levels/entry frames, copy real quote_volume/taker_buy_volume/taker_buy_quote_volume/number_of_trades/trades/trade_count back into the original frames by timestamp
do not use volume/taker_buy_volume as trade-count proxy
keep chart export using real number_of_trades aliases only
```

Expected diagnostics:

```text
Trades panel on trade charts is populated from real number_of_trades/trades/trade_count
strategy calculations and chart export read the same enriched market-activity columns
```

Verification:

```text
python -m compileall strategy/pno/pno_strategy.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
логика позиций не меняется; исходные DataFrame получают дополнительные diagnostic columns, чтобы chart export видел тот же trade-count, что и strategy path
```

---

## 24. P022 — Stage5/results consistency fix

```text
Status: PROPOSED
Type: bugfix / diagnostics
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, vectorbt_runner/backtest_runner.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P021
Supersedes: none
Commit: 56f05338910241286c1cd36e8de6f9aa34f68475
```

Problem:

```text
Stale-level фильтр убирал MAGMA из итоговых trades/charts, но Stage5 review всё ещё показывал его как passed.
Для multi-TF runs строка results.csv могла сохранять базовые 5m/30s вместо runtime TF пары.
```

Change:

```text
разделять stale-level trades на kept/stale
переносить соответствующие stage_5_position events из passed в rejected с reason=level_stale_before_signal
уменьшать stage_hits/positions_generated после переноса
собирать results row из params после runtime levels_timeframe/entry_timeframe injection
```

Expected diagnostics:

```text
stage_5_position/passed/events.csv, results.csv, position_context.csv и charts показывают один и тот же набор валидных trades
MAGMA-like stale reclaim попадает в stage_5_position/rejected/events.csv
1m_5s results.csv пишет pno_levels_timeframe=1m и pno_entry_timeframe=5s
```

Verification:

```text
python -m compileall strategy/pno/pno_strategy.py vectorbt_runner/backtest_runner.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
Trading logic не меняется: stale trades уже не попадали в итоговые trades. Меняется согласованность diagnostics и results row.
```

---

## 25. P023 — PNO none-trades guard

```text
Status: PROPOSED
Type: bugfix
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, vectorbt_runner/backtest_runner.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P022
Supersedes: none
Commit: 1877d2891b1f537a6326857e67cd74e5d761ad96
```

Problem:

```text
После Stage5 diagnostics consistency patch один из no-trade путей PNO мог вернуть None вместо [].
BacktestRunner ожидал iterable и падал на первом символе: TypeError: 'NoneType' object is not iterable.
```

Change:

```text
generate_events_multi_tf возвращает [] при пустом/None результате stale-level фильтра
BacktestRunner получает defensive guard: если стратегия вернула None, логирует предупреждение и продолжает с пустым списком позиций
```

Expected result:

```text
Символы без позиций больше не валят прогон.
Контракт strategy.generate_events_multi_tf снова list[PositionResult].
```

Verification:

```text
python -m compileall strategy/pno/pno_strategy.py vectorbt_runner/backtest_runner.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
Если стратегия вернёт None из-за будущей ошибки, runner не упадёт сразу, а продолжит с warning; это осознанная защита вокруг контрактного пустого результата.
```

---

## 26. P024 — Concise backtest logs

```text
Status: PROPOSED
Type: logging
Trading logic changed: no
Files: vectorbt_runner/backtest_runner.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P023
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
Runtime logs стали слишком разговорными и шумными: тяжёлый прогон, главы, весь путь, причины отсутствия входов.
В консоли нужен короткий progress по TF-паре и итоговая сводка.
```

Change:

```text
логировать запуск бэктеста и число отобранных символов
для каждой TF-пары логировать progress 0/10/20/.../100%
убрать symbol-by-symbol narrative, heavy-run warning, zero-entry explanation и общий финальный шум
выводить итог TF-пары: Позиций нет или Позиций/Винрейт/PF/PnL/DD/SL/TP1_BE/TP2
```

Expected console:

```text
Запуск бэктеста
Отобрано 509 символов.
Таймфреймы: 5m-30s
  0% Проверено: 0 из 509.  ETA: --ч --м --с
 10% Проверено: 51 из 509.  ETA: 00ч 23м 16с
...
100% Проверено: 509 из 509.  ETA: 00ч 00м 00с
Анализ 5m-30s завершён
Позиций: 17
Винрейт: 0.8400
```

Verification:

```text
python -m compileall vectorbt_runner/backtest_runner.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

---

## P035 — PNO lazy entry enrichment CPU fix

```text
Status: APPLIED
Type: performance / bugfix
Trading logic changed: no new rules; bugfix can change results from erroneous all-drop to intended stale-filtered trades
Files: strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P019/P021/P022/P023
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
PnoStrategy.generate_events_multi_tf eagerly enriched both levels and entry frames with aggTrades before the cheap Stage1 gate.
For collect-diagnostics/full-universe runs this loaded/merged entry trade data for symbols that enriched levels would reject at Stage1 anyway.
Also _filter_stale_level_reclaim_trades computed kept/stale trades but returned None, so the caller collapsed non-empty trade lists to [].
```

Change:

```text
resolve category profiles once per symbol
enrich levels first
run fast Stage1 candidate check on enriched levels
only enrich entry frame when at least one profile has a Stage1 candidate
reuse the same profile tuple for the actual engine run
return kept_positions after syncing stale-level diagnostics
```

Expected effect:

```text
CPU and IO drop most on --collect-diagnostics true / full-universe runs where most symbols fail Stage1.
No new entry mode, no wick-touch, no changed thresholds.
Symbols with Stage1 candidates still use enriched entry trade data before trade simulation.
```

Verification:

```text
python -m compileall strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
No-candidate diagnostics no longer force entry aggTrades enrichment; Stage1 rejection remains based on enriched levels data.
The stale-filter return fix may reveal trades that were previously dropped by a return-contract bug.
```

---

## P036 — PNO skip full source-entry enrichment

```text
Status: APPLIED
Type: performance
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P035
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
For seconds-entry PNO pairs (`5m/30s`, `5m/15s`, `1m/5s`) the CLI passes a coarser source entry frame, usually 1m.
PnoStrategy still pre-enriched that whole source entry frame with aggTrades when Stage1 existed.
That can aggregate the full backtest entry window even though PnoEngine later materializes only sparse target-entry windows after Stage1.
```

Change:

```text
infer the actual source entry-frame step from timestamps
skip wrapper-level entry enrichment when target entry TF is finer than the source frame
let PnoEngine keep doing sparse seconds materialization after Stage1
keep eager entry enrichment only when the provided entry frame is already at target/finer resolution
```

Expected effect:

```text
Large CPU/IO reduction on seconds-entry runs with at least one Stage1 candidate.
The win is strongest on `--pno-all-tf-pairs` and `--collect-diagnostics true` where full source-entry enrichment was repeated per TF-set.
```

Verification:

```text
python -m py_compile strategy/pno/pno_strategy.py
python -m compileall strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
Low. Trade decisions should remain on the same sparse materialized target-entry data produced by PnoEngine.
Charts for trades already load target-entry windows through the seconds provider when source entry TF is coarser than target.
```

---

## P037 — PNO skip redundant sparse Stage1 precheck

```text
Status: APPLIED
Type: performance
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P035/P036
Supersedes: none
Commit: 3e8a24765fa342a401815c4044ed0d8db78f284e
```

Problem:

```text
After P036, seconds-entry TF pairs use sparse entry materialization inside PnoEngine.
PnoStrategy.generate_events_multi_tf still computed a wrapper-level fast Stage1 candidate scan across all profiles before deciding entry enrichment.
For sparse-entry mode that scan no longer affects enrichment, and PnoEngine repeats the required Stage1 check anyway before materializing seconds windows.
```

Change:

```text
resolve whether sparse entry materialization is required first
only run wrapper-level _profiles_have_fast_stage1_candidate when eager entry enrichment is still possible
skip the redundant wrapper pre-scan for 5m/30s, 5m/15s and 1m/5s sparse-entry paths
```

Expected effect:

```text
Less per-symbol/per-profile CPU before the real engine run, especially on --pno-all-tf-pairs and discovery mode.
No change to Stage1 decisions, entry logic, close_above, thresholds, TP/SL or diagnostics semantics.
```

Verification:

```text
python -m compileall strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
Low. The skipped scan was only a wrapper guard for eager entry enrichment; sparse-entry mode now relies on the engine's existing Stage1 gate.
```

---

## P038 — Reuse PNO backtest diagnostics export cache

```text
Status: PROPOSED
Type: performance / diagnostics
Trading logic changed: no
Files: vectorbt_runner/backtest_runner.py, cli/commands.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P035/P036/P037
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
With --collect-diagnostics true, BacktestRunner already runs PNO per symbol and consumes trades + generation diagnostics.
After the backtest, CLI artifact export called strategy.generate_events_multi_tf again for every artifact row and symbol.
That repeated the CPU-heavy PNO path and could drift from the exact stage review / chart context produced during the runner pass.
```

Change:

```text
BacktestRunner stores a typed per (params_signature, symbol) snapshot with trades and diagnostics when collect_diagnostics is enabled.
PNO diagnostics export computes the same runtime params signature from the selected results row and reuses cached snapshots when available.
All artifact exporters pass the cache through to diagnostics/stage review/chart export.
Missing cache entries fall back to the existing generation path and are counted in debug logs.
```

Expected effect:

```text
No second full strategy pass after --collect-diagnostics true for the normal post-backtest PNO artifact export path.
Stage reviews, diagnostics JSON/CSV and trade charts use the same trades/diagnostics produced by the runner pass.
No change to PNO entry logic, close_above rules, thresholds, TP/SL, ranking, or data loading.
```

Verification:

```text
python -m compileall vectorbt_runner cli strategy/pno constants.py main.py launcher.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
Medium-low. The cache key uses the same params_to_row signature as runner/results reconstruction.
If a future exporter is run from results only, cache is absent and the old generation path remains explicit.
```

---

## P039 — PNO trade-count chart bars

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / chart
Trading logic changed: no
Files: cli/pno_diagnostics.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P021/P038
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
PNO trade charts had a lower `Trades %` subplot, but `_render_pno_position_chart` sliced `levels_window` without `number_of_trades` / `trades` / `trade_count` columns.
As a result `_resolve_trade_count_series(levels_window)` returned zeros and the subplot could be visually empty even when true exchange trade-count data existed in the plot frames.
```

Change:

```text
keep trade-count columns in the levels-frame chart slice when they exist
plot `Trades %` from the entry plot frame first, using exchange trade-count per candle normalized to the local window max
fallback to levels-frame trade-count only if entry-frame trade-count is absent/zero
leave PNO strategy trades, stage decisions, TP/SL and diagnostics generation unchanged
```

Expected effect:

```text
Trade chart bottom subplot shows exchange activity per candle, not PNO strategy trade rows.
Charts no longer silently show a zero trade-count panel when entry/levels frames contain real trade-count fields.
```

Verification:

```text
python -m compileall cli/pno_diagnostics.py
```

Risk:

```text
Low. Plot-only change; it changes chart rendering, not backtest decisions or exported trade rows.
```

---

## P041 — PNO seconds-entry/stale-level/BE/log cleanup

```text
Status: PROPOSED
Type: bugfix / logging / risk management
Trading logic changed: yes
Files: cli/commands.py, cli/pno_diagnostics.py, strategy/pno/config.py, strategy/pno/engine.py, strategy/pno/pno_strategy.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P009 / P018 / P040
Supersedes: wrapper-level stale trade post-filter from P018/P022
Commit: UNKNOWN
```

Problem:

```text
5m/15s was present in the PNO backtest TF set, but run-backtest treated only 30s/5s as seconds-entry pairs and looked for a native 15s cache.
A GUA case entered a level that had already been reclaimed several LTF candles before the executable entry.
The stale-level protection lived as a wrapper-level post-trade filter and could inspect the source entry frame instead of the actual materialized seconds frame.
Runtime logs still printed run path, diagnostics export cache and research-context export progress.
BE was armed at 80% of the move to active high, which was too late for the observed failed continuation cases.
```

Change:

```text
route every PNO entry timeframe below 1m through source 1m cache + sparse aggTrades materialization to the requested seconds TF
reject Stage5 before simulation when the level had any close_above before the current signal candle
make the stale-level reject non-bypassable by human_bos / ideal-like decay relaxation
stop using wrapper-level stale trade post-filter after simulation
set be_arm_to_active_high_fraction and close_above_be_start_fraction from 0.80 to 0.60
suppress research-context/cache/path runtime logs and normalize stage-review chart progress
```

Verification:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
Medium. This changes trade acceptance and BE timing. Expected effect: fewer stale close_above entries and earlier BE protection. Needs same-window comparison across 5m/30s, 5m/15s, 1m/5s.
```

---

## P040 — PNO diagnostics logging/summary fix

```text
Status: PROPOSED
Type: diagnostics / logging
Trading logic changed: no
Files: cli/commands.py, cli/pno_diagnostics.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md, research/EXPERIMENT_LOG.md
Follow-up to: E006 / P032 / P038
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
31-day 5m/30s PNO run completed strategy execution but diagnostics/chart export emitted logging TypeError.
Several logger.debug calls had fewer `%s` placeholders than supplied arguments.
Research context summary used filtered review rejections, so non-reviewable reasons like insufficient_data could disappear from stage_reason_summary.csv.
A 1-day 5m run could proceed despite being shorter than Stage1 min_data_5m=300.
```

Change:

```text
fix debug format strings for research-context and stage-review progress logs
pass full stage_rejections_by_stage separately for stage_reason_summary.csv while keeping filtered rows for heavy context exports
warn early when requested PNO --days cannot satisfy min levels bars
```

Verification:

```text
python -m compileall cli/pno_diagnostics.py cli/commands.py
```

Risk:

```text
Low. No trade decision, threshold, entry, TP/SL or data-fetch logic changes. Only diagnostics/logging/export summary behavior changes.
```

---

## P042 — PNO research-context export helper fix

```text
Status: PROPOSED
Type: diagnostics / export stability
Trading logic changed: no
Files: cli/pno_diagnostics.py, research/PATCH_LOG.md, research/RESEARCH_STATE.md
Follow-up to: P040 / latest diagnostics crash
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
After diagnostics reached 508/508 symbols, research_context export crashed with NameError because _export_pno_research_context referenced _get_prepared_levels_frame/_get_prepared_entry_frame without defining them. The same block also depended on uninitialized local collectors and counters that would fail after the helper issue was fixed.
```

Change:

```text
restore local prepared levels/entry frame cache helpers inside _export_pno_research_context
initialize position_context, exit-reference, trade-path, levels-path and stage5 outcome collectors before export loops
add the missing Logger import used by diagnostics helper signatures
```

Verification:

```text
python -m compileall cli/pno_diagnostics.py cli/commands.py
```

Risk:

```text
Low. Diagnostics/export-only change. No PNO thresholds, entry rules, exits, fills, data-fetch logic or trading decisions are changed.
```

Next:

```text
Rerun the same --pno-all-tf-pairs command and verify that diagnostics/research_context/*.csv and stage_reason_summary.csv are written after 508/508 symbols.
```

---

## P043 — Human BOS obsolete-level guard

```text
Status: PROPOSED
Type: bugfix / diagnostics
Trading logic changed: yes, for human_bos validity only
Files: strategy/pno/engine.py, cli/pno_diagnostics.py, research/*
Follow-up to: E007 / 1.zip multi-TF diagnostics
Supersedes: none
Commit: UNKNOWN
```

Problem:

```text
5m/30s admitted GUA/MAGMA through `human_bos` levels that were visually and structurally stale: selected BOS was a lower/local level under later or prior local highs. In code, `human_bos` returned from `_resolve_stage4_context` as already valid and `_rebuild_stage4_scores` returned immediately, so overhead/untested-high/stale-level hard blocks were bypassed. Stage5 also nulled non-reclaim decay reasons for `human_bos`.

Stage1 rejected chart export only rendered near-threshold rows. Reasons like `counterflow_ratio_5m_too_high` wrote events.csv but no charts when all rows were far from threshold.
```

Change:

```text
run `human_bos` through normal Stage4 scoring instead of returning early
add explicit `human_bos` obsolete-level guard for prior/later local highs above selected level
apply close_above pre-signal decay to `human_bos` instead of blanket nulling it
invalidate armed `human_bos` entries when level becomes obsolete before trigger
select fallback Stage1 review rows from closest far-threshold rejects when no near-threshold rows exist
```

Expected diagnostics:

```text
GUA/MAGMA-like lower stale `human_bos` trades should move to Stage4/Stage5 rejected with human_bos_* or level_already_* reasons.
Stage1 rejected folders such as counterflow_ratio_5m_too_high should get charts even when all rows are far from threshold.
```

Verification:

```text
python -m compileall strategy/pno cli constants.py main.py launcher.py
rerun same 31d --pno-all-tf-pairs diagnostics and compare GUA/MAGMA, Stage4 unique, Stage5 reasons and rejected charts manifest
```

Risk:

```text
Medium. This intentionally reduces `human_bos` permissiveness. It may remove some valid early BOS continuations; verify through same-window before/after funnel, not PnL only. Diagnostics fallback has low risk and does not affect trading decisions.
```

Next:

```text
Rerun same 31-day multi-TF diagnostics and inspect whether rejected GUA/MAGMA are correct and whether missing Stage1 rejected charts are now exported.
```

---

## 27. Шаблон нового патча

```markdown
## PXXX — Название

Status:
Type:
Trading logic changed:
Files:
Follow-up to:
Supersedes:

Problem:
Change:
Expected diagnostics:
Verification:
Risk:
Next:
```

---

## 28. Правило обновления

Каждый patch должен обновлять:

```text
PATCH_LOG.md
RESEARCH_STATE.md, если меняется статус/вывод
STRATEGY_SPEC.md, если меняется логика стратегии
EXPERIMENT_LOG.md, если связан с run/experiment
```
---

## P033 — PyCharm inspection cleanup

```text
Status: PROPOSED
Type: cleanup / typing
Trading logic changed: no
Files: cli/commands.py, vectorbt_runner/*, data/*, strategy/*, simulation/*, launcher.py, main.py, research/*
Commit: UNKNOWN
```

Problem:

```text
PyCharm inspections reported stale cleanup issues in the uploaded local code snapshot: unresolved BacktestSummary, unused locals/imports, unbound-local warnings, broad exception, invalid cast, name shadowing and pandas category typing. Previous generated patches were based on mismatched cli/commands.py context and did not apply cleanly.
```

Change:

```text
rebuild cleanup patch against uploaded code.zip
remove unused CLI locals/imports and unused helper parameters
cast diagnostics_payloads explicitly before iteration
fix BacktestRunner unbound-local warnings with explicit initialization
replace broad Parquet metadata exception with concrete exception types
fix pandas Categorical categories typing with pd.Index
fix Windows console ctypes access through getattr
remove dead category4 profile construction that was not returned
```

Verification:

```text
python -m compileall data/exchanges data/liquidity simulation strategy/pno strategy/base_strategy.py vectorbt_runner cli constants.py main.py launcher.py
```

Risk:

```text
No intended trading-logic change. Category4 construction was dead code because resolve_pno_category_profiles did not return it.
```

---

## P044 — Position terminology and strict flow data

```text
Status: PROPOSED
Commit: UNKNOWN
Trading logic changed: yes, data-quality gate only
```

Проблема:

```text
Симулированные bot outcomes назывались trades, что смешивало их с биржевыми trades внутри свечей.
PNO flow мог опираться на close*volume / volume_proxy как замену real quote_volume USDT / trade-count.
Diagnostics не показывали coverage по символам и считали Stage5 rows вместо unique setups.
```

Изменение:

```text
TradeResult/TradeSignal/TradeClassifier -> PositionResult/PositionSignal/PositionResultClassifier.
CSV/metrics/stage ids для bot outcomes переименованы в positions/stage_5_position/position_context.
Exchange trade fields number_of_trades/trades/trade_count сохранены как trade-data.
PNO требует real quote_volume в USDT и real trade-count; close*volume и volume_proxy не используются как fallback.
Добавлены diagnostics_coverage.csv, diagnostics_coverage_summary.csv и stage5_unique_setup_summary.csv.
Добавлены chart samples для ключевых Stage1 rejected reasons.
```

Проверка:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Риск:

```text
Backtest results станут менее permissive: символы без real USDT quote_volume или real trade-count будут явно отклоняться по missing_required_market_data.
Это ожидаемо; цель — не маскировать проблему proxy-данных.
```

---

## P046 — Artifact export quality

```text
Status: PROPOSED
Commit: UNKNOWN until applied
Base checked: 3ae80c93fa3e8ca2ec95566a89982f4001956db5
Trading logic changed: no
Files: cli/pno_diagnostics.py, cli/commands.py, research/RESEARCH_STATE.md, research/PATCH_LOG.md, research/EXPERIMENT_LOG.md
```

Problem:

```text
P045 removed the sparse-entry Stage1 false gate, but the new run still produced diagnostics artifacts that were hard to consume: stage_reviews/*/passed/events.csv could be headerless when empty, stage manifest did not match stage_review_summary expectations, and run_context omitted PNO category/entry/variant mode. Data-quality coverage required extra manual grouping to see quote-volume/source bottlenecks.
```

Change:

```text
Write stage-review events with a stable base schema even when rows are empty.
Add per-stage summary.csv and manifest fields used by stage review aggregation.
Write diagnostics_quality_sources.csv and diagnostics_quality_reasons.csv directly from diagnostics_coverage.csv.
Write diagnostics_coverage.csv / summaries with headers when empty.
Persist pno_category_mode, pno_entry_confirmation_mode and pno_variant_id in run_context.json.
Do not add separate artifact validators or post-processing layers.
```

Verification:

```bash
python -m compileall -q data/exchanges strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
No trading-decision path is changed. Artifact schemas gain columns; consumers expecting the old minimal manifest should continue to work because old count/path fields are preserved and new fields are additive.
```

---

## P047 — Real Binance kline quote-volume propagation

```text
Status: PROPOSED
Commit: UNKNOWN until applied
Base: P046 applied locally on top of 2.zip / P045 state
GitHub head checked: 0332f2c470372e986b62283d4df04b16a179a104
Trading logic changed: no
Files: constants.py, data/exchanges/ccxt_types.py, data/exchanges/ccxt_futures_client.py, data/fetchers/ohlcv_fetcher.py, strategy/pno/engine.py, research/RESEARCH_STATE.md, research/PATCH_LOG.md, research/EXPERIMENT_LOG.md
```

Problem:

```text
P045 fixed the sparse-entry gate, but the next blocker was levels_missing_quote_volume_usdt for most symbols. The common OHLCV fetch path kept only CCXT's 6 standard OHLCV columns, losing Binance futures kline fields that contain real quote asset volume, number of trades, taker buy volume and taker buy quote volume. Existing caches with close but no quote_volume looked up-to-date and were not backfilled.
```

Change:

```text
Fetch Binance USD-M klines through the typed exchange boundary and preserve raw quote_volume, number_of_trades, taker_buy_volume and taker_buy_quote_volume.
Keep non-Binance CCXT OHLCV as 6-column OHLCV instead of inventing quote_volume.
Aggregate optional OHLCV market-data columns by sum when deriving M10 or cached aggregate frames.
Use quote_volume as the OHLCV cache watermark so old candles without real quote_volume are re-fetched/backfilled.
Remove close*volume from one-minute Stage1 support; support now requires real quote_volume and real trade-count or returns no support.
```

Verification:

```bash
python -m compileall -q data/exchanges data/fetchers strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
The next OHLCV update may re-fetch a full requested range for old caches that have prices but lack quote_volume. That is intentional. If an exchange path cannot provide real quote_volume, PNO should keep reporting missing_quote_volume_usdt rather than using close*volume.
```

---

## P053 — Canonical diagnostics trade-count only

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / chart
Trading logic changed: no; diagnostics now follows the same canonical trade-count contract as PNO trading path
Files: cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Base: P048/P049/P050/P051/P052 user-applied locally; exact commit UNKNOWN
```

Проблема:

```text
Diagnostics/charts still treated legacy `trades`/`trade_count` as usable trade-count inputs and backfilled missing `number_of_trades`. Missing trade-count could render as zero activity, which is a false diagnostic signal.
```

Изменение:

```text
Plot/review diagnostics read only canonical `number_of_trades`. Legacy aliases remain visible only through ignored source labels. Missing canonical trade-count annotates the Trades panel and stage-review manifest instead of drawing zero bars.
```

Проверка:

```text
python -m compileall cli/pno_diagnostics.py research/PATCH_LOG.md research/RESEARCH_STATE.md
```

Ожидаемый rerun check:

```text
Stage-review charts and manifest should show `missing_canonical_number_of_trades` or `legacy_*_ignored` when canonical trade-count is absent; no chart should present missing trade-count as zero exchange activity.
```


---

## P054 — Per-symbol data-load artifacts

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / data-quality artifact
Trading logic changed: no
Files: cli/commands.py, research/*
Commit: UNKNOWN
Base: P048/P049/P050/P051/P052/P053 user-applied locally; exact commit UNKNOWN
```

Проблема:

```text
P052 introduced explicit SymbolDataLoadResult, but run output still exposed mostly aggregate `data_load_rejections`. Symbols dropped before `symbol_frames` could disappear from diagnostics coverage, making missing cache/schema/window issues hard to distinguish from honest no-signal runs.
```

Изменение:

```text
Write `data_load_status.csv` with one row per load attempt: role, symbol, timeframe, ok, status, reason, path, missing_columns, raw_rows, prepared_rows, window_days and end_timestamp_ms. Write `data_load_rejections.csv` as role/status counts. Link both files from run_context.json and still write them when preparation leaves zero usable symbol frames.
```

Проверка:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Ожидаемый rerun check:

```text
A run with missing timeframe/cache should produce `data_load_status.csv` rows for rejected symbols and `run_context.json` should point to both data-load artifacts; no rejected symbol should be visible only as an aggregate count.
```


---

## P055 — Sparse seconds load-status propagation

```text
Status: PROPOSED
Type: data-quality / diagnostics
Trading logic changed: no; sparse data failures now preserve exact load/materialization reasons
Files: strategy/pno/pno_strategy.py, strategy/pno/engine.py, research/*
Commit: UNKNOWN
Base: P048/P049/P050/P051/P052/P053/P054 user-applied locally; exact commit UNKNOWN
```

Проблема:

```text
P051 made sparse entry materialization explicit, but the provider still returned only a DataFrame. Runtime/persistent seconds-cache statuses, missing parquet, empty requested windows, invalid OHLCV rows and day-fetch empties collapsed into `sparse_entry_no_loaded_frames`.
```

Изменение:

```text
Add typed seconds-window and aggregated-window load results in the PNO seconds provider. Engine now uses `load_aggregated_window_result`, records per-window status rows, nested DataPreparer/day-fetch statuses, reason counts and a bounded status sample in diagnostics context and Stage2 rejection extra.
```

Проверка:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Ожидаемый rerun check:

```text
A sparse-entry run with missing/invalid seconds data should expose `seconds_materialization_load_reason_counts` and `seconds_materialization_load_status_sample`; `sparse_entry_no_loaded_frames` should remain only the top-level rejection, not the only observable cause.
```

---

## P056 - Explicit fallback failure diagnostics

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality / diagnostics / universe selection
Trading logic changed: yes, baseline level fallback and ideal_like decay bypass removed
Files: strategy/pno/pno_strategy.py, strategy/pno/engine.py, strategy/pno/config.py, cli/commands.py, research/*
Commit: UNKNOWN
Follow-up to: P055 and fallback audit from current workspace
```

Problem:

```text
seconds/aggTrades low-level paths still collapsed archive/live/schema/pagination/cleaning failures into empty frames.
Persisted sparse aggregated cache treated missing and invalid cache similarly, then silently rebuilt from seconds.
Stage1 cache read/write failures were recomputed without explicit diagnostics.
Diagnostics kept only bounded seconds materialization samples.
Universe selection could fall back to arbitrary first symbols when liquidity selection produced no liquid universe.
```

Change:

```text
Add typed archive/live aggTrades day results and typed aggregate results with explicit reasons: archive_404, archive_empty, archive_read_failed, live_no_rows, live_fetch_error, live_pagination_stalled, aggtrades_schema_missing, aggtrades_timestamp_missing and aggregate_empty_after_cleaning.
Make persisted sparse aggregated cache return explicit miss/read_failed/schema_invalid/empty statuses; invalid persisted cache now fails the window instead of silently rebuilding.
Record Stage1 cache hit/miss/read_failed/write_failed/schema_invalid status in diagnostics context.
Export full seconds_load_status.csv and stage1_cache_status.csv from PNO diagnostics instead of relying only on context samples.
Remove arbitrary first-symbol universe fallback; no liquid symbols now returns an empty universe with universe_selection_failed metadata.
Remove baseline trading fallback level construction when confirmed/shelf highs are absent.
Disable ideal_like decay invalidation bypass in category_3 baseline profile.
Reject unresolved entry timeframe in the main PNO multi-TF path instead of assuming configured TF.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
Low to medium. Trading acceptance thresholds are unchanged, but runs that previously masked invalid sparse cache, unresolved entry timeframe or failed liquidity universe can now stop earlier with explicit data-quality reasons.
```

Next:

```text
Run a small PNO diagnostics sample and verify data_load_status.csv, data_load_rejections.csv, diagnostics/seconds_load_status.csv and diagnostics/stage1_cache_status.csv.
```

---

## P057 - Strict current-code artifact/cache reads

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality / diagnostics / cache-read hardening
Trading logic changed: no; invalid configured PNO TF pair now fails explicitly instead of silently using default
Files: cli/pno_diagnostics.py, cli/commands.py, data/storage/parquet_storage.py, data/fetchers/ohlcv_fetcher.py, data/exchanges/ccxt_futures_client.py, research/*
Commit: UNKNOWN
Follow-up to: P056
```

Problem:

```text
Some current-code reads still treated missing/empty/broken artifacts as the same empty DataFrame.
Diagnostic chart timeframe inference still accepted a default when frame step could not be inferred.
Parquet cache load returned empty frame for missing cache and raised/failed elsewhere without a reusable status.
M10 cached-base OHLCV aggregation treated missing/schema/window/invalid base cache as a generic empty frame before falling through.
Binance aggTrades typed boundary returned [] for invalid non-list exchange payload.
PNO backtest timeframe config could silently fall back to the default pair if configured pair was invalid.
```

Change:

```text
Add CSV artifact read status for existing artifact readers and write artifact_load_status.csv where current commands read/sync category research context and stage manifests.
Make PNO diagnostic frame-step inference return None when unresolved; chart/context callers now skip or annotate diagnostic_frame_step_unresolved instead of using synthetic TF.
Add ParquetStorage.load_result with explicit missing/empty/read_failed/schema_invalid/ok status while keeping load() as compatibility wrapper.
Make OhlcvFetcher M10 cached-base path use explicit cache load status and distinguish invalid rows and empty requested window.
Raise an explicit error on invalid non-list Binance aggTrades payload instead of returning [].
Reject invalid configured PNO backtest TF pair with configured_timeframe_pair_invalid instead of defaulting to another pair.
```

Verification:

```bash
python -m compileall data/exchanges data/fetchers data/storage strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
Low to medium. No PNO thresholds or entry/exit decisions changed. Some commands that previously continued with guessed/default data now stop or produce explicit artifact/cache failure statuses.
```

Next:

```text
Run a small diagnostics/backtest path and verify artifact_load_status.csv plus existing data_load_status.csv/seconds_load_status.csv/stage1_cache_status.csv are present and distinguish missing vs empty vs read_failed.
```

---

## P058 - Remove remaining PNO fallback tails

```text
Status: APPLIED locally / UNKNOWN commit
Type: data-quality / diagnostics / baseline logic hardening
Trading logic changed: yes, ideal-like fallback level source disabled in baseline/category_3
Files: strategy/pno/engine.py, strategy/pno/pno_strategy.py, strategy/pno/config.py, cli/commands.py, research/*
Commit: UNKNOWN
Follow-up to: P056/P057 and fallback audit checklist
```

Problem:

```text
P056/P057 closed the main fallbacks, but a few tails remained:
Stage2 rejection extra still stored only load_status_sample[:10].
CLI diagnostics export still accepted old seconds_materialization_load_status_sample fallback.
Engine Stage1 metadata/support used `or entry_timeframe_ms` when source entry timeframe inference failed.
PnoStrategy wrapper treated unresolved source entry timeframe as "no sparse materialization", which could route the wrong enrichment path.
category_3 still enabled ideal_like_impulse and `_resolve_level_cluster` could call `_resolve_ideal_like_level_cluster` as a fallback level source.
```

Change:

```text
Store full materialization load_statuses in rejection extra and export only full seconds_materialization_load_statuses.
Reject unresolved entry timeframe explicitly in PnoStrategy wrapper and raise entry_timeframe_unresolved in engine internals that should be unreachable after the main gate.
Disable category_3 ideal_like_impulse_enabled in baseline.
Remove `_resolve_ideal_like_level_cluster` call from baseline level resolution.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py main.py launcher.py
```

Risk:

```text
Medium. Diagnostics become more complete, but category_3/ideal-like setups that depended on relaxed fallback level construction will disappear from baseline. That is intended; ideal-like behavior should be a separate explicit experiment.
```

Next:

```text
Run the same PNO diagnostics window and compare Stage4/Stage5 reasons: no confirmed/shelf level should now show as no setup/reject, not ideal_like fallback level.
```
---

## P059 - Truthful artifact tail cleanup

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness / cleanup
Trading logic changed: no
Files: cli/commands.py, vectorbt_runner/backtest_runner.py, strategy/pno/engine.py, constants.py, research/*
Commit: UNKNOWN
Follow-up to: P056/P057/P058
```

Problem:

```text
The main fallback audit was closed, but several artifact tails could still confuse post-run reading:
--plot-from-results could silently choose an existing configured/default results.csv when no explicit input was supplied.
--id could fall back to row number when no id/combination_id/rank match existed.
Profit factor used a numeric 99.0 cap when there were profits and no losses.
PNO engine still contained unused relaxed/fallback level helpers and a misleading Stage1 fallback builder name.
Stage-review/chart selection still used fallback terminology for diagnostic coverage rows and rescore logic.
```

Change:

```text
Require explicit --results-input for run-backtest --plot-from-results, except saved plot-backtest requests that already pass run_context-derived results_input.
Treat missing --id match as an error instead of selecting row N.
Write profit_factor=inf plus profit_factor_status=infinite_no_losses for no-loss profitable rows; no positions now get profit_factor_status=no_positions.
Remove dead relaxed/fallback level helper functions and rename the Stage1 builder away from fallback terminology.
Rename diagnostic coverage/rescore helper names away from fallback terminology without changing selected rows.
```

Verification:

```bash
python -m compileall data/exchanges data/fetchers data/storage strategy/pno cli vectorbt_runner constants.py main.py launcher.py
```

Risk:

```text
Low to medium. Existing ad-hoc plot-from-results commands now need --results-input. Results.csv schema gains profit_factor_status and no-loss PF is infinite rather than a capped placeholder.
```

Next:

```text
Run a small saved backtest/plot-backtest cycle and confirm results.csv, run_context.json, artifact_load_status.csv, seconds_load_status.csv, sparse_materialization_windows.csv and stage1_cache_status.csv are complete and refer to the same run root.
```
---

## P060 - Strict saved run context artifact path

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/commands.py, cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: P059 second-pass audit
```

Problem:

```text
Saved plot-backtest reconstructed results paths from run_context.json with default values if core fields were missing.
That could make a damaged/incomplete run_context point charts at a synthetic/default results path instead of failing on the real artifact issue.
Diagnostic timeframe inference also kept a dead default_ms parameter after P057, which made the code look like it could still substitute a fake frame step.
```

Change:

```text
Require strategy_id, strategy_results_rel_dir, position_plots_rel_dir, results_file_name, levels_tf and entry_tf in saved run_context before building plot args.
Fail with run_context_missing_required_fields or run_context_results_file_missing instead of reconstructing defaults.
Remove the unused default_ms argument from _infer_pno_frame_step_ms and its chart label call sites.
```

Verification:

```bash
python -m compileall data/exchanges data/fetchers data/storage strategy/pno cli vectorbt_runner constants.py main.py launcher.py
```

Risk:

```text
Low. Existing valid run_context.json files continue to work; incomplete old artifacts now fail explicitly instead of guessing paths.
```

Next:

```text
Use a recent saved run directory for plot-backtest and verify it either uses the exact run_context results file or reports the missing field/file explicitly.
```

---

## P061 - No silent None positions fallback

```text
Status: APPLIED locally / UNKNOWN commit
Type: bugfix / artifact truthfulness
Trading logic changed: no
Files: vectorbt_runner/backtest_runner.py, research/*
Commit: UNKNOWN
Follow-up to: P060 fallback cleanup
```

Problem:

```text
BacktestRunner converted `generate_events_multi_tf(...) is None` into an empty positions list.
That masked strategy contract violations as a valid zero-position run and could produce misleading PNO artifacts.
```

Change:

```text
Remove the silent `None -> []` fallback for per-symbol strategy generation.
A None return now raises `strategy_returned_none` with strategy, symbol, levels timeframe, entry timeframe and params signature.
The optional portfolio pipeline sentinel remains unchanged: BaseStrategy.generate_events_portfolio may still return None to indicate no portfolio-level implementation.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py vectorbt_runner
```

Risk:

```text
Low. Valid strategies already return list[PositionResult]; only broken strategy implementations now fail instead of generating false empty artifacts.
```

Next:

```text
Run a small PNO diagnostic backtest and confirm that zero-position runs still complete when the strategy returns [], while any accidental None return stops before results.csv/artifacts are trusted.
```

---

## P063 - Typed market-id boundary for archive aggTrades

```text
Status: APPLIED locally / UNKNOWN commit
Type: refactor / boundary hardening
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/*
Commit: UNKNOWN
Follow-up to: P060 fallback cleanup
```

Problem:

```text
_PnoSecondsFrameProvider._resolve_market_id reached through CcxtFuturesClient._client and called the underlying ccxt market_id directly.
That bypassed the typed exchange-client boundary and made the archive aggTrades path different from the live aggTrades path.
```

Change:

```text
Keep the existing strategy helper but make it initialize CcxtFuturesClient when needed and call CcxtFuturesClient.get_market_id(symbol).
No new validation layer, no trading-logic change, no new public API.
The private client access is removed from strategy/pno/pno_strategy.py.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
rg -n "_client\._client" strategy/pno data/exchanges
```

Risk:

```text
Low. get_market_id already wraps the same ccxt market_id call after ensuring markets are loaded; behavior should match, but through the supported boundary.
```

Next:

```text
Run a tiny PNO sparse-entry diagnostic using an archive-backed day and confirm seconds load statuses still show archive_loaded/archive_404/live_* accurately.
```

---

## P062 - Remove status-dropping PNO load wrappers

```text
Status: APPLIED locally / UNKNOWN commit
Type: cleanup / fallback removal
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/*
Commit: UNKNOWN
Follow-up to: P060 fallback cleanup, P063 typed market-id boundary
```

Problem:

```text
_PnoSecondsFrameProvider still had dead compatibility wrappers that called the strict result APIs and returned only `.frame`.
Those wrappers discarded ok/status/reason/load_statuses if they were ever called again, recreating the same class of silent failure masking that the strict result APIs were added to prevent.
```

Change:

```text
Remove the unused DataFrame-only wrappers:
- load_aggregated_window
- _ensure_seconds_window
- _fetch_seconds_for_day
- _fetch_seconds_from_archive
- _fetch_seconds_from_live_trades
- _aggregate_agg_trades_to_seconds

Keep the existing `*_result` execution path unchanged.
No new verification layer, no trading-logic change, no public CLI/config change.
```

Verification:

```bash
rg -n "load_aggregated_window\(|_ensure_seconds_window\(|_fetch_seconds_for_day\(|_fetch_seconds_from_archive\(|_fetch_seconds_from_live_trades\(|_aggregate_agg_trades_to_seconds\(" . --glob '!*.pyc'
git diff --check
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
```

Risk:

```text
Low. Current repository search shows no callers for the removed wrappers. Risk is limited to any external/private script importing these non-public helpers directly.
```

Next:

```text
Run a small PNO sparse diagnostic and confirm seconds/aggregated load failures still surface through load_statuses and rejection summaries.
```

---

## P064 - Strict result-row reconstruction

```text
Status: APPLIED locally / UNKNOWN commit
Type: artifact truthfulness / fallback removal
Trading logic changed: no
Files: cli/commands.py, research/*
Commit: UNKNOWN
Follow-up to: P061/P063/P062 fallback cleanup chain
```

Problem:

```text
PNO artifact rebuild paths reconstructed PnoParams from results.csv with a fresh PnoParams() defaults object.
Missing or blank result-row fields were silently replaced by current code defaults, so plot-from-results/stage artifact rebuild could produce plausible but non-reproducible PNO artifacts.
```

Change:

```text
Replace default-backed reconstruction with strict results-row parsing.
Required PNO result columns are centralized; missing/blank fields fail with results_row_missing_required_pno_fields.
Malformed scalar fields fail with results_row_invalid_pno_field.
A row/runtime TF mismatch fails with results_row_timeframe_mismatch instead of rebuilding artifacts against the wrong TF pair.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
```

Risk:

```text
Medium for old results.csv artifacts: older/incomplete rows that depended on default reconstruction now fail explicitly and must be regenerated or supplied with full PNO params.
Live/backtest trade decisions are unchanged.
```

Next:

```text
Run plot-from-results on one fresh PNO results.csv and on a deliberately stripped copy missing pno_min_score; the fresh row should rebuild, the stripped row should fail with results_row_missing_required_pno_fields.
```

---

## P065 - Exception payloads for data failures

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostic truthfulness / failure payload clarity
Trading logic changed: no
Files: strategy/pno/engine.py, strategy/pno/pno_strategy.py, research/*
Commit: UNKNOWN
Follow-up to: P061/P063/P062/P064 fallback cleanup chain
```

Problem:

```text
Several existing failed-status paths preserved only a broad reason such as stage1_cache_read_failed, archive_fetch_error, archive_read_failed, live_fetch_error or persisted_aggregated_window_read_failed.
That made artifacts less truthful because different root causes collapsed into the same generic label.
```

Change:

```text
Keep the same failure branches and reasons, but attach exception_type and a bounded exception_message where an exception is already caught.
Covered paths: Stage1 cache read/write, persisted sparse aggregated cache read, Binance archive fetch/read and live aggTrades fetch.
No new verification layer, no fallback, no trading-logic change.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
rg -n "exception_type|exception_message" strategy/pno/engine.py strategy/pno/pno_strategy.py
```

Risk:

```text
Low. CSV/status artifacts get two new optional diagnostic columns in affected status rows. Existing ok/fail decisions and rejection reasons are unchanged.
```

Next:

```text
Run a small PNO sparse diagnostic with one intentionally unreadable/corrupt Stage1 or sparse aggregated cache file and confirm status artifacts include exception_type/exception_message instead of only *_read_failed.
```

---

## P066 - Stage3-5 candle context artifacts

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / research artifacts
Trading logic changed: no
Files: cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: request for compact candle-level artifacts for external ChatGPT analysis
```

Problem:

```text
Existing Stage3-5 artifacts had event rows, summaries and some path context, but rejected/near-miss setups did not get a compact entry-TF candle window with future outcome labels.
That made questions like "which setups reached TP1 within one hour" or "what candle patterns separate wins from losses" harder to answer from the artifact ZIP alone.
```

Change:

```text
Export research_context/stage3_5_candle_outcomes.csv with one row per selected Stage3-5 setup and future 1h TP1 / pullback-low-break labels, including same-bar ambiguity when both happen in one candle.
Export research_context/stage3_5_candle_context.csv with bounded entry-TF OHLCV/trade-count/quote-volume candles around each selected Stage3-5 anchor plus up to one hour after the anchor.
Each setup window is capped to 1000 candles and deduplicated by context key to avoid large duplicate artifacts.
Pre-anchor context is capped to 60 entry-TF candles; Stage3 candle export is sampled/near-miss only, while Stage4/Stage5 remain full.
No PNO decision logic, thresholds, entry, TP or SL handling changed.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Artifact size increases with selected Stage4/Stage5 rows, especially 5s entry TF, but Stage3 is sampled and each setup is bounded and CSV-only. The TP1/pullback-low first-event labels are diagnostic look-forward metadata and must not be used inside trading decisions.
```

Next:

```text
Run a small PNO diagnostics sample and verify research_context/stage3_5_candle_outcomes.csv and stage3_5_candle_context.csv exist, have headers, and contain tp1_hit_within_1h, pullback_low_broken_within_1h, first_future_event_1h and candle rows for selected Stage3-5 setups.
```

---

## P067 - Research context sparse entry load truthfulness

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/commands.py, cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: audit for remaining fallback masking in PNO run artifacts
```

Problem:

```text
Position charts still looked for removed `load_aggregated_window` instead of the status-carrying `load_aggregated_window_result`.
Research context and Stage3-5 candle context could therefore use the source entry frame when sparse target entry TF needed materialization, making candle artifacts look complete while not necessarily being target-TF candles.
```

Change:

```text
Use `load_aggregated_window_result` for sparse position-chart windows.
Pass the PNO seconds provider into research-context export.
When research context needs sparse target entry candles, load the bounded aggregated window through the status-carrying result API.
Export research_context/research_context_entry_load_status.csv so target-entry candle load failures are explicit instead of silent source-frame fallback.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Trading decisions are unchanged. Some research_context candle artifacts can now expose failed sparse target-entry materialization instead of silently relying on a coarser source frame.
```

Next:

```text
Run a small sparse-entry PNO diagnostics sample and verify research_context/research_context_entry_load_status.csv is present; if any row has ok=false, do not treat stage3_5 candle rows for that symbol as target-entry candles.
```

---

## P068 - Stage5 review synthesis status artifact

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/commands.py, research/*
Commit: UNKNOWN
Follow-up to: audit for remaining fallback masking in PNO run artifacts
```

Problem:

```text
Stage5 review synthesis from latest Stage4 rows had several silent continue branches.
If a Stage4 setup had no existing Stage5 row and synthesis could not be built because of missing/empty entry candles, missing level metadata, invalid level/high relation, missing timestamps or no wick cross, the setup disappeared from the Stage5 review surface without an explicit artifact row.
```

Change:

```text
Export research_context/stage5_review_synthesis_status.csv with one status row per latest Stage4 cycle considered by Stage5 review synthesis.
Rows now show synthesized, skipped because Stage5 already exists, or not_synthesized with a concrete reason.
Synthetic Stage5 rejection generation itself is unchanged; only its skip/synthesis status is persisted.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Trading decisions and review rejection content are unchanged except for adding a status CSV that exposes previously silent synthesis gaps.
```

Next:

```text
Run a small diagnostics sample and check research_context/stage5_review_synthesis_status.csv; any not_synthesized rows must be treated as Stage5 review coverage gaps, not as real clean Stage5 rejects.
```

---

## P069 - Stage3-5 candle context coverage status

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: audit for remaining fallback masking in PNO run artifacts
```

Problem:

```text
Stage3-5 candle context export could skip a selected setup without a row in stage3_5_candle_outcomes.csv when symbol, anchor timestamp or entry candle coverage was missing.
That made candle artifacts less comprehensive because absence of a row could mean either no selected setup or a failed candle-context export.
```

Change:

```text
Export research_context/stage3_5_candle_context_status.csv with one status row for each selected Stage3-5 candle context candidate.
Rows now report exported, skipped duplicate_context_key or not_exported with concrete reasons such as symbol_missing, entry_frame_empty_or_anchor_missing_or_timestamp_missing, entry_timestamp_array_empty, anchor_after_entry_frame_end or context_frame_empty.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Trading decisions and candle outcome calculations are unchanged; the new CSV only exposes coverage of the candle artifact itself.
```

Next:

```text
Run a small diagnostics sample and inspect stage3_5_candle_context_status.csv; any not_exported row means candle-pattern analysis is incomplete for that selected setup.
```

---

## P070 - Category research-context filter status

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/commands.py, research/*
Commit: UNKNOWN
Follow-up to: audit for remaining fallback masking in PNO run artifacts
```

Problem:

```text
Category diagnostics copied research_context CSVs only when they had pno_category_id.
Files without pno_category_id, including status/coverage artifacts, were silently omitted from category research_context while artifact_load_status only reported that the source CSV was read.
Shared stage-review manifest sync also dropped trade_count_source_counts, weakening data-quality traceability.
```

Change:

```text
Category research_context export now writes research_context_filter_status.csv with per-file copied/filtered/not-copied status, source row count, target row count and whether pno_category_id existed.
CSV files without pno_category_id are copied unfiltered and explicitly marked copied_unfiltered/pno_category_id_missing instead of disappearing.
Shared stage-review manifest sync preserves trade_count_source_counts.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Trading decisions are unchanged. Category archives can include additional global status CSVs that are not category-filtered, but the new filter status labels that explicitly.
```

Next:

```text
Run a small pno_category_mode=all diagnostics sample and inspect categories/*/pno_diagnostics/research_context/research_context_filter_status.csv before treating category archives as complete.
```

---

## P071 - Candle context category metadata

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: category artifact completeness audit
```

Problem:

```text
Stage3-5 candle context/outcome/status rows were built manually and did not carry pno_category_id.
Category research_context export therefore could not filter candle artifacts by category and would have to treat them as unfiltered global CSVs.
```

Change:

```text
Add pno_category_id, pno_category_label and pno_profile_variant_id to stage3_5_candle_context.csv, stage3_5_candle_outcomes.csv and stage3_5_candle_context_status.csv rows.
This allows category archives to filter candle evidence by category instead of silently mixing all categories.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Trading decisions and candle calculations are unchanged; CSV schemas gain category metadata columns.
```

Next:

```text
Run a small pno_category_mode=all diagnostics sample and verify category stage3_5 candle CSVs are filtered_by_category in research_context_filter_status.csv.
```

---

## P072 - Chart artifact status coverage

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/commands.py, cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: audit for remaining fallback masking in PNO run artifacts
```

Problem:

```text
Stage-review chart rendering and category chart copying could skip charts when prepared frames were missing, the renderer returned None, chart_paths were absent/mismatched, or source chart files were missing.
Those cases were only indirectly visible through chart counts or empty chart_path fields.
```

Change:

```text
Stage-review export now writes stage_reviews/chart_status.csv with per-chart rendered/not_rendered status and concrete reason.
Category diagnostics now write chart_copy_status.csv with copied/not_copied status for each category position chart candidate.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Trading decisions, stage events and rejection rows are unchanged. Only chart artifact coverage is made explicit.
```

Next:

```text
Run diagnostics with chart rendering enabled and inspect stage_reviews/chart_status.csv and category chart_copy_status.csv before relying on image coverage in the archive.
```

---

## P073 - Stage-review event completeness flags

```text
Status: APPLIED locally / UNKNOWN commit
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: stage-review sampling audit
```

Problem:

```text
Stage-review rejected events can be exported as either complete rows or a review sample.
The summary exposed count and exported_events_count, but did not include an explicit machine-readable completeness flag.
```

Change:

```text
Stage-review summary.csv now includes events_are_complete and event_export_mode.
Rejected reasons with sampled event rows are marked event_export_mode=review_sample; full exports are marked complete.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py
```

Risk:

```text
Low. Trading decisions and exported event selection are unchanged. The summary schema gains explicit completeness fields.
```

Next:

```text
Inspect stage_reviews/*/summary.csv and require events_are_complete=true before treating a reason events.csv as the full rejected-row set.
```

---

## P074 - Artifact contract regression tests

```text
Status: APPLIED locally / UNKNOWN commit
Type: tests / artifact contract
Trading logic changed: no
Files: tests/test_pno.py, research/*
Commit: UNKNOWN
Follow-up to: concern that status artifacts alone do not fix root causes
```

Problem:

```text
The artifact fixes needed executable regression coverage, not just additional status CSVs.
tests/test_pno.py also had stale TradeResultType/TradeResult imports that prevented collection in the current tree.
```

Change:

```text
Update tests/test_pno.py imports to the current PositionResultType/PositionResult modules.
Add focused tests that verify category research_context filters category-aware CSVs, stage-review summaries mark sampled rejected events explicitly, and Stage3-5 candle context/outcome/status rows carry category metadata.
```

Verification:

```bash
python -m pytest tests/test_pno.py -k "category_research_context_filters_category_aware_csvs or stage_review_summary_marks_sampled_rejected_events or stage_candle_context_carries_category_metadata"
python -m compileall strategy/pno cli constants.py tests/test_pno.py
```

Risk:

```text
Low for runtime. Test import compatibility changed only the test suite. Full tests/test_pno.py still has many existing failures against the current code/config and should not be treated as green.
```

Next:

```text
Stabilize the broader PNO test suite separately; do not infer production stability from compileall plus three focused artifact tests.
```

---

## P075 - Split historical PNO tests from runnable suite

```text
Status: APPLIED locally / UNKNOWN commit
Type: tests / suite stabilization
Trading logic changed: no
Files: pyproject.toml, tests/test_pno.py, tests/test_pno_artifacts.py, tests/test_backtest_runner.py, tests/test_risk_manager.py, research/*
Commit: UNKNOWN
Follow-up to: request to stabilize PNO test suite or split stale historical tests from current runnable tests
```

Problem:

```text
tests/test_pno.py mixes current checks with stale historical regression expectations and is not green in the current code/config.
Several non-PNO test files also used stale trade_* imports and prevented default pytest collection.
```

Change:

```text
Mark tests/test_pno.py as pno_historical and exclude that marker from the default pytest run.
Move current artifact-contract tests into tests/test_pno_artifacts.py so they run by default.
Register the pno_historical marker in pyproject.toml.
Update stale test imports to current PositionResultType/PositionResult/PositionSignal names and current RiskConfig.risk_per_position_pct field.
```

Verification:

```bash
python -m pytest -q
python -m pytest -m pno_historical tests/test_pno.py -q
python -m compileall strategy/pno cli constants.py tests
```

Result:

```text
Default runnable suite: 8 passed, 159 deselected.
Historical PNO suite remains red when explicitly requested: 80 failed, 79 passed.
```

Risk:

```text
Medium. Default pytest is now green and honest, but it excludes historical PNO regressions. Those failures still need a separate stabilization pass before using historical examples as current acceptance criteria.
```

Next:

```text
Triage pno_historical failures by class: stale parameter/schema expectations, stale sparse seconds provider internals, and real strategy golden-case regressions.
```

---

## P077 - Avoid empty seconds concat FutureWarning

```text
Status: PROPOSED
Type: runtime hygiene / data-load path
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/*
Commit: UNKNOWN
Follow-up to: runtime FutureWarning at pno_strategy.py:799 during sparse seconds fetch
```

Problem:

```text
Sparse seconds loading can start from an empty schema-only seconds_frame, then append real fetched seconds candles.
pd.concat([empty_schema_frame, fetched]) triggers pandas FutureWarning about concat with empty/all-NA entries.
The warning is noisy and future pandas versions may infer dtypes differently.
```

Change:

```text
When the existing seconds_frame is empty, assign fetched.copy() directly.
Only call pd.concat when both existing seconds_frame and fetched contain rows.
No fallback, suppress-warning filter or post-processing is added.
```

Verification:

```bash
python -m compileall strategy/pno cli constants.py main.py launcher.py vectorbt_runner
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected true --pno-entry-confirmation-mode close_above --days 7 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
Low. The previous concat with an empty frame and fetched rows is equivalent to using fetched rows directly for the requested window.
This should change only warning noise, not candidate selection or position generation.
```

Next:

```text
Inspect data-load/status artifacts first; do not treat warning removal as evidence of edge or artifact completeness.
```


---

## P078 - Preserve quote_volume in PNO research artifacts

```text
Status: PROPOSED
Type: diagnostics / artifact truthfulness
Trading logic changed: no
Files: cli/pno_diagnostics.py, research/*
Commit: UNKNOWN
Follow-up to: 5m/30s 7-day diagnostics run from 5m_30s.zip showed quote_volume_usdt coverage in diagnostics, but all-NaN quote_volume in research_context candle artifacts.
```

Problem:

```text
PNO diagnostics coverage reported levels/entry quote_volume_source=quote_volume_usdt for all 527 analyzed symbols.
However, plot/research frame preparation dropped quote_volume before building research_context, so stage3_5_candle_context.csv exported quote_volume as all NaN and _describe_pno_frame_shape emitted All-NaN RuntimeWarning for quote_volume_median.
This made artifacts less truthful than the loaded data.
```

Change:

```text
Carry quote_volume through prepared levels/entry plot frames.
Export quote_volume, canonical number_of_trades and taker-buy fields in stage5_levels_path_context.
Compute diagnostic medians with an explicit finite-value check so missing data remains NaN without RuntimeWarning noise.
No proxy or fallback value is introduced.
```

Verification:

```bash
python -m compileall cli/pno_diagnostics.py strategy/pno constants.py main.py launcher.py vectorbt_runner
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected true --pno-entry-confirmation-mode close_above --days 7 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
Low. Artifact columns become more complete; trading decisions and rejection logic are unchanged.
If source data genuinely lacks quote_volume, artifacts will still show NaN instead of fabricating a proxy.
```

Next:

```text
Rerun the same 5m/30s diagnostics and verify stage3_5_candle_context.csv quote_volume is populated when diagnostics_coverage reports quote_volume_usdt.
```

---

## P076 - Strict PNO generation and human_bos parity

```text
Status: APPLIED locally / UNKNOWN commit
Type: bugfix / strategy honesty
Trading logic changed: yes, only for human_bos close_above trigger parity
Files: strategy/pno/pno_strategy.py, strategy/pno/engine.py, research/*
Commit: UNKNOWN
Follow-up to: code review risk that None can be hidden as zero positions and human_bos still has a special Stage5 trigger bypass
```

Problem:

```text
PnoStrategy.generate_events_multi_tf returned positions or [], which could hide an internal None as an honest zero-position result.
human_bos Stage4 candidates were constructed as already valid with a high provisional score, and Stage5 skipped close-trigger score adjustment/filtering for human_bos.
```

Change:

```text
Raise RuntimeError if category/profile generation returns None instead of converting it to [].
Make human_bos Stage4 context provisional until _rebuild_stage4_scores computes the real score/validity.
Run human_bos close_above signals through the same close-trigger score adjustment and filter path as normal reclaim levels.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Risk:

```text
Medium. None handling is safer and should only expose bugs. human_bos parity can reduce positions because weak/noisy close_above signals are no longer bypassed. Verify with same-window funnel/reject distribution, not PnL only.
```

Next:

```text
Run the same 31-day multi-TF diagnostics and compare human_bos Stage4/Stage5 rejection reasons before/after P076.
```


---

## P079 - Sparse entry audit trail and sizing

```text
Status: PROPOSED
Type: data-quality / diagnostics
Trading logic changed: no
Files: strategy/pno/engine.py, cli/commands.py, research/*
Commit: UNKNOWN
Follow-up to: 7-day multi-TF artifacts with target seconds-entry materialization deferred behind source 1m data-load status
```

Problem:

```text
For seconds-entry PNO runs, data_load_status.csv records the source entry frame (usually 1m or reused levels), while the requested 5s/15s/30s target-entry frame is materialized later after Stage1.
That made artifacts easy to misread as if target entry data had been fully checked during data preparation.
Sparse materialization also reported load ok before the downstream required-bars gate, so `sparse_entry_materialized_insufficient_bars` was visible only as a stage rejection rather than as a run-level materialization status.
Some target-entry windows were only a few candles short because sparse pre-roll was fixed at 30 minutes instead of matching the target-entry required bar span.
```

Change:

```text
Add pno_diagnostics/sparse_entry_materialization_status.csv with one status row per sparse-entry symbol attempt.
Record source/target/requested entry timeframe and load_mode in data_load_status.csv and run_context.json.
Rename the deferred source-frame status to `levels_ok_target_entry_deferred`.
Mark target-entry usability separately from low-level materialization ok, including rows, required_bars, windows and load_reason_counts.
Expand sparse Stage1 fetch pre-roll to at least `required_bars * target_entry_timeframe_ms` so target-entry materialization is not cut short by a hardcoded 30-minute pre-roll.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected true --pno-entry-confirmation-mode close_above --days 7 --pno-category-mode discovery --collect-diagnostics true
```

Risk:

```text
Low for trading decisions: entry/score/TP/SL logic is unchanged.
Medium for runtime/cache pressure: sparse windows can be wider for candidate symbols, but only after Stage1 and only to satisfy the already-required target-entry bar count.
```

Next:

```text
Inspect sparse_entry_materialization_status.csv first. If target_entry_usable=false, the row must show whether the cause is insufficient bars, missing loaded frames, cache/schema/window/fetch failure or missing provider.
```

---

## P081 - Remove prior-local-high human BOS reject

```text
Status: PROPOSED / compile verified locally
Type: trading logic / experiment
Trading logic changed: yes
Files: strategy/pno/engine.py, research/*
Commit: UNKNOWN
Follow-up to: 20260506_111822_pno 7-day multi-TF run; user-requested removal of `human_bos_below_prior_local_high`
```

Problem:

```text
The previous human_bos freshness guard rejected a selected BOS level when a confirmed local high before the selected BOS was above it.
This can be too strict for PNO continuation: an earlier lower-high inside the pullback does not necessarily obsolete a later reclaim/BOS attempt.
The run still had zero positions, so this change is an experiment to expose whether those prior-high rejects were blocking executable close_above setups, not evidence of edge.
```

Change:

```text
Remove only the `human_bos_below_prior_local_high` rejection.
Confirmed local highs before the selected BOS are skipped instead of rejected.
Keep the later-local-high obsolete guard: a newer confirmed high after the selected BOS can still reject as `human_bos_obsolete_under_later_local_high`.
No data collection, sparse materialization, close_above trigger, TP/SL or RR logic is changed.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Risk:

```text
Medium. This intentionally weakens human_bos structural freshness and may admit weaker rebound setups under older pullback highs.
The next run must compare reject distribution and near-miss charts, not only positions/PnL.
```

Next:

```text
Rerun the same 7-day multi-TF diagnostics and inspect whether prior `human_bos_below_prior_local_high` cases become valid Stage5 attempts, later-local-high rejects, no_close_above rejects, entry-invalidated rejects or real positions.
```



---

## P080 - Propagate sparse materialization diagnostics

```text
Status: PROPOSED / compile verified locally
Type: data-quality / diagnostics
Trading logic changed: no
Files: strategy/pno/pno_strategy.py, research/*
Commit: UNKNOWN
Follow-up to: 2.zip 7-day multi-TF run after P079 artifacts existed but sparse materialization tables were empty
```

Problem:

```text
P079 added sparse_entry_materialization_status.csv and seconds_load_status.csv, but the 7-day run still exported both tables empty.
The sparse materialization details were present inside Stage2 rejection payloads, for example `sparse_entry_materialized_insufficient_bars`, but `_merge_generation_diagnostics` only passed through a small whitelist of context keys.
As a result, diagnostics coverage could say `levels_ok_target_entry_deferred`, while the target-entry materialization audit trail was missing from run-level artifacts.
```

Change:

```text
Propagate sparse-entry context keys through category diagnostics merge: requested/target/source entry TF, target_entry_checked/rows/required/usable, materialization status/reason/windows/bars and seconds load statuses.
Merge seconds_materialization_load_statuses and load_reason_counts instead of dropping them at the category boundary.
No trading decision, filter, score, TP/SL or entry logic is changed.
```

Verification:

```bash
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner simulation domain
```

Risk:

```text
Low. Artifacts become more truthful; trading output should not change.
For multi-profile category runs, status rows may now include multiple profile load-status entries instead of only the first visible scalar context.
```

Next:

```text
Rerun the same 7-day multi-TF command and check that sparse_entry_materialization_status.csv has rows for AVGO/KAVA-like insufficient-bars cases and seconds_load_status.csv contains the underlying cache/window rows.
```

---

## P082 - Add anomaly continuation lab research tool

```text
Status: APPLIED locally / tests passed
Type: research artifact tooling
Trading logic changed: no
Files: research_tools/anomaly_continuation_lab.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Problem:

```text
The early anomaly-continuation hypothesis should be studied separately from executable PNO pullback logic.
Manual IO/ZEC checks showed strong start tape and future continuation in raw candles, but current PNO correctly rejects them as non-pullback setups.
We need a truthful artifact that captures only metrics known after a fixed confirmation window and labels future outcomes separately.
```

Change:

```text
Add a standalone research tool: python -m research_tools.anomaly_continuation_lab.
It scans 1m cached OHLCV for wake-up anomaly starts where quote_volume and number_of_trades are >= 10x previous rolling baseline.
It writes anomaly_continuation_lab.csv and anomaly_continuation_summary.csv.
It includes next-N-candle persistence, trade/quote decay, price retention, midpoint loss, future MFE/MAE labels, and start verticality metrics.
Verticality is represented by a bounded score plus raw components: path efficiency, range efficiency, slope pct per candle, max retrace fraction, and green-candle share.
No PNO filters, entries, exits, scoring, TP/SL or stage logic are changed.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m compileall research_tools tests\test_anomaly_continuation_lab.py
.venv\Scripts\python.exe -m research_tools.anomaly_continuation_lab --cache-dir .output/cache --output-dir .output/manual_checks/anomaly_continuation_lab_io_zec --days 31 --confirmation-candles 4 --symbols IO/USDT:USDT ZEC/USDT:USDT
```

Risk:

```text
Low for production because this is research-only tooling.
Medium research risk: the artifact can still be overfit if rules are chosen after reading future outcome labels. Entry rules must use only columns available at decision_timestamp_ms.
```

Next:

```text
Run the lab artifact across all symbols and compare big_25p, fast_fade and other groups by persistence, verticality, price retention, symbol/month concentration and top-runner dependence before defining any long/short strategy rules.
```

---

## P083 - Add focused runner-date review for anomaly lab

```text
Status: APPLIED locally / tests passed
Type: research artifact tooling
Trading logic changed: no
Files: research_tools/anomaly_runner_review.py, research_tools/anomaly_continuation_lab.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Problem:

```text
Known recent runner dates need a compact review showing where the lab would have had an entry/decision moment.
The generic lab artifact can contain hundreds of anomaly rows, and ranking candidates by future return must be explicitly marked as post-facto review, not a trading rule.
```

Change:

```text
Add research_tools.anomaly_runner_review to filter anomaly_continuation_lab.csv by SYMBOL=YYYY-MM-DD targets using local date offset.
It writes recent_runner_target_review.csv with anomaly timestamp, decision timestamp, decision price, tape persistence, verticality, price retention and future outcomes.
Rows are ranked by future return only for retrospective review and are labelled ranked_by_future_for_review.
Also expose CLI parameters in anomaly_continuation_lab for forward window and anomaly thresholds, enabling strict and relaxed sensitivity runs.
No PNO execution logic is changed.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m compileall research_tools tests\test_anomaly_continuation_lab.py
```

Risk:

```text
Low production risk.
Medium research risk if the ranked-by-future review is mistaken for an entry rule. It is only a way to locate which anomaly row corresponded to a known runner date.
```

Next:

```text
Compare strict 10x/60-candle and relaxed 5x/240-candle runner-date reviews, then design entry rules using only decision-time columns.
```

---

## P084 - Add anomaly lab profitability command

```text
Status: APPLIED locally / tests passed
Type: research backtest tooling
Trading logic changed: no PNO logic changed
Files: research_tools/anomaly_strategy_backtest.py, cli/parser.py, cli/commands.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Problem:

```text
The anomaly-continuation lab had candidate/outcome artifacts but no runnable profitability artifact with explicit entry, stop, partial take and trailing logic.
Without a command-level run, it is too easy to inspect future labels without checking whether a realizable long entry has acceptable risk/reward after fees.
```

Change:

```text
Add CLI command: python main.py run-anomaly-lab.
Default entry: long at decision close after fixed confirmation candles when price_retention >= 0.70 and start_verticality_score >= 0.25.
Initial stop: below the anomaly+confirmation box low with a small range buffer.
TP1: take 50% at +1R, then move stop to breakeven.
Trail: after TP1, ratchet stop under the rolling swing-low of the last 5 closed candles minus 0.10R.
Artifacts: anomaly_candidates.csv, anomaly_signals.csv, anomaly_trades.csv, anomaly_profitability_summary.csv, anomaly_profitability_by_symbol.csv, run_config.csv.
The command is research-only and does not affect PNO engine/stages.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m compileall research_tools cli tests\test_anomaly_continuation_lab.py
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_strategy_recent_runners --days 31 --confirmation-candles 4 --forward-high-candles 240 --forward-low-candles 60 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols LAB/USDT:USDT TON/USDT:USDT ZEC/USDT:USDT PLAY/USDT:USDT IO/USDT:USDT JTO/USDT:USDT NEAR/USDT:USDT DASH/USDT:USDT
```

Risk:

```text
Medium research risk. The first default rule is intentionally simple and not proven to have edge.
OHLCV intrabar ambiguity is handled conservatively by checking stop before TP1 within a candle.
The command should be used to compare rule variants and robustness, not as evidence of deployable edge by itself.
```

Next:

```text
Run across all cached symbols and analyze profitability by hold_count, verticality, price_retention, initial_risk_pct, symbol, date and top-trade contribution.
```

---

## P085 - Add 5m open-interest context to anomaly lab artifacts

```text
Status: APPLIED locally / tests passed
Type: research artifact/data quality
Trading logic changed: no PNO logic changed
Files: research_tools/anomaly_continuation_lab.py, research_tools/anomaly_strategy_backtest.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Problem:

```text
Open interest can be material for anomaly continuation research, but in this project it is available only on 5m data.
The 1m anomaly lab needed OI context without pretending that 1m OI exists or silently filling missing OI with zero.
```

Change:

```text
Attach OI as a 5m as-of feature: last 5m open_interest row with timestamp <= decision_timestamp_ms.
Add oi_timeframe, oi_status, oi_timestamp, oi_age_ms, oi_open_interest and 1/3/6 x 5m OI deltas to candidates, signals and trades.
Write oi_context_status.csv for lab and profitability command outputs.
Missing OI remains NaN and is labelled missing_frame, missing_column, empty_oi, no_oi_before_decision, stale_asof or read_error.
No OI feature is used by the default signal filter yet.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m compileall research_tools cli tests\test_anomaly_continuation_lab.py
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_strategy_recent_runners_oi_check --days 31 --confirmation-candles 4 --forward-high-candles 240 --forward-low-candles 60 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols LAB/USDT:USDT TON/USDT:USDT ZEC/USDT:USDT PLAY/USDT:USDT IO/USDT:USDT JTO/USDT:USDT NEAR/USDT:USDT DASH/USDT:USDT
```

Risk:

```text
Low production risk; this is research-only and does not affect PNO execution.
Current selected cache lacks open_interest columns in 5m data, so OI analysis is unavailable until the 5m cache is fetched/enriched with OI.
```

---

## P086 - Add run-anomaly-lab progress and ETA

```text
Status: APPLIED locally / tests passed
Type: research tooling UX
Trading logic changed: no
Files: research_tools/anomaly_continuation_lab.py, research_tools/anomaly_strategy_backtest.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
run-anomaly-lab now prints sparse progress with percent and ETA while collecting anomaly candidates and simulating trades.
This does not change candidates, signals, exits, OI logic or artifact schema.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest tests/test_anomaly_continuation_lab.py -q
.venv\Scripts\python.exe -m compileall research_tools cli tests\test_anomaly_continuation_lab.py
```

---

## P087 - Fix encoded unicode symbol cache paths in anomaly lab

```text
Status: APPLIED locally / tests passed
Type: bugfix
Trading logic changed: no
Files: research_tools/anomaly_continuation_lab.py, research_tools/anomaly_strategy_backtest.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Use URL quote(symbol, safe="") when reconstructing cache paths, so unicode symbols like 龙虾/USDT:USDT resolve to their percent-encoded cache directories.
```

---

## P088 - Add anomaly OI entry grid

```text
Status: APPLIED locally / tests passed
Type: research tooling
Trading logic changed: no PNO logic changed
Files: research_tools/anomaly_strategy_backtest.py, cli/parser.py, cli/commands.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Add decision-time OI filters and structural entry methods to run-anomaly-lab.
Supported entry methods: market, break_box_high, pullback_box_fraction.
Add --run-entry-grid to write anomaly_entry_grid_summary.csv for a predeclared grid of OI threshold, hold count and pullback fraction.
The default command behavior remains market entry without OI filter unless args are supplied.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall research_tools cli tests\test_anomaly_continuation_lab.py
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_entry_grid_smoke --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols IO/USDT:USDT ZEC/USDT:USDT --run-entry-grid true --grid-oi3-values 0.01,0.03 --grid-hold-values 1,2 --grid-pullback-fractions 0.75
```

---

## P089 - Add anomaly flow/effort/sleep metrics

```text
Status: APPLIED locally / tests passed
Type: research feature engineering
Trading logic changed: no PNO logic changed
Files: research_tools/anomaly_continuation_lab.py, research_tools/anomaly_strategy_backtest.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Add decision-time research features from exchange candle data:
taker buy quote share and persistence, avg trade quote size and decay, start close/range/wick metrics, effort-vs-result ratios, start range expansion vs baseline, post-start pullback fraction, OI x price interaction, and OI change per decision return.
The fields are written to anomaly_candidates.csv and propagated to anomaly_trades.csv.
Missing taker-buy fields are labelled flow_taker_buy_status=missing_columns and stay NaN.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall research_tools cli tests\test_anomaly_continuation_lab.py
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_metrics_smoke --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols IO/USDT:USDT ZEC/USDT:USDT --run-entry-grid true --grid-oi3-values 0.01 --grid-hold-values 1 --grid-pullback-fractions 0.75
```

---

## P090 - Remove anomaly risk proxy and reuse grid frame cache

```text
Status: APPLIED locally / tests passed
Type: honesty/performance
Trading logic changed: no PNO logic changed
Files: research_tools/anomaly_continuation_lab.py, research_tools/anomaly_strategy_backtest.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Add decision_box_low/high/range to candidates and use them for signal risk filtering instead of the prior start-candle risk proxy.
Rename signal risk fields to initial_stop_at_decision and initial_risk_pct_at_decision.
Reuse a shared symbol frame cache across entry-grid variants, avoiding repeated parquet reads for each grid row.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall research_tools cli tests\test_anomaly_continuation_lab.py
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_honesty_speed_smoke --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols IO/USDT:USDT ZEC/USDT:USDT --run-entry-grid true --grid-oi3-values 0.01,0.03 --grid-hold-values 1,2 --grid-pullback-fractions 0.75
```

---

## P091 - Clamp OI fetch window to exchange history limit

```text
Status: APPLIED locally / compile verified
Type: data fetch reliability
Trading logic changed: no
Files: data/fetchers/oi_fetcher.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Clamp OI start_timestamp_ms to the exchange-supported 30-day history window with one timeframe buffer and align OI request start/end to the timeframe boundary.
This avoids Binance startTime invalid errors near the rolling 30-day boundary.
Missing older OI remains missing in artifacts; no synthetic OI is created.
```

Verification:

```bash
.venv\Scripts\python.exe -m compileall data/fetchers/oi_fetcher.py
```

---

## P092 - Add anomaly anti-exhaustion grid profiles

```text
Status: APPLIED locally / tests passed
Type: research feature grid
Trading logic changed: no PNO logic changed
Files: research_tools/anomaly_strategy_backtest.py, cli/parser.py, cli/commands.py, tests/test_anomaly_continuation_lab.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Add optional anti-exhaustion filters to anomaly signal selection and entry-grid summary:
max_price_retention, max_start_quote_ratio, max_start_trade_ratio, max_start_avg_trade_quote_size_ratio, max_start_quote_ratio_per_abs_return, max_start_range_pct_ratio_to_baseline and min_next_taker_buy_quote_share.
Add grid exhaustion profiles none/mild/balanced/strict. Defaults remain unchanged.
The intended first 30-day run should use none,mild,balanced to avoid crushing trade count below the 15-30/month target.
```

Verification:

```bash
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall research_tools cli tests\test_anomaly_continuation_lab.py
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_exhaustion_grid_smoke --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols IO/USDT:USDT ZEC/USDT:USDT --run-entry-grid true --grid-oi3-values 0.03 --grid-hold-values 2 --grid-pullback-fractions 0.75 --grid-exhaustion-profiles none,mild,balanced
```

---

## P093 - Add honest derivatives context columns to anomaly lab

```text
Status: APPLIED locally / smoke verified
Type: research data feature
Trading logic changed: no
Files: research_tools/anomaly_continuation_lab.py, research_tools/anomaly_strategy_backtest.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Anomaly artifacts now include optional derivatives context from explicit cache files only:
funding rate, premium index mark/index prices, mark-price candles, global long-short account ratio, top account long-short ratio, top position long-short ratio and taker long-short ratio.
Each source has per-row status, timestamp and age fields plus point-in-time change metrics.
Missing context is reported as missing_frame/missing_column/no_context_before_decision/stale_asof; no zeros, ticker substitutes, last-price proxies or synthetic fills are used.
run-anomaly-lab writes market_context_status.csv beside oi_context_status.csv.
No signal filter uses these fields yet.
```

Verification:

```bash
.venv\Scripts\python.exe -m compileall research_tools\anomaly_continuation_lab.py research_tools\anomaly_strategy_backtest.py
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output\manual_checks\anomaly_derivatives_context_smoke2 --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols IO/USDT:USDT ZEC/USDT:USDT
```

---

## P094 - Fetch derivatives context during cache update

```text
Status: APPLIED locally / smoke verified
Type: data fetch
Trading logic changed: no
Files: data/fetchers/derivatives_context_fetcher.py, data/exchanges/ccxt_futures_client.py, cli/commands.py, cli/parser.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
fetch-data and update-cache now collect derivatives context by default after OHLCV/OI:
funding_rate, premium_index/5m, mark_price/5m, global_long_short_account_ratio/5m, top_long_short_account_ratio/5m, top_long_short_position_ratio/5m and taker_long_short_ratio/5m.
The optional --skip-derivatives-context flag disables this stage.
Fetched data is written to explicit cache paths consumed by anomaly-lab; no proxy values are created.
```

Verification:

```bash
.venv\Scripts\python.exe -m compileall data\fetchers\derivatives_context_fetcher.py data\exchanges\ccxt_futures_client.py cli\commands.py cli\parser.py research_tools\anomaly_continuation_lab.py research_tools\anomaly_strategy_backtest.py
.venv\Scripts\python.exe main.py update-cache --symbols ZEC/USDT:USDT --days 1 --timeframes 1m --skip-open-interest
.venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output\manual_checks\anomaly_derivatives_context_zec_after_fetch --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols ZEC/USDT:USDT
```

---

## P095 - Clamp derivatives context fetch window

```text
Status: APPLIED locally / smoke verified
Type: data fetch reliability
Trading logic changed: no
Files: data/fetchers/derivatives_context_fetcher.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
Derivatives context fetch now clamps each source start_timestamp_ms to Binance's rolling 30-day lookback with one source interval buffer and aligns start/end to source interval boundaries.
This prevents Binance data endpoints from rejecting startTime near the rolling boundary.
Older derivatives context remains unavailable rather than synthesized.
```

Verification:

```bash
.venv\Scripts\python.exe -m compileall data\fetchers\derivatives_context_fetcher.py
.venv\Scripts\python.exe main.py update-cache --symbols BTC/USDT:USDT --days 30 --timeframes 5m --skip-open-interest
```

---

## P096 - Make derivatives context event-window lazy

```text
Status: APPLIED locally / compile verified
Type: data fetch performance / honest artifact coverage
Trading logic changed: no
Files: cli/parser.py, cli/commands.py, data/fetchers/derivatives_context_fetcher.py, research_tools/anomaly_continuation_lab.py, research_tools/anomaly_strategy_backtest.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
fetch-data/update-cache no longer collect full-universe derivatives context by default.
Full 30-day derivatives context across 500+ symbols is too expensive because 5m long-short endpoints require many paginated requests per symbol.
The stage now runs only with explicit --with-derivatives-context and prints per-symbol progress.
Normal cache collection remains OHLCV/OI focused.
run-anomaly-lab now builds anomaly signals first, then fetches derivatives context only for the event window around a small signal set.
If the signal set is too large, the run prints an explicit skip message and uses cache-only context; market_context_status.csv still reports missing coverage honestly.
```

Verification:

```bash
.venv\Scripts\python.exe -m compileall cli\parser.py cli\commands.py data\fetchers\derivatives_context_fetcher.py research_tools\anomaly_strategy_backtest.py research_tools\anomaly_continuation_lab.py
```

---

## P097 - Avoid redundant derivatives context refetch

```text
Status: APPLIED locally / compile verified
Type: data fetch performance / cache honesty
Trading logic changed: no
Files: data/fetchers/derivatives_context_fetcher.py, research/*
Commit: UNKNOWN
Branch: codex/pno-anomaly-continuation-lab
```

Change:

```text
DerivativesContextFetcher now checks existing per-source cache timestamp bounds before requesting Binance.
It fetches only missing prefix/suffix ranges instead of re-requesting the whole lazy event window on every anomaly-lab run.
No proxy rows are created; missing internal holes remain visible through market_context_status rather than being filled silently.
```

Verification:

```bash
.venv\Scripts\python.exe -m compileall data\fetchers\derivatives_context_fetcher.py cli\commands.py research_tools\anomaly_strategy_backtest.py research_tools\anomaly_continuation_lab.py
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe main.py update-cache --symbols BTC/USDT:USDT --days 1 --timeframes 5m
.venv\Scripts\python.exe main.py update-cache --symbols BTC/USDT:USDT --days 1 --timeframes 5m --skip-open-interest --with-derivatives-context
```
