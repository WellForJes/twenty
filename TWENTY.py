import time
import os
import requests
import telebot
import math
import warnings
from datetime import datetime
from binance.client import Client
from binance.enums import *
from ta.trend import adx
from ta.momentum import RSIIndicator
import pandas as pd
import numpy as np

# === Отключаем предупреждения от ta-lib ===
warnings.filterwarnings("ignore", category=RuntimeWarning)

# === Переменные окружения (Render) ===
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# === Настройки бота ===
ALLOWED_SYMBOLS = [
    'XRPUSDT', 'DOGEUSDT', 'TRXUSDT', 'LINAUSDT', 'BLZUSDT', '1000BONKUSDT'
]

RISK_PER_TRADE = 3  # USD
LEVERAGE = 10
CHECK_INTERVAL = 60  # seconds

# === Binance и Telegram ===
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://fapi.binance.com/fapi'  # 🔥 Правильный URL для REAL Futures
active_positions = {}
symbol_info = {}

def send_message(text):
    bot.send_message(TELEGRAM_CHAT_ID, text)

def load_symbol_info():
    exchange_info = client.futures_exchange_info()
    for s in exchange_info['symbols']:
        symbol = s['symbol']
        step_size = tick_size = 0.0
        for f in s['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
            if f['filterType'] == 'PRICE_FILTER':
                tick_size = float(f['tickSize'])
        symbol_info[symbol] = {'stepSize': step_size, 'tickSize': tick_size}

def round_step(value, step):
    return math.floor(value / step) * step

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
    if df['ADX'].isna().any() or df['RSI'].isna().any():
        return False
    adx_val = df['ADX'].dropna().iloc[-1]
    rsi_val = df['RSI'].dropna().iloc[-1]
    return adx_val < 20 and 40 < rsi_val < 60

def detect_range(df):
    recent = df[-20:]
    support = recent['low'].min()
    resistance = recent['high'].max()
    return support, resistance

def get_price(symbol):
    ticker = client.futures_ticker(symbol=symbol)
    return float(ticker['lastPrice'])

def calculate_tp_sl(entry, direction, support, resistance, symbol):
    if direction == 'long':
        tp = resistance
        sl = entry - (tp - entry) / 3
    else:
        tp = support
        sl = entry + (entry - tp) / 3
    tick = symbol_info[symbol]['tickSize']
    return round_step(tp, tick), round_step(sl, tick)

def get_position_size(entry, sl, symbol):
    loss_per_unit = abs(entry - sl)
    total_loss = RISK_PER_TRADE
    raw_qty = total_loss / loss_per_unit
    step = symbol_info[symbol]['stepSize']
    return round_step(raw_qty, step)

def place_order(symbol, side, qty, sl, tp):
    try:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY if side == 'long' else SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )

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

def check_closed_positions():
    global active_positions
    try:
        positions = client.futures_position_information()
        for pos in positions:
            symbol = pos['symbol']
            position_amt = float(pos['positionAmt'])
            if symbol in active_positions and position_amt == 0:
                active_positions.pop(symbol, None)
                send_message(f"✅ Позиция по {symbol} ЗАКРЫТА")
    except Exception as e:
        send_message(f"⚠️ Ошибка при проверке позиций: {e}")

def initial_analysis_report():
    message = "🤖 Бот запущен!\n\n📊 Анализ монет:\n"
    for symbol in ALLOWED_SYMBOLS:
        try:
            df = get_klines(symbol, interval='1h', limit=50)
            flat = is_flat(df)
            if flat:
                message += f"{symbol} — боковик ✅\n"
            else:
                message += f"{symbol} — тренд ❌\n"
        except Exception as e:
            message += f"{symbol} — ошибка ⚠️ ({e})\n"
    send_message(message)

# === СТАРТ ===
load_symbol_info()
initial_analysis_report()

while True:
    for symbol in ALLOWED_SYMBOLS:
        if symbol in active_positions:
            continue

        try:
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
                tp, sl = calculate_tp_sl(price, direction, support, resistance, symbol)
                qty = get_position_size(price, sl, symbol)
                if place_order(symbol, direction, qty, sl, tp):
                    active_positions[symbol] = True
                    send_message(
                        f"📈 Сделка ОТКРЫТА ({direction.upper()}) {symbol}\n"
                        f"Entry: {price}\nTP: {tp}\nSL: {sl}\nQty: {qty} @ x{LEVERAGE}\n"
                        f"Время: {datetime.utcnow().strftime('%H:%M:%S')} UTC"
                    )
        except Exception as e:
            send_message(f"⚠️ Ошибка при обработке {symbol}: {e}")

    check_closed_positions()
    time.sleep(CHECK_INTERVAL)
