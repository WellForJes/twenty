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

def send_status_to_telegram():
    global last_telegram_report_time
    try:
        tz = pytz.timezone("Europe/Kyiv")
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        # Пример данных, можно заменить на свои
        balance = 20.0
        total_pnl = 0.0
        positions_text = "Статус открытых позиций пока не реализован."

        logs = log_buffer.getvalue()
        last_lines = logs.strip().splitlines()[-20:]
        logs_text = "\n".join(last_lines)

        msg = (
            f"🟢 Боевой Бот работает. Последний цикл: {now} (Kyiv)\n\n"
            f"{positions_text}\n\n"
            f"💰 Баланс: {round(balance, 2)} USDT\n"
            f"📊 Чистый PnL: {round(total_pnl, 2)} USDT\n\n"
            f"📝 <b>Логи:</b>\n<pre>{logs_text}</pre>"
        )

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
        response = requests.post(url, data=payload)
        if response.status_code != 200:
            print(f"⚠️ Ошибка Telegram: {response.text}")
        else:
            print(f"📨 Статус отправлен в Telegram ({now})")

        last_telegram_report_time = int(time.time())

    except Exception as e:
        print(f"❌ Ошибка Telegram-отчёта: {e}")

# === Настройки депозита и риска ===
TOTAL_DEPOSIT = 20
RISK_PER_TRADE = 0.01
MAX_RISK_AMOUNT = TOTAL_DEPOSIT * RISK_PER_TRADE
deposit_in_use = False

# === API-ключи Binance ===
load_dotenv()
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

print("✅ Боевой бот стартовал!")
print("🔐 API_KEY (первые символы):", api_key[:5], "...")
print("🔐 API_SECRET (первые символы):", api_secret[:5], "...")

client = Client(api_key, api_secret)

# === Получение точности лотов ===
symbol_precisions = {}
try:
    exchange_info = client.futures_exchange_info()
    for symbol_info in exchange_info['symbols']:
        symbol = symbol_info['symbol']
        for f in symbol_info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                precision = int(round(-math.log(step_size, 10), 0))
                symbol_precisions[symbol] = precision
except Exception as e:
    print("❌ Ошибка при получении информации о бирже:", e)

symbols = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "AVAXUSDT",
    "LINKUSDT", "INJUSDT", "APTUSDT", "SUIUSDT",
    "XRPUSDT", "NEARUSDT", "OPUSDT", "LDOUSDT", "FTMUSDT"
]

INTERVAL = Client.KLINE_INTERVAL_15MINUTE
LIMIT = 100

def analyze_and_trade(symbol):
    global deposit_in_use
    try:
        print(f"▶️ Начинаю анализ: {symbol}")
        open_orders = client.futures_get_open_orders(symbol=symbol)
        positions = client.futures_position_information(symbol=symbol)
        position = next((p for p in positions if float(p['positionAmt']) != 0), None)

        if position:
            deposit_in_use = True
            entry_price = float(position['entryPrice'])
            side = 'LONG' if float(position['positionAmt']) > 0 else 'SHORT'
            tp_orders = [o for o in open_orders if o['type'] == "TAKE_PROFIT_MARKET"]
            sl_orders = [o for o in open_orders if o['type'] == "STOP_MARKET"]

            if len(tp_orders) + len(sl_orders) > 2:
                for o in open_orders:
                    client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
                print(f"❌ {symbol}: Найдено дублирование TP/SL, всё очищено")

            elif len(tp_orders) == 0 or len(sl_orders) == 0:
                for o in open_orders:
                    client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])

                if side == 'LONG':
                    sl = round(entry_price * 0.99, 2)
                    tp = round(entry_price * 1.05, 2)
                    client.futures_create_order(symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
                                                stopPrice=tp, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    client.futures_create_order(symbol=symbol, side="SELL", type="STOP_MARKET",
                                                stopPrice=sl, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    print(f"🔁 {symbol}: TP/SL восстановлены для LONG")
                else:
                    sl = round(entry_price * 1.01, 2)
                    tp = round(entry_price * 0.95, 2)
                    client.futures_create_order(symbol=symbol, side="BUY", type="TAKE_PROFIT_MARKET",
                                                stopPrice=tp, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    client.futures_create_order(symbol=symbol, side="BUY", type="STOP_MARKET",
                                                stopPrice=sl, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    print(f"🔁 {symbol}: TP/SL восстановлены для SHORT")
            else:
                print(f"⏸ {symbol}: Позиция уже открыта, TP/SL в порядке")
            return

        if deposit_in_use:
            print(f"❌ {symbol}: Депозит уже используется — пропускаю")
            return

        klines = client.futures_klines(symbol=symbol, interval=INTERVAL, limit=LIMIT)
        df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume",
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

        qty_in_usd = min(MAX_RISK_AMOUNT / 0.01, TOTAL_DEPOSIT)
        qty_raw = qty_in_usd / price
        prec = symbol_precisions.get(symbol, 2)
        qty = math.floor(qty_raw * 10**prec) / 10**prec

        if adx < 25 and price <= low * 1.01 and rsi < 40 and price < ema20 < ema50:
            sl = round(price * 0.99, 2)
            tp = round(price * 1.05, 2)
            client.futures_create_order(symbol=symbol, side="BUY", type="MARKET", quantity=qty)
            client.futures_create_order(symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
                                        stopPrice=tp, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            client.futures_create_order(symbol=symbol, side="SELL", type="STOP_MARKET",
                                        stopPrice=sl, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            print(f"✅ {symbol} | ЛОНГ | Цена: {price} | Qty: {qty} | TP: {tp} | SL: {sl}")
            deposit_in_use = True

        elif adx < 25 and price >= high * 0.99 and rsi > 60 and price > ema20 > ema50:
            sl = round(price * 1.01, 2)
            tp = round(price * 0.95, 2)
            client.futures_create_order(symbol=symbol, side="SELL", type="MARKET", quantity=qty)
            client.futures_create_order(symbol=symbol, side="BUY", type="TAKE_PROFIT_MARKET",
                                        stopPrice=tp, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            client.futures_create_order(symbol=symbol, side="BUY", type="STOP_MARKET",
                                        stopPrice=sl, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            print(f"✅ {symbol} | ШОРТ | Цена: {price} | Qty: {qty} | TP: {tp} | SL: {sl}")
            deposit_in_use = True

        else:
            print(f"{symbol}: Условия не выполнены")

    except Exception as e:
        print(f"❌ Ошибка при анализе {symbol}: {type(e).__name__} — {e}")

while True:
    deposit_in_use = False
    tz = pytz.timezone("Europe/Kyiv")
    now = datetime.now(tz).strftime("%H:%M:%S")
    print(f"\n🕒 Анализ монет ({now}):")

    for symbol in symbols:
        analyze_and_trade(symbol)
        time.sleep(1)

    if int(time.time()) - last_telegram_report_time >= 300:
        send_status_to_telegram()

    time.sleep(60)
