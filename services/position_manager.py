from domain.structures import StructureDetector
from utils.logger import log
from utils.str_utils import market_symbol

class PositionManager:
    """
    Управление открытыми позициями: доливка, частичный и полный выход, сопровождение стопов.
    """
    def __init__(self,
                 trade_id: int,
                 symbol: str,
                 side: str,
                 entry: float,
                 sl: float,
                 tp: float,
                 client,
                 atr: float,
                 amount: float,
                 partial_exit_done: bool,
                 tracker):
        self.trade_id = trade_id
        self.symbol = symbol
        self.side = side
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.client = client
        self.atr = atr
        self.amount = amount
        self.partial_exit_done = partial_exit_done
        self.tracker = tracker

    def manage(self) -> None:
        """
        Автоматически сопровождает открытую позицию: trailing stop, частичный и полный выход, логгирование RR-прогресса.
        """
        bars = self.tracker.loader.fetch_ohlcvi(self.symbol, '5m', limit=50)  # structure_tf
        detector = StructureDetector()
        swings = detector.detect_swing_points(bars)
        current_price = bars[-1].close
        rr_progress = abs(current_price - self.entry) / abs(self.entry - self.sl)
        log(f"{self.symbol} @ {current_price:.5f}, RR прогресс: {rr_progress:.2f}")
        # Если цель достигнута (swing high), частично выйти
        if not self.partial_exit_done and current_price >= self.tp:
            self._partial_exit()
        # Trailing SL — подтягиваем по последним swing low
        low_points = [s for s in swings if s.type.is_low]
        if low_points:
            new_sl = low_points[-1].price
            if new_sl > self.sl:
                self._update_sl(new_sl)
        # Слом структуры (новый low ниже предыдущего) — полный выход
        if len(low_points) >= 2 and low_points[-1].price < low_points[-2].price:
            log(f"Слом структуры на {self.symbol}, принудительный выход")
            self._full_exit()

    def _partial_exit(self) -> None:
        """
        Совершает частичный выход (продаёт/закрывает половину объёма), отмечает в базе.
        """
        log(f"Частичный выход (0.5) по {self.symbol}")
        try:
            side = 'sell' if self.side == 'long' else 'buy'
            amount_partial = round(self.amount * 0.5, 6)
            self.client.create_order(
                symbol=market_symbol(self.symbol),
                type='market',
                side=side,
                amount=amount_partial
            )
            self.tracker.mark_partial_exit(self.trade_id)
        except Exception as error:
            log(f"Ошибка при частичном выходе: {error}")
            raise

    def _full_exit(self) -> None:
        """
        Совершает полный выход (закрывает всю позицию), отмечает в системе.
        """
        log(f"Полный выход из позиции {self.symbol}")
        try:
            side = 'sell' if self.side == 'long' else 'buy'
            self.client.create_order(
                symbol=market_symbol(self.symbol),
                type='market',
                side=side,
                amount=self.amount
            )
            self.tracker.mark_closed(self.trade_id)
        except Exception as error:
            log(f"Ошибка при полном выходе: {error}")
            raise

    def _update_sl(self, new_sl: float) -> None:
        """
        Обновляет стоп-лосс (SL) на заданное новое значение.
        """
        self.sl = new_sl
        log(f"Обновили SL на {new_sl:.5f} по {self.symbol}")
        # В реальной торговле можно отправить stop order или просто обновлять в системе
