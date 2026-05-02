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
| P013 | Narrative runtime logs | PROPOSED | `constants.py`, `utils/retry.py`, `data/*`, `vectorbt_runner/backtest_runner.py`, `cli/commands.py`, `research/*` | logging/docs | Превратить runtime-логи в связный консольный рассказ: меньше шума, больше этапов, прогресса, причин и финального смысла. | `python -m compileall domain/enums data/exchanges data/fetchers strategy/pno vectorbt_runner cli constants.py main.py launcher.py` |

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
Commit: UNKNOWN
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

## 16. Шаблон нового патча

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

## 17. Правило обновления

Каждый patch должен обновлять:

```text
PATCH_LOG.md
RESEARCH_STATE.md, если меняется статус/вывод
STRATEGY_SPEC.md, если меняется логика стратегии
EXPERIMENT_LOG.md, если связан с run/experiment
```