"""Visual QA deck for reclaim events: draw N RANDOM 60m setups with EVERYTHING
that matters annotated -- session blocks shaded + labelled, session boundaries,
prior-session range (high=the level / mid=take / low), the poke high + stop, the
entry (return bar), and a volume panel with relative-volume colouring. Pure
matplotlib (manual candles), one PNG per setup.

Usage: python -m ...reclaim_qa_charts --n 24 [--seed 7] [--deep] [--min-reward-bp 200]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from anomaly_science.strategy.session_break.research.reclaim import _resample
from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ, block_seq_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
BLOCK_COLORS = {0: "#e8eef7", 1: "#eaf6ea", 2: "#fff3d6", 3: "#f7e9e9", 4: "#efeaf5"}
OUT = Path(".output/results/session_break/qa_charts")


def draw(ev, df, tf_min: int, out: Path) -> bool:
    ts = df["timestamp"].to_numpy(np.int64)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float)
    idx = {int(t): i for i, t in enumerate(ts)}
    r0 = idx.get(int(ev.ref_start_ts)); ei = idx.get(int(ev.entry_ts)); pk = idx.get(int(ev.poke_ts))
    if r0 is None or ei is None or pk is None:
        return False
    horizon = int(6 * 8)  # ~6h*8 -> show ~2 sessions past entry
    a = max(0, r0 - 2); b = min(len(df), ei + horizon)
    if b - a < 6:
        return False
    seg = np.arange(a, b)
    x = np.arange(len(seg))
    seq = block_seq_for_ms(ts[a:b])
    fig, (ax, axv) = plt.subplots(2, 1, figsize=(14, 7), sharex=True,
                                  gridspec_kw={"height_ratios": [3.2, 1]})
    # ---- session shading + boundaries + labels ----
    start = 0
    for k in range(1, len(seg) + 1):
        if k == len(seg) or seq[k] != seq[start]:
            bl = BLOCK_BY_SEQ[int(seq[start])]
            ax.axvspan(x[start] - 0.5, x[k - 1] + 0.5, color=BLOCK_COLORS[bl.seq], zorder=0)
            ax.text((x[start] + x[k - 1]) / 2, 0.975, bl.name, transform=ax.get_xaxis_transform(),
                    ha="center", va="top", fontsize=7.5, color="#888", zorder=5)
            if start > 0:
                ax.axvline(x[start] - 0.5, color="#bbb", lw=0.8, zorder=1)
            start = k
    # ---- candles ----
    for xi, i in zip(x, seg):
        up = c[i] >= o[i]
        col = "#26a69a" if up else "#ef5350"
        ax.plot([xi, xi], [lo[i], h[i]], color=col, lw=0.7, zorder=2)
        ax.add_patch(plt.Rectangle((xi - 0.3, min(o[i], c[i])), 0.6, abs(c[i] - o[i]) + 1e-12,
                                   facecolor=col, edgecolor=col, zorder=3))
    # ---- levels ----
    def hl(y, color, ls, lbl, lw=1.3):
        ax.axhline(y, color=color, ls=ls, lw=lw, zorder=4)
        ax.text(len(seg) - 0.5, y, f" {lbl}", va="center", ha="left", fontsize=8, color=color)
    hl(ev.ref_high, "#c62828", "-", "prior HIGH = level", 1.8)
    hl(ev.ref_mid, "#1565c0", "--", "MID = take")
    hl(ev.ref_low, "#777", ":", "prior LOW")
    hl(ev.poke_high, "#ef6c00", (0, (1, 1)), "poke high")
    a0 = (ev.poke_high - ev.ref_high)  # visual only
    stop = ev.poke_high + 0.25 * max(a0, (ev.ref_high - ev.ref_mid) * 0.15)
    hl(stop, "#8e24aa", "--", "stop")
    hl(ev.entry_px, "#2e7d32", "-", "entry", 1.0)
    # ---- markers ----
    ax.scatter([pk - a], [ev.poke_high], marker="v", s=70, color="#ef6c00", zorder=6, label="poke")
    ax.scatter([ei - a], [ev.entry_px], marker="^", s=80, color="#2e7d32", zorder=6, label="entry (short)")
    ax.axvspan((ei - a) - 0.5, (ei - a) + 0.5, color="#2e7d32", alpha=0.08, zorder=1)
    # prior-session range bracket
    ax.axvspan((r0 - a) - 0.5, (idx.get(int(ev.entry_ts)) - a), color="none")
    outcome = "MID (revert)" if int(ev.label) == 1 else "ABOVE (real break)"
    rew = (ev.entry_px - ev.ref_mid) / ev.entry_px * 100
    risk = (stop - ev.entry_px) / ev.entry_px * 100
    ax.set_title(f"{ev.symbol}  {BLOCK_BY_SEQ[int(ev.session)].name} session  60m  |  outcome={outcome}  "
                 f"|  reward(entry→mid)={rew:.2f}%  risk(entry→stop)={risk:.2f}%  "
                 f"prior_up_impulse={ev.prior_up_imp:.1f}ATR  poke_rvol={ev.poke_rvol:.1f}x",
                 fontsize=9.5)
    ax.legend(loc="upper left", fontsize=7, framealpha=0.8)
    ax.set_ylabel("price")
    # ---- volume ----
    base = np.nanmedian(qv[max(0, a - 100):a]) if a > 5 else np.nanmedian(qv[a:b])
    for xi, i in zip(x, seg):
        rv = qv[i] / base if base and base > 0 else 1.0
        col = "#ef6c00" if rv >= 3 else ("#8d6e63" if rv >= 1.5 else "#bdbdbd")
        axv.bar(xi, qv[i], width=0.6, color=col, zorder=2)
    axv.set_ylabel("quote vol")
    axv.text(0.01, 0.9, "orange=≥3x median, brown=≥1.5x", transform=axv.transAxes, fontsize=7, color="#666")
    axv.grid(True, axis="y", alpha=0.2)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/rlabel_tf60_scored.parquet"))
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tf", type=int, default=60)
    ap.add_argument("--min-reward-bp", type=float, default=0.0, help="only setups with entry->mid >= this (bp of price)")
    args = ap.parse_args()
    s = pd.read_parquet(args.scored)
    s = s[s.label.isin([0, 1])].copy()
    s["reward_bp"] = (s.entry_px - s.ref_mid) / s.entry_px * 1e4
    if args.min_reward_bp > 0:
        s = s[s.reward_bp >= args.min_reward_bp]
    samp = s.sample(n=min(args.n, len(s)), random_state=args.seed).reset_index(drop=True)
    print(f"pool={len(s):,}  drawing {len(samp)} random setups (min_reward_bp={args.min_reward_bp})")
    OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for sym, g in samp.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        raw = pd.read_parquet(p, columns=["timestamp", "open", "high", "low", "close",
                                          "volume", "quote_volume", "trade_count",
                                          "taker_buy_quote_volume", "open_interest"])
        df = _resample(raw.sort_values("timestamp").reset_index(drop=True), args.tf)
        for ev in g.itertuples():
            out = OUT / f"{made:02d}_{sym}_{BLOCK_BY_SEQ[int(ev.session)].name}_{'MID' if ev.label==1 else 'ABOVE'}.png"
            if draw(ev, df, args.tf, out):
                made += 1
                print(f"  {out.name}")
    print(f"wrote {made} charts -> {OUT}")


if __name__ == "__main__":
    main()
