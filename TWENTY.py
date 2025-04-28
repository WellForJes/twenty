import pandas as pd
import numpy as np
import requests
import ta
import asyncio
import os
import time 
from binance.client import Client
import telegram

# Конфигурация
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
TELEGRAM_TOKEN = '7925464368:AAEmy9EL3z216z0y8ml4t7rulC1v3ZstQ0U'
TELEGRAM_CHAT_ID = '349999939'

client = Client(API_KEY, API_SECRET)
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# Получаем точности монет
exchange_info = client.futures_exchange_info()
precisions = {s['symbol']: s['quantityPrecision'] for s in exchange_info['symbols']}

# Функции

def get_binance_klines(symbol, interval, limit=500):
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

# Основная торговля
async def trading_bot(symbols, interval='30m'):
    balance = 30
    free_balance = balance
    positions = {}
    risk_per_trade = 0.10  # 10% от депозита на сделку

    await send_telegram_message("🤖 Бот запущен!")

    hourly_data = {}
    last_hourly_update = 0

    while True:
        try:
            # Обновляем EMA200 на 1h каждый час
            if time.time() - last_hourly_update > 3600:
                hourly_data = {}
                for symbol in symbols:
                    df_1h = get_binance_klines(symbol, interval='1h', limit=200)
                    df_1h['EMA200_1h'] = ta.trend.ema_indicator(df_1h['close'], window=200)
                    hourly_data[symbol] = df_1h
                last_hourly_update = time.time()

            session_log = "📈 Проверка рынка:\n"
            for symbol in symbols:
                try:
                    df = get_binance_klines(symbol, interval)
                    df = prepare_data(df)
                    if df.empty:
                        session_log += f"{symbol}: Ошибка данных\n"
                        continue

                    last_row = df.iloc[-1]
                    entry_price = last_row['close']
                    precision = precisions.get(symbol, 3)

                    ema_1h = hourly_data[symbol].iloc[-1]['EMA200_1h']

                    if symbol not in positions:
                        if (last_row['ADX'] > 20 and
                            last_row['volatility'] > 0.002 and
                            last_row['volume'] > last_row['volume_mean'] and
                            abs(last_row['CCI']) > 100):

                            if (last_row['EMA50'] > last_row['EMA200'] and last_row['close'] > last_row['EMA200'] and last_row['close'] > ema_1h):
                                side = 'BUY'
                                session_log += f"{symbol}: Условия для лонга ✅\n"
                            elif (last_row['EMA50'] < last_row['EMA200'] and last_row['close'] < last_row['EMA200'] and last_row['close'] < ema_1h):
                                side = 'SELL'
                                session_log += f"{symbol}: Условия для шорта ✅\n"
                            else:
                                session_log += f"{symbol}: Условия не подходят\n"
                                continue

                            trade_amount = free_balance * risk_per_trade
                            qty = round(trade_amount / entry_price, precision)

                            if qty * entry_price >= 5:
                                client.futures_create_order(
                                    symbol=symbol,
                                    side=side,
                                    type='MARKET',
                                    quantity=qty
                                )
                                await send_telegram_message(f"📈 Открыта позиция {symbol}: {side} {qty} по {entry_price}")

                                # Ставим реальный тейк и стоп
                                if side == 'BUY':
                                    take_profit_price = round(entry_price * 1.007, 5)
                                    stop_loss_price = round(entry_price * 0.997, 5)
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='SELL',
                                        type='TAKE_PROFIT_MARKET',
                                        stopPrice=take_profit_price,
                                        closePosition=True
                                    )
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='SELL',
                                        type='STOP_MARKET',
                                        stopPrice=stop_loss_price,
                                        closePosition=True
                                    )
                                else:
                                    take_profit_price = round(entry_price * 0.993, 5)
                                    stop_loss_price = round(entry_price * 1.003, 5)
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='BUY',
                                        type='TAKE_PROFIT_MARKET',
                                        stopPrice=take_profit_price,
                                        closePosition=True
                                    )
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='BUY',
                                        type='STOP_MARKET',
                                        stopPrice=stop_loss_price,
                                        closePosition=True
                                    )

                                free_balance -= trade_amount
                                positions[symbol] = (side, trade_amount)

                    else:
                        open_positions = client.futures_position_information(symbol=symbol)
                        for pos in open_positions:
                            if float(pos['positionAmt']) == 0:
                                side, trade_amount = positions[symbol]
                                realized = float(pos['unrealizedProfit'])
                                fee = trade_amount * 0.0008  # учёт комиссии 0.04% вход + 0.04% выход

                                if realized >= 0:
                                    balance += trade_amount * 0.30 - fee
                                    await send_telegram_message(f"✅ Тейк профит по {symbol}! +{trade_amount * 0.30 - fee:.2f} USD")
                                else:
                                    balance -= trade_amount * 0.10 + fee
                                    await send_telegram_message(f"❌ Стоп лосс по {symbol}! -{trade_amount * 0.10 + fee:.2f} USD")
                                free_balance = balance

                                if symbol in positions:
                                    del positions[symbol]

                except Exception as ex:
                    session_log += f"{symbol}: Ошибка {str(ex)}\n"

            session_log += f"\nБаланс: {balance:.2f} USD | Свободный баланс: {free_balance:.2f} USD"
            await send_telegram_message(session_log)
            await asyncio.sleep(300)

        except Exception as e:
            await send_telegram_message(f"🔥 Ошибка в боте: {str(e)}")
            await asyncio.sleep(300)

# Запуск
symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LTCUSDT', 'ADAUSDT']
asyncio.run(trading_bot(symbols))
