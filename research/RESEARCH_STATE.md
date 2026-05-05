# PNO Research State

Короткая рабочая память проекта. Держать компактной. Подробные правила работы — в Project Instructions.

---

## 1. Текущее состояние

```text
Branch: codex/ideal-like
Commit: e961252 (GitHub head checked); ZIP-local patch stack remains source-of-truth for uncommitted code
Local diff: P061 + P063 applied locally after P060; commit UNKNOWN
Last applied patch: P063 (local workspace; commit UNKNOWN)
Last analyzed run: E008 multi-TF 31-day run from 1.zip after P045
Updated: 2026-05-05
```

Если неизвестно — писать `UNKNOWN`, не выдумывать.

---

## 2. Что такое PNO

```text
pump → active high → healthy pullback → BOS / level reclaim → close_above → executable entry → TP1 active high → runner
```

PNO — это не покупка любого отката после пампа.  
PNO покупает только подтверждение, что откат после реального пампа закончился.

Главная формула:

```text
Не покупать падение. Покупать момент, когда падение после пампа ломается вверх.
```

---

## 3. Критерии годности стратегии

Цель — устойчивый edge, а не красивый одиночный run.

Минимальные ориентиры:

```text
50+ позиций в год
winrate > 0.40
средняя позиция > +1.0%
помесячно преимущественно положительно
нет зависимости от 1–5 топ-позиций
нет lookahead/leakage/явной переоптимизации
```

100%+ годовых желательно, но не ценой хрупкости.

Приоритет:

```text
достоверность вывода > устойчивость edge > скорость проверки > красота метрик
```

---

## 4. Текущие выводы

1. PNO — momentum-continuation после пампа, не контртрендовая ловля дна.
2. `close_above` нельзя ослаблять до wick-touch без отдельного эксперимента.
3. `5m/30s` может быть слишком медленным для быстрых continuation cases.
4. Stage4 rows могут дублироваться; считать нужно unique setups.
5. Без настоящего `number_of_trades` выводы о flow/tape/organic pump ограничены.
6. Ноль позиций — не оценка прибыльности, а материал для funnel/reject анализа.
7. Сначала диагностика и качество данных, потом изменение фильтров.
8. H4 подтверждена: `human_bos` bypass scoring/decay может принимать устаревший нижний BOS-level.
9. Для PNO flow требуется real `quote_volume` в USDT; `close*volume` не допускается как замена.
10. Симулированные результаты бота называются positions; trade/trades остаётся только для биржевых сделок внутри свечей.
11. `1.zip` показал не edge-result, а data-quality bottleneck: sparse entry TF (`5m/15s`, `5m/30s`) резались до materialization из-за раннего entry-quality gate.
12. Trade-data enrichment не должен возвращать исходный OHLCV как success; ошибка aggTrades/window/schema должна становиться явным data-quality rejection.
13. PNO trading path должен доверять только canonical `number_of_trades`; legacy `trades`/`trade_count` нельзя использовать как замену real trade-count.
14. Ticker `baseVolume * last` нельзя подставлять в `quote_volume`; proxy можно хранить только как diagnostics metadata и не использовать для liquidity selection/PNO flow.
15. Sparse entry materialization не должна возвращать пустой OHLCV-frame как универсальный fallback; loader/window/load/empty failures должны быть отдельными diagnostics reasons.
16. DataPreparer не должен возвращать одинаковый empty frame для missing symbol/file/schema/window/invalid rows; PNO preparation должна видеть точный load status.
17. Diagnostics/charts должны читать только canonical `number_of_trades`; legacy `trades`/`trade_count` можно показывать только как ignored source label, а missing trade-count нельзя рисовать как нулевую активность.
18. Aggregate `data_load_rejections` недостаточен: run output должен сохранять per-symbol/role/timeframe status для всех load attempts, включая символы, исключённые до `symbol_frames`.
19. Archive aggTrades market-id resolution no longer reaches through `CcxtFuturesClient._client`; PNO strategy uses the typed `get_market_id()` boundary for both archive and live aggTrades paths.

---

## 5. Патчи

| ID | Название | Статус | Суть |
|---|---|---|---|
| P001 | Stage4 dedup | UNKNOWN | Убрать дубли Stage4 review rows. |
| P002 | Stage5 reject reasons | UNKNOWN | Логировать точные причины no-trade. |
| P003 | Trades % charts | UNKNOWN | Добавить trade-count панель на графики. |
| P004 | True trade-count data | PROPOSED / UNKNOWN | Использовать real `number_of_trades` / `quote_volume`, не volume proxy. |
| P005 | Repo cleanup | APPLIED | Удалены локальные IDE/log/empty artifacts; `.env` не трогался. |
| P006 | PNO entry/data fetch hardening | PROPOSED | Зафиксировать `close_above`, исправить aggTrades candle window и live pagination. |
| P009 | Add 5m/15s PNO TF set | APPLIED | Добавить `15s` enum и включить `5m/15s` в multi-TF backtest set. |
| P010 | Rewrite README for PNO research workflow | APPLIED | Привести README к фактическому PNO workflow: data, 3 TF-set, diagnostics, research memory, data quality. |
| P011 | Humanize runtime logs in Russian | APPLIED | Привести runtime-логи к короткому русскому языку без перегруза служебными деталями. |
| P012 | Fix P011 logging follow-up | APPLIED | Исправить mismatch аргументов logger.info и синхронизировать research bookkeeping. |
| P013 | Narrative runtime logs | APPLIED | Превратить консольные логи из перевода в связную историю прогона: этапы, прогресс, причины и итог. |
| P014 | Polish console logs | APPLIED | Добить оставшийся английский и сухие key=value строки; добавить переносы строк в многочастные сообщения. |
| P015 | Backtest progress and memory logs | APPLIED | Убрать дубли прогресса для одиночной сетки и сделать понятный memory-error. |
| P016 | Fix unclosed logger call | APPLIED | Закрыть незавершённый logger.info после P015. |
| P017 | Fix P015 runner regression | APPLIED | Вернуть portfolio_positions assignment и восстановить аргументы long-symbol logger. |
| P018 | Stale reclaim and trade-count chart fix | APPLIED | Починить пустой trade-count panel и отбрасывать входы в уже провалившийся reclaim уровня. |
| P019 | Clear PNO trade-data caches | APPLIED | Сбрасывать тяжёлые aggTrades runtime-caches после символа, чтобы full-universe run не упирался в RAM. |
| P020 | Trade/chart consistency fix | APPLIED | Синхронизировать trade-count aliases и отбрасывать уровни с close_above до финального сигнала. |
| P021 | True trade-count chart propagation | APPLIED | Прокидывать реальные aggTrades trade-count columns из enriched frames в исходные frames для chart export. |
| P022 | Stage5/results consistency fix | APPLIED | Синхронизировать Stage5 review с stale-level фильтром и писать runtime TF в results row. |
| P023 | PNO none-trades guard | APPLIED | Гарантировать list-return contract для PNO trades и защитить runner от None. |
| P024 | Concise backtest logs | APPLIED | Заменить повествовательные runtime-логи на короткий progress-bar стиль и итог по TF-паре. |
| P025 | Fix concise progress checkpoint init | APPLIED | Исправить UnboundLocalError в progress loop после P024. |
| P026 | Strict backtest runtime log shape | APPLIED | Строгий формат логов: заголовок, TF, progress, итог; без лишней runtime-диагностики. |
| P027 | Quiet runtime logger sweep | APPLIED | Убрать служебный runtime-шум из logger.*, оставив человековажные заголовки, progress, итог, предупреждения и ошибки. |
| P028 | Normalize diagnostics and chart logs | APPLIED | Убрать summary/grid/runner-final шум и нормализовать diagnostics/charts export logs. |
| P029 | Whitelist runtime log format | APPLIED | Runtime INFO/WARNING проходит только для заголовка, TF, progress, итога, diagnostics/charts и ошибок. |
| P030 | Replace runtime log filters with source logs | APPLIED | Удалить глобальный фильтр логов и исправить runtime-шум в конкретных call-sites. |
| P031 | Finish source-level runtime logs | APPLIED | Добить оставшиеся source-level runtime логи без фильтров/whitelist/post-processing. |
| P032 | Normalize source-level artifact logs | PROPOSED | Public runtime status формируется в call-sites; подробные artifact/progress детали переведены в debug. |
| P034 | Diagnostics initial progress | PROPOSED | Печатать стартовый diagnostics progress 0/N и не пропускать checkpoints на пустых символах. |
| P035 | PNO lazy entry enrichment CPU fix | APPLIED | Не грузить entry aggTrades для символов без Stage1-кандидата; восстановить return stale-фильтра. |
| P036 | PNO skip full source-entry enrichment | APPLIED | Не обогащать весь source entry-frame для seconds-entry TF; оставить sparse materialization после Stage1. |
| P037 | PNO skip redundant sparse Stage1 precheck | APPLIED | Не делать wrapper-level Stage1 pre-scan в sparse-entry режиме; engine всё равно делает обязательную Stage1-проверку. |
| P038 | Reuse PNO backtest diagnostics export cache | PROPOSED | Не прогонять PNO strategy повторно при экспорте diagnostics/stage reviews/charts после `--collect-diagnostics true`. |
| P039 | PNO trade-count chart bars | PROPOSED | Нижний `Trades %` на trade charts рисует exchange trade-count per candle, нормированный в проценты, а не пустой subplot. |
| P040 | PNO diagnostics logging/summary fix | APPLIED | Исправить logger.debug placeholder mismatch, warning для короткого окна и full rejected-reason summary. |
| P041 | PNO seconds-entry/stale-level/BE/log cleanup | PROPOSED | Включить `15s` в общий sparse aggTrades path, резать устаревший level до позиции, снизить BE до 60%, убрать лишние runtime logs. |
| P042 | PNO research-context export helper fix | PROPOSED | Восстановить локальные prepared-frame helper’ы и накопители в `_export_pno_research_context`, чтобы diagnostics export не падал после полного прогона. |
| P043 | Human BOS obsolete-level guard | PROPOSED | Убрать bypass scoring/decay для `human_bos` и экспортировать fallback charts для Stage1 rejected reasons без near-threshold rows. |
| P044 | Position terminology and strict flow data | UNKNOWN in ZIP | Развести exchange trades и bot positions; требовать real quote_volume USDT/trade-count; добавить diagnostics coverage и Stage5 unique setup summary. |
| P045 | Sparse entry data-quality gate | PROPOSED | Не валидировать target entry trade data до sparse aggTrades materialization; писать пустые research CSV с колонками. |
| P046 | Artifact export quality | APPLIED locally / UNKNOWN commit | Стабильные schemas для stage-review CSV, stage summary/manifest, data-quality source/reason tables и полный PNO run context. |
| P047 | Real Binance kline quote-volume propagation | PROPOSED | Сохранять real Binance futures kline quote_volume/number_of_trades/taker_buy_* в OHLCV cache; backfill cache по quote_volume; убрать close*volume proxy из one-minute Stage1 support. |
| P048 | Explicit trade-data enrichment failure | APPLIED locally / UNKNOWN commit | Не маскировать провал aggTrades enrichment возвратом raw frame; писать `trade_data_enrichment_failed` diagnostics. |
| P049 | Canonical PNO trade-count only | APPLIED locally / UNKNOWN commit | Доверять в PNO trading path только `number_of_trades`; `trades`/`trade_count` маркировать как legacy ignored, а не использовать. |
| P050 | No ticker quote-volume proxy | APPLIED locally / UNKNOWN commit | Не заменять missing ticker `quoteVolume` на `baseVolume * last`; proxy сохранять только как ignored diagnostics metadata. |
| P051 | Explicit sparse-entry materialization failure | APPLIED locally / UNKNOWN commit | Не маскировать missing seconds loader/windows/frames пустым OHLCV-frame; писать точные `sparse_entry_*` reasons. |
| P052 | Explicit DataPreparer load status | APPLIED locally / UNKNOWN commit | Не сводить missing symbol/file/schema/window/invalid OHLCV к одинаковому empty frame; сохранять точные data_load_rejections. |
| P053 | Canonical diagnostics trade-count only | APPLIED locally / UNKNOWN commit | Diagnostics/charts читают только `number_of_trades`; legacy aliases маркируются как ignored, missing trade-count не рисуется как нули. |
| P054 | Per-symbol data-load artifacts | APPLIED locally / UNKNOWN commit | Сохранять `data_load_status.csv` и `data_load_rejections.csv` по каждому symbol/role/timeframe, а не только aggregate counts. |
| P055 | Sparse seconds load-status propagation | PROPOSED | Пробрасывать причины cache/window/fetch/materialization из seconds provider в engine diagnostics, а не сводить их к `sparse_entry_no_loaded_frames`. |

Статусы:

```text
PROPOSED / APPLIED / VERIFIED / UNKNOWN / REVERTED / SUPERSEDED
```

---

## 6. Открытые гипотезы

| ID | Гипотеза | Основание | Следующий тест |
|---|---|---|---|
| H1 | `30s` entry TF может опаздывать. | ETH case дошёл до active high до executable entry. | Сравнить `5m/30s`, `5m/15s`, `1m/5s`. |
| H2 | Stage1 слишком узкий или рынок дал мало чистых пампов. | Мало Stage1 passes в последнем run. | Длиннее окно + rejection distribution. |
| H3 | Flow-фильтры нельзя честно оценить без real trade-count. | Был volume proxy. | P045 + повтор того же run с diagnostics coverage. |
| H4 | `human_bos` может обходить часть Stage4 scoring. | Подтверждено на `engine.py`: `human_bos` bypassed Stage4 scoring и Stage5 decay. | P043 + rerun same 31d multi-TF diagnostics. |
| H5 | Wick-touch ухудшит качество входов. | BZ/RUNE были wick-only без close_above. | Только отдельный `touch + retest hold` experiment. |

---

## 7. Последний известный run

```text
Run: 1.zip multi-TF
Levels/Entry TF: 1m/5s, 5m/15s, 5m/30s
Period: 31 days
Symbols: ~508
Mode: discovery / close_above
Positions: 0 / 0 / 0
5m/30s PnL: not applicable
PF: not applicable
Issue: old research note claimed 0/2/4 trades, but current 1.zip artifacts show zero simulated positions in every TF set; diagnostics coverage and Stage5 unique setup summary are required to avoid repeating this confusion.
```

Funnel:

```text
5m/30s Stage1 pump:            44
5m/30s Stage2 high_pullback:   34
5m/30s Stage3 valid_pullback:  11
5m/30s Stage4 rows / unique:   35 / 15
5m/30s Stage5 unique setups:    1
5m/30s Stage5 positions:        0
```

Интерпретация:

```text
run полезен для funnel/reject analysis, но не для оценки прибыльности.
GUA/MAGMA: входы в неактуальный `human_bos` level.
`counterflow_ratio_5m_too_high` charts отсутствовали из-за near-threshold sampling.
```

---

## 8. Обязательные data-quality поля

Проверять в каждом run:

```text
trade_count_proxy_used
levels_trade_count_source
entry_trade_count_source
levels_quote_volume_source
entry_quote_volume_source
data_load_rejections
number_of_trades (canonical); trades/trade_count only as legacy ignored diagnostics
quote_volume
quote_volume_source
quote_volume_proxy (diagnostics only; ignored for selection/trading)
taker_buy_volume
taker_buy_quote_volume
```

Если proxy используется:

```text
Выводы о tape/flow/organic pump ограничены.
```

---

## 9. Один следующий самый ценный тест

Текущий приоритет:

```text
P055 → apply → compileall → micro-check sparse seconds path: missing/invalid seconds cache должен дать `seconds_materialization_load_reason_counts` и `seconds_materialization_load_status_sample`, а не только top-level `sparse_entry_no_loaded_frames`.
```

После этого:

```text
сравнить 5m/30s vs 5m/15s vs 1m/5s через --pno-all-tf-pairs.
```

---

## 10. Current local patch note - P056

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
The next blocker is no longer only DataPreparer. seconds/aggTrades, persisted sparse cache, Stage1 cache, diagnostics export and universe selection can also mask data failures as empty/no setup.
P056 makes those failures explicit without changing PNO entry/TP/SL thresholds, and removes baseline fallback level / ideal_like decay bypass behavior.
Invalid sparse persisted cache is now a data failure, not a silent rebuild.
If liquidity selection yields no liquid symbols, the run must stop with universe_selection_failed instead of using arbitrary first symbols.
```

One next test:

```text
Run a small PNO diagnostics sample and inspect seconds_load_status.csv plus stage1_cache_status.csv before reading funnel profitability.
```

---

## 11. Current local patch note - P057

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
Do not add a separate validation/diagnostics layer. Fix the current read paths so normal PNO artifacts are truthful.
P057 makes CSV artifact reads, Parquet cache reads, M10 cached-base OHLCV reads, Binance aggTrades payload shape and PNO configured TF pair failures explicit.
Missing/empty/read_failed/schema_invalid must be visible in artifacts or command status, not converted to an undifferentiated empty DataFrame/default timeframe.
```

One next test:

```text
Run a small PNO diagnostics/backtest path and inspect artifact_load_status.csv, data_load_status.csv, seconds_load_status.csv and stage1_cache_status.csv before reading funnel/PnL.
```

---

## 12. Current local patch note - P058

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
The original fallback checklist is now closed in current code, except compatibility wrappers that return frames for old call sites.
Full seconds materialization statuses are exported; no sample is used for current diagnostics export.
Unresolved entry timeframe is an explicit rejection/failure, not a target-TF assumption.
Baseline PNO no longer uses ideal-like/fallback level construction in category_3.
```

One next test:

```text
Run the same 31-day multi-TF diagnostics and verify no confirmed/shelf level cases do not enter via fallback/ideal-like level_source.
```
---

## 13. Current local patch note - P059

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
The remaining artifact-truthfulness tails are now explicit rather than silent.
plot-from-results must use an explicit results_input/run_context path, --id must match an actual id/combination_id/rank column, and no-loss profit factor is represented as infinite with profit_factor_status instead of a capped numeric placeholder.
Unused fallback/ideal-like level helpers were removed from the baseline engine code to keep the execution path clean, and chart/stage-review coverage helpers no longer use fallback terminology.
```

One next test:

```text
Run a small saved PNO backtest with diagnostics, then plot it through plot-backtest and verify all artifact CSVs are present under the same run root before interpreting funnel or PnL.
```
---

## 14. Current local patch note - P060

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
Second-pass audit found one remaining artifact-path fallback in saved plot-backtest run_context handling.
run_context is now strict: missing required fields or missing results file are explicit failures, not default path reconstruction.
Diagnostic frame-step inference has no default_ms parameter left.
```

One next test:

```text
Run plot-backtest against a valid saved PNO run and a deliberately incomplete copy of run_context.json; the first should use the exact results path and the second should fail before generating misleading artifacts.
```

---

## 15. Current local patch note - P061

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
BacktestRunner no longer converts a strategy bug (`generate_events_multi_tf` returning None) into an honest empty-position result.
A None return now fails immediately with `strategy_returned_none` and includes strategy, symbol, levels TF, entry TF and params signature.
```

One next test:

```text
python -m compileall strategy/pno cli constants.py vectorbt_runner
```

---

## 16. Current local patch note - P063

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
Archive aggTrades market-id resolution now uses CcxtFuturesClient.get_market_id(symbol) instead of reaching through the private ccxt client.
This keeps archive and live aggTrades paths behind the same typed boundary without adding a new verification layer or changing trade decisions.
```

One next test:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
```
