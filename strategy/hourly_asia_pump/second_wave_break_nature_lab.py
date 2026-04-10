from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    FIVE_MINUTES_MS,
    _load_feature_db,
    _load_m1,
    _prepare_scope,
    _pressure_score,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_break_nature_lab"


def _wave_present(frame: pd.DataFrame) -> pd.Series:
    buyer = pd.to_numeric(frame.get("buyer_wave_match_score_30m"), errors="coerce")
    return buyer >= 0.80


def _close_pos(high_price: float, low_price: float, close_price: float) -> float:
    bar_range = high_price - low_price
    if bar_range <= 0.0:
        return 0.5
    return float((close_price - low_price) / bar_range)


def _zone_from_pullback(pullback_frac: float) -> str:
    if pullback_frac <= 0.10:
        return "z_0_10"
    if pullback_frac <= 0.20:
        return "z_10_20"
    if pullback_frac <= 0.35:
        return "z_20_35"
    return "z_35_plus"


def _origin_archetype(pullback_frac: float, bars_since_low: int, cluster_range_frac: float) -> str:
    if pullback_frac <= 0.10 and bars_since_low <= 1:
        return "direct_rebreak"
    if pullback_frac <= 0.20 and bars_since_low >= 2 and cluster_range_frac <= 0.18:
        return "shallow_base_break"
    if pullback_frac <= 0.35 and bars_since_low >= 1:
        return "mid_retest_break"
    return "deep_reversal_attempt"


def _analyze_break(event_row: pd.Series, cache: dict[tuple[str, str], pd.DataFrame]) -> dict[str, object] | None:
    cache_scope = str(event_row["cache_scope"])
    symbol = str(event_row["symbol"])
    m1 = _load_m1(cache_scope, symbol, cache)
    if m1.empty:
        return None

    timestamps = m1["timestamp"].astype("int64").tolist()
    opens = m1["open"].astype(float).tolist()
    highs = m1["high"].astype(float).tolist()
    lows = m1["low"].astype(float).tolist()
    closes = m1["close"].astype(float).tolist()
    volumes = m1["volume"].astype(float).tolist()

    start_ts = int(event_row["timestamp_ms"]) + FIVE_MINUTES_MS
    try:
        start_idx = timestamps.index(start_ts)
    except ValueError:
        return None

    max_wait = 5 if str(event_row["context_archetype"]) == "context_warm" else 10
    end_idx = min(len(closes), start_idx + max_wait)
    trigger_high = float(event_row["trigger_high"])
    trigger_low = float(event_row["trigger_low"])
    trigger_open = float(event_row["trigger_open"])
    trigger_close = float(event_row["trigger_close"])
    trigger_range = max(1e-12, trigger_high - trigger_low)
    trigger_body = max(1e-12, abs(trigger_close - trigger_open))
    avg_trigger_body_1m = trigger_body / 5.0
    avg_trigger_vol_1m = max(1e-12, float(event_row["trigger_volume"]) / 5.0)

    prebreak_low = trigger_high
    lowest_idx = start_idx
    new_low_count = 0
    cumulative_seller_pressure = 0.0
    first_break_idx: int | None = None

    for idx in range(start_idx, end_idx):
        if lows[idx] < prebreak_low:
            prebreak_low = lows[idx]
            lowest_idx = idx
            new_low_count += 1
        if closes[idx] < opens[idx]:
            cumulative_seller_pressure += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        if closes[idx] > trigger_high:
            first_break_idx = idx
            break

    if first_break_idx is None:
        return {
            "found_break": False,
            "start_delay_min": None,
            "pullback_frac": float(max(0.0, (trigger_high - prebreak_low) / trigger_range)),
            "pullback_zone": _zone_from_pullback(float(max(0.0, (trigger_high - prebreak_low) / trigger_range))),
            "origin_archetype": "no_break",
            "cluster_bars": None,
            "bars_since_low": None,
            "new_low_count": int(new_low_count),
            "cluster_range_frac": None,
            "break_close_pos": None,
            "break_body_ratio": None,
            "break_volume_ratio": None,
            "break_extension_frac": None,
            "break_vs_structure_high_frac": None,
            "buyer_break_score": None,
            "seller_pressure_score": float(cumulative_seller_pressure),
            "post_low_hold_share": None,
            "red_bar_share_pre_break": None,
        }

    cluster_lows = lows[start_idx:first_break_idx]
    cluster_highs = highs[start_idx:first_break_idx]
    cluster_low = min(cluster_lows) if cluster_lows else trigger_high
    cluster_high = max(cluster_highs) if cluster_highs else trigger_high
    structure_high = max(highs[lowest_idx:first_break_idx]) if first_break_idx > lowest_idx else trigger_high
    bars_since_low = first_break_idx - lowest_idx
    cluster_bars = first_break_idx - start_idx
    cluster_range_frac = max(0.0, (cluster_high - cluster_low) / trigger_range)
    pullback_frac = max(0.0, (trigger_high - cluster_low) / trigger_range)
    post_low_slice = closes[lowest_idx:first_break_idx] if first_break_idx > lowest_idx else []
    post_low_hold_share = (
        sum(1 for value in post_low_slice if value > trigger_high) / len(post_low_slice)
        if post_low_slice
        else 0.0
    )
    pre_break_slice = range(start_idx, first_break_idx)
    red_bar_share = (
        sum(1 for j in pre_break_slice if closes[j] < opens[j]) / (first_break_idx - start_idx)
        if first_break_idx > start_idx
        else 0.0
    )
    break_body = max(0.0, closes[first_break_idx] - opens[first_break_idx])
    break_body_ratio = break_body / avg_trigger_body_1m if avg_trigger_body_1m > 0.0 else 0.0
    break_volume_ratio = volumes[first_break_idx] / avg_trigger_vol_1m if avg_trigger_vol_1m > 0.0 else 0.0
    break_close_pos = _close_pos(highs[first_break_idx], lows[first_break_idx], closes[first_break_idx])
    break_extension_frac = max(0.0, (closes[first_break_idx] - trigger_high) / trigger_range)
    break_vs_structure_high_frac = max(0.0, (closes[first_break_idx] - structure_high) / trigger_range)
    buyer_break_score = _pressure_score(
        opens[first_break_idx],
        closes[first_break_idx],
        volumes[first_break_idx],
        trigger_range,
        avg_trigger_vol_1m,
    )
    seller_pressure = 0.0
    for j in range(lowest_idx, first_break_idx):
        if closes[j] < opens[j]:
            seller_pressure += _pressure_score(opens[j], closes[j], volumes[j], trigger_range, avg_trigger_vol_1m)

    return {
        "found_break": True,
        "start_delay_min": int(first_break_idx - start_idx + 1),
        "pullback_frac": float(pullback_frac),
        "pullback_zone": _zone_from_pullback(float(pullback_frac)),
        "origin_archetype": _origin_archetype(float(pullback_frac), int(bars_since_low), float(cluster_range_frac)),
        "cluster_bars": int(cluster_bars),
        "bars_since_low": int(bars_since_low),
        "new_low_count": int(new_low_count),
        "cluster_range_frac": float(cluster_range_frac),
        "break_close_pos": float(break_close_pos),
        "break_body_ratio": float(break_body_ratio),
        "break_volume_ratio": float(break_volume_ratio),
        "break_extension_frac": float(break_extension_frac),
        "break_vs_structure_high_frac": float(break_vs_structure_high_frac),
        "buyer_break_score": float(buyer_break_score),
        "seller_pressure_score": float(seller_pressure),
        "post_low_hold_share": float(post_low_hold_share),
        "red_bar_share_pre_break": float(red_bar_share),
    }


def _summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for context in ("context_warm", "context_overheated"):
        scoped = frame[frame["context_archetype"].astype(str) == context].copy()
        for wave in (True, False):
            part = scoped[scoped["wave_present"].astype(bool) == wave].copy()
            if part.empty:
                continue
            found = part[part["found_break"].astype(bool)].copy()
            rows.append(
                {
                    "context_archetype": context,
                    "wave_present": wave,
                    "count": int(len(part)),
                    "found_break_rate": float(found.shape[0] / len(part)),
                    "start_delay_min_median": float(pd.to_numeric(found["start_delay_min"], errors="coerce").median()) if not found.empty else None,
                    "pullback_frac_median": float(pd.to_numeric(found["pullback_frac"], errors="coerce").median()) if not found.empty else None,
                    "cluster_bars_median": float(pd.to_numeric(found["cluster_bars"], errors="coerce").median()) if not found.empty else None,
                    "bars_since_low_median": float(pd.to_numeric(found["bars_since_low"], errors="coerce").median()) if not found.empty else None,
                    "cluster_range_frac_median": float(pd.to_numeric(found["cluster_range_frac"], errors="coerce").median()) if not found.empty else None,
                    "new_low_count_median": float(pd.to_numeric(found["new_low_count"], errors="coerce").median()) if not found.empty else None,
                    "break_body_ratio_median": float(pd.to_numeric(found["break_body_ratio"], errors="coerce").median()) if not found.empty else None,
                    "break_volume_ratio_median": float(pd.to_numeric(found["break_volume_ratio"], errors="coerce").median()) if not found.empty else None,
                    "break_extension_frac_median": float(pd.to_numeric(found["break_extension_frac"], errors="coerce").median()) if not found.empty else None,
                    "break_vs_structure_high_frac_median": float(pd.to_numeric(found["break_vs_structure_high_frac"], errors="coerce").median()) if not found.empty else None,
                    "seller_pressure_score_median": float(pd.to_numeric(found["seller_pressure_score"], errors="coerce").median()) if not found.empty else None,
                    "buyer_break_score_median": float(pd.to_numeric(found["buyer_break_score"], errors="coerce").median()) if not found.empty else None,
                    "post_low_hold_share_median": float(pd.to_numeric(found["post_low_hold_share"], errors="coerce").median()) if not found.empty else None,
                    "red_bar_share_pre_break_median": float(pd.to_numeric(found["red_bar_share_pre_break"], errors="coerce").median()) if not found.empty else None,
                }
            )
    return pd.DataFrame(rows)


def _bucket_tables(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    found = frame[frame["found_break"].astype(bool)].copy()
    zone = (
        found.groupby(["context_archetype", "wave_present", "pullback_zone"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["context_archetype", "wave_present", "pullback_zone"])
        .reset_index(drop=True)
    )
    archetype = (
        found.groupby(["context_archetype", "wave_present", "origin_archetype"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values(["context_archetype", "wave_present", "origin_archetype"])
        .reset_index(drop=True)
    )
    return zone, archetype


def _threshold_leads(frame: pd.DataFrame) -> pd.DataFrame:
    found = frame[frame["found_break"].astype(bool)].copy()
    rows: list[dict[str, object]] = []
    for context in ("context_warm", "context_overheated"):
        scoped = found[found["context_archetype"].astype(str) == context].copy()
        if scoped.empty:
            continue
        base_rate = float(scoped["wave_present"].astype(bool).mean())
        for seller in (0.03, 0.05, 0.08, 0.12):
            for buyer in (0.20, 0.24, 0.30):
                for pull in (0.15, 0.20, 0.25, 0.35):
                    for ext in (0.05, 0.12, 0.20):
                        mask = (
                            (pd.to_numeric(scoped["seller_pressure_score"], errors="coerce") <= seller)
                            & (pd.to_numeric(scoped["buyer_break_score"], errors="coerce") >= buyer)
                            & (pd.to_numeric(scoped["pullback_frac"], errors="coerce") <= pull)
                            & (pd.to_numeric(scoped["break_extension_frac"], errors="coerce") >= ext)
                        )
                        selected = scoped[mask].copy()
                        if len(selected) < 20:
                            continue
                        hit_rate = float(selected["wave_present"].astype(bool).mean())
                        rows.append(
                            {
                                "context_archetype": context,
                                "selected_count": int(len(selected)),
                                "base_wave_present_rate": base_rate,
                                "selected_wave_present_rate": hit_rate,
                                "lift": hit_rate - base_rate,
                                "max_seller_pressure": seller,
                                "min_buyer_break_score": buyer,
                                "max_pullback_frac": pull,
                                "min_break_extension_frac": ext,
                            }
                        )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(
        ["context_archetype", "lift", "selected_wave_present_rate", "selected_count"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)


def _build_report(summary: pd.DataFrame, zones: pd.DataFrame, archetypes: pd.DataFrame, leads: pd.DataFrame) -> str:
    def _table(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "_empty_"
        return "```\n" + frame.to_string(index=False) + "\n```"

    lines: list[str] = []
    lines.append("# Nature Of Structure Break")
    lines.append("")
    lines.append("Что именно считали:")
    lines.append("- `break` = первая `1m`, которая закрылась выше high аномальной `5m`.")
    lines.append("- `cluster_low` = минимальный low после закрытия аномалии и до этой break-минуты.")
    lines.append("- `bars_since_low` = сколько минут прошло от этого low до break.")
    lines.append("- `cluster_range_frac` = ширина диапазона от low/high кластера до break, нормированная на диапазон аномалии.")
    lines.append("- `break_vs_structure_high_frac` = насколько close break-минуты вышел выше локального structure high.")
    lines.append("")
    lines.append("Главная идея: понять, был ли `слом` настоящим выходом из проторговки, или это просто случайная зелёная минута.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(_table(summary))
    lines.append("")
    lines.append("## Pullback Zones")
    lines.append("")
    lines.append(_table(zones))
    lines.append("")
    lines.append("## Origin Archetypes")
    lines.append("")
    lines.append(_table(archetypes))
    lines.append("")
    if not leads.empty:
        lines.append("## Online Threshold Leads")
        lines.append("")
        lines.append(_table(leads.groupby("context_archetype", group_keys=False).head(8)))
        lines.append("")
    lines.append("## Readout")
    lines.append("")
    lines.append("- В `warm` лучший старт второй волны обычно идёт не от нижней границы, а через `direct_rebreak` или `shallow_base_break`.")
    lines.append("- В `overheated` лучший старт чаще идёт через `mid_retest_break`, то есть после отката примерно средней глубины, а не от самого дна.")
    lines.append("- Глубокий откат `35%+` сам по себе не выглядит хорошим местом для long BOS: там волна присутствует заметно реже.")
    lines.append("- Текущий `structure break` в коде действительно грубый: он проверяет выход выше локального high, но почти не требует качественной сжатой проторговки.")
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = _load_feature_db()
    scoped = _prepare_scope(base).copy()
    scoped["wave_present"] = _wave_present(scoped)

    cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    for row in scoped.to_dict("records"):
        analyzed = _analyze_break(pd.Series(row), cache)
        if analyzed is None:
            continue
        rows.append(
            {
                "dataset": row["dataset"],
                "symbol": row["symbol"],
                "timestamp_ms": row["timestamp_ms"],
                "month_utc": row["month_utc"],
                "context_archetype": row["context_archetype"],
                "pre_accumulation_type": row["pre_accumulation_type"],
                "wave_present": bool(row["wave_present"]),
                **analyzed,
            }
        )

    detailed = pd.DataFrame(rows)
    detailed_path = OUTPUT_DIR / "break_nature_events.csv"
    detailed.to_csv(detailed_path, index=False)

    summary = _summary(detailed)
    summary_path = OUTPUT_DIR / "break_nature_summary.csv"
    summary.to_csv(summary_path, index=False)

    zones, archetypes = _bucket_tables(detailed)
    zones_path = OUTPUT_DIR / "break_nature_pullback_zones.csv"
    zones.to_csv(zones_path, index=False)
    archetypes_path = OUTPUT_DIR / "break_nature_origin_archetypes.csv"
    archetypes.to_csv(archetypes_path, index=False)

    leads = _threshold_leads(detailed)
    leads_path = OUTPUT_DIR / "break_nature_threshold_leads.csv"
    leads.to_csv(leads_path, index=False)

    report = _build_report(summary, zones, archetypes, leads)
    report_path = OUTPUT_DIR / "report.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"Saved: {report_path}")


if __name__ == "__main__":
    main()
