import json

from research_tools.anomaly_live2.runner import _compact_live2_heartbeat_event_data


def test_compact_heartbeat_event_omits_full_nested_status_blobs() -> None:
    huge_symbols = [f"SYM{i}/USDT:USDT" for i in range(1000)]
    market_data_status = {
        "market_data_ready_for_entries": True,
        "reason": "ready",
        "shards_connected": 4,
        "shards_total": 4,
        "aggtrade_status_counts": {"ok_active": 900},
        "huge_symbol_list": huge_symbols,
    }
    decision_status = {
        "engines": {
            "15000": {
                "status": "running",
                "total_decisions": 123,
                "total_rejected": 100,
                "total_deadline_missed": 2,
                "total_deadline_expired_backlog": 1,
                "total_flow_freshness_reject": 3,
                "total_data_dependency_not_ready": 4,
                "selected_count": 5,
                "signal_engine": {
                    "total_selected": 6,
                    "large_reject_examples": huge_symbols,
                },
            }
        },
        "full_engine_dump": {"symbols": huge_symbols},
    }
    execution_status = {
        "status": "ready",
        "execution_engine": {
            "open_protected_positions": 1,
            "total_orders_submitted": 2,
            "total_positions_protected": 1,
            "total_integrity_errors": 0,
            "protected_positions": huge_symbols,
        },
    }
    runtime_gate_status = {
        "status": "ready",
        "reason": "all_gates_ready",
        "readiness": {"new_entries_allowed": True},
        "position_supervisor_status": {
            "status": "ready",
            "total_integrity_errors": 0,
            "total_final_closes": 1,
            "total_tp1_closes": 1,
            "protected_positions": huge_symbols,
        },
    }

    payload = _compact_live2_heartbeat_event_data(
        symbols_total=600,
        ticker_status_counts={"ok": 600},
        aggtrade_status_counts={"ok_active": 600},
        startup_aggtrade_status_counts={"ok_active": 600},
        live_aggtrade_status_counts={"ok_active": 600},
        candle_coverage_counts={"live_ready": 600},
        market_data_status=market_data_status,
        decision_status=decision_status,
        deadline_cycle={"cycle_status": "ok"},
        decision_cycle_elapsed_ms=10,
        hot_path_elapsed_ms=11,
        heartbeat_elapsed_ms=12,
        main_loop_gap_ms=13,
        runtime_gate_status=runtime_gate_status,
        new_entries_allowed=True,
        execution_status=execution_status,
        user_data_stream_status={"ready": True, "status": "ready", "events_by_type": {"ORDER_TRADE_UPDATE": 2}},
        position_supervisor_cycle={"cycle_status": "ok"},
        session_top={"top": []},
        top_growth_audit={"status": "completed"},
        artifact_writer_status={
            "ready": True,
            "queue_size": 0,
            "dropped_count": 0,
            "dropped_by_kind": {},
            "output_file_budget": {"live2_events.csv": {"budget_reached": False}},
        },
    )

    encoded = json.dumps(payload, ensure_ascii=False)

    assert "market_data_status" not in payload
    assert "decision_status" not in payload
    assert "execution_status" in payload
    assert '"protected_positions": [' not in encoded
    assert "SYM999/USDT:USDT" not in encoded
    assert len(encoded) < 6000
    assert payload["decision_timeframes"]["15000"]["total_decisions"] == 123
