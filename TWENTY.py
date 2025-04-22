import os
import time
import math
import requests
import pandas as pd
import pytz
from datetime import datetime
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, ADXIndicator
from dotenv import load_dotenv
import io
import sys

# === Telegram параметры ===
TELEGRAM_TOKEN = "7925464368:AAEmy9EL3z216z0y8ml4t7rulC1v3ZstQ0U"
TELEGRAM_CHAT_ID = "349999939"
last_telegram_report_time = 0

# === Настройки депозита ===
DEPOSIT = 20
RISK_PER_TRADE = 0.05  # 5% риска на сделку

# === Загрузка API ключей ===
load_dotenv()
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")
client = Client(api_key, api_secret)

# === Монеты, подходящие под депозит $20 ===
symbols = [
    "ETHUSDT", "SOLUSDT", "LINKUSDT", "INJUSDT", "APTUSDT",
    "SUIUSDT", "XRPUSDT", "OPUSDT", "LDOUSDT"
]

symbol_precisions = {}
min_quantities = {}

try:
    exchange_info = client.futures_exchange_info()
    for symbol_info in exchange_info['symbols']:
        symbol = symbol_info['symbol']
        for f in symbol_info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                min_qty = float(f['minQty'])
                precision = int(round(-math.log(step_size, 10), 0))
                symbol_precisions[symbol] = precision
                min_quantities[symbol] = min_qty
except Exception as e:
    print("❌ Ошибка при получении информации о бирже:", e)

INTERVAL = Client.KLINE_INTERVAL_15MINUTE
LIMIT = 100

log_buffer = io.StringIO()
sys.stdout = log_buffer

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    requests.post(url, data=payload)

def analyze_and_trade(symbol):
    print(f"▶️ Анализ: {symbol}")
    try:
        klines = client.futures_klines(symbol=symbol, interval=INTERVAL, limit=LIMIT)
        df = pd.DataFrame(klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_vol", "taker_buy_quote_vol", "ignore"])
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)

        df['rsi'] = RSIIndicator(df['close']).rsi()
        df['ema20'] = EMAIndicator(df['close'], window=20).ema_indicator()
        df['ema50'] = EMAIndicator(df['close'], window=50).ema_indicator()
        df['adx'] = ADXIndicator(df['high'], df['low'], df['close']).adx()

        latest = df.iloc[-1]
        price = latest['close']
        low = df['close'].iloc[-20:].min()
        high = df['close'].iloc[-20:].max()
        rsi = latest['rsi']
        ema20 = latest['ema20']
        ema50 = latest['ema50']
        adx = latest['adx']

        # Режим: боковик или тренд
        if adx < 20 and abs(ema20 - ema50) / price < 0.005:
            tp_coef = 1.015
            sl_coef = 0.99
            mode = "БОКОВИК"
        else:
            tp_coef = 1.05
            sl_coef = 0.99
            mode = "ТРЕНД"

        print(f"{symbol}: Режим — {mode}")

        risk_amount = DEPOSIT * RISK_PER_TRADE
        position_size = risk_amount / (price * 0.01)
        prec = symbol_precisions.get(symbol, 2)
        qty = math.floor(position_size * 10**prec) / 10**prec
        min_qty = min_quantities.get(symbol, 0.001)
        if qty < min_qty:
            print(f"⛔ {symbol}: qty слишком мала")
            return

        open_orders = client.futures_get_open_orders(symbol=symbol)
        positions = client.futures_position_information(symbol=symbol)
        position = next((p for p in positions if float(p['positionAmt']) != 0), None)

        if position:
            if len(open_orders) > 2:
                for o in open_orders:
                    client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
                print(f"🧹 {symbol}: Удалены лишние ордера")
            entry_price = float(position['entryPrice'])
            side = "LONG" if float(position['positionAmt']) > 0 else "SHORT"
            if side == 'LONG':
                sl = round(entry_price * sl_coef, 2)
                tp = round(entry_price * tp_coef, 2)
                client.futures_create_order(symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
                                            stopPrice=tp, closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
                client.futures_create_order(symbol=symbol, side="SELL", type="STOP_MARKET",
                                            stopPrice=sl, closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
            else:
                sl = round(entry_price * (2 - sl_coef), 2)
                tp = round(entry_price * (2 - tp_coef), 2)
                client.futures_create_order(symbol=symbol, side="BUY", type="TAKE_PROFIT_MARKET",
                                            stopPrice=tp, closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
                client.futures_create_order(symbol=symbol, side="BUY", type="STOP_MARKET",
                                            stopPrice=sl, closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
            print(f"🔁 {symbol}: TP/SL обновлены")
            return

        if open_orders:
            for o in open_orders:
                client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
            print(f"🧹 {symbol}: Очищены старые ордера перед входом")

        if adx < 25 and price <= low * 1.01 and rsi < 40 and price < ema20 < ema50:
            sl = round(price * sl_coef, 2)
            tp = round(price * tp_coef, 2)
            client.futures_create_order(symbol=symbol, side="BUY", type="MARKET", quantity=qty)
            client.futures_create_order(symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET", stopPrice=tp,
                                        closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
            client.futures_create_order(symbol=symbol, side="SELL", type="STOP_MARKET", stopPrice=sl,
                                        closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
            print(f"✅ {symbol} | ЛОНГ | Цена: {price} | Qty: {qty}")

        elif adx < 25 and price >= high * 0.99 and rsi > 60 and price > ema20 > ema50:
            sl = round(price * (2 - sl_coef), 2)
            tp = round(price * (2 - tp_coef), 2)
            client.futures_create_order(symbol=symbol, side="SELL", type="MARKET", quantity=qty)
            client.futures_create_order(symbol=symbol, side="BUY", type="TAKE_PROFIT_MARKET", stopPrice=tp,
                                        closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
            client.futures_create_order(symbol=symbol, side="BUY", type="STOP_MARKET", stopPrice=sl,
                                        closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
            print(f"✅ {symbol} | ШОРТ | Цена: {price} | Qty: {qty}")

        else:
            print(f"{symbol}: Условия не выполнены")

    except Exception as e:
        print(f"❌ Ошибка {symbol}: {type(e).__name__} — {e}")
