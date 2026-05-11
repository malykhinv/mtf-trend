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
