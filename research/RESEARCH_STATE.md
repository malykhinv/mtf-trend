# Anomaly Research State

Compact project memory. Detailed rules live in Project Instructions.

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P123-P128 proposed locally on top of ZIP-derived source
Last active patch: P128 remove PNO active code path
Updated: 2026-05-11
```

PNO is retired from the active source tree. The project direction is anomaly-first: anomaly nature/category research, anomaly continuation backtests, and strict REST-only micro-live validation.

---

## 2. Active system thesis

```text
abnormal activity -> nature/category check -> controlled continuation -> executable entry -> managed exit
```

The edge is not assumed. The current task is to identify which anomaly categories are tradable, which are exhausted/fake/thin/late, and which should be rejected before expensive backtests.

---

## 3. Reliability priority

```text
data availability > leakage safety > execution realism > edge stability > parameter optimization
```

No strong conclusion about anomaly nature without real trade-count and quote-volume evidence available at decision time.

---

## 4. Current code status

```text
Active CLI:
fetch-data
update-cache
run-anomaly-lab
run-anomaly-live
check-quality
clear-cache
```

Removed from active source:

```text
strategy/pno/
cli/pno_diagnostics.py
run-backtest PNO path
plot-backtest PNO path
pno-stage command
PNO strategy factory path
```

Historical research logs may still mention PNO, but they are no longer an executable strategy contract.

---

## 5. Open risks

1. Anomaly live/backtest logic still needs robustness evaluation across months and regimes.
2. Micro-live execution has real slippage, spread, partial fills and operational failure modes.
3. OI/derivatives context availability can limit category classification.
4. `main.py` still has a known Linux `ctypes.windll` import issue; intentionally not fixed in P128.
5. Historical PNO artifacts/log entries should not be used as current source of truth.

---

## 6. Next best step

Run a clean import/CLI smoke after applying P123-P128, then run a small anomaly-lab sample on cached data:

```bash
python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py
python main.py run-anomaly-lab --days 3 --timeframe 1m --run-entry-grid false
```
