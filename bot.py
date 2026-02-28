# ==========================================================
# IMPORTS
# ==========================================================
import time
import requests
import pandas as pd
import numpy as np
import threading
import os
import sys

from datetime import datetime as dt, timezone
from twilio.rest import Client
from flask import Flask

# Fuerza logs en tiempo real
sys.stdout.reconfigure(line_buffering=True)

# ==========================================================
# FLASK WEB SERVER (Requerido para Render Free)
# ==========================================================
app = Flask(__name__)

@app.route("/")
def home():
    return "VWAP Bot activo 🚀"

@app.route("/status")
def status():
    return "Bot corriendo correctamente"

def run_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ==========================================================
# TWILIO CONFIG (Variables de entorno)
# ==========================================================
ACCOUNT_SID = os.environ.get("ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
FROM_WHATSAPP = os.environ.get("FROM_WHATSAPP")
TO_WHATSAPP = os.environ.get("TO_WHATSAPP")

client = Client(ACCOUNT_SID, AUTH_TOKEN)

def send_whatsapp(msg):
    try:
        client.messages.create(
            body=msg,
            from_=FROM_WHATSAPP,
            to=TO_WHATSAPP
        )
    except Exception as e:
        print("Twilio ERROR:", e)

# ==========================================================
# MENSAJE DE INICIO
# ==========================================================
def send_startup_message():
    send_whatsapp("✅ VWAP alerts running")

# ==========================================================
# CONFIG
# ==========================================================
SYMBOL = "BTCUSDT"
BASE_URL = "https://api.binance.com/api/v3/klines"
TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
LIMIT = 500

position = None
entry_price = None
last_forced_close_date = None
intraday_trades = []

# ==========================================================
# CONTROL HORARIO
# ==========================================================
def trading_hours():
    now = dt.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    start = 1 * 60 + 50
    end   = 23 * 60 + 20
    return start <= minutes <= end

# ==========================================================
# DATA
# ==========================================================
def get_klines(interval):
    r = requests.get(BASE_URL, params={
        "symbol": SYMBOL,
        "interval": interval,
        "limit": LIMIT
    })
    data = r.json()

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "_","_","_","_","_","_"
    ])

    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df[["open","high","low","close","volume"]] = \
        df[["open","high","low","close","volume"]].astype(float)

    return df

def get_current_price():
    r = requests.get(TICKER_URL, params={"symbol": SYMBOL})
    return float(r.json()["price"])

# ==========================================================
# RSI WILDER
# ==========================================================
def rsi_tv(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ==========================================================
# VWAP + BANDAS
# ==========================================================
def vwap_daily(df):
    df = df.copy()
    df["date"] = df["time"].dt.date
    df["hl2"] = (df["high"] + df["low"]) / 2
    df["pv"] = df["hl2"] * df["volume"]
    df["pv2"] = df["volume"] * (df["hl2"] ** 2)

    g = df.groupby("date")

    cum_pv = g["pv"].cumsum()
    cum_vol = g["volume"].cumsum()
    cum_pv2 = g["pv2"].cumsum()

    df["vwap"] = cum_pv / cum_vol
    variance = (cum_pv2 / cum_vol) - (df["vwap"] ** 2)
    variance = variance.clip(lower=0)

    df["dev"] = np.sqrt(variance)

    df["upper1"] = df["vwap"] + 1.28 * df["dev"]
    df["upper2"] = df["vwap"] + 2.01 * df["dev"]
    df["upper3"] = df["vwap"] + 2.51 * df["dev"]

    df["lower1"] = df["vwap"] - 1.28 * df["dev"]
    df["lower2"] = df["vwap"] - 2.01 * df["dev"]
    df["lower3"] = df["vwap"] - 2.51 * df["dev"]

    return df

# ==========================================================
# LOOP PRINCIPAL
# ==========================================================
def run_bot():
    global position, entry_price, last_forced_close_date

    send_startup_message()

    while True:
        try:
            now = dt.now(timezone.utc)

            print("=" * 90)
            print("🕒", now)
            print("PAR:", SYMBOL)

            df_3m = vwap_daily(get_klines("3m"))
            last = df_3m.iloc[-1]
            prev = df_3m.iloc[-2]

            price = get_current_price()

            rsi_3m  = rsi_tv(df_3m["close"]).iloc[-1]
            rsi_5m  = rsi_tv(get_klines("5m")["close"]).iloc[-1]
            rsi_15m = rsi_tv(get_klines("15m")["close"]).iloc[-1]
            rsi_1h  = rsi_tv(get_klines("1h")["close"]).iloc[-1]
            rsi_4h  = rsi_tv(get_klines("4h")["close"]).iloc[-1]

            print(f"RSI → 4H:{rsi_4h:.2f} | 1H:{rsi_1h:.2f} | 15m:{rsi_15m:.2f} | 5m:{rsi_5m:.2f} | 3m:{rsi_3m:.2f}")
            print(f"Precio actual: {price:.2f}")
            print(f"VWAP: {last['vwap']:.2f}")

            # Señales
            allowed = trading_hours()
            short_signal = prev["close"] > prev["upper1"] and last["close"] <= last["upper1"]
            long_signal  = prev["close"] < prev["lower1"] and last["close"] >= last["lower1"]

            if position is None and allowed:
                if short_signal:
                    position = "SHORT"
                    entry_price = last["close"]
                    send_whatsapp(f"🔴 SHORT BTC\nEntrada: {entry_price:.2f}")

                elif long_signal:
                    position = "LONG"
                    entry_price = last["close"]
                    send_whatsapp(f"🟢 LONG BTC\nEntrada: {entry_price:.2f}")

            time.sleep(180)

        except Exception as e:
            print("ERROR:", e)
            time.sleep(30)

# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server)
    server_thread.start()

    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()