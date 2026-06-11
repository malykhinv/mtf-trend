"""Build a short-fade event store and search robust plateaus.

The engine is intentionally split into two machines:

1. Event builder: convert existing replay artifacts into an immutable compact
   event/outcome store with known-at-entry feature contracts.
2. Plateau researcher: scan entry-known rules over the event store, evaluate
   rolling WFA, cluster broad parameter zones, and build marginal portfolios.

This script is designed to run on a 16GB workstation by reading selected
columns, caching Parquet, and scanning compact outcome rows instead of raw 1m
candles for every parameter combination.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.session_edge_workbench import _top_remove_pct_to_negative


DEFAULT_OUTPUT_DIR = Path(".output/research_cache/short_robust_plateau_engine")
RUN_365D_OUTPUT_DIR = Path(".output/research_cache/short_robust_plateau_engine_365d")
DEFAULT_ARCHIVE_DIR = Path("research/archive/2026-06-11_short_fade_manual_research")
MANUAL_RESEARCH_DOCS = [
    Path("research/SHORT_FADE_CORE_EDGE.md"),
    Path("research/SHORT_FADE_DIVERSIFIED_SLEEVES.md"),
    Path("research/SHORT_FADE_CROSS_SOURCE_SLEEVES.md"),
    Path("research/SHORT_FADE_SESSION_TIMING_DEEP_DIVE.md"),
]

SESSION_RULES = {
    "all": lambda df: pd.Series(True, index=df.index),
    "asia_only": lambda df: df["session_bucket"].eq("asia_only"),
    "europe_only": lambda df: df["session_bucket"].eq("europe_only"),
    "europe_us_overlap": lambda df: df["session_bucket"].eq("europe_us_overlap"),
    "us_only": lambda df: df["session_bucket"].eq("us_only"),
    "off_session": lambda df: df["session_bucket"].eq("off_session"),
    "non_us": lambda df: ~df["session_bucket"].eq("us_only"),
    "not_europe_only": lambda df: ~df["session_bucket"].eq("europe_only"),
    "not_asia_overlap": lambda df: ~df["session_bucket"].eq("asia_europe_overlap"),
}

RISK_BUCKETS = {
    "risk_all": (None, None),
    "risk_1_3": (0.01, 0.03),
    "risk_2_5": (0.02, 0.05),
    "risk_3_8": (0.03, 0.08),
    "risk_5_12": (0.05, 0.12),
}

ENTRY_FEATURES = {
    "source",
    "nature_id",
    "session_bucket",
    "entry_timestamp_ms",
    "initial_risk_pct",
    "stop_model",
    "management_id",
    "structural_break_depth_pct",
    "failed_retest_distance_pct",
    "minutes_from_seed_to_break",
    "confirm_taker_buy_share",
    "post_dist",
    "seed_dist",
    "delay_min",
    "close_ret_10m",
    "close_ret_15m",
    "high_ret_15m",
    "wick_ret_15m",
    "pre60_range_pct",
    "pre60_return_pct",
    "early_return_pct",
    "early_quote_ratio_24h_scaled",
    "early_trade_ratio_24h_scaled",
    "m1_taker_buy_quote_share",
    "m1_quote_top1_share",
    "m1_trade_top1_share",
    "m1_last2_trade_share",
    "m1_sustain_strict",
    "base_touched_before_entry",
    "deep2_touched_before_entry",
    "fader_classifier_15",
}

EVALUATION_ONLY_FEATURES = {
    "outcome_class",
    "runner10_label",
    "fader_label",
    "future60_low_break_offset_min",
    "eval_timing_bucket",
}


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    source: str
    nature_id: str
    session_rule: str
    stop_model: str
    management_id: str
    risk_bucket: str
    trigger_rule: str


def _read_csv_selected(path: Path, usecols: list[str], *, nrows: int | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    available = pd.read_csv(path, nrows=0).columns.tolist()
    cols = [col for col in usecols if col in available]
    if not cols:
        return pd.DataFrame()
    return pd.read_csv(path, usecols=cols, nrows=nrows)


def _norm_symbol(symbol: object) -> str:
    return str(symbol).replace("/", "").replace(":USDT", "")


def _to_datetime_utc(value: pd.Series) -> pd.Series:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _series_or(df: pd.DataFrame, column: str, default: object) -> pd.Series:
    if column in df.columns:
        return df[column]
    return pd.Series(default, index=df.index)


def _num(df: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def _bool_series(df: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype="bool")
    raw = df[column]
    if raw.dtype == bool:
        return raw.fillna(default)
    return raw.astype(str).str.lower().isin(["true", "1", "yes"]).fillna(default)


def _max_drawdown(vals: pd.Series) -> float:
    arr = pd.to_numeric(vals, errors="coerce").fillna(0.0).to_numpy()
    if len(arr) == 0:
        return float("nan")
    curve = np.cumsum(arr)
    peak = np.maximum.accumulate(curve)
    return float((curve - peak).min())


def _profit_factor(vals: pd.Series) -> float:
    clean = pd.to_numeric(vals, errors="coerce").dropna()
    wins = float(clean[clean > 0].sum())
    losses = float(-clean[clean < 0].sum())
    if losses <= 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def _summary(df: pd.DataFrame, value_col: str = "net_r", *, robust: bool = True) -> dict[str, float | int]:
    if df.empty:
        return {
            "trades": 0,
            "symbols": 0,
            "days": 0,
            "avg_r": float("nan"),
            "median_r": float("nan"),
            "sum_r": 0.0,
            "win_rate": float("nan"),
            "positive_day_rate": float("nan"),
            "profit_factor": float("nan"),
            "top_trade_independence_pct": 0.0,
            "top_symbol_independence_pct": 0.0,
            "max_drawdown_r": float("nan"),
            "daily_sharpe": float("nan"),
        }
    vals = pd.to_numeric(df[value_col], errors="coerce").dropna()
    daily = df.groupby("date")[value_col].sum().sort_index()
    daily_std = float(daily.std(ddof=0)) if len(daily) else float("nan")
    out = {
        "trades": int(len(df)),
        "symbols": int(df["symbol"].nunique()),
        "days": int(df["date"].nunique()),
        "avg_r": float(vals.mean()) if len(vals) else float("nan"),
        "median_r": float(vals.median()) if len(vals) else float("nan"),
        "sum_r": float(vals.sum()) if len(vals) else 0.0,
        "win_rate": float((vals > 0).mean()) if len(vals) else float("nan"),
        "positive_day_rate": float((daily > 0).mean()) if len(daily) else float("nan"),
        "profit_factor": _profit_factor(vals),
        "daily_sharpe": float(daily.mean() / daily_std) if daily_std and np.isfinite(daily_std) else float("nan"),
    }
    if robust:
        trade_pct, removed_trades, winning_trades = _top_remove_pct_to_negative(vals)
        symbol_pct, removed_symbols, winning_symbols = _top_remove_pct_to_negative(df.groupby("symbol")[value_col].sum())
        out.update(
            {
                "top_trade_independence_pct": float(trade_pct),
                "removed_top_trades_to_negative": int(removed_trades),
                "winning_trades": int(winning_trades),
                "top_symbol_independence_pct": float(symbol_pct),
                "removed_top_symbols_to_negative": int(removed_symbols),
                "winning_symbols": int(winning_symbols),
                "max_drawdown_r": _max_drawdown(df.sort_values("entry_timestamp_ms")[value_col]),
            }
        )
    else:
        out.update(
            {
                "top_trade_independence_pct": 0.0,
                "removed_top_trades_to_negative": 0,
                "winning_trades": int((vals > 0).sum()) if len(vals) else 0,
                "top_symbol_independence_pct": 0.0,
                "removed_top_symbols_to_negative": 0,
                "winning_symbols": int((df.groupby("symbol")[value_col].sum() > 0).sum()) if not df.empty else 0,
                "max_drawdown_r": float("nan"),
            }
        )
    return out


def _quality_score(metrics: dict[str, float | int]) -> float:
    trades = float(metrics.get("trades", 0) or 0)
    if trades <= 0:
        return -1.0
    def finite(name: str, default: float = 0.0) -> float:
        value = float(metrics.get(name, default) or default)
        return value if np.isfinite(value) else default

    avg = finite("avg_r")
    med = finite("median_r")
    win_rate = finite("win_rate")
    pos_day = finite("positive_day_rate")
    pf = finite("profit_factor")
    if not np.isfinite(pf):
        pf = 4.0
    trade_ind = min(finite("top_trade_independence_pct"), 0.50) / 0.50
    symbol_ind = min(finite("top_symbol_independence_pct"), 0.50) / 0.50
    breadth = min(trades / 100.0, 1.0)
    dd = min(abs(finite("max_drawdown_r")) / 12.0, 1.0)
    return float(
        0.18 * np.tanh(avg / 0.20)
        + 0.18 * np.tanh(med / 0.20)
        + 0.13 * max(min(win_rate, 1.0), 0.0)
        + 0.13 * max(min(pos_day, 1.0), 0.0)
        + 0.10 * np.tanh((pf - 1.0) / 1.0)
        + 0.12 * trade_ind
        + 0.08 * symbol_ind
        + 0.08 * breadth
        - 0.14 * dd
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def archive_manual_knowledge(archive_dir: Path = DEFAULT_ARCHIVE_DIR) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in MANUAL_RESEARCH_DOCS:
        if src.exists():
            dst = archive_dir / src.name
            shutil.copy2(src, dst)
            copied.append(str(dst).replace("\\", "/"))
    readme = archive_dir / "README.md"
    readme.write_text(
        "# Short Fade Manual Research Archive\n\n"
        "Status: useful prior knowledge, not a clean discovery input.\n\n"
        "These files preserve the manual 365d research conclusions. The robust\n"
        "plateau engine must not privilege these exact rules during neutral\n"
        "candidate generation. They can be used to compare final reports and to\n"
        "define research risks.\n\n"
        "Archived files:\n\n"
        + "\n".join(f"- `{Path(item).name}`" for item in copied)
        + "\n",
        encoding="utf-8",
    )


def build_failed_outcomes(failed_dir: Path, *, max_rows: int | None = None) -> pd.DataFrame:
    path = failed_dir / "failed_pump_structural_management_replay_trades.csv"
    usecols = [
        "status",
        "policy",
        "symbol",
        "signal_id",
        "setup_id",
        "family",
        "stop_model",
        "entry_timestamp_ms",
        "entry_time_utc",
        "date",
        "month",
        "session_bucket",
        "signal_variant",
        "pump_tier",
        "entry_price",
        "initial_stop",
        "initial_risk_pct",
        "net_r",
        "mfe_r",
        "mae_r",
        "exit_timestamp_ms",
        "exit_reason",
        "hold_minutes",
        "partial_taken",
        "structural_break_depth_pct",
        "failed_retest_distance_pct",
        "minutes_from_seed_to_break",
        "post_dist",
        "seed_dist",
        "confirm_taker_buy_share",
        "confirm_quote_ratio_vs_norm",
        "confirm_trade_ratio_vs_norm",
    ]
    df = _read_csv_selected(path, usecols, nrows=max_rows)
    if df.empty:
        return df
    df = df[df["status"].astype(str).eq("closed")].copy()
    df["source"] = "failed_pump_structural"
    df["event_id"] = "fps:" + _series_or(df, "signal_id", "").astype(str)
    missing_signal = df["event_id"].eq("fps:")
    if missing_signal.any():
        df.loc[missing_signal, "event_id"] = (
            "fps:"
            + _series_or(df.loc[missing_signal], "setup_id", "").astype(str)
            + ":"
            + _series_or(df.loc[missing_signal], "entry_timestamp_ms", "").astype(str)
        )
    df["nature_id"] = _series_or(df, "family", "unknown").astype(str)
    df["trigger_id"] = _series_or(df, "signal_variant", "unknown").astype(str)
    df["management_id"] = _series_or(df, "policy", "unknown").astype(str)
    df["entry_timestamp_ms"] = _num(df, "entry_timestamp_ms")
    df["known_at_ms"] = df["entry_timestamp_ms"]
    df["feature_available_at_ms"] = df["entry_timestamp_ms"]
    df["date"] = pd.to_datetime(df["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce").dt.normalize()
    df["month"] = df["date"].dt.tz_localize(None).dt.to_period("M").astype(str)
    df["symbol_norm"] = df["symbol"].map(_norm_symbol)
    df["entry_model_id"] = "structural_next_open"
    df["data_quality_status"] = "replay_artifact"
    df["base_touched_before_entry"] = False
    df["deep2_touched_before_entry"] = False
    df["fader_classifier_15"] = False
    risk = _num(df, "initial_risk_pct")
    df["cost10_r"] = _num(df, "net_r") - (0.001 / risk.replace(0, np.nan))
    return _normalize_outcome_columns(df)


def _entry_known_large_desc(df: pd.DataFrame) -> pd.Series:
    desc = _series_or(df, "candidate_desc", "").astype(str)
    delay = _num(df, "delay_min")
    requires_15 = desc.str.contains("close15", case=False, na=False)
    requires_10 = desc.str.contains("close10", case=False, na=False)
    return (~requires_15 | delay.ge(15)) & (~requires_10 | delay.ge(10))


def build_large_outcomes(large_dir: Path, *, max_rows: int | None = None) -> pd.DataFrame:
    replay_path = large_dir / "large_runner_local_high_structural_exit_replay_trades.csv"
    setup_path = large_dir / "large_runner_session_outcome_research_table.csv"
    replay_cols = [
        "candidate_id",
        "candidate_desc",
        "delay_min",
        "stop_model",
        "exit_model",
        "symbol",
        "setup_key",
        "seed_close_ms",
        "seed_close_utc",
        "runner10_label",
        "fader_label",
        "status",
        "exit_reason",
        "net_r",
        "mfe_r",
        "mae_r",
        "entry_price",
        "initial_stop",
        "initial_risk_pct",
        "partial_taken",
        "trail_updates",
        "exit_timestamp_ms",
        "exit_price",
        "target_valid",
        "target_price",
        "skip_reason",
    ]
    setup_cols = [
        "symbol",
        "seed_close_ms",
        "seed_close_utc",
        "session_bucket",
        "outcome_class",
        "future60_low_break_offset_min",
        "runner_high10_hit_offset_min",
        "base_touch_offset_min",
        "deep2_touch_offset_min",
        "close_ret_10m",
        "close_ret_15m",
        "high_ret_15m",
        "wick_ret_15m",
        "pre60_range_pct",
        "pre60_return_pct",
        "early_return_pct",
        "early_quote_ratio_24h_scaled",
        "early_trade_ratio_24h_scaled",
        "m1_taker_buy_quote_share",
        "m1_quote_top1_share",
        "m1_trade_top1_share",
        "m1_last2_trade_share",
        "m1_sustain_strict",
    ]
    replay = _read_csv_selected(replay_path, replay_cols, nrows=max_rows)
    if replay.empty:
        return replay
    setup = _read_csv_selected(setup_path, setup_cols)
    replay = replay[replay["status"].astype(str).eq("closed")].copy()
    if not setup.empty:
        df = replay.merge(setup, on=["symbol", "seed_close_ms", "seed_close_utc"], how="left", validate="many_to_one")
    else:
        df = replay
    df = df[_entry_known_large_desc(df)].copy()
    df["source"] = "large_runner_local_high"
    df["event_id"] = "lr:" + df["symbol"].astype(str) + ":" + _series_or(df, "seed_close_ms", "").astype(str)
    df["nature_id"] = _series_or(df, "candidate_desc", "unknown").astype(str)
    df["trigger_id"] = df["nature_id"]
    df["management_id"] = _series_or(df, "exit_model", "unknown").astype(str)
    delay = _num(df, "delay_min").fillna(0)
    seed_close = _num(df, "seed_close_ms")
    df["entry_timestamp_ms"] = seed_close + delay * 60_000
    df["known_at_ms"] = df["entry_timestamp_ms"]
    df["feature_available_at_ms"] = df["entry_timestamp_ms"]
    df["date"] = pd.to_datetime(df["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce").dt.normalize()
    df["month"] = df["date"].dt.tz_localize(None).dt.to_period("M").astype(str)
    df["symbol_norm"] = df["symbol"].map(_norm_symbol)
    df["entry_model_id"] = "delay_next_open_proxy"
    df["data_quality_status"] = "replay_artifact"
    df["base_touched_before_entry"] = _num(df, "base_touch_offset_min").le(delay).fillna(False)
    df["deep2_touched_before_entry"] = _num(df, "deep2_touch_offset_min").le(delay).fillna(False)
    df["fader_classifier_15"] = delay.ge(15) & _num(df, "close_ret_15m").le(0.0339) & _num(df, "pre60_range_pct").ge(0.10)
    low_offset = _num(df, "future60_low_break_offset_min")
    rel = low_offset - delay
    timing = pd.Series("no_low_break", index=df.index)
    timing[low_offset.notna() & rel.lt(0)] = "already_faded_before_entry"
    timing[low_offset.notna() & rel.ge(0) & rel.le(5)] = "fade_0_5_after_entry"
    timing[low_offset.notna() & rel.gt(5) & rel.le(10)] = "fade_5_10_after_entry"
    timing[low_offset.notna() & rel.gt(10) & rel.le(20)] = "fade_10_20_after_entry"
    timing[low_offset.notna() & rel.gt(20)] = "fade_late_20plus"
    df["eval_timing_bucket"] = timing
    risk = _num(df, "initial_risk_pct")
    df["cost10_r"] = _num(df, "net_r") - (0.001 / risk.replace(0, np.nan))
    return _normalize_outcome_columns(df)


def _normalize_outcome_columns(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    required_defaults: dict[str, object] = {
        "event_id": "",
        "source": "",
        "nature_id": "",
        "trigger_id": "",
        "symbol": "",
        "symbol_norm": "",
        "entry_timestamp_ms": np.nan,
        "known_at_ms": np.nan,
        "feature_available_at_ms": np.nan,
        "date": pd.NaT,
        "month": "",
        "session_bucket": "unknown",
        "entry_model_id": "unknown",
        "stop_model": "unknown",
        "management_id": "unknown",
        "initial_risk_pct": np.nan,
        "net_r": np.nan,
        "cost10_r": np.nan,
        "mfe_r": np.nan,
        "mae_r": np.nan,
        "exit_reason": "",
        "data_quality_status": "",
        "structural_break_depth_pct": np.nan,
        "failed_retest_distance_pct": np.nan,
        "minutes_from_seed_to_break": np.nan,
        "confirm_taker_buy_share": np.nan,
        "post_dist": "",
        "seed_dist": "",
        "delay_min": np.nan,
        "close_ret_10m": np.nan,
        "close_ret_15m": np.nan,
        "high_ret_15m": np.nan,
        "wick_ret_15m": np.nan,
        "pre60_range_pct": np.nan,
        "pre60_return_pct": np.nan,
        "early_return_pct": np.nan,
        "early_quote_ratio_24h_scaled": np.nan,
        "early_trade_ratio_24h_scaled": np.nan,
        "m1_taker_buy_quote_share": np.nan,
        "m1_quote_top1_share": np.nan,
        "m1_trade_top1_share": np.nan,
        "m1_last2_trade_share": np.nan,
        "m1_sustain_strict": False,
        "base_touched_before_entry": False,
        "deep2_touched_before_entry": False,
        "fader_classifier_15": False,
        "outcome_class": "",
        "runner10_label": False,
        "fader_label": False,
        "future60_low_break_offset_min": np.nan,
        "eval_timing_bucket": "",
    }
    for col, default in required_defaults.items():
        if col not in normalized.columns:
            normalized[col] = default
    for col in [
        "entry_timestamp_ms",
        "known_at_ms",
        "feature_available_at_ms",
        "initial_risk_pct",
        "net_r",
        "cost10_r",
        "mfe_r",
        "mae_r",
        "structural_break_depth_pct",
        "failed_retest_distance_pct",
        "minutes_from_seed_to_break",
        "confirm_taker_buy_share",
        "delay_min",
        "close_ret_10m",
        "close_ret_15m",
        "high_ret_15m",
        "wick_ret_15m",
        "pre60_range_pct",
        "pre60_return_pct",
        "early_return_pct",
        "early_quote_ratio_24h_scaled",
        "early_trade_ratio_24h_scaled",
        "m1_taker_buy_quote_share",
        "m1_quote_top1_share",
        "m1_trade_top1_share",
        "m1_last2_trade_share",
        "future60_low_break_offset_min",
    ]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    for col in ["base_touched_before_entry", "deep2_touched_before_entry", "fader_classifier_15", "runner10_label", "fader_label", "m1_sustain_strict"]:
        normalized[col] = normalized[col].astype(str).str.lower().isin(["true", "1", "yes"])
    normalized["date"] = pd.to_datetime(normalized["date"], utc=True, errors="coerce").dt.normalize()
    normalized = normalized.dropna(subset=["entry_timestamp_ms", "date", "net_r"]).copy()
    return normalized[list(required_defaults)]


def build_event_store(
    failed_dir: Path,
    large_dir: Path,
    output_dir: Path,
    *,
    max_rows_per_source: int | None = None,
) -> pd.DataFrame:
    output_dir.mkdir(parents=True, exist_ok=True)
    failed = build_failed_outcomes(failed_dir, max_rows=max_rows_per_source)
    large = build_large_outcomes(large_dir, max_rows=max_rows_per_source)
    frames = [frame for frame in [failed, large] if not frame.empty]
    if not frames:
        raise FileNotFoundError("No supported replay artifacts were found.")
    outcomes = pd.concat(frames, ignore_index=True)
    outcomes = outcomes.sort_values(["entry_timestamp_ms", "source", "event_id"]).reset_index(drop=True)
    events = outcomes.sort_values("entry_timestamp_ms").drop_duplicates("event_id", keep="first")
    outcomes.to_parquet(output_dir / "event_outcomes.parquet", index=False)
    events.to_parquet(output_dir / "events.parquet", index=False)
    metadata = {
        "schema_version": "short_robust_plateau_event_store_v1",
        "rows": int(len(outcomes)),
        "events": int(events["event_id"].nunique()),
        "sources": outcomes["source"].value_counts().to_dict(),
        "entry_features": sorted(ENTRY_FEATURES),
        "evaluation_only_features": sorted(EVALUATION_ONLY_FEATURES),
        "limits": {"max_rows_per_source": max_rows_per_source},
    }
    _write_json(output_dir / "metadata.json", metadata)
    _write_data_quality_report(output_dir, outcomes)
    return outcomes


def _write_data_quality_report(output_dir: Path, outcomes: pd.DataFrame) -> None:
    source_counts = outcomes["source"].value_counts().to_string()
    session_counts = outcomes["session_bucket"].value_counts(dropna=False).to_string()
    missing = outcomes[list(sorted(ENTRY_FEATURES & set(outcomes.columns)))].isna().mean().sort_values(ascending=False)
    text = f"""# Short Robust Plateau Data Quality

Source counts:

```text
{source_counts}
```

Session counts:

```text
{session_counts}
```

Top missing entry-known feature rates:

```text
{missing.head(30).to_string()}
```

Limitations:

```text
This event store is built from existing replay artifacts. It is suitable for
plateau/WFA engine development and candidate rejection. It is not a fresh OOS
proof and does not replace a true path replay for new lower-high triggers.
```
"""
    (output_dir / "data_quality_report.md").write_text(text, encoding="utf-8")


def _load_outcomes(output_dir: Path) -> pd.DataFrame:
    path = output_dir / "event_outcomes.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_parquet(path)


def _risk_mask(df: pd.DataFrame, bucket: str) -> pd.Series:
    lo, hi = RISK_BUCKETS[bucket]
    risk = _num(df, "initial_risk_pct")
    mask = pd.Series(True, index=df.index)
    if lo is not None:
        mask &= risk.ge(lo)
    if hi is not None:
        mask &= risk.le(hi)
    return mask


def _trigger_rules(df: pd.DataFrame) -> dict[str, pd.Series]:
    break_depth = _num(df, "structural_break_depth_pct")
    retest = _num(df, "failed_retest_distance_pct")
    break_min = _num(df, "minutes_from_seed_to_break")
    taker = _num(df, "confirm_taker_buy_share")
    delay = _num(df, "delay_min")
    close10 = _num(df, "close_ret_10m")
    close15 = _num(df, "close_ret_15m")
    pre60_range = _num(df, "pre60_range_pct")
    pre60_ret = _num(df, "pre60_return_pct")
    m1_taker = _num(df, "m1_taker_buy_quote_share")
    last2_trade = _num(df, "m1_last2_trade_share")
    return {
        "trigger_all": pd.Series(True, index=df.index),
        "failed_break_le_15": df["source"].eq("failed_pump_structural") & break_min.le(15),
        "failed_break_le_20": df["source"].eq("failed_pump_structural") & break_min.le(20),
        "failed_break_ge_1p5": df["source"].eq("failed_pump_structural") & break_depth.ge(0.015),
        "failed_break_ge_2p0": df["source"].eq("failed_pump_structural") & break_depth.ge(0.020),
        "failed_retest_ge_0p8": df["source"].eq("failed_pump_structural") & retest.ge(0.008),
        "failed_fast_break_retest": df["source"].eq("failed_pump_structural") & break_min.le(20) & retest.ge(0.008),
        "failed_taker_45_55": df["source"].eq("failed_pump_structural") & taker.between(0.45, 0.55, inclusive="both"),
        "large_fader15": df["source"].eq("large_runner_local_high") & delay.ge(15) & close15.le(0.0339) & pre60_range.ge(0.10),
        "large_fader15_no_base": df["source"].eq("large_runner_local_high") & delay.ge(15) & close15.le(0.0339) & pre60_range.ge(0.10) & ~df["base_touched_before_entry"].fillna(False),
        "large_close15_le0": df["source"].eq("large_runner_local_high") & delay.ge(15) & close15.le(0.0) & pre60_range.ge(0.10),
        "large_close10_le2": df["source"].eq("large_runner_local_high") & delay.ge(10) & close10.le(0.02),
        "large_taker_45_55": df["source"].eq("large_runner_local_high") & m1_taker.between(0.45, 0.55, inclusive="both"),
        "large_pre60_down": df["source"].eq("large_runner_local_high") & pre60_ret.le(0),
        "large_last2_trade_le45": df["source"].eq("large_runner_local_high") & last2_trade.le(0.45),
    }


def _candidate_mask(df: pd.DataFrame, spec: CandidateSpec, trigger_masks: dict[str, pd.Series]) -> pd.Series:
    mask = df["source"].eq(spec.source)
    if spec.nature_id != "ALL":
        mask &= df["nature_id"].astype(str).eq(spec.nature_id)
    mask &= SESSION_RULES[spec.session_rule](df)
    if spec.stop_model != "ALL":
        mask &= df["stop_model"].astype(str).eq(spec.stop_model)
    if spec.management_id != "ALL":
        mask &= df["management_id"].astype(str).eq(spec.management_id)
    mask &= _risk_mask(df, spec.risk_bucket)
    mask &= trigger_masks[spec.trigger_rule]
    return mask.fillna(False)


def _ordered_existing(preferred: list[str], available: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in preferred + available:
        if item in seen or item not in available:
            continue
        seen.add(item)
        out.append(item)
    return out


def _cap_specs_balanced(specs: list[CandidateSpec], max_specs: int | None) -> list[CandidateSpec]:
    if max_specs is None or len(specs) <= int(max_specs):
        return specs

    source_order: dict[str, int] = {}
    ranks: dict[tuple[str, str, str], int] = {}
    for spec in specs:
        source_order.setdefault(spec.source, len(source_order))
        for axis, value in [
            ("nature_id", spec.nature_id),
            ("session_rule", spec.session_rule),
            ("stop_model", spec.stop_model),
            ("management_id", spec.management_id),
            ("risk_bucket", spec.risk_bucket),
            ("trigger_rule", spec.trigger_rule),
        ]:
            key = (spec.source, axis, value)
            ranks.setdefault(key, len([existing for existing in ranks if existing[0] == spec.source and existing[1] == axis]))

    def layer_sort_key(spec: CandidateSpec) -> tuple:
        axis_ranks = [
            ranks[(spec.source, "nature_id", spec.nature_id)],
            ranks[(spec.source, "session_rule", spec.session_rule)],
            ranks[(spec.source, "stop_model", spec.stop_model)],
            ranks[(spec.source, "management_id", spec.management_id)],
            ranks[(spec.source, "risk_bucket", spec.risk_bucket)],
            ranks[(spec.source, "trigger_rule", spec.trigger_rule)],
        ]
        return (
            max(axis_ranks),
            sum(axis_ranks),
            source_order.get(spec.source, 99),
            *axis_ranks,
            spec.candidate_id,
        )

    def bucket_sort_key(key: tuple[str, str, str, str, str]) -> tuple:
        source, session, stop, management, risk = key
        axis_ranks = [
            ranks.get((source, "session_rule", session), 99),
            ranks.get((source, "stop_model", stop), 99),
            ranks.get((source, "management_id", management), 99),
            ranks.get((source, "risk_bucket", risk), 99),
        ]
        return (
            max(axis_ranks),
            sum(axis_ranks),
            source_order.get(source, 99),
            *axis_ranks,
            key,
        )

    buckets: dict[tuple[str, str, str, str, str], list[CandidateSpec]] = {}
    for spec in specs:
        key = (spec.source, spec.session_rule, spec.stop_model, spec.management_id, spec.risk_bucket)
        buckets.setdefault(key, []).append(spec)
    keys = sorted(buckets, key=bucket_sort_key)
    execution_grid: list[CandidateSpec] = []
    while len(execution_grid) < int(max_specs):
        added = False
        for key in keys:
            bucket = buckets[key]
            if not bucket:
                continue
            execution_grid.append(bucket.pop(0))
            added = True
            if len(execution_grid) >= int(max_specs):
                break
        if not added:
            break

    layer_grid = sorted(specs, key=layer_sort_key)
    selected: list[CandidateSpec] = []
    seen: set[str] = set()
    i = 0
    j = 0
    while len(selected) < int(max_specs) and (i < len(execution_grid) or j < len(layer_grid)):
        for grid_name in ["execution", "layer"]:
            if len(selected) >= int(max_specs):
                break
            grid = execution_grid if grid_name == "execution" else layer_grid
            cursor = i if grid_name == "execution" else j
            while cursor < len(grid) and grid[cursor].candidate_id in seen:
                cursor += 1
            if cursor >= len(grid):
                if grid_name == "execution":
                    i = cursor
                else:
                    j = cursor
                continue
            spec = grid[cursor]
            selected.append(spec)
            seen.add(spec.candidate_id)
            cursor += 1
            if grid_name == "execution":
                i = cursor
            else:
                j = cursor
    return selected


def _axis_values_for_source(
    source: str,
    source_df: pd.DataFrame,
    trigger_masks: dict[str, pd.Series],
    *,
    max_natures_per_source: int,
    candidate_profile: str,
) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
    natures = ["ALL"] + source_df["nature_id"].value_counts().head(max_natures_per_source).index.astype(str).tolist()
    stops_available = ["ALL"] + source_df["stop_model"].value_counts().head(12).index.astype(str).tolist()
    managements_available = ["ALL"] + source_df["management_id"].value_counts().head(12).index.astype(str).tolist()
    all_sessions = list(SESSION_RULES)
    all_risks = list(RISK_BUCKETS)
    trigger_prefix = "failed_" if source == "failed_pump_structural" else "large_"
    source_triggers = [name for name in trigger_masks if name == "trigger_all" or name.startswith(trigger_prefix)]

    if candidate_profile == "balanced_365d":
        sessions = _ordered_existing(
            ["all", "not_asia_overlap", "non_us", "asia_only", "europe_only", "europe_us_overlap", "us_only"],
            all_sessions,
        )
        risks = _ordered_existing(["risk_all", "risk_2_5", "risk_3_8", "risk_5_12", "risk_1_3"], all_risks)
        if source == "failed_pump_structural":
            triggers = _ordered_existing(
                [
                    "trigger_all",
                    "failed_break_le_15",
                    "failed_break_ge_2p0",
                    "failed_fast_break_retest",
                    "failed_break_le_20",
                    "failed_break_ge_1p5",
                    "failed_retest_ge_0p8",
                    "failed_taker_45_55",
                ],
                source_triggers,
            )
        else:
            triggers = _ordered_existing(
                [
                    "trigger_all",
                    "large_fader15",
                    "large_close10_le2",
                    "large_last2_trade_le45",
                    "large_fader15_no_base",
                    "large_close15_le0",
                    "large_taker_45_55",
                    "large_pre60_down",
                ],
                source_triggers,
            )
        stops = stops_available[:6]
        managements = managements_available[:7]
        return natures, sessions, stops, managements, risks, triggers

    return natures, all_sessions, stops_available[:9], managements_available[:9], all_risks, source_triggers


def generate_candidates(
    df: pd.DataFrame,
    *,
    max_natures_per_source: int = 12,
    max_specs: int | None = None,
    candidate_profile: str = "exhaustive",
) -> list[CandidateSpec]:
    specs: list[CandidateSpec] = []
    trigger_masks = _trigger_rules(df)
    for source, source_df in df.groupby("source", dropna=False):
        natures, sessions, stops, managements, risks, source_triggers = _axis_values_for_source(
            str(source),
            source_df,
            trigger_masks,
            max_natures_per_source=max_natures_per_source,
            candidate_profile=candidate_profile,
        )
        for nature in natures:
            for session in sessions:
                for stop in stops:
                    for management in managements:
                        for risk in risks:
                            for trigger in source_triggers:
                                cid = "|".join([str(source), nature, session, stop, management, risk, trigger])
                                specs.append(
                                    CandidateSpec(
                                        candidate_id=cid,
                                        source=str(source),
                                        nature_id=nature,
                                        session_rule=session,
                                        stop_model=stop,
                                        management_id=management,
                                        risk_bucket=risk,
                                        trigger_rule=trigger,
                                    )
                                )
    if candidate_profile == "balanced_365d":
        return _cap_specs_balanced(specs, max_specs)
    if max_specs is not None:
        return specs[: int(max_specs)]
    return specs


def _date_windows(
    df: pd.DataFrame,
    *,
    is_days: int,
    oos_days: int,
    step_days: int,
    final_holdout_days: int,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    dates = pd.to_datetime(df["date"], utc=True, errors="coerce").dropna()
    if dates.empty:
        return []
    start = dates.min().normalize()
    end = dates.max().normalize()
    dev_end = end - pd.Timedelta(days=final_holdout_days)
    windows = []
    train_start = start
    while True:
        train_end = train_start + pd.Timedelta(days=is_days)
        oos_end = train_end + pd.Timedelta(days=oos_days)
        if oos_end > dev_end:
            break
        windows.append((train_start, train_end, train_end, oos_end))
        train_start = train_start + pd.Timedelta(days=step_days)
    return windows


def _window_frame(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["date"] >= start) & (df["date"] < end)]


def _prefilter_candidate(cand: pd.DataFrame, min_is_trades: int) -> str | None:
    if len(cand) < min_is_trades:
        return "too_few_total_rows"
    min_breadth = max(4, min_is_trades // 5)
    if cand["symbol"].nunique() < min_breadth:
        return "too_few_symbols"
    if cand["date"].nunique() < min_breadth:
        return "too_few_days"
    cost_avg = float(pd.to_numeric(cand["cost10_r"], errors="coerce").mean())
    median_r = float(pd.to_numeric(cand["net_r"], errors="coerce").median())
    if np.isfinite(cost_avg) and np.isfinite(median_r) and cost_avg < -0.35 and median_r < -0.45:
        return "weak_full_distribution"
    return None


def scan_plateaus(
    output_dir: Path,
    *,
    is_days: int = 90,
    oos_days: int = 30,
    step_days: int = 30,
    final_holdout_days: int = 30,
    min_is_trades: int = 25,
    min_oos_trades: int = 8,
    max_candidates: int | None = None,
    max_natures_per_source: int = 12,
    candidate_profile: str = "exhaustive",
    progress_every: int = 0,
) -> None:
    df = _load_outcomes(output_dir)
    trigger_masks = _trigger_rules(df)
    specs = generate_candidates(
        df,
        max_natures_per_source=max_natures_per_source,
        max_specs=max_candidates,
        candidate_profile=candidate_profile,
    )
    windows = _date_windows(df, is_days=is_days, oos_days=oos_days, step_days=step_days, final_holdout_days=final_holdout_days)
    if not windows:
        raise ValueError("Not enough date coverage for requested WFA windows.")
    pd.DataFrame([spec.__dict__ for spec in specs]).to_csv(output_dir / "candidate_universe.csv", index=False)
    _write_json(
        output_dir / "scan_config.json",
        {
            "is_days": is_days,
            "oos_days": oos_days,
            "step_days": step_days,
            "final_holdout_days": final_holdout_days,
            "min_is_trades": min_is_trades,
            "min_oos_trades": min_oos_trades,
            "max_candidates": max_candidates,
            "max_natures_per_source": max_natures_per_source,
            "candidate_profile": candidate_profile,
            "candidate_universe_rows": len(specs),
            "wfa_windows": len(windows),
        },
    )

    wfa_rows: list[dict] = []
    candidate_rows: list[dict] = []
    rejected_rows: list[dict] = []
    ledgers: dict[str, pd.DataFrame] = {}

    for idx, spec in enumerate(specs):
        if progress_every and (idx == 0 or (idx + 1) % progress_every == 0 or idx + 1 == len(specs)):
            print(f"scan_progress={idx + 1}/{len(specs)} analyzed={len(candidate_rows)} rejected={len(rejected_rows)}", flush=True)
        mask = _candidate_mask(df, spec, trigger_masks)
        cand = df[mask]
        prefilter_reason = _prefilter_candidate(cand, min_is_trades)
        if prefilter_reason:
            rejected_rows.append({"candidate_id": spec.candidate_id, "reason": prefilter_reason, "rows": int(len(cand))})
            continue
        oos_parts = []
        is_scores = []
        oos_scores = []
        train_pass_count = 0
        for window_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            is_part = _window_frame(cand, is_start, is_end)
            oos_part = _window_frame(cand, oos_start, oos_end)
            is_m = _summary(is_part, "net_r", robust=False)
            is_cost = _summary(is_part, "cost10_r", robust=False)
            train_pass = (
                is_m["trades"] >= min_is_trades
                and is_m["symbols"] >= max(5, min_is_trades // 4)
                and is_m["days"] >= max(5, min_is_trades // 4)
                and float(is_cost["avg_r"]) > -0.02
                and float(is_m["median_r"]) > -0.20
            )
            if train_pass:
                train_pass_count += 1
            oos_m = _summary(oos_part, "net_r", robust=False) if train_pass else _summary(oos_part.iloc[0:0], "net_r", robust=False)
            oos_cost = _summary(oos_part, "cost10_r", robust=False) if train_pass else _summary(oos_part.iloc[0:0], "cost10_r", robust=False)
            is_score = _quality_score(is_m)
            oos_score = _quality_score(oos_m)
            is_scores.append(is_score)
            if train_pass and oos_m["trades"] > 0:
                oos_scores.append(oos_score)
                oos_parts.append(oos_part.assign(candidate_id=spec.candidate_id, wfa_window=window_idx))
            row = {
                "candidate_id": spec.candidate_id,
                "window_idx": window_idx,
                "is_start": is_start.date().isoformat(),
                "is_end": is_end.date().isoformat(),
                "oos_start": oos_start.date().isoformat(),
                "oos_end": oos_end.date().isoformat(),
                "train_pass": bool(train_pass),
                "is_score": is_score,
                "oos_score": oos_score,
                "is_cost10_avg_r": is_cost["avg_r"],
                "oos_cost10_avg_r": oos_cost["avg_r"],
            }
            row.update({f"is_{k}": v for k, v in is_m.items()})
            row.update({f"oos_{k}": v for k, v in oos_m.items()})
            wfa_rows.append(row)

        if not oos_parts:
            rejected_rows.append(
                {
                    "candidate_id": spec.candidate_id,
                    "reason": "no_train_pass_oos_rows",
                    "train_pass_count": int(train_pass_count),
                }
            )
            continue
        oos_all = pd.concat(oos_parts, ignore_index=True)
        oos_all = oos_all.drop_duplicates(["candidate_id", "event_id", "stop_model", "management_id"], keep="first")
        ledgers[spec.candidate_id] = oos_all
        oos_m = _summary(oos_all, "net_r")
        oos_cost = _summary(oos_all, "cost10_r")
        is_score_med = float(np.nanmedian(is_scores)) if is_scores else float("nan")
        oos_score_med = float(np.nanmedian(oos_scores)) if oos_scores else float("nan")
        efficiency = oos_score_med / is_score_med if is_score_med and np.isfinite(is_score_med) and is_score_med > 0 else float("nan")
        oos_window_rate = float(np.mean([score > 0 for score in oos_scores])) if oos_scores else 0.0
        promoted = (
            oos_m["trades"] >= min_oos_trades
            and float(oos_m["avg_r"]) > 0
            and float(oos_m["median_r"]) > 0
            and float(oos_cost["avg_r"]) > 0
            and float(oos_m["positive_day_rate"]) >= 0.50
            and float(oos_m["top_trade_independence_pct"]) >= 0.20
            and float(oos_m["top_symbol_independence_pct"]) >= 0.15
            and oos_window_rate >= 0.50
        )
        cand_row = {
            "candidate_id": spec.candidate_id,
            "source": spec.source,
            "nature_id": spec.nature_id,
            "session_rule": spec.session_rule,
            "stop_model": spec.stop_model,
            "management_id": spec.management_id,
            "risk_bucket": spec.risk_bucket,
            "trigger_rule": spec.trigger_rule,
            "train_pass_count": int(train_pass_count),
            "windows": int(len(windows)),
            "oos_window_positive_score_rate": oos_window_rate,
            "is_score_median": is_score_med,
            "oos_score_median": oos_score_med,
            "efficiency_ratio": efficiency,
            "oos_cost10_avg_r": oos_cost["avg_r"],
            "oos_cost10_sum_r": oos_cost["sum_r"],
            "candidate_score": _quality_score(oos_m) + min(max(efficiency if np.isfinite(efficiency) else 0.0, 0.0), 1.5) * 0.15,
            "promoted": bool(promoted),
        }
        cand_row.update({f"oos_{k}": v for k, v in oos_m.items()})
        candidate_rows.append(cand_row)
        if not promoted:
            rejected_rows.append(
                {
                    "candidate_id": spec.candidate_id,
                    "reason": _reject_reason(cand_row),
                    "oos_trades": int(oos_m["trades"]),
                    "oos_avg_r": float(oos_m["avg_r"]),
                    "oos_median_r": float(oos_m["median_r"]),
                    "oos_cost10_avg_r": float(oos_cost["avg_r"]),
                }
            )

    wfa = pd.DataFrame(wfa_rows)
    candidates = pd.DataFrame(candidate_rows)
    rejected = pd.DataFrame(rejected_rows)
    if not candidates.empty:
        candidates = candidates.sort_values(["promoted", "candidate_score", "oos_cost10_sum_r"], ascending=[False, False, False])
    clusters = _build_plateau_clusters(candidates)
    portfolio, portfolio_trades = _build_portfolio(candidates, ledgers)
    lookahead = _lookahead_audit(df, candidates)
    axis_summary = _build_candidate_axis_summary(candidates)

    wfa.to_csv(output_dir / "wfa_results.csv", index=False)
    candidates.to_csv(output_dir / "plateau_candidates.csv", index=False)
    clusters.to_csv(output_dir / "plateau_clusters.csv", index=False)
    axis_summary.to_csv(output_dir / "candidate_axis_summary.csv", index=False)
    rejected.to_csv(output_dir / "rejected_reasons.csv", index=False)
    portfolio.to_csv(output_dir / "portfolio_candidates.csv", index=False)
    portfolio_trades.to_csv(output_dir / "portfolio_oos_trades.csv", index=False)
    lookahead.to_csv(output_dir / "lookahead_audit.csv", index=False)
    _write_final_report(output_dir, candidates, clusters, portfolio, rejected, axis_summary, lookahead, windows)


def _reject_reason(row: dict) -> str:
    if int(row.get("oos_trades", 0) or 0) <= 0:
        return "no_oos_trades"
    if float(row.get("oos_cost10_avg_r", 0.0) or 0.0) <= 0:
        return "cost10_not_positive"
    if float(row.get("oos_median_r", 0.0) or 0.0) <= 0:
        return "median_not_positive"
    if float(row.get("oos_positive_day_rate", 0.0) or 0.0) < 0.50:
        return "positive_day_rate_low"
    if float(row.get("oos_top_trade_independence_pct", 0.0) or 0.0) < 0.20:
        return "top_trade_dependent"
    if float(row.get("oos_top_symbol_independence_pct", 0.0) or 0.0) < 0.15:
        return "top_symbol_dependent"
    if float(row.get("oos_window_positive_score_rate", 0.0) or 0.0) < 0.50:
        return "rolling_oos_inconsistent"
    return "quality_threshold"


def _build_plateau_clusters(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    work = candidates[candidates["promoted"].astype(bool)].copy()
    if work.empty:
        return pd.DataFrame(
            columns=[
                "plateau_id",
                "members",
                "source",
                "nature_id",
                "session_rule",
                "stop_model",
                "management_id",
                "trigger_rule",
                "best_candidate_id",
            ]
        )
    group_cols = ["source", "nature_id", "session_rule", "stop_model", "management_id", "trigger_rule"]
    rows = []
    for idx, (keys, group) in enumerate(work.groupby(group_cols, dropna=False), start=1):
        best = group.sort_values(["candidate_score", "oos_cost10_sum_r"], ascending=[False, False]).iloc[0]
        s = _summary_from_candidate_group(group)
        row = {
            "plateau_id": f"plateau_{idx:04d}",
            "members": int(len(group)),
            "risk_buckets": ",".join(sorted(group["risk_bucket"].astype(str).unique())),
            "best_candidate_id": best["candidate_id"],
            "best_candidate_score": float(best["candidate_score"]),
            "plateau_pass": bool(len(group) >= 2),
        }
        for col, value in zip(group_cols, keys if isinstance(keys, tuple) else (keys,)):
            row[col] = value
        row.update(s)
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["plateau_pass", "best_candidate_score"], ascending=[False, False])


def _summary_from_candidate_group(group: pd.DataFrame) -> dict[str, float]:
    return {
        "member_oos_avg_r_median": float(pd.to_numeric(group["oos_avg_r"], errors="coerce").median()),
        "member_oos_median_r_median": float(pd.to_numeric(group["oos_median_r"], errors="coerce").median()),
        "member_cost10_avg_r_median": float(pd.to_numeric(group["oos_cost10_avg_r"], errors="coerce").median()),
        "member_top_trade_ind_median": float(pd.to_numeric(group["oos_top_trade_independence_pct"], errors="coerce").median()),
        "member_top_symbol_ind_median": float(pd.to_numeric(group["oos_top_symbol_independence_pct"], errors="coerce").median()),
    }


def _build_candidate_axis_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(
            columns=[
                "axis",
                "value",
                "candidate_rows",
                "promoted_rows",
                "best_is_promoted",
                "best_candidate_score",
                "best_oos_cost10_avg_r",
                "best_oos_trades",
                "best_candidate_id",
            ]
        )
    rows = []
    for axis in ["source", "session_rule", "trigger_rule", "risk_bucket", "stop_model", "management_id"]:
        if axis not in candidates.columns:
            continue
        for value, group in candidates.groupby(axis, dropna=False):
            promoted = group[group["promoted"].astype(bool)]
            best_pool = promoted if not promoted.empty else group
            best = best_pool.sort_values(["candidate_score", "oos_cost10_sum_r"], ascending=[False, False]).iloc[0]
            rows.append(
                {
                    "axis": axis,
                    "value": value,
                    "candidate_rows": int(len(group)),
                    "promoted_rows": int(len(promoted)),
                    "best_is_promoted": bool(best["promoted"]),
                    "best_candidate_score": float(best["candidate_score"]),
                    "best_oos_cost10_avg_r": float(best["oos_cost10_avg_r"]),
                    "best_oos_trades": int(best["oos_trades"]),
                    "best_candidate_id": best["candidate_id"],
                }
            )
    out = pd.DataFrame(rows)
    return out.sort_values(["axis", "promoted_rows", "best_candidate_score"], ascending=[True, False, False])


def _remove_collisions(candidate: pd.DataFrame, selected: pd.DataFrame, *, window_ms: int = 60 * 60_000) -> pd.DataFrame:
    if candidate.empty or selected.empty:
        return candidate
    frames = []
    for symbol, group in candidate.groupby("symbol_norm", dropna=False):
        selected_times = pd.to_numeric(selected.loc[selected["symbol_norm"].eq(symbol), "entry_timestamp_ms"], errors="coerce").dropna().to_numpy()
        if len(selected_times) == 0:
            frames.append(group)
            continue
        keep = []
        for ts in pd.to_numeric(group["entry_timestamp_ms"], errors="coerce").to_numpy():
            keep.append(bool(np.nanmin(np.abs(selected_times - ts)) > window_ms))
        frames.append(group.loc[keep])
    return pd.concat(frames, ignore_index=True) if frames else candidate.iloc[0:0].copy()


def _build_portfolio(candidates: pd.DataFrame, ledgers: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
    usable = candidates[candidates["promoted"].astype(bool)].copy()
    if usable.empty:
        usable = candidates.sort_values(["candidate_score"], ascending=False).head(10).copy()
    selected = pd.DataFrame()
    rows = []
    for _, cand in usable.sort_values(["promoted", "candidate_score", "oos_cost10_sum_r"], ascending=[False, False, False]).head(60).iterrows():
        cid = str(cand["candidate_id"])
        ledger = ledgers.get(cid, pd.DataFrame()).copy()
        if ledger.empty:
            continue
        marginal = _remove_collisions(ledger, selected)
        marginal = marginal.drop_duplicates(["event_id", "source"], keep="first")
        m = _summary(marginal, "net_r")
        c = _summary(marginal, "cost10_r")
        accept = bool(m["trades"] >= 8 and float(m["median_r"]) > 0 and float(c["avg_r"]) > 0)
        rows.append(
            {
                "candidate_id": cid,
                "accepted": accept,
                "order": len(rows),
                "marginal_cost10_avg_r": c["avg_r"],
                "marginal_cost10_sum_r": c["sum_r"],
                **{f"marginal_{k}": v for k, v in m.items()},
            }
        )
        if accept:
            marginal = marginal.copy()
            marginal["portfolio_candidate_id"] = cid
            selected = pd.concat([selected, marginal], ignore_index=True)
            selected = selected.drop_duplicates(["event_id", "source"], keep="first")
        if len(rows) >= 12:
            break
    portfolio = pd.DataFrame(rows)
    return portfolio, selected


def _lookahead_audit(outcomes: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    used_rules = sorted(candidates["trigger_rule"].dropna().unique().tolist()) if not candidates.empty else []
    rows = [
        {
            "check": "feature_available_at_or_before_entry",
            "violations": int((outcomes["feature_available_at_ms"] > outcomes["entry_timestamp_ms"]).sum()),
            "rows": int(len(outcomes)),
            "status": "pass" if int((outcomes["feature_available_at_ms"] > outcomes["entry_timestamp_ms"]).sum()) == 0 else "fail",
        },
        {
            "check": "evaluation_only_columns_not_in_trigger_rules",
            "violations": int(any(name in EVALUATION_ONLY_FEATURES for name in used_rules)),
            "rows": int(len(used_rules)),
            "status": "pass",
        },
        {
            "check": "large_close15_requires_delay15",
            "violations": int((
                outcomes["source"].eq("large_runner_local_high")
                & outcomes["nature_id"].astype(str).str.contains("close15", case=False, na=False)
                & _num(outcomes, "delay_min").lt(15)
            ).sum()),
            "rows": int(outcomes["source"].eq("large_runner_local_high").sum()),
            "status": "pass",
        },
        {
            "check": "large_close10_requires_delay10",
            "violations": int((
                outcomes["source"].eq("large_runner_local_high")
                & outcomes["nature_id"].astype(str).str.contains("close10", case=False, na=False)
                & _num(outcomes, "delay_min").lt(10)
            ).sum()),
            "rows": int(outcomes["source"].eq("large_runner_local_high").sum()),
            "status": "pass",
        },
    ]
    for row in rows:
        if int(row["violations"]) > 0:
            row["status"] = "fail"
    return pd.DataFrame(rows)


def _write_final_report(
    output_dir: Path,
    candidates: pd.DataFrame,
    clusters: pd.DataFrame,
    portfolio: pd.DataFrame,
    rejected: pd.DataFrame,
    axis_summary: pd.DataFrame,
    lookahead: pd.DataFrame,
    windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]],
) -> None:
    scan_config = {}
    metadata = {}
    scan_config_path = output_dir / "scan_config.json"
    metadata_path = output_dir / "metadata.json"
    if scan_config_path.exists():
        scan_config = json.loads(scan_config_path.read_text(encoding="utf-8"))
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    cand_cols = [
        "candidate_id",
        "promoted",
        "candidate_score",
        "oos_trades",
        "oos_avg_r",
        "oos_median_r",
        "oos_cost10_avg_r",
        "oos_win_rate",
        "oos_positive_day_rate",
        "oos_top_trade_independence_pct",
        "oos_top_symbol_independence_pct",
        "efficiency_ratio",
    ]
    cluster_cols = [
        "plateau_id",
        "plateau_pass",
        "members",
        "risk_buckets",
        "source",
        "nature_id",
        "session_rule",
        "stop_model",
        "management_id",
        "trigger_rule",
        "best_candidate_id",
        "best_candidate_score",
    ]
    port_cols = [
        "candidate_id",
        "accepted",
        "marginal_trades",
        "marginal_avg_r",
        "marginal_median_r",
        "marginal_cost10_avg_r",
        "marginal_top_trade_independence_pct",
    ]
    axis_cols = [
        "axis",
        "value",
        "candidate_rows",
        "promoted_rows",
        "best_is_promoted",
        "best_candidate_score",
        "best_oos_cost10_avg_r",
        "best_oos_trades",
        "best_candidate_id",
    ]
    rejected_summary = rejected["reason"].value_counts().head(20).to_string() if not rejected.empty and "reason" in rejected.columns else "none"
    text = f"""# Short Robust Plateau Engine Report

Status: automated plateau/WFA postprocess over immutable replay event store.

Event store:

```text
rows={metadata.get('rows', 'unknown')}
events={metadata.get('events', 'unknown')}
sources={metadata.get('sources', 'unknown')}
```

Scan config:

```json
{json.dumps(scan_config, indent=2, sort_keys=True) if scan_config else '{}'}
```

WFA windows:

```text
{len(windows)}
```

Candidate rows:

```text
{len(candidates)}
```

Promoted candidate rows:

```text
{int(candidates['promoted'].sum()) if not candidates.empty else 0}
```

Plateau clusters:

```text
{len(clusters)}
```

Lookahead audit:

```text
{lookahead.to_string(index=False) if not lookahead.empty else 'none'}
```

Top candidates:

```text
{candidates.head(25)[[c for c in cand_cols if c in candidates.columns]].to_string(index=False) if not candidates.empty else 'none'}
```

Top plateau clusters:

```text
{clusters.head(25)[[c for c in cluster_cols if c in clusters.columns]].to_string(index=False) if not clusters.empty else 'none'}
```

Candidate axis summary:

```text
{axis_summary.head(40)[[c for c in axis_cols if c in axis_summary.columns]].to_string(index=False) if not axis_summary.empty else 'none'}
```

Portfolio candidates:

```text
{portfolio.head(20)[[c for c in port_cols if c in portfolio.columns]].to_string(index=False) if not portfolio.empty else 'none'}
```

Top rejected reasons:

```text
{rejected_summary}
```

Interpretation:

This engine is the neutral mechanism. It should not be treated as fresh proof
when run on the already inspected 365d artifacts. Its value is repeatable
candidate generation, rejection reasons, plateau clustering, WFA accounting,
and portfolio marginal checks.
"""
    (output_dir / "final_report.md").write_text(text, encoding="utf-8")


def update_registry(path: Path = Path("research/HYPOTHESIS_REGISTRY.md")) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = """# Hypothesis Registry

Status: project research control file.

## Manual 365d Knowledge

```text
status: useful_prior_not_final_proof
archive: research/archive/2026-06-11_short_fade_manual_research/
contamination: current 365d artifacts have been heavily inspected
allowed_use: reasoning, risk notes, comparison, engine smoke
not_allowed_use: privileged candidate selection inside neutral plateau engine
```

## Neutral Plateau Engine Protocol

```text
engine: research_tools/short_robust_plateau_engine.py
event_store: immutable replay artifact cache
selection: rolling IS only
validation: rolling OOS segments
final_judge: future unseen/live-forward period
```

## Current Frozen Manual Hypotheses

```text
H001 failed-pump A_plus_fast core
H002 failed-pump diversified structural sleeves
H003 large-runner 15m fader context + fresh lower-high retest, not yet replayed
```
"""
    path.write_text(text, encoding="utf-8")


def run_all(args: argparse.Namespace) -> None:
    if args.archive:
        print("stage=archive_manual_knowledge", flush=True)
        archive_manual_knowledge(Path(args.archive_dir))
        update_registry()
    print("stage=build_event_store", flush=True)
    build_event_store(
        Path(args.failed_dir),
        Path(args.large_dir),
        Path(args.output_dir),
        max_rows_per_source=args.max_rows_per_source,
    )
    print("stage=scan_plateaus", flush=True)
    scan_plateaus(
        Path(args.output_dir),
        is_days=args.is_days,
        oos_days=args.oos_days,
        step_days=args.step_days,
        final_holdout_days=args.final_holdout_days,
        min_is_trades=args.min_is_trades,
        min_oos_trades=args.min_oos_trades,
        max_candidates=args.max_candidates,
        max_natures_per_source=args.max_natures_per_source,
        candidate_profile=args.candidate_profile,
        progress_every=args.progress_every,
    )


def _add_common_args(parser: argparse.ArgumentParser, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> None:
    parser.add_argument("--failed-dir", default=".output/results/failed_pump_short_research_365d")
    parser.add_argument("--large-dir", default=".output/results/large_runner_discovery_365d")
    parser.add_argument("--output-dir", default=str(output_dir))
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    parser.add_argument("--max-rows-per-source", type=int, default=None)


def _add_scan_args(
    parser: argparse.ArgumentParser,
    *,
    is_days: int = 90,
    oos_days: int = 30,
    step_days: int = 30,
    final_holdout_days: int = 30,
    min_is_trades: int = 25,
    min_oos_trades: int = 8,
    max_candidates: int | None = None,
    max_natures_per_source: int = 12,
    candidate_profile: str = "exhaustive",
    progress_every: int = 0,
) -> None:
    parser.add_argument("--is-days", type=int, default=is_days)
    parser.add_argument("--oos-days", type=int, default=oos_days)
    parser.add_argument("--step-days", type=int, default=step_days)
    parser.add_argument("--final-holdout-days", type=int, default=final_holdout_days)
    parser.add_argument("--min-is-trades", type=int, default=min_is_trades)
    parser.add_argument("--min-oos-trades", type=int, default=min_oos_trades)
    parser.add_argument("--max-candidates", type=int, default=max_candidates)
    parser.add_argument("--max-natures-per-source", type=int, default=max_natures_per_source)
    parser.add_argument("--candidate-profile", choices=["exhaustive", "balanced_365d"], default=candidate_profile)
    parser.add_argument("--progress-every", type=int, default=progress_every)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    archive = sub.add_parser("archive")
    archive.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))

    build = sub.add_parser("build-event-store")
    _add_common_args(build)

    scan = sub.add_parser("scan-plateaus")
    scan.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    _add_scan_args(scan)

    all_cmd = sub.add_parser("all")
    _add_common_args(all_cmd)
    all_cmd.add_argument("--archive", action="store_true")
    _add_scan_args(all_cmd)

    run365 = sub.add_parser("run-365d")
    _add_common_args(run365, output_dir=RUN_365D_OUTPUT_DIR)
    run365.set_defaults(
        archive=True,
    )
    _add_scan_args(
        run365,
        is_days=90,
        oos_days=30,
        step_days=30,
        final_holdout_days=30,
        min_is_trades=25,
        min_oos_trades=8,
        max_candidates=5000,
        max_natures_per_source=8,
        candidate_profile="balanced_365d",
        progress_every=250,
    )

    smoke = sub.add_parser("smoke")
    _add_common_args(smoke)
    smoke.set_defaults(
        archive=True,
        is_days=45,
        oos_days=15,
        step_days=15,
        final_holdout_days=15,
        min_is_trades=8,
        min_oos_trades=3,
        max_candidates=350,
        max_natures_per_source=4,
        candidate_profile="balanced_365d",
        progress_every=0,
    )

    args = parser.parse_args()
    if args.command == "archive":
        archive_manual_knowledge(Path(args.archive_dir))
        update_registry()
        print(f"archived_to={args.archive_dir}")
    elif args.command == "build-event-store":
        outcomes = build_event_store(
            Path(args.failed_dir),
            Path(args.large_dir),
            Path(args.output_dir),
            max_rows_per_source=args.max_rows_per_source,
        )
        print(f"event_outcome_rows={len(outcomes)}")
        print(f"event_rows={outcomes['event_id'].nunique()}")
    elif args.command == "scan-plateaus":
        scan_plateaus(
            Path(args.output_dir),
            is_days=args.is_days,
            oos_days=args.oos_days,
            step_days=args.step_days,
            final_holdout_days=args.final_holdout_days,
            min_is_trades=args.min_is_trades,
            min_oos_trades=args.min_oos_trades,
            max_candidates=args.max_candidates,
            max_natures_per_source=args.max_natures_per_source,
            candidate_profile=args.candidate_profile,
            progress_every=args.progress_every,
        )
        print(f"report={Path(args.output_dir) / 'final_report.md'}")
    elif args.command in {"all", "smoke", "run-365d"}:
        if args.command == "smoke" and args.output_dir == str(DEFAULT_OUTPUT_DIR):
            args.output_dir = ".output/research_cache/short_robust_plateau_engine_smoke"
        run_all(args)
        report = Path(args.output_dir) / "final_report.md"
        candidates = pd.read_csv(Path(args.output_dir) / "plateau_candidates.csv")
        print(f"event_store={args.output_dir}")
        print(f"candidate_rows={len(candidates)}")
        print(f"promoted={int(candidates['promoted'].sum()) if not candidates.empty else 0}")
        print(f"report={report}")


if __name__ == "__main__":
    main()
