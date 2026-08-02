"""Desk review population of DUMP TRADES (excl crash week) for eyeballing.

Each card shows: the dump (pump element top->bottom), the ENTRY at the aggression/break
signal, the STOP (the bottom), the TAKE (50%-retrace target), and the realized EXIT (where
price hit take / made a new low / timed out) -- so the expert can see WHY single-coin dumps
mostly make a new low instead of buying back. Random sample (not depth-ranked) across symbols,
the market-crash week excluded. 5m default. Writes the desk contract (candidates + marks).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION, LEVEL_LABEL_SCHEMA_VERSION
from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol
from anomaly_science.strategy.knife_catch.research.dump_signals import _first_entry, RETRACE
from anomaly_science.strategy.session_break.research.reclaim import _resample
import glob

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
REVIEW_DIR = Path(".output/results/knife_catch/dump_trade_review")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6
TF = 5
MIN_DROP = 0.20
SCAN_H = 8.0
HORIZON_H = 72.0
STOP_BUF = 0.004
EXCLUDE_WEEK = "2025-W41"
ENTRY_REASON = "aggression"        # the best-EDGE signal; falls back to break_long
LEFT_CTX_BARS = 96
POST_CTX_BARS = 96


def _outcome(eb, bottom, target, hi, lo, cl, n, horizon):
    we = min(eb + horizon, n - 1)
    for k in range(eb, we + 1):
        if lo[k] < bottom:
            return "LOSS(new low)", k, float(lo[k])
        if hi[k] >= target:
            return "WIN(50% buyback)", k, float(target)
    return "timeout", we, float(cl[we])


def build_symbol(path):
    try:
        dumps = [d for d in _scan_symbol(path, TF) if d.culmination_ms < DEV_END and d.drop_pct > MIN_DROP]
    except (OSError, ValueError, KeyError):
        return []
    if not dumps:
        return []
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "quote_volume", "trade_count", "taker_buy_quote_volume",
                                         "open_interest"]).sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, TF)
    ts = df["timestamp"].to_numpy(np.int64); op = df["open"].to_numpy(float)
    hi = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    base_qv = pd.Series(qv).rolling(240, min_periods=30).median().to_numpy()
    n = len(ts); sym = path.stem
    scan = max(2, round(SCAN_H * 60 / TF)); horizon = max(8, round(HORIZON_H * 60 / TF)); oi_lb = max(1, round(5 / TF))
    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or t >= b or b >= n - 2:
            continue
        wk = pd.Timestamp(int(ts[b]), unit="ms", tz="UTC").strftime("%G-W%V")
        if wk == EXCLUDE_WEEK:
            continue
        T = float(d.pump_start_price)
        eb = bottom = None
        for reason in (ENTRY_REASON, "break_long"):
            eb, bottom = _first_entry(reason, t, b, hi, lo, cl, op, taker, oi, qv, base_qv,
                                      MIN_DROP, scan, oi_lb, TF, n)
            if eb is not None:
                break
        if eb is None or eb >= n - 2:
            continue
        entry = float(op[eb]); stop = bottom * (1 - STOP_BUF); target = bottom + RETRACE * (T - bottom)
        if entry <= stop or target <= entry:
            continue
        label, xb, xprice = _outcome(eb, bottom, target, hi, lo, cl, n, horizon)
        eid = "dumptrade_" + hashlib.blake2b(f"{sym}|{ts[t]}|{ts[b]}".encode(), digest_size=10).hexdigest()
        out.append({
            "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION, "event_id": eid,
            "symbol": sym, "tf": f"{TF}m",
            "review_start_ms": int(ts[max(0, t - LEFT_CTX_BARS)]),
            "review_end_ms": int(ts[min(n - 1, xb + POST_CTX_BARS)]),
            "anchor_time_ms": int(ts[b]),
            "top_ms": int(ts[t]), "top_price": T, "bottom_ms": int(ts[b]), "bottom_price": float(lo[b]),
            "entry_ms": int(ts[eb]), "entry_price": entry, "stop_price": stop,
            "target_price": float(target), "exit_ms": int(ts[xb]), "exit_price": float(xprice),
            "drop_pct": float(d.drop_pct), "outcome": label, "week": wk, "reason": reason,
        })
    return out


def _seed(cd):
    return {
        "event_id": cd["event_id"], "symbol": cd["symbol"], "tf": cd["tf"],
        "source_event_id": cd["event_id"], "source_event_ids": [cd["event_id"]], "selected_tf": cd["tf"],
        "has_level": False, "has_pump_transition": True,
        "setups": [{
            "family": "unknown", "quality": "bad",
            "notes": (f"DUMP TRADE | drop {cd['drop_pct']*100:.0f}% | entry={cd['reason']} | "
                      f"OUTCOME: {cd['outcome']}. pump=dump(top->bottom); entry/stop(bottom)/take(50%) drawn."),
            "has_level": False, "has_pump_transition": True, "has_structure_break": False,
            "level_price": None, "level_start_ms": None, "level_end_ms": None,
            "level_broken": None, "level_touch_times_ms": None,
            "pump_start_ms": cd["top_ms"], "pump_start_price": cd["top_price"],
            "culmination_ms": cd["bottom_ms"], "culmination_price": cd["bottom_price"],
            "structure_break_ms": None, "structure_break_price": None,
            "structure_swing_low_ms": None, "structure_swing_low_price": None,
            "entry_ms": cd["entry_ms"], "entry_price": cd["entry_price"], "entry_auto": True, "entry_source": "bot",
            "exit_ms": cd["exit_ms"], "exit_price": cd["exit_price"],
            "sl_price": cd["stop_price"], "sl_ms": cd["entry_ms"], "sl_hit_ms": None, "sl_auto": True, "sl_source": "bot",
            "zigzag_points": [],
        }],
        "source": "bot_dump_trade", "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION, "saved_at_ms": 0,
    }


def _one(f):
    try:
        return build_symbol(Path(f))
    except Exception:  # noqa: BLE001
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-events", type=int, default=350)
    ap.add_argument("--per-symbol", type=int, default=1)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    from concurrent.futures import ProcessPoolExecutor, as_completed
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed({ex.submit(_one, f): f for f in files}):
            r = fut.result()
            if r:
                rows += r[:args.per_symbol]
    if not rows:
        raise SystemExit("no dump trades")
    rng = np.random.default_rng(0)
    cand = pd.DataFrame(rows)
    idx = rng.permutation(len(cand))[:args.max_events]                 # random sample, not depth-ranked
    cand = cand.iloc[np.sort(idx)].reset_index(drop=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    cand.to_parquet(REVIEW_DIR / "candidates.parquet", index=False)
    (REVIEW_DIR / "marks.jsonl").write_text(
        "".join(json.dumps(_seed(r), ensure_ascii=False, separators=(",", ":")) + "\n" for r in cand.to_dict("records")),
        encoding="utf-8")
    (REVIEW_DIR / "review_labels.jsonl").touch(exist_ok=True)
    vc = cand.outcome.value_counts().to_dict()
    print(f"dump-trade review cards={len(cand)} (excl {EXCLUDE_WEEK}) -> {REVIEW_DIR}")
    print(f"  outcomes: {vc}")
    print(f"  drop% median={cand.drop_pct.median()*100:.0f}%  weeks={cand.week.nunique()}")


if __name__ == "__main__":
    main()
