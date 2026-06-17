"""Analyze bare-HTF short/fader discovery artifacts into category candidates."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _num(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _bool(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([False] * len(frame), index=frame.index)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    return values.astype(str).str.lower().isin({"true", "1", "yes"})


def _event_key(frame: pd.DataFrame) -> pd.Series:
    symbol = frame.get("symbol", pd.Series([""] * len(frame), index=frame.index)).astype(str)
    timestamp = frame.get("timestamp_ms", frame.get("anomaly_timestamp_ms", pd.Series([""] * len(frame), index=frame.index))).astype(str)
    return symbol + "|" + timestamp


def _safe_share(series: pd.Series, n: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().sort_values(ascending=False)
    total = float(values.sum())
    if total <= 0.0:
        return float("inf")
    return float(values.head(n).sum() / total)


def _bucketize(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    prior_spike = _num(result, "prior_spike_count_72h", 0.0).fillna(0.0)
    prior_fade = _num(result, "prior_fast_fade_count_72h", 0.0).fillna(0.0)
    ltf12_ret = _num(result, "ltf12_ret")
    ltf12_taker = _num(result, "ltf12_taker_buy_quote_share")
    ltf12_red = _num(result, "ltf12_red_share")
    htf_ret = _num(result, "htf_return")
    adverse = _num(result, "adverse_up_before_short_low")
    delay = _num(result, "short_trigger_delay_candles")

    result["event_key"] = _event_key(result)
    result["prior_spike_bucket"] = np.select(
        [prior_spike.ge(10), prior_spike.ge(5), prior_spike.gt(0)],
        ["spike_ge10", "spike_5_9", "spike_1_4"],
        default="spike_0",
    )
    result["prior_fade_bucket"] = np.select(
        [prior_fade.ge(3), prior_fade.ge(1)],
        ["fade_ge3", "fade_1_2"],
        default="fade_0",
    )
    result["prior_context_bucket"] = np.select(
        [prior_spike.ge(10) | prior_fade.ge(3), prior_spike.ge(5) | prior_fade.ge(1)],
        ["prior_strong", "prior_some"],
        default="prior_none",
    )
    result["ltf12_direction_bucket"] = np.select(
        [ltf12_ret.lt(-0.005), ltf12_ret.lt(0), ltf12_ret.gt(0.005)],
        ["ltf12_down_gt_0p5", "ltf12_down", "ltf12_up_gt_0p5"],
        default="ltf12_flat_or_unknown",
    )
    result["ltf12_taker_bucket"] = np.select(
        [ltf12_taker.lt(0.45), ltf12_taker.lt(0.50), ltf12_taker.ge(0.55)],
        ["taker_weak_lt45", "taker_weak_lt50", "taker_strong_ge55"],
        default="taker_mid_or_unknown",
    )
    result["ltf12_red_bucket"] = np.select(
        [ltf12_red.ge(0.75), ltf12_red.ge(0.50)],
        ["red_share_ge75", "red_share_ge50"],
        default="red_share_low_or_unknown",
    )
    result["htf_return_bucket"] = np.select(
        [htf_ret.ge(0.05), htf_ret.ge(0.02), htf_ret.lt(0)],
        ["htf_ret_ge5", "htf_ret_2_5", "htf_ret_negative"],
        default="htf_ret_other",
    )
    result["adverse_bucket"] = np.select(
        [adverse.le(0.01), adverse.le(0.015), adverse.le(0.03)],
        ["adverse_le1", "adverse_le1p5", "adverse_le3"],
        default="adverse_dirty_or_unknown",
    )
    result["trigger_delay_bucket"] = np.select(
        [delay.le(4), delay.le(12), delay.le(36)],
        ["delay_le4", "delay_5_12", "delay_13_36"],
        default="delay_late_or_unknown",
    )
    result["decay_category_bucket"] = result.get(
        "short_decay_category_candidate",
        pd.Series(["missing_decay_category"] * len(result), index=result.index),
    ).fillna("missing_decay_category").astype(str)
    result["trigger_bucket"] = result.get(
        "short_trigger_type",
        pd.Series(["missing_trigger"] * len(result), index=result.index),
    ).fillna("missing_trigger").astype(str)
    return result


def _label_stats(frame: pd.DataFrame) -> dict[str, object]:
    short2 = _bool(frame, "short2")
    clean = _bool(frame, "clean_short2")
    long_mfe = _num(frame, "long_mfe_from_post_close")
    short_mfe = _num(frame, "short_mfe_from_post_close")
    adverse = _num(frame, "adverse_up_before_short_low")
    return {
        "events": int(frame["event_key"].nunique()) if "event_key" in frame.columns else int(len(frame)),
        "rows": int(len(frame)),
        "short2_rate": float(short2.mean()) if len(frame) else 0.0,
        "clean_short2_rate": float(clean.mean()) if len(frame) else 0.0,
        "long_up2_rate": float(long_mfe.ge(0.02).mean()) if len(frame) else 0.0,
        "median_short_mfe": float(short_mfe.median()) if short_mfe.notna().any() else np.nan,
        "median_adverse_before_low": float(adverse.median()) if adverse.notna().any() else np.nan,
        "symbols": int(frame["symbol"].nunique()) if "symbol" in frame.columns else 0,
    }


def _trade_stats(frame: pd.DataFrame, *, prefix: str) -> dict[str, object]:
    if frame.empty or "status" not in frame.columns:
        return {
            f"{prefix}_closed": 0,
            f"{prefix}_win_rate": 0.0,
            f"{prefix}_avg_net": np.nan,
            f"{prefix}_median_net": np.nan,
            f"{prefix}_sum_net": 0.0,
            f"{prefix}_top5_share": np.nan,
        }
    closed = frame.loc[frame["status"].astype(str).eq("closed")].copy()
    if closed.empty:
        return {
            f"{prefix}_closed": 0,
            f"{prefix}_win_rate": 0.0,
            f"{prefix}_avg_net": np.nan,
            f"{prefix}_median_net": np.nan,
            f"{prefix}_sum_net": 0.0,
            f"{prefix}_top5_share": np.nan,
        }
    net = pd.to_numeric(closed["net_return"], errors="coerce")
    return {
        f"{prefix}_closed": int(len(closed)),
        f"{prefix}_win_rate": float(net.gt(0).mean()),
        f"{prefix}_avg_net": float(net.mean()),
        f"{prefix}_median_net": float(net.median()),
        f"{prefix}_sum_net": float(net.sum()),
        f"{prefix}_top5_share": _safe_share(net, 5),
    }


def _score_rule(row: pd.Series, *, min_live_trades: int) -> float:
    live_closed = float(row.get("live_closed", 0) or 0)
    if live_closed <= 0:
        return -999.0
    avg_net = float(row.get("live_avg_net", 0.0) or 0.0)
    median_net = float(row.get("live_median_net", 0.0) or 0.0)
    win_rate = float(row.get("live_win_rate", 0.0) or 0.0)
    top5 = float(row.get("live_top5_share", 1.5) or 1.5)
    clean_rate = float(row.get("clean_short2_rate", 0.0) or 0.0)
    long_up2 = float(row.get("long_up2_rate", 0.0) or 0.0)
    frequency_penalty = max(0.0, float(min_live_trades) - live_closed) / max(1.0, float(min_live_trades))
    top_penalty = max(0.0, top5 - 0.85)
    return (
        avg_net * 100.0
        + median_net * 80.0
        + (win_rate - 0.40) * 2.0
        + clean_rate
        - long_up2
        - frequency_penalty
        - top_penalty
    )


def _rule_frames(frame: pd.DataFrame, raw_trades: pd.DataFrame, live_trades: pd.DataFrame, *, min_events: int, min_live_trades: int) -> pd.DataFrame:
    group_specs: list[tuple[str, list[str]]] = [
        ("trigger", ["trigger_bucket"]),
        ("decay_category", ["decay_category_bucket"]),
        ("trigger_x_decay", ["trigger_bucket", "decay_category_bucket"]),
        ("trigger_x_prior", ["trigger_bucket", "prior_context_bucket"]),
        ("trigger_x_ltf12_direction", ["trigger_bucket", "ltf12_direction_bucket"]),
        ("trigger_x_taker", ["trigger_bucket", "ltf12_taker_bucket"]),
        ("trigger_x_red", ["trigger_bucket", "ltf12_red_bucket"]),
        ("trigger_x_delay", ["trigger_bucket", "trigger_delay_bucket"]),
        ("decay_x_prior", ["decay_category_bucket", "prior_context_bucket"]),
        ("decay_x_ltf12_direction", ["decay_category_bucket", "ltf12_direction_bucket"]),
    ]
    rows: list[dict[str, object]] = []
    raw = _bucketize(raw_trades) if not raw_trades.empty else raw_trades.copy()
    live = _bucketize(live_trades) if not live_trades.empty else live_trades.copy()
    for rule_family, columns in group_specs:
        if not set(columns).issubset(frame.columns):
            continue
        for values, group in frame.groupby(columns, dropna=False):
            if not isinstance(values, tuple):
                values = (values,)
            event_keys = set(group["event_key"].astype(str))
            raw_group = raw.loc[raw["event_key"].astype(str).isin(event_keys)] if "event_key" in raw.columns else pd.DataFrame()
            live_group = live.loc[live["event_key"].astype(str).isin(event_keys)] if "event_key" in live.columns else pd.DataFrame()
            row = {
                "rule_family": rule_family,
                "rule": " & ".join(f"{column}={value}" for column, value in zip(columns, values)),
                "rule_columns": ",".join(columns),
                "rule_values": "|".join(str(value) for value in values),
                **_label_stats(group),
                **_trade_stats(raw_group, prefix="raw"),
                **_trade_stats(live_group, prefix="live"),
            }
            row["score"] = _score_rule(pd.Series(row), min_live_trades=min_live_trades)
            row["candidate_status"] = (
                "candidate"
                if int(row["events"]) >= int(min_events)
                and int(row["live_closed"]) >= int(min_live_trades)
                and float(row["live_avg_net"] or 0.0) > 0.0
                and float(row["live_median_net"] or 0.0) >= 0.0
                else "watch"
            )
            rows.append(row)
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result.sort_values(["candidate_status", "score", "live_closed"], ascending=[True, False, False], inplace=True)
    return result.reset_index(drop=True)


def analyze_short_fader_categories(
    *,
    run_dir: Path,
    output_dir: Path | None = None,
    min_events: int = 10,
    min_live_trades: int = 10,
) -> Path:
    output_dir = output_dir or (run_dir / "short_fader_category_analysis")
    output_dir.mkdir(parents=True, exist_ok=True)

    events = _read_csv(run_dir / "bare_htf_short_decay_category_events.csv")
    signals = _read_csv(run_dir / "bare_htf_short_signals.csv")
    triggers = _read_csv(run_dir / "bare_htf_short_triggers.csv")
    raw_trades = _read_csv(run_dir / "bare_htf_short_trades_raw.csv")
    live_trades = _read_csv(run_dir / "bare_htf_short_trades_live_filtered.csv")

    analysis_base = signals if not signals.empty else triggers if not triggers.empty else events
    analysis_base = _bucketize(analysis_base) if not analysis_base.empty else pd.DataFrame()
    raw_trades = _bucketize(raw_trades) if not raw_trades.empty else raw_trades
    live_trades = _bucketize(live_trades) if not live_trades.empty else live_trades

    rule_scores = _rule_frames(
        analysis_base,
        raw_trades,
        live_trades,
        min_events=int(min_events),
        min_live_trades=int(min_live_trades),
    )
    candidates = rule_scores.loc[rule_scores["candidate_status"].eq("candidate")].copy() if not rule_scores.empty else pd.DataFrame()
    watch = rule_scores.loc[rule_scores["candidate_status"].eq("watch")].head(100).copy() if not rule_scores.empty else pd.DataFrame()

    summary_rows = [
        {"metric": "run_dir", "value": str(run_dir)},
        {"metric": "analysis_rows", "value": int(len(analysis_base))},
        {"metric": "unique_events", "value": int(analysis_base["event_key"].nunique()) if "event_key" in analysis_base.columns else 0},
        {"metric": "raw_trades", "value": int(len(raw_trades))},
        {"metric": "live_trades", "value": int(len(live_trades))},
        {"metric": "rule_rows", "value": int(len(rule_scores))},
        {"metric": "candidate_rows", "value": int(len(candidates))},
        {"metric": "min_events", "value": int(min_events)},
        {"metric": "min_live_trades", "value": int(min_live_trades)},
    ]
    pd.DataFrame(summary_rows).to_csv(output_dir / "short_fader_category_analysis_summary.csv", index=False)
    analysis_base.to_csv(output_dir / "short_fader_event_category_matrix.csv", index=False)
    rule_scores.to_csv(output_dir / "short_fader_category_rule_scores.csv", index=False)
    candidates.to_csv(output_dir / "short_fader_category_candidates.csv", index=False)
    watch.to_csv(output_dir / "short_fader_category_watchlist.csv", index=False)
    return output_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--min-events", type=int, default=10)
    parser.add_argument("--min-live-trades", type=int, default=10)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = analyze_short_fader_categories(
        run_dir=Path(args.run_dir),
        output_dir=Path(args.output_dir) if args.output_dir is not None else None,
        min_events=int(args.min_events),
        min_live_trades=int(args.min_live_trades),
    )
    print(f"short/fader category analysis written -> {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
