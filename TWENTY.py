import pandas as pd
import numpy as np
import requests
import ta
import matplotlib.pyplot as plt
from binance.client import Client
import time
import telegram
import asyncio
import os

# Конфигурация через переменные окружения
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
TELEGRAM_TOKEN = '7925464368:AAEmy9EL3z216z0y8ml4t7rulC1v3ZstQ0U'
TELEGRAM_CHAT_ID = '349999939'

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

# Сигнальный бот

async def signal_bot(symbols, interval='30m'):
    await send_telegram_message("🤖 Сигнальный бот запущен!")

    hourly_data = {}
    last_hourly_update = time.time()

    for symbol in symbols:
        df_1h = get_binance_klines(symbol, interval='1h', limit=500)
        df_1h['EMA200_1h'] = ta.trend.ema_indicator(df_1h['close'], window=200)
        hourly_data[symbol] = df_1h

    while True:
        try:
            session_log = "\U0001F4C8 Проверка рынка (сигнальный режим):\n"

            if time.time() - last_hourly_update > 3600:
                for symbol in symbols:
                    df_1h = get_binance_klines(symbol, interval='1h', limit=500)
                    df_1h['EMA200_1h'] = ta.trend.ema_indicator(df_1h['close'], window=200)
                    hourly_data[symbol] = df_1h
                last_hourly_update = time.time()

            for symbol in symbols:
                try:
                    df = get_binance_klines(symbol, interval=interval, limit=500)
                    df = prepare_data(df)
                    if df.empty:
                        session_log += f"{symbol}: Ошибка данных\n"
                        continue

                    last_row = df.iloc[-1]
                    entry_price = last_row['close']

                    current_hour = last_row.name.floor('h')
                    if current_hour in hourly_data[symbol].index:
                        ema200_1h = hourly_data[symbol].loc[current_hour]['EMA200_1h']
                    else:
                        session_log += f"{symbol}: Нет данных 1H\n"
                        continue

                    if last_row['ADX'] > 20 and last_row['volatility'] > 0.002 and last_row['volume'] > last_row['volume_mean'] and abs(last_row['CCI']) > 100:
                        if last_row['EMA50'] > last_row['EMA200'] and last_row['close'] > last_row['EMA200'] and last_row['close'] > ema200_1h:
                            side = 'BUY'
                            take_profit = entry_price * 1.007
                            stop_loss = entry_price * 0.997
                            await send_telegram_message(f"\U0001F4B0 Сигнал: {symbol}\nНаправление: LONG\nЦена входа: {entry_price:.4f}\nTP: {take_profit:.4f}\nSL: {stop_loss:.4f}")
                            session_log += f"{symbol}: Сигнал на LONG\n"
                        elif last_row['EMA50'] < last_row['EMA200'] and last_row['close'] < last_row['EMA200'] and last_row['close'] < ema200_1h:
                            side = 'SELL'
                            take_profit = entry_price * 0.993
                            stop_loss = entry_price * 1.003
                            await send_telegram_message(f"\U0001F4B0 Сигнал: {symbol}\nНаправление: SHORT\nЦена входа: {entry_price:.4f}\nTP: {take_profit:.4f}\nSL: {stop_loss:.4f}")
                            session_log += f"{symbol}: Сигнал на SHORT\n"
                    else:
                        session_log += f"{symbol}: Условия не подходят\n"

                except Exception as ex:
                    session_log += f"{symbol}: Ошибка {str(ex)}\n"

            await send_telegram_message(session_log)
            await asyncio.sleep(300)

        except Exception as e:
            await send_telegram_message(f"⚠️ Ошибка: {str(e)}")
            await asyncio.sleep(300)

# Запуск
symbols = ['ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LTCUSDT', 'ADAUSDT', 'BNBUSDT', 'DOGEUSDT', 'AVAXUSDT']
asyncio.run(signal_bot(symbols))
