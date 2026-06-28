# Core Research Methodology: strategy-independent trading research platform

## 0. Назначение

Этот документ описывает не стратегию, а универсальную исследовательскую платформу для проверки торговых гипотез.

Core отвечает за честность данных, временные контракты, walk-forward, calibration, EV, simulation, audit и воспроизводимость.

Для rule/archetype discovery универсальная реализация Core находится в
`anomaly_science.archetypes`. Evidence status, matched controls, clustered
inference и pristine-holdout governance описаны в
[`pump_fade_archetype_protocol.md`](pump_fade_archetype_protocol.md) на
конкретной зарегистрированной стратегии. Эти статистические механизмы остаются
strategy-neutral; стратегия задаёт только causal feature manifest и target.

Core не должен знать, является ли стратегия anomaly, post-pump, mean-reversion, trend-following, liquidity sweep, basis/funding, market-making или чем-то ещё.

## 1. Главный принцип разделения

```text
Core Methodology = как честно исследовать любую стратегию.
Strategy Spec    = какую конкретную рыночную гипотезу исследовать.
```

Правило:

```text
Если меняется конкретная торговая гипотеза, меняется только файл стратегии.
Если меняется научный протокол проверки, меняется только core methodology.
Если изменение требует правки обоих файлов, значит контракт между Core и Strategy спроектирован плохо.
```

Исключение допускается только при явном version bump базового strategy contract.
Такой bump должен быть отдельным commit и не должен одновременно менять торговую логику конкретной стратегии.

## 2. Обязанности Core

Core выполняет только универсальные задачи:

```text
1. Data Engine
2. Data Quality Gates
3. Point-in-Time Universe
4. Market Context Engine
5. Feature Registry / Feature Schema
6. Strategy Registry
7. Generic Online State Builder
8. Generic Future Path Builder
9. Generic Label Builder
10. Atlas / Discovery Layer
11. Weekly Walk-Forward Prediction
12. Calibration
13. Decision Timing
14. Expected Utility
15. Simplified Pessimistic Trade Simulation
16. Protocol Audit
17. Reproducibility Ledger
```

Запрещено:

```text
вшивать в Core anomaly-specific, post-pump-specific или любую другую strategy-specific семантику
добавлять if/else по имени стратегии внутри ML/Data/Evaluation Engine
менять Core ради одной стратегии, если достаточно расширить Strategy Spec
дублировать правила временной честности в strategy-файлах
```

## 3. Обязанности Strategy Spec

Strategy Spec описывает только конкретную гипотезу:

```text
strategy_name
strategy_version
strategy_contract_version
strategy_family
trigger definition
event/state lifecycle semantics
strategy horizon or explicit horizon-free outcome protocol
structural execution policy version and admissible stop/target anchors
strategy-specific custom features
strategy-specific artifact aliases, если нужны
strategy-specific reject reasons
strategy-specific experiment notes
```

Strategy Spec не имеет права переопределять:

```text
as-of contract
point-in-time universe
purging / embargo
walk-forward split
calibration protocol
final holdout governance
pessimistic double-barrier rule
slippage penalty rule
reproducibility requirements
no-leakage invariants
```

Если стратегия хочет другое поведение в этих областях, это не локальная правка стратегии, а отдельный proposal на изменение Core methodology.

## 4. Stable BaseStrategy contract

Любая стратегия подключается через стабильный контракт. Один инстанс стратегии обслуживает ровно один выбранный горизонт прогнозирования для текущего run-а. Strategy metadata дополнительно объявляет смысловые `allowed_horizons` и `default_horizon_minutes` для этой гипотезы. Multi-horizon research регистрируется как набор отдельных strategy variants или отдельная explicit multi-horizon architecture, а не как неявный tuple/list внутри одного model_version.

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping

import polars as pl


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_name: str
    strategy_version: str
    strategy_contract_version: str
    strategy_family: str
    horizon_minutes: int  # selected target/run horizon
    allowed_horizons: tuple[int, ...]  # semantic strategy subset of Core-supported horizons
    default_horizon_minutes: int
    execution_policy_version: str
    feature_schema_version: str
    label_schema_version: str


class BaseStrategy(ABC):
    @property
    @abstractmethod
    def metadata(self) -> StrategyMetadata:
        """Identity, selected/allowed/default horizons, schemas and structural policy version."""

    @property
    @abstractmethod
    def required_data_streams(self) -> Mapping[str, bool]:
        """
        Missing Data Policy matrix.
        Key = data stream name, value = whether it is required for this strategy instance.
        Core gates missing required streams before trigger generation.
        """

    @abstractmethod
    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        """
        Return a trigger frame using only data <= state_time/snapshot_time.

        Required columns:
            symbol: pl.String
            state_time: pl.Datetime(time_unit="ms")
            is_trigger: pl.Boolean
            event_id: pl.String
            event_start_time: pl.Datetime(time_unit="ms")

        Strategy may add custom audit fields. Core does not parse their semantics;
        it stores only columns declared by the artifact schema.
        """

    @property
    @abstractmethod
    def custom_feature_catalog(self) -> tuple[StrategyCustomFeatureSpec, ...]:
        """Declare every strategy-owned feature, dtype, data dependencies and identifiability."""

    @abstractmethod
    def generate_custom_features(self, context: StrategyFeatureContext) -> Mapping[str, CustomFeatureValue]:
        """
        Возвращает только strategy-specific фичи.
        Фичи должны быть строго causal/as-of: каждая строка использует только данные <= state_time.
        Не fit-ит модели, scaler, calibrator или thresholds.
        Не читает future labels.
        Не использует отрицательные сдвиги, backward-fill, centered rolling windows или future extrema.
        """
```

Правило совместимости:

```text
Core зависит только от BaseStrategy contract.
Strategy зависит от BaseStrategy contract.
Core не зависит от конкретного strategy module.
```

Horizon metadata rule:

```text
horizon_minutes = selected target horizon for this concrete strategy variant/run
allowed_horizons = semantic horizons that this strategy hypothesis allows
default_horizon_minutes = default semantic horizon for this strategy hypothesis

horizon_minutes must be in allowed_horizons
default_horizon_minutes must be in allowed_horizons
allowed_horizons must be a non-empty subset of the Core-supported research horizon set
```


Запрещено:

```text
передавать tuple/list horizons в один strategy instance
смешивать разные horizons внутри одного CatBoost model_version
возвращать из generate_triggers голый boolean mask без event lifecycle metadata
использовать missing required data stream как model feature или market edge
```

### 4.1. Internal time type standard

Внутри расчетного движка Core и в BaseStrategy contract все timestamp-поля используют нативный Polars type:

```text
pl.Datetime(time_unit="ms")
```

Правило:

```text
state_time, event_start_time, event_detection_time и seed_time внутри BaseStrategy/Polars calculation frames являются pl.Datetime[ms, UTC].
Суффикс `_ms` запрещён на BaseStrategy boundary, чтобы не смешивать нативное время Polars с Unix timestamp serialization.
Unix timestamp Int64 в миллисекундах разрешён только на внешней границе: import/export legacy CSV/Parquet, API payload normalization и dataclass-модели persisted artifacts. Если dataclass поле имеет суффикс `_ms`, оно считается serialization boundary, а не hot-path Polars contract.
```

Запрещено:

```text
держать разные внутренние timestamp-типы для разных builders
делать повторные cast Int64 <-> Datetime внутри hot path feature/state/future builders
использовать join_asof между колонками времени разных типов
возвращать `_ms` columns из BaseStrategy.generate_triggers
```


### 4.2. Strategy variant identity and registry invariants

Единица регистрации в Core — не семейство стратегии, а конкретный strategy variant.

Ключ identity variant-а:

```text
strategy_name
strategy_version
strategy_contract_version
strategy_family
horizon_minutes
feature_schema_version
label_schema_version
execution_policy_version
execution_policy_ids
required_data_streams
```

Правило:

```text
Одинаковая пара strategy_name + strategy_version не может иметь разные horizon_minutes, feature_schema_version, label_schema_version или execution policies.
```

Strategy Registry обязан сохранять metadata variant-а в `strategy_run_config.csv` до запуска train/OOS/simulation. Если registry содержит variant, но factory ещё не реализована, Core должен явно завершаться ошибкой `strategy variant is specified but not implemented yet`, а не подменять его ближайшей реализованной стратегией.

Запрещено:

```text
использовать один strategy_name для нескольких horizons
автоматически выбирать default horizon при неизвестном suffix
подменять неготовый strategy variant broad/default стратегией
читать TP/SL из simulation config в обход StrategyMetadata
```

### 4.2.1. Research horizon ownership contract

Core и Strategy делят ответственность за horizons, но не смешивают её.

Core определяет только технически поддержанный research horizon set, то есть набор горизонтов, для которых платформа обязана уметь строить future paths, labels, schemas, prediction target dispatch, controls, EV, simulation, `H_max`, purge и audit.

Утверждённый Core-supported research horizon set для текущего contract version:

```text
15m
30m
60m
120m
180m
```

Strategy Spec определяет смысловые горизонты конкретной гипотезы только через явные strategy variants. Horizon suffix `_h[minutes]` не является свободным параметром CLI или пользователя. Он валиден только если такой variant указан в Strategy Spec, входит в Core-supported horizon set и реализован или явно помечен как specified-but-not-implemented.

Registry является enforcement layer между Core и Strategy. Перед запуском train/OOS/controls/EV/simulation Registry обязан проверить:

```text
strategy variant exists
horizon_minutes входит в Core-supported research horizon set
horizon_minutes соответствует horizon suffix зарегистрированного strategy_name
strategy variant executable, либо run завершается явной ошибкой specified-but-not-implemented
```

Правило:

```text
Core может материализовать raw future/label artifacts для всех Core-supported horizons.
Prediction, controls, EV и simulation выбирают target horizon только из валидированного strategy variant-а.
H_max считается только по активным strategy variants конкретного run-а, а не по всем horizons, которые Core технически умеет считать.
```

Запрещено:

```text
добавлять arbitrary horizons вроде 11m, 32m или 47m только через Strategy Spec или CLI
считать неизвестный suffix допустимым default horizon
разрешать strategy variant, horizon которого отсутствует в Core-supported set
смешивать artifact-supported horizons с executable strategy horizons
использовать 120m/180m broad anomaly run только потому, что Core умеет посчитать 120m/180m labels
```

Добавление нового горизонта вне утверждённого set является Core contract/schema change и требует отдельного patch с обновлением future paths, labels, schemas, prediction/controls/EV/simulation, purge tests, audit и docs.

### 4.3. Trigger Frame validation contract

`generate_triggers()` возвращает не торговый сигнал и не boolean mask, а lifecycle boundary между Strategy и Core.

Минимальная schema trigger frame:

| column | type | contract |
| :--- | :--- | :--- |
| symbol | string | instrument id from point-in-time universe |
| state_time | datetime[ms] | current as-of timestamp of trigger row |
| is_trigger | bool | whether strategy allows this row into strategy-gated dataset |
| event_id | string | stable event id; deterministic hash is preferred over random UUID for reproducibility |
| event_start_time | datetime[ms] | physical start of the event/lifecycle, must be `<= state_time` |

Core validation:

```text
1. required columns exist
2. required columns are non-null for every returned row
3. is_trigger is Boolean
4. event_start_time <= state_time
5. duplicate event_id rows are allowed only when they represent different state_time of the same lifecycle
6. event_id collision across different symbol/event_start_time is forbidden
```

Core-derived lifecycle columns, when needed:

```text
minutes_since_start = floor((state_time - event_start_time) in minutes)
snapshot_time = state_time unless a stricter as-of timestamp is explicitly provided
feature_cutoff_time <= snapshot_time
future_start_time > snapshot_time
```

Запрещено:

```text
создавать event_id из future path/label
делать event_start_time позже state_time
считать is_trigger=false rows negative class для strategy-specific model
прятать data-quality reject внутри is_trigger=false без reason_if_excluded/audit path
```

### 4.4. Causal strategy feature contract

`generate_custom_features()` обязан быть детерминированной causal/as-of трансформацией одного `StrategyFeatureContext`. Core передаёт только закрытые market/event/OI/liquidation rows с timestamp `<= feature_cutoff_time <= snapshot_time`. Для каждой строки feature values должны совпадать с values, полученными из независимо усечённого point-in-time context.

Разрешено:

```text
rolling/expanding windows только по прошлым и текущей closed row внутри symbol/group
положительные lag/shift, которые обращаются к прошлому
asof joins, где правая сторона имеет timestamp <= left timestamp
forward-fill только после явной data-quality проверки и только из прошлого в будущее
```

Запрещено:

```text
shift(-N), lead, negative lag
backward_fill / bfill для model features
centered rolling windows
rolling extrema, которые используют будущие строки относительно state_time
глобальные per-run min/max/percentile/normalization, если они fit-ятся на full period
любая feature, меняющая значение прошлой строки после добавления будущих candles
```

Core имеет право запускать point-in-time equivalence audit:

```text
features_full_context = strategy.generate_custom_features(context whose rows are already truncated at T)
features_independent_context = strategy.generate_custom_features(independently rebuilt context at T)
Оба mappings должны совпадать точно; NaN/inf и undeclared keys запрещены.
```

Если audit FAIL, run получает protocol FAIL; PnL/metrics такого run-а нельзя интерпретировать.

## 5. Universal temporal contract

Для каждой строки исследования:

```text
features можно считать только из данных <= snapshot_time
labels можно считать только из данных > snapshot_time
```

Обязательные поля:

```text
run_id
strategy_name
strategy_version
strategy_contract_version
symbol
state_time
snapshot_time
feature_cutoff_time
future_start_time
label_horizon_minutes
```

Инварианты:

```text
feature_cutoff_time <= snapshot_time
future_start_time > snapshot_time
train_snapshot_time + H_max <= model_freeze_time
```

Запрещено:

```text
использовать future high/low в features
использовать итоговый high/low будущего события как known state
использовать future outcome как фильтр входа
fit scaler/model/calibrator/threshold на full period
обучать и оценивать на одних и тех же днях
```

## 6. Data Engine

Core Data Engine обязан собирать единый point-in-time market frame.

Минимальные данные для perpetual futures research:

```text
1m OHLCV
5m OHLCV, если нужен higher-timeframe context
quote_volume
trade_count, если доступен
taker buy/sell flow, если доступен
closed 5m open interest, если доступен
liquidation / forced order flow, если доступен
BTC/ETH/market context
symbol metadata
listing/delisting metadata
```

Правило:

```text
Missing data является data condition, а не market edge.
```

Запрещено:

```text
тихо заменять неизвестную схему данных generic fallback-ом
объявлять missing OI/liquidation/trade_count рыночным сигналом без отдельного data-quality audit
использовать нынешний universe для исторических дат
исключать delisted symbols из истории, если они торговались в тестируемый день
```

## 7. Data Quality Gates

Core обязан помечать и исключать технические артефакты до strategy trigger.

Минимальные проверки:

```text
missing candles
duplicate candles
bad timestamps
zero/negative prices
zero-volume anomalies
OI gaps
liquidation data gaps
timezone alignment errors
symbol listing gaps
large impossible returns
API maintenance gaps
technical_noise_shock candles after data restoration
```

Правило technical_noise_shock:

```text
Если timestamp_current - timestamp_previous > 3m,
то первая свеча после восстановления потока данных помечается technical_noise_shock = true.
```

Правило Warm-up Window после технического гэпа:

```text
После гэпа Δt > 3m Core обязан включить warmup_after_data_gap для затронутого symbol или market-wide stream.
Длительность warm-up = max_feature_lookback_minutes активного run-а.
max_feature_lookback_minutes берётся из Feature Registry / Feature Schema до запуска strategy trigger.
Внутри warm-up окна запрещены trigger generation, train/validation/calibration/OOS rows и trade simulation entries.
Строки warm-up сохраняются только как data-quality/audit rows с reason_if_excluded = warmup_after_data_gap.
```

Смысл:

```text
Одна свеча technical_noise_shock не лечит искаженные rolling context features.
Все rolling/percentile/ATR/market-relative features считаются недостоверными, пока не накоплена новая непрерывная история длиной max lookback.
```

Запрещено:

```text
использовать technical_noise_shock для генерации strategy triggers
включать technical_noise_shock в ML train/validation/calibration/test
заменять технические gaps silent forward-fill так, будто рынок торговался непрерывно
```

### 7.1. Missing Data Policy через required_data_streams

Core обязан применять декларативную матрицу `strategy.required_data_streams` до генерации triggers.

Правило:

```text
required_data_streams[stream] = true  -> отсутствие stream за symbol/day даёт explicit reject
required_data_streams[stream] = false -> stream optional, missing flag обязателен, missing не является edge
```

Запрещено:

```text
использовать факт отсутствия OI/liquidation/trade_count как торговый сигнал
сдвигать Train и OOS выборки разными missing-data правилами
молчаливо заменять required stream нулями без reject reason
```

Ablation experiments могут временно переключать required streams в `false`, но только как отдельный experiment mode с записью в experiment/research ledger.

### 7.2. Shared DataQualityMask перед trigger

Core обязан строить один общий `DataQualityMask` до вызова `strategy.generate_triggers`.

Правило:

```text
row-local bad candles -> excluded by DataQualityMask before trigger generation
dataset/schema/source failures -> blocking FAIL, trigger generation forbidden
technical_noise_shock and warmup_after_data_gap -> excluded by the same DataQualityMask
```

DataQualityMask является enforcement layer, а не post-processing:

```text
market_frame_asof для strategy.generate_triggers уже не содержит excluded rows.
strategy_events.csv не должен содержать events, seed которых пришёл из excluded rows.
strategy_data_quality.csv обязан хранить explicit row/reason counts for mask exclusions.
```

Запрещено:

```text
сначала сгенерировать triggers, а потом отфильтровать плохие события
использовать critical row-local bad candles как причину silently skip всего detector run-а, если они могут быть явно excluded
пропускать bad rows в baseline/rolling context detector-а
```

## 8. Point-in-Time Universe

Core обязан строить universe на каждый день/момент времени без survivorship bias.

Artifact:

```text
symbol_universe_by_day.csv
```

Минимальные поля:

```text
trade_date
symbol
listed_asof_day
delisted_asof_day
tradable_on_day
has_1m_data
has_5m_data
has_oi_data
has_liquidation_data
liquidity_eligible_on_day
eligible_for_cross_section
first_seen_data_time_ms
last_seen_data_time_ms
data_source_symbol_status
listing_confidence
delisting_confidence
reason_if_excluded
```

Запрещено:

```text
использовать статический список активных сегодня symbols для прошлых дат
исключать delisted symbols только потому, что они не торгуются сейчас
ранжировать cross-section по symbols, данных которых не было доступно <= t
выдавать `delisted_asof_day=false` как доказанный факт, если нет внешней listing/delisting metadata
добавлять symbol в cross-section только потому, что он есть в сегодняшнем universe
```

Правило data-inferred universe:

```text
Если внешняя listing/delisting metadata отсутствует, Core может строить conservative data-inferred universe.
В этом случае first_seen/last_seen и confidence-поля обязательны, а unknown delisting должен быть явно помечен как unknown, не как доказанное отсутствие delisting.
Дни без 1m/5m данных между first_seen и last_seen materialize-ятся как non-tradable rows с reason_if_excluded.
Cross-section использует только eligible_for_cross_section=true.
```

## 9. Feature contract

Core предпочитает relative / normalized / rank / dimensionless features.

Приоритет нормализаций:

```text
volatility-relative: value / ATR_asof, return / realized_volatility_asof
market-relative: cross-sectional percentile/rank среди point-in-time universe
self-history-relative: z-score, percentile, ratio к rolling baseline самого symbol
```

Запрещено:

```text
использовать raw quote_volume, raw OI, raw liquidation notional или raw dollar scale как основной model feature без relative twin-feature
использовать fixed-percent labels/stop/target как универсальную основу между symbols
заполнять отсутствующий percentile нулём без missing flag
```

Strategy-specific фичи допускаются только как дополнение к универсальному feature frame и должны иметь:

```text
feature_name
feature_family
strategy_name
strategy_version
asof_timestamp_contract
normalization_type
is_model_feature
is_audit_only
```


### 9.1. Core ATR-1440 volatility scale

Базовым системным измерителем масштаба цены является `core_atr_1440`: суточная rolling-volatility/ATR, рассчитанная по закрытым 1m candles за 1440 минут.

Правило:

```text
core_atr_1440 считается только из candles с available_time <= snapshot_time.
Все geometry distances, TP/SL defaults, label thresholds и EV distances, где нужен универсальный price scale, нормализуются в долях core_atr_1440.
Короткие ATR/1m range proxy разрешены только для execution micro-penalty, но не как основной label/geometry scale.
```

Граница совместимости:

```text
Исторические artifact columns `ATR_1d_asof_t` / `ATR_1d_pct_asof_t` трактуются как serialization aliases для core_atr_1440.
Новый код обязан использовать смысловое имя core_atr_1440 в contracts/docs, даже если legacy CSV column ещё называется ATR_1d_asof_t.
```

## 10. Anti-Binary Rule для strategy geometry

Core не должен получать от стратегии экспертный hard verdict вместо геометрии.

Запрещено:

```text
подавать в модель hard binary verdict вида is_perfect_setup = 1/0,
если его можно разложить на непрерывные ATR-normalized / relative coordinates.
```

Разрешено:

```text
binary flags как audit/debug fields
continuous relaxed geometry как model features
```

Смысл:

```text
модель должна учить nonlinear decision boundaries, а не получать заранее закодированный ручной ответ.
```

## 11. Generic Online State Builder

Core строит online state rows для выбранной стратегии.

Обязательные поля:

```text
run_id
strategy_name
strategy_version
strategy_contract_version
symbol
event_id
state_time
snapshot_time
event_start_time
minutes_since_start
minutes_since_trigger
is_trigger
state_alive
feature_cutoff_time
```

Core не определяет, что такое trigger семантически.
Это обязанность Strategy Spec. Core только валидирует lifecycle timestamps из trigger frame и строит online state rows из данных, доступных на текущий `state_time`.

Правило:

```text
state row может использовать только market data <= state_time.
state_time должен быть >= event_start_time.
snapshot_time должен быть >= state_time или равен state_time для closed-candle research.
```

Граница артефактов:

```text
strategy_events.csv       = trigger/lifecycle seed rows from Strategy -> Core
strategy_state_1m.csv     = per-minute online state expansion built by Core
strategy_future_paths.csv = future-only path after snapshot_time, never fed back into state/features
```

### 11.1. Trigger de-duplication / episodic dataset rule

Core обязан подавлять сигнальные каскады, чтобы один затяжной market impulse не превращался в десятки почти одинаковых train/simulation rows.

Единица de-duplication:

```text
strategy_name
strategy_version
horizon_minutes
symbol
```

Правило для dataset builders:

```text
Если trigger по symbol X принят в момент t_anchor, то следующие triggers того же strategy variant и symbol X в интервале (t_anchor, t_anchor + horizon_minutes] не создают новые training/calibration/OOS prediction rows.
Они сохраняются только как audit/suppressed rows с reason_if_excluded = cascade_suppressed.
```

Правило для Simplified Trade Simulation:

```text
Если по symbol X уже открыта simulated position для данного strategy variant, новые triggers по X не открывают параллельные позиции.
Unlock наступает после физического закрытия simulated position и после strategy cooldown, если cooldown задан.
При отсутствии simulation close-time в upstream stage используется conservative lock до t_anchor + horizon_minutes.
```

Запрещено:

```text
открывать несколько параллельных virtual trades по одному symbol/strategy variant из одного сигнального каскада
считать каждую минуту одного импульса независимым supervised example
удалять suppressed rows без audit trail
дедуплицировать между разными horizon variants: h15/h30/h60 являются разными model populations
```

## 12. Generic Future Path / Label Builder

Core строит future paths после snapshot_time, но не использует их в features.

Минимальные generic future outcome поля:

```text
future_return_atr_H
future_max_atr_H
future_min_atr_H
time_to_target_atr
time_to_stop_atr
intracandle_double_barrier_hit
barrier_resolution
label_horizon_minutes
label_schema_version
```

Labels должны быть ATR-normalized.

Запрещено:

```text
fixed-percent labels как основной schema
ATR, рассчитанный с будущих свечей после snapshot_time
подбирать label thresholds после просмотра OOS
```

## 13. Pessimistic double-barrier rule

Если внутри одной 1m свечи достижимы и target, и stop, порядок high/low неизвестен.

Правило:

```text
barrier_resolution = stop_loss_first
```

Запрещено:

```text
засчитывать profit-label или profitable trade exit при одновременном достижении stop и target в одной 1m свечи
использовать optimistic intra-candle ordering без tick/order-book данных
```

## 13.5. Atlas / Discovery Layer

Core atlas is descriptive discovery, not decision logic.

Minimum contract:

```text
state rows + feature matrix rows are joined one-to-one as-of snapshot_time
future path fields are used only for descriptive response summaries
all rows preserve feature_cutoff_time <= snapshot_time < future_start_time
all configured research horizons are written explicitly through outcome_horizon_minutes
outcome_coordinate must identify the ATR-normalized horizon
```

Required descriptive coverage:

```text
15m / 30m / 60m / 120m / 180m ATR-normalized outcomes
session / market_context / speed / systemic cluster contexts
feature-family context splits
relaxed continuous geometry slices when strategy provides them
market-shock group summaries by point-in-time market_shock_id
```

Запрещено:

```text
читать atlas output inside prediction, EV, decision, or simulation layers
использовать atlas_outcome_bin как model label
кодировать manual setup verdict instead of continuous geometry bins
```

## 14. Weekly Walk-Forward protocol

Core использует недельное переобучение тяжёлых моделей.

Для каждой календарной недели W и strategy S:

```text
1. freeze strategy_name/version/contract/features/labels before OOS week
2. define weekly_model_freeze_time_W
3. build train only from history strictly before freeze
4. H_max = max(label_horizon_minutes) среди активных strategies run-а
5. apply purging: train_snapshot_time + H_max <= weekly_model_freeze_time_W
6. exclude data-quality failures
7. apply S.generate_triggers(df_asof) -> trigger_frame with event lifecycle metadata
8. remove is_trigger=false rows before train/test for strategy-specific model
9. build features using only data <= snapshot_time
10. split weekly train into train_fit / train_validation / train_calibration
11. fit CatBoost once for week W and strategy S
12. use validation only inside train period for early stopping
13. fit calibrators only on train_calibration
14. freeze model weights, best_iteration, schemas and calibrators
15. predict each OOS day D inside W with frozen weekly model
16. write prediction, calibration, feature importance and audit artifacts
```

Запрещено:

```text
обновлять CatBoost weights на OOS days недели W
refit calibrator на OOS days недели W
менять feature/label/strategy schema внутри OOS недели
использовать daily heavy retrain на CPU без доказанного прироста качества относительно weekly protocol
```

## 15. Prediction and calibration

Основная prediction задача задаётся Strategy Spec.
Core требует только:

```text
probabilities must be calibrated
raw probabilities must be stored
calibrated probabilities must be stored
decision layer may read only calibrated probabilities
```

Базовый протокол:

```text
CatBoostClassifier -> raw probabilities -> Isotonic Regression calibrators -> normalized calibrated probabilities
```

Metrics:

```text
Brier score
multi-class log loss / binary log loss, depending on strategy target
Expected Calibration Error
reliability curves
calibration by session/week/month/symbol/regime
calibration by systemic_cluster_regime / market_shock_group / alpha_decay_bucket / trigger-age bucket
```

Если стратегия использует multi-class target, class order должен быть frozen в model metadata.

## 16. Sample weighting

Core может применять sample_weight, но только из информации <= snapshot_time.

Разрешённые компоненты:

```text
activity known as-of-t
volume/range/liquidation intensity known as-of-t
market-relative rank known as-of-t
alpha-decay/time-since-trigger known as-of-t
class-balanced multiplier from train only
```

Запрещено:

```text
использовать future return magnitude или future class для веса, кроме train-only class balancing
давать одному market shock доминировать над train без cap
подбирать веса по OOS PnL
```

## 17. Decision timing

Core проверяет, появляется ли уверенность достаточно рано.

Artifact:

```text
strategy_decision_timing.csv
```

Минимальные поля:

```text
strategy_name
strategy_version
state_time
snapshot_time
confidence_calibrated
RR_long_proxy
RR_short_proxy
EV_long
EV_short
EV_wait
EV_no_trade
is_prediction_confident
is_RR_still_acceptable
```

Смысл:

```text
Prediction имеет торговую ценность только если confidence появляется до деградации RR.
```

## 18. Expected Utility

Prediction не равна trade.

Core сравнивает:

```text
EV(long)
EV(short)
EV(wait)
EV(no_trade)
```

EV должен учитывать минимум:

```text
entry reference price
stop distance
target distance
fees
slippage penalty
probability of adverse move
probability of follow-through
```

## 19. Simplified pessimistic trade simulation

Trade simulation разрешена только после calibration и decision timing.

Физические границы выхода задаются только структурными anchors, объявленными стратегией. ATR-multiple и fixed-percent stop/target запрещены. Стратегия определяет допустимые политики; Core причинно применяет их и перебирает заранее зарегистрированную сетку partial-close fractions.

Обязательные правила:

```text
entry_reference = next 1m open after signal
entry_price must include pessimistic slippage penalty and toxic-entry ATR_1m penalty
stop/target must resolve to point-in-time structural price levels
trailing structure becomes active only after causal swing confirmation
partial target variants must be selected inside development/WFA and frozen before outer evaluation
fees must be included at least roughly
funding fees must be included when funding_rate stream is present; if absent, short-distribution conclusions are audit-limited
intracandle double barrier resolves as stop_loss_first
one open simulated position per symbol/strategy variant unless an explicit pyramiding experiment is registered
entry toxic penalty = 0.2 * last closed 1m high-low proxy by default, added against the trade direction
```

Funding / systemic-risk boundary:

```text
Если funding_rate stream доступен, simulation обязана списывать funding при пересечении funding cut-off во время hold.
Если funding_rate stream отсутствует, post_pump_distribution short conclusions нельзя считать final EV proof; это audit limitation, а не zero-cost assumption.
Systemic Regime Emergency Exit допускается только от Core market-context stream, рассчитанного as-of-t: simultaneous_anomalies_count_1m и BTC volatility impulse. Strategy не имеет права локально выключать этот risk layer.
```

Запрещено:

```text
использовать next 1m open как финальную идеальную цену без penalty
использовать fixed-percent universal stop/target
делать вывод по full-period PnL без walk-forward и regime breakdown
```

## 20. IS/OOS governance and final holdout

Разделение данных:

```text
IS / exploration
validation / model-selection
walk-forward OOS
final locked holdout
shadow live
```

Для периода около 380 дней стартовое правило:

```text
первые 320 дней: IS / exploration / validation / nested walk-forward
последние 60 дней: final locked holdout
```

Final holdout нельзя читать до freeze protocol.

Artifacts:

```text
research_ledger.csv
holdout_access_log.csv
```

Запрещено:

```text
менять features/labels/model/thresholds/decision rules после просмотра final holdout и считать holdout чистым
превращать OOS в IS без записи в research ledger
```

## 21. Controls and placebo

Core обязан запускать controls/placebo, применимые к выбранной стратегии:

```text
random labels
time-shuffled labels
symbol-shuffled labels
random entry times
session-only baseline
BTC/ETH-only baseline
volume-only baseline
price-only baseline
always no-trade baseline
simple strategy-specific heuristic baselines
```

Если сложная модель не лучше простых baselines OOS, она не имеет исследовательской ценности.

Если модель работает на placebo labels, pipeline протекает или overfits.

## 22. Protocol audit

Core audit должен проверять минимум:

```text
features <= snapshot_time
labels > snapshot_time
train < test
purge/embargo applied
point-in-time universe enforced
technical_noise_shock excluded
warmup_after_data_gap rows excluded until max_feature_lookback_minutes is restored
relative-over-absolute feature contract enforced
ATR as-of computed from closed past candles
fixed-percent labels forbidden
fixed-percent stop/target forbidden
weekly walk-forward enforced
frozen weekly model used for daily OOS
calibrator not fitted on OOS
double-barrier resolved as stop_loss_first
strategy/core separation enforced
BaseStrategy contract valid
one strategy instance has exactly one horizon_minutes
horizon_minutes belongs to Core-supported research horizon set
horizon suffix matches registered strategy variant metadata
unsupported arbitrary horizons rejected before train/OOS
H_max computed from active strategy variants only
trigger frame schema valid and lifecycle timestamps ordered
BaseStrategy trigger-frame timestamp columns are pl.Datetime[ms, UTC], not Int64 `_ms` columns
trigger cascade suppression applied before dataset/simulation rows
required_data_streams applied before trigger generation
strategy simulation defaults present in StrategyMetadata
is_trigger=false rows excluded from strategy-specific model
no strategy-specific logic inside Core
anti-binary feature relaxation enforced
causal feature point-in-time equivalence audit passed
final holdout not accessed before protocol freeze
```

Если audit FAIL, результат нельзя интерпретировать.

Правило independent forensic audit:

```text
Stage-local PASS rows are not sufficient proof.
Core must provide an independent forensic audit layer that re-reads written artifacts and checks schema, temporal contract, purge/H_max, prediction/model horizon identity, canonical/alias consistency, and existing FAIL audit rows from artifact contents.
```

Правило hard gate:

```text
`run-research` must write an independent forensic audit artifact after simulation, add its status/counts to the run summary, and fail the run if the independent forensic audit emits any FAIL row.
```


## 23. Reproducibility

Каждый run обязан сохранять:

```text
git commit hash
data snapshot hash
config hash
feature schema version
label schema version
strategy name/version/contract
strategy_family
horizon_minutes
execution_policy_version
execution_policy_ids
required_data_streams
max_feature_lookback_minutes
trigger_deduplication_policy
internal_time_type
model version
random seed
dependency versions
run timestamp
```

Artifacts:

```text
strategy_run_config.csv
artifact_manifest.json
```

For `run-research`, the root-level `strategy_run_config.csv` is the canonical run manifest. It must include strategy identity, target horizon, active `H_max`, research/holdout mode, protocol freeze id, data snapshot hash, config hash, dependency versions, methodology ledger status, and forensic audit status. Stage-level `strategy_run_config.csv` files may exist, but they do not replace the root run manifest.

The normalized market-data input boundary must also be reproducible. Cache exports must write `cache_export_coverage.csv` and `cache_export_manifest.json` next to the normalized CSV inputs. Those files record exported symbols, effective date range, row counts, missing UTC days, duplicate/missing 1m rows, excluded delivery contracts, and hashes for the exported input artifacts. When `run-research` is called without `--days`, the export uses the full available local cache period.

Full-cache validation must use a separate proof artifact when the normalized CSVs are too large to rewrite during every audit. `validate-cache-export-proof` validates the manifest and coverage files, requires the expected global calendar span, fails on missing UTC days and duplicate rows, and fails on any missing 1m rows that cannot be classified. Settlement-transition gaps may be allowed only when a `{symbol}SETTLED` sibling exists in the same cache and has timestamps inside the gap; those rows remain explicit lifecycle gaps, not hidden data fills.

## 24. Canonical artifact naming

Core использует strategy-neutral artifact names:

```text
strategy_events.csv
strategy_state_1m.csv
strategy_future_paths.csv
strategy_feature_catalog.csv
strategy_registry.csv
strategy_feature_schema.csv
strategy_data_quality.csv
strategy_rejection_funnel.csv
strategy_protocol_audit.csv
strategy_oos_predictions.csv
strategy_calibration.csv
strategy_calibration_breakdown.csv
strategy_decision_timing.csv
strategy_trade_simulation.csv
```


### 24.2. Canonical `strategy_rejection_funnel.csv` boundary

`strategy_rejection_funnel.csv` is the canonical strategy-neutral lineage artifact for explicit row/event inclusion and exclusion reasons across the offline research pipeline.

Minimum stage coverage:

```text
data_quality
point_in_time_universe
events
state
future_path
labels
prediction
decision
simulation
```

Rule:

```text
Every EXCLUDED row must have a non-empty reason_code.
The funnel is artifact-driven and must not rerun strategy trigger logic, model training, thresholds, or trade simulation.
It explains where rows disappeared; it must not decide new rows.
```


### 24.1. Canonical `strategy_events.csv` boundary

`strategy_events.csv` is the canonical strategy-neutral event/lifecycle seed artifact. Strategy-specific aliases such as `anomaly_events.csv` may point to it, but they must not change the Core contract.

Minimum canonical columns:

```text
run_id
strategy_name
strategy_version
strategy_contract_version
strategy_family
symbol
event_id
state_time
event_start_time
minutes_since_start
is_trigger
reason_if_excluded
```

For the anomaly family, `trigger_component` and `trigger_components` may be persisted as declared strategy audit columns. They explain why the strategy accepted a seed row, but Core must not branch on their strategy-specific meaning.

Optional strategy audit columns are allowed only if declared in that strategy artifact schema. Core may store them, but Core must not branch on their strategy-specific meaning.

Запрещено:

```text
использовать events artifact как замену state_1m artifact
добавлять future outcome / label / PnL columns в strategy_events.csv
делать alias более широким контрактом, чем canonical strategy_events.csv
```

MVP1 expected utility / EV fields are part of `strategy_decision_timing.csv`.

Strategy-specific aliases допускаются для совместимости, но не должны становиться Core contract.

Пример:

```text
anomaly_events.csv может быть alias для strategy_events.csv при strategy_family=anomaly
```

## 25. Change isolation rules

Правило 1:

```text
Изменение Core methodology не должно требовать изменения существующих Strategy Spec,
если BaseStrategy contract не изменился.
```

Правило 2:

```text
Изменение Strategy Spec не должно требовать изменения Core methodology,
если стратегия укладывается в существующий BaseStrategy contract.
```

Правило 3:

```text
Если BaseStrategy contract недостаточен, сначала отдельным commit меняется Core contract version,
потом отдельными commits мигрируют стратегии.
```

Правило 4:

```text
Strategy Spec может ссылаться на Core section IDs, но не должен копировать core rules.
Core может ссылаться на BaseStrategy contract, но не должен ссылаться на anomaly/post-pump/etc.
```

## 26. Короткий рабочий принцип

```text
Core доказывает честность исследования.
Strategy формулирует гипотезу.
ML проверяет предсказуемость OOS.
Decision layer проверяет своевременность и EV.
Simulation проверяет, переживает ли идея простую пессимистичную торговую модель.
Production начинается только после этого.
```
