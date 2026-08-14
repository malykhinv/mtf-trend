# Adverse-Long Hazard v1 — Peak-Timing Implementation Correction

**Recorded:** 2026-08-05, after the first v1 artifact and before the corrected
trade-timing metrics were computed.

The frozen v1 protocol requires a warning to occur **before** a catastrophic
trade's adverse peak. The first implementation compared warning availability
with the peak bar's *availability time*. That incorrectly admitted a warning
created by the close of the same 1h bar whose intrabar high defined the peak.
Such a warning is not executable before the adverse move and therefore does not
satisfy the written protocol.

The corrected implementation:

- records both the peak bar's open time and availability time;
- admits a warning only when `warning_snapshot_time <= peak_bar_open_time`;
- measures warning lead to `peak_bar_open_time`, not to peak-bar availability;
- leaves every detector feature, threshold, label, horizon, source row, and
  acceptance gate unchanged.

The artifact directory `hourly_adverse_long_audit_v1` retains the original
invalid timing report for provenance. Corrected artifacts are written to
`hourly_adverse_long_audit_v1_corrected` and are the only admissible v1 result.

