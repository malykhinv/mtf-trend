"""Do post-pump ranges FADE instead of spring? (short side of bee-bite)

The long spring makes a new high only ~24% of the time - it usually gets
stopped. So test the inverse: when the tight post-pump range breaks DOWN,
short it toward the base. First a descriptive race (from range formation, does
price reach the base = fade, or a new high = continuation first?), then a
tradeable short entered on the range-low breakdown (stop above the range,
target the base / a 2R / a trailing swing-high). Compact short barrier sim;
honest ~30bps round trip.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.pump_fade.builder import _load_symbol
from anomaly_science.strategy.pump_long.research.context import DEFAULT_CACHE, DEFAULT_LATTICE, DEV_END_MS
from anomaly_science.strategy.pump_long.research.metrics import summarize
from anomaly_science.strategy.bee_bite.spring import (
    CONSOL_BARS, HOLD_RATIO, MAX_RANGE_FRAC, SWEEP_WINDOW,
)

COST = 0.003          # honest round-trip (~30 bps) for the short barrier
RACE_HORIZON = 480    # bars to resolve fade-vs-continuation
NEW_HIGH_EPS = 0.002


def _short_sim(h, low, c, ei, entry, stop, target):
    """Short from ei's open. Stop ABOVE (intrabar touch), target BELOW."""
    n = len(c)
    for j in range(ei, n):
        if float(h[j]) >= stop:
            return (entry - stop) / entry - COST, False
        if target is not None and float(low[j]) <= target:
            return (entry - target) / entry - COST, True
    return (entry - float(c[-1])) / entry - COST, False


def build_fade_outcomes(*, lattice_path: Path = DEFAULT_LATTICE, cache_dir: Path = DEFAULT_CACHE,
                        end_ms: int | None = DEV_END_MS) -> pd.DataFrame:
    lat = pd.read_parquet(lattice_path)
    mask = lat["runner_label_available"].astype(bool)
    if end_ms is not None:
        mask &= lat["snapshot_time_ms"] < end_ms
    lat = lat.loc[mask].copy()
    idx = lat.groupby("group")["anchor_high"].idxmax()
    ev = lat.loc[idx, ["group", "symbol", "snapshot_time_ms", "anchor_high", "base_level"]].reset_index(drop=True)
    stamp = pd.to_datetime(ev["snapshot_time_ms"], unit="ms", utc=True)
    ev["mo"] = stamp.dt.strftime("%Y-%m"); ev["week"] = stamp.dt.strftime("%G-W%V")
    print(f"pump events: {len(ev)} across {ev['symbol'].nunique()} symbols", flush=True)

    rows: list[dict] = []
    nsym = ev["symbol"].nunique()
    for gi, (symbol, g) in enumerate(ev.groupby("symbol"), 1):
        if gi % 60 == 0 or gi == nsym:
            print(f"  {gi}/{nsym} symbols, ranges {len(rows)}", flush=True)
        try:
            frame, _q = _load_symbol(cache_dir / f"{symbol}.parquet")
        except Exception:
            continue
        ts = frame["timestamp"].to_numpy(np.int64)
        o = frame["open"].to_numpy(float); h = frame["high"].to_numpy(float)
        low = frame["low"].to_numpy(float); c = frame["close"].to_numpy(float)
        for row in g.itertuples():
            hi = int(np.searchsorted(ts, int(row.snapshot_time_ms)))
            if hi >= len(ts) or int(ts[hi]) != int(row.snapshot_time_ms):
                continue
            H = float(row.anchor_high); B = float(row.base_level); n = len(c)
            hold = B + HOLD_RATIO * (H - B)
            ce = hi + CONSOL_BARS
            if ce + 5 >= n or H <= B:
                continue
            seg_lo = low[hi + 1:ce + 1]; seg_hi = h[hi + 1:ce + 1]
            if len(seg_lo) < CONSOL_BARS // 2 or c[hi + 1:ce + 1].min() < hold:
                continue
            range_low = float(seg_lo.min()); range_high = max(float(seg_hi.max()), H)
            rh = range_high - range_low
            if rh <= 0 or rh / range_high > MAX_RANGE_FRAC:
                continue
            # descriptive race: base (fade) vs new high (continuation)
            newhigh = range_high * (1 + NEW_HIGH_EPS); outcome = "none"
            for j in range(ce + 1, min(ce + RACE_HORIZON, n - 1) + 1):
                if float(low[j]) <= B:
                    outcome = "fade"; break
                if float(h[j]) >= newhigh:
                    outcome = "continuation"; break
            # tradeable short: first close below range_low -> short next open
            net_base = net_2r = net_trail = np.nan; short_win = None
            for k in range(ce + 1, min(ce + SWEEP_WINDOW, n - 1) + 1):
                if float(c[k]) < range_low:
                    ei = k + 1
                    if ei >= n:
                        break
                    entry = float(o[ei]); stop = range_high
                    if stop <= entry:
                        break
                    r = stop - entry
                    net_base, short_win = _short_sim(h, low, c, ei, entry, stop, B)
                    net_2r, _ = _short_sim(h, low, c, ei, entry, stop, entry - 2 * r)
                    # trailing: target None but re-stop to lowest*... keep simple: target=base, already net_base
                    net_trail = net_base
                    break
            rows.append({"symbol": symbol, "mo": row.mo, "week": row.week,
                         "pump_size": (H - B) / B, "range_tightness": rh / range_high,
                         "outcome": outcome, "net_short_base": net_base, "net_short_2r": net_2r,
                         "short_win": short_win})
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    res = df["outcome"].value_counts(normalize=True) * 100
    print("\n=== descriptive race from range: fade vs continuation ===", flush=True)
    for k in ("fade", "continuation", "none"):
        print(f"  {k:<13}: {res.get(k, 0):.1f}%", flush=True)
    sh = df.loc[df["net_short_base"].notna()]
    print(f"\n=== tradeable SHORT the range-low breakdown (n={len(sh)}) ===", flush=True)
    for col, lbl in (("net_short_base", "short->base"), ("net_short_2r", "short 2R")):
        net = sh[col].to_numpy()
        s = summarize(net, months=sh["mo"].to_numpy(), weeks=sh["week"].to_numpy())
        print(s.line(lbl), flush=True)
    # by pump size
    print("\n  short->base by pump size:", flush=True)
    for lo, hi in ((0, 0.10), (0.10, 0.20), (0.20, 10)):
        m = sh[(sh["pump_size"] >= lo) & (sh["pump_size"] < hi)]
        if len(m) < 40:
            continue
        net = m["net_short_base"].to_numpy()
        s = summarize(net, months=m["mo"].to_numpy(), weeks=m["week"].to_numpy())
        print("   " + s.line(f"pump {lo*100:.0f}-{hi*100:.0f}%"), flush=True)


def main() -> None:
    df = build_fade_outcomes()
    out = Path(".output/results/bee_bite_v1/fade_outcomes.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    report(df)
    print(f"\nsaved {len(df)} ranges -> {out}", flush=True)


if __name__ == "__main__":
    main()
