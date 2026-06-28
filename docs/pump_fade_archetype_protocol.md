# Pump-fade predictive-phenotype discovery protocol

This is the canonical repository contract for the structural pump-fade
research thread. It supersedes fixed-horizon and ATR-barrier targets for this
strategy. The generic MVP1 pipeline remains available for other strategies.

## Scientific question

At a closed one-minute candle that sets a new running high during a causally
qualified pump, can information available at that close identify predictive phenotypes
that will subsequently close back at the pre-ignition base before any later
close exceeds the anchor high?

Trade entry, stop buffers, costs, and exit optimization are separate later
phases and cannot alter this label.

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

## Exact target

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
The miner also requires the exact label schema value
`pump_fade_close_race_horizon_free_v1`; a dataset with buffered or capped labels
fails before model fitting.

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

Timing variables may be stacked later after pump natures independently pass
the predictability gate. Their incremental value must be reported separately.

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
- Diagnostic real/shuffled AUC uses inverse-anomaly weights, so long pumps with
  more new-high decisions do not dominate the metric.
- The inferential blind is matched by calendar month, decision index, and
  remaining-distance-to-base quantile.
- Confidence bounds resample ISO-week blocks and p-values use week-level wild
  sign flips. This preserves systemic waves and 24–48 hour same-coin recurrence;
  candidates spanning fewer than eight inference weeks are rejected.
- Benjamini-Yekutieli false-discovery correction is applied to frozen distinct
  candidates, remaining valid under arbitrary dependence between overlapping
  rule tests.
- Shuffled labels are transplanted as complete within-anomaly label paths
  between anomaly groups having the same calendar month and decision-row
  count. Labels are aligned by causal decision order. This preserves
  within-pump dependence while destroying the association between a pump's
  features and its future label path. The moved-row fraction is reported; a
  useful null must produce approximately 0.5 later-period AUC and no replicated
  categories.
- A real-label phenotype cannot receive a replicated/verified status unless
  every shuffled run moves at least the registered row fraction, produces no
  verified category, and remains below the registered later-period AUC ceiling.
  Otherwise its status is `CONTROL_FAILED` and it receives no assignment.
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
  origins: nine real-label fits. Each shuffled control repeats the identical
  search budget. The full-origin primary model is reused instead of refit.
- This frozen rule-generation experiment is not a trading probability model.
  Any later probability model used for online decisions still requires weekly
  walk-forward training with frozen weights inside each OOS week.

## Commands

```powershell
.venv/Scripts/python.exe main.py build-pump-fade-dataset `
  --cache-dir .output/market/binance_vision/um_futures/enriched_1m `
  --out .output/results/pump_fade_decisions_30.parquet `
  --limit-symbols 30

.venv/Scripts/python.exe main.py run-archetype-discovery `
  --input .output/results/pump_fade_decisions_30.parquet `
  --config research/pump_fade_archetype_discovery.json `
  --out .output/results/pump_fade_archetypes_30
```

The old scratchpad `trades.parquet` does not satisfy this protocol: it contains
every-candle pullback rows, a 0.5% invalidation buffer, a 1440-minute label cap,
open-time timestamps for closed-candle features, and full-period eligibility.
It must not be used for new claims.
