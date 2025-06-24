from datetime import datetime, timedelta
from data.db import get_connection

class TradeLog:
    def __init__(self):
        self.conn = get_connection()

    def record_trade(self, symbol: str, direction: str, amount_usdt: float):
        with self.conn:
            self.conn.execute(
                "INSERT INTO trades (symbol, direction, amount_usdt) VALUES (?, ?, ?)",
                (symbol, direction, amount_usdt)
            )

    def trades_last_hour(self) -> int:
        one_hour_ago = datetime.now() - timedelta(hours=1)
        result = self.conn.execute(
            "SELECT COUNT(*) FROM trades WHERE timestamp > ?",
            (one_hour_ago.isoformat(),)
        ).fetchone()
        return result[0] if result else 0

    def traded_recently(self, symbol: str, minutes: int) -> bool:
        limit = datetime.now() - timedelta(minutes=minutes)
        result = self.conn.execute(
            "SELECT 1 FROM trades WHERE symbol = ? AND timestamp > ? LIMIT 1",
            (symbol, limit.isoformat())
        ).fetchone()
        return result is not None