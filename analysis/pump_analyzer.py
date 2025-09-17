"""Utility for analyzing pump candles across exchanges."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import List, Sequence
from urllib.parse import urlencode

import requests

from config.credentials import BINANCE
from config.pump_analysis import PumpAnalysisConfig


# Default configuration values are defined as constants to avoid any runtime
# dependencies on external files, environment variables, or CLI arguments.
DEFAULT_CONFIG = PumpAnalysisConfig(
    growth_pct=3.0,
    wick_pct=0.3,
    volume_mult=3.0,
    volume_window=20,
    rehigh_lookahead=3,
    atr_window=14,
)

TP_BARS = 5
SL_BARS = 5
EXCHANGES = ["binance", "bybit"]


@dataclass
class Candle:
    """Simple OHLCV candle representation."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class PumpRecord:
    """Record of a detected pump candle."""

    timestamp: int
    symbol: str
    tf: str
    pct_to_low_break: float
    pct_to_high_break: float
    break_direction: int
    volume: float
    relative_volume: float
    atr_mult: float
    pct_move: float
    upper_wick_pct: float
    rehigh_hit: bool
    liquidation_volume: float | None


def fetch_symbols(exchange: str) -> List[str]:
    """Fetch tradable symbols for the given exchange."""

    if exchange == "binance":
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        symbols = [
            s for s in data.get("symbols", []) if s.get("contractType") == "PERPETUAL"
        ]
        valid: List[str] = []
        for s in symbols:
            pair = s.get("pair") or s.get("symbol")
            try:
                kl_resp = requests.get(
                    "https://fapi.binance.com/fapi/v1/continuousKlines",
                    params={
                        "pair": pair,
                        "contractType": "PERPETUAL",
                        "interval": "1h",
                        "limit": 1,
                    },
                    timeout=5,
                )
                kl_resp.raise_for_status()
                if kl_resp.json():
                    valid.append(s["symbol"])
            except Exception:  # pragma: no cover - network failure logged
                logging.warning("Skipping symbol %s due to kline error", s.get("symbol"))
        return valid
    if exchange == "bybit":
        url = "https://api.bybit.com/v5/market/instruments-info"
        resp = requests.get(url, params={"category": "linear"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        symbols = [s.get("symbol") for s in data.get("result", {}).get("list", [])]
        valid: List[str] = []
        for symbol in symbols:
            try:
                kl_resp = requests.get(
                    "https://api.bybit.com/v5/market/kline",
                    params={
                        "category": "linear",
                        "symbol": symbol,
                        "interval": "1",
                        "limit": 1,
                    },
                    timeout=5,
                )
                kl_resp.raise_for_status()
                if kl_resp.json().get("result", {}).get("list"):
                    valid.append(symbol)
            except Exception:  # pragma: no cover - network failure logged
                logging.warning("Skipping symbol %s due to kline error", symbol)
        return valid
    raise ValueError(f"Unsupported exchange: {exchange}")


def fetch_candles(exchange: str, symbol: str, interval: str) -> List[Candle]:
    """Retrieve recent candles for ``symbol``."""

    if exchange == "binance":
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": 1500}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [
            Candle(
                open_time=int(c[0]),
                open=float(c[1]),
                high=float(c[2]),
                low=float(c[3]),
                close=float(c[4]),
                volume=float(c[5]),
            )
            for c in data
        ]
    if exchange == "bybit":
        # Bybit API allows fetching up to 1000 candles per request. Loop until
        # 1500 candles are collected (or data exhausted).
        url = "https://api.bybit.com/v5/market/kline"
        limit = 1000
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        all_candles: List[Candle] = []
        next_cursor: str | None = None
        while len(all_candles) < 1500:
            if next_cursor:
                params["cursor"] = next_cursor
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            res = resp.json().get("result", {})
            data = res.get("list", [])
            next_cursor = res.get("nextPageCursor")
            all_candles.extend(
                [
                    Candle(
                        open_time=int(c[0]),
                        open=float(c[1]),
                        high=float(c[2]),
                        low=float(c[3]),
                        close=float(c[4]),
                        volume=float(c[5]),
                    )
                    for c in data
                ]
            )
            if not next_cursor or not data:
                break
        return sorted(all_candles[:1500], key=lambda c: c.open_time)
    raise ValueError(f"Unsupported exchange: {exchange}")


def fetch_binance_liquidations(
    symbol: str,
    interval_start: int,
    interval_end: int,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> float | None:
    """Return total liquidation volume on Binance for ``symbol`` in interval."""

    if not api_key or not api_secret:
        api_key = api_key or BINANCE.api_key
        api_secret = api_secret or BINANCE.api_secret

    if not api_key or not api_secret:
        logging.warning(
            "Missing Binance API credentials; skipping liquidation fetch"
        )
        return None

    url = "https://fapi.binance.com/fapi/v1/forceOrders"
    params = {
        "symbol": symbol,
        "startTime": interval_start,
        "endTime": interval_end,
        "limit": 1000,
    }
    query = urlencode(params)
    signature = hmac.new(
        api_secret.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    params["signature"] = signature
    headers = {"X-MBX-APIKEY": api_key}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return sum(float(item.get("executedQty", 0)) for item in data)
    except Exception as exc:  # pragma: no cover - network failure logged
        logging.warning("Failed to fetch Binance liquidations: %s", exc)
        return None


def fetch_bybit_liquidations(
    symbol: str, interval_start: int, interval_end: int
) -> float | None:
    """Return total liquidation volume on Bybit for ``symbol`` in interval."""

    url = "https://api.bybit.com/v5/market/recent-liquidation"
    params = {
        "category": "linear",
        "symbol": symbol,
        "startTime": interval_start,
        "endTime": interval_end,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("result", {}).get("list", [])
        return sum(float(item.get("qty", 0)) for item in data)
    except Exception as exc:  # pragma: no cover - network failure logged
        logging.warning("Failed to fetch Bybit liquidations: %s", exc)
        return None


def _true_range(prev_close: float, candle: Candle) -> float:
    return max(
        candle.high - candle.low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def compute_atr(candles: Sequence[Candle], index: int, period: int = 14) -> float:
    """Compute ATR ending at ``index`` (exclusive)."""

    if index <= 0:
        return 0.0
    start = max(1, index - period)
    trs = [_true_range(candles[i - 1].close, candles[i]) for i in range(start, index)]
    return sum(trs) / len(trs) if trs else 0.0


def find_pumps(
    candles: Sequence[Candle],
    config: PumpAnalysisConfig,
    tp_bars: int,
    sl_bars: int,
) -> List[dict]:
    """Identify pump candles and compute metrics."""

    results: List[dict] = []
    volumes = [c.volume for c in candles]
    window = config.volume_window
    for i in range(window, len(candles) - max(config.rehigh_lookahead, tp_bars, sl_bars)):
        c = candles[i]
        pct_gain = (c.high - c.open) / c.open * 100 if c.open else 0
        median_vol = median(volumes[i - window : i])
        rel_vol = c.volume / median_vol if median_vol else 0
        total_range = c.high - c.low
        upper_wick = c.high - max(c.open, c.close)
        upper_wick_ratio = upper_wick / total_range if total_range else 0
        if not (
            pct_gain >= config.growth_pct
            and rel_vol >= config.volume_mult
            and upper_wick_ratio >= config.wick_pct
        ):
            continue
        atr = compute_atr(candles, i, config.atr_window)
        atr_mult = (total_range / atr) if atr else 0

        rehigh_index = None
        for j in range(i + 1, i + 1 + config.rehigh_lookahead):
            if candles[j].high >= c.high:
                rehigh_index = j
                break
        lookahead_end = (
            rehigh_index if rehigh_index is not None else i + config.rehigh_lookahead
        )
        rehigh_hit = rehigh_index is not None

        pct_to_low_break = 0.0
        pct_to_high_break = 0.0
        break_direction = 0
        low_cross_recorded = False
        high_cross_recorded = False
        for j in range(i + 1, lookahead_end + 1):
            next_candle = candles[j]
            if not low_cross_recorded and next_candle.low <= c.low:
                if c.close:
                    pct_to_low_break = (c.close - next_candle.low) / c.close * 100
                low_cross_recorded = True
                if break_direction == 0:
                    break_direction = -1
            if not high_cross_recorded and next_candle.high >= c.high:
                if c.close:
                    pct_to_high_break = (next_candle.high - c.close) / c.close * 100
                high_cross_recorded = True
                if break_direction == 0:
                    break_direction = 1
            if low_cross_recorded and high_cross_recorded:
                break
        results.append(
            {
                "timestamp": c.open_time,
                "pct_to_low_break": pct_to_low_break,
                "pct_to_high_break": pct_to_high_break,
                "break_direction": break_direction,
                "volume": c.volume,
                "relative_volume": rel_vol,
                "atr_mult": atr_mult,
                "pct_move": pct_gain,
                "upper_wick_pct": upper_wick_ratio * 100,
                "rehigh_hit": rehigh_hit,
                "liquidation_volume": None,
            }
        )
    return results


def _append_record(record: PumpRecord, out_file: Path) -> None:
    """Append ``record`` to ``out_file`` creating the workbook if needed."""

    try:
        from openpyxl import Workbook, load_workbook

        if out_file.exists():
            wb = load_workbook(out_file)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(list(PumpRecord.__annotations__.keys()))
        ws.append(
            [
                record.timestamp,
                record.symbol,
                record.tf,
                record.pct_to_low_break,
                record.pct_to_high_break,
                record.break_direction,
                record.volume,
                record.relative_volume,
                record.atr_mult,
                record.pct_move,
                record.upper_wick_pct,
                record.rehigh_hit,
                record.liquidation_volume,
            ]
        )
        wb.save(out_file)
    except Exception as exc:  # pragma: no cover - file or dependency errors
        logging.warning("Failed to write record to %s: %s", out_file, exc)


def analyze(
    exchange: str,
    config: PumpAnalysisConfig,
    tp_bars: int,
    sl_bars: int,
) -> None:
    """Run pump analysis for the given exchange."""

    try:
        symbols = fetch_symbols(exchange)
    except Exception as exc:  # pragma: no cover - network failure logged
        logging.warning("Failed to fetch symbols for %s: %s", exchange, exc)
        symbols = []
    out_dir = Path("data/pump_analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{exchange}.xlsx"
    pumps_total = 0
    seen: set[tuple[str, str]] = set()
    for symbol in symbols:
        total_candles = 0
        total_pumps = 0
        for interval in ["1m", "5m"]:
            try:
                candles = fetch_candles(
                    exchange,
                    symbol,
                    interval if exchange == "binance" else interval.strip("m"),
                )
            except Exception as exc:  # pragma: no cover - network failure logged
                logging.warning(
                    "Failed to fetch candles for %s %s: %s", symbol, interval, exc
                )
                continue
            total_candles += len(candles)
            try:
                pumps = find_pumps(candles, config, tp_bars, sl_bars)
            except Exception as exc:  # pragma: no cover - safety
                logging.warning(
                    "Failed to analyze candles for %s %s: %s", symbol, interval, exc
                )
                continue
            for pump in pumps:
                key = (symbol, interval)
                if key in seen:
                    continue
                interval_ms = int(interval.strip("m")) * 60_000
                start = pump["timestamp"]
                end = start + interval_ms
                try:
                    if exchange == "binance":
                        liq = fetch_binance_liquidations(
                            symbol, start, end, BINANCE.api_key, BINANCE.api_secret
                        )
                    elif exchange == "bybit":
                        liq = fetch_bybit_liquidations(symbol, start, end)
                    else:  # pragma: no cover - safety
                        liq = None
                except Exception as exc:  # pragma: no cover - network failure logged
                    logging.warning(
                        "Failed to fetch liquidations for %s %s: %s", symbol, interval, exc
                    )
                    liq = None
                record = PumpRecord(
                    timestamp=pump["timestamp"],
                    symbol=symbol,
                    tf=interval,
                    pct_to_low_break=pump["pct_to_low_break"],
                    pct_to_high_break=pump["pct_to_high_break"],
                    break_direction=pump["break_direction"],
                    volume=pump["volume"],
                    relative_volume=pump["relative_volume"],
                    atr_mult=pump["atr_mult"],
                    pct_move=pump["pct_move"],
                    upper_wick_pct=pump["upper_wick_pct"],
                    rehigh_hit=pump["rehigh_hit"],
                    liquidation_volume=liq,
                )
                _append_record(record, out_file)
                seen.add(key)
                total_pumps += 1
                pumps_total += 1
        logging.info(
            "Analyzing %s: processed %d candles, found %d pumps",
            symbol,
            total_candles,
            total_pumps,
        )
    logging.info(
        "Processed %d symbols on %s, found %d pump candles",
        len(symbols),
        exchange,
        pumps_total,
    )


def main() -> None:
    """Run analysis for all supported exchanges using default settings."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for exchange in EXCHANGES:
        analyze(exchange, DEFAULT_CONFIG, TP_BARS, SL_BARS)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    main()

__all__ = ["main", "analyze", "PumpRecord"]
