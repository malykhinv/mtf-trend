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


---

## E025 - Planned sparse-entry artifact audit after P079

```text
Status: PLANNED
Patch: P079 proposed / commit UNKNOWN
Date: 2026-05-06
Code state: local ZIP workspace after P078/P079 proposal; commit UNKNOWN
Run artifact basis: 2.zip 7-day multi-TF run
```

Goal:

```text
Verify that seconds-entry PNO runs expose target-entry sparse materialization health as a first-class artifact before any funnel/PnL interpretation.
```

Command:

```bash
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected true --pno-entry-confirmation-mode close_above --days 7 --pno-category-mode discovery --collect-diagnostics true
```

Checks:

```text
sparse_entry_materialization_status.csv exists for each PNO diagnostics directory.
data_load_status.csv marks source_timeframe, target_timeframe, load_mode and target_checked.
run_context.json records requested_entry_tf, source_entry_tf and entry_load_mode.
`sparse_entry_materialized_insufficient_bars` either disappears or points to a concrete cache/window/data availability problem, not hardcoded pre-roll truncation.
No edge/PnL conclusion until positions exist.
```

---

## E027 - Planned P081 prior-local-high human_bos removal audit

```text
Status: PLANNED
Patch: P081 proposed / compile verified locally / commit UNKNOWN
Date: 2026-05-06
Run baseline: 20260506_111822_pno 7-day multi-TF run from 2-2.zip
Positions baseline: 0 / 0 / 0
```

Goal:

```text
Measure what happens when `human_bos_below_prior_local_high` is removed without touching close_above, later-local-high obsolete rejection, RR, TP/SL or data-quality gates.
```

Command:

```bash
python main.py run-backtest --strategy pno --pno-all-tf-pairs --pno-deposit 10000 --pno-risk-pct 0.05 --plot-rejected true --pno-entry-confirmation-mode close_above --days 7 --pno-category-mode discovery --collect-diagnostics true
```

Checks:

```text
`human_bos_below_prior_local_high` should disappear from Stage5 reject reasons.
Count how many formerly blocked setups move to Stage5 valid attempts, later-local-high obsolete rejects, no_close_above, entry_invalidated_before_trigger or real positions.
If positions appear, do not judge edge yet; first inspect charts, data quality, RR, TP1 timing and whether results depend on a tiny number of cases.
```



---

## E026 - 20260506_104450_pno 7-day multi-TF artifact audit

```text
Status: ANALYZED
Patch state: P079 artifacts present; P080 proposed locally after analysis; commit UNKNOWN
Run: 2.zip / 20260506_104450_pno
Period: 7 days
Mode: discovery / close_above
TF pairs: 5m/30s, 5m/15s, 1m/5s
Positions: 0 / 0 / 0
```

Funnel:

```text
5m/30s: Stage1 25, Stage2 17, Stage3 7, Stage4 unique 4, Stage5 positions 0
5m/15s: Stage1 19, Stage2 14, Stage3 7, Stage4 unique 2, Stage5 positions 0
1m/5s:  Stage1 1,  Stage2 1,  Stage3 0, Stage4 unique 0, Stage5 positions 0
```

Data quality:

```text
diagnostics_coverage reports number_of_trades and quote_volume_usdt for levels/entry on all analyzed symbols.
For 5m/30s and 5m/15s, data_load_status correctly marks requested target entry TF as sparse_deferred and target_checked=false at preparation time.
However sparse_entry_materialization_status.csv and seconds_load_status.csv are empty despite Stage2 `sparse_entry_materialized_insufficient_bars` rejections containing materialization/load details.
Root cause: category diagnostics merge dropped sparse materialization context keys before CLI artifact export.
```

Interpretation:

```text
Run is not evidence of edge or no-edge; zero positions makes PnL meaningless.
The useful finding is artifact honesty: target-entry sparse materialization failures were not surfaced as first-class run-level tables.
Stage5 rejections are dominated by entry_invalidated_before_trigger, human_bos_obsolete_under_later_local_high and no_close_above; do not relax close_above based on this run.
```

Next:

```text
Apply/commit P080, rerun the same 7-day command, then inspect sparse_entry_materialization_status.csv before touching filters or entry logic.
```

---

## E027 - Manual IO/ZEC candle check around user-marked PNO times

```text
Status: ANALYZED
Patch state: no code patch; commit UNKNOWN
Times: Belgrade UTC+2
IO: 2026-05-06 08:19 / 08:26 / 08:29 Belgrade
ZEC: 2026-05-05 22:06 / 22:17 / 22:30 Belgrade
```

Findings:

```text
ZEC targets are covered by 1m/5m cache. Current discovery/close_above PNO produces zero positions and rejects the later 21:00-21:03 UTC pump candidate at Stage1 as pump_candidate_pretrend_too_weak with flow_hold_bar_count=0. The marked 20:06/20:17/20:30 UTC candles occur during pump expansion before the later 21:03 UTC high, not after a completed active-high pullback.
IO 1m/5m and sparse 5s PNO inputs end at 2026-05-06 06:14 UTC, before the marked 06:19/06:26/06:29 UTC candles. Auxiliary 1s cache extends through the window and shows continued impulse from 06:19 toward later highs, but this was not available to the official run input. An in-memory sanity check using aggregated 1s continuation still generated zero PNO positions.
```

Interpretation:

```text
Do not count these marked candles as confirmed PNO wins. They are mostly impulse-continuation candles before the future active high; treating the later high as TP1 for those entries would risk lookahead/leakage unless the setup definition explicitly allows pre-active-high continuation entries.
```

---

## E028 - Targeted IO/ZEC all-TF PNO rerun

```text
Status: ANALYZED
Patch state: no code patch; commit UNKNOWN
Output: .output/manual_checks/pno_manual_20260506_io_zec_cache_only
TF sets: 5m/30s, 5m/15s, 1m/5s
Mode: discovery / close_above
```

Result:

```text
All six targeted runs produced 0 positions and 0 Stage events.
IO 5m/30s and 5m/15s: Stage1 reject at 2026-05-06 08:00 UTC, pump_candidate_pretrend_too_weak, pump_pct=0.653344, flow_hold_bar_count=0.
IO 1m/5s: Stage1 rejects at 07:01, 07:33, 07:34, 07:45 UTC: below_ema20 / jerky.
ZEC 5m/30s and 5m/15s: Stage1 reject at 2026-05-05 21:00 UTC, pump_candidate_pretrend_too_weak, pump_pct=0.306750, flow_hold_bar_count=0.
ZEC 1m/5s: Stage1 reject at 2026-05-05 21:03 UTC, pump_candidate_pretrend_too_weak, pump_pct=0.307478, flow_hold_bar_count=0.
Data quality used real number_of_trades and quote_volume_usdt for levels and entry source frames.
```

Interpretation:

```text
The marked candles can lead to higher future highs on raw candles, but current PNO does not recognize them as valid PNO setups on any supported TF set. They remain impulse/continuation observations, not executable PNO entries under the current spec.
```

---

## E029 - IO/ZEC filter table and anomaly trade-count outcome scan

```text
Status: ANALYZED
Patch state: no code patch; commit UNKNOWN
Output: .output/manual_checks/pno_manual_20260506_io_zec_cache_only
Files: manual_filter_table.csv, anomaly_trade_count_outcome_scan.csv
```

Manual filter table:

```text
IO and ZEC pass start quote-volume ratio, start trade-count ratio, and pump_pct on the rejected 5m Stage1 candidates.
The blocking filter for 5m/30s and 5m/15s is flow-hold: observed next-candle hold count is 0 vs required 2.
IO 1m/5s is blocked by below_ema20 and/or jerky path efficiency: path_efficiency 0.1732 vs required 0.18 on the later candidates.
ZEC 1m/5s is also blocked by flow-hold: observed 0 vs required 10.
```

Exploratory scan:

```text
Definition: 1m anomaly start = quote_volume and number_of_trades >= 10x previous-60-candle rolling median. Hold count is next 4 candles, not minutes.
31-day scan: 92,493 anomaly starts across 537 symbols.
big_25p group: 94 events, median number_of_trades 2,877, median trade_ratio 14.83, median next4-candle hold count 3.
fast_fade group: 2,191 events, median number_of_trades 1,839, median trade_ratio 14.16, median next4-candle hold count 1.
Interpretation: absolute trade-count is somewhat higher in 25%+ moves, but the cleaner separator is persistence of activity across following candles. Start trade-count spike alone is not enough.
```

---

## E030 - Anomaly continuation lab IO/ZEC smoke artifact

```text
Status: COMPLETED
Patch state: P082 applied locally; commit UNKNOWN
Output: .output/manual_checks/anomaly_continuation_lab_io_zec
Command: .venv\Scripts\python.exe -m research_tools.anomaly_continuation_lab --cache-dir .output/cache --output-dir .output/manual_checks/anomaly_continuation_lab_io_zec --days 31 --confirmation-candles 4 --symbols IO/USDT:USDT ZEC/USDT:USDT
```

Result:

```text
The smoke artifact wrote 324 anomaly rows for IO/ZEC.
Schema includes decision_timestamp_utc, next-N-candle hold/decay, price retention, future outcome labels, and start verticality metrics.
Verticality columns: start_verticality_score, start_verticality_path_efficiency, start_verticality_range_efficiency, start_verticality_slope_pct_per_candle, start_verticality_max_retrace_fraction, start_verticality_green_share.
```

Next:

```text
Run the same tool across all symbols and analyze whether high verticality plus next-candle tape persistence improves separation between big_25p and fast_fade groups.
```

---

## E031 - Recent runner-date anomaly lab review

```text
Status: COMPLETED
Patch state: P083 applied locally; commit UNKNOWN
Targets: LAB 2026-05-01, TON 2026-05-04, ZEC 2026-05-05, PLAY 2026-05-04/2026-05-06, IO 2026-05-06, JTO 2026-05-06, NEAR 2026-05-06, DASH 2026-05-04
Outputs:
- .output/manual_checks/anomaly_continuation_recent_runners
- .output/manual_checks/anomaly_continuation_recent_runners_relaxed_4h
```

Strict result:

```text
Strict grid: start quote/trade ratio >= 10x, confirmation 4 candles, future high window 60 candles.
Only IO 2026-05-06 was labelled big_25p among the requested target dates.
IO anomaly local 08:00, decision local 08:04, decision_close 0.13110, hold_count_next_4=2, verticality=0.8214, future high after decision +43.55%.
NEAR had no strict candidates on 2026-05-06.
```

Relaxed sensitivity result:

```text
Relaxed grid: start quote/trade ratio >= 5x, confirmation 4 candles, future high window 240 candles.
Best retrospective candidates by target date:
LAB 2026-05-01: anomaly 21:53, decision 21:57, +49.35%, hold=3, verticality=0.4917.
ZEC 2026-05-05: anomaly 21:17, decision 21:21, +28.18%, hold=0, verticality=0.6076.
PLAY 2026-05-04: anomaly 22:01, decision 22:05, +30.38%, hold=0, verticality=0.2697.
PLAY 2026-05-06: anomaly 15:23, decision 15:27, +46.38%, hold=4, verticality=0.4399.
IO 2026-05-06: anomaly 08:00, decision 08:04, +64.07%, hold=2, verticality=0.8214.
TON, JTO, NEAR and DASH had candidates but did not reach +25% in this post-decision 240-candle window under the current artifact labels.
```

Interpretation:

```text
The moment of entry in this lab is decision_timestamp_local after the fixed confirmation window, not the anomaly candle itself.
Strict 10x thresholds may be too narrow for several known runner dates; relaxed 5x catches more runners but will also add more faders, so rules must be tested on all-symbol distributions before using this as an edge.
```

---

## E032 - Anomaly strategy profitability smoke run on recent runner symbols

```text
Status: COMPLETED
Patch state: P084 applied locally; commit UNKNOWN
Output: .output/manual_checks/anomaly_strategy_recent_runners
Command: .venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_strategy_recent_runners --days 31 --confirmation-candles 4 --forward-high-candles 240 --forward-low-candles 60 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols LAB/USDT:USDT TON/USDT:USDT ZEC/USDT:USDT PLAY/USDT:USDT IO/USDT:USDT JTO/USDT:USDT NEAR/USDT:USDT DASH/USDT:USDT
```

Result:

```text
Closed trades: 409 across 8 symbols.
Win rate: 50.61%.
Average net return per trade after fees: -0.0405%.
Median net return: +0.0174%.
TP1 hit rate: 49.88%.
Exit reasons: 195 stop_loss, 204 trailing_stop, 10 time_exit.
By symbol sum_net_return: IO +0.1737, PLAY +0.1389, DASH +0.0643, JTO -0.0195, ZEC -0.0698, TON -0.0741, NEAR -0.1512, LAB -0.2279.
```

Interpretation:

```text
The first default entry/stop/trail rule is not evidence of stable edge. It is roughly breakeven/slightly negative on the selected runner symbols after fees, despite several large MFE cases.
This is useful: the lab now exposes realizable trade artifacts and shows that catching anomaly continuations requires additional filters or better exit logic, not just verticality + upper-range retention.
```

---

## E033 - Anomaly strategy OI artifact coverage check

```text
Status: COMPLETED
Patch state: P085 applied locally; commit UNKNOWN
Output: .output/manual_checks/anomaly_strategy_recent_runners_oi_check
Command: .venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_strategy_recent_runners_oi_check --days 31 --confirmation-candles 4 --forward-high-candles 240 --forward-low-candles 60 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols LAB/USDT:USDT TON/USDT:USDT ZEC/USDT:USDT PLAY/USDT:USDT IO/USDT:USDT JTO/USDT:USDT NEAR/USDT:USDT DASH/USDT:USDT
```

Result:

```text
anomaly_candidates.csv: 2224 rows with OI columns.
anomaly_signals.csv: 416 rows with OI columns.
anomaly_trades.csv: 416 rows with OI columns.
oi_context_status.csv: oi_timeframe=5m, oi_status=missing_column, rows=2224, symbols=8.
All OI values and OI deltas are NaN because the current selected 5m cache lacks open_interest.
```

Interpretation:

```text
The artifacts are honest for OI coverage: no synthetic 1m OI, no zero fill, no hidden fallback.
This run cannot answer whether OI dynamics separate runners from faders until 5m open_interest exists in cache.
```

---

## E034 - 7-day all-symbol anomaly lab profitability/OI run

```text
Status: ANALYZED
Patch state: local anomaly lab branch with P085-P087; commit UNKNOWN
Output: .output/results/anomaly_lab
Command: .venv\Scripts\python.exe main.py run-anomaly-lab --days 7 --confirmation-candles 4 --forward-high-candles 240 --forward-low-candles 60 --min-quote-ratio-start 5 --min-trade-ratio-start 5
```

Result:

```text
Candidates: 43,232.
Signals: 9,294.
Closed trades: 8,962 across 537 symbols.
Win rate: 46.14%.
Average net return: -0.0854% per trade.
Median net return: -0.1360%.
Total simple sum net return: -7.653.
TP1 hit rate: 47.70%.
Exit reasons: 4,448 stop_loss, 4,265 trailing_stop, 249 time_exit.
Candidate outcomes: 94 big_25p, 956 fast_fade, 42,182 other.
```

OI coverage:

```text
Candidates with OI ok: 41,990 / 43,232.
Signals with OI ok: 9,010 / 9,294.
Closed trades with OI ok: 8,691 / 8,962.
Other OI statuses: missing_column, no_oi_before_decision, stale_asof.
```

Interpretation:

```text
The default anomaly continuation rule is not profitable on the 7-day all-symbol run.
OI is now sufficiently covered for exploratory segmentation.
Positive OI expansion is the most interesting observed layer, but the sample is still small after filtering: oi_change_pct_3x5m > 3% has 64 closed trades, win rate 60.9%, avg net +0.77%; combining it with hold_count >= 2 has 36 trades, win rate 66.7%, avg net +1.29%.
This is a hypothesis for the next test, not a stable edge conclusion.
```

---

## E035 - OI-filtered anomaly win/loss and entry-structure analysis

```text
Status: ANALYZED
Patch state: local anomaly lab branch; commit UNKNOWN
Output: .output/results/anomaly_lab
Basis: E034 7-day all-symbol run
```

Findings:

```text
All closed trades: 8,962, win rate 46.1%, avg net -0.085%.
The strongest non-future filter found in this run is oi_change_pct_3x5m > 3% plus hold_count_next_n_candles >= 2: 36 trades, 34 symbols, 6 days, win rate 66.7%, avg net +1.29%.
Daily results for this filter: 2026-04-30 +0.013, 2026-05-01 +0.165, 2026-05-03 +0.202, 2026-05-04 -0.032, 2026-05-05 -0.024, 2026-05-06 +0.143.
Losses inside this filter are not explained by weak OI. They often have larger start_quote_ratio/start_trade_ratio and stronger next-candle decay/hold, suggesting overly crowded or exhaustion-like pumps.
```

Entry analysis:

```text
On the rule-only oi3>3% + hold>=2 subset, market-at-decision produced 36/36 entries, 66.7% win rate, avg net +1.29%.
Structural pullback to box_low + 0.75 * box_range produced 35/36 entries, 68.6% win rate, avg net +1.70%.
Pullback to box_low + 0.85 * box_range produced 35/36 entries, 62.9% win rate, avg net +1.62%.
Breakout above decision box high produced 32/36 entries, 65.6% win rate, avg net +1.24%.
For post-facto big_25p runner trades, breakout above box high improved runner capture, but that is runner analysis, not a standalone entry rule.
```

Interpretation:

```text
The best current research direction is not "enter on candle N".
It is: anomaly wake-up, real 5m OI expansion over the prior 15 minutes, tape persistence over confirmation candles, then structural pullback entry inside the known impulse/confirmation box.
The edge is not proven until tested on longer periods and on a pre-declared filter grid.
```

---

## E036 - Anomaly flow/effort metric smoke check

```text
Status: COMPLETED
Patch state: P089 applied locally; commit UNKNOWN
Output: .output/manual_checks/anomaly_metrics_smoke
Command: .venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_metrics_smoke --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols IO/USDT:USDT ZEC/USDT:USDT --run-entry-grid true --grid-oi3-values 0.01 --grid-hold-values 1 --grid-pullback-fractions 0.75
```

Result:

```text
anomaly_candidates.csv and anomaly_trades.csv include the new taker buy, avg trade size, effort-vs-result, range expansion and OI x price interaction columns.
This is a schema/availability smoke check only; no edge conclusion is made from this small run.
```

---

## E037 - Anomaly honesty/performance smoke check after P090

```text
Status: COMPLETED
Patch state: P090 applied locally; commit UNKNOWN
Output: .output/manual_checks/anomaly_honesty_speed_smoke
Command: .venv\Scripts\python.exe main.py run-anomaly-lab --output-dir .output/manual_checks/anomaly_honesty_speed_smoke --days 1 --confirmation-candles 4 --forward-high-candles 60 --forward-low-candles 30 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --symbols IO/USDT:USDT ZEC/USDT:USDT --run-entry-grid true --grid-oi3-values 0.01,0.03 --grid-hold-values 1,2 --grid-pullback-fractions 0.75
```

Result:

```text
Smoke run completed.
anomaly_signals.csv includes decision_box_low/high/range and initial_risk_pct_at_decision.
The prior initial_risk_pct_proxy field is removed from the signal artifact.
```

---

## E038 - 31-day anomaly entry-grid edge audit

```text
Status: ANALYZED
Patch state: P090/P091 local branch; commit UNKNOWN
Output: .output/results/anomaly_lab_31d_entry_grid
Command basis: run-anomaly-lab --days 31 --confirmation-candles 4 --forward-high-candles 240 --forward-low-candles 60 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --run-entry-grid true --grid-oi3-values 0,0.01,0.02,0.03,0.05 --grid-hold-values 1,2,3 --grid-pullback-fractions 0.65,0.75,0.85
```

Coverage:

```text
Candidates: 201,451.
Signals: 41,693.
Closed default market trades: 40,123.
OI candidate coverage: ok 184,956; no_oi_before_decision 5,580; missing_column 3,900; stale_asof 56.
OI ok spans 2026-04-07 07:32 UTC through 2026-05-06 13:29 UTC.
```

Default result:

```text
Default market rule remains negative: 40,123 closed trades, win rate 47.41%, avg net -0.0681%, median -0.1103%, sum net -27.325.
```

Entry-grid result:

```text
Best average row in the grid: pullback_box_fraction=0.85, hold>=2, oi_change_pct_3x5m>5%, 60 closed trades, 50 symbols, 23 days, win rate 51.67%, avg net +0.871%, median +1.505%.
Best broader n>=100 row: pullback_box_fraction=0.85, hold>=2, oi_change_pct_3x5m>3%, 158 closed trades, 130 symbols, 29 days, win rate 50.63%, avg net +0.398%, median +0.094%.
The 0.03/hold>=2 family is positive but modest after widening OI coverage across the full month.
The 0.01 and 0.02 variants mostly collapse toward breakeven/slightly negative.
```

Variant resimulation:

```text
strict_oi3_hold2_pb075: 155 closed trades, 124 symbols, 29 days, win rate 51.61%, avg net +0.261%, median +0.154%, sum +0.404.
broader_oi1_hold2_pb075: 593 closed trades, 300 symbols, 30 days, win rate 49.75%, avg net +0.033%, median -0.049%, sum +0.197.
balanced_oi2_hold2_pb075: 262 closed trades, 183 symbols, 30 days, win rate 51.15%, avg net +0.056%, median +0.073%, sum +0.146.
strict_oi3_hold1_pb075: 201 closed trades, 151 symbols, 30 days, win rate 51.74%, avg net +0.214%, median +0.111%, sum +0.430.
```

Risk assessment:

```text
The edge candidate is not stable enough yet.
There is positive signal in strong OI expansion, but it depends on a small tail of high-OI events and has weak monthly robustness.
Daily distribution for strict_oi3_hold2_pb075 includes long red stretches such as 2026-04-17, 2026-04-20, 2026-04-22 and 2026-04-23.
Top-trade dependence is material: top 10 trades sum +1.198 while total is only +0.404 for strict_oi3_hold2_pb075.
Losses are associated with higher start_quote_ratio/start_trade_ratio, larger start_avg_trade_quote_size_ratio and worse MAE, supporting the crowded/exhaustion interpretation.
```

Conclusion:

```text
Do not promote this to a deployable strategy.
Keep OI expansion as a useful feature, but the next research step should add anti-exhaustion filters and regime/day robustness checks before optimizing exits.
```

---

## E039 - Planned 30-day anomaly anti-exhaustion grid

```text
Status: PLANNED
Patch state: P092 applied locally; commit UNKNOWN
Output target: .output/results/anomaly_lab_30d_exhaustion_grid
```

Goal:

```text
Test whether mild/balanced anti-exhaustion filters improve OI-expansion anomaly continuation without reducing frequency below roughly 15-30 trades/month.
```

Command:

```bash
python main.py run-anomaly-lab --output-dir .output/results/anomaly_lab_30d_exhaustion_grid --days 30 --confirmation-candles 4 --forward-high-candles 240 --forward-low-candles 60 --min-quote-ratio-start 5 --min-trade-ratio-start 5 --run-entry-grid true --grid-oi3-values 0.03,0.05 --grid-hold-values 1,2,3 --grid-pullback-fractions 0.65,0.75,0.85 --grid-exhaustion-profiles none,mild,balanced
```

Do not evaluate:

```text
Single best row only. Compare trade count, days, symbols, median, daily distribution and top-trade dependence.
```

---

## E040 - 30-day anomaly anti-exhaustion grid analysis

```text
Status: ANALYZED
Date: 2026-05-07
Patch state: P092 committed on branch codex/pno-anomaly-continuation-lab
Output: .output/results/anomaly_lab_30d_exhaustion_grid
```

Result:

```text
Default market anomaly rule remains negative: 38,703 closed trades, win rate 47.37%, avg net -0.0673%, median -0.1104%.
The useful signal appears only after combining strong 5m OI expansion, confirmation hold and anti-exhaustion caps.
Best current row: market entry, balanced exhaustion profile, hold>=2, oi_change_pct_3x5m>5%; 31 closed trades, 27 symbols, 18 active days, win rate 61.29%, avg net +2.6085%, median +2.5775%, sum +0.8086.
Mild profile with the same OI/hold gives 43 trades, 38 symbols, 21 days, win rate 60.47%, avg net +1.6155%.
No exhaustion profile with the same OI/hold gives 66 trades, 55 symbols, 25 days, win rate 51.52%, avg net +0.5887%.
```

Metric interpretation:

```text
The pattern is not "more volume/trades is better".
Healthy candidates have strong but controlled start quote/trade ratios, moderate avg trade size expansion, lower effort-per-return, controlled range expansion, nonzero OI expansion and enough post-start hold.
Very high start_quote_ratio, start_trade_ratio, avg_trade_size_ratio, quote_ratio_per_abs_return or range expansion behaves like exhaustion/crowded late entry and increases fast-fade risk.
Price retention too close to 1.0 is also suspicious; it can mean no healthy pullback after vertical expansion.
Taker buy share helps only as a floor; high taker share alone does not separate runners.
```

Limitations:

```text
The best row meets the desired 15-30 trades/month area, but top-trade dependence remains material: top5 sum +0.6996 vs total +0.8086; top10 sum +0.9868 while bottom10 drag -0.4210.
The run covers only one recent 30-day regime, so this is a promising research candidate, not a deployable strategy.
```

Conclusion:

```text
Keep the balanced anti-exhaustion profile as the current primary candidate and mild as the frequency-preserving alternative.
Do not add more tightening yet; next evidence must come from out-of-window/month split and top-trade dependency, not another hand-picked metric threshold.
```

---

## E041 - Anomaly leakage, exit and missing exchange metrics review

```text
Status: ANALYZED
Date: 2026-05-07
Patch state: no trading-code patch; commit UNKNOWN before this note
Basis: current code plus .output/results/anomaly_lab_30d_exhaustion_grid
```

Leakage / overfit:

```text
Current signal construction uses decision-time fields only: confirmation-window retention/hold/verticality/flow, decision box, as-of 5m OI and anti-exhaustion caps.
Future fields such as future_high/future_low/outcome_label/MFE/MAE are exported for analysis and summary, but are not used by build_anomaly_signals.
The bigger risk is overfitting/multiple testing: balanced thresholds were selected after inspecting one 30-day window, and the best row is still top-trade dependent.
```

Exit review:

```text
Small exit-grid on the current best signal set tested TP1/trail variants without changing entries.
Current exit: TP1 1R on 50%, BE after TP1, 5-candle swing-low trail with 0.10R buffer: 31 trades, avg +2.61%, median +2.58%, sum +0.809.
Best tested average: TP1 1R on 25%, same trail: avg +2.98%, median +1.89%, sum +0.922, top5_share 0.879.
Higher TP targets or looser runner trails increase top-trade dependence and often reduce median/winrate, so they are not cleaner improvements.
```

Missing easy exchange metrics:

```text
Most useful next historical additions: funding rate / premium-index basis, mark-vs-index basis, global/top long-short ratios, taker long-short ratio, and spot-vs-perp volume/return divergence.
Orderbook imbalance/spread and liquidation/force-order flow may help, but reliable historical coverage is harder unless collected live or via exchange-limited endpoints.
```

Follow-up patch:

```text
P093 implements the first honest artifact layer for these metrics.
The current smoke run on IO/ZEC has market_context_status.csv with missing_frame for all new sources, which is expected because those context caches have not been fetched yet.
This proves absence is visible rather than hidden behind zeros or last-price substitutes.
P094 adds default cache collection for these derivatives context files. A ZEC 1-day smoke fetch produced ok anomaly-lab coverage for premium/mark/long-short/taker context and mixed no_context_before_decision/ok for funding, matching 8h funding cadence.
```

---

## E042 - 30-day anomaly derivatives-context grid analysis

```text
Status: ANALYZED
Date: 2026-05-08
Patch state: P100 committed on branch codex/pno-anomaly-continuation-lab
Output: .output/results/anomaly_lab_30d_derivatives_context_grid
```

Result:

```text
Raw anomaly rule remains negative: 41,304 closed trades, win rate 46.88%, avg net -0.0747%, sum -30.84.
The useful candidate is still balanced anti-exhaustion + OI expansion, not raw anomaly continuation.
Best row: market entry, balanced profile, hold>=2, oi_change_pct_3x5m>5%; 31 trades, 27 symbols, 19 days, win rate 61.29%, avg net +2.36%, median +2.48%, sum +0.730.
Hold>=3 is similar: 29 trades, win rate 62.07%, avg +2.34%, same median, slightly less frequency.
Mild profile with the same OI/hold keeps more trades: 45 trades, 40 symbols, 22 days, win rate 57.78%, avg +1.32%, median +2.05%.
```

Data/context quality:

```text
Candidate window: 206,977 rows across 563 symbols, decision timestamps 2026-04-08T17:25Z..2026-05-08T12:21Z, error rows 0.
OI coverage is mostly ok: 206,125 ok rows; stale/missing/no-before are small and visible.
Derivatives context was fetched only for post-filter grid universe: 232 post-filter signals across 171 instruments; fetch status success for all 171.
Premium/mark/global/top/taker LS coverage is ok for those 232 rows; funding has lower coverage because of 8h cadence and missing source files for some instruments.
Raw candidates outside the context universe have explicit not_in_context_universe status, which is expected.
```

Robustness:

```text
The best row is promising but not yet robust enough to deploy.
Top-tail dependence remains high: for the 31-trade best row, top1 contributes +0.190, top3 +0.472, top5 +0.700 vs total +0.730; top10 exceeds total because losers offset it.
Daily split: 12 positive days, 7 negative days, 19 active days. The largest single day is +0.190 and the worst is -0.126.
The edge is broad by symbols but still sensitive to a few winners.
```

Interpretation:

```text
The repeated pattern is controlled wake-up, not maximum violence.
Better winners show higher verticality, lower effort-per-return, lower range expansion, moderate real trade/quote expansion, OI expansion above 5%, and enough hold.
Derivatives context is useful but not proven as a primary filter yet: selected sample is only 31 trades.
Within that sample, positive hints are mark/decision basis not deeply negative, taker_ls_buy_share above median, controlled top-position LS change, and stronger verticality; treat these as hypotheses only.
```

Next:

```text
Do not optimize another threshold on this same window first.
Run fixed out-of-window validation with the current candidate set: balanced market hold>=2 oi3>5 and mild market hold>=2 oi3>5 as the frequency-preserving alternative.
Best next code addition is a rolling-window validation command/report that runs fixed configs across multiple non-overlapping 30-day windows and reports monthly/day/top-tail stability.
```

Follow-up artifacts and session split:

```text
P101 generated best-grid artifacts for the same run:
anomaly_entry_grid_best_trades.csv, anomaly_entry_grid_best_config.csv, anomaly_edge_health.csv, anomaly_trade_chart_status.csv and 31 PNG charts under charts/best_grid_variant.
Base code commit before artifact patch: 37250044; patch commit is recorded in git history after commit/push.
P102 regenerated those 31 charts with the established PNO diagnostics chart style; the chart artifact quality is now suitable for visual candle review.
Edge health is mostly ok on frequency/winrate/avg/median/days/symbol breadth, but fails top5_dependency and worst_day_return.
Session split uses Europe/Belgrade local time: Asia 00-08, Europe 08-16, US 16-24.
Asia: 9 trades, win rate 44.44%, avg +2.88%, median -0.37%, sum +0.259, 4 positive / 4 negative days.
Europe: 13 trades, win rate 69.23%, avg +1.65%, median +2.88%, sum +0.214, 7 positive / 3 negative days.
US: 9 trades, win rate 66.67%, avg +2.86%, median +2.58%, sum +0.257, 4 positive / 3 negative days.
Interpretation: Europe/US are cleaner by winrate/median; Asia has upside but weaker median and lower winrate. Sample is too small to add a session filter yet.
```

CVX / EMA20 exit follow-up:

```text
CVX in the best row is a suspicious pass, not a clean wake-up: start_trade_count=47, start_taker_buy_quote_share=0.0047, start_close_position_in_range=0.0, start_verticality_max_retrace_fraction=1.33.
This supports adding quote/trade candle histograms and explicit zero-range sleep metrics before adding another hard filter.
P103 adds quote_volume / number_of_trades chart panels, baseline_zero_range_share for future runs, and exit-rule grid support.
Same-window quick check for balanced market hold>=2 oi3>5:
structural_trail: 31 trades, WR 61.29%, avg +2.36%, median +2.48%, sum +0.730.
ema20_close: 31 trades, WR 48.39%, avg +2.31%, median -0.08%, sum +0.718.
ema20_negative_pnl_be_escape: 31 trades, WR 45.16%, avg +2.04%, median -0.08%, sum +0.632.
Interpretation: EMA20 exits may help individual tails, but on this fixed sample they hurt consistency. Keep them in grid for analysis, not as default.
```

Live validation planning:

```text
Date: 2026-05-09
Status: UPDATED
Doc: research/LIVE_VALIDATION_PLAN.md
Decision context: no full-year derivatives context is available for free; waiting a year or buying data is out of scope.
Plan direction: build a micro-live supervisor with scan/watch/position states, priority symbol queue, Telegram events, honest context statuses, category separation and artifacts.
User decision: no paper-first requirement; micro-live is allowed from the start, but infrastructure must keep order/position management higher priority than Telegram/chart work.
Added requirements: OI fresh if latest value is within current time minus 5 minutes; one canonical live_positions.csv for open/closed positions; separate live run artifact folder; all sessions traded with session in Telegram; per-position worker; Telegram cooldowns; symbol cooldown after 2 stops in N hours; network-degraded state with quiet retries.
Follow-up decisions: max open positions = 3; REST-only in v1; use two Telegram bots, one for events and one for positions. Related Telegram updates must reply to the parent message, e.g. close/SL move replies to open-position message.
```

---

## E045 - Planned live category parity smoke

```text
Status: PLANNED
Patch: P113 proposed / compile verified locally / commit UNKNOWN
Date: 2026-05-09
```

Goal:

```text
Verify live selection now follows the fixed candidate set: balanced_market hold>=2 OI>5 first, mild_market hold>=2 OI>5 second only if balanced_market rejects.
```

Checks:

```text
Run run-anomaly-live with --max-cycles 1 on a tiny symbol list and inspect live_events.csv.
Expected: category_rejected rows include category_id/reason/decision_timestamp_ms; category_selected row identifies the traded category; Telegram open text includes category; stop updates edit the first stop message rather than sending a sequence of trail messages.
Do not infer edge from this smoke. It only validates execution-path parity and artifact truthfulness.
```

---

## E046 - Planned live invalid-metric smoke

```text
Status: PLANNED
Patch: P114 proposed / compile + synthetic smoke verified locally / commit UNKNOWN
Date: 2026-05-09
```

Goal:

```text
Verify that P113 live category parity cannot be bypassed by NaN or invalid required metrics.
```

Checks:

```text
Run run-anomaly-live on a tiny symbol list and inspect live_events.csv.
Expected invalid data behavior: missing/stale/invalid OI becomes reject_oi with oi_status; invalid taker-buy confirmation rows become reject_invalid_taker_buy_share; invalid ratio/retention/verticality metrics become explicit category_rejected reasons.
No setup with non-finite required metrics should reach category_selected or position_opened.
```

---

## E047 - Planned live guardrail smoke

```text
Status: PLANNED
Patch: P115 proposed / compile + synthetic smoke verified locally / commit UNKNOWN
Date: 2026-05-09
```

Goal:

```text
Verify that live cannot place orders or emit malformed artifacts when non-signal guardrails return invalid data.
```

Checks:

```text
Run run-anomaly-live with a tiny symbol set in a controlled environment and inspect live_events.csv.
Expected: invalid config fails startup; invalid free balance writes reject_invalid_free_balance and places no orders; missing OHLCV price/flow columns write explicit reject events; details_json parses as strict JSON with no NaN literals.
```
