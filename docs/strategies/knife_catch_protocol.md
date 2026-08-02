# Knife-catch (dump buy-back) — research protocol

**Idea.** Price collapses tens of percent over a few candles (capitulation / long-liq
cascade / panic). Near the bottom, when **long activity appears** (aggressive buying,
absorption of the sells, deleveraging), the dump is bought back — a sharp bounce. We do
not catch the knife itself; we catch the **first sign of reversal at the bottom** and
take the bounce. Timeframes 1m/5m/10m (starting 5m).

**Status.** Design + Stage 0. DEV only. OOS untouched. Code: `strategy/knife_catch/research/`.

## Locked design decisions
- **Exit model: fixed RR + stop below the bottom.** Stop just under the dump low; target =
  entry + k·risk (e.g. RR=2). CatBoost predicts P(target-first before stop). Clean, symmetric
  with the pump work, easy to turn into EV.
- **Start timeframe: 5m** (OI cadence matches; less noise than 1m). Extend to 1m/10m after the
  5m pipeline is validated.

## Data constraints (from flow-data-availability)
- ⚠️ **Liquidations gone** on free Binance (`liquidation_available`=0). Long-liq cascades are the
  canonical capitulation-bottom signal and are simply unavailable — the main blind spot.
- ✅ **OI now dense full-year** (5-min, all 802 symbols). Best available proxy: OI **collapse**
  during the dump = deleveraging / longs flushed (cleaner bottom) vs OI **rising** into the low
  = knife still loaded (more to flush).
- ✅ taker-buy / CVD / trade count & size: full history.

## Guardrails (lessons already paid for)
- **Validity-before-EV** (bee-bite): eyeball setups on the desk before any model.
- **No horizon censoring**: resolve the RR race with a fixed horizon or path-sim + time-stop;
  never drop unresolved.
- **Discrimination ≠ conversion to R**: stops are decisive for knives; the EV stage with a
  realistic stop is mandatory, not "nice AUC → trade".
- Check **symbol-PnL concentration** and **week-stability** (avoid the one-week lottery).

## Roadmap
0. **Dump detector (high-recall) + desk marking.** Causal-ish discovery of sharp drops
   (≥X% in ≤N bars, one descending impulse), marked top→bottom, no cleanliness gate. Desk review.
1. **Visual QA.** Expert keeps / redraws / rejects; reject staircases, slow grinds, dead coins.
2. **Calibrate detector** (depth, speed, single-impulse) to the expert; blind-reproduces-expert check.
3. **Reversal trigger + QA.** Define "long activity appeared" (taker-buy surge, CVD turn-up, first
   higher-low, long lower wick + volume climax, OI stabilising) and draw the causal entry; eyeball it.
4. **Causal outcome label.** Entry at trigger, stop below the low, fixed-RR target; honest horizon.
5. **CatBoost.** Feature families below; matched-blind + shuffled-null + week-stability; DEV only.
6. **Execution/EV.** Path-sim with real stop + costs, R-space, week-stability, symbol concentration.
7. **OOS.**

## Expert validity rules (from Stage 1 desk review, 2026-08-01)
- **GATE — sleep→dump, not pump→dump.** The dump must START from a long calm consolidation, not
  a sharp pump high ("дамп после спячки, а не после пампа; старт на резком хаю = скип, не откупается").
  Implemented: an >=8h pre-dump window must span <= MAX_SLEEP_RANGE (top sits at the sleep level).
- **RED FLAG feature — dump-candle wick above the sleep.** A red dump candle whose upper wick pokes
  well above the sleep range signals a distribution spike over the base -> weak buy-back. Captured as
  `top_spike_above_sleep_pct` (a feature, not a gate).
- **Exit note.** When no buy-back occurs, exit after time or by stop -> confirms the fixed-RR + stop +
  time-stop exit model.

## CatBoost feature families (predict the reversal at the bottom)
1. **Dump geometry:** depth %, depth in ATR, #candles, speed (%/min), verticality/path-efficiency,
   biggest-candle share, single-impulse vs staircase, climax lower-wick share, climax close position,
   sleep_range, base_to_top, pre_top_runup, **top_spike_above_sleep (red flag)**.
2. **Overextension/context:** price vs daily EMA/VWAP, distance below recent swing-low, fraction of a
   prior up-move given back, cap tier, session (thin liquidity).
3. **Flow / buyer appearance (core):** taker-buy share and its turn-up at the low; CVD divergence
   (new price low but higher CVD = absorption); buy-volume spike; trade-count climax; average trade
   size (whale vs retail); **ΔOI over the dump** (collapse = capitulation vs rising = loaded knife).
4. **Reversal microstructure at trigger:** first higher-low, micro-level reclaim, volatility
   contraction after the spike, bounce velocity in the first bars, rejection wick at the low.
5. **Recurrence:** this coin's prior dumps and their buy-back outcomes (leak-safe: only closed before t0).
