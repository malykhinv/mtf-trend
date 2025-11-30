from collections import defaultdict
from typing import Optional

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.setup import Buy
from crypto_screener.domain.models.trade_result import TradeResult
from crypto_screener.utils.logger import log


# region Private.
def _get_profit_pct(
        entry_price: float,
        target_price: float,
        weight: float = 1.0
) -> float:
    return weight * 100 * (target_price - entry_price) / entry_price


# endregion

def evaluate_buy(
        setup: Buy,
        future_bars: list[Bar]
) -> Optional[tuple[TradeResult, float]]:
    entry_price = setup.entry_price
    stop_loss_price = setup.stop_loss_price
    take_profit_price = setup.take_profit_price
    partial_close_price = setup.partial_close_price
    breakeven_price = setup.breakeven_price

    has_partial_close = False

    for bar in future_bars:
        if not has_partial_close:
            if bar.low <= stop_loss_price:
                return TradeResult.SL, _get_profit_pct(entry_price, stop_loss_price)

            if partial_close_price is not None and bar.high >= partial_close_price:
                has_partial_close = True
                if bar.high >= take_profit_price:
                    profit_pct = (
                            _get_profit_pct(entry_price, partial_close_price, 0.5)
                            + _get_profit_pct(entry_price, take_profit_price, 0.5)
                    )
                    return TradeResult.PC_TP, profit_pct
                continue

            if bar.high >= take_profit_price:
                return TradeResult.TP, _get_profit_pct(entry_price, take_profit_price)
        else:
            if breakeven_price is not None and bar.low <= breakeven_price:
                profit_pct = _get_profit_pct(entry_price, partial_close_price or entry_price, 0.5)
                return TradeResult.PC_BE, profit_pct

            if bar.high >= take_profit_price:
                profit_pct = (
                        _get_profit_pct(entry_price, partial_close_price or entry_price, 0.5)
                        + _get_profit_pct(entry_price, take_profit_price, 0.5)
                )
                return TradeResult.PC_TP, profit_pct

    return None


def log_test_summary(
        trade_outcomes: defaultdict[TradeResult, int],
        trade_results: list[float]
) -> None:
    total_trades = sum(trade_outcomes.values())

    log.i("Результаты теста:")
    if not total_trades:
        log.i("Торговые сетапы не найдены.")
    else:
        profitable_trades = len([result for result in trade_results if result > 0])
        losing_trades = len([result for result in trade_results if result < 0])
        win_rate = 100 * profitable_trades / total_trades if total_trades else 0
        total_profit_pct = sum(trade_results)

        log.i(f"Винрейт: {win_rate:.2f}% ({profitable_trades}/{total_trades})")
        log.i(f"Прибыльные сделки: {profitable_trades}")
        log.i(f"Проигрышные сделки: {losing_trades}")
        log.i(f"Итог: {total_profit_pct:+.2f}%")
