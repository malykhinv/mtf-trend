# Core Research Methodology: strategy-independent trading research platform

## 0. Назначение

Этот документ описывает не стратегию, а универсальную исследовательскую платформу для проверки торговых гипотез.

Core отвечает за честность данных, временные контракты, walk-forward, calibration, EV, simulation, audit и воспроизводимость.

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
strategy horizon
take_profit_atr / stop_loss_atr defaults
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

Любая стратегия подключается через стабильный контракт. Один инстанс стратегии обслуживает ровно один фиксированный горизонт прогнозирования. Multi-horizon research регистрируется как набор отдельных strategy variants, а не как tuple/list внутри одного model_version.

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
    horizon_minutes: int
    take_profit_atr: float
    stop_loss_atr: float
    feature_schema_version: str
    label_schema_version: str


class BaseStrategy(ABC):
    @property
    @abstractmethod
    def metadata(self) -> StrategyMetadata:
        """Identity, one fixed horizon, schemas and simulation defaults."""

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
            state_time_ms: pl.Int64
            is_trigger: pl.Boolean
            event_id: pl.String
            event_start_time_ms: pl.Int64

        Strategy may add custom audit fields. Core does not parse their semantics;
        it stores only columns declared by the artifact schema.
        """

    @abstractmethod
    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        """
        Возвращает только strategy-specific фичи.
        Не fit-ит модели, scaler, calibrator или thresholds.
        Не читает future labels.
        """
```

Правило совместимости:

```text
Core зависит только от BaseStrategy contract.
Strategy зависит от BaseStrategy contract.
Core не зависит от конкретного strategy module.
```

Запрещено:

```text
передавать tuple/list horizons в один strategy instance
смешивать разные horizons внутри одного CatBoost model_version
возвращать из generate_triggers голый boolean mask без event lifecycle metadata
использовать missing required data stream как model feature или market edge
```

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
reason_if_excluded
```

Запрещено:

```text
использовать статический список активных сегодня symbols для прошлых дат
исключать delisted symbols только потому, что они не торгуются сейчас
ранжировать cross-section по symbols, данных которых не было доступно <= t
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
symbol
state_time
snapshot_time
minutes_since_trigger
is_trigger
state_alive
feature_cutoff_time
```

Core не определяет, что такое trigger семантически.
Это обязанность Strategy Spec.

Правило:

```text
state row может использовать только market data <= state_time.
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

`take_profit_atr` и `stop_loss_atr` являются simulation defaults конкретного strategy instance из `StrategyMetadata`. Они определяют физические границы simplified simulator и не являются label thresholds Core.

Обязательные правила:

```text
entry_reference = next 1m open after signal
entry_price must include pessimistic slippage penalty
stop/target must be ATR-normalized or structure-based ATR-normalized
fees must be included at least roughly
intracandle double barrier resolves as stop_loss_first
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
is_trigger=false rows excluded from strategy-specific model
no strategy-specific logic inside Core
anti-binary feature relaxation enforced
final holdout not accessed before protocol freeze
```

Если audit FAIL, результат нельзя интерпретировать.

## 23. Reproducibility

Каждый run обязан сохранять:

```text
git commit hash
data snapshot hash
config hash
feature schema version
label schema version
strategy name/version/contract
model version
random seed
dependency versions
run timestamp
```

Artifact:

```text
strategy_run_config.csv
```

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
strategy_protocol_audit.csv
strategy_oos_predictions.csv
strategy_calibration.csv
strategy_decision_timing.csv
strategy_trade_simulation.csv
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
