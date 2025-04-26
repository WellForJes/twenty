import os
import time
import math
import requests
import pandas as pd
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, ADXIndicator
from datetime import datetime
import pytz

# === Параметры ===
TOTAL_DEPOSIT = 20
TRAIL_START_TRIGGER = 1.01
TRAIL_DISTANCE = 0.007
MAX_DEPOSIT_USAGE = 0.5
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "AVAXUSDT",
    "LINKUSDT", "INJUSDT", "APTUSDT", "SUIUSDT",
    "XRPUSDT", "NEARUSDT", "OPUSDT", "LDOUSDT", "FTMUSDT"
]
INTERVAL = Client.KLINE_INTERVAL_15MINUTE
LIMIT = 100

# === Telegram ===
TELEGRAM_TOKEN = os.getenv("7925464368:AAEmy9EL3z216z0y8ml4t7rulC1v3ZstQ0U")
TELEGRAM_CHAT_ID = os.getenv("349999939")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

# === Binance API ===
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")
client = Client(api_key, api_secret)

# === Стартовые переменные ===
open_positions = []

while True:
    try:
        current_balance = TOTAL_DEPOSIT - sum([p['amount'] for p in open_positions if not p['closed']])
        for symbol in SYMBOLS:
            df = pd.DataFrame(client.futures_klines(symbol=symbol, interval=INTERVAL, limit=LIMIT))
            df.columns = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume",
                          "number_of_trades", "taker_buy_base_vol", "taker_buy_quote_vol", "ignore"]
            df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
            
            df['rsi'] = RSIIndicator(df['close']).rsi()
            df['ema20'] = EMAIndicator(df['close'], window=20).ema_indicator()
            df['ema50'] = EMAIndicator(df['close'], window=50).ema_indicator()
            df['adx'] = ADXIndicator(df['high'], df['low'], df['close']).adx()
            df['volume_mean20'] = df['volume'].rolling(window=20).mean()
            df['candle_size'] = abs(df['close'] - df['open'])
            df['candle_size_mean20'] = df['candle_size'].rolling(window=20).mean()

            latest = df.iloc[-1]
            price = latest['close']
            low = df['close'].iloc[-20:].min()
            high = df['close'].iloc[-20:].max()
            rsi = latest['rsi']
            ema20 = latest['ema20']
            ema50 = latest['ema50']
            adx = latest['adx']
            volume = latest['volume']
            volume_mean20 = latest['volume_mean20']
            candle_size = latest['candle_size']
            candle_size_mean20 = latest['candle_size_mean20']

            bullish_engulfing = df.iloc[-2]['close'] < df.iloc[-2]['open'] and df.iloc[-1]['close'] > df.iloc[-1]['open']
            bearish_engulfing = df.iloc[-2]['close'] > df.iloc[-2]['open'] and df.iloc[-1]['close'] < df.iloc[-1]['open']

            # Закрытие сделок
            for pos in open_positions:
                if pos['symbol'] == symbol and not pos['closed']:
                    if pos['side'] == 'long':
                        if price >= pos['entry_price'] * TRAIL_START_TRIGGER:
                            pos['trail_price'] = max(pos.get('trail_price', 0), price * (1 - TRAIL_DISTANCE))
                        if 'trail_price' in pos and price <= pos['trail_price']:
                            pnl = (price - pos['entry_price']) / pos['entry_price'] * pos['amount']
                            send_telegram(f"✅ {symbol} LONG закрыт. PnL: {pnl:.2f} USDT")
                            pos['closed'] = True
                    elif pos['side'] == 'short':
                        if price <= pos['entry_price'] * (2 - TRAIL_START_TRIGGER):
                            pos['trail_price'] = min(pos.get('trail_price', float('inf')), price * (1 + TRAIL_DISTANCE))
                        if 'trail_price' in pos and price >= pos['trail_price']:
                            pnl = (pos['entry_price'] - price) / pos['entry_price'] * pos['amount']
                            send_telegram(f"✅ {symbol} SHORT закрыт. PnL: {pnl:.2f} USDT")
                            pos['closed'] = True

            # Открытие сделок
            if adx < 25 and abs(adx - df.iloc[-2]['adx']) < 5 and volume > volume_mean20 and candle_size > candle_size_mean20:
                if ema20 > ema50 and price <= low * 1.01 and rsi < 40 and bullish_engulfing and current_balance * 0.1 <= TOTAL_DEPOSIT * MAX_DEPOSIT_USAGE:
                    entry_amount = current_balance * 0.10
                    open_positions.append({'symbol': symbol, 'side': 'long', 'entry_price': price, 'amount': entry_amount, 'closed': False})
                    send_telegram(f"🚀 Открыт LONG по {symbol} по цене {price:.2f}")
                elif ema20 < ema50 and price >= high * 0.99 and rsi > 60 and bearish_engulfing and current_balance * 0.1 <= TOTAL_DEPOSIT * MAX_DEPOSIT_USAGE:
                    entry_amount = current_balance * 0.10
                    open_positions.append({'symbol': symbol, 'side': 'short', 'entry_price': price, 'amount': entry_amount, 'closed': False})
                    send_telegram(f"🚀 Открыт SHORT по {symbol} по цене {price:.2f}")

    except Exception as e:
        print(f"Ошибка обработки символа: {e}")

    time.sleep(60)
