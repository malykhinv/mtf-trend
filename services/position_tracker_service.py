from datetime import datetime, timedelta
from data.db import get_connection
from services.position_manager import PositionManager
from utils.logger import log
from data.binance_client import get_binance_client
from config.constants import MIN_COOLDOWN_PER_SYMBOL_MINUTES


class PositionTrackerService:
    """
    Сервис для отслеживания, обновления и фиксации статуса открытых и завершённых сделок (trade management).
    Работает с SQLite и Binance API.
    """
    def __init__(self) -> None:
        """
        Инициализация соединения с Binance и БД.
        """
        self.client = get_binance_client()

    def track_all(self) -> None:
        """
        Обходит все открытые сделки и запускает PositionManager для сопровождения позиции.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
            SELECT id, symbol, side, entry, sl, tp, atr, amount_usdt, partial_exit_done
            FROM trades WHERE active = 1
        """
            )
            rows = cursor.fetchall()

        for row in rows:
            trade_id, symbol, side, entry, sl, tp, atr, amount, partial_exit_done = row
            manager = PositionManager(
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                entry=entry,
                sl=sl,
                tp=tp,
                client=self.client,
                atr=atr,
                amount=amount,
                partial_exit_done=bool(partial_exit_done),
                tracker=self
            )
            try:
                manager.manage()
            except Exception as error:
                log(f"Ошибка в PositionManager для {symbol}: {error}")
                raise

    def add_trade(self, symbol: str, side, entry: float, sl: float, tp: float, atr: float, amount: float) -> None:
        """
        Добавляет новую сделку в БД, ставит cooldown.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            # Проверка: если уже есть активная сделка по symbol — новая не добавляется
            cursor.execute(
                "SELECT COUNT(*) FROM trades WHERE symbol = ? AND active = 1",
                (symbol,),
            )
            result = cursor.fetchone()
            if result and result[0] > 0:
                log(f"[DB] Есть активная сделка по {symbol}, новая не создаётся")
                return
            # Рассчитываем cooldown до
            cooldown_until = datetime.now() + timedelta(minutes=MIN_COOLDOWN_PER_SYMBOL_MINUTES)
            cursor.execute(
                """
            INSERT INTO trades (symbol, side, entry, sl, tp, atr, amount_usdt, active, cooldown_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
                (symbol, side, entry, sl, tp, atr, amount, cooldown_until.isoformat()),
            )
            conn.commit()
            log(f"[DB] Добавлена сделка {symbol} {side} @ {entry}")

    def mark_partial_exit(self, trade_id: int) -> None:
        """
        Ставит флаг partial_exit_done и обновляет last_action_ts.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
            UPDATE trades SET partial_exit_done = 1, last_action_ts = ? WHERE id = ?
        """,
                (datetime.now().isoformat(), trade_id),
            )
            conn.commit()
            log(f"[DB] Частичный выход зафиксирован для сделки ID={trade_id}")

    def mark_closed(self, trade_id: int) -> None:
        """
        Ставит статус active=0 и обновляет last_action_ts — сделка полностью закрыта.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
            UPDATE trades SET active = 0, last_action_ts = ? WHERE id = ?
        """,
                (datetime.now().isoformat(), trade_id),
            )
            conn.commit()
            log(f"[DB] Сделка ID={trade_id} закрыта")
