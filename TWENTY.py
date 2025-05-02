# === config.py ===
# Вставь сюда свои ключи Binance
API_KEY = 'YOUR_BINANCE_API_KEY'
API_SECRET = 'YOUR_BINANCE_API_SECRET'

TELEGRAM_TOKEN = '7797995733:AAENKe8raT-UB0f98JEd5lEh93fvr2wED5o'
TELEGRAM_CHAT_ID = '349999939'

ALLOWED_SYMBOLS = [
    'XRPUSDT', 'DOGEUSDT', 'TRXUSDT', 'LINAUSDT', 'BLZUSDT', 'PEPEUSDT', '1000BONKUSDT'
]

RISK_PER_TRADE = 3  # USD
LEVERAGE = 10
CHECK_INTERVAL = 60  # seconds

# === main.py ===
import time
import requests
import telebot
import math
from datetime import datetime
from binance.client import Client
from binance.enums import *
from ta.volatility import average_true_range
from ta.trend import adx
from ta.momentum import RSIIndicator
import pandas as pd
import numpy as np
import config

bot = telebot.TeleBot(config.TELEGRAM_TOKEN)
client = Client(config.API_KEY, config.API_SECRET)
active_positions = {}

def send_message(text):
    bot.send_message(config.TELEGRAM_CHAT_ID, text)

def get_klines(symbol, interval='1h', limit=50):
    data = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(data)
    df.columns = ['time','open','high','low','close','volume','close_time','qav','num_trades','taker_base_vol','taker_quote_vol','ignore']
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['open'] = df['open'].astype(float)
    return df

def is_flat(df):
    df['ADX'] = adx(df['high'], df['low'], df['close'], window=14)
    df['RSI'] = RSIIndicator(df['close'], window=14).rsi()
    adx_val = df['ADX'].iloc[-1]
    rsi_val = df['RSI'].iloc[-1]
    return adx_val < 20 and 40 < rsi_val < 60

def detect_range(df):
    recent = df[-20:]
    support = recent['low'].min()
    resistance = recent['high'].max()
    return support, resistance

def get_price(symbol):
    ticker = client.futures_ticker(symbol=symbol)
    return float(ticker['lastPrice'])

def calculate_tp_sl(entry, direction, support, resistance):
    if direction == 'long':
        tp = resistance
        sl = entry - (tp - entry) / 3
    else:
        tp = support
        sl = entry + (entry - tp) / 3
    return round(tp, 6), round(sl, 6)

def get_position_size(entry, sl):
    loss_per_unit = abs(entry - sl)
    total_loss = config.RISK_PER_TRADE
    qty = total_loss / loss_per_unit
    return round(qty, 2)

def place_order(symbol, side, qty, sl, tp):
    try:
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY if side == 'long' else SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        pos_side = 'BUY' if side == 'long' else 'SELL'
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side == 'long' else SIDE_BUY,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=round(sl, 4),
            closePosition=True,
            timeInForce='GTC',
            reduceOnly=True
        )
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side == 'long' else SIDE_BUY,
            type=ORDER_TYPE_LIMIT,
            price=round(tp, 4),
            timeInForce='GTC',
            reduceOnly=True,
            quantity=qty
        )
        return True
    except Exception as e:
        send_message(f"❌ Ошибка при создании ордера: {e}")
        return False

# === MAIN LOOP ===
while True:
    for symbol in config.ALLOWED_SYMBOLS:
        if symbol in active_positions:
            continue

        df = get_klines(symbol, interval='1h', limit=50)
        if not is_flat(df):
            continue

        support, resistance = detect_range(df)
        price = get_price(symbol)
        direction = None

        if price <= support * 1.01:
            direction = 'long'
        elif price >= resistance * 0.99:
            direction = 'short'

        if direction:
            tp, sl = calculate_tp_sl(price, direction, support, resistance)
            qty = get_position_size(price, sl)
            if place_order(symbol, direction, qty, sl, tp):
                active_positions[symbol] = True
                send_message(
                    f"📈 Сделка ОТКРЫТА ({direction.upper()}) {symbol}\nEntry: {price}\nTP: {tp}\nSL: {sl}\nQty: {qty} @ x10\nВремя: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
                )

    time.sleep(config.CHECK_INTERVAL)
