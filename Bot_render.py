# ==========================================================
# BOT avisa entradas en RSI 26/82 de 30 minutos 
# (MISMA LÓGICA - SOLO SE AGREGA EJECUCIÓN BINANCE)
# ==========================================================

# ==========================================================
# IMPORTS
# ==========================================================
import sys
import time
import requests
import pandas as pd
import numpy as np
import threading
import os
import re
from datetime import datetime as dt, timezone
from flask import Flask
from twilio.rest import Client
from collections import deque

# 🔥 BINANCE
from binance.client import Client as BinanceClient

app = Flask(__name__)

# ==========================================================
# LOG CAPTURE (IDÉNTICO)
# ==========================================================
log_buffer = deque(maxlen=1000)

class DualOutput:
    def __init__(self, original):
        self.original = original

    def write(self, text):
        self.original.write(text)
        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        log_buffer.append(clean)

    def flush(self):
        self.original.flush()

sys.stdout = DualOutput(sys.stdout)

# ==========================================================
# COLORES (IDÉNTICO)
# ==========================================================
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
PINK = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"
RESET = "\033[0m"

# ==========================================================
# TWILIO (IDÉNTICO)
# ==========================================================
ACCOUNT_SID = os.environ.get("ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
FROM_WHATSAPP = os.environ.get("FROM_WHATSAPP")
TO_WHATSAPP = os.environ.get("TO_WHATSAPP")

client = Client(ACCOUNT_SID, AUTH_TOKEN) if ACCOUNT_SID and AUTH_TOKEN else None

def send_whatsapp(msg):
    if not client:
        return
    try:
        client.messages.create(body=msg, from_=FROM_WHATSAPP, to=TO_WHATSAPP)
    except Exception as e:
        print("Twilio ERROR:", e)

# ==========================================================
# 🔥 BINANCE CONFIG
# ==========================================================
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

binance = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET)
binance.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

BINANCE_SYMBOL = "BTCUSDT"

last_order_time = 0
ORDER_COOLDOWN = 10

def get_step_size():
    info = binance.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == BINANCE_SYMBOL:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return float(f["stepSize"])

STEP_SIZE = get_step_size()

def adjust_qty(qty):
    return round(qty - (qty % STEP_SIZE), 8)

def log_execution(action, price, qty):
    print(f"[BINANCE] {action} | Price: {price:.2f} | Qty: {qty}")

def open_position(side, usdt_size, leverage):
    global last_order_time

    if time.time() - last_order_time < ORDER_COOLDOWN:
        return

    try:
        binance.futures_change_leverage(symbol=BINANCE_SYMBOL, leverage=leverage)

        price = float(binance.futures_mark_price(symbol=BINANCE_SYMBOL)['markPrice'])
        qty = adjust_qty(usdt_size / price)

        order_side = "BUY" if side == "LONG" else "SELL"

        order = binance.futures_create_order(
            symbol=BINANCE_SYMBOL,
            side=order_side,
            type="MARKET",
            quantity=qty
        )

        last_order_time = time.time()

        log_execution(f"OPEN {side}", price, qty)
        print(order)

    except Exception as e:
        print("BINANCE OPEN ERROR:", e)

def close_position():
    try:
        pos = binance.futures_position_information(symbol=BINANCE_SYMBOL)

        for p in pos:
            amt = float(p["positionAmt"])

            if amt != 0:
                side_close = "SELL" if amt > 0 else "BUY"

                order = binance.futures_create_order(
                    symbol=BINANCE_SYMBOL,
                    side=side_close,
                    type="MARKET",
                    quantity=abs(amt),
                    reduceOnly=True
                )

                log_execution("CLOSE", float(p["entryPrice"]), abs(amt))
                print(order)

    except Exception as e:
        print("BINANCE CLOSE ERROR:", e)

def check_existing_position():
    try:
        pos = binance.futures_position_information(symbol=BINANCE_SYMBOL)
        for p in pos:
            if float(p["positionAmt"]) != 0:
                print("⚠️ Posición abierta en Binance detectada")
    except:
        pass

# ==========================================================
# CONFIG (IDÉNTICO)
# ==========================================================
SYMBOL = "BTC-USDT"
LIMIT = 500
position = None

RSI_LONG = 26.5
RSI_SHORT = 82.2

DCA_PERCENTS = [1.6, 3.4, 5.2]
SIZES = [200, 200, 280, 400]

TP_PERCENT = 1.8

prev_signal_long = False
prev_signal_short = False

# ==========================================================
# HORARIO (IDÉNTICO)
# ==========================================================
def trading_hours():
    now = dt.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    return (1*60+50) <= minutes <= (23*60+20)

# ==========================================================
# DATA (IDÉNTICO)
# ==========================================================
def get_klines(interval):
    interval_map = {"3m":"3m","5m":"5m","15m":"15m","30m":"30m","1h":"1H","4h":"4H"}

    r = requests.get(
        "https://www.okx.com/api/v5/market/candles",
        params={"instId": SYMBOL, "bar": interval_map[interval], "limit": LIMIT}
    )

    data = r.json()["data"]

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume","_","_","_"
    ])

    df["time"] = pd.to_datetime(df["time"].astype(int), unit="ms", utc=True)
    df[["open","high","low","close","volume"]] = \
        df[["open","high","low","close","volume"]].astype(float)

    return df.sort_values("time")

def get_current_price():
    r = requests.get(
        "https://www.okx.com/api/v5/market/ticker",
        params={"instId": SYMBOL}
    )
    return float(r.json()["data"][0]["last"])

# ==========================================================
# RSI (IDÉNTICO)
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
# VWAP (IDÉNTICO)
# ==========================================================
def vwap_continuo(df):
    df = df.copy()

    df["hl2"] = (df["high"] + df["low"]) / 2
    df["pv"] = df["hl2"] * df["volume"]
    df["pv2"] = df["volume"] * (df["hl2"] ** 2)

    df["cum_pv"] = df["pv"].cumsum()
    df["cum_vol"] = df["volume"].cumsum()
    df["cum_pv2"] = df["pv2"].cumsum()

    df["vwap"] = df["cum_pv"] / df["cum_vol"]

    variance = (df["cum_pv2"] / df["cum_vol"]) - (df["vwap"] ** 2)
    variance = variance.clip(lower=0)
    df["dev"] = np.sqrt(variance)

    df["upper1"] = df["vwap"] + df["dev"]
    df["upper2"] = df["vwap"] + df["dev"] * 2
    df["upper3"] = df["vwap"] + df["dev"] * 3

    df["lower1"] = df["vwap"] - df["dev"]
    df["lower2"] = df["vwap"] - df["dev"] * 2
    df["lower3"] = df["vwap"] - df["dev"] * 3

    return df

# ==========================================================
# PROMEDIO (IDÉNTICO)
# ==========================================================
def calculate_avg_price(entries):
    total = sum(e["price"] * e["size"] for e in entries)
    size = sum(e["size"] for e in entries)
    return total / size if size else 0

# ==========================================================
# LOOP (SOLO SE AGREGAN LLAMADAS BINANCE)
# ==========================================================
def trading_loop():

    global position, prev_signal_long, prev_signal_short

    while True:
        try:
            print("\n" + "="*60)
            print(dt.now(timezone.utc))
            print("PAR:", SYMBOL)

            df = vwap_continuo(get_klines("3m"))
            last = df.iloc[-1]
            price = get_current_price()

            rsi_30m = rsi_tv(get_klines("30m")["close"]).iloc[-1]

            close_0 = df["close"].iloc[-1]
            close_1 = df["close"].iloc[-2]
            close_2 = df["close"].iloc[-3]

            long_zone = close_0 <= last["lower1"] and close_0 >= last["lower3"]
            short_zone = close_0 >= last["upper1"] and close_0 <= last["upper3"]

            long_condition = long_zone and rsi_30m <= RSI_LONG
            short_condition = short_zone and rsi_30m >= RSI_SHORT

            enter_long = prev_signal_long
            enter_short = prev_signal_short

            prev_signal_long = long_condition
            prev_signal_short = short_condition

            allowed = trading_hours()

            # ENTRADAS
            if position is None and allowed:

                if enter_long:
                    position = {
                        "side": "LONG",
                        "entries": [{"price": price, "size": SIZES[0]}],
                        "dca_done": [False]*3
                    }
                    send_whatsapp(f"🟢 LONG BTC\nEntrada: {price:.2f}")
                    open_position("LONG", 10, 10)

                elif enter_short:
                    position = {
                        "side": "SHORT",
                        "entries": [{"price": price, "size": SIZES[0]}],
                        "dca_done": [False]*3
                    }
                    send_whatsapp(f"🔴 SHORT BTC\nEntrada: {price:.2f}")
                    open_position("SHORT", 10, 10)

            # LONG
            elif position and position["side"] == "LONG":

                avg = calculate_avg_price(position["entries"])
                pnl = (price - avg) / avg * 100

                dca_prices = [
                    avg * (1 - DCA_PERCENTS[0]/100),
                    avg * (1 - DCA_PERCENTS[1]/100),
                    avg * (1 - DCA_PERCENTS[2]/100),
                ]

                if not position["dca_done"][0] and close_0 <= dca_prices[0]:
                    position["entries"].append({"price": price, "size": SIZES[1]})
                    position["dca_done"][0] = True
                    open_position("LONG", 20, 7)

                if not position["dca_done"][1] and close_1 <= dca_prices[1]:
                    position["entries"].append({"price": price, "size": SIZES[2]})
                    position["dca_done"][1] = True
                    open_position("LONG", 40, 5)

                if not position["dca_done"][2] and close_2 <= dca_prices[2]:
                    position["entries"].append({"price": price, "size": SIZES[3]})
                    position["dca_done"][2] = True
                    open_position("LONG", 80, 3)

                if price >= avg * (1 + TP_PERCENT/100):
                    close_position()
                    position = None

            # SHORT
            elif position and position["side"] == "SHORT":

                avg = calculate_avg_price(position["entries"])
                pnl = (avg - price) / avg * 100

                dca_prices = [
                    avg * (1 + DCA_PERCENTS[0]/100),
                    avg * (1 + DCA_PERCENTS[1]/100),
                    avg * (1 + DCA_PERCENTS[2]/100),
                ]

                if not position["dca_done"][0] and close_0 >= dca_prices[0]:
                    position["entries"].append({"price": price, "size": SIZES[1]})
                    position["dca_done"][0] = True
                    open_position("SHORT", 20, 7)

                if not position["dca_done"][1] and close_1 >= dca_prices[1]:
                    position["entries"].append({"price": price, "size": SIZES[2]})
                    position["dca_done"][1] = True
                    open_position("SHORT", 40, 5)

                if not position["dca_done"][2] and close_2 >= dca_prices[2]:
                    position["entries"].append({"price": price, "size": SIZES[3]})
                    position["dca_done"][2] = True
                    open_position("SHORT", 80, 3)

                if price <= avg * (1 - TP_PERCENT/100):
                    close_position()
                    position = None

            time.sleep(180)

        except Exception as e:
            print("ERROR:", e)
            time.sleep(30)

# ==========================================================
# FLASK (IDÉNTICO)
# ==========================================================
@app.route("/")
def home():
    return "<h2>Bot activo</h2><a href='/logs'>Ver logs</a>"

@app.route("/logs")
def logs():
    return f"<pre>{''.join(log_buffer)}</pre>"

# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":

    send_whatsapp("Iniciando...")

    check_existing_position()

    t = threading.Thread(target=trading_loop)
    t.daemon = True
    t.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)