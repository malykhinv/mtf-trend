import pytest

from utils.retry import run_with_retry


def test_run_with_retry_stops_immediately_for_non_retriable_error() -> None:
    calls = 0

    def _failing_call() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError('binanceusdm {"msg":"parameter \'startTime\' is invalid.","code":-1130}')

    with pytest.raises(RuntimeError, match="startTime"):
        run_with_retry(
            "ccxt_fetch_open_interest_history",
            _failing_call,
            attempts=3,
            backoff_seconds=0.0,
            retriable_exceptions=(RuntimeError,),
            endpoint="fetch_open_interest_history",
            symbol="TRX/USDT:USDT",
            should_retry=lambda exc: "-1130" not in str(exc),
        )

    assert calls == 1
