# Session-anchored pump → RR=1 fade-vs-newhigh — discovery report

**Question (user hypothesis, 2026-07-31).** A session opens (e.g. ASIA). The
*prior* session printed an anomaly: a clean **long pump ≥ 20%** on **≥ 5×** the
quote-volume *and* trade-count of its recent same-type sessions (baselines
6/9/12/16), a **single ascending cycle** (not a 2+-cycle meander), and price
**near the high** at the new session's open. From the open we monitor OI, taker
flow, volume, trades and price; when long sentiment **fades**, can CatBoost predict
whether price **fades to the middle** of the pump (−0.5·pump) or makes a **new high
of the same size** (+0.5·pump)? Because reward = risk, the whole edge is win rate.

**Status.** DEV-only (2025‑06 → 2026‑01‑01). OOS untouched. Code:
`strategy/session_break/research/session_pump_rr1.py`. **Verdict: no tradeable /
predictable edge.**

---

## Design (all strictly causal)

- **Timeframe** 5m (matches Binance OI cadence). Sessions = fixed macro blocks.
- **Anomaly gate** on prior session P: pump = (peak − base)/base, base = lowest low
  at/ before the peak; vol & trade ratios = P vs the *median* of each 6/9/12/16
  same‑type‑session lookback (strictest = the **min** ratio must clear the mult).
- **Single-cycle gate** deepest retrace ≤ MAXDD·(total run) and ≤ N pullback
  episodes > 20% of the run.
- **Near-high** current session opens within `near` below P's peak.
- **Fade-onset trigger** first bar where price is **off the session high** AND the
  taker-buy fast EMA < slow EMA (buyer pressure fading) AND OI is **below its
  session peak** (leverage unwinding).
- **Label** symmetric RR=1 first-touch race from the trigger, reward = 0.5·pump;
  y = 1 iff the down target hits first. Both-in-one-bar and unresolved-in-horizon
  are dropped. 27 causal features (prior-pump geometry, OI trajectory, taker/CVD,
  activity). GroupKFold-by-week CatBoost vs a within-week shuffled null.

---

## Results

**The setup is rare.** Faithful config (pump ≥ 20%, vol/tc ≥ 5×, strict single
cycle, near-high ≤ 12%, 8h race): **387 setups / 191 DEV** over 802 symbols × 1yr.

**Direction is a coinflip inflated by horizon censoring — NOT a fade.** The
apparent "74% continuation" (fade-first 0.26) is mostly an artifact: up-targets
resolve fast, down-drifts need time, and unresolved races are dropped.

| race horizon | 4h | 8h | 16h | 32h |
|---|---|---|---|---|
| fade-first (of resolved) | 0.328 | 0.330 | 0.404 | 0.453 |
| % resolved | 23% | 36% | 50% | 65% |

Fade-first climbs toward 0.5 as the window lengthens; the residual up-first tilt
(~0.55 at 32h, still 35% unresolved) is the well-known continuation lean, the
**opposite** of the fade hypothesis.

**No model discrimination.**
- Strict config (N=191 DEV): week-CV **AUC 0.526 vs shuffled null 0.584** — real
  model *below* null.
- Powered config (pump ≥ 20% kept; vol ≥ 3×, single-cycle dropped, near ≤ 15%, 16h
  race → **866 DEV / 1735 total**): week-CV **AUC 0.531 vs null 0.512** (+0.019 =
  noise). Confident tails ride the base rate, not skill; the confident-fade tail
  wins only 18–21% of *weeks*. OI features (`p_oi_build`, `oi_drop_from_peak`) rank
  mid-pack and add nothing decisive.

The eye-catching "confident-newhigh net +747bp" is **not realizable**: the
first-touch label discards ~50% unresolved races (biasing toward fast movers) and
the per-trade reward is ≥10%. It is the base-rate continuation majority. The
continuation-long itself is already **OOS-rejected** (see `pump_long.md` /
runner-vs-fizzle: frozen rule −0.54%/trade = blind).

---

## Conclusion

With OI now fully available for the first time, and with a purpose-built intraday
fade-onset trigger and a single-cycle cleanliness gate, the dozens of
market-logical metrics **cannot separate fade from continuation** at the trigger
(AUC ≈ shuffled null at N≈900). The raw direction is a censoring-inflated coinflip
with a mild continuation tilt — the reverse of the fade premise — and that tilt is
the same continuation wave already rejected OOS. **No new tradeable edge on free
Binance 1m/OI data.** OOS was not touched.

**If revisited:** the only genuinely new asset is dense OI. A fair test of the
continuation *long* (proper path sim with a time-stop marking the ~50% unresolved,
not first-touch drop) is the one execution question this study did not close — but
prior OOS results make a positive outcome unlikely.
