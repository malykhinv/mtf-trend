from typing import Literal
from config.settings.constants import POSITION_USDT, MIN_RR
from ccxt import binance

from utils.logger import log
from services.position_tracker_service import PositionTrackerService


class TradeExecutor:
    def __init__(self, client: binance, tracker: PositionTrackerService):
        self.client = client
        self.tracker = tracker

    def execute(self, symbol: str, direction: Literal['long', 'short'], sl: float, tp: float,
                amount_usdt: float = POSITION_USDT):
        try:
            market_symbol = self._to_market_symbol(symbol)
            entry = self._get_price(market_symbol)
            market = self.client.market(market_symbol)

            if not self._validate_rr(entry, sl, tp, symbol):
                return

            if not self._validate_sl_tp(entry, sl, tp, direction, symbol):
                return

            precision_amount = market['precision']['amount'] if 'precision' in market else 6
            precision_price = market['precision']['price'] if 'precision' in market else 6

            amount = round(amount_usdt / entry, precision_amount)
            side: Literal['buy', 'sell'] = 'buy' if direction == 'long' else 'sell'
            opposite_side: Literal['buy', 'sell'] = 'sell' if direction == 'long' else 'buy'

            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=side,
                amount=amount
            )

            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=opposite_side,
                amount=amount,
                params={
                    'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': round(tp, precision_price),
                    'closePosition': True
                }
            )

            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=opposite_side,
                amount=amount,
                params={
                    'type': 'STOP_MARKET',
                    'stopPrice': round(sl, precision_price),
                    'closePosition': True
                }
            )

            rr = round(abs(tp - entry) / abs(entry - sl), 2)
            log(f"[ВХОД] {symbol} {direction.upper()} @ {entry}\nSL: {sl}, TP: {tp}, RR: {rr}")

            scenario = 'rebound' if rr <= 4 else 'momentum'
            atr = abs(entry - sl)
            self.tracker.add_trade(
                symbol=symbol,
                direction=direction,
                entry=entry,
                sl=sl,
                tp=tp,
                scenario=scenario,
                atr=atr,
                amount=amount
            )

        except Exception as error:
            log(f"Исполнение ордера по {symbol} не удалось: {error}")

    @staticmethod
    def _to_market_symbol(symbol: str) -> str:
        return symbol.replace("USDT", "/USDT")

    def _get_price(self, market_symbol: str) -> float:
        ticker = self.client.fetch_ticker(market_symbol)
        return ticker['last'] if 'last' in ticker else 0.0

    @staticmethod
    def _validate_rr(entry: float, sl: float, tp: float, symbol: str) -> bool:
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
    def _validate_sl_tp(entry: float, sl: float, tp: float, direction: str, symbol: str) -> bool:
        if direction == 'long' and (sl >= entry or tp <= entry):
            log(f"SL/TP не соответствуют направлению сделки по {symbol}.")
            return False
        if direction == 'short' and (sl <= entry or tp >= entry):
            log(f"SL/TP не соответствуют направлению сделки по {symbol}.")
            return False
        return True
