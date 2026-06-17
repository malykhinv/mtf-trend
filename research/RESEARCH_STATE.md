# Research state

Current phase: anomaly_science rebirth.

Active rule:
- `legacy_quarantine` is reference-only.
- New code must not import legacy modules.
- First target is MVP 1: dataset/state/future-path/audit, not trading.
- Current implemented slice after applying Patch 9: data source boundary, data quality, point-in-time universe skeleton, broad anomaly events, online 1m anomaly state, raw future paths, descriptive anomaly nature atlas, descriptive future-nature outcome labels, and first walk-forward calibrated baseline prediction.
- Future paths remain raw outcomes.
- Atlas outcome bins are descriptive discovery bins only, not decision rules, EV, PnL, or trade simulation.
- Outcome labels are descriptive scenario targets for later walk-forward prediction calibration, not trading labels.
- Placebo/control tests, decision timing, EV, trade simulation, shadow live, and production live are still intentionally absent.

Current commit: UNKNOWN until committed and verified with `git rev-parse HEAD`.
Last verified base snapshot for Patch 9 generation: Patch 8 applied on top of Patch 7 and `project_20260617_104034.zip`; GitHub head not checked in this environment.
