# Short Fade Core Edge

Date: 2026-06-11
Status: closest honest result, research-only, not live-approved.
Commit: UNKNOWN.

This file freezes the best currently verified failed-pump short candidate. It is
not a broad portfolio and not a claim that pump shorts are solved. It is the
cleanest core sleeve found so far after:

- first 180d IS discovery;
- frozen second 180d OOS verification;
- WFA/plateau sanity;
- top-trade and top-symbol independence checks;
- basic lookahead audit.

## Strategy In Plain Trader Language

We short only a specific kind of failed pump:

```text
pump wakes up -> price fails structurally -> lower-high / failed-retest short
-> SL above nearest recent local high -> no profit exit before 0.75R
-> keep only if the trade starts moving in our favor within 5-10 minutes
```

The trade is not "short every pump after 15 minutes". The edge appears only
when the pump loses structure quickly, outside the weakest session context, and
the stop distance is not too tight or too wide.

## Frozen Core Definition

```text
family_group:    A_plus_fast
families:        A_post_oneprint_break1p5_retest0p8
                 fast_deep_break_taker_above
session_filter:  not_asia_overlap
stop_model:      recent5
base_policy:     tp075_full
entry feature:   risk_3_8pct
best guard:      m5_mfe025
guard plateau:   m5_mfe025 / m10_mfe025 / m10_mfe050 / m15_mfe050
```

Session filter:

```text
allowed: asia_only, europe_only, europe_us_overlap, us_only, off_session
excluded: asia_europe_overlap
```

Risk filter:

```text
initial_risk_pct between 3% and 8%
```

Entry model:

```text
closed 1m confirmation candle
entry at next 1m open after confirmation close
entry_price = next_open * (1 - entry_slippage_pct)
```

Exit model used in the best row:

```text
TP: full exit at 0.75R
SL: recent5 structural/local-high stop
guard: if by 5 minutes MFE < 0.25R and 0.75R was not already hit,
       exit at the 5m close proxy
```

## OOS Result

Best independence variant:

```text
A_plus_fast + not_asia_overlap + recent5 + tp075_full + m5_mfe025 + risk_3_8pct

IS:  33 trades, avg +0.237R, median +0.710R
OOS: 34 trades, avg +0.268R, median +0.477R
OOS win rate: 61.8%
OOS positive-day rate: 62.1%
OOS top-trade independence: 61.9%
OOS top-symbol independence: 65.0%
OOS avg after 10bps extra cost proxy: +0.226R
```

This is the first candidate in this short-fade research that clears the user's
50%+ top-removal target on the second 180d half.

## Guard Plateau

The result is not a single isolated guard point:

```text
m5_mfe025:
OOS 34 trades, avg +0.268R, median +0.477R, WR 61.8%,
top-trade 61.9%, top-symbol 65.0%.

m10_mfe050:
OOS 34 trades, avg +0.264R, median +0.491R, WR 70.6%,
top-trade 54.2%, top-symbol 59.1%.

m15_mfe050:
OOS 34 trades, avg +0.256R, median +0.710R, WR 67.6%,
top-trade 52.2%, top-symbol 54.5%.

m10_mfe025:
OOS 34 trades, avg +0.252R, median +0.713R, WR 67.6%,
top-trade 52.2%, top-symbol 54.5%.
```

Interpretation:

```text
The exact minute/threshold is less important than the idea:
the short must start producing downside acceptance soon after entry.
```

## Why Broader Portfolios Are Not Promoted

Frequency expansion weakens the result:

```text
entry_top3:  89 OOS trades, avg +0.041R, median +0.218R,
             10bps extra cost avg -0.015R.

entry_top5:  136 OOS trades, avg +0.045R, median +0.118R,
             10bps extra cost avg -0.012R.

entry_top10: 140 OOS trades, avg +0.040R, median +0.090R,
             10bps extra cost avg -0.016R.
```

So the current edge is a narrow core sleeve. It does not satisfy the desired
3-15 trades/day frequency target.

## Lookahead Audit

Verdict:

```text
No direct lookahead was found for this frozen core under the current
closed-candle next-open proxy.
```

Artifact audit:

```text
.output/results/failed_pump_short_research_365d/short_core_lookahead_audit.csv
```

Exact core audit:

```text
core rows: 67
IS rows: 33
OOS rows: 34
future_label_available_at_entry true: 0
short_confirm_closed_before_entry false: 0
entry before confirm close: 0
non-60s confirm candles: 0
selection_eligible false: 0
entry_minus_confirm_close_ms: 0 for all 67 rows
```

Signal generation audit:

```text
failed_pump_short_signals.csv rows: 571820
future_label_available_at_entry true: 0
short_confirm_closed_before_entry false: 0
entry before confirm close: 0
entry_model: next_1m_open_after_confirm_plus_slippage
```

One signal in the full CSV has delayed entry by 55 minutes, but it is
`selection_eligible=False` and belongs to the simple-fade audit baseline. It is
not part of the frozen core.

## Candle-Close Timing

The core uses closed 1m confirmation candles:

```text
confirm_open_ms = candle timestamp
confirm_close_ms = confirm_open_ms + 60_000
entry_open_ms = next 1m candle timestamp
```

In the audited core:

```text
entry_open_ms == confirm_close_ms
```

This is valid for a backtest proxy that assumes entry at the next candle open
after the previous candle is closed. It is still optimistic versus live if the
system needs extra latency after candle close. Live promotion must stress:

```text
entry delayed by 1-2 candles
extra spread/slippage
missed fills
RR collapse after signal close
```

## Post-Entry Guard Timing

The `m5_mfe025` guard is not an entry filter. It is a management rule:

```text
enter first;
wait 5 closed 1m candles after entry;
if MFE < 0.25R and TP was not already reached, exit at the 5m close proxy.
```

The verifier explicitly rejects hindsight filtering: post-entry features are
not used to remove bad trades from the sample. If the continuation condition
fails, the trade is closed at the corresponding minute close proxy.

## Remaining Risks

1. Execution latency:
   the backtest enters at the exact next 1m open after confirmation close.
   Real live entry may be later.

2. OHLC intrabar ordering:
   1m candles do not reveal the true order of high/low inside the minute.
   Current replay is conservative on stop/target conflict by checking stop
   first, but exact tick/orderbook replay is still stronger.

3. `recent5` stop provenance:
   the final replay consumes `recent5` stop from
   `failed_pump_structural_management_replay_trades.csv`. The artifact-level
   checks show valid positive risk and no future-label signal leakage in the
   core, but the original generator for the recent-local-high stop was not
   independently reconstructed in this audit.

4. OOS consumed:
   the second 180d half has now been used for this hypothesis. Do not keep
   tuning this same core on that OOS period.

5. Frequency:
   this is not a full strategy portfolio. It is a low-frequency high-quality
   sleeve.

## Promotion Checklist

Before live or paper-live promotion:

```text
1. Rebuild the core as one explicit executable rule.
2. Add entry delay stress: +1m and +2m.
3. Add fee/slippage stress beyond the current 10bps proxy.
4. Verify exact stop placement source for recent5.
5. Re-run on a new unseen period or live-forward sample.
6. In live, calculate PnL only from actual fill and verified stop.
```

Current action:

```text
freeze, do not optimize further on the same OOS half.
```
