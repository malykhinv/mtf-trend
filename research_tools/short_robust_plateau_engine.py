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
import zlib
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
DEFAULT_RISK_PER_TRADE_PCT = 0.04
DEFAULT_TOP_REMOVAL_PCT = 0.35
DEFAULT_MC_ITERATIONS = 1000
DEFAULT_MIN_CALENDAR_POSITIVE_DAY_RATE = 0.60
DEFAULT_SELF_IMPROVEMENT_QUEUE_ROWS = 400
DEFAULT_DIVERSIFIED_PORTFOLIO_MAX_CANDIDATES = 18
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

HYPOTHESIS_AXES = [
    "source",
    "nature_id",
    "session_rule",
    "stop_model",
    "management_id",
    "risk_bucket",
    "trigger_rule",
]

SESSION_NEIGHBORS = {
    "all": ["not_asia_overlap", "non_us", "asia_only", "europe_only", "europe_us_overlap", "us_only"],
    "asia_only": ["all", "not_asia_overlap", "off_session"],
    "europe_only": ["all", "not_europe_only", "europe_us_overlap"],
    "europe_us_overlap": ["all", "europe_only", "us_only", "non_us"],
    "us_only": ["all", "non_us", "europe_us_overlap"],
    "off_session": ["all", "asia_only", "not_asia_overlap"],
    "non_us": ["all", "asia_only", "europe_only", "europe_us_overlap"],
    "not_europe_only": ["all", "non_us", "europe_us_overlap"],
    "not_asia_overlap": ["all", "non_us", "asia_only", "off_session"],
}

RISK_ORDER = ["risk_1_3", "risk_2_5", "risk_3_8", "risk_5_12", "risk_all"]

PLATEAU_NEIGHBOR_AXES = [
    "risk_bucket",
    "session_rule",
    "trigger_rule",
    "stop_model",
    "management_id",
    "nature_id",
]

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


def _stable_id(prefix: str, text: str) -> str:
    return f"{prefix}{zlib.crc32(text.encode('utf-8')) & 0xFFFFFFFF:08x}"


def _candidate_id(
    *,
    source: str,
    nature_id: str,
    session_rule: str,
    stop_model: str,
    management_id: str,
    risk_bucket: str,
    trigger_rule: str,
) -> str:
    return "|".join([source, nature_id, session_rule, stop_model, management_id, risk_bucket, trigger_rule])


def _candidate_family_key(row: pd.Series | dict[str, object]) -> str:
    return "|".join(str(row.get(axis, "")) for axis in HYPOTHESIS_AXES if axis != "risk_bucket")


def _spec_family_key(spec: CandidateSpec) -> str:
    return "|".join([spec.source, spec.nature_id, spec.session_rule, spec.stop_model, spec.management_id, spec.trigger_rule])


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


def _model_quality_score(metrics: dict[str, float | int]) -> float:
    """Fixed-weight score from the theoretical model, with bounded inputs."""
    def finite(name: str, default: float = 0.0) -> float:
        value = float(metrics.get(name, default) or default)
        return value if np.isfinite(value) else default

    sharpe = np.tanh(finite("daily_sharpe") / 2.0)
    win_rate = max(min(finite("win_rate"), 1.0), 0.0)
    pf = np.tanh((finite("profit_factor") - 1.0) / 2.0)
    positive_days = max(min(finite("positive_day_rate"), 1.0), 0.0)
    drawdown_penalty = min(abs(finite("max_drawdown_r")) / 12.0, 1.0)
    return float(0.30 * sharpe + 0.20 * win_rate + 0.20 * pf + 0.20 * positive_days - 0.10 * drawdown_penalty)


def _top_removal_summary(vals: pd.Series, *, remove_pct: float = DEFAULT_TOP_REMOVAL_PCT) -> dict[str, float | int | bool]:
    clean = pd.to_numeric(vals, errors="coerce").dropna().sort_values(ascending=False)
    if clean.empty:
        return {
            "top_removed_pct": remove_pct,
            "top_removed_count": 0,
            "top_removed_remaining_trades": 0,
            "top_removed_remaining_sum_r": 0.0,
            "top_removed_remaining_avg_r": float("nan"),
            "top_removed_pass": False,
        }
    remove_count = int(math.ceil(len(clean) * remove_pct))
    keep = clean.iloc[remove_count:]
    return {
        "top_removed_pct": remove_pct,
        "top_removed_count": remove_count,
        "top_removed_remaining_trades": int(len(keep)),
        "top_removed_remaining_sum_r": float(keep.sum()) if len(keep) else 0.0,
        "top_removed_remaining_avg_r": float(keep.mean()) if len(keep) else float("nan"),
        "top_removed_pass": bool(len(keep) > 0 and keep.sum() > 0),
    }


def _monte_carlo_stress(
    vals: pd.Series,
    *,
    iterations: int = DEFAULT_MC_ITERATIONS,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    seed: int = 42,
) -> dict[str, float | int | bool]:
    clean = pd.to_numeric(vals, errors="coerce").dropna().to_numpy(dtype="float64")
    if len(clean) == 0 or iterations <= 0:
        return {
            "mc_iterations": int(max(iterations, 0)),
            "mc_shuffle_dd95_r": float("nan"),
            "mc_shuffle_dd95_pct": float("nan"),
            "mc_bootstrap_positive_rate": float("nan"),
            "mc_bootstrap_sum_p05_r": float("nan"),
            "mc_pass": False,
        }
    rng = np.random.default_rng(seed)
    dd_vals = np.empty(iterations, dtype="float64")
    boot_sums = np.empty(iterations, dtype="float64")
    for idx in range(iterations):
        shuffled = rng.permutation(clean)
        curve = np.cumsum(shuffled)
        dd_vals[idx] = float((curve - np.maximum.accumulate(curve)).min()) if len(curve) else 0.0
        boot_sums[idx] = float(rng.choice(clean, size=len(clean), replace=True).sum())
    dd95_r = float(np.percentile(np.abs(dd_vals), 95))
    positive_rate = float((boot_sums > 0).mean())
    return {
        "mc_iterations": int(iterations),
        "mc_shuffle_dd95_r": dd95_r,
        "mc_shuffle_dd95_pct": dd95_r * risk_per_trade_pct,
        "mc_bootstrap_positive_rate": positive_rate,
        "mc_bootstrap_sum_p05_r": float(np.percentile(boot_sums, 5)),
        "mc_pass": bool((dd95_r * risk_per_trade_pct) < 0.15 and positive_rate >= 0.90),
    }


def _calendar_days_from_windows(windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]) -> pd.DatetimeIndex:
    parts = []
    for _, _, oos_start, oos_end in windows:
        parts.append(pd.date_range(oos_start, oos_end - pd.Timedelta(days=1), freq="D", tz="UTC"))
    if not parts:
        return pd.DatetimeIndex([], tz="UTC")
    out = parts[0]
    for part in parts[1:]:
        out = out.union(part)
    return out


def _calendar_stats(df: pd.DataFrame, calendar_days: pd.DatetimeIndex, value_col: str = "cost10_r") -> dict[str, float | int]:
    if len(calendar_days) == 0:
        return {
            "calendar_days": 0,
            "calendar_median_trades_per_day": float("nan"),
            "calendar_positive_day_rate": float("nan"),
            "calendar_sum_r": 0.0,
        }
    if df.empty:
        daily_sum = pd.Series(0.0, index=calendar_days)
        daily_count = pd.Series(0, index=calendar_days)
    else:
        work = df.copy()
        work["date"] = pd.to_datetime(work["date"], utc=True, errors="coerce").dt.normalize()
        daily_sum = work.groupby("date")[value_col].sum().reindex(calendar_days, fill_value=0.0)
        daily_count = work.groupby("date")[value_col].size().reindex(calendar_days, fill_value=0)
    return {
        "calendar_days": int(len(calendar_days)),
        "calendar_median_trades_per_day": float(daily_count.median()),
        "calendar_positive_day_rate": float((daily_sum > 0).mean()),
        "calendar_sum_r": float(daily_sum.sum()),
    }


def _date_bounds(
    df: pd.DataFrame,
    *,
    final_holdout_days: int,
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    dates = pd.to_datetime(df["date"], utc=True, errors="coerce").dropna()
    if dates.empty:
        raise ValueError("No valid dates in event store.")
    start = dates.min().normalize()
    end_exclusive = dates.max().normalize() + pd.Timedelta(days=1)
    final_start = end_exclusive - pd.Timedelta(days=final_holdout_days)
    return start, end_exclusive, final_start


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


def _as_bool(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (float, np.floating)) and not np.isfinite(float(value)):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def _safe_int(value: object, default: int = 0) -> int:
    return int(_safe_float(value, float(default)))


def _spec_from_parts(
    *,
    source: str,
    nature_id: str,
    session_rule: str,
    stop_model: str,
    management_id: str,
    risk_bucket: str,
    trigger_rule: str,
) -> CandidateSpec:
    return CandidateSpec(
        candidate_id=_candidate_id(
            source=source,
            nature_id=nature_id,
            session_rule=session_rule,
            stop_model=stop_model,
            management_id=management_id,
            risk_bucket=risk_bucket,
            trigger_rule=trigger_rule,
        ),
        source=source,
        nature_id=nature_id,
        session_rule=session_rule,
        stop_model=stop_model,
        management_id=management_id,
        risk_bucket=risk_bucket,
        trigger_rule=trigger_rule,
    )


def _spec_from_series(row: pd.Series) -> CandidateSpec:
    return _spec_from_parts(
        source=str(row.get("source", "")),
        nature_id=str(row.get("nature_id", "")),
        session_rule=str(row.get("session_rule", "")),
        stop_model=str(row.get("stop_model", "")),
        management_id=str(row.get("management_id", "")),
        risk_bucket=str(row.get("risk_bucket", "")),
        trigger_rule=str(row.get("trigger_rule", "")),
    )


def _mutate_spec(spec: CandidateSpec, **updates: str) -> CandidateSpec:
    values = {
        "source": spec.source,
        "nature_id": spec.nature_id,
        "session_rule": spec.session_rule,
        "stop_model": spec.stop_model,
        "management_id": spec.management_id,
        "risk_bucket": spec.risk_bucket,
        "trigger_rule": spec.trigger_rule,
    }
    values.update(updates)
    return _spec_from_parts(**values)


def _adjacent_values(values: list[str], current: str, *, radius: int = 1) -> list[str]:
    if current not in values:
        return [value for value in values[: max(0, radius * 2 + 1)] if value != current]
    idx = values.index(current)
    lo = max(0, idx - radius)
    hi = min(len(values), idx + radius + 1)
    out = [value for value in values[lo:hi] if value != current]
    if not out:
        out = [value for value in values if value != current][:2]
    return out


def _build_hypothesis_grammar(
    df: pd.DataFrame,
    specs: list[CandidateSpec],
    trigger_masks: dict[str, pd.Series],
) -> pd.DataFrame:
    """Describe the typed, entry-known search grammar used by this scan."""
    rows: list[dict[str, object]] = []
    spec_rows = pd.DataFrame([spec.__dict__ for spec in specs])

    def add(axis: str, value: str, *, source: str = "ALL", rows_count: int = 0, unique_events: int = 0, primitive_kind: str = "categorical") -> None:
        rows.append(
            {
                "axis": axis,
                "value": value,
                "source": source,
                "primitive_kind": primitive_kind,
                "entry_known": True,
                "event_rows": int(rows_count),
                "unique_events": int(unique_events),
                "candidate_universe_uses": int((spec_rows[axis].astype(str).eq(value)).sum()) if axis in spec_rows.columns and not spec_rows.empty else 0,
                "notes": "All primitives must be available at or before entry_timestamp_ms.",
            }
        )

    for source, group in df.groupby("source", dropna=False):
        source_s = str(source)
        add("source", source_s, source=source_s, rows_count=len(group), unique_events=group["event_id"].nunique(), primitive_kind="event_source")
        for nature, ng in group.groupby("nature_id", dropna=False):
            add("nature_id", str(nature), source=source_s, rows_count=len(ng), unique_events=ng["event_id"].nunique(), primitive_kind="source_descriptor")
        for stop, sg in group.groupby("stop_model", dropna=False):
            add("stop_model", str(stop), source=source_s, rows_count=len(sg), unique_events=sg["event_id"].nunique(), primitive_kind="execution_model")
        for management, mg in group.groupby("management_id", dropna=False):
            add("management_id", str(management), source=source_s, rows_count=len(mg), unique_events=mg["event_id"].nunique(), primitive_kind="position_management")

    for session in SESSION_RULES:
        mask = SESSION_RULES[session](df).fillna(False)
        sub = df[mask]
        add("session_rule", session, rows_count=len(sub), unique_events=sub["event_id"].nunique(), primitive_kind="time_partition")
    for risk in RISK_BUCKETS:
        mask = _risk_mask(df, risk).fillna(False)
        sub = df[mask]
        add("risk_bucket", risk, rows_count=len(sub), unique_events=sub["event_id"].nunique(), primitive_kind="risk_filter")
    for trigger, mask in trigger_masks.items():
        sub = df[mask.fillna(False)]
        source = "failed_pump_structural" if trigger.startswith("failed_") else "large_runner_local_high" if trigger.startswith("large_") else "ALL"
        add("trigger_rule", trigger, source=source, rows_count=len(sub), unique_events=sub["event_id"].nunique(), primitive_kind="entry_known_trigger")

    out = pd.DataFrame(rows)
    return out.sort_values(["axis", "source", "event_rows"], ascending=[True, True, False]) if not out.empty else out


def _event_store_fingerprint(metadata: dict[str, object], scan_config: dict[str, object]) -> str:
    payload = {
        "metadata": {key: metadata.get(key) for key in ["schema_version", "rows", "events", "sources"]},
        "scan": {key: scan_config.get(key) for key in ["is_days", "oos_days", "step_days", "final_holdout_days", "candidate_profile", "candidate_universe_rows"]},
    }
    return _stable_id("D", json.dumps(payload, sort_keys=True, default=str))


def _date_windows(
    df: pd.DataFrame,
    *,
    is_days: int,
    oos_days: int,
    step_days: int,
    final_holdout_days: int,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    try:
        start, _, dev_end = _date_bounds(df, final_holdout_days=final_holdout_days)
    except ValueError:
        return []
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


def _load_guided_candidate_specs(
    path: Path | None,
    *,
    limit: int,
    existing_ids: set[str],
) -> list[CandidateSpec]:
    if path is None or limit <= 0 or not path.exists():
        return []
    queue = pd.read_csv(path)
    if queue.empty:
        return []
    if "priority_score" in queue.columns:
        queue = queue.sort_values("priority_score", ascending=False)
    specs: list[CandidateSpec] = []
    seen = set(existing_ids)
    for _, row in queue.iterrows():
        source = str(row.get("source", row.get("proposed_source", "")))
        nature_id = str(row.get("nature_id", row.get("proposed_nature_id", "")))
        session_rule = str(row.get("session_rule", row.get("proposed_session_rule", "")))
        stop_model = str(row.get("stop_model", row.get("proposed_stop_model", "")))
        management_id = str(row.get("management_id", row.get("proposed_management_id", "")))
        risk_bucket = str(row.get("risk_bucket", row.get("proposed_risk_bucket", "")))
        trigger_rule = str(row.get("trigger_rule", row.get("proposed_trigger_rule", "")))
        if not all([source, nature_id, session_rule, stop_model, management_id, risk_bucket, trigger_rule]):
            continue
        if session_rule not in SESSION_RULES or risk_bucket not in RISK_BUCKETS:
            continue
        spec = _spec_from_parts(
            source=source,
            nature_id=nature_id,
            session_rule=session_rule,
            stop_model=stop_model,
            management_id=management_id,
            risk_bucket=risk_bucket,
            trigger_rule=trigger_rule,
        )
        if spec.candidate_id in seen:
            continue
        specs.append(spec)
        seen.add(spec.candidate_id)
        if len(specs) >= limit:
            break
    return specs


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
    mc_iterations: int = DEFAULT_MC_ITERATIONS,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    top_removal_pct: float = DEFAULT_TOP_REMOVAL_PCT,
    min_calendar_positive_day_rate: float = DEFAULT_MIN_CALENDAR_POSITIVE_DAY_RATE,
    candidate_queue_file: Path | None = None,
    guided_candidates_limit: int = 0,
) -> None:
    df = _load_outcomes(output_dir)
    _, final_end, final_start = _date_bounds(df, final_holdout_days=final_holdout_days)
    dev_df = df[df["date"].lt(final_start)].copy()
    trigger_masks = _trigger_rules(dev_df)
    full_trigger_masks = _trigger_rules(df)
    specs = generate_candidates(
        dev_df,
        max_natures_per_source=max_natures_per_source,
        max_specs=max_candidates,
        candidate_profile=candidate_profile,
    )
    queue_path = candidate_queue_file
    if queue_path is None and guided_candidates_limit > 0:
        queue_path = output_dir / "self_improvement_queue.csv"
    guided_specs = _load_guided_candidate_specs(
        queue_path,
        limit=guided_candidates_limit,
        existing_ids={spec.candidate_id for spec in specs},
    )
    if guided_specs:
        specs = specs + guided_specs
    windows = _date_windows(df, is_days=is_days, oos_days=oos_days, step_days=step_days, final_holdout_days=final_holdout_days)
    if not windows:
        raise ValueError("Not enough date coverage for requested WFA windows.")
    pd.DataFrame([spec.__dict__ for spec in specs]).to_csv(output_dir / "candidate_universe.csv", index=False)
    metadata_path = output_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    scan_config = {
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
        "base_candidate_universe_rows": len(specs) - len(guided_specs),
        "guided_candidate_rows": len(guided_specs),
        "guided_candidates_limit": guided_candidates_limit,
        "candidate_queue_file": str(queue_path) if queue_path is not None else "",
        "development_rows": int(len(dev_df)),
        "wfa_windows": len(windows),
        "final_holdout_start": final_start.date().isoformat(),
        "final_holdout_end_exclusive": final_end.date().isoformat(),
        "mc_iterations": mc_iterations,
        "risk_per_trade_pct": risk_per_trade_pct,
        "top_removal_pct": top_removal_pct,
        "min_calendar_positive_day_rate": min_calendar_positive_day_rate,
        "event_store_fingerprint": _event_store_fingerprint(metadata, {"candidate_profile": candidate_profile, "candidate_universe_rows": len(specs), "is_days": is_days, "oos_days": oos_days, "step_days": step_days, "final_holdout_days": final_holdout_days}),
    }
    _write_json(
        output_dir / "scan_config.json",
        scan_config,
    )

    wfa_rows: list[dict] = []
    candidate_rows: list[dict] = []
    rejected_rows: list[dict] = []
    ledgers: dict[str, pd.DataFrame] = {}

    for idx, spec in enumerate(specs):
        if progress_every and (idx == 0 or (idx + 1) % progress_every == 0 or idx + 1 == len(specs)):
            print(f"scan_progress={idx + 1}/{len(specs)} analyzed={len(candidate_rows)} rejected={len(rejected_rows)}", flush=True)
        mask = _candidate_mask(dev_df, spec, trigger_masks)
        cand = dev_df[mask]
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
    plateau_neighborhoods = _build_plateau_neighborhoods(candidates)
    portfolio, portfolio_trades = _build_portfolio(candidates, ledgers)
    lookahead = _lookahead_audit(df, candidates)
    axis_summary = _build_candidate_axis_summary(candidates)
    meta_validation, final_holdout = _build_meta_validation(
        df,
        candidates,
        clusters,
        plateau_neighborhoods,
        ledgers,
        full_trigger_masks,
        windows,
        final_start=final_start,
        final_end=final_end,
        min_oos_trades=min_oos_trades,
        mc_iterations=mc_iterations,
        risk_per_trade_pct=risk_per_trade_pct,
        top_removal_pct=top_removal_pct,
        min_calendar_positive_day_rate=min_calendar_positive_day_rate,
    )
    portfolio_meta = _build_portfolio_meta_validation(
        portfolio_trades,
        windows,
        mc_iterations=mc_iterations,
        risk_per_trade_pct=risk_per_trade_pct,
        top_removal_pct=top_removal_pct,
    )
    gate_diagnostics = _build_model_gate_diagnostics(
        meta_validation,
        final_holdout,
        portfolio_meta,
        min_oos_trades=min_oos_trades,
        top_removal_pct=top_removal_pct,
        min_calendar_positive_day_rate=min_calendar_positive_day_rate,
    )
    hypothesis_grammar = _build_hypothesis_grammar(dev_df, specs, trigger_masks)
    hypothesis_ledger = _build_hypothesis_ledger(
        specs,
        candidates,
        rejected,
        meta_validation,
        final_holdout,
        clusters,
        scan_config=scan_config,
    )
    diversity_scores = _build_diversity_scores(meta_validation, ledgers, portfolio, portfolio_trades)
    diversified_portfolio, diversified_portfolio_trades = _build_diversified_portfolio(
        meta_validation,
        diversity_scores,
        ledgers,
        windows,
        top_removal_pct=top_removal_pct,
    )
    diversified_portfolio_meta = _build_portfolio_meta_validation(
        diversified_portfolio_trades,
        windows,
        mc_iterations=mc_iterations,
        risk_per_trade_pct=risk_per_trade_pct,
        top_removal_pct=top_removal_pct,
    )
    self_improvement_queue = _build_self_improvement_queue(
        dev_df,
        trigger_masks,
        specs,
        candidates,
        rejected,
        meta_validation,
        diversity_scores,
        min_is_trades=min_is_trades,
        min_calendar_positive_day_rate=min_calendar_positive_day_rate,
        max_rows=DEFAULT_SELF_IMPROVEMENT_QUEUE_ROWS,
    )
    compute_budget_plan = _build_compute_budget_plan(
        candidate_universe_rows=len(specs),
        candidate_rows=len(candidates),
        guided_candidate_rows=len(guided_specs),
        rejected=rejected,
        self_improvement_queue=self_improvement_queue,
    )
    theoretical_model_gap_analysis = _build_theoretical_model_gap_analysis(
        hypothesis_grammar=hypothesis_grammar,
        hypothesis_ledger=hypothesis_ledger,
        plateau_neighborhoods=plateau_neighborhoods,
        diversity_scores=diversity_scores,
        self_improvement_queue=self_improvement_queue,
        portfolio=portfolio,
        diversified_portfolio=diversified_portfolio,
        compute_budget_plan=compute_budget_plan,
        lookahead=lookahead,
    )
    improvement_plan = _build_improvement_plan(
        candidates,
        rejected,
        axis_summary,
        meta_validation,
        portfolio_meta,
        diversity_scores,
        self_improvement_queue,
    )

    wfa.to_csv(output_dir / "wfa_results.csv", index=False)
    candidates.to_csv(output_dir / "plateau_candidates.csv", index=False)
    clusters.to_csv(output_dir / "plateau_clusters.csv", index=False)
    plateau_neighborhoods.to_csv(output_dir / "plateau_neighborhoods.csv", index=False)
    axis_summary.to_csv(output_dir / "candidate_axis_summary.csv", index=False)
    meta_validation.to_csv(output_dir / "meta_validation.csv", index=False)
    final_holdout.to_csv(output_dir / "final_holdout_validation.csv", index=False)
    portfolio_meta.to_csv(output_dir / "portfolio_meta_validation.csv", index=False)
    diversified_portfolio_meta.to_csv(output_dir / "diversified_portfolio_meta_validation.csv", index=False)
    gate_diagnostics.to_csv(output_dir / "model_gate_diagnostics.csv", index=False)
    hypothesis_grammar.to_csv(output_dir / "hypothesis_grammar.csv", index=False)
    hypothesis_ledger.to_csv(output_dir / "hypothesis_ledger.csv", index=False)
    diversity_scores.to_csv(output_dir / "diversity_scores.csv", index=False)
    self_improvement_queue.to_csv(output_dir / "self_improvement_queue.csv", index=False)
    compute_budget_plan.to_csv(output_dir / "compute_budget_plan.csv", index=False)
    theoretical_model_gap_analysis.to_csv(output_dir / "theoretical_model_gap_analysis.csv", index=False)
    improvement_plan.to_csv(output_dir / "improvement_plan.csv", index=False)
    rejected.to_csv(output_dir / "rejected_reasons.csv", index=False)
    portfolio.to_csv(output_dir / "portfolio_candidates.csv", index=False)
    portfolio_trades.to_csv(output_dir / "portfolio_oos_trades.csv", index=False)
    diversified_portfolio.to_csv(output_dir / "diversified_portfolio_candidates.csv", index=False)
    diversified_portfolio_trades.to_csv(output_dir / "diversified_portfolio_oos_trades.csv", index=False)
    lookahead.to_csv(output_dir / "lookahead_audit.csv", index=False)
    _write_final_report(
        output_dir,
        candidates,
        clusters,
        plateau_neighborhoods,
        portfolio,
        diversified_portfolio,
        rejected,
        axis_summary,
        meta_validation,
        final_holdout,
        portfolio_meta,
        diversified_portfolio_meta,
        gate_diagnostics,
        hypothesis_grammar,
        hypothesis_ledger,
        diversity_scores,
        self_improvement_queue,
        compute_budget_plan,
        theoretical_model_gap_analysis,
        improvement_plan,
        lookahead,
        windows,
    )
    _write_self_improvement_report(
        output_dir,
        hypothesis_grammar,
        hypothesis_ledger,
        diversity_scores,
        self_improvement_queue,
        diversified_portfolio,
        diversified_portfolio_meta,
        compute_budget_plan,
        theoretical_model_gap_analysis,
        gate_diagnostics,
        portfolio_meta,
    )


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
        scores = pd.to_numeric(group["candidate_score"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        best_score = float(scores.max()) if len(scores) else float("nan")
        worst_score = float(scores.min()) if len(scores) else float("nan")
        degradation = 1.0 - (worst_score / best_score) if best_score and np.isfinite(best_score) and best_score > 0 and np.isfinite(worst_score) else float("nan")
        row = {
            "plateau_id": f"plateau_{idx:04d}",
            "members": int(len(group)),
            "risk_buckets": ",".join(sorted(group["risk_bucket"].astype(str).unique())),
            "member_candidate_ids": ";".join(group["candidate_id"].astype(str).tolist()),
            "best_candidate_id": best["candidate_id"],
            "best_candidate_score": float(best["candidate_score"]),
            "plateau_score_degradation_pct": float(degradation),
            "plateau_pass": bool(len(group) >= 2 and np.isfinite(degradation) and degradation <= 0.30),
            "plateau_strong_pass": bool(len(group) >= 2 and np.isfinite(degradation) and degradation <= 0.15),
        }
        for col, value in zip(group_cols, keys if isinstance(keys, tuple) else (keys,)):
            row[col] = value
        row.update(s)
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["plateau_pass", "best_candidate_score"], ascending=[False, False])


def _build_plateau_neighborhoods(candidates: pd.DataFrame) -> pd.DataFrame:
    """Find broad positive neighborhoods by varying one search axis at a time."""
    if candidates.empty:
        return pd.DataFrame()
    work = candidates[candidates["promoted"].astype(bool)].copy()
    if work.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for varied_axis in PLATEAU_NEIGHBOR_AXES:
        if varied_axis not in work.columns:
            continue
        fixed_axes = [axis for axis in HYPOTHESIS_AXES if axis != varied_axis and axis in work.columns]
        if not fixed_axes:
            continue
        for keys, group in work.groupby(fixed_axes, dropna=False):
            unique_values = sorted(group[varied_axis].astype(str).unique().tolist())
            if len(unique_values) < 2:
                continue
            scores = pd.to_numeric(group["candidate_score"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            if scores.empty:
                continue
            best = group.sort_values(["candidate_score", "oos_cost10_sum_r"], ascending=[False, False]).iloc[0]
            worst_score = float(scores.min())
            best_score = float(scores.max())
            degradation = 1.0 - (worst_score / best_score) if best_score > 0 else float("nan")
            s = _summary_from_candidate_group(group)
            row: dict[str, object] = {
                "neighborhood_id": _stable_id("N", f"{varied_axis}|{'|'.join(map(str, keys if isinstance(keys, tuple) else (keys,)))}"),
                "varied_axis": varied_axis,
                "members": int(len(group)),
                "unique_values": int(len(unique_values)),
                "varied_values": ",".join(unique_values),
                "member_candidate_ids": ";".join(group["candidate_id"].astype(str).tolist()),
                "best_candidate_id": str(best["candidate_id"]),
                "best_candidate_score": best_score,
                "worst_candidate_score": worst_score,
                "score_degradation_pct": float(degradation),
                "neighborhood_pass": bool(np.isfinite(degradation) and degradation <= 0.30 and len(group) >= 2 and len(unique_values) >= 2),
                "neighborhood_strong_pass": bool(np.isfinite(degradation) and degradation <= 0.15 and len(group) >= 2 and len(unique_values) >= 2),
            }
            for axis, value in zip(fixed_axes, keys if isinstance(keys, tuple) else (keys,)):
                row[axis] = value
            row.update(s)
            rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["neighborhood_pass", "neighborhood_strong_pass", "best_candidate_score"], ascending=[False, False, False])


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


def _candidate_spec_from_row(row: pd.Series) -> CandidateSpec:
    return CandidateSpec(
        candidate_id=str(row["candidate_id"]),
        source=str(row["source"]),
        nature_id=str(row["nature_id"]),
        session_rule=str(row["session_rule"]),
        stop_model=str(row["stop_model"]),
        management_id=str(row["management_id"]),
        risk_bucket=str(row["risk_bucket"]),
        trigger_rule=str(row["trigger_rule"]),
    )


def _plateau_membership(clusters: pd.DataFrame) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    if clusters.empty or "member_candidate_ids" not in clusters.columns:
        return out
    for _, cluster in clusters.iterrows():
        for cid in str(cluster.get("member_candidate_ids", "")).split(";"):
            if not cid:
                continue
            out[cid] = {
                "plateau_id": cluster.get("plateau_id", ""),
                "plateau_pass": bool(cluster.get("plateau_pass", False)),
                "plateau_strong_pass": bool(cluster.get("plateau_strong_pass", False)),
                "plateau_members": int(cluster.get("members", 0) or 0),
                "plateau_score_degradation_pct": float(cluster.get("plateau_score_degradation_pct", np.nan)),
            }
    return out


def _neighborhood_membership(neighborhoods: pd.DataFrame) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    if neighborhoods.empty or "member_candidate_ids" not in neighborhoods.columns:
        return out
    for _, neighborhood in neighborhoods.iterrows():
        for cid in str(neighborhood.get("member_candidate_ids", "")).split(";"):
            if not cid:
                continue
            current = out.get(cid)
            score = float(neighborhood.get("best_candidate_score", 0.0) or 0.0)
            if current is not None and float(current.get("neighborhood_best_candidate_score", 0.0) or 0.0) >= score:
                continue
            out[cid] = {
                "neighborhood_id": neighborhood.get("neighborhood_id", ""),
                "neighborhood_varied_axis": neighborhood.get("varied_axis", ""),
                "neighborhood_pass": bool(neighborhood.get("neighborhood_pass", False)),
                "neighborhood_strong_pass": bool(neighborhood.get("neighborhood_strong_pass", False)),
                "neighborhood_members": int(neighborhood.get("members", 0) or 0),
                "neighborhood_unique_values": int(neighborhood.get("unique_values", 0) or 0),
                "neighborhood_score_degradation_pct": float(neighborhood.get("score_degradation_pct", np.nan)),
                "neighborhood_best_candidate_score": score,
            }
    return out


def _model_gate(
    *,
    base: dict[str, object],
    min_oos_trades: int,
    min_calendar_positive_day_rate: float,
) -> tuple[bool, bool, str]:
    strict = (
        int(base.get("oos_trades", 0) or 0) >= min_oos_trades
        and bool(base.get("plateau_pass", False))
        and float(base.get("efficiency_ratio", 0.0) or 0.0) >= 0.70
        and bool(base.get("top_removal_pass", False))
        and bool(base.get("mc_pass", False))
        and float(base.get("oos_win_rate", 0.0) or 0.0) >= 0.50
        and float(base.get("calendar_positive_day_rate", 0.0) or 0.0) > min_calendar_positive_day_rate
        and float(base.get("oos_cost10_avg_r", 0.0) or 0.0) > 0
        and float(base.get("oos_median_r", 0.0) or 0.0) > 0
    )
    theoretical = (
        strict
        and bool(base.get("plateau_strong_pass", False))
        and float(base.get("calendar_positive_day_rate", 0.0) or 0.0) > min_calendar_positive_day_rate
        and float(base.get("calendar_median_trades_per_day", 0.0) or 0.0) >= 3.0
    )
    if theoretical:
        grade = "theoretical_accept"
    elif strict:
        grade = "robust_watchlist"
    elif bool(base.get("top_removal_pass", False)) and bool(base.get("mc_pass", False)):
        grade = "stress_pass_but_plateau_or_consistency_weak"
    else:
        grade = "research_only"
    return strict, theoretical, grade


def _build_meta_validation(
    df: pd.DataFrame,
    candidates: pd.DataFrame,
    clusters: pd.DataFrame,
    neighborhoods: pd.DataFrame,
    ledgers: dict[str, pd.DataFrame],
    trigger_masks: dict[str, pd.Series],
    windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]],
    *,
    final_start: pd.Timestamp,
    final_end: pd.Timestamp,
    min_oos_trades: int,
    mc_iterations: int,
    risk_per_trade_pct: float,
    top_removal_pct: float,
    min_calendar_positive_day_rate: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
    calendar_days = _calendar_days_from_windows(windows)
    final_days = pd.date_range(final_start, final_end - pd.Timedelta(days=1), freq="D", tz="UTC")
    membership = _plateau_membership(clusters)
    neighborhood = _neighborhood_membership(neighborhoods)
    promoted = candidates[candidates["promoted"].astype(bool)].copy()
    rows: list[dict] = []
    final_rows: list[dict] = []
    for _, cand in promoted.iterrows():
        cid = str(cand["candidate_id"])
        ledger = ledgers.get(cid, pd.DataFrame()).copy()
        if ledger.empty:
            continue
        net_m = _summary(ledger, "net_r")
        cost_m = _summary(ledger, "cost10_r")
        calendar = _calendar_stats(ledger, calendar_days, "cost10_r")
        top40 = _top_removal_summary(ledger["cost10_r"], remove_pct=top_removal_pct)
        mc = _monte_carlo_stress(
            ledger["cost10_r"],
            iterations=mc_iterations,
            risk_per_trade_pct=risk_per_trade_pct,
            seed=zlib.crc32(cid.encode("utf-8")),
        )
        base: dict[str, object] = {
            "candidate_id": cid,
            "source": cand["source"],
            "nature_id": cand["nature_id"],
            "session_rule": cand["session_rule"],
            "stop_model": cand["stop_model"],
            "management_id": cand["management_id"],
            "risk_bucket": cand["risk_bucket"],
            "trigger_rule": cand["trigger_rule"],
            "oos_trades": int(net_m["trades"]),
            "oos_avg_r": net_m["avg_r"],
            "oos_median_r": net_m["median_r"],
            "oos_win_rate": net_m["win_rate"],
            "oos_profit_factor": net_m["profit_factor"],
            "oos_positive_day_rate_active": net_m["positive_day_rate"],
            "oos_cost10_avg_r": cost_m["avg_r"],
            "oos_cost10_sum_r": cost_m["sum_r"],
            "oos_top_trade_independence_pct": net_m["top_trade_independence_pct"],
            "oos_top_symbol_independence_pct": net_m["top_symbol_independence_pct"],
            "oos_max_drawdown_r": net_m["max_drawdown_r"],
            "oos_daily_sharpe": net_m["daily_sharpe"],
            "model_quality_score": _model_quality_score(net_m),
            "efficiency_ratio": cand.get("efficiency_ratio", np.nan),
            "oos_window_positive_score_rate": cand.get("oos_window_positive_score_rate", np.nan),
            **calendar,
            **top40,
            **mc,
            **membership.get(cid, {}),
            **neighborhood.get(cid, {}),
        }
        base["top_removal_pass"] = bool(base.get("top_removed_pass", False))
        base["top40_pass"] = bool(base.get("top_removed_pass", False))
        strict, theoretical, grade = _model_gate(
            base=base,
            min_oos_trades=min_oos_trades,
            min_calendar_positive_day_rate=min_calendar_positive_day_rate,
        )
        base["strict_model_pass"] = strict
        base["theoretical_accept_pass"] = theoretical
        base["model_grade"] = grade
        rows.append(base)

        spec = _candidate_spec_from_row(cand)
        final_mask = _candidate_mask(df, spec, trigger_masks)
        final = df[final_mask & df["date"].ge(final_start) & df["date"].lt(final_end)].copy()
        final_net = _summary(final, "net_r")
        final_cost = _summary(final, "cost10_r")
        final_cal = _calendar_stats(final, final_days, "cost10_r")
        final_top40 = _top_removal_summary(final["cost10_r"], remove_pct=top_removal_pct) if not final.empty else _top_removal_summary(pd.Series(dtype=float), remove_pct=top_removal_pct)
        final_pass = (
            final_net["trades"] >= min_oos_trades
            and float(final_net["median_r"]) > 0
            and float(final_cost["avg_r"]) > 0
            and float(final_net["win_rate"]) >= 0.50
            and float(final_cal["calendar_positive_day_rate"]) > min_calendar_positive_day_rate
            and bool(final_top40["top_removed_pass"])
        )
        final_rows.append(
            {
                "candidate_id": cid,
                "final_start": final_start.date().isoformat(),
                "final_end_exclusive": final_end.date().isoformat(),
                "final_pass_basic": bool(final_pass),
                "final_trades": int(final_net["trades"]),
                "final_avg_r": final_net["avg_r"],
                "final_median_r": final_net["median_r"],
                "final_win_rate": final_net["win_rate"],
                "final_cost10_avg_r": final_cost["avg_r"],
                "final_cost10_sum_r": final_cost["sum_r"],
                "final_top_trade_independence_pct": final_net["top_trade_independence_pct"],
                "final_top_symbol_independence_pct": final_net["top_symbol_independence_pct"],
                **{f"final_{k}": v for k, v in final_cal.items()},
                **{f"final_{k}": v for k, v in final_top40.items()},
            }
        )
    meta = pd.DataFrame(rows)
    final_df = pd.DataFrame(final_rows)
    if not meta.empty:
        meta = meta.sort_values(
            ["theoretical_accept_pass", "strict_model_pass", "model_quality_score", "oos_cost10_sum_r"],
            ascending=[False, False, False, False],
        )
    if not final_df.empty:
        final_df = final_df.sort_values(["final_pass_basic", "final_cost10_sum_r"], ascending=[False, False])
    return meta, final_df


def _build_portfolio_meta_validation(
    portfolio_trades: pd.DataFrame,
    windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]],
    *,
    mc_iterations: int,
    risk_per_trade_pct: float,
    top_removal_pct: float,
) -> pd.DataFrame:
    if portfolio_trades.empty:
        return pd.DataFrame()
    calendar_days = _calendar_days_from_windows(windows)
    net_m = _summary(portfolio_trades, "net_r")
    cost_m = _summary(portfolio_trades, "cost10_r")
    top40 = _top_removal_summary(portfolio_trades["cost10_r"], remove_pct=top_removal_pct)
    mc = _monte_carlo_stress(
        portfolio_trades["cost10_r"],
        iterations=mc_iterations,
        risk_per_trade_pct=risk_per_trade_pct,
        seed=20260612,
    )
    calendar = _calendar_stats(portfolio_trades, calendar_days, "cost10_r")
    return pd.DataFrame(
        [
            {
                "portfolio_trades": int(net_m["trades"]),
                "portfolio_symbols": int(net_m["symbols"]),
                "portfolio_days_active": int(net_m["days"]),
                "portfolio_avg_r": net_m["avg_r"],
                "portfolio_median_r": net_m["median_r"],
                "portfolio_win_rate": net_m["win_rate"],
                "portfolio_cost10_avg_r": cost_m["avg_r"],
                "portfolio_cost10_sum_r": cost_m["sum_r"],
                "portfolio_top_trade_independence_pct": net_m["top_trade_independence_pct"],
                "portfolio_top_symbol_independence_pct": net_m["top_symbol_independence_pct"],
                "portfolio_model_quality_score": _model_quality_score(net_m),
                **calendar,
                **top40,
                **mc,
            }
        ]
    )


def _diagnostic_count(
    rows: list[dict[str, object]],
    *,
    scope: str,
    check: str,
    condition: pd.Series | list[bool] | np.ndarray,
    total: int,
    note: str,
) -> None:
    passed = int(pd.Series(condition).fillna(False).sum()) if total else 0
    rows.append(
        {
            "scope": scope,
            "check": check,
            "passed": passed,
            "total": int(total),
            "pass_rate": float(passed / total) if total else float("nan"),
            "note": note,
        }
    )


def _build_model_gate_diagnostics(
    meta_validation: pd.DataFrame,
    final_holdout: pd.DataFrame,
    portfolio_meta: pd.DataFrame,
    *,
    min_oos_trades: int,
    top_removal_pct: float,
    min_calendar_positive_day_rate: float,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    kept_pct = max(0.0, 1.0 - top_removal_pct)
    calendar_pct = min_calendar_positive_day_rate * 100.0
    top_remove_pct = top_removal_pct * 100.0
    total = int(len(meta_validation))
    if total:
        neighborhood_pass = (
            meta_validation["neighborhood_pass"].map(_as_bool).astype(bool)
            if "neighborhood_pass" in meta_validation.columns
            else pd.Series(False, index=meta_validation.index, dtype="bool")
        )
        neighborhood_strong_pass = (
            meta_validation["neighborhood_strong_pass"].map(_as_bool).astype(bool)
            if "neighborhood_strong_pass" in meta_validation.columns
            else pd.Series(False, index=meta_validation.index, dtype="bool")
        )
        candidate_checks = [
            ("min_oos_trades", meta_validation["oos_trades"].ge(min_oos_trades), "Enough rolling OOS trades for a sleeve."),
            ("plateau_pass", meta_validation["plateau_pass"].fillna(False), "Candidate belongs to a risk-bucket plateau with <=30% score degradation."),
            ("plateau_strong_pass", meta_validation["plateau_strong_pass"].fillna(False), "Candidate belongs to a risk-bucket plateau with <=15% score degradation."),
            ("neighborhood_pass", neighborhood_pass, "Candidate belongs to a multi-axis neighborhood with <=30% score degradation."),
            ("neighborhood_strong_pass", neighborhood_strong_pass, "Candidate belongs to a multi-axis neighborhood with <=15% score degradation."),
            ("efficiency_ratio_ge_0p70", meta_validation["efficiency_ratio"].ge(0.70), "OOS score does not collapse versus IS score."),
            ("top_removal_pass", meta_validation["top_removed_pass"].fillna(False), f"Remaining {kept_pct:.0%} of trades is still positive after removing top {top_remove_pct:.0f}% winners."),
            ("mc_pass", meta_validation["mc_pass"].fillna(False), "Monte Carlo drawdown/positive-rate stress survives fixed risk."),
            ("win_rate_ge_0p50", meta_validation["oos_win_rate"].ge(0.50), "Rolling OOS win rate is at least 50%."),
            ("calendar_positive_gt_0p60", meta_validation["calendar_positive_day_rate"].gt(min_calendar_positive_day_rate), f"Strictly more than {calendar_pct:.0f}% of rolling OOS calendar days are positive."),
            ("median_trades_per_day_ge_3", meta_validation["calendar_median_trades_per_day"].ge(3.0), "The theoretical full model's frequency target."),
            ("cost10_avg_positive", meta_validation["oos_cost10_avg_r"].gt(0), "Average trade remains positive after extra 10bps cost proxy."),
            ("median_r_positive", meta_validation["oos_median_r"].gt(0), "Median trade is positive; not only tail-driven."),
            ("strict_model_pass", meta_validation["strict_model_pass"].fillna(False), "All strict sleeve-level gates passed."),
            ("theoretical_accept_pass", meta_validation["theoretical_accept_pass"].fillna(False), "Full theoretical sleeve target passed."),
        ]
        for check, condition, note in candidate_checks:
            _diagnostic_count(rows, scope="candidate_oos", check=check, condition=condition, total=total, note=note)

    final_total = int(len(final_holdout))
    if final_total:
        final_checks = [
            ("final_trades_ge_min", final_holdout["final_trades"].ge(min_oos_trades), "Enough trades in the unopened final holdout."),
            ("final_median_positive", final_holdout["final_median_r"].gt(0), "Final holdout median trade is positive."),
            ("final_cost10_avg_positive", final_holdout["final_cost10_avg_r"].gt(0), "Final holdout survives extra 10bps cost proxy."),
            ("final_win_rate_ge_0p50", final_holdout["final_win_rate"].ge(0.50), "Final holdout win rate is at least 50%."),
            ("final_calendar_positive_gt_0p60", final_holdout["final_calendar_positive_day_rate"].gt(min_calendar_positive_day_rate), f"Strictly more than {calendar_pct:.0f}% of final holdout calendar days are positive."),
            ("final_top_removal_pass", final_holdout["final_top_removed_pass"].fillna(False), f"Final holdout remains positive after removing top {top_remove_pct:.0f}% winners."),
            ("final_pass_basic", final_holdout["final_pass_basic"].fillna(False), "All basic final holdout gates passed."),
        ]
        for check, condition, note in final_checks:
            _diagnostic_count(rows, scope="final_holdout", check=check, condition=condition, total=final_total, note=note)

    if not portfolio_meta.empty:
        row = portfolio_meta.iloc[0]
        portfolio_checks = [
            ("portfolio_trades_positive", bool(row.get("portfolio_trades", 0) > 0), "Portfolio has selected marginal trades."),
            ("portfolio_cost10_avg_positive", bool(row.get("portfolio_cost10_avg_r", 0) > 0), "Portfolio average remains positive after extra 10bps cost proxy."),
            ("portfolio_median_positive", bool(row.get("portfolio_median_r", 0) > 0), "Portfolio median trade is positive."),
            ("portfolio_win_rate_ge_0p50", bool(row.get("portfolio_win_rate", 0) >= 0.50), "Portfolio win rate is at least 50%."),
            ("portfolio_calendar_positive_gt_0p60", bool(row.get("calendar_positive_day_rate", 0) > min_calendar_positive_day_rate), f"Strictly more than {calendar_pct:.0f}% of OOS calendar days are positive."),
            ("portfolio_median_trades_per_day_ge_3", bool(row.get("calendar_median_trades_per_day", 0) >= 3.0), "Portfolio meets the target frequency."),
            ("portfolio_top_removal_pass", bool(row.get("top_removed_pass", False)), f"Portfolio remains positive after removing top {top_remove_pct:.0f}% winners."),
            ("portfolio_mc_pass", bool(row.get("mc_pass", False)), "Portfolio passes Monte Carlo stress."),
        ]
        for check, passed, note in portfolio_checks:
            _diagnostic_count(rows, scope="portfolio_oos", check=check, condition=[passed], total=1, note=note)

    return pd.DataFrame(rows)


def _build_hypothesis_ledger(
    specs: list[CandidateSpec],
    candidates: pd.DataFrame,
    rejected: pd.DataFrame,
    meta_validation: pd.DataFrame,
    final_holdout: pd.DataFrame,
    clusters: pd.DataFrame,
    *,
    scan_config: dict[str, object],
) -> pd.DataFrame:
    cand_by = candidates.drop_duplicates("candidate_id").set_index("candidate_id") if not candidates.empty else pd.DataFrame()
    meta_by = meta_validation.drop_duplicates("candidate_id").set_index("candidate_id") if not meta_validation.empty else pd.DataFrame()
    final_by = final_holdout.drop_duplicates("candidate_id").set_index("candidate_id") if not final_holdout.empty else pd.DataFrame()
    if not rejected.empty and "candidate_id" in rejected.columns:
        rejected_by = rejected.groupby("candidate_id", dropna=False).agg(
            rejection_reason=("reason", "first"),
            rejected_rows=("rows", "max") if "rows" in rejected.columns else ("reason", "size"),
        )
    else:
        rejected_by = pd.DataFrame()
    cluster_membership = _plateau_membership(clusters)
    event_store_fingerprint = str(scan_config.get("event_store_fingerprint", ""))
    rows: list[dict[str, object]] = []
    for idx, spec in enumerate(specs):
        cid = spec.candidate_id
        family_key = _spec_family_key(spec)
        base: dict[str, object] = {
            "hypothesis_id": _stable_id("H", cid),
            "family_id": _stable_id("F", family_key),
            "candidate_id": cid,
            "family_key": family_key,
            "candidate_universe_order": idx,
            "source_data_hash": event_store_fingerprint,
            "selection_scope": "development_wfa",
            "final_holdout_opened": False,
            "parent_hypothesis_id": "",
            "generation_source": "balanced_or_guided_scan",
            "source": spec.source,
            "nature_id": spec.nature_id,
            "session_rule": spec.session_rule,
            "stop_model": spec.stop_model,
            "management_id": spec.management_id,
            "risk_bucket": spec.risk_bucket,
            "trigger_rule": spec.trigger_rule,
        }
        if cid in cand_by.index:
            cand = cand_by.loc[cid]
            base.update(
                {
                    "scan_status": "analyzed",
                    "promoted": bool(cand.get("promoted", False)),
                    "candidate_score": _safe_float(cand.get("candidate_score", np.nan), float("nan")),
                    "oos_trades": _safe_int(cand.get("oos_trades", 0)),
                    "oos_cost10_avg_r": _safe_float(cand.get("oos_cost10_avg_r", np.nan), float("nan")),
                    "oos_cost10_sum_r": _safe_float(cand.get("oos_cost10_sum_r", np.nan), float("nan")),
                    "oos_median_r": _safe_float(cand.get("oos_median_r", np.nan), float("nan")),
                    "oos_win_rate": _safe_float(cand.get("oos_win_rate", np.nan), float("nan")),
                    "efficiency_ratio": _safe_float(cand.get("efficiency_ratio", np.nan), float("nan")),
                }
            )
        else:
            base.update({"scan_status": "not_analyzed", "promoted": False})
        if cid in rejected_by.index:
            rej = rejected_by.loc[cid]
            base["rejection_reason"] = str(rej.get("rejection_reason", ""))
            base["rejected_rows"] = _safe_int(rej.get("rejected_rows", 0))
        else:
            base["rejection_reason"] = ""
            base["rejected_rows"] = 0
        if cid in meta_by.index:
            meta = meta_by.loc[cid]
            strict = _as_bool(meta.get("strict_model_pass", False))
            theoretical = _as_bool(meta.get("theoretical_accept_pass", False))
            grade = str(meta.get("model_grade", "research_only"))
            base.update(
                {
                    "model_grade": grade,
                    "strict_model_pass": strict,
                    "theoretical_accept_pass": theoretical,
                    "top_removal_pass": _as_bool(meta.get("top_removal_pass", meta.get("top_removed_pass", False))),
                    "mc_pass": _as_bool(meta.get("mc_pass", False)),
                    "plateau_pass": _as_bool(meta.get("plateau_pass", False)),
                    "plateau_strong_pass": _as_bool(meta.get("plateau_strong_pass", False)),
                    "calendar_positive_day_rate": _safe_float(meta.get("calendar_positive_day_rate", np.nan), float("nan")),
                    "calendar_median_trades_per_day": _safe_float(meta.get("calendar_median_trades_per_day", np.nan), float("nan")),
                    "model_quality_score": _safe_float(meta.get("model_quality_score", np.nan), float("nan")),
                }
            )
            if theoretical:
                status = "theoretical_accept"
            elif strict:
                status = "robust_watchlist"
            else:
                status = grade
        elif _as_bool(base.get("promoted", False)):
            status = "promoted_without_meta"
        elif base.get("rejection_reason"):
            status = "rejected_" + str(base["rejection_reason"])
        else:
            status = "not_evaluated"
        if cid in final_by.index:
            final = final_by.loc[cid]
            base.update(
                {
                    "final_holdout_opened": True,
                    "final_pass_basic": _as_bool(final.get("final_pass_basic", False)),
                    "final_trades": _safe_int(final.get("final_trades", 0)),
                    "final_cost10_avg_r": _safe_float(final.get("final_cost10_avg_r", np.nan), float("nan")),
                    "final_median_r": _safe_float(final.get("final_median_r", np.nan), float("nan")),
                    "final_calendar_positive_day_rate": _safe_float(final.get("final_calendar_positive_day_rate", np.nan), float("nan")),
                }
            )
        else:
            base.update({"final_pass_basic": False})
        base.update(cluster_membership.get(cid, {}))
        base["ledger_status"] = status
        rows.append(base)
    ledger = pd.DataFrame(rows)
    return ledger.sort_values(["theoretical_accept_pass", "strict_model_pass", "promoted", "candidate_score"], ascending=[False, False, False, False]) if not ledger.empty else ledger


def _ledger_key_set(df: pd.DataFrame) -> set[str]:
    if df.empty or "event_id" not in df.columns:
        return set()
    source = df["source"].astype(str) if "source" in df.columns else pd.Series("", index=df.index)
    return set((df["event_id"].astype(str) + "|" + source).tolist())


def _overlap_pct(left: set[str], right: set[str]) -> float:
    if not left:
        return 0.0
    return float(len(left & right) / len(left))


def _build_diversity_scores(
    meta_validation: pd.DataFrame,
    ledgers: dict[str, pd.DataFrame],
    portfolio: pd.DataFrame,
    portfolio_trades: pd.DataFrame,
) -> pd.DataFrame:
    if meta_validation.empty:
        return pd.DataFrame()
    work = meta_validation.copy()
    if not portfolio.empty and "candidate_id" in portfolio.columns and "accepted" in portfolio.columns:
        accepted_ids = set(portfolio.loc[portfolio["accepted"].astype(bool), "candidate_id"].astype(str))
    else:
        accepted_ids = set()
    candidate_keys = {cid: _ledger_key_set(ledger) for cid, ledger in ledgers.items()}
    candidate_symbols = {
        cid: set(ledger["symbol"].astype(str)) if not ledger.empty and "symbol" in ledger.columns else set()
        for cid, ledger in ledgers.items()
    }
    candidate_days = {
        cid: set(ledger["date"].astype(str)) if not ledger.empty and "date" in ledger.columns else set()
        for cid, ledger in ledgers.items()
    }
    total = max(1, len(work))
    axis_freq: dict[str, dict[str, float]] = {}
    for axis in HYPOTHESIS_AXES:
        if axis in work.columns:
            counts = work[axis].astype(str).value_counts(normalize=True)
            axis_freq[axis] = counts.to_dict()

    ranked = work.sort_values(["model_quality_score", "oos_cost10_sum_r"], ascending=[False, False]) if "model_quality_score" in work.columns else work
    peer_ids = ranked["candidate_id"].astype(str).head(80).tolist()
    rows: list[dict[str, object]] = []
    for _, row in work.iterrows():
        cid = str(row["candidate_id"])
        keys = candidate_keys.get(cid, set())
        symbols = candidate_symbols.get(cid, set())
        days = candidate_days.get(cid, set())
        other_portfolio = portfolio_trades
        if not portfolio_trades.empty and "portfolio_candidate_id" in portfolio_trades.columns:
            other_portfolio = portfolio_trades[~portfolio_trades["portfolio_candidate_id"].astype(str).eq(cid)]
        portfolio_keys = _ledger_key_set(other_portfolio)
        portfolio_symbols = set(other_portfolio["symbol"].astype(str)) if not other_portfolio.empty and "symbol" in other_portfolio.columns else set()
        portfolio_days = set(other_portfolio["date"].astype(str)) if not other_portfolio.empty and "date" in other_portfolio.columns else set()
        max_peer_overlap = 0.0
        for other_id in peer_ids:
            if other_id == cid:
                continue
            max_peer_overlap = max(max_peer_overlap, _overlap_pct(keys, candidate_keys.get(other_id, set())))
        axis_uniqueness = 0.0
        axis_count = 0
        for axis, freqs in axis_freq.items():
            axis_uniqueness += 1.0 - float(freqs.get(str(row.get(axis, "")), 1.0))
            axis_count += 1
        axis_uniqueness = axis_uniqueness / axis_count if axis_count else 0.0
        portfolio_event_overlap = _overlap_pct(keys, portfolio_keys)
        portfolio_symbol_overlap = _overlap_pct(symbols, portfolio_symbols)
        portfolio_day_overlap = _overlap_pct(days, portfolio_days)
        robustness_score = (
            0.25 * float(_as_bool(row.get("top_removal_pass", row.get("top_removed_pass", False))))
            + 0.25 * float(_as_bool(row.get("mc_pass", False)))
            + 0.20 * float(_as_bool(row.get("plateau_pass", False)))
            + 0.15 * min(max(_safe_float(row.get("calendar_positive_day_rate", 0.0)), 0.0), 1.0)
            + 0.15 * min(max(_safe_float(row.get("oos_top_symbol_independence_pct", 0.0)) / 0.50, 0.0), 1.0)
        )
        novelty_score = (
            0.35 * (1.0 - portfolio_event_overlap)
            + 0.20 * (1.0 - max_peer_overlap)
            + 0.20 * (1.0 - portfolio_symbol_overlap)
            + 0.15 * (1.0 - portfolio_day_overlap)
            + 0.10 * axis_uniqueness
        )
        selection_utility = 0.45 * _safe_float(row.get("model_quality_score", 0.0)) + 0.35 * novelty_score + 0.20 * robustness_score
        if portfolio_event_overlap >= 0.70 or max_peer_overlap >= 0.70:
            grade = "redundant"
        elif novelty_score >= 0.70 and robustness_score >= 0.45:
            grade = "independent_watchlist"
        elif cid in accepted_ids:
            grade = "portfolio_selected"
        else:
            grade = "complementary_research"
        rows.append(
            {
                "candidate_id": cid,
                "diversity_grade": grade,
                "novelty_score": float(novelty_score),
                "robustness_score": float(robustness_score),
                "selection_utility_score": float(selection_utility),
                "portfolio_event_overlap_pct": float(portfolio_event_overlap),
                "portfolio_symbol_overlap_pct": float(portfolio_symbol_overlap),
                "portfolio_day_overlap_pct": float(portfolio_day_overlap),
                "max_peer_event_overlap_pct": float(max_peer_overlap),
                "axis_uniqueness_score": float(axis_uniqueness),
                "candidate_trades": int(len(keys)),
                "portfolio_selected": cid in accepted_ids,
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values(["selection_utility_score", "novelty_score"], ascending=[False, False]) if not out.empty else out


def _candidate_failure_driver(row: pd.Series, *, min_calendar_positive_day_rate: float) -> str:
    drivers: list[str] = []
    if _safe_float(row.get("calendar_positive_day_rate", 0.0)) <= min_calendar_positive_day_rate:
        drivers.append("calendar_sparse")
    if _safe_float(row.get("calendar_median_trades_per_day", 0.0)) < 3.0:
        drivers.append("frequency_low")
    if not _as_bool(row.get("top_removal_pass", row.get("top_removed_pass", False))):
        drivers.append("tail_dependent")
    if not _as_bool(row.get("mc_pass", False)):
        drivers.append("mc_fragile")
    if not _as_bool(row.get("plateau_pass", False)):
        drivers.append("plateau_fragile")
    return "+".join(drivers) if drivers else "exploit_neighborhood"


def _queue_status_hint(candidate_id: str, *, universe_ids: set[str], analyzed_ids: set[str], rejected_ids: set[str]) -> str:
    if candidate_id in analyzed_ids:
        return "already_analyzed"
    if candidate_id in rejected_ids:
        return "already_rejected"
    if candidate_id in universe_ids:
        return "in_current_universe_unanalyzed"
    return "new_guided_candidate"


def _build_self_improvement_queue(
    dev_df: pd.DataFrame,
    trigger_masks: dict[str, pd.Series],
    specs: list[CandidateSpec],
    candidates: pd.DataFrame,
    rejected: pd.DataFrame,
    meta_validation: pd.DataFrame,
    diversity_scores: pd.DataFrame,
    *,
    min_is_trades: int,
    min_calendar_positive_day_rate: float,
    max_rows: int = DEFAULT_SELF_IMPROVEMENT_QUEUE_ROWS,
) -> pd.DataFrame:
    if meta_validation.empty:
        return pd.DataFrame()
    universe_ids = {spec.candidate_id for spec in specs}
    analyzed_ids = set(candidates["candidate_id"].astype(str)) if not candidates.empty and "candidate_id" in candidates.columns else set()
    rejected_ids = set(rejected["candidate_id"].astype(str)) if not rejected.empty and "candidate_id" in rejected.columns else set()
    div = diversity_scores.set_index("candidate_id") if not diversity_scores.empty and "candidate_id" in diversity_scores.columns else pd.DataFrame()
    seeds = meta_validation.copy()
    if not div.empty:
        seeds = seeds.merge(div[["novelty_score", "selection_utility_score", "diversity_grade"]], left_on="candidate_id", right_index=True, how="left")
    else:
        seeds["novelty_score"] = 0.0
        seeds["selection_utility_score"] = seeds.get("model_quality_score", 0.0)
        seeds["diversity_grade"] = "unknown"
    seeds["seed_failure_driver"] = seeds.apply(lambda row: _candidate_failure_driver(row, min_calendar_positive_day_rate=min_calendar_positive_day_rate), axis=1)
    seeds["seed_priority"] = (
        pd.to_numeric(seeds.get("selection_utility_score", 0.0), errors="coerce").fillna(0.0)
        + 0.15 * seeds["seed_failure_driver"].str.contains("calendar_sparse").astype(float)
        + 0.10 * seeds["seed_failure_driver"].str.contains("tail_dependent").astype(float)
        + 0.05 * seeds["seed_failure_driver"].str.contains("plateau_fragile").astype(float)
    )
    seeds = seeds.sort_values(["seed_priority", "model_quality_score", "oos_cost10_sum_r"], ascending=[False, False, False]).head(120)

    options_by_source: dict[str, dict[str, list[str]]] = {}
    for source, source_df in dev_df.groupby("source", dropna=False):
        natures, sessions, stops, managements, risks, triggers = _axis_values_for_source(
            str(source),
            source_df,
            trigger_masks,
            max_natures_per_source=12,
            candidate_profile="exhaustive",
        )
        options_by_source[str(source)] = {
            "nature_id": natures,
            "session_rule": sessions,
            "stop_model": stops,
            "management_id": managements,
            "risk_bucket": risks,
            "trigger_rule": triggers,
        }

    rows: list[dict[str, object]] = []
    emitted: set[str] = set()

    def add_proposal(seed: pd.Series, spec: CandidateSpec, *, mutation_axis: str, mutation_value: str, rationale: str, generation_reason: str) -> None:
        if spec.candidate_id == str(seed["candidate_id"]):
            return
        key = f"{seed['candidate_id']}->{spec.candidate_id}|{mutation_axis}"
        if key in emitted:
            return
        emitted.add(key)
        try:
            est_rows = int(_candidate_mask(dev_df, spec, trigger_masks).sum())
        except KeyError:
            est_rows = 0
        status_hint = _queue_status_hint(spec.candidate_id, universe_ids=universe_ids, analyzed_ids=analyzed_ids, rejected_ids=rejected_ids)
        seed_score = _safe_float(seed.get("seed_priority", 0.0))
        novelty = _safe_float(seed.get("novelty_score", 0.0))
        status_bonus = 0.30 if status_hint == "new_guided_candidate" else -0.30 if status_hint == "already_analyzed" else -0.15
        row_penalty = -0.40 if est_rows < min_is_trades else 0.05 * min(est_rows / 250.0, 1.0)
        priority_score = seed_score + 0.25 * novelty + status_bonus + row_penalty
        rows.append(
            {
                "priority_score": float(priority_score),
                "seed_candidate_id": str(seed["candidate_id"]),
                "proposed_candidate_id": spec.candidate_id,
                "mutation_axis": mutation_axis,
                "mutation_value": mutation_value,
                "generation_reason": generation_reason,
                "failure_driver": str(seed.get("seed_failure_driver", "")),
                "rationale": rationale,
                "status_hint": status_hint,
                "estimated_development_rows": est_rows,
                "source": spec.source,
                "nature_id": spec.nature_id,
                "session_rule": spec.session_rule,
                "stop_model": spec.stop_model,
                "management_id": spec.management_id,
                "risk_bucket": spec.risk_bucket,
                "trigger_rule": spec.trigger_rule,
                "seed_model_quality_score": _safe_float(seed.get("model_quality_score", np.nan), float("nan")),
                "seed_novelty_score": novelty,
                "seed_diversity_grade": str(seed.get("diversity_grade", "")),
            }
        )

    for _, seed in seeds.iterrows():
        source = str(seed["source"])
        opts = options_by_source.get(source)
        if not opts:
            continue
        seed_spec = _spec_from_series(seed)
        failure = str(seed.get("seed_failure_driver", ""))
        session_values = SESSION_NEIGHBORS.get(seed_spec.session_rule, ["all", "non_us", "not_asia_overlap"])
        if "calendar_sparse" in failure or "frequency_low" in failure or "tail_dependent" in failure:
            for session in session_values[:4]:
                add_proposal(
                    seed,
                    _mutate_spec(seed_spec, session_rule=session),
                    mutation_axis="session_rule",
                    mutation_value=session,
                    generation_reason="calendar_or_tail_diversification",
                    rationale="Search a different session partition to improve calendar density and reduce same-regime tails.",
                )
        if "tail_dependent" in failure or "mc_fragile" in failure or "plateau_fragile" in failure:
            for risk in _adjacent_values([r for r in RISK_ORDER if r in opts["risk_bucket"]], seed_spec.risk_bucket, radius=1):
                add_proposal(
                    seed,
                    _mutate_spec(seed_spec, risk_bucket=risk),
                    mutation_axis="risk_bucket",
                    mutation_value=risk,
                    generation_reason="risk_plateau_neighbor",
                    rationale="Check whether the edge survives adjacent risk buckets instead of a single sharp point.",
                )
        for trigger in _adjacent_values(opts["trigger_rule"], seed_spec.trigger_rule, radius=2)[:4]:
            add_proposal(
                seed,
                _mutate_spec(seed_spec, trigger_rule=trigger),
                mutation_axis="trigger_rule",
                mutation_value=trigger,
                generation_reason="trigger_family_neighbor",
                rationale="Explore nearby entry-known trigger definitions around the current fader nature.",
            )
        if "tail_dependent" in failure or "mc_fragile" in failure:
            for stop in _adjacent_values(opts["stop_model"], seed_spec.stop_model, radius=1)[:3]:
                add_proposal(
                    seed,
                    _mutate_spec(seed_spec, stop_model=stop),
                    mutation_axis="stop_model",
                    mutation_value=stop,
                    generation_reason="execution_model_neighbor",
                    rationale="Stress whether a nearby local-structure stop makes the distribution less tail-dependent.",
                )
            for management in _adjacent_values(opts["management_id"], seed_spec.management_id, radius=1)[:3]:
                add_proposal(
                    seed,
                    _mutate_spec(seed_spec, management_id=management),
                    mutation_axis="management_id",
                    mutation_value=management,
                    generation_reason="management_neighbor",
                    rationale="Test adjacent partial/exit management families without changing event source.",
                )
        nature_options = opts["nature_id"]
        nature_neighbors = [n for n in nature_options if n != seed_spec.nature_id][:4] if seed_spec.nature_id == "ALL" else ["ALL"] + _adjacent_values(nature_options, seed_spec.nature_id, radius=1)
        for nature in nature_neighbors[:4]:
            add_proposal(
                seed,
                _mutate_spec(seed_spec, nature_id=nature),
                mutation_axis="nature_id",
                mutation_value=nature,
                generation_reason="nature_diversification",
                rationale="Search a different fader nature to reduce dependence on one event family.",
            )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values(["priority_score", "estimated_development_rows"], ascending=[False, False])
    out = out.drop_duplicates("proposed_candidate_id", keep="first")
    return out.head(max_rows).reset_index(drop=True)


def _build_improvement_plan(
    candidates: pd.DataFrame,
    rejected: pd.DataFrame,
    axis_summary: pd.DataFrame,
    meta_validation: pd.DataFrame,
    portfolio_meta: pd.DataFrame,
    diversity_scores: pd.DataFrame | None = None,
    self_improvement_queue: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if not rejected.empty and "reason" in rejected.columns:
        for reason, count in rejected["reason"].value_counts().head(8).items():
            if reason == "cost10_not_positive":
                action = "Tighten entry quality or improve execution cost/risk buckets; current edge is eaten by costs."
            elif reason == "no_train_pass_oos_rows":
                action = "Increase sample breadth or reduce sparse rule intersections before WFA promotion."
            elif reason == "median_not_positive":
                action = "Avoid tail-only pockets; require positive median before ranking by average."
            elif reason == "top_trade_dependent":
                action = "Diversify away from top winners; prefer candidates with top-removal pass and wider symbols."
            elif reason == "positive_day_rate_low":
                action = "Gate by session/regime; current returns are too clustered by day."
            else:
                action = "Inspect rejected_reasons.csv and candidate_axis_summary.csv for the responsible axis."
            rows.append({"priority": len(rows) + 1, "source": "reject_funnel", "signal": reason, "count": int(count), "recommended_action": action})
    if not meta_validation.empty:
        strict = int(meta_validation["strict_model_pass"].sum()) if "strict_model_pass" in meta_validation.columns else 0
        theoretical = int(meta_validation["theoretical_accept_pass"].sum()) if "theoretical_accept_pass" in meta_validation.columns else 0
        rows.append(
            {
                "priority": len(rows) + 1,
                "source": "model_gate",
                "signal": "strict_vs_theoretical_acceptance",
                "count": strict,
                "recommended_action": f"Strict model passes={strict}, theoretical full passes={theoretical}. If theoretical is zero, combine sleeves at portfolio level rather than forcing each sleeve to trade 3/day.",
            }
        )
    if not portfolio_meta.empty:
        row = portfolio_meta.iloc[0]
        rows.append(
            {
                "priority": len(rows) + 1,
                "source": "portfolio",
                "signal": "portfolio_frequency_and_mc",
                "count": int(row.get("portfolio_trades", 0) or 0),
                "recommended_action": "Use portfolio_meta_validation.csv to decide whether sleeve combination, not single-sleeve optimization, meets frequency/drawdown targets.",
            }
        )
    if not axis_summary.empty:
        top_sessions = axis_summary[(axis_summary["axis"].eq("session_rule")) & (axis_summary["promoted_rows"] > 0)].head(4)
        if not top_sessions.empty:
            rows.append(
                {
                    "priority": len(rows) + 1,
                    "source": "axis_summary",
                    "signal": "best_sessions",
                    "count": int(top_sessions["promoted_rows"].sum()),
                    "recommended_action": "Prioritize session-specific sleeve portfolios: " + ", ".join(top_sessions["value"].astype(str).tolist()),
                }
            )
    if diversity_scores is not None and not diversity_scores.empty:
        independent = int(diversity_scores["diversity_grade"].astype(str).eq("independent_watchlist").sum()) if "diversity_grade" in diversity_scores.columns else 0
        rows.append(
            {
                "priority": len(rows) + 1,
                "source": "diversity",
                "signal": "independent_watchlist_candidates",
                "count": independent,
                "recommended_action": "Use diversity_scores.csv to prefer sleeves with low event/symbol/day overlap before adding more variants of the same pattern.",
            }
        )
    if self_improvement_queue is not None and not self_improvement_queue.empty:
        new_guided = int(self_improvement_queue["status_hint"].astype(str).eq("new_guided_candidate").sum()) if "status_hint" in self_improvement_queue.columns else 0
        rows.append(
            {
                "priority": len(rows) + 1,
                "source": "self_improvement_queue",
                "signal": "next_guided_candidates",
                "count": new_guided,
                "recommended_action": "Run the next scan with this queue as guided candidates; keep final holdout as diagnostic only, not as a generator target.",
            }
        )
    return pd.DataFrame(rows)


def _build_compute_budget_plan(
    *,
    candidate_universe_rows: int,
    candidate_rows: int,
    guided_candidate_rows: int,
    rejected: pd.DataFrame,
    self_improvement_queue: pd.DataFrame,
) -> pd.DataFrame:
    rejected_count = int(len(rejected))
    queue_rows = int(len(self_improvement_queue))
    new_queue_rows = int(self_improvement_queue["status_hint"].astype(str).eq("new_guided_candidate").sum()) if not self_improvement_queue.empty and "status_hint" in self_improvement_queue.columns else 0
    rows = [
        {
            "stage": "event_store",
            "rows": "cached",
            "cost_class": "already_paid",
            "policy": "Reuse Parquet event outcomes; do not rebuild raw artifacts unless source logic changes.",
            "next_action": "skip_rebuild",
        },
        {
            "stage": "cheap_prefilter",
            "rows": candidate_universe_rows,
            "cost_class": "low",
            "policy": "Reject sparse/weak candidates before WFA windows.",
            "next_action": "keep_first",
        },
        {
            "stage": "rolling_wfa",
            "rows": candidate_rows,
            "cost_class": "medium",
            "policy": "Run WFA only after total-row/symbol/day prefilter.",
            "next_action": "parallelize_by_candidate_later",
        },
        {
            "stage": "rejection_funnel",
            "rows": rejected_count,
            "cost_class": "low",
            "policy": "Use rejection reasons to avoid expanding dead branches.",
            "next_action": "feed_queue_generator",
        },
        {
            "stage": "guided_queue",
            "rows": queue_rows,
            "cost_class": "bounded_medium",
            "policy": "Cap next guided scan; prefer new candidates with enough estimated development rows.",
            "next_action": f"scan_top_{min(new_queue_rows, DEFAULT_SELF_IMPROVEMENT_QUEUE_ROWS)}_new_guided",
        },
        {
            "stage": "stress_tests",
            "rows": candidate_rows,
            "cost_class": "medium_high",
            "policy": "Run MC/top-removal only for promoted/meta candidates, not the full grid.",
            "next_action": "keep_after_promotion",
        },
        {
            "stage": "final_holdout",
            "rows": candidate_rows,
            "cost_class": "audit_only",
            "policy": "Use final holdout for verification only; never generate new candidates from final wins.",
            "next_action": "seal_family_after_open",
        },
    ]
    if guided_candidate_rows > 0:
        rows.append(
            {
                "stage": "guided_iteration_read",
                "rows": guided_candidate_rows,
                "cost_class": "medium",
                "policy": "Compare guided candidates against base candidates by strict gates, not promoted count.",
                "next_action": "keep_only_if_calendar_and_top_removal_improve",
            }
        )
    return pd.DataFrame(rows)


def _build_theoretical_model_gap_analysis(
    *,
    hypothesis_grammar: pd.DataFrame,
    hypothesis_ledger: pd.DataFrame,
    plateau_neighborhoods: pd.DataFrame,
    diversity_scores: pd.DataFrame,
    self_improvement_queue: pd.DataFrame,
    portfolio: pd.DataFrame,
    diversified_portfolio: pd.DataFrame,
    compute_budget_plan: pd.DataFrame,
    lookahead: pd.DataFrame,
) -> pd.DataFrame:
    def status(ok: bool, partial: bool = False) -> str:
        if ok:
            return "implemented"
        if partial:
            return "partial"
        return "missing"

    lookahead_ok = bool(not lookahead.empty and "status" in lookahead.columns and lookahead["status"].astype(str).eq("pass").all())
    rows = [
        {
            "capability": "entry_known_hypothesis_grammar",
            "theoretical_goal": "Typed primitives only from features available at or before entry.",
            "current_status": status(not hypothesis_grammar.empty),
            "implemented_artifact": "hypothesis_grammar.csv",
            "remaining_gap": "Add new raw event sources before expanding grammar beyond existing artifacts.",
            "priority": 2,
        },
        {
            "capability": "sealed_hypothesis_ledger",
            "theoretical_goal": "Every hypothesis has id, family id, source-data hash, status and holdout audit.",
            "current_status": "partial" if not hypothesis_ledger.empty else "missing",
            "implemented_artifact": "hypothesis_ledger.csv",
            "remaining_gap": "Cross-run immutable ledger enforcement is still local to output_dir, not a central database.",
            "priority": 2,
        },
        {
            "capability": "multi_axis_plateau_neighborhoods",
            "theoretical_goal": "Prefer broad flat zones over single best points across risk/session/trigger/stop/management/nature axes.",
            "current_status": status(not plateau_neighborhoods.empty),
            "implemented_artifact": "plateau_neighborhoods.csv",
            "remaining_gap": "Current neighborhoods are categorical; numeric threshold heatmaps need raw feature-threshold replay.",
            "priority": 1,
        },
        {
            "capability": "diversity_and_novelty_scoring",
            "theoretical_goal": "Penalize same event, symbol, day and peer-return drivers.",
            "current_status": status(not diversity_scores.empty),
            "implemented_artifact": "diversity_scores.csv",
            "remaining_gap": "Return-driver correlation can be made stronger with minute-path feature vectors.",
            "priority": 1,
        },
        {
            "capability": "failure_driven_iteration_queue",
            "theoretical_goal": "Use model failures to generate the next bounded search queue.",
            "current_status": status(not self_improvement_queue.empty),
            "implemented_artifact": "self_improvement_queue.csv",
            "remaining_gap": "Queue still mutates existing artifact axes; it cannot invent unbuilt path-replay event sources.",
            "priority": 1,
        },
        {
            "capability": "multi_objective_portfolio_builder",
            "theoretical_goal": "Build portfolio by marginal contribution, novelty, top-removal, calendar and overlap, not one-candidate score.",
            "current_status": status(not diversified_portfolio.empty and bool(diversified_portfolio.get("accepted", pd.Series(dtype=bool)).astype(bool).any()), partial=not portfolio.empty),
            "implemented_artifact": "diversified_portfolio_candidates.csv",
            "remaining_gap": "Still greedy bounded selection, not full combinatorial optimization; this is deliberate for 16GB/i5.",
            "priority": 1,
        },
        {
            "capability": "compute_budget_controller",
            "theoretical_goal": "Cheap reject first; expensive stress only for survivors; bounded guided scans.",
            "current_status": status(not compute_budget_plan.empty),
            "implemented_artifact": "compute_budget_plan.csv",
            "remaining_gap": "No multiprocessing scheduler yet; current controller is artifact/policy level.",
            "priority": 3,
        },
        {
            "capability": "lookahead_and_final_holdout_guard",
            "theoretical_goal": "Never generate from future/final data; final holdout remains audit only.",
            "current_status": status(lookahead_ok),
            "implemented_artifact": "lookahead_audit.csv, hypothesis_ledger.csv",
            "remaining_gap": "A truly fresh judge still requires future/live-forward data outside this inspected 365d cache.",
            "priority": 1,
        },
        {
            "capability": "new_fader_nature_discovery",
            "theoretical_goal": "Discover genuinely different fader natures, not just variants of the same failed-pump source.",
            "current_status": "partial",
            "implemented_artifact": "self_improvement_queue.csv",
            "remaining_gap": "Requires new executable event-source/path replay builders for fresh lower-high, failed retest and static/runner separators.",
            "priority": 1,
        },
    ]
    return pd.DataFrame(rows).sort_values(["priority", "capability"], ascending=[True, True])


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


def _build_diversified_portfolio(
    meta_validation: pd.DataFrame,
    diversity_scores: pd.DataFrame,
    ledgers: dict[str, pd.DataFrame],
    windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]],
    *,
    top_removal_pct: float,
    max_candidates: int = DEFAULT_DIVERSIFIED_PORTFOLIO_MAX_CANDIDATES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if meta_validation.empty:
        return pd.DataFrame(), pd.DataFrame()
    work = meta_validation.copy()
    if not diversity_scores.empty and "candidate_id" in diversity_scores.columns:
        div_cols = [
            "candidate_id",
            "novelty_score",
            "robustness_score",
            "selection_utility_score",
            "diversity_grade",
            "portfolio_event_overlap_pct",
            "max_peer_event_overlap_pct",
        ]
        work = work.merge(diversity_scores[[c for c in div_cols if c in diversity_scores.columns]], on="candidate_id", how="left")
    for col in ["novelty_score", "robustness_score", "selection_utility_score", "portfolio_event_overlap_pct", "max_peer_event_overlap_pct"]:
        if col not in work.columns:
            work[col] = 0.0
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0)
    work["portfolio_rank_score"] = (
        0.28 * pd.to_numeric(work.get("model_quality_score", 0.0), errors="coerce").fillna(0.0)
        + 0.22 * work["selection_utility_score"]
        + 0.18 * work["novelty_score"]
        + 0.14 * work["robustness_score"]
        + 0.10 * pd.to_numeric(work.get("oos_cost10_avg_r", 0.0), errors="coerce").fillna(0.0).clip(-1.0, 1.0)
        + 0.08 * pd.to_numeric(work.get("calendar_positive_day_rate", 0.0), errors="coerce").fillna(0.0)
    )
    candidates = work.sort_values(["strict_model_pass", "portfolio_rank_score", "oos_cost10_sum_r"], ascending=[False, False, False]).head(180)
    selected = pd.DataFrame()
    rows: list[dict[str, object]] = []
    calendar_days = _calendar_days_from_windows(windows)
    selected_sessions: dict[str, int] = {}
    selected_sources: dict[str, int] = {}

    for _, cand in candidates.iterrows():
        cid = str(cand["candidate_id"])
        ledger = ledgers.get(cid, pd.DataFrame()).copy()
        if ledger.empty:
            continue
        before_keys = _ledger_key_set(ledger)
        marginal = _remove_collisions(ledger, selected)
        marginal = marginal.drop_duplicates(["event_id", "source"], keep="first")
        if marginal.empty:
            continue
        after_keys = _ledger_key_set(marginal)
        collision_removed_pct = 1.0 - (len(after_keys) / len(before_keys)) if before_keys else 0.0
        net_m = _summary(marginal, "net_r")
        cost_m = _summary(marginal, "cost10_r")
        calendar = _calendar_stats(marginal, calendar_days, "cost10_r")
        top_removed = _top_removal_summary(marginal["cost10_r"], remove_pct=top_removal_pct)
        selected_keys = _ledger_key_set(selected)
        event_overlap = _overlap_pct(after_keys, selected_keys)
        session = str(cand.get("session_rule", ""))
        source = str(cand.get("source", ""))
        session_penalty = min(selected_sessions.get(session, 0) * 0.04, 0.20)
        source_penalty = min(selected_sources.get(source, 0) * 0.02, 0.12)
        marginal_score = (
            0.24 * np.tanh(_safe_float(cost_m["avg_r"]) / 0.18)
            + 0.18 * np.tanh(_safe_float(net_m["median_r"]) / 0.18)
            + 0.14 * _safe_float(net_m["win_rate"])
            + 0.12 * min(_safe_float(calendar["calendar_positive_day_rate"]) / 0.60, 1.0)
            + 0.10 * float(bool(top_removed["top_removed_pass"]))
            + 0.10 * min(max(_safe_float(cand.get("novelty_score", 0.0)), 0.0), 1.0)
            + 0.08 * min(max(_safe_float(cand.get("robustness_score", 0.0)), 0.0), 1.0)
            + 0.04 * min(_safe_float(net_m["top_symbol_independence_pct"]) / 0.50, 1.0)
            - 0.16 * event_overlap
            - 0.10 * collision_removed_pct
            - session_penalty
            - source_penalty
        )
        accept = bool(
            net_m["trades"] >= 8
            and _safe_float(cost_m["avg_r"]) > 0
            and _safe_float(net_m["median_r"]) > 0
            and event_overlap < 0.70
            and marginal_score > 0.05
        )
        rows.append(
            {
                "candidate_id": cid,
                "accepted": accept,
                "order": len(rows),
                "marginal_score": float(marginal_score),
                "collision_removed_pct": float(collision_removed_pct),
                "selected_event_overlap_pct": float(event_overlap),
                "session_penalty": float(session_penalty),
                "source_penalty": float(source_penalty),
                "novelty_score": _safe_float(cand.get("novelty_score", np.nan), float("nan")),
                "robustness_score": _safe_float(cand.get("robustness_score", np.nan), float("nan")),
                "diversity_grade": str(cand.get("diversity_grade", "")),
                "marginal_cost10_avg_r": cost_m["avg_r"],
                "marginal_cost10_sum_r": cost_m["sum_r"],
                "marginal_calendar_positive_day_rate": calendar["calendar_positive_day_rate"],
                "marginal_calendar_median_trades_per_day": calendar["calendar_median_trades_per_day"],
                "marginal_top_removed_pass": top_removed["top_removed_pass"],
                "marginal_top_removed_remaining_sum_r": top_removed["top_removed_remaining_sum_r"],
                **{f"marginal_{k}": v for k, v in net_m.items()},
            }
        )
        if accept:
            marginal = marginal.copy()
            marginal["portfolio_candidate_id"] = cid
            marginal["portfolio_model"] = "diversified_multi_objective"
            selected = pd.concat([selected, marginal], ignore_index=True)
            selected = selected.drop_duplicates(["event_id", "source"], keep="first")
            selected_sessions[session] = selected_sessions.get(session, 0) + 1
            selected_sources[source] = selected_sources.get(source, 0) + 1
        if int(sum(1 for row in rows if row.get("accepted"))) >= max_candidates:
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
    plateau_neighborhoods: pd.DataFrame,
    portfolio: pd.DataFrame,
    diversified_portfolio: pd.DataFrame,
    rejected: pd.DataFrame,
    axis_summary: pd.DataFrame,
    meta_validation: pd.DataFrame,
    final_holdout: pd.DataFrame,
    portfolio_meta: pd.DataFrame,
    diversified_portfolio_meta: pd.DataFrame,
    gate_diagnostics: pd.DataFrame,
    hypothesis_grammar: pd.DataFrame,
    hypothesis_ledger: pd.DataFrame,
    diversity_scores: pd.DataFrame,
    self_improvement_queue: pd.DataFrame,
    compute_budget_plan: pd.DataFrame,
    theoretical_model_gap_analysis: pd.DataFrame,
    improvement_plan: pd.DataFrame,
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
    neighborhood_cols = [
        "neighborhood_id",
        "varied_axis",
        "neighborhood_pass",
        "neighborhood_strong_pass",
        "members",
        "unique_values",
        "varied_values",
        "best_candidate_id",
        "best_candidate_score",
        "score_degradation_pct",
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
    diversified_port_cols = [
        "candidate_id",
        "accepted",
        "marginal_score",
        "marginal_trades",
        "marginal_cost10_avg_r",
        "marginal_median_r",
        "marginal_calendar_positive_day_rate",
        "marginal_top_removed_pass",
        "selected_event_overlap_pct",
        "novelty_score",
        "robustness_score",
        "diversity_grade",
    ]
    meta_cols = [
        "candidate_id",
        "model_grade",
        "strict_model_pass",
        "theoretical_accept_pass",
        "plateau_pass",
        "plateau_strong_pass",
        "neighborhood_pass",
        "neighborhood_strong_pass",
        "oos_trades",
        "oos_cost10_avg_r",
        "oos_median_r",
        "oos_win_rate",
        "calendar_positive_day_rate",
        "calendar_median_trades_per_day",
        "top_removed_remaining_sum_r",
        "mc_shuffle_dd95_pct",
        "mc_bootstrap_positive_rate",
        "model_quality_score",
    ]
    final_cols = [
        "candidate_id",
        "final_pass_basic",
        "final_trades",
        "final_cost10_avg_r",
        "final_median_r",
        "final_win_rate",
        "final_calendar_positive_day_rate",
        "final_top_removed_remaining_sum_r",
    ]
    portfolio_meta_cols = [
        "portfolio_trades",
        "portfolio_cost10_avg_r",
        "portfolio_cost10_sum_r",
        "portfolio_median_r",
        "portfolio_win_rate",
        "calendar_positive_day_rate",
        "calendar_median_trades_per_day",
        "top_removed_remaining_sum_r",
        "mc_shuffle_dd95_pct",
        "mc_bootstrap_positive_rate",
        "mc_pass",
    ]
    gap_cols = [
        "capability",
        "current_status",
        "implemented_artifact",
        "remaining_gap",
        "priority",
    ]
    budget_cols = [
        "stage",
        "rows",
        "cost_class",
        "policy",
        "next_action",
    ]
    gate_cols = [
        "scope",
        "check",
        "passed",
        "total",
        "pass_rate",
        "note",
    ]
    ledger_cols = [
        "hypothesis_id",
        "family_id",
        "candidate_id",
        "ledger_status",
        "model_grade",
        "promoted",
        "strict_model_pass",
        "theoretical_accept_pass",
        "top_removal_pass",
        "mc_pass",
        "calendar_positive_day_rate",
        "final_pass_basic",
        "rejection_reason",
    ]
    diversity_cols = [
        "candidate_id",
        "diversity_grade",
        "novelty_score",
        "robustness_score",
        "selection_utility_score",
        "portfolio_event_overlap_pct",
        "portfolio_symbol_overlap_pct",
        "max_peer_event_overlap_pct",
        "axis_uniqueness_score",
    ]
    queue_cols = [
        "priority_score",
        "status_hint",
        "seed_candidate_id",
        "proposed_candidate_id",
        "mutation_axis",
        "mutation_value",
        "generation_reason",
        "failure_driver",
        "estimated_development_rows",
    ]
    grammar_cols = [
        "axis",
        "value",
        "source",
        "primitive_kind",
        "entry_known",
        "event_rows",
        "unique_events",
        "candidate_universe_uses",
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
    strict_count = int(meta_validation["strict_model_pass"].sum()) if not meta_validation.empty and "strict_model_pass" in meta_validation.columns else 0
    theoretical_count = int(meta_validation["theoretical_accept_pass"].sum()) if not meta_validation.empty and "theoretical_accept_pass" in meta_validation.columns else 0
    final_pass_count = int(final_holdout["final_pass_basic"].sum()) if not final_holdout.empty and "final_pass_basic" in final_holdout.columns else 0
    plateau_pass_count = int(clusters["plateau_pass"].sum()) if not clusters.empty and "plateau_pass" in clusters.columns else 0
    plateau_strong_count = int(clusters["plateau_strong_pass"].sum()) if not clusters.empty and "plateau_strong_pass" in clusters.columns else 0
    neighborhood_pass_count = int(plateau_neighborhoods["neighborhood_pass"].sum()) if not plateau_neighborhoods.empty and "neighborhood_pass" in plateau_neighborhoods.columns else 0
    neighborhood_strong_count = int(plateau_neighborhoods["neighborhood_strong_pass"].sum()) if not plateau_neighborhoods.empty and "neighborhood_strong_pass" in plateau_neighborhoods.columns else 0
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

Strict plateau pass clusters:

```text
plateau_pass={plateau_pass_count}
plateau_strong_pass={plateau_strong_count}
```

Multi-axis plateau neighborhoods:

```text
neighborhood_pass={neighborhood_pass_count}
neighborhood_strong_pass={neighborhood_strong_count}
```

Model gate:

```text
strict_model_pass={strict_count}
theoretical_accept_pass={theoretical_count}
final_holdout_basic_pass={final_pass_count}
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

Top plateau neighborhoods:

```text
{plateau_neighborhoods.head(25)[[c for c in neighborhood_cols if c in plateau_neighborhoods.columns]].to_string(index=False) if not plateau_neighborhoods.empty else 'none'}
```

Candidate axis summary:

```text
{axis_summary.head(40)[[c for c in axis_cols if c in axis_summary.columns]].to_string(index=False) if not axis_summary.empty else 'none'}
```

Portfolio candidates:

```text
{portfolio.head(20)[[c for c in port_cols if c in portfolio.columns]].to_string(index=False) if not portfolio.empty else 'none'}
```

Diversified portfolio candidates:

```text
{diversified_portfolio.head(30)[[c for c in diversified_port_cols if c in diversified_portfolio.columns]].to_string(index=False) if not diversified_portfolio.empty else 'none'}
```

Meta validation:

```text
{meta_validation.head(25)[[c for c in meta_cols if c in meta_validation.columns]].to_string(index=False) if not meta_validation.empty else 'none'}
```

Final holdout validation:

```text
{final_holdout.head(25)[[c for c in final_cols if c in final_holdout.columns]].to_string(index=False) if not final_holdout.empty else 'none'}
```

Portfolio meta validation:

```text
{portfolio_meta.head(10)[[c for c in portfolio_meta_cols if c in portfolio_meta.columns]].to_string(index=False) if not portfolio_meta.empty else 'none'}
```

Diversified portfolio meta validation:

```text
{diversified_portfolio_meta.head(10)[[c for c in portfolio_meta_cols if c in diversified_portfolio_meta.columns]].to_string(index=False) if not diversified_portfolio_meta.empty else 'none'}
```

Model gate diagnostics:

```text
{gate_diagnostics[[c for c in gate_cols if c in gate_diagnostics.columns]].to_string(index=False) if not gate_diagnostics.empty else 'none'}
```

Hypothesis grammar:

```text
{hypothesis_grammar.head(40)[[c for c in grammar_cols if c in hypothesis_grammar.columns]].to_string(index=False) if not hypothesis_grammar.empty else 'none'}
```

Hypothesis ledger:

```text
{hypothesis_ledger.head(30)[[c for c in ledger_cols if c in hypothesis_ledger.columns]].to_string(index=False) if not hypothesis_ledger.empty else 'none'}
```

Diversity scores:

```text
{diversity_scores.head(25)[[c for c in diversity_cols if c in diversity_scores.columns]].to_string(index=False) if not diversity_scores.empty else 'none'}
```

Self-improvement queue:

```text
{self_improvement_queue.head(30)[[c for c in queue_cols if c in self_improvement_queue.columns]].to_string(index=False) if not self_improvement_queue.empty else 'none'}
```

Theoretical model gap analysis:

```text
{theoretical_model_gap_analysis[[c for c in gap_cols if c in theoretical_model_gap_analysis.columns]].to_string(index=False) if not theoretical_model_gap_analysis.empty else 'none'}
```

Compute budget plan:

```text
{compute_budget_plan[[c for c in budget_cols if c in compute_budget_plan.columns]].to_string(index=False) if not compute_budget_plan.empty else 'none'}
```

Improvement plan:

```text
{improvement_plan.head(20).to_string(index=False) if not improvement_plan.empty else 'none'}
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


def _write_self_improvement_report(
    output_dir: Path,
    hypothesis_grammar: pd.DataFrame,
    hypothesis_ledger: pd.DataFrame,
    diversity_scores: pd.DataFrame,
    self_improvement_queue: pd.DataFrame,
    diversified_portfolio: pd.DataFrame,
    diversified_portfolio_meta: pd.DataFrame,
    compute_budget_plan: pd.DataFrame,
    theoretical_model_gap_analysis: pd.DataFrame,
    gate_diagnostics: pd.DataFrame,
    portfolio_meta: pd.DataFrame,
) -> None:
    status_counts = hypothesis_ledger["ledger_status"].value_counts().head(12).to_string() if not hypothesis_ledger.empty and "ledger_status" in hypothesis_ledger.columns else "none"
    diversity_counts = diversity_scores["diversity_grade"].value_counts().to_string() if not diversity_scores.empty and "diversity_grade" in diversity_scores.columns else "none"
    queue_counts = self_improvement_queue["status_hint"].value_counts().to_string() if not self_improvement_queue.empty and "status_hint" in self_improvement_queue.columns else "none"
    gate_view = gate_diagnostics[["scope", "check", "passed", "total", "pass_rate"]] if not gate_diagnostics.empty else pd.DataFrame()
    portfolio_view = portfolio_meta.head(5).to_string(index=False) if not portfolio_meta.empty else "none"
    diversified_portfolio_view = diversified_portfolio_meta.head(5).to_string(index=False) if not diversified_portfolio_meta.empty else "none"
    queue_cols = [
        "priority_score",
        "status_hint",
        "seed_candidate_id",
        "proposed_candidate_id",
        "mutation_axis",
        "mutation_value",
        "failure_driver",
        "estimated_development_rows",
    ]
    diversity_cols = [
        "candidate_id",
        "diversity_grade",
        "novelty_score",
        "robustness_score",
        "selection_utility_score",
        "portfolio_event_overlap_pct",
        "max_peer_event_overlap_pct",
    ]
    diversified_port_cols = [
        "candidate_id",
        "accepted",
        "marginal_score",
        "marginal_trades",
        "marginal_cost10_avg_r",
        "marginal_median_r",
        "marginal_calendar_positive_day_rate",
        "marginal_top_removed_pass",
        "selected_event_overlap_pct",
        "novelty_score",
        "robustness_score",
    ]
    gap_cols = ["capability", "current_status", "implemented_artifact", "remaining_gap", "priority"]
    budget_cols = ["stage", "rows", "cost_class", "policy", "next_action"]
    grammar_summary = (
        hypothesis_grammar.groupby("axis", dropna=False)
        .agg(values=("value", "nunique"), event_rows=("event_rows", "sum"), candidate_uses=("candidate_universe_uses", "sum"))
        .reset_index()
        .to_string(index=False)
        if not hypothesis_grammar.empty
        else "none"
    )
    text = f"""# Self-Improving Plateau Research Report

Status: controlled hypothesis generator over cached entry-known event outcomes.

This is not an unconstrained optimizer. The queue is generated from development
WFA/meta failures and diversity scores. Final holdout fields are ledgered for
audit, but they are not used as a generation target.

Grammar summary:

```text
{grammar_summary}
```

Ledger status counts:

```text
{status_counts}
```

Diversity grade counts:

```text
{diversity_counts}
```

Queue status counts:

```text
{queue_counts}
```

Model gate bottlenecks:

```text
{gate_view.to_string(index=False) if not gate_view.empty else 'none'}
```

Portfolio meta:

```text
{portfolio_view}
```

Diversified portfolio meta:

```text
{diversified_portfolio_view}
```

Top independent / complementary sleeves:

```text
{diversity_scores.head(30)[[c for c in diversity_cols if c in diversity_scores.columns]].to_string(index=False) if not diversity_scores.empty else 'none'}
```

Next guided candidate queue:

```text
{self_improvement_queue.head(40)[[c for c in queue_cols if c in self_improvement_queue.columns]].to_string(index=False) if not self_improvement_queue.empty else 'none'}
```

Diversified portfolio candidates:

```text
{diversified_portfolio.head(40)[[c for c in diversified_port_cols if c in diversified_portfolio.columns]].to_string(index=False) if not diversified_portfolio.empty else 'none'}
```

Theoretical model gap analysis:

```text
{theoretical_model_gap_analysis[[c for c in gap_cols if c in theoretical_model_gap_analysis.columns]].to_string(index=False) if not theoretical_model_gap_analysis.empty else 'none'}
```

Compute budget plan:

```text
{compute_budget_plan[[c for c in budget_cols if c in compute_budget_plan.columns]].to_string(index=False) if not compute_budget_plan.empty else 'none'}
```

How to iterate:

```text
python research_tools/short_robust_plateau_engine.py scan-plateaus --output-dir {output_dir} --candidate-queue-file {output_dir / 'self_improvement_queue.csv'} --guided-candidates-limit {DEFAULT_SELF_IMPROVEMENT_QUEUE_ROWS}
```

Research control:

```text
Do not accept a candidate because it appears in this queue.
Accept only after it survives WFA, top-removal, Monte Carlo, plateau,
calendar and final-holdout diagnostics under the normal model gates.
```
"""
    (output_dir / "self_improvement_report.md").write_text(text, encoding="utf-8")


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
        mc_iterations=args.mc_iterations,
        risk_per_trade_pct=args.risk_per_trade_pct,
        top_removal_pct=args.top_removal_pct,
        min_calendar_positive_day_rate=args.min_calendar_positive_day_rate,
        candidate_queue_file=Path(args.candidate_queue_file) if args.candidate_queue_file else None,
        guided_candidates_limit=args.guided_candidates_limit,
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
    parser.add_argument("--mc-iterations", type=int, default=DEFAULT_MC_ITERATIONS)
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT)
    parser.add_argument("--top-removal-pct", type=float, default=DEFAULT_TOP_REMOVAL_PCT)
    parser.add_argument("--min-calendar-positive-day-rate", type=float, default=DEFAULT_MIN_CALENDAR_POSITIVE_DAY_RATE)
    parser.add_argument("--candidate-queue-file", default="")
    parser.add_argument("--guided-candidates-limit", type=int, default=0)


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
    run365.set_defaults(guided_candidates_limit=DEFAULT_SELF_IMPROVEMENT_QUEUE_ROWS)

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
        mc_iterations=250,
        risk_per_trade_pct=DEFAULT_RISK_PER_TRADE_PCT,
        top_removal_pct=DEFAULT_TOP_REMOVAL_PCT,
        min_calendar_positive_day_rate=DEFAULT_MIN_CALENDAR_POSITIVE_DAY_RATE,
        candidate_queue_file="",
        guided_candidates_limit=0,
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
            mc_iterations=args.mc_iterations,
            risk_per_trade_pct=args.risk_per_trade_pct,
            top_removal_pct=args.top_removal_pct,
            min_calendar_positive_day_rate=args.min_calendar_positive_day_rate,
            candidate_queue_file=Path(args.candidate_queue_file) if args.candidate_queue_file else None,
            guided_candidates_limit=args.guided_candidates_limit,
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
