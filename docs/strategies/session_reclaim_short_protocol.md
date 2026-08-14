# Session reclaim short: causal mechanics protocol

```text
strategy_name = session_reclaim_short_v1
strategy_family = failed_break_reclaim_short
protocol_freeze_id = session_reclaim_short_is_mechanics_20260803_v1
development_end_exclusive = 2026-01-01T00:00:00Z
live_strategy = false
```

## Why this is a new study

The historical `RR>=3` result cannot be used as evidence. Its source builder
stored only events whose future MID-vs-ABOVE race resolved inside the label
horizon. Events that reached neither boundary were silently omitted before the
trade simulation. The saved population therefore depended on a future outcome,
even though the retained feature columns were causal. The old `+0.082R` result
is a discovery clue only.

This study rebuilds the signal population independently of every future label,
trade result, CatBoost score, MFE, MAE, TP, SL, or PnL field.

The source universe is Binance USD-M perpetual instruments whose parquet schema
contains the frozen OHLC, quote-volume, trade-count, and taker-quote fields.
Dated delivery contracts and files missing a required field are excluded before
any price/event calculation; every exclusion and reason is written to the run
report. Values are never silently imputed.

## Causal event

All decisions use closed 60-minute candles built from IS 1-minute data.

1. The immediately preceding UTC liquidity-session block supplies a range high,
   mid, and low.
2. The high must be an interior, already-confirmed swing high: at least two
   closed 60-minute bars exist on each side inside the reference session, and
   the rise into and fall from it are each at least two causal 24-hour ATRs.
3. The current session opens below the reference high.
4. Price trades above that high and later, inside the same current session,
   closes a full 60-minute candle back below it.
5. `signal_time` is that return candle's close. Candidate membership is final at
   this instant. Every later path is retained, including no-fill, unresolved,
   time-exit, and censored cases.

Required time invariants:

```text
feature_cutoff_time_ms = signal_time_ms
reference_end_time_ms <= poke_time_ms < signal_time_ms
feature_cutoff_time_ms <= order_activation_time_ms
future_path_start_time_ms >= order_activation_time_ms
source_timestamp_ms < 2026-01-01T00:00:00Z
```

The last events without a complete IS-only 48-hour future remain in the
universe with `future_complete=false`; they are counted as censored and never
silently dropped or read from 2026.

## Frozen mechanics families

All exits are anchored to point-in-time market structure. Fixed-percent and
ATR-multiple physical TP/SL levels are forbidden.

Entry policies, evaluated separately:

```text
market_reclaim       = market at the first 1m open after signal
maker_reference_high = resting short at the reclaimed reference high;
                       primary fill requires strict 1m trade-through
retest_rejection     = after a 1m retest of the reference high closes back
                       below it, enter at the next 1m open
```

Maker/retest orders expire after six hours. A target reached before entry is a
cancelled/no-fill event, not a winner. Ambiguous same-minute paths use the
adverse ordering.

Stop/invalidation policies, evaluated separately:

```text
poke_touch       = hard stop at the known poke high
upper_structure  = hard stop at the nearest higher confirmed 60m pivot high
close1_uppercat  = exit after one closed 60m bar above the reference high,
                   with the higher pivot as catastrophe stop
close2_uppercat  = same, after two consecutive closed 60m bars above
```

Profit anchors:

```text
mid_full       = close 100% at prior-range mid
mid50_low50    = close 50% at mid and 50% at prior-range low
```

The primary horizon is 48 hours. Costs are reported at 0/2/4/6/10/20/30 bps
round trip. Touch-fill is diagnostic only; strict trade-through is the primary
maker assumption. One-second data may later adjudicate only deterministic
hash-selected ambiguous cases, never cases selected by PnL.

## Admission sequence

1. Universe future-invariance and IS-boundary audit.
2. Per-variant fill, path, gross-EV, cost-headroom, week/month/symbol stability,
   CVaR5, drawdown, positive-day share, and top-trade dependence.
3. Deterministic outcome-free Desk sample with every mechanics variant and its
   complete lifecycle visible.
4. Expert morphology validation and blinded repeat audit.
5. Only a mechanics family with positive cost-adjusted EV and stability may be
   passed to frozen weekly CatBoost selection.
6. The 2026 partition remains untouched until the complete IS protocol is
   frozen. Because the setup has already been mined on 2025, any IS success is
   post-selection evidence and requires genuinely forward confirmation.
