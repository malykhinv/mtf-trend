# C06 - Session Short Red Flags Status 2026-03-30

Статус: `Разведка`

Идея:
- взять красные флаги неудачных long-пампов вне Азии;
- проверить, усиливают ли они short-логику в Европе и Америке;
- искать не "шорт любой аномалии", а short после признаков слабости и перегрева.

Что проверяли:
- сессии:
  - Европа `08:00-15:59 UTC`
  - Америка `16:00-23:59 UTC`
- минуты:
  - все `5m` минуты
  - отдельные exact-минуты
  - группы `quarter_hours`, `half_hours`, `top_of_hour_only`, `non_quarter_hours`
- red-flag контексты:
  - `ctx_warm`
  - `ctx_hot_drift`
  - `ctx_hot_range`
  - `ctx_hot_combo`
  - `ctx_very_hot`

Красные флаги, которые переносили в short:
- высокий `pre_base_range_pct_60m`
- высокий `pre_base_drift_pct_60m`
- вне Азии особенно важен перегрев перед аномалией
- для Америки отдельно проверяли и "слишком горячий" объём через `sig_hotvol30/50`

Что получилось по Америке:
- лучший широкий short-кандидат всё ещё без отдельного context-фильтра:
  - `all_5m_minutes`
  - `sig_weak | tp_none | np_mid | ctx_none | limit_top05_s10_rr20`
  - `49.8` сделок/год
  - `mean trade = +1.03%`
  - `WR = 57.4%`
  - `annualized unit pnl = +51.4%`
  - `DD = 8.8%`
- лучший red-flag short-кандидат:
  - `quarter_hours`
  - `sig_weak | tp_none | np_soft | ctx_warm | open3_s05_rr15`
  - `16.6` сделок/год
  - `mean trade = +3.57%`
  - `WR = 83.3%`
  - `annualized unit pnl = +59.4%`
  - `DD = 6.3%`

Вывод по Америке:
- red flags действительно поднимают качество и winrate;
- но делают это ценой частоты;
- как широкий боевой слой они пока не победили baseline;
- как quality-overlay это сильный след для дальнейшего поиска.

Что получилось по Европе:
- лучший широкий short-кандидат:
  - `all_5m_minutes`
  - `sig_hotvol30 | tp_none | np_soft | ctx_none | limit_top05_s10_rr20`
  - `56.2` сделок/год
  - `mean trade = +0.75%`
  - `WR = 44.3%`
  - `annualized unit pnl = +42.4%`
  - `DD = 18.0%`
- лучший red-flag short-кандидат:
  - `all_5m_minutes`
  - `sig_hotvol30 | tp_none | np_soft | ctx_warm | limit_top05_s10_rr20`
  - `45.2` сделок/год
  - `mean trade = +0.81%`
  - `WR = 46.9%`
  - `annualized unit pnl = +36.9%`
  - `DD = 15.1%`

Вывод по Европе:
- red flags слегка улучшают качество и просадку;
- но не превращают широкий short в сильный edge;
- зато дают узкие niche-подмножества, особенно вокруг quarter-hours.

Главный честный вывод:
- long-red-flags действительно полезны для short;
- но они работают как sharpeners качества, а не как волшебный бустер широкой short-логики;
- Америка выглядит более перспективной для short, чем Европа;
- следующий шаг логично строить так:
  - Америка: широкий baseline short + аккуратный red-flag overlay
  - Европа: отдельный поиск узкого short-режима, а не попытка силой натянуть американскую модель.
