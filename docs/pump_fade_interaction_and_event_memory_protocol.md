# Pump-fade interaction and resolved-event-memory protocol

## Scope

This protocol adds two research layers without moving strategy logic into Core:

1. the pump-fade strategy builds a causal memory of prior events for the same symbol;
2. generic Core evaluates explicitly registered single-bin and interaction regimes.

Neither layer authorizes EV, trade simulation, or production admission. They only
test whether future fade nature becomes more predictable online.

## Resolved-event memory

The strategy owns event semantics and emits `pump_fade_event_memory_v1`.

Qualification-only coordinates become available at `qualification_time_ms`.
Final peak, outcome, resolution price, flow, and morphology become available only
when:

```text
prior_resolution_time_ms <= current_snapshot_time_ms
```

An unresolved event contributes only to counts, its base known at ignition, and
its qualification size. Its offline-finalized peak or outcome is forbidden.

The bounded memory contains:

- counts over 6h, 12h, 24h, and 48h;
- resolved fade/continuation and unresolved counts;
- a fixed 12-hour-half-life fade rate;
- aggregate size, resolution duration, peak-to-resolution duration, and depth;
- price-relative coordinates against prior base, peak, and resolution close;
- the three latest resolved event slots with flow and morphology summaries;
- explicit slot/memory availability flags.

Raw price is not a model coordinate. Price structure is expressed as a ratio to a
point-in-time structural anchor.

Events separated by at most 48 hours form a causal connected component named
`recurrence_chain_id`. Weekly WFA keeps the ordinary event ID for weighting and
paired prediction keys, but uses the chain ID for fit/validation/calibration and
train/test isolation.

## Incremental probability test

The registered experiment compares, on identical rows and weekly freezes:

```text
baseline market mechanics
baseline market mechanics + resolved-event memory
```

Variants are T0, new-high ordinal 1, and new-high ordinal 2. Familywise alpha is
divided across those three variants. Incremental evidence requires all registered
AUC, log-loss, and Brier effect, clustered interval, and sign-flip gates, followed
by all absolute probability/calibration gates.

## Interaction atlas

Core freezes every axis from discovery only. The strategy explicitly declares at
most 50 pairs/triples, including a non-empty mechanistic rationale. Arbitrary
Cartesian generation is not supported.

The candidate family contains both:

- registered candidate bins for every single axis;
- registered exact-bin pairs and triples.

The same frozen family is evaluated separately at T0, new-high ordinal 1, and
new-high ordinal 2. BY-FDR is applied within each variant at one third of the
registered family alpha, providing a Bonferroni correction across the three
online decision stages.

Cheap support, matched-coverage, and minimum-effect pruning occurs before cluster
bootstrap. Surviving discovery candidates are evaluated unchanged in later
development verification. Benjamini-Yekutieli FDR is applied jointly across all
surviving single and interaction candidates.

Matching remains same-symbol, calendar-month, and frozen activity regime. Week,
month, and symbol clustered intervals plus month/symbol stability gates remain
mandatory.

## Required artifacts

Interaction atlas:

```text
regime_discovery_log.csv
regime_screening.csv
regime_stability.csv
regime_controls.csv
frozen_regime_spec.json
holdout_access_log.csv
regime_atlas.metadata.json
regime_atlas.manifest.json
```

The frozen spec records component axes, bins, and rationales.

Resolved-event-memory probability experiment:

```text
event_memory_probability_summary.csv
event_memory_probability_protocol.json
<variant>/baseline/*
<variant>/with_event_memory/*
<variant>/paired_metrics.csv
<variant>/paired_inference.csv
<variant>/paired_gates.csv
event_memory_probability.manifest.json
```

## Interpretation rules

- CatBoost interactions are not interaction evidence by themselves.
- A useful single effect does not validate any pair containing it.
- A useful pair does not establish causality or participant identity.
- Development verification is not a pristine holdout.
- Rejected interactions are retained and may not be threshold-mined on the same period.
- A regime may enter a future frozen model only after this protocol and a new
  forward confirmation interval.
