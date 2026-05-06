# PNO Research State

Короткая рабочая память проекта. Держать компактной. Подробные правила работы — в Project Instructions.

---

## 1. Текущее состояние

```text
Branch: codex/ideal-like
Commit: e961252 (GitHub head checked); ZIP-local patch stack remains source-of-truth for uncommitted code
Local diff: P076 applied locally on top of ZIP-local stack through P075; commit UNKNOWN
Last applied patch: P076 (local workspace; commit UNKNOWN)
Last analyzed run: E008 multi-TF 31-day run from 1.zip after P045
Updated: 2026-05-06
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
8. H4 исправляется локальным P076: `human_bos` не должен получать auto-valid Stage4 score или Stage5 close-trigger bypass; нужна same-window проверка rejection distribution.
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
20. PNO sparse seconds provider exposes only status-carrying `*_result` load paths for aggregated windows, seconds windows, per-day seconds, archive aggTrades, live aggTrades and aggTrades aggregation; status-dropping DataFrame compatibility wrappers were removed.

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
| P076 | Strict PNO generation and human_bos parity | APPLIED locally / UNKNOWN commit | Не маскировать `None` как 0 positions; убрать оставшийся `human_bos` auto-valid/close-trigger bypass. |

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
| H4 | `human_bos` может обходить часть Stage4/Stage5 качества. | P076 убирает оставшийся provisional auto-valid score и close-trigger bypass. | Rerun same 31d multi-TF diagnostics; сравнить human_bos reject distribution. |
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

---

## 17. Current local patch note - P062

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
The sparse seconds provider no longer keeps dead compatibility wrappers that returned only DataFrame frames and discarded ok/status/reason/load_statuses.
The remaining load path is the status-carrying result API, so failures cannot be silently flattened by these wrappers.
Trading logic is unchanged because all removed wrappers had no callers in the current codebase.
```

One next test:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
```

---

## 18. Current local patch note - P064

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-05
```

Current conclusion:

```text
PNO artifact rebuild no longer reconstructs params by mixing results.csv with current PnoParams defaults.
Missing/blank required result-row params now fail explicitly as results_row_missing_required_pno_fields; invalid scalar values fail as results_row_invalid_pno_field; TF mismatch fails as results_row_timeframe_mismatch.
This makes plot-from-results/stage artifact rebuild less backward-compatible but more truthful.
```

One next test:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
```

---

## 19. Current local patch note - P065

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Existing PNO data-failure branches no longer collapse caught exceptions into only generic status labels.
Stage1 cache read/write, persisted sparse aggregated cache read, Binance archive fetch/read and live aggTrades fetch now add exception_type and bounded exception_message to their existing failure payloads.
Trading decisions, rejection reasons and fallback behavior are unchanged.
```

One next test:

```text
python -m compileall data/exchanges strategy/pno cli constants.py main.py launcher.py vectorbt_runner
```

---

## 20. Current local patch note - P066

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
PNO diagnostics now export compact Stage3-5 candle artifacts for external analysis.
research_context/stage3_5_candle_outcomes.csv answers setup-level future questions such as TP1 reached within one hour and whether TP1 or pullback-low break happened first; same-candle order is marked ambiguous.
research_context/stage3_5_candle_context.csv stores bounded entry-TF OHLCV/trade-count/quote-volume candles around selected Stage3-5 anchors plus one hour forward.
To control artifact size, pre-anchor context is 60 entry-TF candles and Stage3 candle rows are sampled/near-miss only; Stage4/Stage5 remain full.
This is diagnostic look-forward metadata only; it must not enter trade decisions or parameter scoring.
```

One next test:

```text
Run a small PNO diagnostics sample and inspect the two stage3_5 candle CSVs, especially first_future_event_1h, before using them for win/loss candle-pattern analysis.
```

---

## 21. Current local patch note - P067

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
One remaining artifact fallback was found: research/plot candle paths could use source entry_frame when sparse target-entry materialization was needed.
PNO research-context export now uses load_aggregated_window_result when target entry TF is finer than the source frame and writes research_context/research_context_entry_load_status.csv.
If target-entry candle loading fails, the failure is visible in artifacts instead of being hidden behind a coarser source-frame fallback.
Trading decisions are unchanged.
```

One next test:

```text
Run a small sparse-entry PNO diagnostics sample and inspect research_context_entry_load_status.csv before interpreting stage3_5 candle context.
```

---

## 22. Current local patch note - P068

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Another artifact truthfulness gap was found in Stage5 review synthesis.
Latest Stage4 setups without an existing Stage5 row could be silently skipped when synthesis lacked entry candles, level metadata, timestamps or a post-level wick cross.
PNO diagnostics now export research_context/stage5_review_synthesis_status.csv, making each synthesized/skipped/not_synthesized outcome explicit.
Trading logic is unchanged.
```

One next test:

```text
Run a small PNO diagnostics sample and inspect stage5_review_synthesis_status.csv before treating Stage5 review artifacts as comprehensive.
```

---

## 23. Current local patch note - P069

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Stage3-5 candle artifacts now have explicit export coverage.
Previously a selected setup could be absent from stage3_5_candle_outcomes.csv if candle context could not be built.
PNO diagnostics now export research_context/stage3_5_candle_context_status.csv so missing candle rows are visible as not_exported with a concrete reason.
Trading logic is unchanged.
```

One next test:

```text
Run a small PNO diagnostics sample and inspect stage3_5_candle_context_status.csv before using candle artifacts for win/loss pattern analysis.
```

---

## 24. Current local patch note - P070

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Category research_context artifacts had a remaining completeness risk.
CSV files without pno_category_id were silently absent from category archives, which would hide global status/coverage artifacts such as candle and synthesis status tables.
Category research_context now writes research_context_filter_status.csv and copies non-category CSVs unfiltered with an explicit copied_unfiltered marker.
Shared stage-review manifest sync keeps trade_count_source_counts.
Trading logic is unchanged.
```

One next test:

```text
Run a small pno_category_mode=all diagnostics sample and inspect research_context_filter_status.csv in each category archive.
```

---

## 25. Current local patch note - P071

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Stage3-5 candle artifacts now carry category metadata.
Without pno_category_id, category archives could not honestly filter candle context/outcome/status rows and could mix all categories.
The candle CSV schemas now include pno_category_id, pno_category_label and pno_profile_variant_id.
Trading logic is unchanged.
```

One next test:

```text
Run a small pno_category_mode=all diagnostics sample and verify candle CSVs are filtered by category, not copied_unfiltered.
```

---

## 26. Current local patch note - P072

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Chart artifact coverage now has explicit status.
Stage-review charts can fail to render when prepared frames are unavailable or the renderer returns no path; category chart copies can fail when chart_paths are absent/mismatched or source files are missing.
PNO diagnostics now write stage_reviews/chart_status.csv and category chart_copy_status.csv so image coverage gaps are visible.
Trading logic is unchanged.
```

One next test:

```text
Run a chart-enabled diagnostics sample and inspect chart_status.csv / chart_copy_status.csv before relying on image completeness.
```

---

## 27. Current local patch note - P073

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Stage-review rejected event CSVs can be complete or review samples.
The archive now marks this explicitly in each stage_reviews/*/summary.csv via events_are_complete and event_export_mode.
Trading logic and event selection are unchanged.
```

One next test:

```text
Inspect stage_reviews/*/summary.csv and only treat reason events.csv as full when events_are_complete=true.
```

---

## 28. Current local patch note - P074

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
Artifact truthfulness changes now have focused regression tests.
The tests verify category filtering of research_context, explicit sample/full stage-review summary flags, and category metadata in Stage3-5 candle artifacts.
tests/test_pno.py collection was blocked by stale TradeResult imports; those imports now point to current PositionResult modules.
Full tests/test_pno.py is not green in the current repo state, so codebase stability is not proven by these focused tests.
```

One next test:

```text
Make the broader PNO test suite green or split stale historical regression tests from current runnable artifact/engine tests.
```

---

## 29. Current local patch note - P075

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
The test suite is now split instead of pretending the stale PNO regression file is green.
Default pytest excludes tests marked pno_historical and runs the current runnable suite.
tests/test_pno_artifacts.py contains current artifact-contract tests for category research_context filtering, stage-review event completeness flags and Stage3-5 candle category metadata.
tests/test_pno.py remains available via -m pno_historical and currently fails 80 tests, which is an explicit debt bucket rather than hidden default-suite noise.
```

One next test:

```text
Triage the pno_historical failures into stale test expectations versus real PNO strategy regressions.
```

---

## 31. Current local patch note - P077

```text
Status: PROPOSED
Updated: 2026-05-06
```

Current conclusion:

```text
The pandas FutureWarning during sparse seconds loading was caused by concatenating an empty schema-only seconds frame with newly fetched seconds candles.
This is not a trading/data rejection and not a PNO signal issue, but it pollutes runtime logs and may become unstable when pandas changes concat dtype inference.
P077 replaces the empty frame with fetched candles directly and only uses concat when both sides contain rows.
Trading logic, data filters, rejection reasons and artifact schemas are unchanged.
```

One next test:

```text
Run the same 7-day pno-all-tf-pairs diagnostics command and verify the FutureWarning from pno_strategy.py:799 is gone before reading funnel/PnL.
```


---

## 30. Current local patch note - P076

```text
Status: APPLIED locally / UNKNOWN commit
Updated: 2026-05-06
```

Current conclusion:

```text
PNO no longer converts category/profile generation None into an honest zero-position result.
human_bos Stage4 candidates are provisional until normal score rebuild, and Stage5 close_above trigger quality filters now run for human_bos the same way as for ordinary reclaim levels.
This is a small trading-logic tightening for human_bos only; data collection and artifact schemas are unchanged.
```

One next test:

```text
Run the same 31-day multi-TF diagnostics and compare human_bos Stage4/Stage5 rejection reasons before/after P076.
```
