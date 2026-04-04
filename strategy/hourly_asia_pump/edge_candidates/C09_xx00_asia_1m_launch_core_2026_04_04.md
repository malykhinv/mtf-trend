# C09 - XX00 Asia 1m Launch Core 2026-04-04

Status: `Core / validated`

This note fixes the first reliable early-entry XX:00 long candidate after an explicit optimistic-bias check on 2026-04-04.

## Rule

- candidate_id: `C09_xx00_asia_1m_launch_core`
- rule_id: `long_launch_r010_c65_v04_p0_rr20`
- primary cohort: `long_union`
- confirmation cohort: `long_hot_range`
- entry timing: minute 1 close, i.e. `00:01` after the XX:00 launch minute
- conditions:
- minute-0 return >= `+1.0%`
- minute-0 close position >= `0.65`
- minute-0 volume ratio >= `4.0x`
- `prev240` break is not required
- stop: below minute-0 low
- target: `2R`

## Why It Passes Bias Check

- Rank `1` across all tested Asia families in both tracked cohorts.
- Walk-forward check kept selecting the same rule in every available anchored test month.
- Current `long_union`: `85` cases, `23` trades, `82.61%` win rate, `+4.46%` mean trade, `+129.31%` equity return at `5%` risk, `4.84%` max drawdown.
- Old `long_union`: `1` trade, `+6.21%` mean return.
- Current `long_hot_range`: `59` cases, `14` trades, `92.86%` win rate, `+5.07%` mean trade, `+98.74%` equity return at `5%` risk, `2.91%` max drawdown.
- Symbol concentration stayed controlled on current data: top-1 share `25.8%` in `long_union`, `16.0%` in `long_hot_range`; top-3 share stayed below `50%` in both.
- Current month distribution stayed clean: `9/9` positive active months in both tracked cohorts.

## Scope And Limits

- Validated status applies to Asia only.
- Europe and America transfer results are promising, but they come from a broader loose XX:00 anomaly universe rather than the same Asia regime-construction layer.
- Combined portfolio numbers across sessions are exploratory and should not be treated as final production stats yet.

## Cross-Session Transfer Scan

- Europe selected the same rule as `best_ready` on `all_loose_xx00`.
- Europe current: `13079` cases, `1039` trades, `72.09%` win rate, `+2.60%` mean trade, `+12.46x` equity return at `5%` risk, `17.38%` max drawdown.
- Europe old: `119` trades, `+2.51%` mean trade.
- America selected the same rule as `best_ready` on `all_loose_xx00`.
- America current: `9943` cases, `643` trades, `58.94%` win rate, `+1.90%` mean trade, `+14.96x` equity return at `5%` risk, `15.26%` max drawdown.
- America old: `134` trades, `+0.88%` mean trade.

## Practical Use

- Production status: keep this candidate as the current Asia XX:00 core long.
- Research status: keep Europe and America as exploratory overlays that still need additional untouched holdout checks before being treated as stable edge layers.
