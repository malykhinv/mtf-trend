import ccxt
from config.credentials import BINANCE_API_KEY, BINANCE_API_SECRET


def get_binance_client() -> ccxt.binance:
    return ccxt.binance(
        {
            'apiKey': BINANCE_API_KEY,
            'secret': BINANCE_API_SECRET,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True
            }
        }
    )
