# Anomaly Research State

Compact project memory. Detailed rules live in Project Instructions.

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P123-P129 proposed locally on top of ZIP-derived source
Last active patch: P129 purge retired strategy history from active research memory
Updated: 2026-05-11
```

The project direction is anomaly-first: anomaly nature/category research, anomaly continuation backtests, and strict REST-only micro-live validation.

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

Runtime strategy path:

```text
research_tools/anomaly_config.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
research_tools/charting.py
```

---

## 5. Open risks

1. Anomaly live/backtest logic still needs robustness evaluation across months and regimes.
2. Micro-live execution has real slippage, spread, partial fills and operational failure modes.
3. OI/derivatives context availability can limit category classification.
4. `main.py` still has a known Linux `ctypes.windll` import issue; intentionally not fixed in the current cleanup stack.
5. Historical local artifacts may contain stale compiled files; they are ignored by git and should be deleted locally.

---

## 6. Next best step

Run a clean import/CLI smoke after applying P123-P129, then run a small anomaly-lab sample on cached data:

```bash
python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py
python main.py run-anomaly-lab --days 3 --timeframe 1m --run-entry-grid false
```
