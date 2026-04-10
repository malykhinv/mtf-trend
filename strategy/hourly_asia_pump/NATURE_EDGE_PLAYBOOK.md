# Nature Edge Playbook

This document records the most valuable working knowledge from the anomaly research.

## 1. Universe

- Work on Binance USDT-M futures symbols available in the local cache for the tested period.
- Current-period research uses `.output/cache`.
- Older-period research uses `.output/cache_prev_year_5m`.
- The old-period cache is still not a perfect full historical universe because it starts from the currently available symbol set and then backfills history.

## 2. What Counts As A Pump

An anomaly is a green `5m` bar inside a session window that passes a loose universe filter:

- `trigger_return_pct >= 3%`
- `range_atr >= 4`
- `volume_mult >= 4`
- `close_to_high_frac <= 0.25`

These events are scanned across all `5m` minutes. Session assignment is:

- Asia: `00:00-07:59 UTC`
- Europe: `08:00-15:59 UTC`
- America: `16:00-23:59 UTC`

## 3. Features We Keep For Every Anomaly

For each anomaly we store:

- symbol, timestamp, session, month
- age of the symbol at event time
- anomaly size in percent and in ATR
- body, upper wick, lower wick, close position in the bar
- volume spike
- pre-base range and drift before the anomaly
- next-bar hold, pullback and extension
- short forward descriptive features such as post-15m and post-30m buyer or seller rematch
- pre-pump accumulation or distribution type

The main database is built by:

- `strategy/hourly_asia_pump/anomaly_category_lab.py`

## 4. The Most Useful Broad Natural Signs

Long-friendly:

- body-driven anomaly
- clean or at least holding confirmation after the anomaly
- buyer rematch after the anomaly
- warm pre-pump accumulation is a quality bonus

Short-friendly:

- body-driven anomaly
- failed continuation
- no buyer rematch, or clear seller rematch
- pre-pump accumulation is a red flag against short, especially in America

## 5. Natural Categories That Matter

The strongest broad categories found so far:

- Asia long:
  - `warm_continuation`
  - `mixed_transition` with body-drive plus buyer rematch
- Europe long:
  - `warm_continuation / context_warm / impulse_body_drive / confirm_clean / wave_buyer_match`
  - `mixed_transition / context_overheated / impulse_body_drive / confirm_holding / wave_buyer_match`
  - `mixed_transition / context_warm / impulse_body_drive / confirm_holding / wave_none`
- America long:
  - `mixed_transition / context_overheated / impulse_body_drive / confirm_holding / wave_buyer_match`
  - `warm_continuation / context_warm / impulse_body_drive / confirm_holding / wave_buyer_match / accumulation_warm`
  - `mixed_transition / context_warm / impulse_body_drive / confirm_clean / wave_none`
- Europe and America short:
  - failed continuation after a body-driven anomaly
  - best execution is usually a short re-entry from the upper part of the anomaly range, not chasing weakness lower

## 6. How We Currently Trade Them Broadly

Best broad long execution still looks like early continuation, not pure `1m` second-wave timing.

Most useful long execution models:

- `Next Open Red Trail`
- `Aggressive Break Fast`
- `Monster Break 3pct`
- in some Europe long cases: `Context Monster 5pct`

Broad long logic:

- buy early continuation above the anomaly high
- use a stop tied to the anomaly body or structure
- fail quickly if continuation does not appear early

Broad short logic:

- do not short the first green anomaly blindly
- wait for failed continuation
- sell from the upper zone of the anomaly range with a defined stop above the high

## 7. What Is Descriptive Vs Tradable

This distinction is critical.

Descriptive category features can be excellent for research, but some of them are not directly tradable because they use information from after the anomaly:

- next-bar confirmation quality
- 15m or 30m buyer or seller rematch

So:

- natural categories are strong as a research lens
- they are not automatically a live entry engine
- a live model must only use information available at the decision time

## 8. What Has Worked Less Well

- frozen rules across years
- a single universal second-wave `1m` long entry
- forcing one entry style across warm and overheated pumps

The current evidence says we understand the nature of good pumps better than we understand the exact online entry trigger.

## 9. Honest Current Status

- Natural categorization is useful and likely necessary.
- Asia long remains the clearest long core.
- Europe and America long are real and not empty once the long universe is built correctly.
- The strongest current portfolio results outside Asia come from broad combinations of natural categories plus already known continuation trade models.
- Untouched out-of-sample validation must freeze the category plus model selection on one period and test it on the other.
