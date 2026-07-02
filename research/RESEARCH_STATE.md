# Research state

Last updated: 2026-06-29.

Architecture boundary: `legacy_quarantine` is historical reference only; the
active `anomaly_science` core and pump-fade strategy must not import it or depend
on its runtime behavior.

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
online 1m anomaly state builder in the strategy-neutral compatibility core
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
- Pump-fade lifecycle artifacts are now physically separated: the canonical output is `pump_fade_online_state_v1`, future-derived outcomes live in a sibling offline-label artifact, and research consumers use an explicit one-to-one supervised join. Future-tail invariance is covered by regression tests. This is an architecture/protocol result, not evidence of predictive edge.
- Pump-fade builds now emit canonical per-symbol quality, per-feature missingness, and fail-closed rejection-summary artifacts. Missing OI positioning states remain `NaN` behind `oi_available=false`; absence of OI is not a model state. This is data-governance capability, not OI evidence.
- Generic coarse-regime inference now exists before ML. An unfrozen dirty-worktree full-history diagnostic found one development-replicated top-1% `rel_vol_phase` regime under matched controls, re-estimated clustered CI, shuffled labels, BY-FDR, and month/symbol stability. It must be regenerated from the separated lifecycle dataset at a clean commit before it can enter the evidence ledger as a frozen development result.
- Generic horizon-free binary weekly WFA now exists with actual label-resolution eligibility, event-exclusive chronological fit/validation/calibration, frozen weekly CatBoost plus isotonic models, causal weekly baselines, reliability/null artifacts, and pre-registered gates. The pump-fade t0 protocol is registered but has not yet earned predictive evidence; the full regenerated lifecycle run is the next gate before any later-state or EV work.
- Full separated-lifecycle development WFA is now complete. T0 failed AUC, log-loss, and high-confidence gates. Separate causal new-high ordinal models 1-5 showed modest OOS ranking/calibration gains, but no ordinal emitted a calibrated probability at or above 0.70; ordinals 8/13 degraded. These are immutable negative development results. EV, trade simulation, and PnL optimization remain blocked.
- A paired complete-case OI increment on t0 and ordinals 1-2 is also negative: delta AUC stayed between +0.0005 and +0.0028, all week-cluster intervals crossed zero, and no high-confidence row appeared. OI features are not admitted. The next registered information family is causal reference-market context, built in Core rather than embedded in pump-fade strategy logic.
- The generic Core BTC/ETH reference-market context family has now been tested and rejected too: it degraded t0/ordinal 1 and added only a small non-significant ordinal 2 improvement. No context arm emitted `p>=0.70`. Existing local market/OI context is exhausted; the next evidence-bearing input requires a new archived positioning stream (top-trader/long-short/taker ratios) or other genuinely new data, not model retuning.
- BTC/ETH daily top-trader/global/taker ratio archives were then acquired with full coverage and a conservative publication lag, but that paired family also failed. Reference positioning is excluded. The next distinct hypothesis is event-scoped symbol-specific ratios for the pumped asset; this is new data acquisition, not reuse or retuning of failed reference features.
- Registered single/interaction atlas support now exists at T0 and new-high ordinals 1-2. Core evaluates both singles and explicitly strategy-declared pairs/triples, logs every screened hypothesis, applies joint BY-FDR within each variant and Bonferroni across variants, and never generates an unrestricted Cartesian search. No interaction passed all gates. A narrow efficient-path/upper-wick candidate had large matched deltas but failed its pre-registered month/symbol support gates and remains rejected pending genuinely new forward data.
- Online-state v2 fixed a causal defect where `last_prior_size` could expose an unresolved prior event's offline-finalized peak. It introduced `pump_fade_event_memory_v1`: a 48h resolved-event memory with relative price structure, flow/morphology summaries, three fixed prior slots, explicit missingness, and recurrence-chain split isolation. The paired T0/ordinal1/ordinal2 WFA was negative: T0 delta AUC +0.00152 with CI crossing zero; later states degraded. No `p>=0.70` row appeared, so event memory is not admitted.
- Event-scoped same-symbol positioning is now acquired and joined causally through generic Core: 20,998/20,998 symbol-day archives succeeded, 1,869 invalid non-positive ratio rows were excluded explicitly, and complete-case coverage exceeds 99.6%. The preregistered 19-feature paired family failed: T0 delta AUC +0.00332 and ordinal 2 +0.00241 were sub-minimum with intervals crossing zero; ordinal 1 degraded; no `p>=0.70` row appeared. Symbol positioning is excluded, and ratio/lag/subset mining on this OOS period is forbidden.
- Pump-fade online state is now `pump_fade_online_state_v3` / feature schema `pump_fade_market_mechanics_v4` and emits a causal 11-coordinate `pump_fade_cvd_path_v1` from closed event candles. The full paired weekly WFA rejected the family: T0 delta AUC -0.00099, ordinal 1 -0.00048, and ordinal 2 +0.00055 with every interval crossing zero and every incremental gate failing. No variant emitted `p>=0.70`. CVD is not admitted; post-hoc window/feature/stage mining on this OOS period is forbidden.
- Core now acquires and causally joins same-symbol 1m premium-index plus published funding history. The preregistered 16-coordinate `pump_fade_perp_crowding_v1` produced the first consistent incremental T0 result: delta AUC +0.00934 (CI +0.00467..+0.01530, p=0.004), log-loss +0.00231, and Brier +0.00114. It still failed admission because delta AUC missed the registered +0.010 minimum, absolute AUC was 0.597<0.600, and no `p>=0.70` row existed; ordinal 1 was sub-minimum and ordinal 2 degraded. The family is a forward-confirmation candidate only, not a production feature, and must not be retuned on this OOS period.
- The primary discovery path is now broad cross-fitted phenotype search rather than one-family-at-a-time global-AUC iteration. On 288 causal strategy features (430 explicit transformed coordinates), 29,151 direct/OOF-surrogate leaves produced 917 fold candidates and 17 distinct frozen rules. Nineteen calendar-block null searches produced zero frozen rules each. Fourteen rules passed later development verification and jointly covered 39.1% of verification events, but frozen probabilities were only 0.60..0.676 and later rates 0.54..0.594; no rule reached the registered 0.70 high-probability gate. These are useful medium-probability development phenotypes, not the requested high-probability production output.
- Frozen phenotypes are not disposable binary pass/fail rows. A governed follow-up registry retains all 17 with their unchanged status, failure mode, mechanism features, priority, evidence snapshot, and admissible next experiment. This preserves research dynamics without converting exploratory reuse of the viewed verification period into false confirmation.
- Online state v4 / feature schema v5 adds a bounded causal path-dynamics family computed only from the existing closed 1m OHLCV, trade-count, taker-flow, and OI-enriched cache. It also adds label-only pump/fade duration geometry to nature schema v2. These are implementation capabilities only; the family has no predictive evidence until a newly registered paired walk-forward experiment is run.
- Event-scoped Binance Futures `aggTrades` were backfilled for all 693 event symbols (19,814 symbol-days, 0 missing/0 failed, 3.45 GB of minute sidecars) and reduced to a causal 30-feature `pump_fade_aggtrades_dynamics_v1` family (trade-size distribution, aggressor run/flip sequencing, inter-arrival burstiness, and buy/sell price-impact exhaustion) gated by a boolean `aggtrades_available` complete-case flag. This is the raw-tape process signal every prior OHLCV/OI/CVD family could only see as a consequence. The full preregistered paired weekly WFA rejected it as well: T0 delta AUC +0.00014 (CI -0.0031..+0.0033, sign-flip p=0.474), ordinal 1 -0.00021 (p=0.528), ordinal 2 -0.00394 (p=0.912); every 95% interval crossed zero, every incremental gate failed, and no arm emitted `p>=0.70`. aggTrades is not admitted; window/feature/stage mining on this OOS period is forbidden. This exhausts the free Binance data sources — the remaining process signals (true L2 order-book dynamics, liquidations, cross-exchange lead/lag) require a paid historical vendor, and no tradeable free-data fade edge at `p>=0.70` with adequate coverage has been demonstrated.
- The LONG side (pump continuation riding) was then tested as a clean separate strategy (`strategy/pump_long/`): the same weak close-race classifier plus convex continuation payoff plus a confirmed-swing-low structural trail, on the thesis that convexity could be EV-positive where the capped short payoff was not. Registered mechanics: next-1m-open entry with pessimistic slippage, 20bps round trip, entries eligible until price CONFIRMS closes below half the pump height for max(3min, 10% of pump age). A phase-0 protocol was frozen on the development period only (`research/pump_long_ev_v1.json`): first-pump-in-48h stratum, event_base stop, classifier gate p_cont>=per-week median from a pooled-ordinal weekly WFA, one position per recurrence chain, and deliberately NO d_max cap (development quartiles put the convexity in far-base large pumps, so a distance cap would delete the edge). Development was encouraging — oracle first-in-48h EV +2.5% (CI above zero), blind +1.2%, and pseudo-OOS classifier selection lifted positive-week share 21%->43% at +2.8% EV. The single true-OOS (2026-01..06) evaluation rejected it: the frozen realizable rule returned EV -0.54%/trade (CI -0.73..-0.35, entirely below zero), 16% positive weeks, EV without the top 5% -1.23%, negative at every cost stress — indistinguishable from blind (-0.45%), so the classifier added no OOS skill. The OOS oracle held only +0.33% (CI +0.12..+0.53), an order of magnitude below development: the continuation convexity is real but faint out of sample, and far short of the ~0.9% gap that imperfect 0.66-AUC classification plus costs consumes. The long side is not admitted. Both directions of the pump-fade close race now fail out of sample on free data; the honest options are a paid microstructure vendor or a genuinely different strategy, not further tuning of this label on this OOS period.
