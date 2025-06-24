from typing import Literal

from data.loader import Loader
from domain.structures import StructureDetector
from utils.logger import log


class PositionManager:
    def __init__(self,
                 symbol: str,
                 direction: Literal['long', 'short'],
                 entry: float,
                 sl: float,
                 tp: float,
                 scenario: Literal['momentum', 'rebound'],
                 client,
                 atr: float,
                 amount: float,
                 tracker):
        self.symbol = symbol
        self.direction = direction
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
        bars = self.loader.fetch_ohlcv(self.symbol, '5m', limit=50)
        detector = StructureDetector(bars, self.atr)
        swings = detector.detect_swing_points()

        current_price = bars[-1].close
        progress_rr = abs(current_price - self.entry) / abs(self.entry - self.sl)

        log(f"[PositionManager] {self.symbol} @ {current_price:.5f}, RR={progress_rr:.2f}")

        if self.direction == 'long':
            self._manage_long(progress_rr, swings)
        else:
            self._manage_short(progress_rr, swings)

    def _manage_long(self, progress_rr: float, swings):
        # Частичный выход при +3R
        if progress_rr >= 3.0 and not self.partial_exit_done:
            self._partial_close()
            self.partial_exit_done = True

        # Перенос SL под HL после 2 HL
        hl_candidates = [s for s in swings if s.kind == 'low']
        if len(hl_candidates) >= 2 and progress_rr >= 1.0:
            new_sl = hl_candidates[-1].price
            if new_sl > self.trailing_sl:
                self._update_sl(new_sl)

        # Фиксация при сломе HL
        if len(hl_candidates) >= 2 and hl_candidates[-1].price < hl_candidates[-2].price and progress_rr > 1.0:
            log(f"[PositionManager] Слом HL на 5m — выход из {self.symbol}")
            self._full_close()

    def _manage_short(self, progress_rr: float, swings):
        lh_candidates = [s for s in swings if s.kind == 'high']
        if progress_rr >= 3.0 and not self.partial_exit_done:
            self._partial_close()
            self.partial_exit_done = True

        if len(lh_candidates) >= 2 and progress_rr >= 1.0:
            new_sl = lh_candidates[-1].price
            if new_sl < self.trailing_sl:
                self._update_sl(new_sl)

        if len(lh_candidates) >= 2 and lh_candidates[-1].price > lh_candidates[-2].price and progress_rr > 1.0:
            log(f"[PositionManager] Слом LH на 5m — выход из {self.symbol}")
            self._full_close()

    def _partial_close(self):
        log(f"[PositionManager] Частичный выход из {self.symbol} на +3R")
        try:
            side = 'sell' if self.direction == 'long' else 'buy'
            market_symbol = self.symbol.replace("USDT", "/USDT")
            amount_partial = round(self.amount * 0.5, 6)
            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=side,
                amount=amount_partial
            )
        except Exception as error:
            log(f"Ошибка при частичном выходе по {self.symbol}: {error}")

    def _full_close(self):
        log(f"[PositionManager] Полный выход из позиции по {self.symbol}")
        try:
            side = 'sell' if self.direction == 'long' else 'buy'
            market_symbol = self.symbol.replace("USDT", "/USDT")
            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=side,
                amount=self.amount
            )
            self.tracker.mark_closed(self.symbol)
        except Exception as error:
            log(f"Ошибка при полном выходе по {self.symbol}: {error}")

    def _update_sl(self, new_sl: float):
        self.trailing_sl = new_sl
        log(f"[PositionManager] Обновление SL до {new_sl:.5f} по {self.symbol}")
        try:
            opposite_side = 'sell' if self.direction == 'long' else 'buy'
            market_symbol = self.symbol.replace("USDT", "/USDT")
            market = self.client.market(market_symbol)
            self.client.create_order(
                symbol=market_symbol,
                type='market',
                side=opposite_side,
                amount=self.amount,
                params={
                    'type': 'STOP_MARKET',
                    'stopPrice': round(new_sl, market['precision']['price']),
                    'closePosition': True
                }
            )
        except Exception as error:
            log(f"Ошибка при обновлении SL по {self.symbol}: {error}")