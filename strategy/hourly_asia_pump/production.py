from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd

from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _build_priority_selected_events,
    _frame_to_markdown,
    _normalize_calendar_months,
    _save_priority_equity_curve_chart,
    _save_priority_monthly_returns_chart,
    _save_priority_trade_distribution_chart,
    _save_priority_trade_timeline_chart,
    _sort_summary_frame,
    _summarize_events,
)

_PRODUCTION_HOLDOUT_SPLITS: tuple[tuple[str, int], ...] = (
    ("train8_test4", 8),
    ("train9_test3", 9),
)


@dataclass(frozen=True, slots=True)
class HourlyAsiaPumpProductionVariantSpec:
    profile_id: str
    title: str
    description: str
    component_ids: tuple[str, ...]
    top_k_per_timestamp: int
    priority: int


@dataclass(frozen=True, slots=True)
class HourlyAsiaPumpProductionKpi:
    annualized_unit_pnl_pct_min: float = 1.0
    mean_return_pct_min: float = 0.02
    win_rate_min: float = 0.40
    trades_per_year_min: float = 50.0
    max_drawdown_pct_max: float = 0.30
    positive_months_min: int = 9


HOURLY_ASIA_PUMP_PRODUCTION_VARIANTS: tuple[HourlyAsiaPumpProductionVariantSpec, ...] = (
    HourlyAsiaPumpProductionVariantSpec(
        profile_id="core_a",
        title="A База",
        description="Главный производственный режим с лучшим балансом частоты, средней сделки и устойчивости на полном году.",
        component_ids=("mb5_01_shallow", "mb3_03_raw", "mb5_06_shallow", "tf7_raw", "conf00_trg65_pb50"),
        top_k_per_timestamp=99,
        priority=1,
    ),
    HourlyAsiaPumpProductionVariantSpec(
        profile_id="d_sharp",
        title="D Усиленный",
        description="Более острый подрежим с сильнее средней сделкой и ниже просадкой; используем как дополнительное подтверждение.",
        component_ids=("mb5_01_shallow", "mb3_03_trg_q75", "mb5_06_shallow", "tf7_raw", "conf00_trg65_pb50"),
        top_k_per_timestamp=2,
        priority=2,
    ),
    HourlyAsiaPumpProductionVariantSpec(
        profile_id="h_active",
        title="H Активный",
        description="Активный overlay-режим, который опирается на monster-continuation семейство в 03h и 06h.",
        component_ids=("mb5_01_shallow", "mb5_06_shallow", "mb5_03_raw", "tf7_raw", "conf00_trg65_pb50"),
        top_k_per_timestamp=99,
        priority=3,
    ),
    HourlyAsiaPumpProductionVariantSpec(
        profile_id="mo_low_dd",
        title="MO Низкая Просадка",
        description="Запасной низкорисковый режим для более спокойных состояний рынка.",
        component_ids=("mb5_01_shallow", "tf7_raw", "conf00_trg65_pb50"),
        top_k_per_timestamp=99,
        priority=4,
    ),
)


def _normalize_component_ids(raw_value: object) -> tuple[str, ...]:
    if raw_value is None or raw_value is pd.NA:
        return tuple()
    parts = [part.strip() for part in str(raw_value).split(",")]
    return tuple(part for part in parts if part)


def _load_required_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Не найден обязательный CSV: {path}")
    frame = pd.read_csv(path)
    return frame


def _load_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _combo_id_from_variant_spec(variant_spec: HourlyAsiaPumpProductionVariantSpec) -> str:
    return f"{'|'.join(variant_spec.component_ids)}__top{variant_spec.top_k_per_timestamp}"


def _find_variant_row(
    *,
    great_catalog: pd.DataFrame,
    variant_spec: HourlyAsiaPumpProductionVariantSpec,
) -> pd.Series:
    if great_catalog.empty:
        raise ValueError("Каталог static combo пуст; невозможно собрать production-профиль.")
    normalized = great_catalog.copy()
    normalized["component_tuple"] = normalized.get("component_ids", pd.Series(dtype="object")).apply(_normalize_component_ids)
    matches = normalized[
        (normalized["component_tuple"] == tuple(variant_spec.component_ids))
        & (pd.to_numeric(normalized.get("top_k_per_timestamp"), errors="coerce") == int(variant_spec.top_k_per_timestamp))
    ].copy()
    if matches.empty:
        raise ValueError(
            "Не найден production-вариант в great_combo_catalog: "
            f"{variant_spec.profile_id} ({','.join(variant_spec.component_ids)} | top_k={variant_spec.top_k_per_timestamp})"
        )
    return matches.sort_values(["combo_priority", "priority_score"], ascending=[True, False]).iloc[0]


def _build_production_variant_summary(
    *,
    great_catalog: pd.DataFrame,
    combo_events: pd.DataFrame,
    calendar_months: list[str],
    robustness_summary: pd.DataFrame,
) -> pd.DataFrame:
    robustness_index: dict[str, dict[str, Any]] = {}
    if not robustness_summary.empty and "combo_id" in robustness_summary.columns:
        robustness_index = {
            str(row["combo_id"]): row
            for row in robustness_summary.to_dict("records")
        }

    rows: list[dict[str, object]] = []
    for spec in HOURLY_ASIA_PUMP_PRODUCTION_VARIANTS:
        row: pd.Series | None = None
        try:
            row = _find_variant_row(great_catalog=great_catalog, variant_spec=spec)
        except ValueError:
            combo_id = _combo_id_from_variant_spec(spec)
            fallback_events = combo_events[combo_events["combo_id"].astype(str) == combo_id].copy()
            fallback_summary = _summarize_events(fallback_events, calendar_months=calendar_months)
            if fallback_summary is None:
                raise
            row = pd.Series(
                {
                    "combo_variant": "unranked",
                    "combo_id": combo_id,
                    "component_ids": ",".join(spec.component_ids),
                    "top_k_per_timestamp": spec.top_k_per_timestamp,
                    **fallback_summary,
                }
            )
        robustness = robustness_index.get(str(row["combo_id"]), {})
        rows.append(
            {
                "production_profile_id": spec.profile_id,
                "production_title": spec.title,
                "production_description": spec.description,
                "production_priority": spec.priority,
                "combo_variant": row.get("combo_variant"),
                "combo_id": row.get("combo_id"),
                "component_ids": row.get("component_ids"),
                "top_k_per_timestamp": row.get("top_k_per_timestamp"),
                "trades_count": row.get("trades_count"),
                "trade_days_count": row.get("trade_days_count"),
                "trades_per_year": row.get("trades_per_year"),
                "mean_return_pct": row.get("mean_return_pct"),
                "median_return_pct": row.get("median_return_pct"),
                "win_rate": row.get("win_rate"),
                "profit_factor": row.get("profit_factor"),
                "annualized_unit_pnl_pct": row.get("annualized_unit_pnl_pct", row.get("annualized_sum_return_pct")),
                "unit_pnl_sum_pct": row.get("unit_pnl_sum_pct", row.get("annual_sum_return_pct")),
                "mean_pos_trade_pct": row.get("mean_pos_trade_pct"),
                "mean_neg_trade_pct": row.get("mean_neg_trade_pct"),
                "positive_months_count": row.get("positive_months_count"),
                "non_positive_months_count": row.get("non_positive_months_count"),
                "max_drawdown_pct": row.get("max_drawdown_pct"),
                "max_concurrent_trades": row.get("max_concurrent_trades"),
                "overlapping_entries_count": row.get("overlapping_entries_count"),
                "robustness_label": robustness.get("robustness_label"),
                "local_goal_rate": robustness.get("local_goal_rate"),
                "local_mean_return_p25_pct": robustness.get("local_mean_return_p25_pct"),
                "local_dd_p75_pct": robustness.get("local_dd_p75_pct"),
                "local_annualized_median_pct": robustness.get("local_annualized_median_pct"),
            }
        )
    return pd.DataFrame(rows)


def _build_production_catalog(variant_summary: pd.DataFrame) -> pd.DataFrame:
    if variant_summary.empty:
        return pd.DataFrame()
    catalog = variant_summary.copy()
    catalog["combo_priority"] = pd.to_numeric(catalog["production_priority"], errors="coerce")
    catalog["priority_score"] = (
        pd.to_numeric(catalog["annualized_unit_pnl_pct"], errors="coerce").fillna(0.0) * 2.0
        + pd.to_numeric(catalog["mean_return_pct"], errors="coerce").fillna(0.0) * 4.0
        + pd.to_numeric(catalog["win_rate"], errors="coerce").fillna(0.0)
    )
    catalog["combo_variant"] = catalog["production_profile_id"]
    return catalog


def _build_production_holdout_summary(
    *,
    combo_events: pd.DataFrame,
    production_catalog: pd.DataFrame,
    calendar_months: list[str],
) -> pd.DataFrame:
    if combo_events.empty or production_catalog.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    scoped_events = combo_events[combo_events["combo_id"].astype(str).isin(production_catalog["combo_id"].astype(str))].copy()
    if scoped_events.empty:
        return pd.DataFrame()
    months = _normalize_calendar_months(calendar_months)
    for split_id, train_months_count in _PRODUCTION_HOLDOUT_SPLITS:
        if len(months) <= train_months_count:
            continue
        test_months = months[train_months_count:]
        test_events = scoped_events[scoped_events["month_utc"].astype(str).isin(test_months)].copy()
        selected = _build_priority_selected_events(combo_events=test_events, combo_catalog=production_catalog)
        summary = _summarize_events(selected, calendar_months=test_months)
        rows.append(
            {
                "split_id": split_id,
                "test_start_month_utc": test_months[0],
                "test_end_month_utc": test_months[-1],
                "test_months_count": len(test_months),
                **summary,
            }
        )
    return pd.DataFrame(rows)


def _evaluate_kpi_gate(
    *,
    full_year_summary: pd.DataFrame,
    holdout_summary: pd.DataFrame,
    kpi: HourlyAsiaPumpProductionKpi,
) -> pd.DataFrame:
    if full_year_summary.empty:
        raise ValueError("Production summary пуст; невозможно проверить KPI-gate.")
    row = full_year_summary.iloc[0]
    checks: list[dict[str, object]] = [
        {
            "metric": "annualized_unit_pnl_pct",
            "scope": "full_year",
            "actual": row.get("annualized_unit_pnl_pct", row.get("annualized_sum_return_pct")),
            "threshold": kpi.annualized_unit_pnl_pct_min,
            "operator": ">=",
            "passed": float(pd.to_numeric(row.get("annualized_unit_pnl_pct", row.get("annualized_sum_return_pct")), errors="coerce")) >= kpi.annualized_unit_pnl_pct_min,
        },
        {
            "metric": "mean_return_pct",
            "scope": "full_year",
            "actual": row.get("mean_return_pct"),
            "threshold": kpi.mean_return_pct_min,
            "operator": ">=",
            "passed": float(pd.to_numeric(row.get("mean_return_pct"), errors="coerce")) >= kpi.mean_return_pct_min,
        },
        {
            "metric": "win_rate",
            "scope": "full_year",
            "actual": row.get("win_rate"),
            "threshold": kpi.win_rate_min,
            "operator": ">=",
            "passed": float(pd.to_numeric(row.get("win_rate"), errors="coerce")) >= kpi.win_rate_min,
        },
        {
            "metric": "trades_per_year",
            "scope": "full_year",
            "actual": row.get("trades_per_year"),
            "threshold": kpi.trades_per_year_min,
            "operator": ">=",
            "passed": float(pd.to_numeric(row.get("trades_per_year"), errors="coerce")) >= kpi.trades_per_year_min,
        },
        {
            "metric": "max_drawdown_pct",
            "scope": "full_year",
            "actual": row.get("max_drawdown_pct"),
            "threshold": kpi.max_drawdown_pct_max,
            "operator": "<=",
            "passed": float(pd.to_numeric(row.get("max_drawdown_pct"), errors="coerce")) <= kpi.max_drawdown_pct_max,
        },
        {
            "metric": "positive_months_count",
            "scope": "full_year",
            "actual": row.get("positive_months_count"),
            "threshold": kpi.positive_months_min,
            "operator": ">=",
            "passed": float(pd.to_numeric(row.get("positive_months_count"), errors="coerce")) >= kpi.positive_months_min,
        },
    ]

    if holdout_summary.empty:
        checks.append(
            {
                "metric": "holdout_presence",
                "scope": "holdout",
                "actual": 0,
                "threshold": 1,
                "operator": ">=",
                "passed": False,
            }
        )
    else:
        for _, holdout_row in holdout_summary.iterrows():
            split_id = str(holdout_row.get("split_id"))
            checks.extend(
                [
                    {
                        "metric": "all_active_months_positive",
                        "scope": split_id,
                        "actual": bool(holdout_row.get("all_active_months_positive")),
                        "threshold": True,
                        "operator": "==",
                        "passed": bool(holdout_row.get("all_active_months_positive")),
                    },
                    {
                        "metric": "win_rate",
                        "scope": split_id,
                        "actual": holdout_row.get("win_rate"),
                        "threshold": kpi.win_rate_min,
                        "operator": ">=",
                        "passed": float(pd.to_numeric(holdout_row.get("win_rate"), errors="coerce")) >= kpi.win_rate_min,
                    },
                    {
                        "metric": "mean_return_pct",
                        "scope": split_id,
                        "actual": holdout_row.get("mean_return_pct"),
                        "threshold": kpi.mean_return_pct_min,
                        "operator": ">=",
                        "passed": float(pd.to_numeric(holdout_row.get("mean_return_pct"), errors="coerce")) >= kpi.mean_return_pct_min,
                    },
                    {
                        "metric": "max_drawdown_pct",
                        "scope": split_id,
                        "actual": holdout_row.get("max_drawdown_pct"),
                        "threshold": kpi.max_drawdown_pct_max,
                        "operator": "<=",
                        "passed": float(pd.to_numeric(holdout_row.get("max_drawdown_pct"), errors="coerce")) <= kpi.max_drawdown_pct_max,
                    },
                    {
                        "metric": "annualized_unit_pnl_pct",
                        "scope": split_id,
                        "actual": holdout_row.get("annualized_unit_pnl_pct", holdout_row.get("annualized_sum_return_pct")),
                        "threshold": 0.0,
                        "operator": ">",
                        "passed": float(pd.to_numeric(holdout_row.get("annualized_unit_pnl_pct", holdout_row.get("annualized_sum_return_pct")), errors="coerce")) > 0.0,
                    },
                ]
            )

    validation = pd.DataFrame(checks)
    validation["gate_passed"] = bool(validation["passed"].all()) if not validation.empty else False
    return validation


def _save_production_variant_chart(variant_summary: pd.DataFrame, path: Path) -> None:
    if variant_summary.empty:
        return
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    figure, axis = plt.subplots(figsize=(12, 7))
    scoped = variant_summary.copy()
    colors = {
        "strong": "#198754",
        "mixed": "#FD7E14",
        "fragile": "#DC3545",
    }
    for _, row in scoped.iterrows():
        color = colors.get(str(row.get("robustness_label", "mixed")), "#6C757D")
        x_value = float(pd.to_numeric(row.get("trades_per_year"), errors="coerce"))
        y_value = float(pd.to_numeric(row.get("mean_return_pct"), errors="coerce"))
        axis.scatter(x_value, y_value, s=170, alpha=0.92, color=color)
        axis.annotate(
            str(row.get("production_profile_id")),
            (x_value, y_value),
            xytext=(6, 6),
            textcoords="offset points",
            fontsize=10,
        )
    axis.set_title("Производственные варианты: частота против средней сделки")
    axis.set_xlabel("Сделок в год")
    axis.set_ylabel("Средняя доходность сделки")
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value * 100:.0f}%"))
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _write_production_report(
    *,
    output_dir: Path,
    context: dict[str, object],
    variant_summary: pd.DataFrame,
    production_summary: pd.DataFrame,
    production_monthly: pd.DataFrame,
    production_holdout: pd.DataFrame,
    kpi_validation: pd.DataFrame,
    chart_paths: dict[str, Path],
) -> Path:
    gate_passed = bool(kpi_validation["passed"].all()) if not kpi_validation.empty else False
    default_profiles = ",".join(variant_summary["production_profile_id"].astype(str).tolist()) if not variant_summary.empty else ""
    commission_rate = pd.to_numeric(pd.Series([context.get("commission_rate")]), errors="coerce").iloc[0]
    round_trip_taker_fee_pct = pd.to_numeric(pd.Series([context.get("round_trip_taker_fee_pct")]), errors="coerce").iloc[0]
    scope_map = {
        "full_year": "полный_год",
        "holdout": "отложенное_окно",
        "train8_test4": "train8_test4",
        "train9_test3": "train9_test3",
    }
    metric_map = {
        "annualized_unit_pnl_pct": "Годовой unit-PnL",
        "mean_return_pct": "Средняя сделка",
        "win_rate": "WR",
        "trades_per_year": "Сделок в год",
        "max_drawdown_pct": "Макс. просадка",
        "positive_months_count": "Плюсовых месяцев",
        "holdout_presence": "Наличие отложенного окна",
        "all_active_months_positive": "Все активные месяцы в плюс",
    }
    context_display = pd.DataFrame(
        [
            {
                "key": key,
                "value": json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value,
            }
            for key, value in context.items()
        ]
    )
    kpi_display = kpi_validation.copy()
    if not kpi_display.empty:
        kpi_display["scope"] = kpi_display["scope"].astype(str).map(lambda value: scope_map.get(value, value))
        kpi_display["metric"] = kpi_display["metric"].astype(str).map(lambda value: metric_map.get(value, value))
    production_holdout_display = production_holdout.copy()
    if not production_holdout_display.empty and "split_id" in production_holdout_display.columns:
        production_holdout_display["split_id"] = production_holdout_display["split_id"].astype(str).map(
            lambda value: scope_map.get(value, value)
        )
    lines = [
        "# Боевой Отчёт По Hourly Asia Pump",
        "",
        "## Краткий Вывод",
        "",
        f"- Производственный набор: `{default_profiles}`.",
        "- Правило приоритета: если один сигнал проходит несколько производственных вариантов, берём вариант с наименьшим `production_priority`.",
        f"- KPI-проверка пройдена: `{'да' if gate_passed else 'нет'}`.",
        "",
        "## Контекст Запуска",
        "",
        _frame_to_markdown(
            context_display,
            columns=("key", "value"),
            header_labels={"key": "Параметр", "value": "Значение"},
        ),
        "",
        "## Сводка По Боевому Паку",
        "",
        _frame_to_markdown(
            production_summary,
            columns=(
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "profit_factor",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "mean_pos_trade_pct",
                "mean_neg_trade_pct",
                "positive_months_count",
                "non_positive_months_count",
                "max_concurrent_trades",
                "overlapping_entries_count",
                "matched_combo_variants_count",
            ),
            header_labels={
                "trades_count": "Сделок",
                "trades_per_year": "Сделок в год",
                "mean_return_pct": "Средняя сделка",
                "median_return_pct": "Медианная сделка",
                "win_rate": "WR",
                "profit_factor": "PF",
                "annualized_unit_pnl_pct": "Годовой unit-PnL",
                "max_drawdown_pct": "Макс. просадка",
                "mean_pos_trade_pct": "Средняя прибыльная",
                "mean_neg_trade_pct": "Средняя убыточная",
                "positive_months_count": "Плюсовых месяцев",
                "non_positive_months_count": "Неплюсовых месяцев",
                "max_concurrent_trades": "Макс. одновременных сделок",
                "overlapping_entries_count": "Перекрывающихся входов",
                "matched_combo_variants_count": "Вариантов в наборе",
            },
        ),
        "",
        "## KPI-Проверка",
        "",
        _frame_to_markdown(
            kpi_display,
            columns=("scope", "metric", "actual", "operator", "threshold", "passed"),
            header_labels={
                "scope": "Область",
                "metric": "Метрика",
                "actual": "Факт",
                "operator": "Условие",
                "threshold": "Порог",
                "passed": "Пройдено",
            },
        ),
        "",
        "## Именованные Рабочие Варианты",
        "",
        _frame_to_markdown(
            variant_summary,
            columns=(
                "production_profile_id",
                "production_title",
                "combo_variant",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "max_concurrent_trades",
                "robustness_label",
            ),
            header_labels={
                "production_profile_id": "Профиль",
                "production_title": "Название",
                "combo_variant": "Вариант",
                "trades_per_year": "Сделок в год",
                "mean_return_pct": "Средняя сделка",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой unit-PnL",
                "max_drawdown_pct": "Макс. просадка",
                "positive_months_count": "Плюсовых месяцев",
                "max_concurrent_trades": "Макс. одновременных сделок",
                "robustness_label": "Устойчивость",
            },
        ),
        "",
        "## Распределение По Месяцам",
        "",
        _frame_to_markdown(
            production_monthly,
            columns=("month_utc", "total_return_pct", "trades_count", "month_positive"),
            header_labels={
                "month_utc": "Месяц",
                "total_return_pct": "Сумма доходности",
                "trades_count": "Сделок",
                "month_positive": "Месяц в плюс",
            },
        ),
        "",
        "## Поздняя Проверка На Отложенном Окне",
        "",
        _frame_to_markdown(
            production_holdout_display,
            columns=(
                "split_id",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "non_positive_months_count",
                "max_concurrent_trades",
                "overlapping_entries_count",
                "all_active_months_positive",
            ),
            header_labels={
                "split_id": "Сплит",
                "trades_count": "Сделок",
                "trades_per_year": "Сделок в год",
                "mean_return_pct": "Средняя сделка",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой unit-PnL",
                "max_drawdown_pct": "Макс. просадка",
                "positive_months_count": "Плюсовых месяцев",
                "non_positive_months_count": "Неплюсовых месяцев",
                "max_concurrent_trades": "Макс. одновременных сделок",
                "overlapping_entries_count": "Перекрывающихся входов",
                "all_active_months_positive": "Все активные месяцы в плюс",
            },
        ),
        "",
        "## Галерея Графиков",
        "",
        "### Кривая Результатов Боевого Пака",
        "",
        f"![Кривая результатов боевого пака]({chart_paths['equity_curve'].relative_to(output_dir).as_posix()})",
        "",
        "### Помесячная Доходность Боевого Пака",
        "",
        f"![Помесячная доходность боевого пака]({chart_paths['monthly_returns'].relative_to(output_dir).as_posix()})",
        "",
        "### Лента Сделок Боевого Пака",
        "",
        f"![Лента сделок боевого пака]({chart_paths['trade_timeline'].relative_to(output_dir).as_posix()})",
        "",
        "### Распределение Сделок Боевого Пака",
        "",
        f"![Распределение сделок боевого пака]({chart_paths['trade_distribution'].relative_to(output_dir).as_posix()})",
        "",
        "### Сравнение Вариантов",
        "",
        f"![Сравнение рабочих вариантов]({chart_paths['variant_scatter'].relative_to(output_dir).as_posix()})",
        "",
        "## Важные Оговорки",
        "",
        "- `annualized_unit_pnl_pct` — это суммарный unit-PnL по сделкам, а не CAGR счёта с ограничением по капиталу.",
        (
            f"- Доходности сделок уже учитывают комиссию тейкера: `да`. "
            f"Принята комиссия тейкера = `{float(commission_rate) * 100:.02f}%` на сторону, `{float(round_trip_taker_fee_pct) * 100:.02f}%` за круг."
            if pd.notna(commission_rate) and pd.notna(round_trip_taker_fee_pct)
            else "- Доходности сделок уже считаются с учётом комиссии тейкера на предыдущем этапе исследования."
        ),
        "- `max_concurrent_trades` и `overlapping_entries_count` показывают, где сделки перекрываются по времени.",
        "- Сам набор выбран на этом же полном исследовательском периоде; поздняя проверка на отложенном окне здесь только дополнительная проверка, а не независимое доказательство прибыльности в реальной торговле.",
        "- Это боевая KPI-проверка для текущего исследовательского датасета, а не гарантия будущей реальной доходности.",
    ]
    report_path = output_dir / "production_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_hourly_asia_pump_production_artifacts(
    *,
    static_combo_dir: Path | str,
    output_dir: Path | str | None = None,
    kpi: HourlyAsiaPumpProductionKpi | None = None,
) -> dict[str, Path | bool]:
    static_combo_path = Path(static_combo_dir)
    output_path = Path(output_dir) if output_dir is not None else static_combo_path
    output_path.mkdir(parents=True, exist_ok=True)
    active_kpi = kpi or HourlyAsiaPumpProductionKpi()

    great_catalog = _load_required_csv(static_combo_path / "great_combo_catalog.csv")
    combo_events = _load_required_csv(static_combo_path / "combo_events.csv")
    robustness_summary = _load_optional_csv(static_combo_path / "combo_robustness_summary.csv")
    priority_trade_chart_manifest = _load_optional_csv(static_combo_path / "priority_trade_chart_manifest.csv")
    static_combo_context = _load_optional_json(static_combo_path / "static_combo_context.json")
    calendar_months = _normalize_calendar_months(static_combo_context.get("analysis_calendar_months", []))
    if not calendar_months and "month_utc" in combo_events.columns:
        calendar_months = _normalize_calendar_months(combo_events["month_utc"].dropna().astype(str).tolist())

    variant_summary = _build_production_variant_summary(
        great_catalog=great_catalog,
        combo_events=combo_events,
        calendar_months=calendar_months,
        robustness_summary=robustness_summary,
    )
    production_catalog = _build_production_catalog(variant_summary)
    production_events = _build_priority_selected_events(
        combo_events=combo_events,
        combo_catalog=production_catalog,
    )
    production_summary_payload = _summarize_events(production_events, calendar_months=calendar_months)
    if production_summary_payload is None:
        raise ValueError("Производственный набор не выбрал ни одной сделки; невозможно собрать production-отчёт.")
    production_summary = pd.DataFrame(
        [
            {
                **production_summary_payload,
                "matched_combo_variants_count": int(variant_summary["production_profile_id"].nunique()),
            }
        ]
    )
    production_monthly = _build_monthly_returns_frame(production_events, calendar_months=calendar_months)
    production_holdout = _build_production_holdout_summary(
        combo_events=combo_events,
        production_catalog=production_catalog,
        calendar_months=calendar_months,
    )
    kpi_validation = _evaluate_kpi_gate(
        full_year_summary=production_summary,
        holdout_summary=production_holdout,
        kpi=active_kpi,
    )

    variant_summary = _sort_summary_frame(
        variant_summary,
        sort_columns=["production_priority"],
        ascending=[True],
    )
    production_monthly = _sort_summary_frame(
        production_monthly,
        sort_columns=["month_utc"],
        ascending=[True],
    )
    production_holdout = _sort_summary_frame(
        production_holdout,
        sort_columns=["split_id"],
        ascending=[True],
    )

    charts_dir = output_path / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    chart_paths = {
        "equity_curve": charts_dir / "production_equity_curve.png",
        "monthly_returns": charts_dir / "production_monthly_returns.png",
        "trade_distribution": charts_dir / "production_trade_distribution.png",
        "trade_timeline": charts_dir / "production_trade_timeline.png",
        "variant_scatter": charts_dir / "production_variant_scatter.png",
    }
    _save_priority_equity_curve_chart(production_events, chart_paths["equity_curve"])
    _save_priority_monthly_returns_chart(production_monthly, chart_paths["monthly_returns"])
    _save_priority_trade_distribution_chart(production_events, chart_paths["trade_distribution"])
    _save_priority_trade_timeline_chart(production_events, chart_paths["trade_timeline"])
    _save_production_variant_chart(variant_summary, chart_paths["variant_scatter"])

    context = {
        "static_combo_dir": str(static_combo_path),
        "commission_rate": static_combo_context.get("commission_rate"),
        "round_trip_taker_fee_pct": static_combo_context.get("round_trip_taker_fee_pct"),
        "source_trade_returns_net_of_taker_fees": static_combo_context.get("source_trade_returns_net_of_taker_fees"),
        "production_profiles": [
            {
                "profile_id": spec.profile_id,
                "title": spec.title,
                "component_ids": list(spec.component_ids),
                "top_k_per_timestamp": spec.top_k_per_timestamp,
                "priority": spec.priority,
            }
            for spec in HOURLY_ASIA_PUMP_PRODUCTION_VARIANTS
        ],
        "kpi": {
            "annualized_unit_pnl_pct_min": active_kpi.annualized_unit_pnl_pct_min,
            "mean_return_pct_min": active_kpi.mean_return_pct_min,
            "win_rate_min": active_kpi.win_rate_min,
            "trades_per_year_min": active_kpi.trades_per_year_min,
            "max_drawdown_pct_max": active_kpi.max_drawdown_pct_max,
            "positive_months_min": active_kpi.positive_months_min,
        },
        "selection_bias_warning": True,
        "production_pack_matches_top_variant": bool(
            not production_events.empty
            and not great_catalog.empty
            and int(production_summary.iloc[0]["trades_count"]) == int(pd.to_numeric(great_catalog.iloc[0]["trades_count"], errors="coerce"))
            and abs(float(production_summary.iloc[0]["mean_return_pct"]) - float(pd.to_numeric(great_catalog.iloc[0]["mean_return_pct"], errors="coerce"))) < 1e-12
        ),
        "trade_chart_manifest_rows": int(len(priority_trade_chart_manifest)),
    }
    context_path = output_path / "production_context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    variant_summary_path = output_path / "production_variant_summary.csv"
    production_catalog_path = output_path / "production_catalog.csv"
    production_events_path = output_path / "production_default_events.csv"
    production_summary_path = output_path / "production_default_summary.csv"
    production_monthly_path = output_path / "production_default_monthly.csv"
    production_holdout_path = output_path / "production_default_holdout.csv"
    kpi_validation_path = output_path / "production_kpi_validation.csv"
    variant_summary.to_csv(variant_summary_path, index=False)
    production_catalog.to_csv(production_catalog_path, index=False)
    production_events.to_csv(production_events_path, index=False)
    production_summary.to_csv(production_summary_path, index=False)
    production_monthly.to_csv(production_monthly_path, index=False)
    production_holdout.to_csv(production_holdout_path, index=False)
    kpi_validation.to_csv(kpi_validation_path, index=False)

    report_path = _write_production_report(
        output_dir=output_path,
        context=context,
        variant_summary=variant_summary,
        production_summary=production_summary,
        production_monthly=production_monthly,
        production_holdout=production_holdout,
        kpi_validation=kpi_validation,
        chart_paths=chart_paths,
    )

    gate_passed = bool(kpi_validation["passed"].all()) if not kpi_validation.empty else False
    return {
        "report": report_path,
        "production_context": context_path,
        "production_variant_summary": variant_summary_path,
        "production_catalog": production_catalog_path,
        "production_default_events": production_events_path,
        "production_default_summary": production_summary_path,
        "production_default_monthly": production_monthly_path,
        "production_default_holdout": production_holdout_path,
        "production_kpi_validation": kpi_validation_path,
        "charts_dir": charts_dir,
        "gate_passed": gate_passed,
    }
