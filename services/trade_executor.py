from typing import Literal, cast
from config.constants import POSITION_USDT, MIN_RR, FLOAT_UNDEFINED, MIN_SL_PCT, MIN_TP_PCT
from ccxt import binance

from domain.models.order_side import OrderSide
from domain.models.scenario import Scenario
from domain.models.side import Side
from utils.float_utils import precision, is_defined, get_pct
from utils.logger import log, logw
from utils.str_utils import market_symbol
from services.position_tracker_service import PositionTrackerService


class TradeExecutor:
    def __init__(self, client: binance, tracker: PositionTrackerService):
        self.client = client
        self.tracker = tracker

    def execute(self,
                symbol: str,
                scenario: Scenario,
                side: Side,
                sl: float,
                tp: float,
                amount_usdt: float = POSITION_USDT):
        try:
            symbol_market = market_symbol(symbol)
            entry = self._get_price(symbol_market)
            market = self.client.market(symbol_market)

            if not self._validate_rr(entry, sl, tp, symbol):
                return

            if not self._validate_sl_tp(entry, sl, tp, side, symbol):
                return

            precision_amount = precision(market['precision']['amount']) if 'precision' in market else 6
            precision_price = precision(market['precision']['price']) if 'precision' in market else 6

            amount = round(amount_usdt / entry, precision_amount)
            open_side = OrderSide.BUY if side.is_long else OrderSide.SELL
            close_side = OrderSide.SELL if side.is_long else OrderSide.BUY

            self.client.create_order(
                symbol=symbol_market,
                type='market',
                side=cast(Literal["buy", "sell"], open_side.value),
                amount=amount
            )

            self.client.create_order(
                symbol=symbol_market,
                type='market',
                side=cast(Literal["buy", "sell"], close_side.value),
                amount=amount,
                params={
                    'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': round(tp, precision_price),
                    'closePosition': True
                }
            )

            self.client.create_order(
                symbol=symbol_market,
                type='market',
                side=cast(Literal["buy", "sell"], close_side.value),
                amount=amount,
                params={
                    'type': 'STOP_MARKET',
                    'stopPrice': round(sl, precision_price),
                    'closePosition': True
                }
            )

            rr = round(abs(tp - entry) / abs(entry - sl), 2)
            log(f"[ВХОД] {symbol} {open_side} @ {entry}\nSL: {sl}, TP: {tp}, RR: {rr}")

            atr = abs(entry - sl)
            self.tracker.add_trade(
                symbol=symbol,
                side=side,
                entry=entry,
                sl=sl,
                tp=tp,
                scenario=scenario,
                atr=atr,
                amount=amount
            )

        except Exception as error:
            log(f"Исполнение ордера по {symbol} не удалось: {error}")
            raise

    def _get_price(self, symbol_market: str) -> float:
        ticker = self.client.fetch_ticker(symbol_market)
        return ticker['last'] if 'last' in ticker else FLOAT_UNDEFINED

    @staticmethod
    def _validate_rr(entry: float, sl: float, tp: float, symbol: str) -> bool:
        if not get_pct(sl, entry) > MIN_SL_PCT:
            logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return False

        if not get_pct(tp, entry) > MIN_TP_PCT:
            logw(f"Entry и TP слишком близки (entry={entry}, tp={tp}).")
            return False

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk == 0:
            log(f"{symbol}: риск равен 0. Невозможно рассчитать RR.")
            return False
        rr = reward / risk
        if rr < MIN_RR:
            log(f"{symbol}: RR={rr:.2f} ниже порога ({MIN_RR}).")
            return False
        return True

    @staticmethod
    def _validate_sl_tp(entry: float, sl: float, tp: float, side: Side, symbol: str) -> bool:
        def log_illegal():
            log(f"SL/TP не соответствуют направлению сделки по {symbol}: {entry}.")

        if side.is_long and (sl >= entry or tp <= entry):
            log_illegal()
            return False
        if side.is_short and (sl <= entry or tp >= entry):
            log_illegal()
            return False
        return True
