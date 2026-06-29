# Pump-fade predictive-phenotype discovery protocol

This file is the single canonical Strategy Spec for the current structural
pump-fade research thread. Other Markdown files may explain implementation
details or market intuition, but they must not redefine trigger semantics,
target semantics, lifecycle states, feature admissibility, evidence language,
or execution-policy boundaries for this strategy.

Canonical identity:

```text
strategy_name = pump_fade_close_race_v1
strategy_family = pump_fade
strategy_contract_version = horizon_free_event_strategy_v1
feature_schema_version = pump_fade_market_mechanics_v2
nature_label_schema_version = pump_fade_event_peak_close_race_v1
decision_label_schema_version = pump_fade_close_race_horizon_free_v1
live_trading_strategy = false
```

It supersedes fixed-horizon and ATR-barrier targets for this strategy. The
generic MVP1 pipeline remains available for other strategies and compatibility
smoke tests, but it is not the current pump-fade source of truth.

## Scientific questions and stage order

The research is explicitly two-stage:

1. **Event nature:** at the first causally eligible new-high snapshot, can we
   predict whether the completed pump will fade from its eventual event peak
   back to the pre-ignition base before that peak is invalidated?
2. **Top timing:** only if event nature is predictably positive, can later
   closed new-high snapshots identify when the event peak has probably been
   reached?

Nature has one immutable label per pump. Timing has one label per decision
snapshot. A decision-level top-catching label must never again be presented as
the pump's nature.

Trade entry, stop buffers, costs, and exit optimization are separate later
phases and cannot alter this label.

## Structural execution policy boundary

The current research output is not a live trading rule. When the strategy later
reaches EV and simulation phases, physical exits must be anchored to
point-in-time market structure declared here, not to fixed-percent or
ATR-multiple price levels. ATR remains allowed for normalization and
comparability only.

Admissible fade-after-pump execution policy:

```text
side: short
initial stop anchor: event main high known as-of entry
stop trigger: 1m close beyond the structural high
trailing stop: monotonically lower confirmed swing highs
target anchor: event base
target trigger: touch, with pessimistic fill/slippage assumptions
partial close grid: 25%, 50%, 75%, 100%
remainder after a partial target: remains open under the structural trailing stop
```

Core simulation owns causal swing confirmation, fill assumptions, fees, funding,
same-candle pessimism, partial-close enumeration, and position-overlap rules.
The best close fraction must not be selected on the same OOS block used to
report performance; selection belongs to an inner development/WFA loop and the
selected policy must then be frozen for an outer test block or pristine forward
holdout.

## Online anomaly and decision contract

- Ignition: trade count or quote volume is at least 10x its trailing 24-hour
  median. The current candle is excluded from both baselines.
- Baselines are computed only inside contiguous one-minute blocks. After a
  market-data gap they are unavailable until 1,440 prior contiguous minutes
  exist; data before the gap is never silently treated as adjacent.
- Non-finite/invalid required market rows are removed explicitly and become
  time gaps. Counts and reasons are written to the dataset's
  `.data_quality.csv`; malformed symbols are quarantined as `REJECTED` instead
  of aborting or silently weakening the universe.
- A dataset build fails closed after writing the diagnostic quality table when
  rejected symbols exceed 2% of the requested universe or dropped required
  market rows exceed 1% overall. These operational thresholds are independent
  of the anomaly and label definition.
- Base: low of the last closed red candle strictly before ignition.
- Period: closes after five consecutive minutes below 25% of the running peak
  activity, or after 60 minutes. Confirmation candles remain in the online
  period because their eventual sequence is not known beforehand.
- Qualification is absorbing and evaluated as-of each candle: running pump
  size at least 5%, cumulative turnover at least 300,000 USDT, and running
  median true range at least 3x the pre-ignition 24-hour median true range.
- Decision rows: only closed candles that set a strict new running high, and
  only after causal qualification.

An event is never admitted because of its final size, duration, ATR, peak, or
any other full-period value.

## Exact targets

### Stage 1: event nature

The nature anchor is the first strict new-high decision at or after causal
qualification. Let `B` be the fixed pre-ignition base and let `H*` be the
eventual maximum high inside the registered pump period. Starting strictly
after the candle that sets `H*`:

- nature fade: first future close `<= B`;
- nature invalidation: first future close `> H*`;
- the earlier close wins.

`H*` and the event end are label-only future information. They are never model
features. Registered archetype configs must declare future-only columns in
`input.label_only_columns`; the feature-boundary validator rejects any attempt to
use those columns, label availability flags, label schema IDs, or row-filter
columns as model features. If `H*` occurred before the nature anchor, the event
is censored for nature research because its peak was already missed online. If
neither barrier resolves before the next data gap or end of data, the nature
label is censored.

The nature schema is `pump_fade_event_peak_close_race_v1`. Nature discovery
selects only `is_nature_anchor=true`, so every event contributes at most one
row and one label.

### Stage 2: top timing

For base `B`, anchor high `H`, and future one-minute closes strictly after the
decision snapshot:

- fade: first close `<= B`;
- invalidation: first close `> H`;
- the earlier close wins.

There is no stop buffer and no fixed time horizon. If neither barrier resolves
before the first market-data gap or end of data, the row is censored
(`label_available=false`) and cannot enter supervised fitting or evaluation.

`snapshot_time_ms` and `feature_cutoff_time_ms` are the availability time of
the closed decision candle. `future_start_time_ms` is the availability time of
the next closed candle. These fields are materialized by the builder; the
registered config does not accept inferred timestamps or an attestation.
The decision schema remains `pump_fade_close_race_horizon_free_v1`. It is
reserved for timing and later combined decision research, not nature discovery.

Missing market/context values are data conditions, not neutral values. The
builder must emit `NaN` plus explicit availability/context flags for optional or
not-yet-available inputs instead of hiding missingness behind magic numbers such
as `0`, `0.5`, or `1_000_000`.

## Scientific claim boundary

The output is a catalog of online-identifiable predictive phenotypes, not a
claim that CatBoost leaves are causal pump mechanisms. Here `causal` means
point-in-time available without look-ahead. It does not mean that an observed
feature causes the fade.

Completeness is defined only inside the registered search space: the declared
causal features, CatBoost generator specifications, rolling origins, minimum
support/effect gates, and overlap policy. The run must never claim to have
found every real-world pump nature. It reports coverage and the explicit
`unclassified` remainder in `anomaly_archetype_coverage.csv` and
`anomaly_archetype_assignments.parquet`.

There is no default category-count cap. If an operational cap is registered,
the coverage artifact marks the search as truncated and exhaustive language
is forbidden.

## Nature versus timing

The registered archetype model uses only `features.numeric`, categorical
context, and causal clock features. `features.decision_timing_numeric` is
declared separately and excluded by default. This prevents a category such as
"already pulled back 6%" from being presented as a pump nature.

Clock features used for nature discovery are anchored to ignition, not to the
later decision snapshot. In particular, round-hour means distance of the
ignition minute from `hh:00`.

Timing variables may be fitted only after the full nature run passes its
real-vs-null predictability and replication gates. Online combination will be
`P(event fade nature) * P(peak now | fade nature, current state)`. Training may
condition the second model on resolved fade-nature events, but online filtering
may use only the first model's OOS probability, never the realized nature label.

## Validation and evidence language

Time-series periods must never be randomized into IS/OOS rows.

All history already inspected by researchers is development data. A later
temporal interval can replicate a rule during development, but it is not a
blind holdout. Such categories are labeled `DEVELOPMENT_REPLICATED`.

`PRISTINE_VERIFIED` requires:

1. a protocol freeze identifier created before holdout access;
2. a chronologically later interval whose labels were not inspected during
   feature, threshold, rule, or code development;
3. frozen discovery rules and controls;
4. no tuning after seeing holdout results.

Until new forward data exists, historical robustness should additionally be
estimated with rolling-origin development analysis. It cannot restore a blind
holdout once historical labels have been inspected.

Trade simulation on the same later interval used to assign
`DEVELOPMENT_REPLICATED` is descriptive only. An unbiased EV estimate requires
outer rolling-origin folds whose test blocks were not used to choose the rule,
or the future pristine holdout after protocol freeze.

## Controls and inference

- Global blind is descriptive only.
- Nature AUC has one row per anomaly. Decision/timing diagnostics use
  inverse-anomaly weights so long pumps do not dominate.
- The inferential blind is matched by calendar month, decision index, and
  remaining-distance-to-base quantile.
- Confidence bounds resample ISO-week blocks and p-values use week-level wild
  sign flips. This preserves systemic waves and 24–48 hour same-coin recurrence;
  candidates spanning fewer than eight inference weeks are rejected.
- Benjamini-Yekutieli false-discovery correction is applied to frozen distinct
  candidates, remaining valid under arbitrary dependence between overlapping
  rule tests.
- Nature labels are permuted between complete events within calendar-month
  blocks. Timing controls transplant complete within-anomaly label paths between
  events with the same month and decision-row count. The moved-row fraction is
  reported.
- Global real-vs-null evidence uses at least 19 registered permutations and the
  finite-sample upper-tail p-value `(1 + null >= real) / (N + 1)`. An arbitrary
  absolute shuffled-AUC ceiling is forbidden.
- A real-label phenotype cannot receive a replicated/verified status unless
  the empirical AUC control passes and, when real verified categories exist,
  the identical full rule search also beats the null distribution of verified
  category counts. Otherwise its status is `CONTROL_FAILED`.
- Rule regions are pruned by event-set Jaccard overlap and minimum unique event
  coverage. Different rule text alone does not establish a different nature.

## Registered search and computational budget

- Candidate rules are generated by the registered shallow CatBoost ensemble,
  once for each expanding rolling-origin fraction. No future origin is used by
  an earlier fit.
- A phenotype must recur across the configured fraction of origins and model
  generators by event-membership consensus before it can reach the later
  verification interval.
- After consensus, rules are selected greedily by marginal coverage of events
  not covered by earlier rules. This residual scan is vectorized over the
  complete registered rule pool; models are not repeatedly refit for every
  selected category.
- The production configuration uses three generators and three rolling
  origins: nine real-label fits. Every null always fits the registered primary
  diagnostic model. The expensive full null rule search runs only when the real
  search has a category to validate, and then matches the complete real budget.
- `anomaly_archetype_candidate_funnel.csv` reports exactly which registered
  gate removes candidates; an empty catalog without this funnel is incomplete.
- `anomaly_archetype_threshold_stability.csv` is a diagnostic-only robustness
  artifact for discovered float cutpoints. It evaluates the original CatBoost
  border plus rounded, nearby, and discovery-only coarse-quantile variants on
  discovery and later verification rows. These rows do not change the frozen
  rule, do not create a new archetype, and cannot upgrade evidence status.
- This frozen rule-generation experiment is not a trading probability model.
  Any later probability model used for online decisions still requires weekly
  walk-forward training with frozen weights inside each OOS week.

## Runtime discipline

The canonical pump-fade builder uses `bounded_inflight_symbol_pool_v1`: with `workers > 1`, it submits at most `--max-inflight-symbols` symbol jobs at a time, defaulting to `workers*2`. This keeps full-universe runs from queueing hundreds of pandas/pyarrow symbol tasks at once on a 16 GB laptop while preserving deterministic symbol-order output.

## Commands

```powershell
.venv/Scripts/python.exe main.py build-pump-fade-dataset `
  --cache-dir .output/market/binance_vision/um_futures/enriched_1m `
  --out .output/results/pump_fade_decisions_30.parquet `
  --limit-symbols 30 `
  --workers 4 `
  --max-inflight-symbols 8

.venv/Scripts/python.exe main.py build-pump-fade-nature-dataset `
  --input .output/results/pump_fade_decisions_30.parquet `
  --out .output/results/pump_fade_nature_30.parquet

.venv/Scripts/python.exe main.py run-archetype-discovery `
  --input .output/results/pump_fade_nature_30.parquet `
  --config research/pump_fade_nature_discovery.json `
  --out .output/results/pump_fade_nature_archetypes_30
```

The old scratchpad `trades.parquet` does not satisfy this protocol: it contains
every-candle pullback rows, a 0.5% invalidation buffer, a 1440-minute label cap,
open-time timestamps for closed-candle features, and full-period eligibility.
It must not be used for new claims.
