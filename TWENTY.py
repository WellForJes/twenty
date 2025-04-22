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
POSITION_PERCENT = 0.2  # 20% депозита на сделку
RISK_PER_TRADE = 0.05  # по-прежнему используем для расчёта позиции

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

def send_status_to_telegram():
    try:
        tz = pytz.timezone("Europe/Kyiv")
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        positions_info = []
        account_info = client.futures_account()
        balance_info = next((b for b in account_info.get('assets', []) if b['asset'] == 'USDT'), None)
        balance = float(balance_info.get('availableBalance', 0.0)) if balance_info else 0.0
        total_pnl = 0

        for symbol in symbols:
            klines = client.futures_klines(symbol=symbol, interval=INTERVAL, limit=LIMIT)
            df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume",
                                               "close_time", "quote_asset_volume", "number_of_trades",
                                               "taker_buy_base_vol", "taker_buy_quote_vol", "ignore"])
            df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
            df['ema20'] = EMAIndicator(df['close'], window=20).ema_indicator()
            df['ema50'] = EMAIndicator(df['close'], window=50).ema_indicator()
            df['adx'] = ADXIndicator(df['high'], df['low'], df['close']).adx()

            latest = df.iloc[-1]
            price = latest['close']
            ema20 = latest['ema20']
            ema50 = latest['ema50']
            adx = latest['adx']

            if adx < 20 and abs(ema20 - ema50) / price < 0.005:
                tp_coef = 1.02
                sl_coef = 0.995
            else:
                tp_coef = 1.05
                sl_coef = 0.99

            positions = client.futures_position_information(symbol=symbol)
            position = next((p for p in positions if float(p['positionAmt']) != 0), None)
            if position:
                amt = float(position['positionAmt'])
                entry = float(position['entryPrice'])
                mark = float(position['markPrice'])
                unrealized = float(position.get('unrealizedProfit', 0.0))
                side = "LONG" if amt > 0 else "SHORT"
                tp = round(entry * tp_coef, 2) if amt > 0 else round(entry * (2 - tp_coef), 2)
                sl = round(entry * sl_coef, 2) if amt > 0 else round(entry * (2 - sl_coef), 2)
                total_pnl += unrealized

                open_orders = client.futures_get_open_orders(symbol=symbol)
                tp_exists = any(o['type'] == 'TAKE_PROFIT_MARKET' and abs(float(o['stopPrice']) - tp) < 0.01 for o in open_orders)
                sl_exists = any(o['type'] == 'STOP_MARKET' and abs(float(o['stopPrice']) - sl) < 0.01 for o in open_orders)

                if not (tp_exists and sl_exists):
                    for o in open_orders:
                        if o['type'] in ['TAKE_PROFIT_MARKET', 'STOP_MARKET']:
                            client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
                    side_tp = "SELL" if amt > 0 else "BUY"
                    client.futures_create_order(symbol=symbol, side=side_tp, type="TAKE_PROFIT_MARKET",
                                                stopPrice=tp, closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
                    client.futures_create_order(symbol=symbol, side=side_tp, type="STOP_MARKET",
                                                stopPrice=sl, closePosition=True, timeInForce='GTC', workingType='MARK_PRICE')
                    print(f"🔁 {symbol}: Обновлены TP/SL до TP={tp} SL={sl}")

                positions_info.append(f"{symbol}: {side} | Вход: {entry} | Марк: {mark} | TP: {tp} | SL: {sl} | PnL: {round(unrealized, 2)}")

        if positions_info:
            positions_text = "\n".join(positions_info)
        else:
            positions_text = "Нет открытых позиций."

        logs = log_buffer.getvalue()
        last_lines = logs.strip().splitlines()[-20:]
        logs_text = "\n".join(last_lines)

        msg = (
            f"🟢 Бот работает. Последний цикл: {now} (Kyiv)\n\n"
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
            print(f"📨 Статус и отчёт отправлены в Telegram ({now})")
    except Exception as e:
        print(f"❌ Ошибка Telegram-отчёта: {e}")
