# Anomaly Science Rebirth Plan

This document is the project-level execution order for the anomaly-science rebuild.

The old repository is treated as legacy reference material only. New code must be
implemented as isolated, typed, reusable modules. Backtest, shadow live, and live
must be built from the same domain contracts and decision modules instead of being
separate strategy implementations.

## Non-negotiable rules

1. Work on one stage at a time.
2. Do not start a later stage until the previous stage has a passing Definition of Done.
3. Do not import from `legacy_quarantine` in new code.
4. Do not copy old strategy rules as the basis of the new methodology.
5. Legacy code may be read only as reference for useful practices.
6. No silent fallbacks, monkeypatches, post-processing fixes, global suppressors, or string-matching hacks.
7. Features must use only data available at or before `snapshot_time`.
8. Labels and future paths must use only data strictly after `snapshot_time`.
9. Do not optimize for PnL before prediction, calibration, timing, and EV are proven.
10. Backtest and live must share the same contracts, state builders, feature builders,
    prediction interfaces, and decision modules.

## Target architecture

```text
src/anomaly_science/
  contracts/      # typed domain contracts, schemas, time rules
  data/           # data ports, adapters, cache readers, quality checks
  universe/       # point-in-time symbol universe
  events/         # broad anomaly detector
  state/          # online 1m state builder
  future/         # raw future paths and scenario labels
  features/       # feature catalog and feature builders
  atlas/          # discovery surfaces and context splits
  validation/     # walk-forward, purging, calibration, placebo
  decision/       # timing, RR feasibility, expected utility
  simulation/     # simplified pessimistic trade simulation
  live/           # shadow/live adapters using shared modules
  artifacts/      # artifact writers/readers and run manifests
  audit/          # protocol, data, temporal, and parity audits
```

The core direction is:

```text
historical/live data
  -> data quality gates
  -> point-in-time universe
  -> broad anomaly detector
  -> online 1m state builder
  -> future path builder
  -> nature atlas
  -> walk-forward calibrated prediction
  -> decision timing / RR feasibility
  -> expected utility
  -> simplified trade simulation
  -> shadow live
  -> production live
```

---

# Stage 0 — Legacy quarantine

## Goal

Move the current mixed repository into a quarantine area and start the new system
from a clean source root.

## Required actions

- Move old project files into `legacy_quarantine/old_repo/`.
- Create `legacy_quarantine/README.md` with reference-only rules.
- Create clean roots: `src/anomaly_science/`, `tests/`, `docs/`, `research/`.
- Create a minimal new `main.py` that routes only into `anomaly_science.cli`.
- Add a `doctor` command that proves the new bootstrap works.
- Add a test that forbids imports from `legacy_quarantine` and old top-level packages.

## Forbidden

- Do not fix old strategy logic.
- Do not preserve old root imports as shared code.
- Do not make new modules depend on old `research_tools`, `strategy`, `data`, or `cli`.

## Definition of Done

- `python main.py doctor` passes.
- `python -m compileall main.py src tests` passes.
- New code has no imports from legacy paths.
- One commit contains quarantine only, no methodology implementation.

---

# Stage 1 — Project contracts and artifact schemas

## Goal

Define the stable domain contracts before implementing logic.

## Required modules

```text
src/anomaly_science/contracts/time.py
src/anomaly_science/contracts/market.py
src/anomaly_science/contracts/events.py
src/anomaly_science/contracts/state.py
src/anomaly_science/contracts/future.py
src/anomaly_science/contracts/features.py
src/anomaly_science/contracts/artifacts.py
```

## Required decisions

- Canonical timestamp type and timezone policy.
- Candle schema for 1m and 5m OHLCV.
- Closed 5m open-interest schema.
- Liquidation-flow schema.
- Symbol metadata and point-in-time universe schema.
- Event schema.
- Online state schema.
- Future path schema.
- Feature catalog schema.
- Run manifest schema.

## Definition of Done

- Schemas are typed and documented.
- Artifact names and required columns are fixed for MVP 1.
- Temporal invariants are explicit:
  - `feature_cutoff_time <= snapshot_time`
  - `future_start_time > snapshot_time`
- Unit tests validate the basic schema invariants.

---

# Stage 2 — Data ports and cache adapters

## Goal

Build clean data access boundaries without importing legacy internals.

## Required modules

```text
src/anomaly_science/data/ports.py
src/anomaly_science/data/cache.py
src/anomaly_science/data/readers.py
src/anomaly_science/data/availability.py
```

## Required behavior

- Read 1m candles.
- Read 5m candles.
- Read closed 5m open interest if available.
- Read liquidation flow if available.
- Read symbol metadata.
- Report missing data explicitly.

## Forbidden

- No generic fallback from missing fields into proxy fields.
- No silent conversion of bad payloads into empty data.
- No dependency on legacy private client fields.

## Definition of Done

- Data ports are interfaces or explicit boundary functions.
- Cache adapters are replaceable.
- Missing data is represented as data quality condition, not market signal.
- Tests cover missing columns, bad timestamps, duplicates, and empty data.

---

# Stage 3 — Data quality gates

## Goal

Reject or mark invalid data before any research result is produced.

## Required artifact

```text
anomaly_data_quality.csv
```

## Required checks

- Missing candles.
- Duplicate candles.
- Bad timestamps.
- Timezone misalignment.
- Non-positive prices.
- Impossible returns.
- Zero-volume anomalies.
- OI gaps.
- Liquidation data gaps.
- Symbol listing gaps.

## Definition of Done

- Data quality can return PASS/WARN/FAIL.
- Research pipeline refuses to interpret results when critical checks FAIL.
- Data quality output is deterministic and saved per run.

---

# Stage 4 — Point-in-time universe

## Goal

Prevent survivorship bias and future-universe leakage.

## Required artifact

```text
symbol_universe_by_day.csv
```

## Required columns

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

## Definition of Done

- Static current-universe research is forbidden by audit.
- Delisted historical symbols are not excluded when data exists.
- Universe decisions are reproducible from saved artifacts.

---

# Stage 5 — Broad anomaly detector

## Goal

Detect broad market activity moments, not trade setups.

## Required artifact

```text
anomaly_events.csv
```

## Required properties

- Detector must not know future outcomes.
- Detector should be broad enough to include one-shot spikes, bursts, grinds,
  volume-only anomalies, range expansions, breakouts, noise pumps, session bursts,
  and market-wide impulses.

## Required columns

```text
event_id
symbol
event_start_time
event_detection_time
seed_time
seed_open
seed_high
seed_low
seed_close
initial_move_pct
initial_volume_zscore
initial_quote_volume_zscore
initial_trade_count_zscore
