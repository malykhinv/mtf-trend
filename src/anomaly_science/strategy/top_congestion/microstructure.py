"""On-demand 1-second microstructure around top-congestion breakouts.

Phase 3 of the top-congestion study. Binance Vision only publishes DAILY
aggTrades archives, so getting 1s resolution for a breakout means downloading
the day(s) the breakout window touches, resampling the raw tape to 1-second
bars, and keeping ONLY the thin window slice (memory-economical, per the user's
constraint - we never persist the raw tick tape).

Typical use: build a 1s sidecar for each congestion the human MARKED as a
winner, plus (for the Phase 4 comparison) a sample of the non-winner pool.

  python -m anomaly_science.strategy.top_congestion.microstructure \
      --congestion-ids tcc_ab12... tcc_cd34...
  python -m anomaly_science.strategy.top_congestion.microstructure --marked
  python -m anomaly_science.strategy.top_congestion.microstructure --sample-pool 40
"""

from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.binance_vision_cache import CacheConfig, download_optional_bytes
from anomaly_science.strategy.pump_fade.aggtrades_backfill import aggtrades_archive_url, read_aggtrades
from anomaly_science.strategy.top_congestion.spec import PROTOCOL_FREEZE_ID, TopCongestionConfig

UNIVERSE_DIR = Path(".output/research/top_congestion/universe_is")
SECONDS_DIR = Path(".output/research/top_congestion/seconds")
LABELS_PATH = UNIVERSE_DIR / "review_labels.jsonl"

SECOND_BAR_COLUMNS: tuple[str, ...] = (
    "timestamp", "open", "high", "low", "close",
    "volume", "quote_volume", "trade_count",
    "taker_buy_base_volume", "taker_buy_quote_volume",
)
SECOND_MS = 1_000
# Default padding around the congestion so we capture the run-up into the break
# and the resolution toward the new high (or the stop).
DEFAULT_PRE_PAD_MIN = 30
DEFAULT_POST_PAD_MIN = 30


def build_second_bars(
    *,
    price: np.ndarray,
    quantity: np.ndarray,
    transact_time_ms: np.ndarray,
    is_buyer_maker: np.ndarray,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Resample a raw aggTrades tape into 1-second OHLCV bars over [start, end).

    Seconds with no trades are absent (missing, not zero). ``is_buyer_maker``
    True means the buyer was the passive maker, so the aggressor SOLD; taker-buy
    volume therefore counts trades where ``is_buyer_maker`` is False.
    """

    t = np.asarray(transact_time_ms, dtype=np.int64)
    mask = (t >= int(start_ms)) & (t < int(end_ms))
    if not mask.any():
        return pd.DataFrame(columns=SECOND_BAR_COLUMNS)
    t = t[mask]
    p = np.asarray(price, dtype=float)[mask]
    q = np.asarray(quantity, dtype=float)[mask]
    taker_buy = ~np.asarray(is_buyer_maker, dtype=bool)[mask]

    order = np.argsort(t, kind="mergesort")
    t, p, q, taker_buy = t[order], p[order], q[order], taker_buy[order]
    notional = p * q
    taker_buy_q = np.where(taker_buy, q, 0.0)
    taker_buy_n = np.where(taker_buy, notional, 0.0)

    sec = (t // SECOND_MS) * SECOND_MS
    unique_sec, start_idx = np.unique(sec, return_index=True)
    bounds = np.append(start_idx, len(t))
    rows = []
    for i, second_ts in enumerate(unique_sec):
        a, b = int(bounds[i]), int(bounds[i + 1])
        rows.append({
            "timestamp": int(second_ts),
            "open": float(p[a]), "high": float(p[a:b].max()),
            "low": float(p[a:b].min()), "close": float(p[b - 1]),
            "volume": float(q[a:b].sum()), "quote_volume": float(notional[a:b].sum()),
            "trade_count": int(b - a),
            "taker_buy_base_volume": float(taker_buy_q[a:b].sum()),
            "taker_buy_quote_volume": float(taker_buy_n[a:b].sum()),
        })
    return pd.DataFrame(rows, columns=SECOND_BAR_COLUMNS)


def _utc_days(start_ms: int, end_ms: int) -> list:
    cur = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
    last = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date()
    days = []
    while cur <= last:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _request_config() -> CacheConfig:
    return CacheConfig(out_dir=SECONDS_DIR, download_workers=1, retries=5,
                       timeout_seconds=45.0, connect_timeout_seconds=8.0)


def fetch_window_trades(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Download the daily aggTrades archives the window touches and return the
    trades inside [start_ms, end_ms). Missing days (404) are skipped."""

    cfg = _request_config()
    frames: list[pd.DataFrame] = []
    for day in _utc_days(start_ms, end_ms):
        url = aggtrades_archive_url(symbol=symbol, day=day)
        try:
            payload = download_optional_bytes(url, cfg)
        except RuntimeError:
            payload = None
        if payload is None:
            continue
        try:
            trades = read_aggtrades(payload)
        except (zipfile.BadZipFile, ValueError, KeyError):
            continue
        t = trades["transact_time"].to_numpy(np.int64)
        trades = trades[(t >= int(start_ms)) & (t < int(end_ms))]
        if not trades.empty:
            frames.append(trades)
    if not frames:
        return pd.DataFrame(columns=["price", "quantity", "transact_time", "is_buyer_maker"])
    return pd.concat(frames, ignore_index=True).sort_values("transact_time").reset_index(drop=True)


def build_congestion_seconds(
    row: pd.Series,
    *,
    out_dir: Path = SECONDS_DIR,
    pre_pad_min: int = DEFAULT_PRE_PAD_MIN,
    post_pad_min: int = DEFAULT_POST_PAD_MIN,
    horizon_min: int | None = None,
    refresh: bool = False,
) -> dict:
    """Build (and cache) the 1s window for one congestion. The window spans the
    run-up before the congestion through the resolution horizon after it."""

    symbol = str(row["symbol"])
    congestion_id = str(row["congestion_id"])
    horizon = (horizon_min if horizon_min is not None
               else TopCongestionConfig(timeframe_minutes=int(row["timeframe_minutes"])).outcome.resolution_horizon_minutes)
    start_ms = int(row["cong_start_time_ms"]) - pre_pad_min * 60_000
    end_ms = int(row["cong_end_time_ms"]) + (horizon + post_pad_min) * 60_000

    out_path = out_dir / symbol / f"{congestion_id}.parquet"
    if out_path.exists() and not refresh:
        n = len(pd.read_parquet(out_path, columns=["timestamp"]))
        return {"congestion_id": congestion_id, "symbol": symbol, "second_bars": n, "cached": True, "path": str(out_path)}

    trades = fetch_window_trades(symbol, start_ms, end_ms)
    bars = build_second_bars(
        price=trades["price"].to_numpy(),
        quantity=trades["quantity"].to_numpy(),
        transact_time_ms=trades["transact_time"].to_numpy(),
        is_buyer_maker=trades["is_buyer_maker"].to_numpy(),
        start_ms=start_ms, end_ms=end_ms,
    )
    bars.attrs["congestion_id"] = congestion_id
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bars.to_parquet(out_path, index=False)
    return {"congestion_id": congestion_id, "symbol": symbol, "second_bars": int(len(bars)),
            "cached": False, "path": str(out_path), "window_start_ms": start_ms, "window_end_ms": end_ms}


def _hash_marked_id(event_id: str, start_ms: int, end_ms: int) -> str:
    import hashlib
    raw = f"{PROTOCOL_FREEZE_ID}|marked|{event_id}|{start_ms}|{end_ms}".encode("utf-8")
    return f"tcm_{hashlib.blake2b(raw, digest_size=10).hexdigest()}"


def marked_congestion_rows(
    *, labels_path: Path = LABELS_PATH, events_path: Path = UNIVERSE_DIR / "events.parquet"
) -> pd.DataFrame:
    """Synthesize congestion rows from the boxes the human drew (setups[].zones
    with pattern == 'congestion'), joined to their parent event for symbol/TF.

    The human's box - not a detector congestion - is the object of interest, so
    a 1s window is built straight from its start/end. Returns a frame with the
    columns build_congestion_seconds needs."""

    if not labels_path.exists():
        return pd.DataFrame(columns=["congestion_id", "event_id", "symbol", "timeframe_minutes",
                                     "cong_start_time_ms", "cong_end_time_ms"])
    events = pd.read_parquet(events_path, columns=["event_id", "symbol", "timeframe_minutes", "pump_peak_price"])
    ev_by_id = events.set_index("event_id")
    rows: list[dict] = []
    seen: set[str] = set()
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("unlabeled") is True:
            continue
        event_id = str(obj.get("event_id") or "")
        # A grouped candidate carries the real per-TF ids in source_event_ids.
        candidate_ids = [event_id, *[str(x) for x in (obj.get("source_event_ids") or [])]]
        parent = next((cid for cid in candidate_ids if cid in ev_by_id.index), None)
        if parent is None:
            continue
        ev = ev_by_id.loc[parent]
        for setup in obj.get("setups", []) or []:
            for zone in setup.get("zones", []) or []:
                if zone.get("pattern") != "congestion":
                    continue
                start_ms = int(zone.get("base_start_ms"))
                end_ms = int(zone.get("base_end_ms"))
                cid = _hash_marked_id(parent, start_ms, end_ms)
                if cid in seen:
                    continue
                seen.add(cid)
                rows.append({
                    "congestion_id": cid, "event_id": parent, "symbol": str(ev["symbol"]),
                    "timeframe_minutes": int(ev["timeframe_minutes"]),
                    "cong_start_time_ms": start_ms, "cong_end_time_ms": end_ms,
                    "marked_low": float(zone.get("lower_price")), "marked_high": float(zone.get("upper_price")),
                    "target_new_high": float(ev["pump_peak_price"]),
                })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 1s microstructure sidecars for top-congestion breakouts.")
    parser.add_argument("--congestion-ids", nargs="*", default=[])
    parser.add_argument("--marked", action="store_true", help="Build for every congestion box the human drew on the desk.")
    parser.add_argument("--sample-pool", type=int, default=0, help="Build for N random pool congestions (validation).")
    parser.add_argument("--winners-only", action="store_true", help="With --sample-pool: only outcome==reached_new_high.")
    parser.add_argument("--pre-pad-min", type=int, default=DEFAULT_PRE_PAD_MIN)
    parser.add_argument("--post-pad-min", type=int, default=DEFAULT_POST_PAD_MIN)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    if args.marked:
        subset = marked_congestion_rows()
        if subset.empty:
            raise SystemExit("no human-marked congestion boxes found yet; mark winners on the desk first")
    elif args.congestion_ids:
        congs = pd.read_parquet(UNIVERSE_DIR / "congestions.parquet")
        subset = congs[congs["congestion_id"].isin(args.congestion_ids)]
    elif args.sample_pool:
        congs = pd.read_parquet(UNIVERSE_DIR / "congestions.parquet")
        pool = congs[congs["outcome"] == "reached_new_high"] if args.winners_only else congs
        subset = pool.sample(min(args.sample_pool, len(pool)), random_state=args.seed)
    else:
        raise SystemExit("give --marked, --congestion-ids, or --sample-pool N")

    print(f"protocol={PROTOCOL_FREEZE_ID}  building {len(subset)} second-window sidecars ...")
    for _, row in subset.iterrows():
        info = build_congestion_seconds(row, pre_pad_min=args.pre_pad_min, post_pad_min=args.post_pad_min, refresh=args.refresh)
        tag = "cached" if info["cached"] else "built"
        print(f"  [{tag}] {info['symbol']:>14} {info['congestion_id']}  bars={info['second_bars']:>6}")


if __name__ == "__main__":
    main()
