# Strategy Report: pump_long (continuation riding)

> Scope: the LONG side of the pump anomaly — ride the continuation of a
> qualified pump with a structural trailing stop, instead of shorting the fade.
> Core methodology invariants live in `docs/research_methodology_core.md` and
> are not duplicated here. All hypothesis logic lives in
> `src/anomaly_science/strategy/pump_long/research/` (one universe builder, one
> Core simulator, one metrics module) — never in ad-hoc scripts.

Status:

```text
current_primary_research_strategy = false
live_trading_strategy            = false
verdict                          = no_robust_tradeable_edge (runner PREDICTION is real; PROFIT is not)
best_config                      = repeat-after-fade + below-prior + distributed-vol + recent<6h,
                                   base stop, stage-dependent structural exit (SWING_ATR=2.5, R_SWITCH=2.0),
                                   take-profit just below the nearest prior high
```

## 0. Гипотеза

Тот же слабый classifier, что не окупался в шорте (payoff шорта ограничен),
на ЛОНГ-стороне встречает выпуклый payoff продолжения (хвост +20–50%) и
структурный трейлинг. Тезис: это может быть EV-положительным без требования
p≥0.70. Лейбл симметричен (close-race): `continuation` = первый close строго
выше running-high раньше, чем close ≤ base; `fade` = наоборот.

## 1. Что достигнуто (механика — теперь монолит)

- **Стадийный выход** (лучший механизм) перенесён из tmp в ядро
  `simulation/numpy_path.py` как параметр `stage_switch_r`: пока пик хода
  < `stage_switch_r`·R — «рано», стоп только по ЗАКРЫТИЮ под структурой
  (толерантность к шуму); после ≥`stage_switch_r`·R — «поздно/сняли риск»,
  стоп по первому КАСАНИЮ. Покрыт юнит-тестами.
- **Вселенная и метрики** консолидированы в
  `strategy/pump_long/research/` (`context.py` — 48ч prior-fade контекст,
  overhead-цели near/far, кластеры уровней; `backtest.py` — единый прогон;
  `metrics.py` — EV, winrate, PF, `top_removed_to_negative`,
  `ev_excluding_best_month`, drawdown, positive-weeks; `hypotheses.py` —
  реестр гипотез как конфигов). Паритет со старыми tmp-числами подтверждён
  (медиана и winrate совпадают в точности).

## 2. Что узнали (результаты)

Метрика честности хвоста: `top2neg` = сколько % топ-сделок надо удалить, чтобы
EV≤0 (0% = уже отрицательно/несёт хвост). `posW` = доля положительных недель.

### 2.1. Runner-ness ПРЕДСКАЗУЕМА (реальный актив)

- OOS AUC ≈ 0.62 на причинных рыночно-логичных фичах, стабильно вне
  калибровки. НО прибыль была сконцентрирована в одной взрывной неделе (W41);
  вне неё преимущество не окупало издержки.

### 2.2. Широкая честная вселенная — робастно ОТРИЦАТЕЛЬНА

Universe: `≥1 faded prior 48h` + recent<6h + distributed-vol + has-overhead
(≈1855 событий, development):

| target | costs | EV | winrate | median | PF | top2neg | posW |
|--------|-------|-----|---------|--------|-----|---------|------|
| NEAR   | 20bps flat | −0.49% | 0.41 | −1.03% | 0.69 | 0.0% | 0.13 |
| FAR    | 20bps flat | −0.54% | 0.38 | −1.32% | 0.68 | 0.0% | 0.13 |
| NEAR   | honest 30bps | −0.59% | 0.40 | −1.13% | 0.65 | 0.0% | 0.10 |

Цель (near vs far) — не причина: обе отрицательны. Причина минуса —
асимметрия payoff: проигравшие проседают глубже (MAE ~0.78 vs 2.38 ATR у
победителей), издержки добивают ~break-even gross.

### 2.3. Lookback-окно 24–144ч — не спасает

Все окна (honest costs) дают −0.59…−0.60% EV, PF ~0.65, posW 0.10, $1000→~$0.
Ширина памяти не создаёт edge.

### 2.4. Мульти-тестированные уровни — держатся, но не торгуемы в плюс

Уровни, от которых уже фейдили, держатся ~90% (break% 9–10% независимо от
числа тестов). Торгуемо это ОТРИЦАТЕЛЬНО и УХУДШАЕТСЯ с числом тестов:
n_tests=1 −0.33%, n_tests=2 −0.59%, n_tests≥3 −0.74% (honest costs). Шорт
отбоя от уровня — ~break-even/шум, edge нет.

### 2.5. Строгая leak-safe FEATURE-вселенная — редкая удача, зафиксирована

Universe по фиче-колонкам лэттиса `n_prior_fades_48h≥1` (resolved-before-t0) +
below-prior + distributed-vol + recent<6h = **всего 40–64 события** →
**EV +5.89%**, winrate 56%, PF 1.73, $1000→$1405–2127 (риск 2–5%), лучшая
+129% (+4.5R), худшая −14.9%. НО `top2neg` всего 0.7–3.3% — весь плюс несёт
пара монстров. Это **малосэмпловый артефакт, а не робастный edge** — но он
ЗАФИКСИРОВАН здесь как редкая, реально случившаяся удача (сетап
«повторный памп после разрешённого фейда, ниже прошлого хая» встречается
редко, ~40 раз за 6 мес, и на нём статистически нельзя отличить edge от везения).

## 3. Итоговый вердикт и почему

**Runner-ПРЕДСКАЗАНИЕ реально (AUC 0.62), но в устойчивую ПРИБЫЛЬ после
издержек не конвертируется** на свободных 1m-данных. Каждый «плюс» оказывался
либо режимно-концентрированным (W41), либо малосэмпловым (строгие 40–64).
Широко измеренная стратегия отрицательна. Дисциплина (широкий пересчёт,
target A/B, `top2neg`, honest costs) поймала мираж на development, а не на
деньгах — это система, сработавшая как задумано.

## 4. Идеи усиления (прежде чем спрыгивать с гипотезы)

1. Модель P(cont) поверх САМОЙ строгой вселенной (не broad) + недельно-квантильный порог входа.
2. Ужесточение структуры цели: не любой overhead, а подтверждённый кластерный уровень.
3. Условие на «свежесть/качество» предыдущего фейда (как быстро/глубоко фейдил).
4. Комбинация с флоу-моделью (упирается в [flow-data-availability]: нужен платный вендор).

## 5. Как воспроизвести

```bash
PYTHONPATH=src python -m anomaly_science.strategy.pump_long.research.hypotheses
```
Регистрированные протоколы: `research/pump_long_*.json`. Датасеты:
`.output/results/pump_long_v1/state_lattice_runner.parquet` (решения+фичи+forward_mfe).
