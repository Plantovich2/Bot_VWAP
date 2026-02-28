# ==========================================================
# IMPORTS
# ==========================================================
import time
import requests
import pandas as pd
import numpy as np
import threading
from datetime import datetime as dt, timezone
from flask import Flask
from twilio.rest import Client

app = Flask(__name__)

# ==========================================================
# COLORES ANSI
# ==========================================================
RED = "\033[91m"
GREEN = "\033[92m"
PINK = "\033[95m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# ==========================================================
# TWILIO CONFIG
# ==========================================================
import os

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

send_whatsapp("✅ VWAP alerts running")

# ==========================================================
# CONFIG
# ==========================================================
SYMBOL = "BTC-USDT"
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
    end = 23 * 60 + 20
    return start <= minutes <= end

# ==========================================================
# DATA (OKX)
# ==========================================================
def get_klines(interval):

    interval_map = {
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1H",
        "4h": "4H"
    }

    url = "https://www.okx.com/api/v5/market/candles"
    params = {
        "instId": SYMBOL,
        "bar": interval_map[interval],
        "limit": LIMIT
    }

    r = requests.get(url, params=params)
    data = r.json()["data"]

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "_","_","_"
    ])

    df["time"] = pd.to_datetime(df["time"].astype(int), unit="ms", utc=True)
    df[["open","high","low","close","volume"]] = \
        df[["open","high","low","close","volume"]].astype(float)

    df = df.sort_values("time")

    return df

def get_current_price():
    url = "https://www.okx.com/api/v5/market/ticker"
    params = {"instId": SYMBOL}
    r = requests.get(url, params=params)
    return float(r.json()["data"][0]["last"])

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
# LOOP PRINCIPAL EN THREAD (Render-friendly)
# ==========================================================
def trading_loop():

    global position, entry_price, last_forced_close_date, intraday_trades

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

            rsi_3m = rsi_tv(df_3m["close"]).iloc[-1]
            rsi_5m = rsi_tv(get_klines("5m")["close"]).iloc[-1]
            rsi_15m = rsi_tv(get_klines("15m")["close"]).iloc[-1]
            rsi_1h = rsi_tv(get_klines("1h")["close"]).iloc[-1]
            rsi_4h = rsi_tv(get_klines("4h")["close"]).iloc[-1]

            print(f"{YELLOW}RSI → 4H: {rsi_4h:.2f} | 1H: {rsi_1h:.2f} | 15m: {rsi_15m:.2f} | 5m: {rsi_5m:.2f} | 3m: {rsi_3m:.2f}{RESET}\n")
            print(f"BTC Actual: {price:.2f}")
            print(f"VWAP: {last['vwap']:.2f}\n")

            print(f"{PINK}Upper1 (SH entry): {last['upper1']:.2f}{RESET} | "
                  f"{RED}Upper2: {last['upper2']:.2f}{RESET} | "
                  f"{RED}Upper3 (SL Short): {last['upper3']:.2f}{RESET}")

            print(f"{CYAN}Lower1 (LG entry): {last['lower1']:.2f}{RESET} | "
                  f"{GREEN}Lower2: {last['lower2']:.2f}{RESET} | "
                  f"{GREEN}Lower3 (SL Long): {last['lower3']:.2f}{RESET}")

            time.sleep(180)

        except Exception as e:
            print("ERROR:", e)
            time.sleep(30)

# ==========================================================
# FLASK ROUTE
# ==========================================================
@app.route("/")
def home():
    return "VWAP BOT RUNNING"

# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":
    thread = threading.Thread(target=trading_loop)
    thread.start()
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
