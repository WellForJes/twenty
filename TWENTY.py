import pandas as pd
import numpy as np
import requests
import ta
import matplotlib.pyplot as plt
from binance.client import Client
import time
import telegram

# Конфигурация
API_KEY = 'your_api_key'
API_SECRET = 'your_api_secret'
TELEGRAM_TOKEN = 'your_telegram_bot_token'
TELEGRAM_CHAT_ID = 'your_chat_id'

client = Client(API_KEY, API_SECRET)
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# Функции

def get_binance_klines(symbol='BTCUSDT', interval='30m', limit=1000):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    return df

def prepare_data(df):
    df['EMA50'] = ta.trend.ema_indicator(df['close'], window=50)
    df['EMA200'] = ta.trend.ema_indicator(df['close'], window=200)
    df['RSI'] = ta.momentum.rsi(df['close'], window=14)
    df['ADX'] = ta.trend.adx(df['high'], df['low'], df['close'], window=14)
    df['volatility'] = (df['high'] - df['low']) / df['close']
    df['volume_mean'] = df['volume'].rolling(window=50).mean()
    df['CCI'] = ta.trend.cci(df['high'], df['low'], df['close'], window=20)
    return df.dropna()

def send_telegram_message(message):
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

# Торговая логика

def trading_bot(symbols, interval='30m'):
    balance = 20
    free_balance = balance
    positions = {}
    risk_per_trade = 0.03

    send_telegram_message("🤖 Бот запущен!")

    while True:
        try:
            for symbol in symbols:
                df = get_binance_klines(symbol, interval=interval, limit=500)
                df = prepare_data(df)
                if df.empty:
                    continue

                last_row = df.iloc[-1]
                entry_price = last_row['close']

                if symbol not in positions:
                    trade_amount = free_balance * 0.10
                    if last_row['ADX'] > 20 and last_row['volatility'] > 0.002 and last_row['volume'] > last_row['volume_mean'] and abs(last_row['CCI']) > 100:
                        if last_row['EMA50'] > last_row['EMA200'] and last_row['close'] > last_row['EMA200']:
                            side = 'BUY'
                            qty = round(trade_amount / entry_price, 3)
                            client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                            take_profit = entry_price * 1.007
                            stop_loss = entry_price * 0.997
                            positions[symbol] = ('long', entry_price, stop_loss, take_profit, qty)
                            free_balance -= trade_amount
                        elif last_row['EMA50'] < last_row['EMA200'] and last_row['close'] < last_row['EMA200']:
                            side = 'SELL'
                            qty = round(trade_amount / entry_price, 3)
                            client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                            take_profit = entry_price * 0.993
                            stop_loss = entry_price * 1.003
                            positions[symbol] = ('short', entry_price, stop_loss, take_profit, qty)
                            free_balance -= trade_amount

                else:
                    direction, entry_price, stop_loss, take_profit, qty = positions[symbol]
                    if direction == 'long':
                        if last_row['low'] <= stop_loss:
                            client.futures_create_order(symbol=symbol, side='SELL', type='MARKET', quantity=qty)
                            loss = trade_amount * risk_per_trade
                            balance -= loss
                            free_balance += trade_amount
                            send_telegram_message(f"❌ Stop Loss {symbol}: {loss:.2f} USD")
                            positions.pop(symbol)
                        elif last_row['high'] >= take_profit:
                            client.futures_create_order(symbol=symbol, side='SELL', type='MARKET', quantity=qty)
                            profit = trade_amount * risk_per_trade * 3
                            balance += profit
                            free_balance += trade_amount
                            send_telegram_message(f"✅ Take Profit {symbol}: {profit:.2f} USD")
                            positions.pop(symbol)
                    elif direction == 'short':
                        if last_row['high'] >= stop_loss:
                            client.futures_create_order(symbol=symbol, side='BUY', type='MARKET', quantity=qty)
                            loss = trade_amount * risk_per_trade
                            balance -= loss
                            free_balance += trade_amount
                            send_telegram_message(f"❌ Stop Loss {symbol}: {loss:.2f} USD")
                            positions.pop(symbol)
                        elif last_row['low'] <= take_profit:
                            client.futures_create_order(symbol=symbol, side='BUY', type='MARKET', quantity=qty)
                            profit = trade_amount * risk_per_trade * 3
                            balance += profit
                            free_balance += trade_amount
                            send_telegram_message(f"✅ Take Profit {symbol}: {profit:.2f} USD")
                            positions.pop(symbol)

            send_telegram_message(f"📊 Баланс: {balance:.2f} USD, Свободный: {free_balance:.2f} USD")
            time.sleep(600)

        except Exception as e:
            send_telegram_message(f"⚠️ Ошибка: {str(e)}")
            time.sleep(600)

# Запуск
symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LTCUSDT', 'ADAUSDT']
trading_bot(symbols)
