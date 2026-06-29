# Experiment log

This file is the source of truth for experiment evidence status.
`research/RESEARCH_STATE.md` may summarize current state, but if the two files
conflict, the status recorded here wins until the ledger is updated.

Status vocabulary:

```text
SMOKE_ONLY              engineering/pipeline smoke; useful for debugging only
INCONCLUSIVE_SMOKE      smoke signal exists, but uncertainty is too high for evidence
DEVELOPMENT             active development experiment; can guide engineering, not claims
FAILED_PARTIAL          run did not complete all required paired/comparison arms
EVIDENCE_ELIGIBLE       protocol frozen, audits clean, not yet final holdout proof
PRISTINE_HOLDOUT_PASSED final locked holdout passed under frozen protocol
```

Rules:

```text
No SMOKE_ONLY, INCONCLUSIVE_SMOKE, DEVELOPMENT, or FAILED_PARTIAL result may be
used as evidence of tradeable edge.

If one arm of a paired experiment fails, completed sibling arms are diagnostic
only. They are not incremental evidence for the failed comparison.

Machine-discovered thresholds are discovery artifacts until they survive a
pre-registered stability audit or frozen walk-forward protocol.
```

## 2026-06-29 — structural pump-fade nature discovery

Status: DEVELOPMENT.

Research question:

```text
Can late-stage structural pump states be separated into future fade vs non-fade
nature better than broad/matched baselines using only causal as-of features?
```

Observed development readout:

```text
rows: 39,283 pump-fade event-nature rows
overall fade rate: approximately 48.6%
replicated fade archetypes: present but selective and narrow-coverage
trade PnL proof: absent
live-readiness: absent
```

Interpretation:

```text
The current result is a development signal for selective regimes, not a trading
system and not final evidence. Exact discovered cutpoints such as
`rel_vol_phase > 241.8` are machine-discovery thresholds, not economic constants.
```

Required before evidence upgrade:

```text
threshold stability audit
walk-forward calibrated prediction
decision timing / RR / EV validation
matched controls
pessimistic trade simulation
clean evidence-mode forensic audit
```

## 2026-06-29 — 30-symbol pump-fade OI incremental smoke

Status: INCONCLUSIVE_SMOKE.

Research question:

```text
Does adding registered open-interest features improve pump-fade archetype
classification on the same OI-available population?
```

Observed smoke readout:

```text
decision rows: 4,382
event-nature rows: 1,093
discovery groups: 714
later-verification groups: 324
AUC no-OI: 0.6050
AUC with-OI: 0.6351
paired bootstrap mean delta: +0.0298
95% interval: -0.0038 .. +0.0632
```

Interpretation:

```text
Pipeline-positive but not OI evidence. The paired bootstrap interval crosses
zero, so the incremental OI effect is not confirmed on this smoke run.
```

Required before evidence upgrade:

```text
finite registered OI model-feature availability contract
full paired run completion
paired bootstrap interval excluding zero, or other pre-registered success rule
same-population baseline and OI arms under the same frozen split
```

## 2026-06-29 — full pump-fade OI run v3

Status: FAILED_PARTIAL.

Output path observed in local notes:

```text
.output/results/pump_fade_oi_full_v3
```

Paired experiment state:

```text
completed arm: baseline_same_oi_population
failed arm: with_open_interest
failure: numeric feature `oi_change_5m` contains missing values
paired summary: not produced / not valid
```

Interpretation:

```text
This run is not completed OI evidence. The completed baseline_same_oi_population
arm is diagnostic only because its required paired OI arm failed.
```

Contract bug exposed:

```text
`oi_available=true` does not currently guarantee that every registered OI model
feature is finite.
```

Required fix before rerun:

```text
separate raw/current OI stream availability from registered OI model-feature
availability; filter paired arms on the exact registered OI feature set or add an
explicit causal imputation + missing-flag protocol; write FAILED_PARTIAL status
when any required paired arm fails.
```
