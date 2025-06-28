from domain.models.scenario import Scenario
from domain.models.side import Side
from services.position_manager import PositionManager
from data.loader import Loader
from data.db import get_connection
from utils.logger import log
from data.binance_client import get_binance_client


class PositionTrackerService:
    def __init__(self):
        self.client = get_binance_client()
        self.loader = Loader()
        self.conn = get_connection()

    def track_all(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT symbol, tfs, side, entry, sl, tp, scenario, atr, amount_usdt 
            FROM trades WHERE active = 1
        """)
        rows = cursor.fetchall()

        for row in rows:
            symbol, tfs, side, entry, sl, tp, scenario, atr, amount = row
            manager = PositionManager(
                symbol=symbol,
                tfs=tfs,
                side=side,
                entry=entry,
                sl=sl,
                tp=tp,
                scenario=scenario,
                client=self.client,
                atr=atr,
                amount=amount,
                tracker=self
            )
            try:
                manager.manage()
            except Exception as error:
                log(f"Ошибка в PositionManager для {symbol}: {error}")
                raise

    def add_trade(self,
                  symbol: str,
                  side: Side,
                  entry: float,
                  sl: float,
                  tp: float,
                  scenario: Scenario,
                  atr: float,
                  amount: float):
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO trades (symbol, side, entry, sl, tp, scenario, atr, amount_usdt, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (symbol, side, entry, sl, tp, scenario, atr, amount))
        self.conn.commit()
        log(f"[DB] Добавлена сделка {symbol} {side} @ {entry}")

    def mark_closed(self, symbol: str):
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE trades SET active = 0 WHERE symbol = ?
        """, (symbol,))
        self.conn.commit()
        log(f"[DB] Сделка по {symbol} завершена")
