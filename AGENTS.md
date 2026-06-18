# AGENTS.md

Project instructions for OpenAI Codex / coding agents working in this repository.

This file is the local analogue of ChatGPT Project Instructions. Read it before
making any changes. If a more specific `AGENTS.md` exists in a subdirectory, the
more specific file wins for files under that directory.

## Project identity

Repository / working branch context:

```text
codex/pno-anomaly-continuation-lab
```

Project goal:

```text
Build anomaly_science as a clean scientific research core for anomaly-driven
crypto trading research.
```

Current strategic direction:

```text
Do not repair the old architecture. Legacy code may be read as reference only.
New production/research code must be independent, typed, testable, and reusable.
```

## Non-negotiable development rules

### No hacks

No workarounds unless the user explicitly approves them.

Forbidden by default:

```text
monkeypatch
global filters
suppress/rewrite/post-processing layers
string-matching hacks
silent fallback
temporary bypasses
masking symptoms instead of fixing root cause
```

Required solution shape:

```text
explicit
minimal
typed
maintainable
understandable without hidden magic
```

If the clean fix is larger than the user expects, say so and propose the clean
plan. Do not hide a workaround inside a patch.

### Legacy quarantine boundary

Rules:

```text
legacy_quarantine may be inspected as historical reference
new code must not import from legacy_quarantine
new code must not depend on legacy_quarantine runtime behavior
```

If old behavior is needed, extract the idea and reimplement it behind a clean
typed boundary.

### Core / strategy separation

The project is a multi-strategy research platform.

Core Engine owns:

```text
data contracts
artifact schemas
data quality
point-in-time universe
online state builder
future paths
atlas
walk-forward ML
calibration
EV
simulation infrastructure
audits
```

Strategy modules own:

```text
trigger definition
strategy horizon
ATR-normalized TP/SL parameters
strategy-specific relaxed geometry features
```

Forbidden:

```text
strategy-specific if/else inside Core Engine
changing Data Engine or ML Engine for one strategy-specific hypothesis
mixing strategy-specific feature engineering into universal features without schema versioning
```

## Scientific methodology constraints

Primary research sequence:

```text
1. prove whether future anomaly nature is predictable online
2. prove whether predictability appears early enough
3. prove positive EV
4. only then add trade simulation, shadow live, and production execution realism
```

Do not jump directly to PnL optimization if prediction calibration, timing, and
EV have not passed audits.

### Time contract

For every research row:

```text
features <= snapshot_time
labels   >  snapshot_time
```

Required invariants:

```text
feature_cutoff_time <= snapshot_time
future_start_time > snapshot_time
```

Forbidden:

```text
future high/low in features
final event high as known high
future label as filter
failed long trade as short signal
fit scaler/model/threshold on full period
train and evaluate on the same days
```

### ML protocol

Use Weekly Walk-Forward for heavy ML models unless there is a pre-registered,
statistically meaningful reason to do otherwise.

Rules:

```text
CatBoost and Isotonic Regression are trained once per calendar week per active strategy
daily OOS days inside the week use frozen weekly weights and calibrators
OOS days must not update CatBoost weights
