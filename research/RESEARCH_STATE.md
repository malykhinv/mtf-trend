# PNO Research State

Короткая рабочая память проекта. Держать компактной. Подробные правила работы — в Project Instructions.

---

## 1. Текущее состояние

```text
Branch: codex/ideal-like
Commit: 68c0f43bd1f9041a829f5e08809203f667af16af
Local diff: P008 proposed typing/client-boundary hardening; P032 proposed normalize source-level artifact logs
Last applied patch: P031 Finish source-level runtime logs
Last analyzed run: E001 5m/30s
Updated: 2026-05-02
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
50+ сделок в год
winrate > 0.40
средний трейд > +1.0%
помесячно преимущественно положительно
нет зависимости от 1–5 топ-сделок
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
6. Ноль сделок — не оценка прибыльности, а материал для funnel/reject анализа.
7. Сначала диагностика и качество данных, потом изменение фильтров.

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
| P017 | Fix P015 runner regression | APPLIED | Вернуть portfolio_trades assignment и восстановить аргументы long-symbol logger. |
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
| H3 | Flow-фильтры нельзя честно оценить без real trade-count. | Был volume proxy. | P004 + повтор того же run. |
| H4 | `human_bos` может обходить часть Stage4 scoring. | Нужно проверить текущий execution path. | Code review актуального `engine.py`. |
| H5 | Wick-touch ухудшит качество входов. | BZ/RUNE были wick-only без close_above. | Только отдельный `touch + retest hold` experiment. |

---

## 7. Последний известный run

```text
Run: 5m_30s
Levels TF: 5m
Entry TF: 30s
Period: 3 days
Symbols: ~508
Mode: discovery / close_above
Trades: 0
PnL: 0
PF: неинформативен при trades = 0
```

Funnel:

```text
Stage1 pump:            8
Stage2 high_pullback:   6
Stage3 valid_pullback:  3
Stage4 rows:            7
Stage4 unique expected: 4
Stage5 trades:          0
```

Интерпретация:

```text
run полезен для funnel/reject analysis, но не для оценки прибыльности.
BZ/RUNE: wick-only / no close_above.
ETH: вероятный entry TF latency case.
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
number_of_trades / trades / trade_count
quote_volume
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
P004 true trade-count → повторить тот же 5m/30s период → сравнить Stage1/reject distribution.
```

После этого:

```text
сравнить 5m/30s vs 5m/15s vs 1m/5s через --pno-all-tf-pairs.
```