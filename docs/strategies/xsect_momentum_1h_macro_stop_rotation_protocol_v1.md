# Cross-Sectional Quality — Macro-Stop Rotation Follow-up v1

**Frozen:** 2026-08-05, after regime-atlas v1 and before macro-stop results.

Regime-atlas v1 found no fully admissible candidate because otherwise stable
directional filters still breached the whole-event high in roughly 30–33% of
the next 24h paths. This follow-up does not tighten a stop or search new return
thresholds. It tests exact, visibly major market highs as wider anchors.

## Frozen directional candidates

1. `btc_slow_rotation_eth_strength`:
   BTC 7d mean below its 30d mean AND ETH outperforms BTC over 7d.
2. `eth_strength_coin_weakness`:
   ETH outperforms BTC over 7d AND the candidate coin underperforms BTC over 7d.
3. `btc_strong_alt_weak_isolated`:
   BTC above its 30d mean, fewer than 40% of the point-in-time universe positive
   over 7d, and no more than 10% of active shorts simultaneously in warning.
4. `asia_session`:
   re-entry snapshot between 00:00 and 07:59 UTC; retained as a deliberately
   non-standard calendar hypothesis from the atlas.

## Exact macro-stop anchors

- `event_high`: known high of the complete observed anomaly event;
- `external_7d_high`: maximum of event high and the exact preceding 7d market
  high known at re-entry;
- `external_30d_high`: maximum of event high and the exact preceding 30d market
  high known at re-entry.

The wider anchors are real historical highs, not percentage/ATR offsets. Every
variant reports stop distance and the position-size multiplier required to keep
the same monetary risk as the event-high anchor.

## Exploratory acceptance

For every year 2023–2025: at least 30 events, negative mean 24h return, negative
return share above 50%, and macro-stop breach no greater than 25%. The pooled
95% bootstrap upper bound of mean return must be below zero. Results remain
exploratory on already-seen IS; any survivor requires a separately frozen
confirmatory protocol and ultimately untouched 2026 OOS.

