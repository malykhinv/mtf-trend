# PNO Strategy Spec

Краткая спецификация PNO. Держать компактной. Менять только если меняется смысл стратегии или торговая логика.

---

## 1. Суть

PNO — лонговая momentum-continuation стратегия после пампа.

```text
real pump → active high → controlled pullback → BOS/reclaim → close_above → next-bar entry → TP1 active high → runner
```

---

## 15. Baseline fallback rule

```text
Baseline PNO must not construct a trading level from fallback highs when confirmed/shelf highs are absent.
No confirmed or shelf level means no Stage4 setup.
ideal_like category profiles must not ignore close_above/decay invalidation inside the baseline comparison.
category_3 baseline must not enable ideal_like fallback level construction.
Relaxed or ideal-like level behavior is allowed only as an explicitly named experiment/profile with separate diagnostics.
```

Стратегия не торгует:

```text
любой откат
wick-touch
ловлю ножа
простой “памп был — покупаю”
```

---

## 2. Stage pipeline

```text
Stage1: Pump
Stage2: High Pullback
Stage3: Valid Pullback
Stage4: Level / BOS Setup
Stage5: Position
```

---

## 3. Хороший памп

Памп должен выглядеть как настоящее пробуждение рынка:

```text
быстрое направленное движение
рост объёма
рост real trade-count
достаточный real quote_volume в USDT
нормальные тела свечей
не один верхний фитиль
не frozen/zero-body tape
не тонкая ликвидность
```

Если real trade-count или quote_volume USDT отсутствуют, PNO не должен заменять их volume/close*volume proxy; символ должен явно отклоняться по data quality.

---

## 4. Active high

`active_high` — главный high пампа.

Используется как:

```text
верхняя граница setup
TP1
проверка, не опоздал ли entry
```

Если TP1/active high достигнут до executable entry, позиция часто invalid.

---

## 5. Здоровый откат

Откат должен:

```text
снять перегрев
не уничтожить памп
не пробить критические structural floors
сохранить читаемую структуру
дать lower-high / BOS context
не стать хаотичной пилой
```

Плохой откат:

```text
слишком мелкий → плохой RR
слишком глубокий → pump thesis сломан
слишком долгий → stale setup
слишком много upper wicks → продавец силён
```

---

## 6. Stage4 level / BOS

Рабочий уровень — это место, выше которого откат считается сломанным вверх.

Источники:

```text
structure_high
BOS
local high cluster
reclaim level
```

Хороший level:

```text
свежий
не затёртый
не слишком близко к active_high
даёт нормальный RR до TP1
связан с реальной структурой отката
не является нижним устаревшим BOS под более свежими local highs
не отбрасывается только из-за более раннего local high перед выбранным BOS
```

Stage4 rows не равны independent setups. Один и тот же level может повторяться несколько entry bars.


После P081 `human_bos_below_prior_local_high` не является торговым reject-фильтром: более ранний local high перед выбранным BOS сам по себе не отменяет setup. Более свежий local high после выбранного BOS всё ещё может сделать уровень устаревшим через `human_bos_obsolete_under_later_local_high`.

`human_bos` не является bypass-режимом. Он должен проходить те же проверки свежести уровня, overhead/untested-high context, close_above decay и close-trigger quality, что и обычный reclaim level. Его Stage4 score/validity до пересчёта считается provisional, а не принятым setup.

---

## 7. Entry mode

Primary mode:

```text
close_above
```

Сигнал:

```text
signal close > level
```

Недостаточно:

```text
signal high >= level
```

Почему:

```text
wick-touch = касание
close_above = временное принятие выше уровня
```

Не заменять `close_above` на wick-touch.  
Если нужен ранний вход — только отдельный эксперимент:

```text
touch + retest hold
```

---

## 8. Executable entry

В `close_above` вход обычно на следующей entry-TF свече.

Перед сделкой проверить:

```text
есть следующая свеча
entry ниже active_high
entry ниже TP1
TP1 не был достигнут signal candle
actual_entry_pos не слишком высоко
net RR до TP1 достаточный
stop ниже entry
```

---

## 9. Stop-loss

SL структурный, не произвольный процент.

Источники:

```text
ниже structure_low
ниже last red candle low
ниже pullback_low
+ небольшой noise buffer
```

SL стоит там, где идея “откат закончился” становится ложной.

---

## 10. TP / BE / Runner

TP1:

```text
active_high
```

BE:

```text
после ~70–80% пути до TP1 можно подтягивать SL к BE/BE+
```

Runner:

```text
вести по trailing / EMA / structure
```

Runner не должен оправдывать плохой RR до TP1.

---

## 11. Stage5 reject reasons

Stage5 должен по возможности логировать точную причину:

```text
no_close_above
actual_entry_pos_too_high
entry_price_above_active_high
entry_price_above_tp1
net_rr_too_low
no_next_entry_bar
close_trigger_filter_failed
entry_invalidated_before_trigger
tp1_already_tagged_in_signal
stop_not_below_entry
position_size_non_positive
```

Общая причина `level_crossed_no_trade` недостаточна для исследования.

---

## 12. Хороший PNO setup

```text
реальный памп
здоровый откат
понятный active_high
читаемая структура отката
свежий BOS/level
есть close_above
entry исполним
TP1 ещё не достигнут
entry_pos не слишком высокий
RR до TP1 достаточный
SL структурный
data quality понятна
```

---

## 13. Плохой PNO setup

```text
памп одним фитилём
нет real trade-count
откат уничтожил импульс
level слишком близко к active_high
только wick-touch
entry выше active_high/TP1
RR плохой
setup stale
много upper wicks
тонкая ликвидность
```

---

## 14. Исследовательский порядок улучшений

```text
1. диагностика
2. качество данных
3. entry TF comparison
4. устойчивость по периодам
5. изменение фильтров
6. оптимизация параметров
```

Не менять торговую логику, пока неизвестно, почему текущая логика отказывает.
