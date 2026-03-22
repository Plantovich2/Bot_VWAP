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

app = Flask(__name__)

# ==========================================================
# LOG CAPTURE
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
# COLORES
# ==========================================================
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
PINK = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"
RESET = "\033[0m"

# ==========================================================
# TWILIO
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
# CONFIG
# ==========================================================
SYMBOL = "BTC-USDT"
LIMIT = 500
position = None

# ==========================================================
# HORARIO
# ==========================================================
def trading_hours():
    now = dt.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    return (1*60+50) <= minutes <= (23*60+20)

# ==========================================================
# DATA
# ==========================================================
def get_klines(interval):
    interval_map = {"3m":"3m","5m":"5m","15m":"15m","1h":"1H","4h":"4H"}

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
# VWAP CONTINUO
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

    df["upper1"] = df["vwap"] + 1.28 * df["dev"]
    df["upper3"] = df["vwap"] + 2.51 * df["dev"]
    df["lower1"] = df["vwap"] - 1.28 * df["dev"]
    df["lower3"] = df["vwap"] - 2.51 * df["dev"]

    return df

# ==========================================================
# PROMEDIO
# ==========================================================
def calculate_avg_price(entries):
    total = sum(e["price"] * e["size"] for e in entries)
    size = sum(e["size"] for e in entries)
    return total / size if size else 0

# ==========================================================
# LOOP
# ==========================================================
def trading_loop():

    global position

    while True:
        try:
            print("\n" + "="*60)
            print(dt.now(timezone.utc))
            print("PAR:", SYMBOL)

            df = vwap_continuo(get_klines("3m"))
            last = df.iloc[-1]

            price = get_current_price()

            # =========================
            # RSI MULTI TF (RESTAURADO)
            # =========================
            rsi_3m = rsi_tv(df["close"]).iloc[-1]
            rsi_5m = rsi_tv(get_klines("5m")["close"]).iloc[-1]
            rsi_15m = rsi_tv(get_klines("15m")["close"]).iloc[-1]
            rsi_1h = rsi_tv(get_klines("1h")["close"]).iloc[-1]
            rsi_4h = rsi_tv(get_klines("4h")["close"]).iloc[-1]

            print(f"{YELLOW}RSI 4H: {rsi_4h:.2f} | 1H: {rsi_1h:.2f} | 15m: {rsi_15m:.2f} | 5m: {rsi_5m:.2f} | 3m: {rsi_3m:.2f}{RESET}")

            # =========================
            # NIVELES (RESTAURADO)
            # =========================
            levels = [
                ("Upper3", last['upper3'], RED),
                ("Upper1", last['upper1'], PINK),
                ("Precio", price, WHITE),
                ("VWAP", last['vwap'], YELLOW),
                ("Lower1", last['lower1'], CYAN),
                ("Lower3", last['lower3'], GREEN),
            ]

            print()
            for name, value, color in sorted(levels, key=lambda x: x[1], reverse=True):
                print(f"{color}{name}: {value:.2f}{RESET}")
            print()

            allowed = trading_hours()

            # =========================
            # ENTRADAS (SIN CAMBIOS)
            # =========================
            if position is None and allowed:

                if price < last["lower1"] and rsi_3m < 26:
                    position = {
                        "side": "LONG",
                        "entries": [{"price": price, "size": 200}],
                        "dca_triggered": [False]*3,
                        "tp_done": False
                    }
                    send_whatsapp(f"🟢 LONG BTC\nEntrada: {price:.2f}")

                elif price > last["upper1"] and rsi_3m > 72.2:
                    position = {
                        "side": "SHORT",
                        "entries": [{"price": price, "size": 200}],
                        "dca_triggered": [False]*3,
                        "tp_done": False
                    }
                    send_whatsapp(f"🔴 SHORT BTC\nEntrada: {price:.2f}")

            # =========================
            # LONG
            # =========================
            elif position and position["side"] == "LONG":

                avg = calculate_avg_price(position["entries"])
                pnl = (price - avg) / avg * 100
                total_size = sum(e["size"] for e in position["entries"])

                print(f"{GREEN}LONG | Avg: {avg:.2f} | PnL: {pnl:.2f}% | Size: {total_size}{RESET}")

                if not position["dca_triggered"][0] and pnl <= -1.8:
                    position["entries"].append({"price": price, "size": 200})
                    position["dca_triggered"][0] = True
                    send_whatsapp(f"📉 DCA1 LONG\n{price:.2f}")

                if not position["dca_triggered"][1] and pnl <= -3.4:
                    position["entries"].append({"price": price, "size": 280})
                    position["dca_triggered"][1] = True
                    send_whatsapp(f"📉 DCA2 LONG\n{price:.2f}")

                if not position["dca_triggered"][2] and pnl <= -5.2:
                    position["entries"].append({"price": price, "size": 400})
                    position["dca_triggered"][2] = True
                    send_whatsapp(f"📉 DCA3 LONG\n{price:.2f}")

                if not position["tp_done"] and price >= last["vwap"]:
                    print(f"{YELLOW}TP PARCIAL{RESET}")
                    position["tp_done"] = True

                if any(position["dca_triggered"]) and pnl > 0.3:
                    send_whatsapp(f"❌ CIERRE LONG (BE)\n{pnl:.2f}%")
                    position = None

                elif price >= last["upper1"]:
                    send_whatsapp(f"❌ CIERRE LONG (TP)\n{pnl:.2f}%")
                    position = None

                elif price <= last["lower3"]:
                    send_whatsapp(f"❌ CIERRE LONG (SL)\n{pnl:.2f}%")
                    position = None

            # =========================
            # SHORT
            # =========================
            elif position and position["side"] == "SHORT":

                avg = calculate_avg_price(position["entries"])
                pnl = (avg - price) / avg * 100
                total_size = sum(e["size"] for e in position["entries"])

                print(f"{RED}SHORT | Avg: {avg:.2f} | PnL: {pnl:.2f}% | Size: {total_size}{RESET}")

                if not position["dca_triggered"][0] and pnl <= -1.8:
                    position["entries"].append({"price": price, "size": 200})
                    position["dca_triggered"][0] = True
                    send_whatsapp(f"📉 DCA1 SHORT\n{price:.2f}")

                if not position["dca_triggered"][1] and pnl <= -3.4:
                    position["entries"].append({"price": price, "size": 280})
                    position["dca_triggered"][1] = True
                    send_whatsapp(f"📉 DCA2 SHORT\n{price:.2f}")

                if not position["dca_triggered"][2] and pnl <= -5.2:
                    position["entries"].append({"price": price, "size": 400})
                    position["dca_triggered"][2] = True
                    send_whatsapp(f"📉 DCA3 SHORT\n{price:.2f}")

                if not position["tp_done"] and price <= last["vwap"]:
                    print(f"{YELLOW}TP PARCIAL{RESET}")
                    position["tp_done"] = True

                if any(position["dca_triggered"]) and pnl > 0.3:
                    send_whatsapp(f"❌ CIERRE SHORT (BE)\n{pnl:.2f}%")
                    position = None

                elif price <= last["lower1"]:
                    send_whatsapp(f"❌ CIERRE SHORT (TP)\n{pnl:.2f}%")
                    position = None

                elif price >= last["upper3"]:
                    send_whatsapp(f"❌ CIERRE SHORT (SL)\n{pnl:.2f}%")
                    position = None

            time.sleep(180)

        except Exception as e:
            print("ERROR:", e)
            time.sleep(30)

# ==========================================================
# FLASK
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
    t = threading.Thread(target=trading_loop)
    t.daemon = True
    t.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
