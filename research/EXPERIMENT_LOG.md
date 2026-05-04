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
