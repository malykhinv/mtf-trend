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

## 2026-06-29 — full coarse causal regime-atlas V3 diagnostic

Status: UNFROZEN_DEVELOPMENT_DIAGNOSTIC.

Inputs and governance:

```text
input: .output/results/pump_fade_nature_canonical_full.parquet
input SHA256: 5c640fc09a175f72f04c988e4fce570b0d69a3a17c860c48279bc48bddd55b4b
output: .output/results/pump_fade_regime_atlas_dev_v3
code commit recorded: 1b78035392c783713944261ba84e6f0d7f74b690
working tree dirty: true
pristine holdout accessed: false
discovery rows: 21,746
later development-verification rows: 17,520
```

Pre-ML protocol:

```text
coarse axes frozen from discovery only
same-symbol x calendar-month x activity-regime matched controls
ISO-week, calendar-month, and symbol cluster bootstraps with matched baseline
re-estimated in every draw
999 within-matched-stratum label permutations
Benjamini-Yekutieli FDR across discovery-selected candidates
month and symbol stability gates
```

Observed candidate funnel:

```text
frozen bins: 15
discovery candidates: 3
statistically development-replicated regimes: 1
```

Surviving statistical development candidate:

```text
axis/bin: rel_vol_phase discovery percentile 99+
frozen lower bound: 318.9768997378991
discovery matched rows: 196 / 218 signal rows
discovery fade rate: 0.7551
discovery matched rate: 0.4737
discovery delta: +0.2814
discovery re-estimated week-bootstrap 95% CI: +0.2311 .. +0.4211
verification matched rows: 202 / 233 signal rows
verification fade rate: 0.6139
verification matched rate: 0.4068
verification delta: +0.2071
verification re-estimated week-bootstrap 95% CI: +0.1443 .. +0.3195
verification re-estimated month-bootstrap 95% CI: +0.1480 .. +0.2680
verification re-estimated symbol-bootstrap 95% CI: +0.1114 .. +0.3025
shuffled p-value: 0.001
BY q-value: 0.0055
positive eligible months: 5 / 5
positive eligible symbols: 35 / 44
```

Interpretation:

```text
This is a strong development candidate that justifies proceeding to a frozen
online probability/timing experiment. It is not yet a trustworthy probability,
pristine confirmation, EV proof, or trade signal. The run used a dirty worktree
and a nature artifact generated before the new physical online/offline artifact
separation. Its exact numeric threshold is a discovery percentile boundary, not
an economic constant.
```

Required before evidence upgrade:

```text
regenerate online states and offline labels from the separated lifecycle code
run from a clean committed protocol and record FROZEN status
preserve the 99+ percentile rule without post-hoc threshold changes
evaluate early and later admissible online snapshots under group-aware weekly WFA
calibrate probabilities and pass Brier/logloss/ECE gates
reserve a new forward pristine interval; do not reuse development verification as holdout
```

## 2026-06-29 — separated lifecycle rebuild and t0 weekly probability V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

Lifecycle input:

```text
online rows: 157,360
events: 39,283
resolved nature labels: 39,266 / 39,283
registered t0 population: 37,296
development rows: 20,673
OOS rows: 16,623
online schema: pump_fade_online_state_v1
rejected perpetual symbols: 0 / 796
dropped market-row fraction: 0.0002443 (PASS)
all temporal/schema/join checks: PASS
working tree dirty: true
pristine holdout accessed: false
```

The coarse regime-atlas V3 rerun on this separated lifecycle artifact reproduced
the prior result exactly: 3 discovery candidates, 1 development-replicated
top-1% `rel_vol_phase` regime, verification matched delta `+0.2071`, week-cluster
lower 95% bound `+0.1443`, and BY q-value `0.0055`. This remains development
evidence only.

Registered t0 weekly WFA:

```text
protocol: pump-fade-t0-weekly-probability-development-v1
OOS predictions: 16,623
frozen weeks: 25 / 25
skipped weeks: 0
temporal audit: all PASS
AUC: 0.58339 (FAIL versus 0.60)
calibrated log-loss improvement: -0.000033 (FAIL versus +0.005)
Brier improvement: +0.005128 (PASS)
ECE: 0.007816 (PASS)
p >= 0.70 support: 108
p >= 0.70 observed fade rate: 0.62037 (FAIL versus 0.65)
p >= 0.70 Wilson lower 95%: 0.52620 (FAIL versus 0.60)
p >= 0.70 calibration gap: 0.12053 (FAIL versus <= 0.08)
all registered gates pass: false
```

Diagnostic only: raw CatBoost had AUC `0.58480`, log-loss improvement about
`+0.01040`, Brier improvement about `+0.00518`, and ECE `0.01835`. Direct weekly
isotonic reduced aggregate ECE but overfit small endpoint plateaus to 0/1,
damaging log-loss and the high-probability tail. This diagnosis does not upgrade
or alter the frozen V1 result.

Interpretation:

```text
There is statistically non-random but weak t0 ranking information.
The registered high-confidence fade hypothesis failed.
No threshold, probability, EV, trade, or PnL claim is allowed from this run.
```

## 2026-06-29 — causal new-high state probability family V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

Protocol:

```text
registered causal event-local ordinals: 1, 2, 3, 5, 8, 13
separate weekly model/calibrator for every ordinal
actual label resolution strictly before weekly freeze
25 frozen OOS weeks per ordinal; zero skipped weeks
familywise permutation alpha: 0.05; per-variant alpha: 0.05 / 6
calibration: 20 equal-support bins, Beta prior strength 20, then isotonic
target: current-new-high close-to-base before close-above-current-high race
working tree dirty: true
pristine holdout accessed: false
```

OOS results:

```text
ordinal 1: n=16,631, AUC=0.63924, log-loss gain=+0.02306, Brier gain=+0.00859, ECE=0.00378
ordinal 2: n=11,653, AUC=0.64208, log-loss gain=+0.02046, Brier gain=+0.00680, ECE=0.00769
ordinal 3: n= 8,474, AUC=0.61422, log-loss gain=+0.01268, Brier gain=+0.00387, ECE=0.00168
ordinal 5: n= 4,905, AUC=0.60174, log-loss gain=+0.01146, Brier gain=+0.00348, ECE=0.00507
ordinal 8: n= 2,314, AUC=0.58391, log-loss gain=+0.00588, Brier gain=+0.00135, ECE=0.01056
ordinal 13: n=716, AUC=0.45381, log-loss gain=-0.00190, Brier gain=-0.00036, ECE=0.01896
```

Base fade rates fall from `0.2397` at ordinal 1 to `0.0978` at ordinal 13.
No ordinal produced any calibrated `p >= 0.70` row. Consequently every ordinal
failed the registered high-probability support/rate/Wilson gates; ordinals 8 and
13 also failed predictive-quality gates. No ordinal passed all gates.

Interpretation:

```text
Early new-high states contain useful average ranking information, strongest at
ordinals 1-2, but they do not isolate a high-probability fade population.
Repeated new highs make an immediate base-fade race less likely, not more likely.
This family does not authorize EV, simulation, or trading work.
```

## 2026-06-30 — paired incremental OI probability V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

Protocol:

```text
variants: t0, new-high ordinal 1, new-high ordinal 2
baseline and OI arms use identical complete-case rows
required: oi_available=true and all 9 registered OI features finite
OI-complete coverage: >99.9%
weekly CatBoost and regularized isotonic V2 frozen identically per arm
1000 ISO-week block bootstraps
999 ISO-week cluster sign-flips
familywise alpha: 0.05 / 3 = 0.01667
working tree dirty: true
pristine holdout accessed: false
```

Paired OOS incremental results:

```text
t0:        n=16,614, delta AUC=+0.00047, log-loss gain=+0.00049, Brier gain=+0.00023
ordinal 1: n=16,622, delta AUC=+0.00161, log-loss gain=+0.00036, Brier gain=+0.00012
ordinal 2: n=11,648, delta AUC=+0.00282, log-loss gain=+0.00058, Brier gain=+0.00012
```

Every effect was below its registered minimum. Every week-block 95% interval
crossed zero. Familywise sign-flip p-values for AUC were `0.430`, `0.149`, and
`0.054`; log-loss and Brier p-values were also non-significant. No OI arm
produced a calibrated `p >= 0.70` row, and no paired or augmented model passed
all gates.

Interpretation:

```text
The nine available point-in-time OI coordinates do not add reproducible
incremental fade probability to the current market-mechanics model.
Do not retain them as production model features and do not mine OI subsets on
these OOS periods. Further progress requires a genuinely different causal
information family, not OI hyperparameter tuning.
```

## 2026-06-30 — paired BTC/ETH reference-market probability V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

Protocol:

```text
Core artifact: reference_market_context_btc_eth_v1
references: BTCUSDT, ETHUSDT
24 added features: returns, OI changes, taker imbalance, range activity,
and quote activity versus prior 24h
exact closed-minute point-in-time join
OOS complete-case coverage: about 99.3%
variants: t0, new-high ordinal 1, new-high ordinal 2
identical complete-case baseline/context arms
1000 ISO-week block bootstraps; 999 ISO-week cluster sign-flips
familywise alpha: 0.05 / 3 = 0.01667
working tree dirty: true
pristine holdout accessed: false
```

Paired OOS incremental results:

```text
t0:        n=16,511, delta AUC=-0.00143, log-loss gain=-0.00047, Brier gain=-0.00020
ordinal 1: n=16,514, delta AUC=-0.00814, log-loss gain=-0.00302, Brier gain=-0.00110
ordinal 2: n=11,579, delta AUC=+0.00362, log-loss gain=+0.00123, Brier gain=+0.00042
```

T0 and ordinal 1 were degraded. Ordinal 2 point estimates were positive but
below every registered minimum; all week-block intervals crossed zero and
familywise p-values (`0.077`, `0.033`, `0.034`) failed `0.01667`. No augmented
variant produced a calibrated `p >= 0.70` row. No paired or augmented model
passed all gates.

Interpretation:

```text
Locally available BTC/ETH price, OI, taker, range, and activity context does not
add reproducible high-probability fade information. Large CatBoost importance
for ETH 60m return did not translate into paired OOS improvement and must not be
interpreted as evidence. The family is rejected as a production input.
```

## 2026-06-30 — paired BTC/ETH archived positioning metrics V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

New data artifact:

```text
source: Binance Vision daily metrics archives
dates: 2025-06-01 through 2026-06-18
symbols: BTCUSDT, ETHUSDT
requested archives: 766
ok: 766; missing: 0; failed: 0
metrics interval: 5m
conservative publication lag: +5m
join: backward as-of, available_time <= snapshot, max age 10m
```

The registered family contained 38 log-ratio/change/spread features from
top-trader account ratio, top-trader position ratio, global account ratio, and
taker long-short volume ratio. Complete-case OOS coverage exceeded `99.9%`.

Paired OOS incremental results:

```text
t0:        n=16,613, delta AUC=-0.00819, log-loss gain=-0.00144, Brier gain=-0.00073
ordinal 1: n=16,621, delta AUC=-0.00190, log-loss gain=-0.00046, Brier gain=-0.00011
ordinal 2: n=11,648, delta AUC=+0.00397, log-loss gain=+0.00092, Brier gain=+0.00027
```

T0 and ordinal 1 degraded. Ordinal 2 effects were below registered minima; all
week-block intervals crossed zero and adjusted p-values failed. No augmented
variant produced `p >= 0.70`; no paired or prediction family passed all gates.

Interpretation:

```text
Reference-asset trader positioning does not add reproducible fade edge.
The next non-overlapping hypothesis is symbol-specific positioning for the
pumped asset itself. It requires an event-scoped archive acquisition protocol;
reference feature mining or threshold relaxation is forbidden.
```

## 2026-06-30 — registered single/interaction regime-atlas family V1

Status: UNFROZEN_MIXED_DEVELOPMENT_RESULT.

Protocol and population:

```text
input lifecycle schema: pump_fade_online_state_v2
event-memory schema: pump_fade_event_memory_v1
online rows: 157,360
events / t0 rows: 39,283
state-lattice rows: 105,624
variants: t0, new-high ordinal 1, new-high ordinal 2
single axes: 16
explicit mechanistic interactions: 20 (16 pairs, 4 triples)
arbitrary Cartesian interaction generation: forbidden
support/effect pruning before bootstrap: enabled and fully logged
within-variant correction: BY-FDR across selected singles and interactions
cross-variant correction: Bonferroni, alpha 0.05 / 3
working tree dirty: true
pristine holdout accessed: false
```

Every registered hypothesis, including support/effect-pruned candidates, is
retained in `regime_screening.csv`. The later verification interval never changes
axis cuts or interaction definitions.

Development-replicated single regimes:

```text
t0 rel_vol_phase 99+: n=233, matched delta=+0.20707, week CI lower=+0.13609, BY q=0.00725
t0 high pre_return_60m: n=3,067, matched delta=+0.05863, week CI lower=+0.03467, BY q=0.00725
ordinal 1 decision_index bin 0: n=3,237, matched delta=+0.08063, week CI lower=+0.05068, BY q=0.00285
ordinal 1 close_drawdown 0.015..0.03: n=3,111, matched delta=+0.08367, week CI lower=+0.06600, BY q=0.00285
ordinal 2: no regime passed all gates
```

Interaction result:

```text
No registered interaction passed all gates.
The only repeated verification candidate was efficient_path_x_upper_wick:
  t0:       n=116, delta=+0.19657
  ordinal1: n=116, delta=+0.21201
It was rejected without threshold relaxation: only 2 eligible months and 17
eligible symbols versus registered minima of 3 and 20.
```

Interpretation:

```text
The interaction layer is operational and falsifiable, but it has not established
a reproducible combination edge. The narrow efficient-path/rejection candidate
may only be re-evaluated on additional forward data; mining neighboring bins on
the current development period is forbidden. The replicated single regimes are
development candidates, not calibrated probabilities or trade signals.
```

## 2026-06-30 — causal resolved-event-memory probability V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

Feature and split contract:

```text
lookback: 48 hours
qualification-only fields available from prior qualification
peak/outcome/price/flow/morphology embargoed until prior resolution <= snapshot
latest resolved event slots: 3
memory model coordinates including flags: 78
decision rows with resolved memory: 110,025 / 157,360 (69.92%)
decision rows with at least one resolved prior fade: 74,001
split isolation: causal 48h recurrence_chain_id connected components
variants: t0, new-high ordinal 1, new-high ordinal 2
identical rows/freezes for baseline and augmented arms
1000 ISO-week block bootstraps; 999 ISO-week sign-flips
familywise alpha: 0.05 / 3
working tree dirty: true
pristine holdout accessed: false
```

Paired OOS incremental results:

```text
t0:        n=16,623, delta AUC=+0.00152, log-loss gain=+0.00041, Brier gain=+0.00019
            AUC CI=-0.00216..+0.00542, p=0.208
ordinal 1: n=16,631, delta AUC=-0.00275, log-loss gain=-0.00088, Brier gain=-0.00030
            AUC CI=-0.00534..+0.00011, p=0.973
ordinal 2: n=11,653, delta AUC=-0.00614, log-loss gain=-0.00177, Brier gain=-0.00058
            AUC CI=-0.01096..-0.00184, p=0.991
```

Augmented absolute AUC was `0.58488`, `0.63819`, and `0.63805`. No augmented
variant emitted a calibrated `p >= 0.70` row. Every paired family gate failed;
no augmented probability variant passed all absolute gates.

Interpretation:

```text
The full causal memory of recent resolved pump outcomes and their relative price,
flow, and morphology does not add reproducible fade probability to the current
market-mechanics model. It is not admitted as a production feature family.
Do not mine event-memory slots, horizons, or subsets on this OOS period. The
memory layer remains available for future pre-registered hypotheses and new data.
```

## 2026-06-30 — event-scoped symbol positioning probability V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

Preregistered mechanism and acquisition:

```text
hypothesis: positioning of the pumped symbol itself may expose crowding,
top-trader/global divergence, and taker-flow changes that reference BTC/ETH cannot
event scope: registered t0/state-lattice snapshots only
symbols: 693
requested symbol-day archives: 20,998
ok: 20,998; missing: 0; failed: 0
retained causal 5m metrics rows: 587,091
invalid non-positive ratio rows excluded with audit: 1,869
publication lag: +5m
join: same-symbol backward as-of; max age 10m
features: 19 frozen levels, 15m/60m changes, ignition-relative changes, divergences
arbitrary ratio/lag mining: forbidden
```

Population and protocol:

```text
t0 complete-case coverage: 39,158 / 39,283 (99.68%)
state complete-case coverage: 105,305 / 105,624 (99.70%)
variants: t0, new-high ordinal 1, new-high ordinal 2
baseline and augmented arms use identical complete-case rows and weekly freezes
recurrence-chain split isolation retained
1000 ISO-week block bootstraps; 999 ISO-week sign-flips
familywise alpha: 0.05 / 3
working tree dirty: true
pristine holdout accessed: false
```

Paired OOS incremental results:

```text
t0:        n=16,593, delta AUC=+0.00332, log-loss gain=+0.00081, Brier gain=+0.00040
            AUC CI=-0.00103..+0.00764, p=0.067
ordinal 1: n=16,600, delta AUC=-0.00045, log-loss gain=-0.00048, Brier gain=-0.00015
            AUC CI=-0.00381..+0.00293, p=0.590
ordinal 2: n=11,628, delta AUC=+0.00241, log-loss gain=+0.00075, Brier gain=+0.00027
            AUC CI=-0.00240..+0.00754, p=0.172
```

Augmented absolute AUC was `0.58728`, `0.63966`, and `0.64096`. Every incremental
effect was below the registered minimum, every interval crossed zero, and no
augmented variant emitted a calibrated `p >= 0.70` row. No paired or absolute
probability family passed all gates.

Interpretation:

```text
Same-symbol top-trader/global/taker positioning is materially more relevant than
BTC/ETH reference positioning, but it still does not add reproducible fade edge
under the frozen protocol. It is rejected as a production model family. Do not
mine individual ratios, lags, symbols, or event stages on this OOS period.
Further progress requires a genuinely different causal data family, such as
liquidation flow or independently constructed CVD, not positioning retuning.
```

## 2026-06-30 — causal pump-fade CVD path probability V1

Status: UNFROZEN_NEGATIVE_DEVELOPMENT_RESULT.

Preregistered mechanism and lifecycle audit:

```text
hypothesis: event-path taker-flow trajectory may expose absorption or failed
new-high confirmation beyond scalar event taker imbalance
family: pump_fade_cvd_path_v1
features: 11 frozen path/acceleration/divergence coordinates
source: closed 1m quote volume, taker-buy quote volume, and close only
online state: pump_fade_online_state_v3
feature schema: pump_fade_market_mechanics_v4
v3 rows/events: 157,360 / 39,283; identical keys and labels to v2
source availability: 157,360 / 157,360 (100%)
full-family decision coverage: 130,344 / 157,360 (82.83%)
undefined rows: event age 1-5m, because the registered disjoint prior 5m
acceleration window is unavailable and is not replaced by a sentinel
temporal cutoff audit: pass
```

Population and protocol:

```text
variants: t0, new-high ordinal 1, new-high ordinal 2
baseline and augmented arms use identical complete-case rows and weekly freezes
paired OOS rows: 10,064 / 10,070 / 8,692
recurrence-chain split isolation retained
1000 ISO-week block bootstraps; 999 ISO-week sign-flips
familywise alpha: 0.05 / 3
working tree dirty: true
pristine holdout accessed: false
```

Paired OOS incremental results:

```text
t0:        delta AUC=-0.00099, log-loss gain=-0.00074, Brier gain=-0.00034
            AUC CI=-0.00629..+0.00387, p=0.627
ordinal 1: delta AUC=-0.00048, log-loss gain=-0.00038, Brier gain=-0.00026
            AUC CI=-0.00422..+0.00338, p=0.608
ordinal 2: delta AUC=+0.00055, log-loss gain=+0.00022, Brier gain=+0.00004
            AUC CI=-0.00554..+0.00598, p=0.448
```

Augmented absolute AUC was `0.59136`, `0.62782`, and `0.64494`. Every paired
gate failed. No augmented variant emitted a calibrated `p >= 0.70` row; T0 had
273 rows at `p >= 0.60` with observed rate 0.5934, while later-state variants
emitted no row at even `p >= 0.50`. All 749 lifecycle/experiment manifest
entries passed size and SHA-256 verification.

Interpretation:

```text
The registered CVD trajectory does not add reproducible fade probability beyond
the existing market-mechanics model, including any conditional interactions
CatBoost can learn with the base features. It is not admitted as a production
family. Do not mine alternative CVD windows, coordinate subsets, or event ages
on this OOS period. Historical USD-M liquidation snapshots remain unavailable
from the current public archive; testing true liquidation flow requires a new
causal source or forward collection, not a CVD proxy relabeling.
```

## 2026-06-30 — same-symbol perp-crowding probability V1

Status: UNFROZEN_MIXED_DEVELOPMENT_RESULT_NOT_ADMITTED.

Acquisition and causal contract:

```text
family: pump_fade_perp_crowding_v1
features: 16 frozen premium-index/funding coordinates
symbols: 693
requested official Binance Vision archives: 27,847
ok: 27,218; prelisting/missing: 629; failed: 0
retained premium minutes: 6,985,005
retained funding records: 135,540
premium availability: close_time + 1ms
funding availability: calc_time + 1m
archive manifest entries verified: 2,082 / 2,082
```

Population and protocol:

```text
t0 eligible complete-case coverage: 34,824 / 37,313 (93.33%)
ordinal 1 complete-case coverage: 36,642 / 39,283 (93.28%)
ordinal 2 complete-case coverage: 25,346 / 27,018 (93.81%)
baseline and augmented arms use identical rows and weekly freezes
paired OOS rows: 14,324 / 14,326 / 10,126
1000 ISO-week block bootstraps; 999 ISO-week sign-flips
familywise alpha: 0.05 / 3
working tree dirty: true
pristine holdout accessed: false
```

Paired OOS incremental results:

```text
t0:        delta AUC=+0.00934, log-loss gain=+0.00231, Brier gain=+0.00114
            AUC CI=+0.00467..+0.01530, p=0.004
ordinal 1: delta AUC=+0.00370, log-loss gain=+0.00117, Brier gain=+0.00042
            AUC CI=+0.00066..+0.00736, p=0.016
ordinal 2: delta AUC=-0.00235, log-loss gain=-0.00027, Brier gain=-0.00020
            AUC CI=-0.00713..+0.00245, p=0.811
```

T0 log-loss and Brier passed their registered minimum, CI, and familywise-p
gates. T0 AUC passed CI and familywise-p gates but missed the registered
`+0.010` minimum by `0.00066`. Its absolute AUC was `0.59716`, below `0.60`.
Ordinal 1 effects were statistically positive but below every registered
practical minimum; ordinal 2 degraded. No variant emitted `p >= 0.70`. T0 had
657 rows at `p >= 0.60`, observed fade rate `0.6301`, mean probability `0.6185`.
All 2,775 acquisition/projection/experiment manifest entries passed verification.

Interpretation:

```text
Same-symbol perp premium/funding is the first new family to add consistent T0
probability information across AUC, log-loss, and Brier. It still fails the
pre-registered admission contract and does not deliver high-probability online
predictions. It is not admitted to production. Preserve the exact family as a
forward-confirmation candidate on genuinely new data; do not tune windows,
subsets, thresholds, symbols, or event stages on this development OOS period.
```

## 2026-07-01 — broad cross-fitted pump-fade phenotype discovery V1

Status: UNFROZEN_MIXED_DEVELOPMENT_RESULT_NO_HIGH_PROBABILITY_PHENOTYPE.

Frozen design and population:

```text
feature surface: pump_fade_broad_phenotype_surface_v1
strategy features: 288
explicit model coordinates after missing indicators/one-hot: 430
search: 2025-06-01 .. 2025-11-01; rows/groups: 13,486
untouched calibration: 2025-11-01 .. 2026-01-01; rows/groups: 6,566
later verification: 2026-01-01 .. 2026-06-18; rows/groups: 16,444
recurrence-chain overlap across segments: none
search CatBoost generators: depths 3/4/5, three temporal folds
candidate sources: direct fold leaves and pooled-OOF-risk surrogate leaves
null controls: 19 complete within-month label-permutation searches
working tree dirty: true
pristine forward holdout accessed: false
```

Search and control result:

```text
screened leaves: 29,151
fold candidates passing support/rate/lift/Wilson gates: 917
distinct recurrent frozen phenotypes: 17
null frozen phenotype counts: 0 in every 19/19 permutations
candidate-count empirical p: 0.05
best-OOF-rate empirical p: 0.05
```

Calibration and verification result:

```text
development-verified phenotypes: 14 / 17
calibration rejected: 2
later verification rejected: 1
verification coverage of verified phenotypes: 6,425 / 16,444 (39.07%)
frozen probability range: 0.5666 .. 0.6759
verified frozen probability range: 0.6016 .. 0.6759
later observed-rate range: 0.5381 .. 0.5939
HIGH_PROBABILITY_VERIFIED at frozen p>=0.70: 0
```

The strongest frozen calibration probabilities were `0.6759` and `0.6750`,
but their later observed rates were only `0.5796` and `0.5939`. This is real
temporal overprediction/drift, not rounded into a successful 0.70 claim. All 19
null searches returned no recurrent frozen phenotype; the written manifest and
assignment temporal contract passed audit.

Interpretation:

```text
The requested architecture now exists and does produce many separate, stable,
interpretable fade phenotypes from one broad CatBoost surface. Fourteen retain a
statistically positive later edge, but they are medium-probability groups, not
high-probability groups. The original high-probability objective remains unmet.
Do not lower the 0.70 gate or retune the 17 rules on verification. The exact
catalog may be forward-confirmed; further historical work must add genuinely new
information or improve target homogeneity, not mine neighboring thresholds.
```

Follow-up governance amendment (does not change the frozen result): all 17 rules
are retained in a separate research-memory registry. The 14 verified
medium-probability rules are high-priority forward recalibration candidates, the
verification failure is a medium-priority stability candidate, and the two
calibration failures remain low-priority archived research candidates. Each row
stores its failure mode, evidence snapshot, mechanism features, and admissible
next experiment. No disposition permits retuning on the viewed periods.

## 2026-08-02 — blind drawdown ladder Stage 0

Status: `IS_DESCRIPTIVE_PHENOMENON_ONLY_NO_EDGE_CLAIM`.

Frozen design:

```text
protocol freeze: drawdown_ladder_stage0_20260802_v1
population: Binance USDT perpetual one-minute data available from 2025-06-03
IS end / OOS start: 2026-01-01 00:00 UTC
OOS rows read: 0
anchor: previous completed close at a versioned UTC session boundary
order activation delay: one minute
fill: limit price traded through by 5 bps
measurement depths: 3% through 90% in 1% increments
registered equal-notional grids: 3%, 5%, and 10%
future horizon: 48 hours, beginning strictly after the fill snapshot
cost-adjusted recovery thresholds: 10, 25, and 50 bps
```

Build result:

```text
source perpetual symbols: 796
individual filled-level rows: 347,866
equal-notional ladder-state rows: 214,992
unique parent symbol/session events: 106,922
temporal audit: PASS
candidate/outcome joins: one-to-one
feature cutoff <= snapshot: PASS
future start > snapshot: PASS
```

Selected descriptive results at a 25 bps recovery buffer:

```text
individual 3% limit: 106,922 rows, KM 48h recovery 97.18%, KM median 2 min
individual 5% limit:  40,175 rows, KM 48h recovery 97.06%, KM median 1 min
individual 10% limit:  8,018 rows, KM 48h recovery 97.72%, KM median 1 min

3% grid through 6%: 24,718 rows, KM 48h recovery 87.80%, KM median 60 min
3% grid through 9%:  8,921 rows, KM 48h recovery 82.72%, KM median 115 min
3% grid through 15%: 2,168 rows, KM 48h recovery 78.66%, KM median 146 min

5% grid through 10%: 7,438 rows, KM 48h recovery 87.34%, KM median 34 min
5% grid through 20%: 1,127 rows, KM 48h recovery 80.94%, KM median 125 min

10% grid through 20%: 1,337 rows, KM 48h recovery 87.72%, KM median 35 min
```

The apparently high recovery probability of an individual deep limit is not a
strategy result. Multiple levels filled by one fall are dependent observations,
the blended inventory recovers materially less often, recovery varies by month,
and touching a small positive threshold says nothing about the losses of paths
that do not recover. For example, the 3% grid through 6% recovered 25 bps in
82.3% to 92.4% of observed monthly cohorts, while its mean 48-hour return was
negative in five of seven calendar-month cohorts. Deeper exact strata are sparse
and show stronger temporal instability.

Interpretation:

```text
The raw rebound phenomenon exists strongly enough to continue studying.
No trading edge, TP, protection filter, position sizing rule, or profitable bot
has been established. Stage 1 must add outcome-blind matched controls and the
mirrored short experiment before feature selection or CatBoost. Subsequent
inference must cluster dependent observations at least by symbol/time block and
must preserve the untouched 2026 partition.
```

## 2026-08-02 — blind ladder mirrored rally/short control

Status: `IS_DIRECTIONAL_CONTROL_REJECTS_LONG_SPECIFIC_RECOVERY`.

The mirror implementation and primary comparison were frozen in commit
`1865c7d3` before the full result was opened. The only directional change was
drawdown/long to rally/short; session anchors, activation delay, trade-through,
depths, equal-notional grids, costs, censoring, horizon, universe, and IS
boundary were identical.

Build/audit result:

```text
source perpetual symbols: 796
individual mirrored short-limit rows: 381,270
equal-notional mirrored ladder states: 238,585
unique mirrored parent symbol/session events: 96,956
temporal audit: PASS
OOS rows read: 0
```

Frozen primary long-minus-mirrored-short comparison at the 25 bps recovery
threshold:

```text
3% grid through 6%:
  recovery delta -4.09 pp; symbol-week cluster 95% CI -4.59 .. -3.58 pp
  signed 48h return delta +0.63 pp; 95% CI -0.35 .. +1.67 pp

5% grid through 10%:
  recovery delta -5.96 pp; symbol-week cluster 95% CI -6.89 .. -5.05 pp
  signed 48h return delta +1.29 pp; 95% CI -0.56 .. +3.30 pp

10% grid through 20%:
  recovery delta -4.95 pp; symbol-week cluster 95% CI -7.03 .. -2.90 pp
  signed 48h return delta +5.10 pp; 95% CI +1.25 .. +9.55 pp
```

The short mirror reached a small cost-adjusted recovery more often and faster,
but had a worse adverse tail. Mean 48-hour worst signed excursions for the
three primary states were approximately -19.8%, -28.3%, and -45.7% for the
short mirror versus -13.9%, -20.3%, and -35.3% for the long drawdown arm.
Therefore the mirror does not establish a short edge; it exposes the exact
high-win-rate/heavy-tail trap that a recovery-only statistic hides.

Interpretation:

```text
The claim that large drawdowns have uniquely strong recovery is rejected by the
frozen directional control. Symmetric rallies mean-reverted to a small short
threshold even more often. Neither side has positive EV proof. Continue to the
frozen prior non-drawdown matched control; do not select a side, TP, or filter
from the mirror result.
```

## 2026-08-02 — prior non-drawdown matched control

Status: `IS_GATE_0C_REJECTS_NAIVE_LONG_EDGE`.

The control builder and paired statistics were frozen in commits `2c689958`,
`9069e4a4`, and the explicit missing-activity audit amendment `125fcc57` before
the full paired result was opened.

Build/audit result:

```text
long ladder-state signals: 214,992
causal prior matched controls: 162,548
overall matched coverage: 75.61%
no-prior-pool rows: 8,397
failed-caliper rows: 44,047
IS raw quote_volume missing rows: 67,659 across five affected symbols
temporal audit: PASS
controls strictly prior: PASS
control 48h outcome resolved before signal: PASS
future start > control snapshot: PASS
OOS rows read: 0
```

Frozen primary paired results:

```text
3% grid through 6%:
  matched 18,512 / 24,718 (74.89%); eligible
  signal recovery 87.01%; control recovery 97.57%; delta -10.56 pp
  symbol-week 95% CI -11.08 .. -10.03 pp
  symbol 95% CI -11.15 .. -10.01 pp
  signed 48h return delta -2.48 pp
  week 95% CI -3.42 .. -1.60 pp; symbol 95% CI -4.52 .. -1.14 pp

5% grid through 10%:
  matched 2,896 / 7,438 (38.94%); ineligible by frozen 70% coverage gate

10% grid through 20%:
  matched 109 / 1,337 (8.15%); ineligible by frozen coverage/support gates
```

For the eligible 3→6% state, recovery delta was negative in every one of seven
calendar months. Signed-return delta was negative in five months, positive in
June, and approximately flat in December. Control reuse was negligible:
99.86% of paired 3→6% rows used a unique symbol/timestamp control.

Interpretation:

```text
The naive unconditional long ladder has no validated edge. Even after a better
limit entry, the 3→6% anomaly population recovers less often and has worse 48h
return than causally matched ordinary market states. Deeper states cannot be
declared positive or negative because a genuinely non-drawdown control cannot
be matched with adequate coverage. Gate 0C does not authorize execution or PnL
optimization. The only admissible continuation is to test whether success and
tail failure inside the anomaly population are predictably separable online.
```

## 2026-08-02 — drawdown ladder Stage 1 preregistration

Status: `FROZEN_DESIGN_PENDING_IS_FEATURE_BUILD`.

The naive unconditional long hypothesis was rejected at Gate 0C. Stage 1 does
not optimize a bot. It tests whether the 25 bps / 48-hour recovery label can be
predicted at the fill snapshot using the complete causal feature catalog.

The full CatBoost arm is paired against a 20-field structural geometry baseline
on identical weekly walk-forward rows. Parent symbol/session events are excluded
across temporal splits and receive equal total weight. Only labels resolved
strictly before a weekly freeze can train that week's models. The full arm must
pass absolute AUC, proper-score, calibration, support, and within-week
permutation gates and must improve AUC, log loss, and Brier score over the
structural arm under ISO-week clustered inference. The complete protocol and
numeric gates are recorded in `docs/strategies/drawdown_ladder_protocol.md`.

No 2026 data, trading simulation, TP/SL selection, leverage, or portfolio sizing
is admissible at this stage.

## 2026-08-03 — drawdown ladder Stage 1 result

Status: `IS_CAUSAL_CONTEXT_INCREMENTAL_HYPOTHESIS_REJECTED`.

Build and audit:

```text
214,992 causal feature rows; 213,528 complete labels
168,102 internal weekly walk-forward predictions
263 declared model features
22/22 weeks frozen and scored
temporal audit PASS; OOS 2026 rows read = 0
```

The full causal model passed all corrected absolute gates (AUC 0.71196,
log-loss improvement 0.03143, Brier improvement 0.002825, ECE 0.02151,
permutation p=0.001). However, the frozen 20-field structural control was
strictly better (AUC 0.74384, log loss 0.22545, Brier 0.05950).

The pre-registered full-minus-structural comparison failed all nine incremental
gates. AUC delta was -0.03188 (ISO-week 95% CI -0.04942 to -0.00262), log-loss
improvement was -0.00989 (CI -0.01961 to -0.00124), and Brier improvement was
-0.000566 (CI -0.001831 to +0.000426). The broad causal context therefore added
temporal overfit rather than stable signal.

Post-gate diagnostics found failures across 594/605 symbols, with only 7.86% of
failures in the top ten contributors. The full model improved log loss for only
22.7% of 450 adequately supported diagnostic symbols and was worse in four of
five months and four of five sessions. Geometry dominated importance; broad
market/BTC features received substantial train importance but harmed frozen
weeks. These diagnostics have no filter authority.

No PnL, TP/SL, leverage, protection, or portfolio optimization is authorized.
The pristine 2026 partition remains untouched.

## 2026-08-03 — structural protection EV preregistration

Status: `FROZEN_POST_SELECTION_DESIGN_PENDING_IS_EV_OPEN`.

The absolute structural probability model passed, while the broader causal arm
failed against it. A separately governed post-fill inventory decision is now
frozen before opening 48-hour return evidence: hold at calibrated probability
at least 0.92, otherwise exit at the snapshot close, versus unconditional 48h
hold. The primary exact state is 3% grid through 6%; 5→10 and 10→20 are declared
secondary states. Primary all-in cost is 25 bps with 10/50 bps sensitivities.

Both absolute policy EV and improvement over hold must have positive adjusted
one-sided lower bounds under separate ISO-week and symbol cluster bootstraps.
The familywise alpha is 0.05/(2 viewed arms × 2 endpoints × 3 viewed states),
with 20,000 draws. Support, 4-month stability, CVaR5, and top-1%-removal gates
are also frozen. No physical TP/SL, funding, concurrency, sizing, leverage, or
portfolio simulation is part of this experiment.

This is explicitly post-selection IS evidence. Passing can authorize the next
mechanics stage but cannot establish deployable edge without frozen forward
confirmation. Failing closes the branch without same-sample retuning. The 2026
partition remains physically untouched.

## 2026-08-03 — structural protection EV result

Status: `IS_STRUCTURAL_PROTECTION_EV_GATES_FAIL`.

The immutable causal join contained 27,560 rows and passed every timing and OOS
audit. The primary 3→6 state had 19,821 rows (12,228 HOLD; 7,593 EXIT). At the
frozen 25 bps cost, policy mean return was -1.4436% versus -1.2718% for
unconditional 48-hour hold, so the protection worsened mean EV by 0.1718
percentage points. Policy return was positive on 25.59% of rows and 29.05% of
148 trading days; all five months were negative.

Adjusted one-sided lower bounds were negative by both ISO week and symbol for
absolute policy EV and improvement over hold. The policy improved CVaR5 from
-38.13% to -32.37%, but this tail reduction did not compensate for the worse
mean. Secondary 5→10 and 10→20 states were also negative, and all states stayed
negative at the optimistic 10 bps sensitivity.

This is a clean rejection, not a tuning invitation. The fixed policy has no
validated EV, the execution/portfolio stage is forbidden, and neither the 0.92
cutoff nor the exact states may be retuned on these rows. OOS 2026 remains
untouched.

## 2026-08-03 — mirrored-rally short continuation preregistration

Status: `FROZEN_POST_SELECTION_MIRROR_DESIGN_PENDING_STAGE1_BUILD`.

Naive mirrored short hold is already negative in terminal-return terms: the
3→6 state averages -1.46% signed return before costs and has a severe adverse
tail, despite frequent small recovery touches. A separate mirror continuation
hypothesis is now frozen rather than inverting the rejected long model.

Core Stage 1 is made direction-explicit. Short source validation requires the
frozen mirrored-rally Stage 0; rally geometry has its own field and schema; the
short HOLD-minus-EXIT target subtracts `-(snapshot_close/entry-1)` from the
signed short 48-hour return. Raw causal price, flow, BTC, and breadth fields
retain their documented market direction.

The exact 3→6 state is primary. Structural and full challenger arms inherit the
frozen weekly parent-exclusive CatBoost, calibration, permutation, and paired
gates from continuation nature. No EV, score inversion, coin filter, execution,
or 2026 access is authorized.

## 2026-08-03 — mirrored-rally short continuation result

Status: `IS_MIRRORED_SHORT_CONTINUATION_PREDICTION_REJECTED`.

Direction-explicit mirror Stage 1 built 238,585 rows and 263 features across all
796 source perpetuals. The complete primary-state dataset contained 27,795
rows; both arms scored 21,951 internal OOS rows over 22/22 weeks. Every temporal
and OOS audit passed, with no 2026 access.

Short HOLD beat immediate snapshot EXIT on 60.55% of rows, but this high base
rate was not separable. Structural AUC was 0.4636, log-loss gain -0.00905,
Brier gain -0.00426, and ECE 0.0765. Full causal AUC was 0.4907, log-loss gain
-0.00750, Brier gain -0.00353, and ECE 0.0594. Both high-probability cohorts
failed the registered observed-rate/calibration contract.

Proper-score improvement was negative in all five months for both arms. Only
34 symbols had at least 100 diagnostic rows, and isolated symbol improvements
have no filter authority. The mirror therefore fails both naive terminal EV
and causal selection. No magnitude EV, score inversion, filters, or execution
stage is authorized; OOS 2026 remains untouched.

## 2026-08-03 — HOLD-versus-EXIT continuation preregistration

Status: `FROZEN_POST_SELECTION_PREDICTION_DESIGN_PENDING_BUILD`.

The rejected 0.92 recovery policy is not retuned. A new target asks whether the
48-hour terminal mark improves upon the already observable snapshot-close exit
from the same blended entry. Its binary nature label is
`future_return_2880m - snapshot_close_to_entry > 0`, with resolution fixed at
the full 48-hour endpoint.

The exact 3→6 state is the sole primary population. Weekly parent-exclusive
CatBoost uses the structural arm as the pre-registered primary model and the
complete causal catalog as a challenger. Absolute AUC, proper-score,
calibration, high-probability support, within-week permutation, and paired
incremental gates are frozen in the strategy protocol. The primary null alpha
is adjusted for two viewed arms and two endpoints.

This stage contains no EV threshold, PnL, funding, physical exit, leverage, or
portfolio simulation. It can only authorize a later magnitude-aware EV test.
No 2026 data may be read.

## 2026-08-03 — HOLD-versus-EXIT continuation result

Status: `IS_CONTINUATION_NATURE_PREDICTION_REJECTED`.

The primary-state dataset had 24,567 complete rows. Both CatBoost arms scored
19,821 internal OOS rows over 22/22 frozen weeks with all temporal audits PASS
and no 2026 access. HOLD outperformed EXIT on 46.97% of rows.

Structural-primary AUC was 0.4816, with log-loss improvement -0.01336, Brier
improvement -0.00629, and ECE 0.0849. Its p≥0.60 cohort contained only 107 rows
and succeeded 33.64% of the time. Full causal was worse: AUC 0.4753, log-loss
improvement -0.03702, Brier improvement -0.01683, ECE 0.1190; its p≥0.60 cohort
succeeded only 45.85% of the time.

Both arms had AUC below 0.5 in all five months. Only 18 symbols had at least 100
diagnostic rows, and proper-score improvements were isolated to one structural
and two full-model symbols, which cannot become filters. Small permutation
p-values merely show the models were less poor than within-week shuffled labels;
they do not override failed absolute discrimination, proper scores, calibration,
and support.

The long continuation-selection branch is closed. Score inversion, threshold
search, coin selection, magnitude EV, and execution simulation are forbidden on
these rows. OOS 2026 remains untouched.
