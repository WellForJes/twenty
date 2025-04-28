import pandas as pd
import numpy as np
import requests
import ta
import asyncio
import os
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

    # Заранее получаем EMA200 на 1h
    for symbol in symbols:
        df_1h = get_binance_klines(symbol, interval='1h', limit=200)
        df_1h['EMA200_1h'] = ta.trend.ema_indicator(df_1h['close'], window=200)
        hourly_data[symbol] = df_1h

    while True:
        try:
            for symbol in symbols:
                try:
                    df = get_binance_klines(symbol, interval)
                    df = prepare_data(df)
                    if df.empty:
                        continue

                    last_row = df.iloc[-1]
                    entry_price = last_row['close']
                    precision = precisions.get(symbol, 3)

                    # Получаем EMA200 с 1h
                    ema_1h = hourly_data[symbol].iloc[-1]['EMA200_1h']

                    if symbol not in positions:
                        if (last_row['ADX'] > 20 and
                            last_row['volatility'] > 0.002 and
                            last_row['volume'] > last_row['volume_mean'] and
                            abs(last_row['CCI']) > 100):

                            if (last_row['EMA50'] > last_row['EMA200'] and last_row['close'] > last_row['EMA200'] and last_row['close'] > ema_1h):
                                side = 'BUY'
                                stop_loss = entry_price * 0.90
                                take_profit = entry_price * 1.30
                            elif (last_row['EMA50'] < last_row['EMA200'] and last_row['close'] < last_row['EMA200'] and last_row['close'] < ema_1h):
                                side = 'SELL'
                                stop_loss = entry_price * 1.10
                                take_profit = entry_price * 0.70
                            else:
                                continue

                            trade_amount = free_balance * risk_per_trade
                            qty = round(trade_amount / entry_price, precision)

                            if qty * entry_price >= 5:
                                order = client.futures_create_order(
                                    symbol=symbol,
                                    side=side,
                                    type='MARKET',
                                    quantity=qty
                                )
                                await send_telegram_message(f"📈 Открыта позиция {symbol}: {side} {qty} по {entry_price}")

                                if side == 'BUY':
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='SELL',
                                        type='TAKE_PROFIT_MARKET',
                                        quantity=qty,
                                        stopPrice=take_profit,
                                        closePosition=True
                                    )
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='SELL',
                                        type='STOP_MARKET',
                                        quantity=qty,
                                        stopPrice=stop_loss,
                                        closePosition=True
                                    )
                                else:
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='BUY',
                                        type='TAKE_PROFIT_MARKET',
                                        quantity=qty,
                                        stopPrice=take_profit,
                                        closePosition=True
                                    )
                                    client.futures_create_order(
                                        symbol=symbol,
                                        side='BUY',
                                        type='STOP_MARKET',
                                        quantity=qty,
                                        stopPrice=stop_loss,
                                        closePosition=True
                                    )

                                free_balance -= trade_amount
                                positions[symbol] = (side, trade_amount)

                    else:
                        open_positions = client.futures_position_information(symbol=symbol)
                        for pos in open_positions:
                            if float(pos['positionAmt']) == 0:
                                side, trade_amount = positions[symbol]
                                if side == 'BUY':
                                    profit = trade_amount * 0.30
                                    loss = trade_amount * 0.10
                                else:
                                    profit = trade_amount * 0.30
                                    loss = trade_amount * 0.10

                                # Пытаемся определить по марже был профит или убыток
                                realized = float(pos['unrealizedProfit'])
                                if realized >= 0:
                                    balance += profit
                                    await send_telegram_message(f"✅ Тейк профит по {symbol}! +{profit:.2f} USD")
                                else:
                                    balance -= loss
                                    await send_telegram_message(f"❌ Стоп лосс по {symbol}! -{loss:.2f} USD")
                                free_balance = balance

                                if symbol in positions:
                                    del positions[symbol]

                except Exception as ex:
                    await send_telegram_message(f"⚠️ Ошибка по {symbol}: {str(ex)}")

            await asyncio.sleep(300)

        except Exception as e:
            await send_telegram_message(f"🔥 Ошибка в боте: {str(e)}")
            await asyncio.sleep(300)

# Запуск
symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LTCUSDT', 'ADAUSDT']
asyncio.run(trading_bot(symbols))
