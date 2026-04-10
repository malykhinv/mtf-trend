from __future__ import annotations

import argparse
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError

from strategy.hourly_asia_pump.market_regime_dataset import _resolve_input_db_path

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOADER_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "recent_derivatives_loader"
RAW_DIR = LOADER_DIR / "raw"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "recent_market_regime_lab"

ENDPOINT_FILES = {
    "oi": RAW_DIR / "oi",
    "taker": RAW_DIR / "taker",
    "global_ls": RAW_DIR / "global_ls",
    "top_account": RAW_DIR / "top_account",
    "top_position": RAW_DIR / "top_position",
    "basis": RAW_DIR / "basis",
    "premium": RAW_DIR / "premium",
    "funding": RAW_DIR / "funding",
}


def _safe_float(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _load_recent_anomalies(days: int) -> pd.DataFrame:
    path = _resolve_input_db_path()
    frame = pd.read_csv(path, low_memory=False)
    frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
    frame = frame.dropna(subset=["timestamp_ms", "symbol", "dataset"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    frame = frame[(frame["dataset"].astype(str) == "current") & (frame["timestamp_ms"] >= cutoff_ms)].copy()
    frame["trade_triggered"] = frame.get("trade_triggered", False).astype(str).str.lower().isin(["true", "1"])
    frame["exit_return_pct"] = pd.to_numeric(frame.get("exit_return_pct"), errors="coerce")
    return frame.reset_index(drop=True)


def _load_symbol_endpoint(endpoint: str, raw_symbol: str) -> pd.DataFrame:
    path = ENDPOINT_FILES[endpoint] / f"{raw_symbol}.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()
    if "timestamp_ms" in frame.columns:
        frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
        frame = frame.dropna(subset=["timestamp_ms"]).copy()
        frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    return frame.sort_values("timestamp_ms").reset_index(drop=True)


def _lookup_last(frame: pd.DataFrame, event_ts: int, column: str) -> float | None:
    if frame.empty or column not in frame.columns or "timestamp_ms" not in frame.columns:
        return None
    timestamps = pd.to_numeric(frame["timestamp_ms"], errors="coerce").fillna(-1).astype("int64").to_numpy()
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
    idx = timestamps.searchsorted(int(event_ts), side="right") - 1
    if idx < 0 or idx >= len(values):
        return None
    value = values[idx]
    if math.isnan(value):
        return None
    return float(value)


def _lookup_delta_pct(frame: pd.DataFrame, event_ts: int, column: str, back_bars: int) -> float | None:
    if frame.empty or column not in frame.columns or "timestamp_ms" not in frame.columns:
        return None
    timestamps = pd.to_numeric(frame["timestamp_ms"], errors="coerce").fillna(-1).astype("int64").to_numpy()
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
    idx = timestamps.searchsorted(int(event_ts), side="right") - 1
    prev_idx = idx - back_bars
    if idx < 0 or prev_idx < 0 or idx >= len(values):
        return None
    if math.isnan(values[idx]) or math.isnan(values[prev_idx]) or abs(values[prev_idx]) <= 1e-12:
        return None
    return float((values[idx] - values[prev_idx]) / values[prev_idx])


def _assign_market_regime(row: pd.Series) -> str:
    context = str(row.get("context_archetype") or "")
    impulse = str(row.get("impulse_archetype") or "")
    pre_acc = str(row.get("pre_accumulation_type") or "")
    taker_ratio = _safe_float(row.get("taker_buy_sell_ratio"))
    oi_60m = _safe_float(row.get("oi_delta_60m_pct"))
    funding = abs(_safe_float(row.get("funding_rate_last")) or 0.0)
    basis_rate = abs(_safe_float(row.get("basis_rate")) or 0.0)
    premium = abs(_safe_float(row.get("premium_close")) or 0.0)
    upper_wick = _safe_float(row.get("upper_wick_frac")) or 0.0
    close_to_high = _safe_float(row.get("close_to_high_frac")) or 1.0
    trigger_return = _safe_float(row.get("trigger_return_pct")) or 0.0
    range_atr = _safe_float(row.get("range_atr")) or 0.0
    volume_mult = _safe_float(row.get("volume_mult")) or 0.0

    if range_atr >= 10.0 and volume_mult <= 4.0 and upper_wick >= 0.20:
        return "Thin Liquidity Pump"
    if pre_acc == "accumulating" and context in {"context_coiled", "context_warm"} and impulse == "impulse_body_drive" and (taker_ratio is None or 0.52 <= taker_ratio <= 0.60) and funding <= 0.00008 and basis_rate <= 0.0006:
        return "Early Spot Accumulation"
    if context in {"context_coiled", "context_warm"} and impulse == "impulse_body_drive" and (taker_ratio is None or 0.58 <= taker_ratio <= 0.68) and (oi_60m is None or (-0.005 <= oi_60m <= 0.03)) and funding <= 0.00015 and basis_rate <= 0.0010:
        return "Spot Expansion"
    if oi_60m is not None and oi_60m >= 0.020 and funding >= 0.00008 and basis_rate >= 0.0005 and (taker_ratio is None or taker_ratio >= 0.60):
        return "Futures Momentum Build-up"
    if oi_60m is not None and oi_60m >= 0.050 and funding >= 0.00020 and (basis_rate >= 0.0010 or premium >= 0.0008):
        return "Derivative Overheating"
    if oi_60m is not None and oi_60m <= -0.010 and trigger_return >= 0.05 and (taker_ratio is None or taker_ratio >= 0.62):
        return "Short Squeeze"
    if upper_wick >= 0.30 and close_to_high <= 0.55 and funding >= 0.00010 and (oi_60m is None or oi_60m >= -0.005):
        return "Distribution Pump"
    if upper_wick >= 0.20 and close_to_high <= 0.45 and trigger_return >= 0.02:
        return "False Breakout"
    if upper_wick >= 0.18 and funding >= 0.00015 and basis_rate >= 0.0006 and trigger_return >= 0.05:
        return "Exhaustion Blow-off"
    if volume_mult >= 6.0 and upper_wick >= 0.15 and close_to_high <= 0.70 and (oi_60m is None or oi_60m >= 0.0):
        return "Absorption Pump"
    if trigger_return >= 0.05 and volume_mult >= 5.0 and impulse == "impulse_body_drive":
        return "News Repricing"
    if oi_60m is not None and oi_60m >= 0.03 and funding >= 0.00012:
        return "Aggressive Long Build-up"
    return "Unclassified Pump"


def _load_raw_symbol_map() -> dict[str, str]:
    manifest_path = LOADER_DIR / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = pd.read_json(manifest_path)
    _ = manifest  # placeholder to keep function simple if manifest format changes
    mapping: dict[str, str] = {}
    for endpoint_dir in RAW_DIR.glob("*"):
        if not endpoint_dir.is_dir():
            continue
        for file_path in endpoint_dir.glob("*.csv"):
            raw_symbol = file_path.stem
            mapping.setdefault(raw_symbol, raw_symbol)
    return mapping


def run(*, days: int = 14) -> dict[str, Path]:
    logger = module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    anomalies = _load_recent_anomalies(days)
    if anomalies.empty:
        raise ValueError("Нет recent anomalies")

    raw_symbol_map = _load_raw_symbol_map()
    anomalies["raw_symbol"] = anomalies["symbol"].astype(str).map(lambda value: value.replace("/USDT:USDT", "USDT").replace("/USDT", "USDT"))

    rows: list[dict[str, object]] = []
    grouped = anomalies.groupby("raw_symbol", sort=True)
    total = grouped.ngroups
    for index, (raw_symbol, scoped) in enumerate(grouped, start=1):
        endpoint_frames = {endpoint: _load_symbol_endpoint(endpoint, raw_symbol) for endpoint in ENDPOINT_FILES}
        if index % 50 == 0 or index == total:
            logger.info("recent-market-regime progress: %s/%s", index, total)
        for _, row in scoped.iterrows():
            event_ts = int(row["timestamp_ms"])
            record = row.to_dict()
            record["taker_buy_sell_ratio"] = _lookup_last(endpoint_frames["taker"], event_ts, "taker_buy_sell_ratio")
            record["taker_buy_vol"] = _lookup_last(endpoint_frames["taker"], event_ts, "taker_buy_vol")
            record["taker_sell_vol"] = _lookup_last(endpoint_frames["taker"], event_ts, "taker_sell_vol")
            record["global_long_short_ratio"] = _lookup_last(endpoint_frames["global_ls"], event_ts, "global_long_short_ratio")
            record["top_account_long_short_ratio"] = _lookup_last(endpoint_frames["top_account"], event_ts, "top_account_long_short_ratio")
            record["top_position_long_short_ratio"] = _lookup_last(endpoint_frames["top_position"], event_ts, "top_position_long_short_ratio")
            record["basis_rate"] = _lookup_last(endpoint_frames["basis"], event_ts, "basis_rate")
            record["annualized_basis_rate"] = _lookup_last(endpoint_frames["basis"], event_ts, "annualized_basis_rate")
            record["basis_abs"] = _lookup_last(endpoint_frames["basis"], event_ts, "basis")
            record["premium_close"] = _lookup_last(endpoint_frames["premium"], event_ts, "premium_close")
            record["funding_rate_last"] = _lookup_last(endpoint_frames["funding"], event_ts, "funding_rate")
            record["oi_at_event"] = _lookup_last(endpoint_frames["oi"], event_ts, "open_interest_value")
            record["oi_delta_15m_pct"] = _lookup_delta_pct(endpoint_frames["oi"], event_ts, "open_interest_value", 3)
            record["oi_delta_60m_pct"] = _lookup_delta_pct(endpoint_frames["oi"], event_ts, "open_interest_value", 12)
            record["oi_delta_24h_pct"] = _lookup_delta_pct(endpoint_frames["oi"], event_ts, "open_interest_value", 288)
            record["market_regime"] = _assign_market_regime(pd.Series(record))
            rows.append(record)

    dataset = pd.DataFrame(rows)
    coverage = pd.DataFrame(
        [
            {
                "field": field,
                "coverage": float(pd.to_numeric(dataset[field], errors="coerce").notna().mean()) if field in dataset.columns else 0.0,
            }
            for field in [
                "oi_at_event",
                "oi_delta_15m_pct",
                "oi_delta_60m_pct",
                "taker_buy_sell_ratio",
                "global_long_short_ratio",
                "top_account_long_short_ratio",
                "top_position_long_short_ratio",
                "basis_rate",
                "premium_close",
                "funding_rate_last",
            ]
        ]
    )
    regime_summary = (
        dataset.groupby(["session_id", "market_regime"], as_index=False)
        .agg(
            anomalies=("symbol", "count"),
            symbols=("symbol", "nunique"),
            trades=("trade_triggered", lambda values: int(pd.Series(values).sum())),
            mean_return_pct=("exit_return_pct", "mean"),
            win_rate=("exit_return_pct", lambda values: float((pd.to_numeric(values, errors="coerce") > 0).mean())),
        )
        .sort_values(["trades", "anomalies"], ascending=[False, False])
        .reset_index(drop=True)
    )

    def _markdown(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "_empty_"
        scoped = frame.copy()
        for column in scoped.columns:
            if pd.api.types.is_float_dtype(scoped[column]):
                scoped[column] = scoped[column].map(lambda value: f"{value:.4f}" if pd.notna(value) else "")
        header = "| " + " | ".join(map(str, scoped.columns.tolist())) + " |"
        divider = "| " + " | ".join(["---"] * len(scoped.columns)) + " |"
        rows_md = [
            "| " + " | ".join("" if pd.isna(value) else str(value) for value in row) + " |"
            for row in scoped.itertuples(index=False, name=None)
        ]
        return "\n".join([header, divider, *rows_md])

    report = "\n".join(
        [
            "# Recent Market Regime Lab",
            "",
            f"- days: `{days}`",
            f"- anomalies: `{len(dataset)}`",
            "",
            "## Coverage",
            "",
            _markdown(coverage),
            "",
            "## Regime Summary",
            "",
            _markdown(regime_summary.head(40)),
        ]
    )

    paths = {
        "dataset": OUTPUT_DIR / f"recent_market_regime_dataset_{days}d.csv",
        "coverage": OUTPUT_DIR / f"coverage_{days}d.csv",
        "summary": OUTPUT_DIR / f"regime_summary_{days}d.csv",
        "report": OUTPUT_DIR / f"report_{days}d.md",
    }
    dataset.to_csv(paths["dataset"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    regime_summary.to_csv(paths["summary"], index=False)
    paths["report"].write_text(report, encoding="utf-8")
    return paths


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build recent pump market-regime dataset from cached derivatives metrics.")
    parser.add_argument("--days", type=int, default=14)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(days=int(args.days))
