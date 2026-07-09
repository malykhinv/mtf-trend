# Triple-tap frozen candidate IS audit

Scope: IS only. OOS was not touched.

## Frozen hypothesis

- Policy: `structure_0.50_part_0.50_runner_initial_stop_be_0.50_inactive_6h`.
- Entry: confirmed close above point-in-time breakout level.
- Exit: 50% at structural 50% measured-move target; 50% runner to original structural target.
- Protection: BE after +0.50R; 6h inactivity exits losers or arms BE for non-losers.
- Selection: causal q70 from prior forward-scored weeks only.
- Portfolio: max 3 concurrent, one position per symbol, risk sizing capped at 1x notional.

## Baseline IS result

| Metric | Value |
|---|---:|
| Trades | 17 |
| Win rate | 41.2% |
| Trading days | 14 |
| Positive days | 6 (42.9%) |
| Return | -2.0% |
| Max DD | -6.8% |
| Profit factor | 0.79 |
| Median duration | 0.8h |
| P90 duration | 6.0h |
| Trades/day mean | 1.21 |
| Trades/day max | 3 |

## Risk stress with 1x notional cap

| Risk target | Effective risk p50 | Return | Max DD | PF |
|---:|---:|---:|---:|---:|
| 2% | 2.00% | -2.0% | -6.8% | 0.79 |
| 3% | 3.00% | -2.4% | -9.5% | 0.83 |
| 4% | 4.00% | -1.1% | -10.9% | 0.93 |
| 5% | 4.19% | +0.2% | -11.7% | 1.01 |

## Monthly stability

| Month | Trades | PnL | Net R | PF |
|---|---:|---:|---:|---:|
| 2025-10 | 5 | $+182 | +0.9 | 2.34 |
| 2025-11 | 5 | $-280 | -1.4 | 0.42 |
| 2025-12 | 7 | $-103 | -0.5 | 0.71 |

## Honesty checks

- Real score/realised-R Spearman: -0.073.
- Shuffled-train q70 control return: -3.5%.
- Random same-count median return: -3.7%.
- All listed time-contract checks passed.

Full tables are in the JSON artifact.
