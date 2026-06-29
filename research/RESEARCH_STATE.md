# Research state

Last updated: 2026-06-29.

Current phase: structural pump-fade archetype research inside `anomaly_science`.
This is offline research only. It is not live, shadow-live, production execution,
or a complete trading system.

## Source of truth

Active strategy source of truth:

```text
docs/pump_fade_archetype_protocol.md
```

That file is the only normative Strategy Spec for the current structural
pump-fade thread.

Non-normative / secondary files:

```text
README.md                                operational overview only
COMMANDS.md                              command reference only
research/STRATEGY_SPEC.md                pointer to the canonical Strategy Spec
docs/pump_fade_market_mechanics.md       explanatory market-mechanics notes only
docs/strategies/anomaly_strategy.md      fixed-horizon compatibility strategy only
```

Core methodology source of truth:

```text
docs/research_methodology_core.md
```

Experiment evidence source of truth:

```text
research/EXPERIMENT_LOG.md
```

`RESEARCH_STATE.md` is a current-state summary. It must not upgrade or soften
experiment statuses from `EXPERIMENT_LOG.md`. If the two files conflict, the
experiment ledger wins until both are reconciled in the same patch.

Core methodology must remain strategy-independent. It must not point to
`docs/pump_fade_archetype_protocol.md`, `docs/strategies/anomaly_strategy.md`,
or any other strategy-specific protocol as part of its normative contract.
Concrete strategies may point to Core; Core must not depend on concrete
strategies.

## Active strategy contract

Current active strategy family:

```text
structural pump-fade
```

Current implementation contract:

```text
PumpFadeStrategyDefinition
strategy_contract_version = horizon_free_event_strategy_v1
```

The active pump-fade target is a horizon-free structural close race at causally
qualified new running highs:

```text
fade      = close-to-base happens before close-above-running-high confirmation
non-fade  = close-above-running-high confirmation happens first
```

Fixed-horizon `BaseStrategy` variants remain compatibility and smoke-test paths.
They do not define the active structural pump-fade strategy.

## Current implemented scope

Implemented and usable for the offline research scope:

```text
legacy quarantine boundary
Binance Vision enriched 1m cache/export boundary
cache export coverage and manifest proof artifacts
data quality gates
point-in-time universe from historical data availability
strategy-neutral canonical strategy_* artifacts with anomaly_* compatibility aliases
fixed-horizon MVP1 research pipeline for compatibility/smoke usage
structural pump-fade decision dataset builder
structural pump-fade nature dataset builder
archetype discovery and controls for pump-fade nature
paired OI incremental experiment runner with finite-OI-feature population gating and shared prepared population/split reuse
strict missingness flags for pump-fade optional/context features
label-only feature-boundary guard for pump-fade archetype configs
whole-event train/validation/calibration split for generic prediction
bounded CatBoost thread count from one runtime source of truth
symbol-filtered simulation grouping for i5/16GB memory discipline
```

Still intentionally absent:

```text
live trading
shadow live
production execution
exchange-order simulation
position sizing
final deployable entry/stop/target policy for pump-fade
```

## Latest pump-fade readout

The full pump-fade nature artifacts produced a useful directional research
signal, but not a tradeable system:

```text
pump-fade event-nature rows: 39,283
overall fade rate: approximately 48.6%
replicated fade archetypes: present, selective, narrow coverage
trade PnL proof: absent
live-readiness: absent
```

Interpretation:

```text
The edge candidate is not "short every pump".
It is a selective late-stage overheated pump regime where fade probability
appears materially higher than the matched baseline.
```

The exact discovered thresholds, for example `rel_vol_phase > 241.8`, are
machine discovery cutpoints. They are not economic constants. They must be
validated through pre-registered coarse bins, rounded-threshold sensitivity, or
continuous-model walk-forward evidence before becoming decision policy.

## OI experiment status

The 30-symbol OI smoke was pipeline-positive but not OI evidence:

```text
decision rows: 4,382
event-nature rows: 1,093
discovery groups: 714
later-verification groups: 324
AUC no-OI: 0.6050
AUC with-OI: 0.6351
paired bootstrap mean delta: +0.0298
95% interval: -0.0038 .. +0.0632
status: inconclusive smoke
```

The full OI run under `.output/results/pump_fade_oi_full_v3` must be treated as
failed partial output, not as completed OI evidence:

```text
completed arm: baseline_same_oi_population
failed arm: with_open_interest
failure: numeric feature `oi_change_5m` contains missing values
paired summary: not produced / not valid
```

The contract bug found by the failed full run is now represented as a hard population gate in code: `oi_available=true` is only raw stream availability; paired OI runs additionally require finite values for every registered OI model feature. The OI runner now writes `oi_incremental_summary.json` with `FAILED_PARTIAL` and returns the output directory instead of crashing after partial artifacts; `FAILED_PARTIAL` remains non-evidential and must not be interpreted as OI value.

Current evidence governance:

```text
EXPERIMENT_LOG.md is the evidence source of truth.
RESEARCH_STATE.md is a status summary.
METHODOLOGY_GAP_LEDGER.md tracks platform capability only, not active-strategy proof.
PATCH_LOG.md tracks proposed/applied code changes only.
```

Still required before interpreting OI:

```text
rerun the full paired experiment from the fixed code
require both arms to complete and pass controls
require the paired group-bootstrap interval to exclude zero for development evidence
never treat historical baseline_same_oi_population partial output as incremental OI evidence
```

## Cache/export validation status

The full local Binance Vision cache/export proof remains the current data-source
boundary validation:

```text
validation artifact: research/validation/cache_export_380d_validation.json
period: 2025-06-03 .. 2026-06-17
exported perpetual symbols: 796
1m rows: 344,895,197
missing UTC days: 0
duplicate 1m rows: 0
unclassified 1m gaps: 0
classified settlement-transition missing rows: 3,570
```

## Current Git state note

Checked-out archive head observed while preparing this update:

```text
d5dc75b
```

Do not treat older notes that mention `fd6f0be4` as current. After applying this
patch and committing, update this section to the actual repository head from:

```bash
git rev-parse --short HEAD
```

If the current repository head cannot be checked, write `UNKNOWN` instead of
inventing a commit.

## Next priorities

Priority 1:

```text
Fix side-specific RR acceptability in decision/simulation.
General is_RR_still_acceptable must not allow a long trade because only short RR is acceptable, or vice versa.
```

Priority 2:

```text
Mark nature-class EV as proxy utility and block final EV-proof language until realized barrier outcome modeling exists.
```

Priority 3:

```text
Add threshold stability audit for discovered archetype rules:
- rounded thresholds
- coarse pre-registered bins
- nearby cutoff sensitivity
- discovery-only quantile bins
```

Priority 4:

```text
Only after OI, RR, proxy-EV, and threshold-stability cleanup, move promising
pump-fade regimes into stronger controls, state-lattice prediction, realized
barrier outcomes, and pessimistic trade simulation.
```

- Runtime thread-count source is centralized in `src/anomaly_science/runtime.py`; archetype and prediction CatBoost configs use the same bounded default for i5/16GB runs, and CLI thread-count overrides are intentionally not exposed.
