# PNO Research State

Короткая рабочая память проекта. Держать компактной. Подробные правила работы — в Project Instructions.

---

## 1. Текущее состояние

```text
Branch: codex/ideal-like
Commit: e961252 (GitHub head checked); ZIP-local patch stack remains source-of-truth for uncommitted code
Local diff: P076 applied locally on top of ZIP-local stack through P075; commit UNKNOWN
Last applied patch: P080 proposed locally; compile verified; commit UNKNOWN
Last analyzed run: 20260506_104450_pno 7-day multi-TF run from 2.zip
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
21. 7-day multi-TF artifacts showed that source-entry data-load status is not enough for seconds-entry runs; target-entry sparse materialization needs a global status table and enough pre-roll to avoid false `sparse_entry_materialized_insufficient_bars`.
22. P079 artifact tables existed in the 7-day run, but were empty because category diagnostics merge dropped sparse materialization context; P080 propagates those context keys so target-entry materialization failures are exported instead of hidden inside Stage2 rejection payloads.

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
| P079 | Sparse entry audit trail and sizing | PROPOSED | Писать `sparse_entry_materialization_status.csv`, явно маркировать source/target entry TF и расширить sparse pre-roll до required bars. |
| P081 | Remove prior-local-high human BOS reject | PROPOSED / compile verified locally | Вырезать только `human_bos_below_prior_local_high`; later-local-high obsolete guard оставить. |

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

## 32. Current local patch note - P078

```text
Status: PROPOSED
Updated: 2026-05-06
```

Current conclusion:

```text
The 5m/30s 7-day diagnostics run is usable for funnel analysis, not edge.
Data coverage says real number_of_trades and quote_volume_usdt are available, but research_context artifacts were dropping quote_volume in prepared plot/research frames.
P078 fixes artifact truthfulness by carrying quote_volume through prepared levels/entry frames and exporting flow fields in stage5_levels_path_context.
No proxy quote volume is introduced and trading logic is unchanged.
```

One next test:

```text
Rerun the same 5m/30s diagnostics and check diagnostics_coverage.csv against research_context/stage3_5_candle_context.csv: if source is quote_volume_usdt, quote_volume should not be all NaN.
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


---

## 33. Current local patch note - P079

```text
Status: PROPOSED
Updated: 2026-05-06
```

Current conclusion:

```text
The 7-day multi-TF artifacts are mostly honest on real trade-count and quote-volume, but seconds-entry auditability is incomplete.
For 5s/15s/30s target entry TF, data_load_status only proves the source 1m/levels frame; target-entry sparse materialization happens later and must have its own run-level status table.
P079 adds sparse_entry_materialization_status.csv, labels source/target entry TF in data_load_status and run_context, and expands sparse fetch pre-roll to the target-entry required bar span to reduce false insufficient-bars rejections.
Trading thresholds, close_above, TP/SL and scoring are unchanged.
```

One next test:

```text
Run the same 7-day --pno-all-tf-pairs diagnostics and inspect sparse_entry_materialization_status.csv before reading funnel/PnL; target_entry_usable_reason should explain every failed sparse materialization, and avoidable insufficient-bars cases should disappear or expose a concrete cache/window cause.
```

---

## 34. Current local patch note - P081

```text
Status: PROPOSED / compile verified locally
Updated: 2026-05-06
```

Current conclusion:

```text
P081 is a deliberate trading-logic experiment, not a data-quality patch.
It removes the `human_bos_below_prior_local_high` rejection by skipping confirmed highs that occurred before the selected BOS level.
The existing `human_bos_obsolete_under_later_local_high` guard remains, so newer local highs after the selected BOS can still obsolete the level.
This should increase Stage5 attempts only for prior-high-blocked human_bos setups; edge/PnL still requires positions and robustness checks.
```

One next test:

```text
Run the same 7-day --pno-all-tf-pairs diagnostics and compare Stage5 reject distribution: `human_bos_below_prior_local_high` should disappear; track whether those cases become later-obsolete, no_close_above, invalidated-before-trigger, or real positions.
```

---

## 35. Current branch direction - anomaly continuation lab

```text
Status: STARTED
Updated: 2026-05-06
Branch: codex/pno-anomaly-continuation-lab
```

Current conclusion:

```text
This branch separates early wake-up continuation research from current PNO pullback execution.
The new lab artifact is not a trading strategy and does not relax PNO engine filters.
It studies anomaly starts using only metrics available after a fixed confirmation-candle window, then attaches future outcome labels for analysis.
Start-pump verticality is now first-class: bounded verticality score plus path efficiency, range efficiency, slope pct per candle, max retrace fraction and green-candle share.
```

One next test:

```text
Generate anomaly_continuation_lab.csv across all cached symbols, then test whether persistence + verticality + price retention separate 25%+ continuations from fast fades without relying on a tiny number of symbols or rare runners.
```

---

## 36. Current runner-date review note

```text
Status: ANALYZED
Updated: 2026-05-06
Branch: codex/pno-anomaly-continuation-lab
```

Current conclusion:

```text
For the known recent runner list, strict 10x/60-candle anomaly settings only identify IO 2026-05-06 as a big_25p target-date candidate.
Relaxed 5x/240-candle settings identify LAB, ZEC, PLAY 2026-05-04, PLAY 2026-05-06 and IO as post-decision +25% continuations.
This suggests that threshold sensitivity is material. The lab should not jump directly to strategy rules before comparing all-symbol fader contamination under the same relaxed settings.
```

One next test:

```text
Run relaxed 5x/240-candle settings across all cached symbols and compare fader rate by hold_count, verticality and price_retention bins.
```

---

## 37. Current anomaly profitability status

```text
Status: TOOLING READY / EDGE NOT PROVEN
Updated: 2026-05-06
Branch: codex/pno-anomaly-continuation-lab
```

Current conclusion:

```text
The repository now has a command-level anomaly profitability run: python main.py run-anomaly-lab.
Default logic: decision-close long entry after confirmation candles, structural box stop, 50% TP1 at 1R, breakeven after TP1, rolling swing-low trail.
The first smoke run on recent runner symbols is slightly negative after fees, so the current default rule is not a proven edge.
The artifact is now suitable for honest research because every signal has entry, stop, TP1/trail behavior, MFE/MAE and net return.
```

One next test:

```text
Run anomaly profitability across all cached symbols, then segment expectancy by hold_count_next_n_candles, start_verticality_score, price_retention_next_n and initial_risk_pct before changing thresholds.
```

---

## 38. Current anomaly OI coverage status

```text
Status: TOOLING READY / CACHE OI MISSING
Updated: 2026-05-06
Branch: codex/pno-anomaly-continuation-lab
```

Current conclusion:

```text
Anomaly lab artifacts now include open-interest context only as 5m as-of data at decision time.
The implementation does not create synthetic 1m OI and does not fill missing OI with zero.
The latest runner-symbol OI check wrote oi_context_status.csv with 2224 candidate rows across 8 symbols all marked missing_column, meaning the current selected 5m cache has no open_interest column.
Therefore no conclusion about OI dynamics is currently supported by the available cache.
```

One next test:

```text
Refresh/fetch 5m cache with open_interest for the runner-symbol set, rerun run-anomaly-lab, then segment net_return and big_25p/fast_fade by oi_change_pct_1x5m, oi_change_pct_3x5m and oi_age_ms.
```

Progress note:

```text
run-anomaly-lab prints percent and ETA for candidate collection and trade simulation after P086. This is a logging/UX change only.
```

Research update:

```text
E035 found the most promising current non-future layer: oi_change_pct_3x5m > 3% plus hold_count_next_n_candles >= 2.
On the 7-day all-symbol run it produced 36 closed trades across 34 symbols and 6 days, win rate 66.7%, avg net +1.29%.
Entry analysis favors structural pullback into the known impulse/confirmation box, especially around box_low + 0.75 * box_range, over a naive candle-number entry.
This remains a hypothesis, not a proven stable edge, until tested on longer history and pre-declared grids.
```

Tooling update:

```text
P088 adds run-anomaly-lab --run-entry-grid. The next OI test should use a predeclared 31-day grid and compare anomaly_entry_grid_summary.csv before selecting a specific rule.
```

Feature update:

```text
P089 adds flow/effort/sleep features for runner-vs-fader analysis: taker buy quote share, avg trade quote size, effort per return, range expansion vs baseline, wick/body/range structure, post-start pullback fraction and OI x price interaction.
These features are analysis-only and should be evaluated on the next 31-day predeclared grid before adding any new filter.
```

Honesty/performance update:

```text
P090 removes the remaining anomaly signal risk proxy. Signals now use the exact decision_box_low/high/range already known at decision time.
Entry-grid variants reuse cached symbol frames across the grid, reducing repeated parquet reads without changing trade logic.
```

OI fetch note:

```text
P091 clamps OI fetch start time to Binance's rolling 30-day history boundary and aligns OI requests to timeframe boundaries.
Older OI remains unavailable rather than backfilled or synthesized.
```

31-day edge audit:

```text
E038 analyzed .output/results/anomaly_lab_31d_entry_grid.
The initial 7-day OI+hold edge weakened materially after broader OI coverage across roughly 30 days.
Best broader row: pullback_box_fraction=0.85, hold>=2, oi_change_pct_3x5m>3%, 158 closed trades, win rate 50.63%, avg net +0.398%.
Strict pullback 0.75 / hold>=2 / oi3>3 resimulation: 155 trades, 124 symbols, 29 days, win rate 51.61%, avg net +0.261%, sum +0.404.
Conclusion: OI expansion is useful but not enough; next step is anti-exhaustion filtering, especially against too-large quote/trade/avg-trade-size effort per price progress.
```

Anti-exhaustion grid update:

```text
P092 adds optional anti-exhaustion filters and grid profiles none/mild/balanced/strict.
The next 30-day test should use none,mild,balanced first to preserve trade frequency and only inspect strict later if the softer profiles still leave enough trades.
```

30-day anti-exhaustion result:

```text
E040 analyzed .output/results/anomaly_lab_30d_exhaustion_grid.
Default anomaly market rule remains negative, but strong OI expansion plus anti-exhaustion filtering produces a materially better candidate.
Best current candidate: market entry, balanced exhaustion profile, hold>=2, oi_change_pct_3x5m>5%; 31 trades, 27 symbols, 18 active days, win rate 61.29%, avg net +2.61%, median +2.58%.
Interpretation: runners are not simply the largest volume spikes; the better pattern is controlled wake-up volume/trades, OI expansion, persistence/hold, moderate avg trade size, lower effort-per-return and no blowoff range expansion.
Risk: result is still top-trade dependent and one-regime only; do not deploy or further overfit before out-of-window/month split validation.
```

Leakage/exit/metrics review:

```text
E041 found no obvious lookahead in current anomaly signal construction: future labels/MFE/MAE are artifacts, while filters use decision-time features and as-of 5m OI.
Main risk is overfitting from selecting thresholds on one recent 30-day window.
Exit check suggests TP1 1R on 25% instead of 50% can improve average on the current best set, but it lowers median and still needs out-of-window validation.
Do not loosen runner trail aggressively yet; higher TP/looser trail variants increased dependence on top winners.
Next useful exchange metrics: funding/premium basis, mark-index basis, global/top long-short ratios, taker long-short ratio, and spot-vs-perp divergence.
```

Derivatives context tooling:

```text
P093 adds honest optional derivatives context columns to anomaly artifacts.
The lab now reads only explicit cached funding/premium/mark/long-short/taker-ratio parquet files and writes market_context_status.csv.
If those files are absent, status is missing_frame and values remain NaN; no proxy/fill/fallback is used.
These fields are analysis-only for now and must not be used as filters until coverage is checked on the target run.
```

Derivatives context fetch:

```text
P094 wires derivatives context collection into fetch-data/update-cache by default, with --skip-derivatives-context as an explicit opt-out.
Smoke on ZEC wrote funding, premium_index/5m, mark_price/5m, global/top long-short and taker long-short cache files; anomaly-lab then reported ok coverage for these sources except one funding row before the first funding timestamp.
Spot-vs-perp divergence is not implemented yet because current cache/fetch stack is futures-only; adding spot needs a separate spot client/cache namespace to avoid mixing markets.
```

Derivatives context fetch note:

```text
P095 clamps derivatives context fetch start times to Binance's rolling 30-day boundary with a per-source interval buffer.
This fixes startTime invalid errors on long-short endpoints near the 30-day edge.
Older context rows remain missing; no backfill/proxy is created.
```

Derivatives context performance note:

```text
P096 changes derivatives context collection from default-on full-universe cache to event-window lazy collection in run-anomaly-lab.
Reason: full-universe 30-day 5m long-short/premium/mark context is tens of thousands of Binance endpoint calls and is not appropriate for ordinary cache updates.
Ordinary update-cache/fetch-data remains OHLCV/OI focused unless --with-derivatives-context is explicitly requested.
run-anomaly-lab fetches derivatives context around a small post-filter signal set; large grids use cache-only context and print that limitation explicitly.
Artifacts remain honest through market_context_status coverage instead of proxies/fallback fills.
```

Derivatives context cache efficiency:

```text
P097 adds per-source timestamp-bound checks to DerivativesContextFetcher.
Repeated anomaly-lab runs should not refetch already cached event windows; only prefix/suffix misses are requested.
Internal historical holes are not synthesized or filled by proxy, so coverage must still be read from market_context_status.csv.
```

Derivatives context event universe:

```text
P098 removes the arbitrary lazy-fetch signal cap.
For anomaly entry grids, context fetch now uses the union of post-filter grid signals after OI/hold/exhaustion stages.
This keeps raw discovery broad but fetches expensive derivatives data only where the research stage has already narrowed candidates.
market_context_fetch_status.csv records fetch success/errors per symbol; missing context remains visible in market_context_status.csv.
```

Derivatives context local enrichment:

```text
P099 fixes the post-fetch silent bottleneck: derivatives context enrichment is now scoped to the post-filter signal universe.
Raw candidates outside that universe receive explicit not_in_context_universe context statuses.
This preserves broad anomaly discovery while keeping expensive context work tied to the narrowed research stage.
```

Anomaly lab progress contract:

```text
P100 standardizes run-anomaly-lab progress reporting to phase-level 5% ETA logs without instrument names.
Expensive detail remains in CSV status artifacts rather than console spam.
Entry-grid signal sets are computed once and reused for context universe plus grid simulation to avoid duplicated filtering work.
```

Anomaly best-grid artifacts:

```text
P101 adds chart and health artifacts for the best grid variant in run-anomaly-lab.
For grid runs, charts target the selected best grid trades, not the broad raw anomaly trades.
Current 30-day derivatives-context grid now has 31 rendered best-grid trade charts and anomaly_edge_health.csv.
Health table use is diagnostic only: it highlights weak aspects such as top-tail dependence, not a deploy/deploy-not score.
P102 upgrades anomaly trade charts to the established PNO diagnostics style: 1m price, 5m context, risk/profit zones, price tags, quote-volume and canonical exchange trade-count panels.
P103 changes the last panel to quote_volume / number_of_trades per candle, adds EMA20 exit-rule grid support, and exposes baseline_zero_range_share for future sleep-quality analysis.
On the current 31-trade best config, EMA20 exits were worse by winrate/median than structural_trail; treat them as grid hypotheses, not default exits.
```

---

## 2026-05-09 Live Micro Validation Update

```text
Live validation moved from plan to first strict implementation slice.
Command: run-anomaly-live.
Mode: real micro-live only with explicit --confirm-real-orders guard; no paper fallback.
Operational truth: REST-only, max open positions default 3, two Telegram bots, artifacts under results/live_anomaly_runs.
Data honesty: quote_volume, number_of_trades and required OI must be present and fresh; otherwise setup is rejected, not approximated.
Residual risk: first live slice needs exchange-level small-symbol smoke with max-cycles before unattended run; order semantics depend on Binance/CCXT STOP_MARKET support.
```

2026-05-09 follow-up:

```text
Live/backtest stop semantics changed to max(previous structural stop, EMA20), not max(pump_bottom, EMA20).
CVX-like no-sleep patterns are targeted by a relative prior up-down whipsaw guard, not by symbol ban and not by absolute baseline range.
Live shared state is protected by RLock; position threads and scanner no longer read/write open position dictionaries without synchronization.
Scanner now checks a recent decision-candle backfill window and deduplicates seen decisions; this is required because full-universe REST rotation can inspect a symbol several minutes after the actual confirmation candle.
Still required before real unattended use: fill env, run one-symbol max-cycles smoke, then verify Binance STOP_MARKET payload on an intentionally tiny position.
```

2026-05-09 env cleanup:

```text
.env no longer contains obsolete COINGECKO_API_KEY, IGNORE_COINGECKO or FETCH_ANCHOR_DATETIME.
If a fixed fetch anchor is needed, current config expects FETCH_ANCHOR_TIMESTAMP_MS.
```

2026-05-09 live incident fix:

```text
Observed live error: slots LiveSignal had no __dict__, so ledger serialization failed after order placement.
Observed live error: tight-stop sizing could request notional larger than available margin and trigger Binance -2019.
Fix: LiveSignal serializes via asdict; live caps notional to 95% of free USDT and rejects if that cannot satisfy 12 USDT minimum.
Operational note: any live instance started before this patch should be stopped and restarted after checking the exchange position/stop state.
```

2026-05-09 live PnL fix:

```text
Observed Telegram closes after TP1/trailing reported 0.00 USDT because flat-position reconciliation used entry price when no explicit pnl_price was passed.
Fix: live position tracks current_stop_price and realized TP1 PnL; final close PnL is realized_pnl + remaining_amount * (exit - entry).
```

2026-05-09 live artifacts review:

```text
Observed too many live entries because live missed the balanced exhaustion caps used in research grid; BANANA/CARV/BANK-like quote/trade ratio extremes should be rejected.
Fix: live defaults to max_start_quote_ratio=80 and max_start_trade_ratio=40.
Observed no Telegram charts; close handler now renders and sends a compact chart PNG.
Observed oversized notional on tight stops; live now caps notional per remaining position slot.
```
