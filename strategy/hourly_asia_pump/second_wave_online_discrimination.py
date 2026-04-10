from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_online_signal_diagnostics"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_online_discrimination"

INPUT_PATH = INPUT_DIR / "second_wave_start_signals.csv"


def _load() -> pd.DataFrame:
    frame = pd.read_csv(INPUT_PATH, low_memory=False)
    frame["found_start_break"] = frame["found_start_break"].astype(str).str.lower().isin(["true", "1"])
    frame["wave_present"] = frame["wave_present"].astype(str).str.lower().isin(["true", "1"])
    for column in [
        "start_delay_min",
        "break_body_ratio",
        "break_volume_ratio",
        "break_close_pos",
        "break_extension_frac",
        "pre_break_pullback_frac",
        "seller_pressure_score",
        "buyer_break_score",
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "trigger_return_pct",
        "range_atr",
        "volume_mult",
        "close_to_high_frac",
    ]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _single_thresholds(frame: pd.DataFrame) -> pd.DataFrame:
    feature_specs: dict[str, tuple[str, tuple[float, ...]]] = {
        "start_delay_min": ("le", (1.0, 2.0, 3.0, 4.0)),
        "break_body_ratio": ("ge", (0.8, 1.0, 1.2, 1.5, 2.0)),
        "break_volume_ratio": ("ge", (0.8, 1.0, 1.2, 1.5, 2.0)),
        "break_close_pos": ("ge", (0.60, 0.65, 0.70, 0.75, 0.80)),
        "break_extension_frac": ("ge", (0.05, 0.10, 0.15, 0.20, 0.25)),
        "pre_break_pullback_frac": ("le", (0.10, 0.15, 0.20, 0.25, 0.35)),
        "seller_pressure_score": ("le", (0.00, 0.03, 0.05, 0.08, 0.12)),
        "buyer_break_score": ("ge", (0.15, 0.20, 0.24, 0.30, 0.40)),
        "pre_base_range_pct_60m": ("le", (0.015, 0.02, 0.03, 0.04)),
        "pre_base_drift_pct_60m": ("le", (0.008, 0.012, 0.02, 0.03)),
        "trigger_return_pct": ("ge", (0.01, 0.012, 0.015, 0.02)),
        "range_atr": ("ge", (2.0, 2.5, 3.0, 3.5, 4.0)),
        "volume_mult": ("ge", (4.0, 5.0, 6.0, 8.0, 10.0)),
        "close_to_high_frac": ("le", (0.08, 0.06, 0.04)),
    }

    rows: list[dict[str, object]] = []
    for context in sorted(frame["context_archetype"].dropna().astype(str).unique()):
        scoped = frame[frame["context_archetype"].astype(str) == context].copy()
        if scoped.empty:
            continue
        base_total = len(scoped)
        base_rate = float(scoped["wave_present"].mean())
        for dataset in ("combined", "current", "old"):
            part = scoped if dataset == "combined" else scoped[scoped["dataset"].astype(str) == dataset].copy()
            if part.empty:
                continue
            dataset_rate = float(part["wave_present"].mean())
            for feature, (op, thresholds) in feature_specs.items():
                if feature not in part.columns:
                    continue
                series = pd.to_numeric(part[feature], errors="coerce")
                for threshold in thresholds:
                    mask = series >= threshold if op == "ge" else series <= threshold
                    selected = part[mask.fillna(False)].copy()
                    if len(selected) < 15:
                        continue
                    rows.append(
                        {
                            "context_archetype": context,
                            "dataset": dataset,
                            "feature": feature,
                            "operator": op,
                            "threshold": threshold,
                            "selected_count": int(len(selected)),
                            "selected_share": float(len(selected) / len(part)),
                            "wave_present_rate": float(selected["wave_present"].mean()),
                            "dataset_base_rate": dataset_rate,
                            "lift_vs_dataset": float(selected["wave_present"].mean() - dataset_rate),
                            "combined_base_rate": base_rate,
                            "lift_vs_combined": float(selected["wave_present"].mean() - base_rate),
                            "current_count": int((selected["dataset"].astype(str) == "current").sum()),
                            "old_count": int((selected["dataset"].astype(str) == "old").sum()),
                            "base_total": base_total,
                        }
                    )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(
        ["context_archetype", "dataset", "lift_vs_dataset", "wave_present_rate", "selected_count"],
        ascending=[True, True, False, False, False],
    ).reset_index(drop=True)


def _combo_thresholds(frame: pd.DataFrame, single: pd.DataFrame) -> pd.DataFrame:
    if single.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for context in sorted(frame["context_archetype"].dropna().astype(str).unique()):
        scoped = frame[frame["context_archetype"].astype(str) == context].copy()
        if scoped.empty:
            continue
        for dataset in ("combined", "current", "old"):
            part = scoped if dataset == "combined" else scoped[scoped["dataset"].astype(str) == dataset].copy()
            if part.empty:
                continue
            dataset_rate = float(part["wave_present"].mean())
            top = (
                single[
                    (single["context_archetype"].astype(str) == context)
                    & (single["dataset"].astype(str) == dataset)
                ]
                .copy()
                .head(10)
            )
            rules = top[["feature", "operator", "threshold"]].drop_duplicates().to_dict("records")
            for left, right in combinations(rules, 2):
                left_series = pd.to_numeric(part[left["feature"]], errors="coerce")
                right_series = pd.to_numeric(part[right["feature"]], errors="coerce")
                left_mask = left_series >= left["threshold"] if left["operator"] == "ge" else left_series <= left["threshold"]
                right_mask = right_series >= right["threshold"] if right["operator"] == "ge" else right_series <= right["threshold"]
                selected = part[left_mask.fillna(False) & right_mask.fillna(False)].copy()
                if len(selected) < 12:
                    continue
                rows.append(
                    {
                        "context_archetype": context,
                        "dataset": dataset,
                        "rule_a": f"{left['feature']} {left['operator']} {left['threshold']}",
                        "rule_b": f"{right['feature']} {right['operator']} {right['threshold']}",
                        "selected_count": int(len(selected)),
                        "selected_share": float(len(selected) / len(part)),
                        "wave_present_rate": float(selected["wave_present"].mean()),
                        "dataset_base_rate": dataset_rate,
                        "lift_vs_dataset": float(selected["wave_present"].mean() - dataset_rate),
                        "current_count": int((selected["dataset"].astype(str) == "current").sum()),
                        "old_count": int((selected["dataset"].astype(str) == "old").sum()),
                    }
                )
    combo = pd.DataFrame(rows)
    if combo.empty:
        return combo
    return combo.sort_values(
        ["context_archetype", "dataset", "lift_vs_dataset", "wave_present_rate", "selected_count"],
        ascending=[True, True, False, False, False],
    ).reset_index(drop=True)


def _group_compare(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    metrics = [
        "start_delay_min",
        "break_body_ratio",
        "break_volume_ratio",
        "break_close_pos",
        "break_extension_frac",
        "pre_break_pullback_frac",
        "seller_pressure_score",
        "buyer_break_score",
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "trigger_return_pct",
        "range_atr",
        "volume_mult",
        "close_to_high_frac",
    ]
    for context in sorted(frame["context_archetype"].dropna().astype(str).unique()):
        scoped = frame[frame["context_archetype"].astype(str) == context].copy()
        present = scoped[scoped["wave_present"].astype(bool)].copy()
        absent = scoped[~scoped["wave_present"].astype(bool)].copy()
        if present.empty or absent.empty:
            continue
        for metric in metrics:
            p = pd.to_numeric(present[metric], errors="coerce")
            a = pd.to_numeric(absent[metric], errors="coerce")
            rows.append(
                {
                    "context_archetype": context,
                    "metric": metric,
                    "present_count": int(p.notna().sum()),
                    "absent_count": int(a.notna().sum()),
                    "present_median": float(p.median()) if p.notna().any() else None,
                    "absent_median": float(a.median()) if a.notna().any() else None,
                    "median_diff": float(p.median() - a.median()) if p.notna().any() and a.notna().any() else None,
                    "present_mean": float(p.mean()) if p.notna().any() else None,
                    "absent_mean": float(a.mean()) if a.notna().any() else None,
                    "mean_diff": float(p.mean() - a.mean()) if p.notna().any() and a.notna().any() else None,
                }
            )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["context_archetype", "median_diff"], ascending=[True, False]).reset_index(drop=True)


def _report(group_compare: pd.DataFrame, single: pd.DataFrame, combo: pd.DataFrame) -> str:
    def _table(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "_empty_"
        return "```\n" + frame.to_string(index=False) + "\n```"

    lines: list[str] = []
    lines.append("# Online Discrimination Of Second-Wave Start")
    lines.append("")
    lines.append("Точка сравнения:")
    lines.append("- только кейсы, где уже появился первый `start-break`, то есть первая `1m`, закрывшаяся выше high аномальной `5m`;")
    lines.append("- используются только признаки, известные к этому моменту;")
    lines.append("- цель: понять, чем `wave_present` отличается от `wave_absent`, когда вход в long ещё не считается упущенным.")
    lines.append("")
    lines.append("## Group Compare")
    lines.append("")
    for context in ("context_warm", "context_overheated"):
        part = group_compare[group_compare["context_archetype"].astype(str) == context].copy()
        if part.empty:
            continue
        lines.append(f"### {context}")
        lines.append("")
        lines.append(_table(part.head(12)))
        lines.append("")
    lines.append("## Best Single Thresholds")
    lines.append("")
    for context in ("context_warm", "context_overheated"):
        part = single[(single["context_archetype"].astype(str) == context) & (single["dataset"].astype(str) == "combined")].copy()
        if part.empty:
            continue
        lines.append(f"### {context}")
        lines.append("")
        lines.append(_table(part.head(12)))
        lines.append("")
    lines.append("## Best Two-Rule Combinations")
    lines.append("")
    for context in ("context_warm", "context_overheated"):
        part = combo[(combo["context_archetype"].astype(str) == context) & (combo["dataset"].astype(str) == "combined")].copy()
        if part.empty:
            continue
        lines.append(f"### {context}")
        lines.append("")
        lines.append(_table(part.head(10)))
        lines.append("")
    return "\n".join(lines)


def run() -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = _load()
    frame = frame[frame["found_start_break"].astype(bool)].copy().reset_index(drop=True)

    group_compare = _group_compare(frame)
    single = _single_thresholds(frame)
    combo = _combo_thresholds(frame, single)
    report = _report(group_compare, single, combo)

    paths = {
        "group_compare": OUTPUT_DIR / "group_compare.csv",
        "single_thresholds": OUTPUT_DIR / "single_thresholds.csv",
        "combo_thresholds": OUTPUT_DIR / "combo_thresholds.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    group_compare.to_csv(paths["group_compare"], index=False)
    single.to_csv(paths["single_thresholds"], index=False)
    combo.to_csv(paths["combo_thresholds"], index=False)
    paths["report"].write_text(report, encoding="utf-8")
    return paths


if __name__ == "__main__":
    output = run()
    for key, value in output.items():
        print(f"{key}={value}")
