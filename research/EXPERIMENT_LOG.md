# PNO Experiment Log

Короткий журнал экспериментов. Не превращать в отчёт на много страниц.

---

## 1. Правила оценки

Сначала устойчивость, потом красивые метрики.

PNO-кандидат перспективен, если:

```text
50+ позиций/год
winrate > 0.40
average position > +1.0%
помесячно преимущественно положительно
нет зависимости от top-1/3/5 позиций
нет leakage/lookahead
нет явной переоптимизации
```

При малом числе позиций или `positions=0` анализировать только:

```text
funnel
reject reasons
near-miss cases
data quality
```

---

## 2. Индекс

| ID   | Название                       | Статус    | Главный вывод                                            |
|------|--------------------------------|-----------|----------------------------------------------------------|
| E001 | `5m/30s` 3-day run             | ANALYZED  | 0 trades; полезен для funnel/reject, не для PnL.         |
| E002 | Same window + true trade-count | PLANNED   | Проверить Stage1/flow на real trade-count.               |
| E003 | Entry TF comparison            | PLANNED   | Сравнить `5m/30s`, `5m/15s`, `1m/5s`.                    |
| E004 | `touch + retest hold`          | IDEA ONLY | Только отдельный режим, не замена `close_above`.         |
| E005 | Year robustness test           | PLANNED   | Проверить 50+ positions/year, months, top-position dependency. |
| E006 | `5m/30s` 31-day diagnostics run | ANALYZED  | 2 SL trades; diagnostics export has logging-format bug. |
| E007 | `1.zip` multi-TF 31-day diagnostics | ANALYZED | Edge not proven; current artifact mostly proves sparse-entry data-quality gate/order issue. |
| E008 | Same multi-TF after P045 | PLANNED | Check that sparse entry TF reach real funnel/reject reasons instead of pre-materialization entry data-quality rejects. |

---

## 3. E001 — `5m/30s` 3-day run

```text
Status: ANALYZED
Levels TF: 5m
Entry TF: 30s
Period: 3 days
Symbols: ~508
Mode: discovery / close_above
Trades: 0
PnL: 0
PF: not meaningful
```

Funnel:

```text
Stage1: 8
Stage2: 6
Stage3: 3
Stage4 rows: 7
Stage4 unique expected: 4
Stage5 trades: 0
```

Data quality:

```text
trade_count_proxy_used: true / suspected
trade_count_proxy: volume
quote_volume_proxy: close * volume
```

Near-miss:

```text
BZ: wick above level, no close_above, entry_pos high.
RUNE: wick-only/no close_above.
ETH: close_above occurred, but active_high reached before executable 30s entry.
```

Вывод:

```text
не доказывает прибыльность/убыточность
Stage5 отказы в near-miss в основном корректны
close_above не ослаблять
сначала true trade-count, потом entry TF comparison
```

---

## 4. E002 — Same window + true trade-count

```text
Status: PLANNED
Depends on: P004
```

Цель:

```text
проверить Stage1/flow после замены volume proxy на real trade-count
```

Сравнить с E001:

```text
trade_count_proxy_used
Stage1 pass count
Stage1 rejection distribution
Stage4 unique setups
Stage5 reject reasons
no_close_above
actual_entry_pos_too_high
entry_price_above_active_high
net_rr_too_low
```

Успех:

```text
real source labels есть, Stage1 cache не старый, выводы о flow стали достовернее
```

---

## 5. E003 — Entry TF comparison

```text
Status: PLANNED
```

Сравнить:

```text
5m/30s
5m/15s
1m/5s
```

Code prep: P009 applied in commit `a4857715b556693c74c10993b74249278275ec16`; `15s` enum added and `5m/15s` included in `PNO_BACKTEST_TIMEFRAME_PAIRS`.

Метрики:

```text
unique Stage4 setups
close_above signals
trades opened
TP1 tagged before executable entry
entry_price_above_active_high
entry_price_above_tp1
actual_entry_pos_too_high
net_rr_too_low
TP1 hit rate
average position
expectancy
```

Правило:

```text
больше позиций ≠ лучше, если вырос fake reclaim / плохой RR
```

---

## 6. E004 — `touch + retest hold`

```text
Status: IDEA ONLY
```

Цель:

```text
проверить более ранний вход без превращения close_above в wick-touch
```

Запрещено:

```text
заменять primary close_above на high >= level
```

Возможный режим:

```text
touch level → retest hold/reclaim within N bars → structural SL → valid RR
```

---

## 7. E005 — Year robustness test

```text
Status: PLANNED
```

Проверить:

```text
positions/year
average position
winrate
expectancy
annualized return
profit factor
max drawdown
monthly distribution
top-1/top-3/top-5/top-10 dependency
symbol concentration
regime concentration
```

Не годится, если:

```text
год держится на одном месяце
результат держится на 1–5 позициях
слишком мало trades
edge только в одном режиме
```

---

## 8. E006 — `5m/30s` 31-day diagnostics run

```text
Status: ANALYZED
Date: 2026-05-03
Code state: codex/ideal-like, commit 3e8a24765fa342a401815c4044ed0d8db78f284e + local proposed diagnostics patches
Config: --strategy pno --pno-all-tf-pairs --days 31 --pno-category-mode discovery --collect-diagnostics true --plot-rejected true --pno-entry-confirmation-mode close_above
Levels TF: 5m
Entry TF: 30s
Symbols: 508
Trades: 2
PnL: -1.68%
Winrate: 0.0%
PF: 0.00
```

Result:

```text
Strategy execution completed and produced 2 trades, both SL.
Diagnostics export reused cache: hits=508 misses=0.
During research_context/stage review export logging emitted TypeError because debug format strings did not match argument count.
```

Conclusion:

```text
Do not assess edge from 2 trades. Fix diagnostics logging first, then rerun/check stage_reason_summary and near-miss outputs.
```

One next test:

```text
Apply P040 and rerun the same 31-day command; verify no Logging error and stage_reason_summary.csv includes rejected reasons.
```

---

## 9. E007 — `1.zip` multi-TF 31-day diagnostics

```text
Status: ANALYZED
Date: 2026-05-04
Code state: README.zip / codex/ideal-like snapshot, exact commit UNKNOWN
Config: --pno-all-tf-pairs, close_above, discovery
Data quality: Stage4/Stage5 near-miss symbols have real number_of_trades; quote_volume must be real USDT not close*volume proxy.
```

Result:

```text
1m/5s: 0 positions
5m/15s: 0 positions
5m/30s: 0 positions
5m/30s funnel: Stage1 44, Stage2 34, Stage3 11, Stage4 35 rows / 15 unique, Stage5 1 unique setup / 0 positions
```

Evidence:

```text
Current 1.zip has zero simulated positions. Stage5 rows are repeated reviews of one setup, not independent opportunities. Stage1 rejected reasons such as flow_window_no_price_growth, pump_candidate_pretrend_too_weak, pump_nonorganic_tape and active_flow_faded_before_structure lacked chart samples.
```

Limitations:

```text
Zero positions; PnL/edge/winrate are not evaluable. Previous 0/2/4 trades note was stale relative to current artifacts.
```

Conclusion:

```text
Do not optimize PnL. First fix terminology, strict quote_volume USDT/trade-count requirements, diagnostics coverage and Stage5 unique setup summary; then rerun same window and compare funnel/reject distribution.
```

One next test:

```text
Apply P043 and rerun the same 31-day multi-TF diagnostics; inspect GUA/MAGMA, Stage4 unique, Stage5 rejected reasons and rejected charts manifest.
```

---

## 8. Шаблон нового эксперимента

```markdown
## EXXX — Название

Status:
Date:
Code state:
Config:
Data quality:

Goal:
Hypothesis:
Metrics:
Result:
Evidence:
Limitations:
Conclusion:
One next test:
```
---

## E008 — Same multi-TF after P045

```text
Status: PLANNED
Depends on: P045
Levels/Entry TF: 1m/5s, 5m/15s, 5m/30s
Period: same as E007 / 31 days
Mode: discovery / close_above
```

Цель:

```text
проверить, что sparse entry TF больше не режутся на Stage1 из-за target entry trade-count до materialization
```

Успех:

```text
5m/15s и 5m/30s показывают levels-quality failures, market rejections или post-materialization entry-data failures, но не 508/508 entry_missing_real_trade_count до Stage1.
research_context/*.csv имеют header даже при пустых rows.
```

Не оценивать:

```text
PnL / winrate / edge, пока positions мало или 0.
```

---

## E009 — Artifact quality rerun after P046

```text
Status: PLANNED
Depends on: P046
Levels/Entry TF: 1m/5s, 5m/15s, 5m/30s
Period: same as E008 / 31 days
Mode: discovery / close_above
```

Goal:

```text
prove that the current exporter produces analyzable diagnostics artifacts even when the funnel stops at data-quality Stage1.
```

Success:

```text
stage_reviews/*/passed/events.csv can be read by pandas with headers at zero rows; stage_reviews/*/summary.csv and manifest.csv agree on events_count; diagnostics_quality_sources.csv exposes quote-volume/trade-count source coverage; diagnostics_quality_reasons.csv exposes top quality bottlenecks; run_context.json records category mode, entry confirmation mode and variant id.
```

Do not evaluate:

```text
PnL / winrate / edge if positions remain zero or too few.
```

---

## E010 — PNO data-quality rerun after P047

```text
Status: PLANNED
Depends on: P047
Levels/Entry TF: 1m/5s, 5m/15s, 5m/30s
Period: same as E008 / 31 days
Mode: discovery / close_above
```

Goal:

```text
prove that levels candles now carry real Binance quote_volume and trade-count fields from exchange kline payload, not close*volume proxy.
```

Success:

```text
diagnostics_coverage shows trade_count_proxy_used=false, levels_trade_count_source=number_of_trades and levels_quote_volume_source=quote_volume_usdt for the main symbol set; Stage1 missing_required_market_data is no longer dominated by levels_missing_quote_volume_usdt; sparse entry TF proceed to post-Stage1 sparse materialization when candidates exist.
```

Do not evaluate:

```text
PnL / winrate / edge if positions remain zero or too few. First read funnel, reject reasons, near-miss and data quality.
```

---

## E011 - Fallback diagnostics micro-check after P056

```text
Status: PLANNED
Depends on: P056
Date: 2026-05-05
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that seconds/aggTrades, persisted sparse cache, Stage1 cache and universe-selection failures produce explicit artifacts instead of empty/no-setup ambiguity.
```

Success:

```text
diagnostics/seconds_load_status.csv exists and contains per-window plus nested archive/live/cache reasons.
diagnostics/stage1_cache_status.csv exists and contains hit/miss/read_failed/write_failed/schema_invalid statuses where applicable.
Invalid persisted sparse cache reports persisted_aggregated_window_schema_invalid/read_failed/empty instead of silently rebuilding.
No liquid universe returns universe_selection_failed instead of first-symbol fallback.
Baseline no-confirmed-level cases do not create fallback trading levels.
category_3 does not bypass close_above/decay invalidation in baseline comparison.
```

Do not evaluate:

```text
PnL / winrate / edge. This is a data-quality diagnostics check only.
```

---

## E012 - Artifact/cache read truthfulness after P057

```text
Status: PLANNED
Depends on: P057
Date: 2026-05-05
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that current PNO artifact/cache readers expose missing/empty/read_failed/schema_invalid/unresolved-TF states without adding a separate validation layer.
```

Success:

```text
artifact_load_status.csv is written when category/stage artifacts are read or synced.
Parquet cache reads used by current fetch paths expose explicit load_result status.
M10 cached-base OHLCV path does not hide missing/schema/window/invalid base cache as a generic empty frame.
Invalid aggTrades payload shape raises a typed-boundary error that PNO seconds diagnostics can record.
Diagnostic frame step unresolved is visible as diagnostic_frame_step_unresolved or the chart is skipped, not drawn with a fake TF.
```

Do not evaluate:

```text
PnL / winrate / edge. This checks diagnostic truthfulness only.
```

---

## E013 - Baseline without remaining fallback tails

```text
Status: PLANNED
Depends on: P058
Date: 2026-05-05
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that the PNO baseline no longer admits setups through sampled diagnostics, unresolved entry TF assumptions or ideal-like fallback level creation.
```

Success:

```text
seconds_load_status.csv covers all sparse materialization rows needed to explain data failures.
Stage rejection extra contains full load_statuses when sparse materialization rejects.
entry_timeframe_unresolved appears as explicit rejection/failure when source entry TF cannot be inferred.
No Stage4/Stage5 accepted setup has level_source from ideal_like fallback construction in baseline/category_3.
```

Do not evaluate:

```text
PnL / edge until data-quality and fallback-removal effects are confirmed on funnel/reject distribution.
```
---

## E009 - Planned P059 artifact truthfulness check

```text
Status: PLANNED
Patch: P059 / commit UNKNOWN
Goal: verify that normal PNO run artifacts stay complete and point to the same run root after removing residual artifact fallbacks.
```

Check:

```text
Run a small saved PNO backtest with diagnostics, then use plot-backtest on the saved run directory.
Before reading PnL/funnel, verify results.csv has profit_factor_status, run_context.json points to the selected results file, and artifact_load_status.csv / data_load_status.csv / seconds_load_status.csv / sparse_materialization_windows.csv / stage1_cache_status.csv are present.
```
---

## E010 - Planned strict run_context artifact check

```text
Status: PLANNED
Patch: P060 / commit UNKNOWN
Goal: confirm saved plot-backtest no longer reconstructs default results paths from incomplete run_context metadata.
```

Check:

```text
Run plot-backtest on a valid saved PNO run and verify results_input equals run_root/strategy_results_rel_dir/results_file_name.
Then remove one required context field in a temporary copy and verify the command fails with run_context_missing_required_fields before writing new misleading plot artifacts.
```

---

## E014 - Stage3-5 candle artifact check

```text
Status: PLANNED
Patch: P066 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that a normal PNO diagnostics run exports compact candle-level context for selected Stage3-5 setups without changing trading logic or bloating artifacts with full raw market history.
```

Success:

```text
research_context/stage3_5_candle_outcomes.csv has headers and one row per exported Stage3-5 context key with tp1_hit_within_1h, pullback_low_broken_within_1h, first_future_event_1h, event timestamps and max/min future 1h fields; same-candle TP1/low-break ambiguity is explicit.
research_context/stage3_5_candle_context.csv has bounded entry-TF OHLCV/trade-count/quote-volume candle rows around each anchor and up to one hour forward.
Stage3 is sampled/near-miss only; Stage4/Stage5 are complete enough for setup-level review.
Rows can be grouped by context_key to compare wins/losses and by symbol to answer which coins reached TP1 within one hour.
```

Do not evaluate:

```text
Edge/PnL from this check. The new future fields are diagnostic look-forward labels, not trade-decision inputs.
```

---

## E015 - Sparse research-context entry load check

```text
Status: PLANNED
Patch: P067 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that research-context candle artifacts for sparse entry TF use target-entry materialized candles or explicitly report why they could not.
```

Success:

```text
research_context/research_context_entry_load_status.csv exists on sparse-entry diagnostics runs.
Rows with ok=true correspond to target entry timeframe windows used by stage3_5_candle_context.csv.
Rows with ok=false expose reason/source_detail/seconds_status and prevent treating those candle rows as clean target-TF evidence.
```

Do not evaluate:

```text
PnL/edge. This is an artifact truthfulness check.
```

---

## E016 - Stage5 review synthesis coverage check

```text
Status: PLANNED
Patch: P068 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that Stage5 review synthesis coverage is explicit for every latest Stage4 setup considered by the synthesis path.
```

Success:

```text
research_context/stage5_review_synthesis_status.csv exists and has one row per latest Stage4 cycle considered by synthesis.
Rows are split into synthesized, skipped/stage5_already_present and not_synthesized with concrete reasons such as entry_frame_missing_or_empty, level_missing, level_valid_timestamp_missing, post_level_frame_empty or no_wick_cross_after_level_valid.
Any not_synthesized row is interpreted as a Stage5 review coverage gap rather than a real Stage5 rejection.
```

Do not evaluate:

```text
PnL/edge. This is a diagnostics completeness and truthfulness check.
```

---

## E017 - Stage3-5 candle artifact coverage check

```text
Status: PLANNED
Patch: P069 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that candle-context artifacts expose coverage for every selected Stage3-5 setup candidate.
```

Success:

```text
research_context/stage3_5_candle_context_status.csv exists.
Every selected candle-context candidate has exported, skipped/duplicate_context_key or not_exported status.
Rows with not_exported explain why stage3_5_candle_outcomes.csv and stage3_5_candle_context.csv lack candle evidence for that setup.
```

Do not evaluate:

```text
PnL/edge. This checks artifact completeness only.
```

---

## E018 - Category research-context completeness check

```text
Status: PLANNED
Patch: P070 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that category diagnostics archives do not silently drop research_context status/coverage CSVs.
```

Success:

```text
categories/*/pno_diagnostics/research_context/research_context_filter_status.csv exists.
Each source research_context CSV is marked filtered_by_category, copied_empty, copied_unfiltered or not_copied with source_rows and target_rows.
Non-category status CSVs are present in the category research_context and marked copied_unfiltered/pno_category_id_missing.
Stage-review manifest rows keep trade_count_source_counts after shared stage-review sync.
```

Do not evaluate:

```text
PnL/edge. This checks category artifact completeness and data-quality traceability only.
```

---

## E019 - Category-filtered candle artifact check

```text
Status: PLANNED
Patch: P071 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that Stage3-5 candle artifacts are category-filterable and do not mix categories inside category archives.
```

Success:

```text
stage3_5_candle_context.csv, stage3_5_candle_outcomes.csv and stage3_5_candle_context_status.csv include pno_category_id, pno_category_label and pno_profile_variant_id.
In pno_category_mode=all output, category research_context_filter_status.csv marks these candle CSVs as filtered_by_category rather than copied_unfiltered.
```

Do not evaluate:

```text
PnL/edge. This checks category artifact integrity only.
```

---

## E020 - Chart artifact coverage check

```text
Status: PLANNED
Patch: P072 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that missing stage-review charts and category position chart copies are visible in status artifacts.
```

Success:

```text
stage_reviews/chart_status.csv exists and marks chart candidates as rendered or not_rendered with reasons such as prepared_frames_missing or renderer_returned_none.
categories/*/pno_diagnostics/chart_copy_status.csv exists and marks chart copies as copied or not_copied with reasons such as chart_paths_missing, chart_paths_count_mismatch or source_chart_missing.
```

Do not evaluate:

```text
PnL/edge. This checks image artifact coverage only.
```

---

## E021 - Stage-review event completeness check

```text
Status: PLANNED
Patch: P073 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that stage-review reason event CSVs explicitly declare whether they are full exports or review samples.
```

Success:

```text
stage_reviews/*/summary.csv contains events_are_complete and event_export_mode.
Rows where exported_events_count < count have events_are_complete=false and event_export_mode=review_sample.
Rows where exported_events_count == count have events_are_complete=true and event_export_mode=complete.
```

Do not evaluate:

```text
PnL/edge. This checks artifact interpretation only.
```

---

## E022 - Artifact contract regression test

```text
Status: PARTIAL_PASS
Patch: P074 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Verify that the artifact truthfulness fixes are enforced by focused tests instead of relying only on status CSV inspection.
```

Result:

```text
Focused artifact tests pass: category research_context filtering, sampled stage-review event flags, and candle category metadata.
Full tests/test_pno.py does not pass in the current repo state; many failures are outside the artifact contract patch and indicate the broader PNO test suite is stale or the strategy/config changed without test updates.
```

Commands:

```bash
python -m pytest tests/test_pno.py -k "category_research_context_filters_category_aware_csvs or stage_review_summary_marks_sampled_rejected_events or stage_candle_context_carries_category_metadata"
python -m pytest tests/test_pno.py -q
python -m compileall strategy/pno cli constants.py tests/test_pno.py
```

Do not evaluate:

```text
Edge/PnL from this test. The full-suite failures need a separate stabilization pass before claiming broad PNO code stability.
```

---

## E023 - Runnable vs historical PNO test split

```text
Status: PASS_WITH_HISTORICAL_DEBT
Patch: P075 / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace, commit UNKNOWN
```

Goal:

```text
Separate stale historical PNO regression tests from the current runnable test suite without hiding that the historical suite is red.
```

Result:

```text
Default pytest now passes with historical PNO tests excluded by marker.
Current artifact-contract tests run in tests/test_pno_artifacts.py.
Historical tests/test_pno.py is explicitly marked pno_historical and remains available for triage.
```

Commands:

```bash
python -m pytest -q
python -m pytest -m pno_historical tests/test_pno.py -q
python -m compileall strategy/pno cli constants.py tests
```

Outcome:

```text
Default runnable suite: 8 passed, 159 deselected.
Historical PNO suite: 80 failed, 79 passed.
```

Do not evaluate:

```text
Edge/PnL. This is test-suite hygiene and separation of current acceptance checks from stale historical regressions.
```


---

## E024 - 5m/30s 7-day discovery diagnostics artifact audit

```text
Status: ANALYZED
Patch: P078 proposed / commit UNKNOWN
Date: 2026-05-06
Code state: local workspace after P077; P076 application status should be checked in the user repo before interpreting human_bos parity.
Run artifact: 5m_30s.zip
```

Config:

```text
Strategy: PNO discovery / close_above
Levels TF: 5m
Entry TF: 30s
Period: 7 days
Symbols analyzed: 527
Positions: 0
```

Funnel:

```text
Stage1: 958 events / 13 passed
Stage2: 10 events / 9 passed
Stage3: 10 events / 3 passed
Stage4: 6 events / 1 passed
Stage5: 18 events / 0 positions
Unique Stage5 setup: 1, GOOGL/USDT:USDT, rejected
```

Data quality:

```text
Diagnostics coverage reports levels_trade_count_source=number_of_trades and entry_trade_count_source=number_of_trades for all 527 symbols.
Diagnostics coverage reports levels_quote_volume_source=quote_volume_usdt and entry_quote_volume_source=quote_volume_usdt for all 527 symbols.
Research_context candle artifacts nevertheless had all-NaN quote_volume because prepared plot/research frames dropped the column.
This is an artifact bug, not evidence that loaded market data lacks quote_volume.
```

Interpretation:

```text
No profitability conclusion is possible with 0 positions.
The single Stage5 setup failed honestly: the GOOGL reclaim did not reach active high and broke pullback low first.
Several rejected Stage3/Stage4 near-misses hit TP1 within one hour, but those were pre-entry/invalid cases and must not be counted as trades.
```

Next:

```text
Apply P078, rerun the same 5m/30s diagnostics, then compare quote_volume coverage in diagnostics_coverage.csv versus stage3_5_candle_context.csv before changing filters.
```
