# Pump-fade cross-fitted phenotype discovery protocol

Status: frozen before the broad-context discovery run.

## Objective

The primary discovery output is a catalog of separate, interpretable online
phenotypes with frozen fade probabilities. Global CatBoost AUC is diagnostic;
it is not the discovery objective. CatBoost supplies nonlinear tree paths, while
Core owns temporal cross-fitting, recurrence stability, calibration,
verification, multiplicity control, null controls, assignments, and coverage.

## Temporal separation

```text
search:       2025-06-01 .. 2025-11-01
calibration:  2025-11-01 .. 2026-01-01
verification: 2026-01-01 .. 2026-06-18
```

Recurrence-chain IDs cannot cross these segments. Inside search, three expanding
temporal folds train only on chains whose labels resolved before the validation
block. Candidate leaf rules are scored only on their later fold, never on their
training rows.

## Broad feature surface

The strategy declares `pump_fade_broad_phenotype_surface_v1`. It contains all
typed causal pump-fade model features plus BTC/ETH market context, BTC/ETH
positioning, same-symbol positioning, CVD/event memory, premium-index, funding,
and explicit availability flags. Future/label fields are forbidden.

Numeric missing values receive a search-period median and a separate missingness
indicator. Categorical levels are frozen from search and unknown later levels
receive an explicit indicator. The transform is persisted; no full-period fit or
silent sentinel is allowed.

## Search, freeze, and probability

- Three CatBoost generators use depths 3/4/5 and fixed seeds.
- Every non-empty tree leaf is screened and retained in the screening ledger.
- Fold CatBoost probabilities are strictly out-of-fold. Three shallow risk
  surrogates partition the pooled OOF risk surface into interpretable paths;
  surrogate paths must independently pass the same rules in at least two
  temporal folds and two generators. This adds stable consensus partitions,
  not in-sample label fitting.
- A fold candidate needs at least 40 events, fade rate 0.58, lift 1.12 against
  its ISO-week baseline, and Wilson lower bound 0.48.
- Membership-equivalent rules are clustered at Jaccard 0.55. A phenotype must
  recur in at least two folds and two generators.
- Duplicate frozen phenotypes above Jaccard 0.90 are removed; at most 50 distinct
  rules are frozen.
- Probabilities are estimated only in the untouched calibration segment with a
  Beta shrinkage prior of strength 20 anchored to its global fade rate.

## Later verification and claims

A high-probability phenotype requires frozen probability at least 0.70, at least
50 verification events, observed rate at least 0.65, Wilson lower 95% at least
0.60, calibration gap at most 0.08, positive edge in at least 60% of eligible
weeks, and BY-FDR q at most 0.05.

Nineteen within-calendar-month label permutations repeat the complete
cross-fitted search. Real candidate count and best search OOF rate must both beat
the empirical controls. Development results cannot become production evidence;
unchanged rules require genuinely new forward confirmation.

## Research memory and follow-up

Failure of an admission gate does not delete a phenotype. Every frozen rule is
written to `phenotype_followup_registry.csv` with its unchanged scientific
status, failure mode, mechanism features, follow-up priority, and next admissible
study. Verified medium-probability rules are forward recalibration candidates;
later-verification failures are stability candidates; calibration failures remain
archived research candidates rather than disappearing from the evidence trail.

This registry is not a softer acceptance layer. It forbids changing a frozen
rule, probability threshold, or gate using the already viewed calibration and
verification periods. Mechanism ablations and feature refinements require a new
frozen protocol, and confirmation requires data after the source verification
boundary.
