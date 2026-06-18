# Strategy Spec: anomaly family

Status:

```text
active_research_strategy = true
live_trading_strategy = false
primary_variant = broad_anomaly_v1_h30
strategy_contract_version = base_strategy_v1
```

## 0. Назначение

Этот документ описывает стратегию семейства `anomaly`.

Аномалия — это не Core methodology, а один конкретный тип торговой гипотезы, подключаемый к универсальной research platform через BaseStrategy contract.

Core-инварианты не дублируются здесь. Они живут в:

```text
docs/research_methodology_core.md
```

Этот файл должен меняться только при изменении anomaly-specific гипотезы, triggers, features, horizons, reject reasons или strategy experiment notes.

## 1. Strategy identity

Базовая идентичность семейства:

```text
strategy_family = anomaly
strategy_contract_version = base_strategy_v1
```

Рекомендуемые конкретные strategy_name обязаны включать suffix горизонта `_h[minutes]`:

```text
broad_anomaly_v1_h15
broad_anomaly_v1_h30
broad_anomaly_v1_h60
post_anomaly_extension_v1_h60
post_anomaly_extension_v1_h120
post_pump_distribution_v1_h60
post_pump_distribution_v1_h120
post_pump_distribution_v1_h180
```

Правило:

```text
Нельзя смешивать разные strategy_name/strategy_version в одном model_version без явного split.
```


### 1.1. Implementation status and registry matrix

`Strategy Spec` может описывать больше базовых variants, чем уже реализовано в registry. Это не permission на silent fallback.

| Variant | Horizon | Registry status | Required streams | Simulation defaults |
| :--- | ---: | :--- | :--- | :--- |
| broad_anomaly_v1_h15 | 15 | implemented | OI optional, liquidations optional | TP 1.5 ATR / SL 1.0 ATR |
| broad_anomaly_v1_h30 | 30 | implemented, primary MVP variant | OI optional, liquidations optional | TP 2.0 ATR / SL 1.1 ATR |
| broad_anomaly_v1_h60 | 60 | implemented | OI optional, liquidations optional | TP 2.5 ATR / SL 1.2 ATR |
| post_anomaly_extension_v1_h60 | 60 | specified, not implemented until explicit patch | OI optional by default, liquidations optional by default | TP 2.5 ATR / SL 1.3 ATR |
| post_anomaly_extension_v1_h120 | 120 | specified, not implemented until explicit patch | OI optional by default, liquidations optional by default | TP 3.0 ATR / SL 1.5 ATR |
| post_pump_distribution_v1_h60 | 60 | specified, not implemented until explicit patch | OI required, liquidations required | TP 2.0 ATR / SL 1.2 ATR |
| post_pump_distribution_v1_h120 | 120 | specified, not implemented until explicit patch | OI required, liquidations required | TP 3.0 ATR / SL 1.5 ATR |
| post_pump_distribution_v1_h180 | 180 | specified, not implemented until explicit patch | OI required, liquidations required | TP 4.0 ATR / SL 2.0 ATR |

Правило:

```text
Если variant specified but not implemented, CLI/registry должен падать явной ошибкой.
Запрещено запускать specified-only variant через broad/default factory.
```

## 2. Что именно исследует anomaly strategy

Гипотеза:

```text
после резкой неординарной активности рынка будущая природа состояния может быть предсказуема online
из price path, volume, flow, OI, liquidation, CVD, market-relative и context features,
доступных на момент state_time.
```

Будущая природа для базовой anomaly model:

```text
long_continuation
short_fade
static_or_chop
unclear
```

Торговая цель не является первичной.
Сначала стратегия должна доказать:

```text
1. future nature предсказуема OOS
2. probabilities калиброваны
3. confidence появляется достаточно рано
4. EV положительный после basic costs и pessimistic execution
5. результат не держится на одном symbol/day/market shock
```

## 3. Trigger semantics

Anomaly strategy может иметь несколько trigger variants.
Каждый variant должен быть отдельным `strategy_name` или отдельным `strategy_version`.

### 3.1. broad_anomaly_v1_*

Цель:

```text
найти широкие моменты аномальной активности, не превращая trigger в trade setup.
```

Broad anomaly detector должен ловить:

```text
one-shot spike
fast burst
grind pump
volume-only anomaly
range expansion
breakout
pump inside noise
session activity burst
market-wide impulse
```

Запрещено:

```text
использовать future outcome в trigger
использовать final high/low события в trigger
делать detector настолько узким, что он уже кодирует будущий edge
использовать technical_noise_shock rows как trigger source
```

Минимальные trigger fields:

```text
event_id
symbol
event_start_time
event_detection_time / event_detection_time_ms at artifact boundary
seed_time / seed_time_ms at artifact boundary
seed_open
seed_high
seed_low
seed_close
initial_move_pct
initial_volume_zscore
initial_quote_volume_zscore
initial_trade_count_zscore
technical_noise_shock
raw_candle_gap_minutes
excluded_by_data_quality_gate
detector_version
```


### 3.1.1. Trigger frame semantics for anomaly variants

Anomaly Strategy возвращает trigger frame в формате BaseStrategy contract. Для broad MVP один row соответствует моменту detection события; последующий поминутный lifecycle строится Core в `anomaly_state_1m.csv`.

Минимальные contract columns:

```text
symbol
state_time
is_trigger
event_id
event_start_time
```

Внутренний тип времени:

```text
state_time, event_start_time, event_detection_time и seed_time имеют тип pl.Datetime[ms].
Unix timestamp Int64 допускается только при внешнем import/export, не внутри BaseStrategy contract.
```

Anomaly-specific lifecycle mapping:

```text
state_time = event_detection_time для первичного detection row
event_start_time = физический старт импульса/полки
minutes_since_start = floor((state_time - event_start_time) in minutes)
seed_time >= event_start_time
```

Запрещено:

```text
подменять event_start_time временем будущего high/low
пересоздавать event_id недетерминированно между повторными runs на одинаковых данных
смешивать detection row и trade entry row
считать anomaly_events.csv полным поминутным state artifact
```

### 3.2. post_pump_distribution_v1_*

Цель:

```text
исследовать не первичный spike, а позднее post-extension состояние,
где рынок уже сделал сильное движение и может перейти в continuation, fade или chop.
```

Стартовый trigger:

```text
daily_return_asof_t > 0.30
and trade_count_market_percentile_asof_t > 0.99
```

Правило:

```text
post-pump t_0 — это момент фактической активации post-pump trigger,
а не обязательно первичный seed_time broad anomaly.
```

Запрещено:

```text
использовать final daily return
использовать trade_count percentile, рассчитанный по будущим минутам
считать post-pump trigger заменой универсального Core methodology
```

## 4. Event and state lifecycle

Для anomaly family online state обновляется каждую 1m после trigger/detection.

Strategy-specific state fields:

```text
event_id
symbol
state_time
minutes_since_event_start
minutes_since_detection
minutes_since_trigger
event_alive
running_high_asof_t
running_high_time_asof_t
running_low_asof_t
running_low_time_asof_t
time_since_running_high
time_to_running_high
current_close
current_return_from_start
distance_to_running_high_core_atr_1440
distance_to_running_low_core_atr_1440
distance_to_structural_low_core_atr_1440
distance_to_structural_high_core_atr_1440
```

Правило:

```text
running_high_asof_t = max(high) from event_start_time to state_time only.
```

Запрещено:

```text
использовать final high всего будущего события
использовать future reclaim/failure для текущего state
```


### 4.1. Artifact boundary: events vs online state

```text
anomaly_events.csv:
  event/lifecycle seed rows returned by generate_triggers() and validated by Core.
  It answers: what event exists, when it started, when it was detected, why it passed/rejected gates.

anomaly_state_1m.csv:
  per-minute online expansion after detection.
  It answers: what was knowable at each state_time while the event was alive.
```

Правило:

```text
Если нужен поминутный lifecycle, читать anomaly_state_1m.csv, а не растягивать anomaly_events.csv вручную.
```

Запрещено:

```text
добавлять running_high_asof_t/running_low_asof_t в anomaly_events.csv как final event summary
добавлять future_return/future_label/PnL в anomaly_events.csv
использовать anomaly_events.csv как training matrix напрямую без state/features builders
```

## 5. Required Core feature families for anomaly

Anomaly strategy ожидает, что Core предоставит generic feature families:

```text
Price Path core_atr_1440-normalized
Speed-normalized Time / Alpha Decay
Volume Relative
Trades / Flow
Open Interest Relative
Liquidation Flow
CVD Divergence
Cross-Sectional / Market-Relative
Signal Clustering / Systemic Beta
Market Context
Structure core_atr_1440-normalized
Data Quality Flags
```

Этот файл не переопределяет формулы Core feature contract.
Если формула универсальна для всех стратегий, она должна жить в core methodology, а не здесь.

## 5.1. Time context without raw calendar leakage

Запрещено подавать в model features сырые календарные индексы `hour_of_day`, `day_of_week` или похожие ordinal calendar IDs.

Разрешённая замена:

```text
relative_volume_hourly_phase = current rolling 60m volume / historical average volume for the same hour-of-day from train-only rolling history
market_liquidity_rank = current cross-sectional rank of trading activity inside point-in-time universe
session/liquidity phase features must be physical relative features, not raw clock labels
```

Смысл:

```text
Модель должна видеть, что ликвидность выше/ниже обычной для текущей фазы рынка, а не заучивать конкретный номер часа из истории.
```

## 6. Anomaly-specific relaxed geometry features

Для post-extension / shelf / false-breakout anomaly variants стратегия добавляет relaxed continuous geometry.

Минимальный strategy-specific feature set:

```text
initial_pump_height_core_atr_1440
post_pump_consolidation_minutes
consolidation_width_ratio
shelf_low_asof_t
shelf_high_asof_t
current_low_minus_shelf_low_core_atr_1440
current_close_minus_shelf_low_core_atr_1440
current_high_minus_shelf_high_core_atr_1440
minutes_spent_below_shelf
minutes_since_reclaim
volume_on_sweep_percentile
trade_count_on_sweep_percentile
cvd_change_during_sweep
oi_change_during_sweep
liq_intensity_during_sweep
```

Правило:

```text
Эти фичи должны быть рассчитаны только из данных <= state_time.
Расчет должен проходить point-in-time equivalence audit Core: добавление будущих свечей не должно менять feature values прошлых rows.
```

Запрещено:

```text
подавать hard binary is_liquidity_sweep / is_perfect_shelf / is_false_breakout как основной model feature
отбрасывать неидеальные setups до ML только потому, что они не совпали с ручным шаблоном
использовать future reclaim для вычисления shelf/sweep feature на state_time
использовать backward_fill, shift(-N), centered rolling или future extrema для anomaly geometry features
```

Binary flags разрешены только как audit/debug fields.

## 7. Liquidation / OI / CVD interpretation inside anomaly strategy

Anomaly strategy использует эти семейства как объясняющие признаки, но не как ручные trade rules.

Ожидаемая рыночная физика:

```text
short liquidations могут поддерживать upside squeeze / continuation
long liquidations могут поддерживать downside cascade / fade
OI growth/decline помогает отличать new leverage от forced close
CVD divergence помогает отличать подтверждённый поток от absorption/distribution
```

Запрещено:

```text
делать вывод о manipulation/spoofing только по CVD
использовать missing OI/liquidation как edge
делать short/long signal только из факта убыточного противоположного сценария
```

## 7.1. Missing Data Policy: required_data_streams

Запрещено использовать факт отсутствия или наличия данных Open Interest и Liquidation как торговый паттерн, edge или model feature. Anomaly family использует явную матрицу требований к данным.

```text
broad_anomaly_v1_*:
  open_interest: optional
  liquidations: optional
  если stream отсутствует, Core выставляет missing_oi_flag / missing_liquidation_flag и не трактует missing как edge

post_pump_distribution_v1_*:
  open_interest: required
  liquidations: required
  если stream отсутствует за symbol/day, Core делает explicit reject до генерации triggers

post_anomaly_extension_v1_*:
  open_interest: optional by default
  liquidations: optional by default
  required mode допускается только как отдельный strategy_version или experiment mode
```

Ablation Runs:

```text
no_oi_mode / no_liquidation_mode могут принудительно переключать required=false,
но только как отдельный experiment с записью в EXPERIMENT_LOG/research ledger.
```


### 7.2. Optional stream flags are audit/debug, not alpha features

Для `broad_anomaly_v1_*` отсутствие OI/liquidation не должно менять смысл гипотезы. Поэтому optional stream handling разделяется на два слоя:

```text
feature value fallback:
  missing optional numeric stream -> neutral value required by feature schema, usually 0.0

audit flag:
  missing_oi_flag / missing_liquidation_flag -> system/debug field, not model feature by default
```

Правило:

```text
Если missing flag предлагается как model feature, это отдельный data-quality experiment, а не стандартный anomaly run.
```

Запрещено:

```text
давать модели exploit-ить наличие/отсутствие Binance stream как рыночный edge
сравнивать train/OOS, если required_data_streams менялись без нового experiment id
скрывать отсутствующий required stream под нулевым feature value
```

## 8. Strategy variants and horizons

Anomaly family поддерживает разные horizons только через отдельные strategy variants.

Правило:

```text
Каждая комбинация торговой логики и временного горизонта регистрируется как самостоятельный изолированный инстанс стратегии.
Запрещено смешивать разные горизонты прогнозирования внутри одной модели CatBoost.
Один strategy_name/version = один horizon_minutes.
```

Утверждённый список базовых инстансов платформы:

```text
broad_anomaly_v1_h15              быстрый скальпинг импульса
broad_anomaly_v1_h30
broad_anomaly_v1_h60
post_anomaly_extension_v1_h60     торговля продолжения на часовом окне
post_anomaly_extension_v1_h120
post_pump_distribution_v1_h60     торговля полки распределения
post_pump_distribution_v1_h120
post_pump_distribution_v1_h180
```

Правило:

```text
post-extension / post-pump strategies не должны использовать короткий 15m horizon как основной,
если setup формируется часами и торговая гипотеза живёт в 60–180m окне.
```

H_max для purging/embargo рассчитывает Core как максимум horizons активных strategies run-а.

## 8.1. Trading & Simulation Defaults

Параметры `take_profit_atr_1440` и `stop_loss_atr_1440` являются дефолтными настройками риск-менеджмента конкретных инстансов стратегий для модуля simplified trade simulation. Они определяют физические границы выхода из позиции в бэктестере и не должны смешиваться с общими математическими порогами разметки Core labels.

| Имя инстанса стратегии | Horizon (m) | Default TP (в долях core_atr_1440) | Default SL (в долях core_atr_1440) |
| :--- | :--- | :--- | :--- |
| broad_anomaly_v1_h15 | 15 | 1.5 | 1.0 |
| broad_anomaly_v1_h30 | 30 | 2.0 | 1.1 |
| broad_anomaly_v1_h60 | 60 | 2.5 | 1.2 |
| post_anomaly_extension_v1_h60 | 60 | 2.5 | 1.3 |
| post_anomaly_extension_v1_h120 | 120 | 3.0 | 1.5 |
| post_pump_distribution_v1_h60 | 60 | 2.0 | 1.2 |
| post_pump_distribution_v1_h120 | 120 | 3.0 | 1.5 |
| post_pump_distribution_v1_h180 | 180 | 4.0 | 2.0 |

## 9. Label semantics for anomaly family

Базовый anomaly target:

```text
long_continuation
short_fade
static_or_chop
unclear
```

Примерная интерпретация:

```text
long_continuation:
  decisive upside continuation in ATR units without prior structural failure

short_fade:
  failed continuation, structural break, or downside move in ATR units

static_or_chop:
  no decisive continuation or fade inside horizon, range persistence

unclear:
  conflicting path, insufficient data, overlapping label conditions, trap-like ambiguity
```

Правило:

```text
trap не является пятым основным классом для MVP anomaly model.
Если нужен отдельный trap-class, это новый label_schema_version и отдельный experiment.
```

Запрещено:

```text
использовать fixed-percent thresholds вместо core_atr_1440-normalized labels
подбирать thresholds после OOS
смешивать different label_schema_version в одном model_version
```

## 10. Strategy-gated dataset

Для anomaly-specific model Core должен применить:

```text
market_frame
  -> anomaly_strategy.generate_triggers(market_frame_asof)
  -> trigger_frame with event_id/state_time/event_start_time/is_trigger
  -> keep only is_trigger == true
  -> build train/validation/calibration/OOS prediction rows
```

Запрещено:

```text
обучать anomaly-specific CatBoost на спокойном рынке is_trigger=false как negative class
разбавлять dataset миллионами фоновых минут ради искусственной accuracy
```

Правильный вопрос модели:

```text
не “есть ли активность на рынке”,
а “какова будущая природа выбранного anomaly state”.
```


### 10.1. Dataset row identity

Одна обучающая строка anomaly model должна иметь стабильный identity:

```text
strategy_name
strategy_version
horizon_minutes
symbol
event_id
snapshot_time
label_horizon_minutes
feature_schema_version
label_schema_version
```

Правило:

```text
Для одного model_version все rows должны иметь один horizon_minutes и один label_schema_version.
Для одного strategy variant + symbol первый accepted trigger блокирует новые dataset rows до state_time + horizon_minutes.
Последующие минуты того же сигнального каскада получают reason_if_excluded = cascade_suppressed и используются только для audit/debug, не для train/simulation.
```

Запрещено:

```text
склеивать h15/h30/h60 rows в один CatBoost multiclass model
учить модель на event-level rows, если prediction принимается на state-level rows
дедуплицировать разные snapshot_time одного event_id как будто это один пример
считать каждую минуту одного пампа независимой обучающей строкой
```

## 11. Anomaly atlas

Anomaly atlas строится только после корректных state/future path artifacts.

Обязательные anomaly slices:

```text
price_shape_atr × future_outcome_atr
speed_regime × future_outcome_atr
volume_regime_relative × future_outcome_atr
flow_regime × future_outcome_atr
OI_regime_relative × future_outcome_atr
liquidation_regime_relative × future_outcome_atr
CVD_divergence_regime × future_outcome_atr
cross_sectional_rank_regime × future_outcome_atr
systemic_cluster_regime × future_outcome_atr
alpha_decay_bucket × future_outcome_atr
session × future_outcome_atr
market_context × future_outcome_atr
```

Atlas не доказывает edge.
Он только генерирует гипотезы и показывает, есть ли структура в будущих outcomes.

## 12. Anomaly-specific controls

Кроме universal Core placebo, anomaly family требует baselines:

```text
always follow anomaly
always fade anomaly
fade only after extension
follow only early squeeze
no CVD features ablation
no OI features ablation
no liquidation features ablation
idiosyncratic-only subset
systemic-cluster-only subset
```

Если anomaly model выигрывает только в systemic beta shock, это не считается доказательством symbol-specific alpha.

## 13. Anomaly-specific reject reasons

Strategy-specific reject reasons должны быть явными и не маскировать data bugs.

Минимальный список:

```text
not_triggered
technical_noise_shock
warmup_after_data_gap
cascade_suppressed
data_quality_fail
insufficient_history_for_ATR
insufficient_cross_section
missing_required_liquidation_data
missing_required_oi_data
horizon_not_available
future_path_incomplete
anti_binary_rule_failed
causality_gate_failed
outside_strategy_lifecycle
RR_unacceptable
calibrated_confidence_too_low
systemic_cluster_guardrail
```

Запрещено:

```text
silent fallback
generic reject без reason_if_excluded
скрывать неизвестную схему данных под empty result без audit
```

## 14. Anomaly events artifact lifecycle structure

`anomaly_events.csv` формируется Core на основе trigger frame, возвращённого `generate_triggers()` выбранной стратегии. Это event/lifecycle seed artifact, а не полный поминутный state artifact. Строки упорядочиваются по `state_time`.

Внутренний trigger frame, который стратегия возвращает в Core, обязан содержать:

```text
event_id: str
symbol: str
state_time: pl.Datetime[ms, UTC]        # минута/момент калькуляции t_0 во внутреннем формате Core
event_start_time: pl.Datetime[ms, UTC]  # физическое начало импульса/полки
minutes_since_start: int                # возраст события: state_time - event_start_time в минутах
is_trigger: bool                        # активен ли signal/lifecycle trigger прямо сейчас
```

Persisted `anomaly_events.csv` является внешней serialization boundary и сохраняет эти же времена как:

```text
state_time_ms: int
event_start_time_ms: int
event_detection_time_ms: int
seed_time_ms: int
```

`cascade_suppressed` и `warmup_after_data_gap` не должны попадать в accepted event artifact как train/simulation rows; они отражаются в data-quality/protocol audit.

Кастомные audit fields anomaly family, сохраняемые по declared artifact schema:

```text
event_detection_time / event_detection_time_ms at artifact boundary
seed_time / seed_time_ms at artifact boundary
seed_open
seed_high
seed_low
seed_close
initial_move_pct
initial_volume_zscore
initial_quote_volume_zscore
initial_trade_count_zscore
technical_noise_shock
raw_candle_gap_minutes
excluded_by_data_quality_gate
detector_version
```

Правило:

```text
Core не выводит семантику стратегии из audit fields.
Audit fields нужны для диагностики trigger/lifecycle, а не для обхода BaseStrategy contract.
Persisted CSV aliases могут сериализовать время как `*_ms`; это внешний artifact boundary. BaseStrategy trigger frame обязан возвращать `state_time/event_start_time` как pl.Datetime[ms, UTC].
Rows с cascade_suppressed или warmup_after_data_gap не имеют права попадать в model train/OOS/simulation entry set.
```

## 15. Artifact aliases for anomaly family

Core canonical artifacts остаются strategy-neutral.

Для совместимости anomaly family может писать aliases:

```text
anomaly_events.csv              -> strategy_events.csv where strategy_family=anomaly
anomaly_state_1m.csv            -> strategy_state_1m.csv where strategy_family=anomaly
anomaly_future_paths.csv        -> strategy_future_paths.csv where strategy_family=anomaly
anomaly_nature_atlas.csv        -> strategy_nature_atlas.csv where strategy_family=anomaly
anomaly_oos_predictions.csv     -> strategy_oos_predictions.csv where strategy_family=anomaly
anomaly_calibration.csv         -> strategy_calibration.csv where strategy_family=anomaly
anomaly_decision_timing.csv     -> strategy_decision_timing.csv where strategy_family=anomaly
anomaly_trade_simulation.csv    -> strategy_trade_simulation.csv where strategy_family=anomaly
anomaly_protocol_audit.csv      -> strategy_protocol_audit.csv where strategy_family=anomaly
```

MVP1 expected utility / EV fields live inside `anomaly_decision_timing.csv`.

Правило:

```text
Alias не должен становиться Core contract.
Новая не-anomaly стратегия не обязана создавать anomaly_* artifacts.
```

## 16. What belongs here vs Core

Менять этот файл, если меняется:

```text
anomaly trigger
anomaly lifecycle
anomaly-specific geometry features
anomaly horizons
anomaly class semantics
anomaly reject reasons
anomaly ablations/baselines
anomaly artifact aliases
```

Не менять этот файл, если меняется:

```text
walk-forward protocol
purging / embargo
calibration protocol
holdout governance
universal data quality gates
point-in-time universe rules
slippage penalty
double-barrier rule
Core artifact schema
BaseStrategy contract
```

Если хочется поменять и этот файл, и core methodology одновременно, сначала проверить:

```text
это действительно contract version bump
или мы снова смешиваем методологию со стратегией?
```

## 17. Короткий принцип anomaly family

```text
Аномалия — только один подключаемый strategy family.
Она формулирует trigger и рыночную гипотезу.
Core отдельно доказывает, что проверка этой гипотезы честная, калиброванная и воспроизводимая.
```
