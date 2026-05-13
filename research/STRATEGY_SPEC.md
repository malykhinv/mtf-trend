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

Live scheduling contract:

```text
selected hot symbol -> scan all due configured TF sets -> then move to next symbol
cold universe discovery is explicit budget, not implicit heavy work
ticker-radar promotion can add watch symbols but cannot itself open trades
inactive_scan_slots_per_cycle=0 means active/radar-only scan and must be visible in artifacts
```



---

## 4A. Timeframe contract

The strategy uses separate setup and execution timeframes.

```text
setup_timeframe / HTF:
- dormancy baseline;
- abnormal quote-volume and trade-count expansion;
- price expansion and retention at setup level;
- exhaustion / category checks;
- initial risk box context.

entry_timeframe / LTF:
- does not re-prove the whole pump thesis;
- confirms the setup is still alive and executable;
- checks activation hold, path/verticality, freshness, drift/RR and live price guards;
- supplies the executable decision timestamp for market-entry proxy and live order guards.
```

Live may create a setup before the HTF candle closes by aggregating already closed LTF candles inside the current HTF bucket. This must be marked as `setup_source=forming_htf_from_entry_tf` with `setup_elapsed_fraction` and `setup_closed_entry_candles`. Backtest parity mode must use the same contract and write `feature_contract=htf_setup_ltf_entry_v1`.

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

---

## Trade chart review artifact

Trade screenshots are diagnostic artifacts, not trading signals. The canonical trade chart should show:

```text
top: LTF trade-window candles with entry/exit/risk/TP annotations
second: HTF candles for the same local trade window
third: normalized quote-volume and number_of_trades line curves
bottom: independent 1h context covering the last 4 days through the candle that contains the main chart end
```

The 1h context panel may draw strict overhead levels from the hourly-level diagnostic, but those levels must be discovered only from the same displayed 4-day 1h window. They must be unlabeled chart context only and must not be treated as an entry/exit rule unless a separate strategy patch wires them into the decision path and updates backtest/live parity.

The lower flow panel is shape/timing context: quote volume and trade count are each normalized to 0-100 within the displayed trade window and are not comparable as absolute magnitudes.

Live open/close Telegram charts use the same four-panel renderer after verified fill/stop creation. Because the trade is not closed yet, open charts must not draw completed risk/reward rectangles; they show TP1 and SL as horizontal levels instead. Telegram is operator UI only: `live_events.csv` and live position state remain the audit source of truth.

---

## Telegram operator-message style

Telegram is operator UI only; audit truth remains in `live_events.csv`, ledger files, exchange state, and chart artifacts.

Style contract:

```text
symbol-specific messages use one deterministic animal emoji per compact symbol
service/startup/pause messages use 🚧
service/error messages use ⚠️
error payloads and integrity reasons are rendered as Telegram code
TF/context lines are rendered as Telegram code
```

The symbol emoji must be algorithmic, not per-symbol hardcoded and not Python `hash()` based. It is derived from a stable digest of the compact symbol string so the same symbol maps to the same animal across runs.

---

## Live audit failure visibility

Live audit truth must not depend on console output or Telegram delivery. Important operator-path failures and fallbacks must be visible in `live_events.csv`, including:

```text
network_degraded / network_recovered
telegram_async_send_failed
telegram_photo_send_failed
telegram_open_chart_failed / telegram_open_chart_missing_id / telegram_open_text_fallback / telegram_open_text_missing_id
telegram_close_photo_sent / telegram_close_photo_failed / telegram_close_photo_missing_id / telegram_close_text_fallback
reject_stop_cooldown
```

These events are diagnostic/audit artifacts only. They must not change signal selection, entry execution, stop placement, TP/SL math, or position monitoring.

---

## HTF/LTF live-entry contract

The current live/backtest contract is:

```text
HTF/forming HTF = setup quality
LTF = entry permission
exchange fill = risk/PnL source of truth
```

Intended anomaly TF sets are:

```text
5m/30s
1m/15s
1m/5s
```

For a pair such as `5m/30s`, live does not wait for the 5m candle to close. It aggregates closed 30s buckets inside the current 5m bucket and evaluates setup flow on the forming HTF candle. Forming HTF flow is judged by pace-normalized quote-volume and trade-count ratios against the closed HTF baseline, plus a raw-progress floor so very small early bursts are not treated as a real wake-up.

LTF confirmation should not re-prove the whole pump thesis. It confirms that the entry is still executable: activation hold, path/verticality, freshness, price drift, TP already reached, RR collapse and actual exchange fillability.

TP1 is a management milestone, not proof that market room exists. The default TP1 target is the nearest higher round market number above the old 1R target. The round step is derived from current price and movement size so the level is psychologically/operationally cleaner without jumping to an unrelated far-away target.

Optional red-flag profiles are research/backtest filters only until proven on longer data. They may use only pre-decision/live-available fields such as mark basis, OI interaction, context freshness, taker-buy share delta and effort-per-return. They must not use `future_*`, `outcome_label`, `exit_reason`, `tp1_hit`, MFE/MAE or realized return fields.

Runner-oriented research should prioritize early features available at decision time:

```text
mark basis versus decision close
mark/contract context momentum before decision
quote/trade effort per unit of price displacement
hold ratio / retention shape inside the LTF confirmation segment
recent same-symbol spike density and time since prior spike
```

Recent prior spikes are not automatically invalid. They can indicate an active theme. The red flag to test is serial failed or overcrowded wake-ups, not any prior attention.
