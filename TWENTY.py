import os
import time
import math
import csv
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, ADXIndicator
from dotenv import load_dotenv

# === Telegram параметры ===
TELEGRAM_TOKEN = "7797995733:AAENKe8raT-UB0f98JEd5lEh93fvr2wED5o"
TELEGRAM_CHAT_ID = "349999939"
last_telegram_report_time = 0

# === Настройки депозита и риска ===
TOTAL_DEPOSIT = 20
RISK_PER_TRADE = 0.10  # риск в $ на одну сделку (фиксированный убыток)
TAKE_PROFIT_MULTIPLIER = 1.03  # тейк-профит (например, 1.03 = +3%)
STOP_LOSS_MULTIPLIER = 0.99    # стоп-лосс (например, 0.99 = -1%)

# === Telegram уведомление ===
def send_status_to_telegram():
    try:
        tz = pytz.timezone("Europe/Kyiv")
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        message = f"\U0001F7E2 Боевой Бот работает. Последний цикл: {now} (Kyiv)"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        response = requests.post(url, data=payload)
        if response.status_code != 200:
            print(f"\u26A0\uFE0F Ошибка отправки Telegram: {response.text}")
        else:
            print(f"\U0001F4E8 Статус отправлен в Telegram ({now})")
    except Exception as e:
        print(f"\u274C Ошибка Telegram-отчёта: {e}")

# === API-ключи Binance ===
load_dotenv()
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

print("\u2705 Боевой бот стартовал!")
print("\U0001F512 API_KEY (первые символы):", api_key[:5], "...")
print("\U0001F512 API_SECRET (первые символы):", api_secret[:5], "...")

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
    print("\u274C Ошибка при получении информации о бирже:", e)

symbols = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "AVAXUSDT",
    "LINKUSDT", "INJUSDT", "APTUSDT", "SUIUSDT",
    "XRPUSDT", "NEARUSDT", "OPUSDT", "LDOUSDT", "FTMUSDT"
]

INTERVAL = Client.KLINE_INTERVAL_15MINUTE
LIMIT = 100

def analyze_and_trade(symbol):
    try:
        print(f"\u25B6\uFE0F Начинаю анализ: {symbol}")

        open_orders = client.futures_get_open_orders(symbol=symbol)
        positions = client.futures_position_information(symbol=symbol)
        position = next((p for p in positions if float(p['positionAmt']) != 0), None)

        # === Очистка TP/SL если позиция уже закрыта ===
        if position is None and (len(open_orders) > 0):
            for o in open_orders:
                client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
            print(f"\U0001F9F9 {symbol}: Позиции нет, но TP/SL были — всё очищено")
            return

        if position:
            entry_price = float(position['entryPrice'])
            side = 'LONG' if float(position['positionAmt']) > 0 else 'SHORT'

            tp_orders = [o for o in open_orders if o['type'] == "TAKE_PROFIT_MARKET"]
            sl_orders = [o for o in open_orders if o['type'] == "STOP_MARKET"]

            if len(tp_orders) + len(sl_orders) > 2:
                for o in open_orders:
                    client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
                print(f"\u274C {symbol}: Найдено дублирование TP/SL, всё очищено")

            elif len(tp_orders) == 0 or len(sl_orders) == 0:
                for o in open_orders:
                    client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])

                if side == 'LONG':
                    stop_loss = round(entry_price * STOP_LOSS_MULTIPLIER, 4)
                    take_profit = round(entry_price * TAKE_PROFIT_MULTIPLIER, 4)
                    client.futures_create_order(symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
                                                stopPrice=take_profit, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    client.futures_create_order(symbol=symbol, side="SELL", type="STOP_MARKET",
                                                stopPrice=stop_loss, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    print(f"\U0001F501 {symbol}: TP/SL восстановлены для LONG")
                else:
                    stop_loss = round(entry_price * (2 - STOP_LOSS_MULTIPLIER), 4)
                    take_profit = round(entry_price * (2 - TAKE_PROFIT_MULTIPLIER), 4)
                    client.futures_create_order(symbol=symbol, side="BUY", type="TAKE_PROFIT_MARKET",
                                                stopPrice=take_profit, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    client.futures_create_order(symbol=symbol, side="BUY", type="STOP_MARKET",
                                                stopPrice=stop_loss, closePosition=True,
                                                timeInForce='GTC', workingType='MARK_PRICE')
                    print(f"\U0001F501 {symbol}: TP/SL восстановлены для SHORT")
            else:
                print(f"\u23F8 {symbol}: Позиция уже открыта, TP/SL в порядке")
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

        prec = symbol_precisions.get(symbol, 2)

        # === Логика входа по условиям стратегии ===
        if adx < 25 and price <= low * 1.01 and rsi < 40 and price < ema20 < ema50:
            stop_price = price * STOP_LOSS_MULTIPLIER
            loss_per_unit = price - stop_price
            quantity = RISK_PER_TRADE / loss_per_unit
            quantity = math.floor(quantity * 10**prec) / 10**prec
            take_profit = round(price * TAKE_PROFIT_MULTIPLIER, 4)
            stop_loss = round(stop_price, 4)

            client.futures_create_order(symbol=symbol, side="BUY", type="MARKET", quantity=quantity)
            client.futures_create_order(symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
                                        stopPrice=take_profit, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            client.futures_create_order(symbol=symbol, side="SELL", type="STOP_MARKET",
                                        stopPrice=stop_loss, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            print(f"\u2705 {symbol} | ЛОНГ | Цена: {price} | Qty: {quantity} | TP: {take_profit} | SL: {stop_loss}")

        elif adx < 25 and price >= high * 0.99 and rsi > 60 and price > ema20 > ema50:
            stop_price = price * (2 - STOP_LOSS_MULTIPLIER)
            loss_per_unit = stop_price - price
            quantity = RISK_PER_TRADE / loss_per_unit
            quantity = math.floor(quantity * 10**prec) / 10**prec
            take_profit = round(price * (2 - TAKE_PROFIT_MULTIPLIER), 4)
            stop_loss = round(stop_price, 4)

            client.futures_create_order(symbol=symbol, side="SELL", type="MARKET", quantity=quantity)
            client.futures_create_order(symbol=symbol, side="BUY", type="TAKE_PROFIT_MARKET",
                                        stopPrice=take_profit, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            client.futures_create_order(symbol=symbol, side="BUY", type="STOP_MARKET",
                                        stopPrice=stop_loss, closePosition=True,
                                        timeInForce='GTC', workingType='MARK_PRICE')
            print(f"\u2705 {symbol} | ШОРТ | Цена: {price} | Qty: {quantity} | TP: {take_profit} | SL: {stop_loss}")

        else:
            print(f"{symbol}: Условия не выполнены")

    except Exception as e:
        print(f"\u274C Ошибка при анализе {symbol}: {type(e).__name__} — {e}")

while True:
    tz = pytz.timezone("Europe/Kyiv")
    now = datetime.now(tz).strftime("%H:%M:%S")
    print(f"\n\U0001F552 Анализ монет ({now}):")

    for symbol in symbols:
        analyze_and_trade(symbol)
        time.sleep(1)

    if int(time.time()) - last_telegram_report_time >= 300:
        send_status_to_telegram()
        last_telegram_report_time = int(time.time())

    time.sleep(60)
