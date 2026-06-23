from anomaly_science.progress import format_duration_seconds, format_progress_message


def test_format_progress_message_with_total_has_percent_eta_and_rate() -> None:
    message = format_progress_message(
        stage_name="feature_matrix",
        done=25,
        total=100,
        unit="rows",
        elapsed_seconds=10.0,
        detail="chunk=1",
    )

    assert "feature_matrix" in message
    assert "25.0%" in message
    assert "25/100 rows" in message
    assert "elapsed=10s" in message
    assert "eta=30s" in message
    assert "rate=2.50 rows/s" in message
    assert "detail=chunk=1" in message


def test_format_progress_message_without_total_keeps_done_and_elapsed() -> None:
    message = format_progress_message(
        stage_name="state",
        done=1000,
        total=None,
        unit="rows",
        elapsed_seconds=65.0,
    )

    assert message.startswith("state 1000 rows")
    assert "elapsed=1m05s" in message
    assert "eta=" not in message


def test_format_duration_seconds_is_human_readable() -> None:
    assert format_duration_seconds(4.2) == "4s"
    assert format_duration_seconds(65.0) == "1m05s"
    assert format_duration_seconds(3661.0) == "1h01m01s"
