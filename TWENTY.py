import time
import pandas as pd
import numpy as np
import requests
import ta
import asyncio
import aiohttp
import telegram
from datetime import datetime
from binance.um_futures import UMFutures

# Параметры подключения
API_KEY = 'YOUR_API_KEY'
API_SECRET = 'YOUR_API_SECRET'
TELEGRAM_TOKEN = '7925464368:AAEmy9EL3z216z0y8ml4t7rulC1v3ZstQ0U'
TELEGRAM_CHAT_ID = '349999939'

# Binance Futures клиент
client = UMFutures(key=API_KEY, secret=API_SECRET)

# Telegram бот
bot = telegram.Bot(token=TELEGRAM_TOKEN)

symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LTCUSDT', 'ADAUSDT']
interval = '30m'
leverage = 10
risk_per_trade = 0.03

positions = {}
balance = None
wins = 0
losses = 0

async def send_telegram(message):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

async def get_balance():
    acc_info = client.balance()
    for asset in acc_info:
        if asset['asset'] == 'USDT':
            return float(asset['balance'])
    return 0

def get_klines(symbol, interval, limit=500):
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

async def trade_logic():
    global balance, wins, losses
    hourly_trend = {}

    for symbol in symbols:
        df_hour = get_klines(symbol, '1h', 200)
        df_hour['EMA200_1h'] = ta.trend.ema_indicator(df_hour['close'], window=200)
        hourly_trend[symbol] = df_hour

    while True:
        try:
            tasks = [asyncio.to_thread(get_klines, symbol, interval, 100) for symbol in symbols]
            data = await asyncio.gather(*tasks)

            for idx, symbol in enumerate(symbols):
                df = prepare_data(data[idx])
                last = df.iloc[-1]

                # Обновляем баланс
                balance = await get_balance()
                trade_amount = balance * 0.10

                hour_row = hourly_trend[symbol].loc[last.name.floor('h')]
                ema200_1h = hour_row['EMA200_1h']

                if symbol not in positions:
                    if last['ADX'] > 20 and last['volatility'] > 0.002 and last['volume'] > last['volume_mean'] and abs(last['CCI']) > 100:
                        if last['EMA50'] > last['EMA200'] and last['close'] > last['EMA200'] and last['close'] > ema200_1h:
                            qty = round(trade_amount * leverage / last['close'], 3)
                            client.new_order(symbol=symbol, side='BUY', type='MARKET', quantity=qty)
                            positions[symbol] = ('long', last['close'], trade_amount)
                        elif last['EMA50'] < last['EMA200'] and last['close'] < last['EMA200'] and last['close'] < ema200_1h:
                            qty = round(trade_amount * leverage / last['close'], 3)
                            client.new_order(symbol=symbol, side='SELL', type='MARKET', quantity=qty)
                            positions[symbol] = ('short', last['close'], trade_amount)

                else:
                    side, entry_price, alloc = positions[symbol]
                    take_profit = entry_price * (1.007 if side == 'long' else 0.993)
                    stop_loss = entry_price * (0.997 if side == 'long' else 1.003)

                    if (side == 'long' and last['low'] <= stop_loss) or (side == 'short' and last['high'] >= stop_loss):
                        wins += 0
                        losses += 1
                        del positions[symbol]
                    elif (side == 'long' and last['high'] >= take_profit) or (side == 'short' and last['low'] <= take_profit):
                        wins += 1
                        losses += 0
                        del positions[symbol]

            await send_telegram(f"Бот работает ✅\nБаланс: {balance:.2f} USDT\nТекущие сделки: {len(positions)}\nПобеды: {wins}, Поражения: {losses}")

        except Exception as e:
            print(f"Ошибка в торговле: {e}")
            await send_telegram(f"Ошибка в торговле: {e}")

        await asyncio.sleep(600)  # каждые 10 минут

async def main():
    await send_telegram("Бот запущен ✅")
    await trade_logic()

if __name__ == "__main__":
    asyncio.run(main())
