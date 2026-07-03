# Strategy Report: pump_fade (short the fade)

> Scope: the SHORT side of the pump anomaly — a qualified pump is expected to
> fade (close back to base before making a new running high). This report
> summarizes outcomes; the registered protocol contracts live in the
> `docs/pump_fade_*_protocol.md` files and `research/pump_fade_*.json`.
> Core methodology invariants: `docs/research_methodology_core.md`.

Status:

```text
current_primary_research_strategy = true (short side)
live_trading_strategy            = false
verdict                          = real-but-weak causal P(fade) signal; NOT tradeable after costs
proven_edges                     = geometry + activity + recurrence
dead_ends                        = flow (untestable on free data), most engineered feature families
```

## 0. Определение аномалии (финальное, трейдерское)

Аномалия = ПЕРИОД пампа (1м–1ч), где: сделки ИЛИ объём ≥10× 24ч-базлайна,
+5% от базы, ATR ≥3×, ≥300k USDT оборот. База = low последней красной свечи
до ignition (держим, даже если пробита). Это заменило старый
per-minute broad-детектор. Полное определение: [[anomaly-definition]].

## 1. Что достигнуто (методология — монолит)

Причинный, leak-safe пайплайн: close-race лейбл (fade vs continuation),
недельный walk-forward (WFA) с event-exclusive сплитами, изоляцией
recurrence-цепочек, within-week label-permutation null-тестами, BY-FDR,
calendar-block shuffled-контролем. Канонический протокол:
[[archetype-discovery-protocol]] (`docs/pump_fade_archetype_protocol.md`).

## 2. Что узнали (результаты)

### 2.1. Слабый, но РЕАЛЬНЫЙ причинный сигнал P(fade)

На строго причинных данных: модель даёт **−0.17%/сделку @20bps**, что бьёт
blind (−0.40%) и shuffled (−1.04%) → сигнал реален, но слаб. Top-call
AUC ≈ 0.66. Валом gross ~break-even, издержки убивают.
Ранний «положительный» результат был РЕТРАКТИРОВАН (look-ahead leak:
full-period peak/size фичи + непричинный entry-gate). См. [[execution-ev-first-cut]].

### 2.2. Потолок инженерных фич-семейств

Все семейства (aggTrades, CVD, OI, market_context, perp_crowding,
event_memory, positioning, interaction_atlas, phenotypes, regimes) упёрлись
в AUC ~0.66, p 0.60–0.676. aggTrades отдельно ОТКЛОНЁН парным WFA
(ΔAUC ~0): [[aggtrades-feature-source]].

### 2.3. Что реально работает

**Geometry + activity + recurrence.** Структурные pump-geometry лейблы
(base/high/retrace) прошли; ATR-лейблы отклонены. Повторяемость: та же монета
повторяет пампы через 24–48ч; исходы прошлых РАЗРЕШЁННЫХ аномалий (fade/extend)
— фичи для тайминга (leak-правило: только исходы, закрытые до t0).
См. [[prior-anomaly-recurrence]], [[strategy-and-research-order]].

### 2.4. Флоу — непроверяемо на свободных данных

HARD-констрейнт: бесплатные исторические ликвидации Binance удалены (~2023),
OI только ~2.5 мес и слабый, taker без сигнала. Флоу-эдж непроверяем без
платного вендора. См. [[flow-data-availability]].

## 3. Вердикт и почему

Причинный P(fade)-сигнал РЕАЛЕН, но СЛАБ (AUC ~0.66) и не окупает издержки на
шорт-стороне из-за ограниченного payoff шорта. Это и мотивировало тест
лонг-стороны (`docs/strategies/pump_long.md`), где payoff выпуклый — но и там
робастной прибыли не нашлось.

## 4. Как воспроизвести

Регистрированные протоколы `research/pump_fade_*.json` через
`load_binary_weekly_walk_forward_config` + `run_binary_weekly_walk_forward`
(см. `COMMANDS.md`). Канонический датасет решений:
`.output/results/pump_fade_aggtrades_full/`.
