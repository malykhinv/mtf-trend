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

## 8. Шаблон нового патча

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

## 9. Правило обновления

Каждый patch должен обновлять:

```text
PATCH_LOG.md
RESEARCH_STATE.md, если меняется статус/вывод
STRATEGY_SPEC.md, если меняется логика стратегии
EXPERIMENT_LOG.md, если связан с run/experiment
```