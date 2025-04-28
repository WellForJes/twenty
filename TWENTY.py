import pandas as pd
import numpy as np
import requests
import ta
import matplotlib.pyplot as plt
from binance.client import Client
import time
import telegram
import asyncio

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

async def send_telegram_message(message):
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

# Торговая логика

def trading_bot(symbols, interval='30m'):
    balance = 30
    free_balance = balance
    positions = {}
    risk_per_trade = 0.03
    
    asyncio.run(send_telegram_message("🤖 Бот запущен!"))

    hourly_data = {}
    last_hourly_update = time.time()

    for symbol in symbols:
        df_1h = get_binance_klines(symbol, interval='1h', limit=500)
        df_1h['EMA200_1h'] = ta.trend.ema_indicator(df_1h['close'], window=200)
        hourly_data[symbol] = df_1h

    while True:
        try:
            # Обновляем данные 1h раз в час
            if time.time() - last_hourly_update > 3600:
                for symbol in symbols:
                    df_1h = get_binance_klines(symbol, interval='1h', limit=500)
                    df_1h['EMA200_1h'] = ta.trend.ema_indicator(df_1h['close'], window=200)
                    hourly_data[symbol] = df_1h
                last_hourly_update = time.time()

            for symbol in symbols:
                client.futures_change_leverage(symbol=symbol, leverage=10)
                df = get_binance_klines(symbol, interval=interval, limit=500)
                df = prepare_data(df)
                if df.empty:
                    continue

                last_row = df.iloc[-1]
                entry_price = last_row['close']

                # Подтверждение через EMA200 на 1h
                current_hour = last_row.name.floor('h')
                if current_hour in hourly_data[symbol].index:
                    ema200_1h = hourly_data[symbol].loc[current_hour]['EMA200_1h']
                else:
                    continue

                if symbol not in positions:
                    trade_amount = free_balance * 0.30  # 30% на сделку
                    if last_row['ADX'] > 20 and last_row['volatility'] > 0.002 and last_row['volume'] > last_row['volume_mean'] and abs(last_row['CCI']) > 100:
                        if last_row['EMA50'] > last_row['EMA200'] and last_row['close'] > last_row['EMA200'] and last_row['close'] > ema200_1h:
                            side = 'BUY'
                            qty = round(trade_amount / entry_price, 3)
                            if qty * entry_price >= 5:
                                client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                                take_profit = entry_price * 1.007
                                stop_loss = entry_price * 0.997
                                positions[symbol] = ('long', entry_price, stop_loss, take_profit, qty, trade_amount)
                                free_balance -= trade_amount
                        elif last_row['EMA50'] < last_row['EMA200'] and last_row['close'] < last_row['EMA200'] and last_row['close'] < ema200_1h:
                            side = 'SELL'
                            qty = round(trade_amount / entry_price, 3)
                            if qty * entry_price >= 5:
                                client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty)
                                take_profit = entry_price * 0.993
                                stop_loss = entry_price * 1.003
                                positions[symbol] = ('short', entry_price, stop_loss, take_profit, qty, trade_amount)
                                free_balance -= trade_amount

                else:
                    direction, entry_price, stop_loss, take_profit, qty, trade_amount = positions[symbol]
                    if direction == 'long':
                        if last_row['low'] <= stop_loss:
                            client.futures_create_order(symbol=symbol, side='SELL', type='MARKET', quantity=qty)
                            loss = trade_amount * risk_per_trade
                            balance -= loss
                            free_balance += trade_amount
                            asyncio.run(send_telegram_message(f"❌ Stop Loss {symbol}: {loss:.2f} USD"))
                            positions.pop(symbol)
                        elif last_row['high'] >= take_profit:
                            client.futures_create_order(symbol=symbol, side='SELL', type='MARKET', quantity=qty)
                            profit = trade_amount * risk_per_trade * 3
                            balance += profit
                            free_balance += trade_amount
                            asyncio.run(send_telegram_message(f"✅ Take Profit {symbol}: {profit:.2f} USD"))
                            positions.pop(symbol)
                    elif direction == 'short':
                        if last_row['high'] >= stop_loss:
                            client.futures_create_order(symbol=symbol, side='BUY', type='MARKET', quantity=qty)
                            loss = trade_amount * risk_per_trade
                            balance -= loss
                            free_balance += trade_amount
                            asyncio.run(send_telegram_message(f"❌ Stop Loss {symbol}: {loss:.2f} USD"))
                            positions.pop(symbol)
                        elif last_row['low'] <= take_profit:
                            client.futures_create_order(symbol=symbol, side='BUY', type='MARKET', quantity=qty)
                            profit = trade_amount * risk_per_trade * 3
                            balance += profit
                            free_balance += trade_amount
                            asyncio.run(send_telegram_message(f"✅ Take Profit {symbol}: {profit:.2f} USD"))
                            positions.pop(symbol)

            asyncio.run(send_telegram_message(f"📊 Баланс: {balance:.2f} USD, Свободный: {free_balance:.2f} USD"))
            time.sleep(600)

        except Exception as e:
            asyncio.run(send_telegram_message(f"⚠️ Ошибка: {str(e)}"))
            time.sleep(600)

# Запуск
symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LTCUSDT', 'ADAUSDT']
trading_bot(symbols)
