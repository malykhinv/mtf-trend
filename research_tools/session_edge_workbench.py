"""Build session-aware edge workbench artifacts from existing research outputs.

This is a lightweight postprocess. It does not rerun discovery/backtests.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


def _safe_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def _top_remove_pct_to_negative(values: pd.Series) -> tuple[float, int, int]:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    total = float(vals.sum())
    winners = vals[vals > 0].sort_values(ascending=False).to_numpy()
    if len(winners) == 0 or total <= 0:
        return 0.0, 0, int(len(winners))

    remaining = total
    for idx, winner in enumerate(winners, start=1):
        remaining -= float(winner)
        if remaining <= 0:
            return float(idx / len(winners)), int(idx), int(len(winners))
    return 1.0, int(len(winners)), int(len(winners))


def _summarize_replay(df: pd.DataFrame) -> pd.Series:
    net_r = pd.to_numeric(df["net_r"], errors="coerce").dropna()
    top_pct, removed, winners = _top_remove_pct_to_negative(net_r)
    dates = pd.to_datetime(df["seed_close_utc"], errors="coerce").dt.date
    return pd.Series(
        {
            "replay_trades": int(len(df)),
            "replay_symbols": int(df["symbol"].nunique()),
            "replay_days": int(dates.nunique()),
            "replay_avg_r": float(net_r.mean()) if len(net_r) else np.nan,
            "replay_median_r": float(net_r.median()) if len(net_r) else np.nan,
            "replay_sum_r": float(net_r.sum()) if len(net_r) else np.nan,
            "replay_win_rate": float((net_r > 0).mean()) if len(net_r) else np.nan,
            "top_remove_winner_pct_to_negative": top_pct,
            "removed_winners_to_negative": removed,
            "winning_trades": winners,
            "replay_p10_r": float(net_r.quantile(0.10)) if len(net_r) else np.nan,
            "replay_p90_r": float(net_r.quantile(0.90)) if len(net_r) else np.nan,
            "replay_initial_risk_pct_median": float(df["initial_risk_pct"].median()),
        }
    )


def _feature_catalog() -> pd.DataFrame:
    rows = [
        ("session_bucket", "session", "decision_time", "UTC session bucket used for stratification."),
        ("close_ret_10m", "price_acceptance", "known_after_10m", "10m close acceptance after seed."),
        ("close_ret_15m", "price_acceptance", "known_after_15m", "15m close acceptance after seed."),
        ("high_ret_10m", "price_acceptance", "known_after_10m", "10m high extension after seed."),
        ("high_ret_15m", "price_acceptance", "known_after_15m", "15m high extension after seed."),
        ("wick_ret_10m", "rejection", "known_after_10m", "10m upper wick/rejection component."),
        ("wick_ret_15m", "rejection", "known_after_15m", "15m upper wick/rejection component."),
        ("close15_to_high15_ratio", "acceptance", "known_after_15m", "Close position vs 15m high."),
        ("pre60_return_pct", "pre_context", "decision_time", "Pre-seed 60m return context."),
        ("pre60_range_pct", "pre_context", "decision_time", "Pre-seed 60m range/stretched context."),
        ("pre60_min_path_pct", "pre_context", "decision_time", "Pre-seed downside path."),
        ("early_return_pct", "seed_price", "decision_time", "Seed candle return."),
        ("early_high_return_pct", "seed_price", "decision_time", "Seed candle high return."),
        ("early_quote_ratio_24h_scaled", "flow", "decision_time", "Quote-volume anomaly vs own baseline."),
        ("early_trade_ratio_24h_scaled", "flow", "decision_time", "Trade-count anomaly vs own baseline."),
        ("early_taker_buy_quote_share", "flow", "decision_time", "Seed taker-buy quote share, if available."),
        ("m1_quote_top1_share", "microstructure", "decision_time", "Top 1m quote-volume concentration."),
        ("m1_trade_top1_share", "microstructure", "decision_time", "Top 1m trade-count concentration."),
        ("m1_last2_quote_share", "microstructure", "decision_time", "Last 2m quote-volume share."),
        ("m1_last2_trade_share", "microstructure", "decision_time", "Last 2m trade-count share."),
        ("m1_taker_buy_quote_share", "microstructure", "decision_time", "1m taker-buy quote share."),
        ("m1_sustain_mid", "microstructure", "decision_time", "Mid-level 1m sustain flag."),
        ("m1_sustain_strict", "microstructure", "decision_time", "Strict 1m sustain flag."),
        ("large_runner_nature_selected", "existing_policy", "decision_time", "Existing nature policy selection flag."),
        ("oi_status", "oi", "tail_only_context", "OI availability/status; not full-period training-safe here."),
        ("oi_change_early_pct", "oi", "tail_only_context", "Early OI change; late-cache only."),
        ("oi_change_pre60_pct", "oi", "tail_only_context", "Pre60 OI change; late-cache only."),
        ("outcome_class", "label", "evaluation_only", "fast fader / runner / static / other label."),
        ("future60_min_path_pct", "label", "evaluation_only", "Future 60m minimum path."),
        ("runner_high10_next60", "label", "evaluation_only", "Future runner10 label."),
        ("base_touch_offset_min", "label", "evaluation_only", "Future base-touch timing."),
        ("net_r", "execution_result", "execution_result", "Replay trade R result."),
        ("top_remove_winner_pct_to_negative", "robustness", "execution_result", "Share of winning trades removed before sum <= 0."),
    ]
    return pd.DataFrame(rows, columns=["field", "category", "availability", "notes"])


def _add_setup_buckets(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close15 = pd.to_numeric(out["close_ret_15m"], errors="coerce")
    pre60_range = pd.to_numeric(out["pre60_range_pct"], errors="coerce")
    out["edge_bucket"] = np.select(
        [
            (close15 <= 0.0339) & (pre60_range >= 0.10),
            (close15 > 0.0339) & (close15 <= 0.081),
            close15 >= 0.12,
        ],
        ["fader_candidate", "static_no_trade", "runner_no_short"],
        default="mixed_unclassified",
    )
    out["fader_classifier_core"] = out["edge_bucket"].eq("fader_candidate")
    out["static_zone_core"] = out["edge_bucket"].eq("static_no_trade")
    out["runner_veto_core"] = out["edge_bucket"].eq("runner_no_short")
    out["non_us"] = ~out["session_bucket"].eq("us_only")
    out["europe_or_off"] = out["session_bucket"].isin(["europe_only", "off_session"])
    return out


def _make_setup_master(setups: pd.DataFrame) -> pd.DataFrame:
    wanted = [
        "setup_key",
        "symbol",
        "seed_close_ms",
        "seed_close_utc",
        "date",
        "month",
        "session_bucket",
        "edge_bucket",
        "fader_classifier_core",
        "static_zone_core",
        "runner_veto_core",
        "outcome_class",
        "is_fast_fader",
        "is_runner",
        "is_static",
        "is_static_or_other",
        "close_ret_10m",
        "close_ret_15m",
        "high_ret_10m",
        "high_ret_15m",
        "wick_ret_10m",
        "wick_ret_15m",
        "close15_to_high15_ratio",
        "wick15_share",
        "pre60_return_pct",
        "pre60_min_path_pct",
        "pre60_range_pct",
        "early_return_pct",
        "early_high_return_pct",
        "early_quote_ratio_24h_scaled",
        "early_trade_ratio_24h_scaled",
        "early_taker_buy_quote_share",
        "m1_quote_top1_share",
        "m1_trade_top1_share",
        "m1_last2_quote_share",
        "m1_last2_trade_share",
        "m1_taker_buy_quote_share",
        "m1_sustain_mid",
        "m1_sustain_strict",
        "large_runner_nature_selected",
        "oi_status",
        "oi_change_early_pct",
        "oi_change_pre60_pct",
        "future60_min_path_pct",
        "future60_high_return_pct",
        "base_touch_offset_min",
        "runner_high10_hit_offset_min",
        "upper_half_close_share_60",
        "future60_close_vs_base_pos",
    ]
    cols = [c for c in wanted if c in setups.columns]
    return setups[cols].copy()


def _selector_defs() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    return {
        "all_close15_le_339": lambda d: d["close_ret_15m"] <= 0.0339,
        "all_close15_le_339_pre60range_ge10": lambda d: (d["close_ret_15m"] <= 0.0339)
        & (d["pre60_range_pct"] >= 0.10),
        "all_close15_le_339_pre60ret_ge3": lambda d: (d["close_ret_15m"] <= 0.0339)
        & (d["pre60_return_pct"] >= 0.03),
        "non_us_close15_le_339_pre60range_ge10": lambda d: (d["close_ret_15m"] <= 0.0339)
        & (d["pre60_range_pct"] >= 0.10)
        & (~d["session_bucket"].eq("us_only")),
        "europe_or_off_close15_le_339_pre60range_ge10": lambda d: (d["close_ret_15m"] <= 0.0339)
        & (d["pre60_range_pct"] >= 0.10)
        & (d["session_bucket"].isin(["europe_only", "off_session"])),
        "asia_only_close15_le_339_pre60range_ge10": lambda d: (d["close_ret_15m"] <= 0.0339)
        & (d["pre60_range_pct"] >= 0.10)
        & (d["session_bucket"].eq("asia_only")),
        "us_only_close15_le_339_pre60range_ge10": lambda d: (d["close_ret_15m"] <= 0.0339)
        & (d["pre60_range_pct"] >= 0.10)
        & (d["session_bucket"].eq("us_only")),
        "static_band_all_339_810": lambda d: (d["close_ret_15m"] > 0.0339)
        & (d["close_ret_15m"] <= 0.081),
        "runner_veto_close15_ge12": lambda d: d["close_ret_15m"] >= 0.12,
    }


def _build_replay_scorecard(setups: pd.DataFrame, replay: pd.DataFrame) -> pd.DataFrame:
    join_cols = [
        "symbol",
        "seed_close_ms",
        "session_bucket",
        "close_ret_15m",
        "pre60_range_pct",
        "pre60_return_pct",
        "edge_bucket",
        "outcome_class",
    ]
    merged = replay.merge(setups[join_cols], on=["symbol", "seed_close_ms"], how="inner", validate="many_to_one")
    closed = merged[merged["status"].eq("closed")].copy()

    rows: list[pd.Series] = []
    for rule_name, selector in _selector_defs().items():
        rule_df = closed[selector(closed)]
        if rule_df.empty:
            continue
        summary = _summarize_replay(rule_df)
        summary["selector"] = rule_name
        summary["management"] = "ALL"
        summary["candidate_desc"] = "ALL"
        summary["stop_model"] = "ALL"
        summary["exit_model"] = "ALL"
        rows.append(summary)

        for keys, group in rule_df.groupby(["candidate_desc", "stop_model", "exit_model"], dropna=False):
            if len(group) < 25:
                continue
            summary = _summarize_replay(group)
            summary["selector"] = rule_name
            summary["candidate_desc"] = str(keys[0])
            summary["stop_model"] = str(keys[1])
            summary["exit_model"] = str(keys[2])
            summary["management"] = " | ".join(map(str, keys))
            rows.append(summary)

    scorecard = pd.DataFrame(rows)
    if scorecard.empty:
        return scorecard
    scorecard["passes_replay_robustness"] = (
        (scorecard["replay_trades"] >= 100)
        & (scorecard["replay_avg_r"] > 0)
        & (scorecard["replay_median_r"] > 0)
        & (scorecard["top_remove_winner_pct_to_negative"] >= 0.50)
    )
    scorecard["watchlist_positive_but_top_dependent"] = (
        (scorecard["replay_trades"] >= 50)
        & (scorecard["replay_avg_r"] > 0)
        & (scorecard["top_remove_winner_pct_to_negative"] < 0.50)
    )
    sort_cols = ["passes_replay_robustness", "top_remove_winner_pct_to_negative", "replay_avg_r", "replay_trades"]
    return scorecard.sort_values(sort_cols, ascending=[False, False, False, False])


def _write_readme(out_dir: Path, setup_rows: int, score_rows: int, robust_passes: int) -> None:
    text = f"""# Large Runner Session Edge Workbench

This directory now contains a table-first research workbench built from existing
365d artifacts. It does not rerun discovery or change trading code.

## Files

```text
large_runner_edge_workbench_setups.csv
large_runner_edge_workbench_feature_catalog.csv
large_runner_edge_workbench_replay_scorecard.csv
large_runner_edge_workbench_readme.md
```

## Setup Table

`large_runner_edge_workbench_setups.csv` is the master setup-level table:

```text
rows: {setup_rows}
unit: one labeled setup
key: symbol + seed_close_ms / setup_key
```

It contains:

- session bucket;
- fader/static/runner outcome class;
- decision-time price/flow/microstructure features;
- OI fields marked as tail-only context;
- evaluation-only path fields for audit, not training;
- core buckets: `fader_candidate`, `static_no_trade`, `runner_no_short`.

## Replay Scorecard

`large_runner_edge_workbench_replay_scorecard.csv` joins the core selectors into
the existing local-high structural short replay and reports:

- R expectancy;
- median R;
- winrate;
- symbols/days/trade count;
- top-winning-trade removal to negative;
- promotion/watchlist flags.

Rows in scorecard: {score_rows}

Strict replay robustness passes:

```text
{robust_passes}
```

## Promotion Bar

A short rule should not be promoted unless it satisfies at least:

```text
replay_trades >= 100
replay_avg_r > 0
replay_median_r > 0
top_remove_winner_pct_to_negative >= 0.50
enough symbols/days
no future/evaluation-only fields in selector
```

Current known result: the fader classifier is rolling-stable as an outcome
classifier, but current short execution policies do not pass this replay
robustness bar.
"""
    (out_dir / "large_runner_edge_workbench_readme.md").write_text(text, encoding="utf-8")


def build(base_dir: Path) -> None:
    setup_path = base_dir / "large_runner_session_outcome_research_table.csv"
    replay_path = base_dir / "large_runner_local_high_structural_exit_replay_trades.csv"
    if not setup_path.exists():
        raise FileNotFoundError(setup_path)
    if not replay_path.exists():
        raise FileNotFoundError(replay_path)

    setups = pd.read_csv(setup_path)
    setups = _add_setup_buckets(setups)
    setup_master = _make_setup_master(setups)
    setup_master.to_csv(base_dir / "large_runner_edge_workbench_setups.csv", index=False)

    catalog = _feature_catalog()
    catalog.to_csv(base_dir / "large_runner_edge_workbench_feature_catalog.csv", index=False)

    replay = pd.read_csv(replay_path)
    scorecard = _build_replay_scorecard(setups, replay)
    scorecard.to_csv(base_dir / "large_runner_edge_workbench_replay_scorecard.csv", index=False)

    robust_passes = int(scorecard.get("passes_replay_robustness", pd.Series(dtype=bool)).sum())
    _write_readme(base_dir, len(setup_master), len(scorecard), robust_passes)

    print(f"setup_master_rows={len(setup_master)}")
    print(f"feature_catalog_rows={len(catalog)}")
    print(f"replay_scorecard_rows={len(scorecard)}")
    print(f"passes_replay_robustness={robust_passes}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-dir",
        default=".output/results/large_runner_discovery_365d",
        help="Directory containing existing large-runner research artifacts.",
    )
    args = parser.parse_args()
    build(Path(args.base_dir))


if __name__ == "__main__":
    main()
