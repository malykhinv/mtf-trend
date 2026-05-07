from pathlib import Path

import pandas as pd

from cli import commands, pno_diagnostics


def test_pno_category_research_context_filters_category_aware_csvs(tmp_path: Path) -> None:
    source_diagnostics_dir = tmp_path / "source" / "pno_diagnostics"
    source_research_dir = source_diagnostics_dir / "research_context"
    source_research_dir.mkdir(parents=True)
    target_diagnostics_dir = tmp_path / "target" / "pno_diagnostics"

    pd.DataFrame(
        [
            {"context_key": "a", "pno_category_id": "cat_a", "value": 1},
            {"context_key": "b", "pno_category_id": "cat_b", "value": 2},
        ]
    ).to_csv(source_research_dir / "stage3_5_candle_outcomes.csv", index=False)
    pd.DataFrame([{"metric": "global_status", "value": "ok"}]).to_csv(
        source_research_dir / "global_status.csv",
        index=False,
    )

    commands._write_pno_category_research_context(
        source_diagnostics_dir=source_diagnostics_dir,
        target_diagnostics_dir=target_diagnostics_dir,
        category_id="cat_a",
    )

    target_research_dir = target_diagnostics_dir / "research_context"
    filtered = pd.read_csv(target_research_dir / "stage3_5_candle_outcomes.csv")
    assert filtered["context_key"].tolist() == ["a"]

    copied_global = pd.read_csv(target_research_dir / "global_status.csv")
    assert copied_global["metric"].tolist() == ["global_status"]

    filter_status = pd.read_csv(target_research_dir / "research_context_filter_status.csv")
    status_by_name = {
        Path(str(row["path"])).name: row
        for _, row in filter_status.iterrows()
    }
    assert status_by_name["stage3_5_candle_outcomes.csv"]["status"] == "filtered_by_category"
    assert int(status_by_name["stage3_5_candle_outcomes.csv"]["target_rows"]) == 1
    assert status_by_name["global_status.csv"]["status"] == "copied_unfiltered"
    assert status_by_name["global_status.csv"]["reason"] == "pno_category_id_missing"


def test_pno_stage_review_summary_marks_sampled_rejected_events(tmp_path: Path) -> None:
    rows = [
        {
            "symbol": f"SYM{idx}/USDT:USDT",
            "stage_id": pno_diagnostics.PNO_STAGE_1_PUMP,
            "timestamp_ms": idx * 60_000,
        }
        for idx in range(12)
    ]

    pno_diagnostics._export_pno_stage_reviews(
        diagnostics_dir=tmp_path,
        symbol_frames={},
        stage_rows_by_stage={pno_diagnostics.PNO_STAGE_1_PUMP: []},
        stage_rejections_by_stage={
            pno_diagnostics.PNO_STAGE_1_PUMP: {"flow_window_no_price_growth": rows}
        },
        selected_stage_ids=(pno_diagnostics.PNO_STAGE_1_PUMP,),
        render_charts=False,
    )

    summary = pd.read_csv(tmp_path / "stage_reviews" / pno_diagnostics.PNO_STAGE_1_PUMP / "summary.csv")
    rejected = summary.loc[summary["reason"] == "flow_window_no_price_growth"].iloc[0]
    assert int(rejected["count"]) == 12
    assert int(rejected["exported_events_count"]) < int(rejected["count"])
    assert bool(rejected["events_are_complete"]) is False
    assert rejected["event_export_mode"] == "review_sample"


def test_pno_stage_candle_context_carries_category_metadata() -> None:
    entry_frame = pd.DataFrame(
        {
            "timestamp": [0, 60_000, 120_000],
            "open": [10.0, 10.1, 10.2],
            "high": [10.2, 10.4, 10.8],
            "low": [9.9, 10.0, 10.1],
            "close": [10.1, 10.3, 10.7],
            "volume": [100.0, 110.0, 120.0],
            "quote_volume": [1_000.0, 1_100.0, 1_200.0],
            "number_of_trades": [10.0, 11.0, 12.0],
            "taker_buy_volume": [55.0, 60.0, 70.0],
            "taker_buy_quote_volume": [550.0, 600.0, 700.0],
        }
    )
    row = {
        "symbol": "TEST/USDT:USDT",
        "stage_id": pno_diagnostics.PNO_STAGE_3_VALID_PULLBACK,
        "timestamp_ms": 60_000,
        "active_high_timestamp_ms": 120_000,
        "pullback_low_timestamp_ms": 60_000,
        "active_high": 10.8,
        "pullback_low": 10.0,
        "level": 10.2,
        "pno_category_id": "cat_a",
        "pno_category_label": "Category A",
        "pno_profile_variant_id": "variant_a",
    }

    candle_rows, outcome_rows, status_rows = pno_diagnostics._collect_pno_stage_candle_context_rows(
        get_prepared_entry_frame=lambda symbol: entry_frame.copy(),
        stage_rows_by_stage={pno_diagnostics.PNO_STAGE_3_VALID_PULLBACK: [row]},
        stage_rejections_by_stage={},
    )

    assert candle_rows
    assert outcome_rows
    assert status_rows
    assert {candle["pno_category_id"] for candle in candle_rows} == {"cat_a"}
    assert outcome_rows[0]["pno_category_id"] == "cat_a"
    assert status_rows[0]["pno_category_id"] == "cat_a"
    assert status_rows[0]["status"] == "exported"
