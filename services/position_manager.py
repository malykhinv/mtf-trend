from config.constants import PROGRESS_RR_FAR, PROGRESS_RR_NEAR
from data.loader import Loader
from domain.models.order_side import OrderSide
from domain.models.scenario import Scenario
from domain.models.side import Side
from domain.models.swing_type import SwingType
from domain.models.timeframe import Timeframe
from domain.structures import StructureDetector
from utils.logger import log
from utils.str_utils import market_symbol


class PositionManager:
    def __init__(self,
                 symbol: str,
                 side: Side,
                 entry: float,
                 sl: float,
                 tp: float,
                 scenario: Scenario,
                 client,
                 atr: float,
                 amount: float,
                 tracker):
        self.symbol = symbol
        self.side = side
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.client = client
        self.tracker = tracker
        self.scenario = scenario
        self.atr = atr
        self.amount = amount
        self.loader = Loader()
        self.last_action_price = entry
        self.trailing_sl = sl
        self.partial_exit_done = False

    def manage(self):
        bars = self.loader.fetch_ohlcv(self.symbol, Timeframe.M5, limit=50)
        detector = StructureDetector(bars, self.atr)
        swings = detector.detect_swing_points()

        current_price = bars[-1].close
        progress_rr = abs(current_price - self.entry) / abs(self.entry - self.sl)

        log(f"{self.symbol} @ {current_price:.5f}, RR={progress_rr:.2f}")

        if self.side.is_long:
            self._manage_long(progress_rr, swings)
        else:
            self._manage_short(progress_rr, swings)

    def _manage_long(self, progress_rr: float, swings):
        # Частичный выход
        if progress_rr >= PROGRESS_RR_FAR and not self.partial_exit_done:
            self._partial_close()
            self.partial_exit_done = True

        # Перенос SL под HL
        hl_candidates = [s for s in swings if s.type.is_low]
        if len(hl_candidates) >= 2 and progress_rr >= PROGRESS_RR_NEAR:
            new_sl = hl_candidates[-1].price
            if new_sl > self.trailing_sl:
                self._update_sl(new_sl)

        # Фиксация при сломе HL
        if (len(hl_candidates) >= 2 and
                hl_candidates[-1].price < hl_candidates[-2].price and
                progress_rr > PROGRESS_RR_NEAR):
            log(f"Слом HL на 5m — выход из {self.symbol}")
            self._full_close()

    def _manage_short(self, progress_rr: float, swings):
        lh_candidates = [s for s in swings if s.type.is_high]
        if progress_rr >= PROGRESS_RR_FAR and not self.partial_exit_done:
            self._partial_close()
            self.partial_exit_done = True

        if len(lh_candidates) >= 2 and progress_rr >= PROGRESS_RR_NEAR:
            new_sl = lh_candidates[-1].price
            if new_sl < self.trailing_sl:
                self._update_sl(new_sl)

        if (len(lh_candidates) >= 2 and
                lh_candidates[-1].price > lh_candidates[-2].price and
                progress_rr > PROGRESS_RR_NEAR):
            log(f"Слом LH на {Timeframe.M5.value} — выход из {self.symbol}")
            self._full_close()

    def _partial_close(self):
        log(f"Частичный выход из {self.symbol} на +{PROGRESS_RR_FAR}RR")
        try:
            order_side = OrderSide.SELL if self.side.is_long else OrderSide.BUY
            symbol = market_symbol(self.symbol)
            amount_partial = round(self.amount * 0.5, 6)
            self.client.create_order(
                symbol=symbol,
                type='market',
                side=order_side.value,
                amount=amount_partial
            )
        except Exception as error:
            log(f"Ошибка при частичном выходе по {self.symbol}: {error}")
            raise

    def _full_close(self):
        log(f"Полный выход из позиции по {self.symbol}")
        try:
            order_side = OrderSide.SELL if self.side.is_long else OrderSide.BUY
            symbol = market_symbol(self.symbol)
            self.client.create_order(
                symbol=symbol,
                type='market',
                side=order_side.value,
                amount=self.amount
            )
            self.tracker.mark_closed(self.symbol)
        except Exception as error:
            log(f"Ошибка при полном выходе по {self.symbol}: {error}")
            raise

    def _update_sl(self, new_sl: float):
        self.trailing_sl = new_sl
        log(f"Обновление SL до {new_sl:.5f} по {self.symbol}")
        try:
            order_side = OrderSide.SELL if self.side.is_long else OrderSide.BUY
            symbol = market_symbol(self.symbol)
            market = self.client.market(market_symbol)
            self.client.create_order(
                symbol=symbol,
                type='market',
                side=order_side,
                amount=self.amount,
                params={
                    'type': 'STOP_MARKET',
                    'stopPrice': round(new_sl, market['precision']['price']),
                    'closePosition': True
                }
            )
        except Exception as error:
            log(f"Ошибка при обновлении SL по {self.symbol}: {error}")
            raise
