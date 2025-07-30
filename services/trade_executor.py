from typing import Literal, cast
import threading
from config.constants import TRADE_POSITION_USDT, MIN_RISK_REWARD, FLOAT_UNDEFINED, MIN_STOP_LOSS_PERCENT, MIN_TAKE_PROFIT_PERCENT
from ccxt import binance

from domain.models.order_side import OrderSide
from domain.models.side import Side
from utils.float_utils import precision, get_pct
from utils.logger import log, logw
from services.position_tracker_service import PositionTrackerService


class TradeExecutor:
    """
    Класс для автоматического исполнения торговых сигналов (создания ордеров, стопов и тейк-профитов).
    Управляет рисками и регистрирует входы в систему.
    """
    def __init__(self, client: binance, tracker: PositionTrackerService, client_lock: threading.Lock | None = None):
        """Сохраняет клиента биржи и трекер сделок."""
        self.client = client
        self.tracker = tracker
        self._client_lock = client_lock or threading.Lock()

    def execute(self,
                symbol: str,
                side: Side,
                sl: float,
                tp: float,
                amount_usdt: float = TRADE_POSITION_USDT) -> None:
        """Создаёт позицию и выставляет стопы и тейк."""
        try:
            symbol_market = symbol
            entry = self._get_price(symbol_market)
            with self._client_lock:
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

            with self._client_lock:
                self.client.create_order(
                    symbol=symbol_market,
                    type='market',
                    side=cast(Literal["buy", "sell"], open_side.value),
                    amount=amount
                )

            with self._client_lock:
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

            with self._client_lock:
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
                atr=atr,
                amount=amount
            )

        except Exception as error:
            log(f"Исполнение ордера по {symbol} не удалось: {error}")
            raise

    def _get_price(self, symbol_market: str) -> float:
        """Возвращает последнюю цену по тикеру."""
        with self._client_lock:
            ticker = self.client.fetch_ticker(symbol_market)
        return ticker['last'] if 'last' in ticker else FLOAT_UNDEFINED

    @staticmethod
    def _validate_rr(entry: float, sl: float, tp: float, symbol: str) -> bool:
        """Проверяет, что RR и расстояния удовлетворяют минимуму."""
        if not get_pct(sl, entry) > MIN_STOP_LOSS_PERCENT:
            logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return False

        if not get_pct(tp, entry) > MIN_TAKE_PROFIT_PERCENT:
            logw(f"Entry и TP слишком близки (entry={entry}, tp={tp}).")
            return False

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if not is_defined(risk):
            log(f"{symbol}: риск равен 0. Невозможно рассчитать RR.")
            return False
        rr = reward / risk
        if rr < MIN_RISK_REWARD:
            log(f"{symbol}: RR={rr:.2f} ниже порога ({MIN_RISK_REWARD}).")
            return False
        return True

    @staticmethod
    def _validate_sl_tp(entry: float, sl: float, tp: float, side: Side, symbol: str) -> bool:
        """Проверяет, что SL и TP корректны для стороны сделки."""
        def log_illegal():
            log(f"SL/TP не соответствуют направлению сделки по {symbol}: {entry}.")
        if side.is_long and (sl >= entry or tp <= entry):
            log_illegal()
            return False
        if side.is_short and (sl <= entry or tp >= entry):
            log_illegal()
            return False
        return True
