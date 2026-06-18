from __future__ import annotations

from datetime import date

from anomaly_science.binance_vision_cache import filter_delivery_contract_symbols, is_delivery_contract_symbol


def test_delivery_contract_symbols_are_excluded_without_flag() -> None:
    symbols, skipped = filter_delivery_contract_symbols(
        symbols=["BTCUSDT", "ETHUSDT_221230", "1000PEPEUSDT", "BTCUSDT_260327"],
        start_date=date(2025, 6, 2),
        end_date=date(2026, 6, 16),
    )

    assert symbols == ["BTCUSDT", "1000PEPEUSDT"]
    assert [(item.symbol, item.reason) for item in skipped] == [
        ("ETHUSDT_221230", "delivery_contract_excluded"),
        ("BTCUSDT_260327", "delivery_contract_excluded"),
    ]
    assert is_delivery_contract_symbol("ETHUSDT_221230")
    assert not is_delivery_contract_symbol("ETHUSDT")
