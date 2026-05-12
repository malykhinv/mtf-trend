# Anomaly Strategy Spec

Compact current strategy spec for the anomaly-first trading system.

---

## 1. Core idea

The system trades only after detecting an abnormal market wake-up and classifying whether the anomaly is likely organic and still executable.

```text
abnormal activity -> nature/category check -> controlled continuation -> executable entry -> managed exit
```

The first question is the nature of the anomaly. Entry logic is secondary and must not smuggle in future information or visual hindsight.

---

## 2. Required anomaly evidence

A candidate must show enough live-available evidence:

```text
quote_volume expansion
trade_count expansion
price displacement
verticality / directional intent
retention after impulse
limited whipsaw before impulse
acceptable initial risk
```

If real quote-volume or trade-count is unavailable, conclusions about tape/flow/organic behavior are limited and the candidate should be rejected or marked explicitly degraded.

---

## 3. Category / nature checks

The system should separate at least:

```text
organic wake-up
thin-liquidity spike
exhausted blow-off
news/one-print jump
choppy fake-out
late continuation
```

Category labels are research contracts. They must be backed by observable features, not visual preference.

---

## 4. Entry logic

Entry must be executable without future information.

Allowed research entry families:

```text
market after confirmed wake-up
break_box_high
pullback_box_fraction
```

Every entry test must preserve:

```text
known signal timestamp
known entry price rule
known stop rule
timeout
fees/slippage assumptions
```

Live execution contract:

```text
signal_entry_price/time != actual_fill_price/time
actual fill must come from exchange order/trade payloads
no candle/ticker-derived synthetic fill for ledger/PnL
no stale signal order after freshness window
no order if TP1 is already reached or RR collapsed at live price
BE/TP/PnL are computed from actual fill, not signal close
```

---

## 5. Exit logic

Current research exit families:

```text
structural_trail
ema20_close
ema20_negative_pnl_be_escape
```

Exit evaluation must report average trade, winrate, tail dependence, monthly distribution and dependence on top outliers before claiming edge.

---

## 6. Data-quality contract

No silent fallback for core evidence:

```text
quote_volume missing != close * volume substitute
number_of_trades missing != trades/trade_count alias substitute
open_interest missing/stale != neutral context
empty cache window != valid zero-signal result
```

Unknown or degraded source must become an explicit status/reason.

---

## 7. Current invalid assumptions

Do not assume:

```text
anomaly means continuation
large candle means organic demand
higher future high means executable edge
visual setup means live-available setup
single run means stable edge
```

The research objective is to break weak anomaly categories cheaply before optimizing parameters.


---

## Live executable-entry contract

A selected live signal is not automatically tradable. Before any market order, live must reject the signal when:

```text
signal age after candle close > max_signal_age_ms
live price is invalid
live price has already reached signal TP1
actual live risk is invalid or too wide
absolute live-price drift from signal entry > max_entry_price_drift_pct
RR from live price to signal TP1 < min_executable_rr_to_signal_tp1
```

These rejects are trading decisions and must be visible in artifacts. Telegram may notify the operator, but artifact rows remain the source of truth.

---

## Live position-management contract

After entry fill, live must not assume the position is safely managed unless exchange state confirms it:

```text
initial stop id visible in open orders
stop side/type/reduceOnly/amount/stopPrice verified
TP1 partial exit fill resolved from exchange order/trade payload
position monitor OHLCV available repeatedly enough to manage BE/trail
integrity errors written as artifacts and surfaced to Telegram
```

If stop/fill/monitor state is unknown, the run must emit an explicit artifact reason. It must not count synthetic TP1/BE/PnL as reliable edge evidence.

Backtest market entries remain a proxy, not real exchange fills. Artifacts must label the execution model and export skipped-entry reasons, especially stale/non-executable/price-drift/RR-collapse reasons.


---

## 1h overhead-level context diagnostic

Overhead levels are context, not an entry trigger. They may support a long-continuation hypothesis only when they are above current price and still leave enough target room.

A valid 1h overhead level must satisfy:

```text
at least 3 valid high-based touches in the same price band
each counted touch is at least 6h after the previous counted touch
each valid touch has a meaningful bounce after touch
a source pivot/high candle has no close above its high in the previous 12h
level is above current price
level is not pierced by later wick/high
level is not a held broken level
symbol/level context is not a clear downtrend pseudo-resistance
```

The diagnostic metric records:

```text
distance_pct
valid_touch_count
max_reaction_pct
median_valid_reaction_pct
recent_move_pct
reaction_to_recent_move_ratio
context = bullish_target / danger_ceiling / overhead_level
```

A touch is valid only when the candle high is near the level while the candle body remains below the touch band. A candle is not allowed to seed a level when any close in the previous 12h is above that candle high. Body/interior range intersections and later wick/high pierces are rejected instead of being rescued by a later bounce.


---

## Live discovery latency contract

Live may skip expensive signal construction for decisions that are already older than `max_signal_age_ms`, but the skip is still an audit decision and must be written to artifacts, e.g. `reject_stale_signal` with a prescan stage.

Top-growth snapshots are research/audit data, not trading signals. They should run through an explicit standalone command so they do not compete with live discovery and execution guards for API budget.

---

## Live ticker-radar scheduling contract

Ticker radar is allowed only as a scheduling priority layer:

```text
ticker snapshot delta -> bounded watch promotion -> normal closed-kline deep scan -> normal signal/risk/execution guards
```

It must not:

```text
open a trade
replace quote_volume / number_of_trades evidence from closed klines
prune or permanently skip cold-universe symbols
hide missing ticker fields behind proxy volume
consume normal round-robin inactive slots without explicit operator configuration
```

Ticker-derived rows are diagnostic/scheduler artifacts only. Final signal validity still depends on the existing closed-kline flow, category, risk and execution checks.
