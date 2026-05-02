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
| P018 | Stale reclaim and trade-count chart fix | APPLIED | `cli/pno_diagnostics.py`, `strategy/pno/pno_strategy.py`, `research/*` | bugfix/diagnostics | Канонизировать trade-count column для графиков и отбрасывать сделки после failed reclaim уровня до финального сигнала. | `python -m compileall cli/pno_diagnostics.py strategy/pno/pno_strategy.py` |
| P019 | Clear PNO trade-data caches | APPLIED | `strategy/pno/pno_strategy.py` | memory | Сбрасывать aggTrades-derived runtime caches после обработки символа. | `python -m compileall strategy/pno/pno_strategy.py` |
| P020 | Trade/chart consistency fix | APPLIED | `cli/pno_diagnostics.py`, `strategy/pno/pno_strategy.py`, `research/*` | bugfix/diagnostics | Заполнить все trade-count aliases и считать уровень stale, если close_above случился до финального сигнала. | `python -m compileall cli/pno_diagnostics.py strategy/pno/pno_strategy.py` |
| P021 | True trade-count chart propagation | APPLIED | `strategy/pno/pno_strategy.py`, `research/*` | bugfix/diagnostics | Прокидывать реальные number_of_trades/trades/trade_count из enriched PNO frames обратно в исходные frames, которые использует chart export. | `python -m compileall strategy/pno/pno_strategy.py` |
| P022 | Stage5/results consistency fix | APPLIED | `strategy/pno/pno_strategy.py`, `vectorbt_runner/backtest_runner.py`, `research/*` | bugfix/diagnostics | Переносить stale-level Stage5 passed events в rejected и писать runtime TF в results.csv. | `python -m compileall strategy/pno/pno_strategy.py vectorbt_runner/backtest_runner.py` |
| P023 | PNO none-trades guard | APPLIED | `strategy/pno/pno_strategy.py`, `vectorbt_runner/backtest_runner.py`, `research/*` | bugfix | Вернуть list-return contract для PNO generate_events_multi_tf и не валить runner, если стратегия вернула None. | `python -m compileall strategy/pno/pno_strategy.py vectorbt_runner/backtest_runner.py` |
| P024 | Concise backtest logs | APPLIED | `vectorbt_runner/backtest_runner.py`, `research/*` | logging | Убрать лишнюю прозу из runtime backtest logs, оставить TF, progress 0..100% и итоговую сводку по сделкам. | `python -m compileall vectorbt_runner/backtest_runner.py` |
| P025 | Fix concise progress checkpoint init | PROPOSED | `vectorbt_runner/backtest_runner.py`, `research/*` | bugfix | Инициализировать progress checkpoint cursor перед per-symbol progress loop. | `python -m compileall vectorbt_runner/backtest_runner.py` |

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
нули могут означать отсутствие поля, а не отсутствие сделок
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
логика стратегии и расчёта сделок не меняется; меняются только progress/error logs и re-raise MemoryError с русским сообщением
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
P016 устранил syntax error, но не восстановил потерянный portfolio_trades assignment и аргументы logger.info.
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
После P015/P016 BacktestRunner.run обращался к portfolio_trades до присваивания.
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
после генерации PNO trades отбрасывать сделки, где между level_valid_timestamp_ms и entry_signal_timestamp_ms была свеча high > level и close < level
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
логика сделок не меняется; исходные DataFrame получают дополнительные diagnostic columns, чтобы chart export видел тот же trade-count, что и strategy path
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
переносить соответствующие stage_5_trade events из passed в rejected с reason=level_stale_before_signal
уменьшать stage_hits/trades_generated после переноса
собирать results row из params после runtime levels_timeframe/entry_timeframe injection
```

Expected diagnostics:

```text
stage_5_trade/passed/events.csv, results.csv, trade_context.csv и charts показывают один и тот же набор валидных trades
MAGMA-like stale reclaim попадает в stage_5_trade/rejected/events.csv
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
BacktestRunner получает defensive guard: если стратегия вернула None, логирует предупреждение и продолжает с пустым списком сделок
```

Expected result:

```text
Символы без сделок больше не валят прогон.
Контракт strategy.generate_events_multi_tf снова list[TradeResult].
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
выводить итог TF-пары: Сделок нет или Сделок/Винрейт/PF/PnL/DD/SL/TP1_BE/TP2
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
Сделок: 17
Винрейт: 0.8400
```

Verification:

```text
python -m compileall vectorbt_runner/backtest_runner.py
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected false --pno-entry-confirmation-mode close_above --days 14 --pno-category-mode discovery --collect-diagnostics true
```

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