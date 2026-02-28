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

sys.stdout.reconfigure(line_buffering=True)

# ==========================================================
# FLASK WEB SERVER (Render Free)
# ==========================================================
app = Flask(__name__)

@app.route("/")
def home():
    return "VWAP Bot activo 🚀"

@app.route("/status")
def status():
    return "Bot corriendo correctamente"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ==========================================================
# TWILIO CONFIG
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

# ==========================================================
# CONTROL HORARIO
# ==========================================================
def trading_hours():
    now = dt.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    return (1 * 60 + 50) <= minutes <= (23 * 60 + 20)

# ==========================================================
# DATA SAFE
# ==========================================================
def get_klines(interval):
    try:
        r = requests.get(BASE_URL, params={
            "symbol": SYMBOL,
            "interval": interval,
            "limit": LIMIT
        }, timeout=10)

        data = r.json()

        # ⚠️ Protección contra respuestas inválidas
        if not isinstance(data, list) or len(data) < 50:
            print(f"⚠️ Datos insuficientes en {interval}")
            return None

        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "_","_","_","_","_","_"
        ])

        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        df[["open","high","low","close","volume"]] = \
            df[["open","high","low","close","volume"]].astype(float)

        return df

    except Exception as e:
        print("KLINES ERROR:", e)
        return None

def get_current_price():
    try:
        r = requests.get(TICKER_URL, params={"symbol": SYMBOL}, timeout=10)
        return float(r.json()["price"])
    except:
        return None

# ==========================================================
# RSI
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
# VWAP
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
    df["lower1"] = df["vwap"] - 1.28 * df["dev"]

    return df

# ==========================================================
# LOOP PRINCIPAL
# ==========================================================
def run_bot():
    global position, entry_price

    send_startup_message()

    while True:
        try:
            now = dt.now(timezone.utc)
            print("=" * 90)
            print("🕒", now)
            print("PAR:", SYMBOL)

            df_3m = get_klines("3m")
            if df_3m is None:
                time.sleep(30)
                continue

            df_3m = vwap_daily(df_3m)

            # ⚠️ Protección crítica
            if len(df_3m) < 3:
                print("⚠️ No hay suficientes velas")
                time.sleep(30)
                continue

            last = df_3m.iloc[-1]
            prev = df_3m.iloc[-2]

            price = get_current_price()
            if price is None:
                time.sleep(30)
                continue

            rsi_3m = rsi_tv(df_3m["close"]).iloc[-1]

            print(f"RSI 3m: {rsi_3m:.2f}")
            print(f"Precio: {price:.2f}")
            print(f"VWAP: {last['vwap']:.2f}")

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
    threading.Thread(target=run_server).start()
    threading.Thread(target=run_bot).start()
