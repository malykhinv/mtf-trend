# Pump Nature Manual

This file is the working playbook for classifying pumps by **what drives them**
and **what stage of the move they are in**.

It is not a textbook note. It is a practical reference for research and trading.

---

# Core idea

A pump by itself is not a useful class.

What matters:

- who is moving price
- how price is being moved
- whether the move is early, healthy, crowded, trapped, exhausted, or distributed

The minimum useful metric families are:

- `return / range / ATR`
- `volume spike`
- `wick / close quality`
- `open interest delta`
- `funding`
- `premium / basis`
- `taker buy/sell pressure`
- `crowding proxies`:
  - global long/short ratio
  - top account long/short ratio
  - top position long/short ratio
- `depth imbalance` when available

---

# Regime Table

| Regime | What is really happening | Typical price shape | OI | Funding / Premium / Basis | Taker / crowding | What it usually means | How to act |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Early Spot Accumulation** | Size is entering without obvious leverage pressure | Tight base, moderate candles, weak wicks, price holds well | Flat or mild up | Neutral to mild positive | Taker pressure positive but not extreme | Quiet preparation before public impulse | Best long risk/reward if continuation confirms |
| **Spot Expansion** | Cash-led move starts repricing the market | Body-driven green candles, shallow pullback, closes stay high | Flat to mild up | Mild positive | Taker buy pressure healthy, not euphoric | Healthy continuation regime | Long on controlled continuation / reclaim |
| **Futures Momentum Build-up** | Leverage starts pushing trend harder | Faster candles, smaller retraces, range expands | Clear up | Positive and rising | Aggressive buy pressure, crowding starts to grow | Trend is real, but no longer early | Can continue, but late entries get dangerous |
| **Short Squeeze** | Shorts are forced out, not necessarily true fresh demand | One to three violent candles, little structure | Down or spike then down | Usually hot | Taker burst can be extreme | Move can overshoot and then fade fast | Avoid buying peak, prefer fade / post-spike retest logic |
| **Aggressive Long Build-up** | New longs pile in aggressively | Strong serial candles, crowded trend | Fast up | Clearly elevated | Strong buy pressure and long crowding | Trend may continue, but overheating is building | Hold if early, avoid late long chase |
| **Thin Liquidity Pump** | Price jumps because book is empty, not because demand is deep | Gaps, long wicks, unstable prints | Weak response or unavailable | Can be noisy | Ratios can look strong but are not trustworthy | Structural fragility / manipulation risk | Avoid or treat as low-quality edge |
| **News Repricing** | Market jumps into a new fair range after information shock | Large impulse, then compression or range discovery | Reactive | Positive but not necessarily crowded | Early pressure spike, then normalization | New regime can form after the shock | Trade only after stabilization |
| **Exhaustion Blow-off** | Late buyers are paying up into the final leg | Huge bodies, upper wicks start to appear | Flat or down | Extreme | Buy pressure was strong, then weakens | End-phase acceleration | Exit longs / look for short setup quality |
| **Distribution Pump** | Large sellers unload into public strength | Upper wicks, weak closes, price stops holding highs | Flat or mild up | Elevated | Crowd still buys, but structure weakens | Smart money is selling into the move | Avoid long, watch for short |
| **False Breakout** | Liquidity is taken above highs and then rejected | Break above range, upper wick, close back inside | Often up first | Mild to elevated | Crowd gets trapped | Trap rather than trend | Reverse or short after failure confirms |
| **Derivative Overheating** | Move is being carried too much by leverage | Strong candles, rich premium, crowded continuation | Fast up | Extreme | Long crowding elevated | Overbought through leverage rather than healthy sponsorship | Wait for cooling, do not chase |
| **Absorption Pump** | There is volume, but the move stops travelling | Bodies shrink, wicks increase, highs stop extending | Often still up or flat | Elevated but not expanding cleanly | Buy pressure no longer converts to travel | Seller is absorbing flow | Often pre-reversal or at least poor continuation |

---

# Practical reading rules

## 1. Price + OI is the first split

| Price | OI | Interpretation |
| --- | --- | --- |
| Up | Up | new longs / real trend participation |
| Up | Flat | more cash-like or lighter leverage participation |
| Up | Down | squeeze / forced covering |
| Up | Up too fast | overheating risk |

## 2. Funding / premium / basis tells you whether leverage is paying up

- Mildly positive and stable:
  often healthy continuation
- Strongly positive:
  trend is crowded
- Extremely positive:
  you may be buying liquidity for trapped late longs

## 3. Taker flow tells you whether aggression is real

- Mildly positive:
  steady expansion
- Very high:
  momentum burst or squeeze
- Strong flow with weak candle hold:
  absorption / distribution warning

## 4. Wicks and close quality tell you whether the move is being accepted

- Small upper wick + close near high:
  acceptance
- Large upper wick + weak close:
  rejection

---

# Best use in our research

For `long`:

- prefer:
  - `Early Spot Accumulation`
  - `Spot Expansion`
  - sometimes `Futures Momentum Build-up`
- be careful with:
  - `Aggressive Long Build-up`
  - `News Repricing`
- avoid:
  - `Short Squeeze`
  - `Distribution Pump`
  - `False Breakout`
  - `Derivative Overheating`
  - `Thin Liquidity Pump`

For `short`:

- best candidates:
  - `Distribution Pump`
  - `False Breakout`
  - `Exhaustion Blow-off`
  - `Absorption Pump`
  - some `Short Squeeze` aftermath
- worst candidates:
  - `Spot Expansion`
  - healthy `Early Spot Accumulation`

---

# Important research caveats

- `OI` is only usable where recent 5m history is available.
- Old-period Binance `OI` is not safely recoverable through public REST for our use case.
- Historical `depth imbalance` is not available in our pipeline yet.
- This taxonomy is a **decision framework**, not an entry trigger by itself.

Use it to answer:

- should this pump be continued?
- should it be faded?
- should it be ignored?
- is it healthy or crowded?

Do not use it alone to answer:

- exact 1m entry
- exact stop placement
- exact second-wave trigger
