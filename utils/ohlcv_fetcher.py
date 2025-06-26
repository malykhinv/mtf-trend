import os

import ccxt
import pandas as pd

from utils.logger import log
from utils.str_utils import clean_symbol

# === НАСТРОЙКИ ===
coin = 'SEI'
symbol = 'SEI/USDT'
symbol_clean = clean_symbol(symbol)
limit = 500
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
output_dir = os.path.join(project_root, '.generated/ohlcv')
os.makedirs(output_dir, exist_ok=True)
filename = os.path.join(output_dir, f"{symbol_clean}_OHLCV.csv")

# Таймфреймы Binance (ccxt)
timeframes = {
    '1D': '1d',
    '4H': '4h',
    '1H': '1h',
    '15M': '15m',
    '5M': '5m'
}

# === ИНИЦИАЛИЗАЦИЯ ===
exchange = ccxt.binance()

def fetch_ohlcv(tf):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

with open(filename, 'w', encoding='utf-8') as f:
    f.write(f"# SYMBOL: {symbol_clean}\n")
    f.write(f"# FORMAT: OHLCV\n")
    for label, tf in timeframes.items():
        f.write(f"# TIMEFRAME: {label}\n")
        df = fetch_ohlcv(tf)
        df.to_csv(f, index=False)
        f.write("\n")

    # Инструкция для ChatGPT
    f.write("# INSTRUCTION:\n")
    f.write("# Проанализируй этот файл по моей системе MTF (momentum + flat).\n")
    f.write("# Определи фазу на каждом ТФ, найди потенциальные сетапы на вход и уточни, где я мог бы войти.\n")

log(f"✓ Файл сохранён: {filename}")