# ==========================================================
# IMPORTS
# ==========================================================
import time
import requests
import pandas as pd
import numpy as np
import threading
import os
from datetime import datetime as dt, timezone
from flask import Flask
from twilio.rest import Client
from collections import deque

app = Flask(__name__)

# ==========================================================
# LOG CAPTURE ROBUSTO
# ==========================================================
import sys
from collections import deque

log_buffer = deque(maxlen=1000)  # guarda últimos 1000 prints

class DualOutput:
    def __init__(self, original):
        self.original = original

    def write(self, text):
        self.original.write(text)
        if text.strip() != "":
            log_buffer.append(text)

    def flush(self):
        self.original.flush()

sys.stdout = DualOutput(sys.stdout)

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
# TWILIO CONFIG (ENV)
# ==========================================================
ACCOUNT_SID = os.environ.get("ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
FROM_WHATSAPP = os.environ.get("FROM_WHATSAPP")
TO_WHATSAPP = os.environ.get("TO_WHATSAPP")

client = None
if ACCOUNT_SID and AUTH_TOKEN:
    client = Client(ACCOUNT_SID, AUTH_TOKEN)

def send_whatsapp(msg):
    if not client:
        return
    try:
        client.messages.create(
            body=msg,
            from_=FROM_WHATSAPP,
            to=TO_WHATSAPP
        )
    except Exception as e:
        log(f"Twilio ERROR: {e}")

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
# DATA OKX
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
    df["upper2"] = df["vwap"] + 2.01 * df["dev"]
    df["upper3"] = df["vwap"] + 2.51 * df["dev"]
    df["lower1"] = df["vwap"] - 1.28 * df["dev"]
    df["lower2"] = df["vwap"] - 2.01 * df["dev"]
    df["lower3"] = df["vwap"] - 2.51 * df["dev"]

    return df

# ==========================================================
# LOOP PRINCIPAL
# ==========================================================

# ==========================================================
# LOOP PRINCIPAL
# ==========================================================
def trading_loop():

    global position, entry_price, last_forced_close_date, intraday_trades

    while True:
        try:
            now = dt.now(timezone.utc)

            print("\n" + "=" * 60 + "\n")
            print("PAR:", SYMBOL + "\n")

            df_3m = vwap_daily(get_klines("3m"))
            last = df_3m.iloc[-1]
            prev = df_3m.iloc[-2]
            price = get_current_price()

            rsi_3m = rsi_tv(df_3m["close"]).iloc[-1]
            rsi_5m = rsi_tv(get_klines("5m")["close"]).iloc[-1]
            rsi_15m = rsi_tv(get_klines("15m")["close"]).iloc[-1]
            rsi_1h = rsi_tv(get_klines("1h")["close"]).iloc[-1]
            rsi_4h = rsi_tv(get_klines("4h")["close"]).iloc[-1]

            print(f"{YELLOW}RSI 4H: {rsi_4h:.2f} | 1H: {rsi_1h:.2f} | 15m: {rsi_15m:.2f} | 5m: {rsi_5m:.2f} | 3m: {rsi_3m:.2f}{RESET}\n")
            print(f"BTC Actual: {price:.2f}\n")
            print(f"VWAP: {last['vwap']:.2f}\n")

            print(f"{PINK}Upper1 (SH entry): {last['upper1']:.2f}{RESET} | "
                  f"{RED}Upper2: {last['upper2']:.2f}{RESET} | "
                  f"{RED}Upper3 (SL Short): {last['upper3']:.2f}{RESET}")

            print(f"{CYAN}Lower1 (LG entry): {last['lower1']:.2f}{RESET} | "
                  f"{GREEN}Lower2: {last['lower2']:.2f}{RESET} | "
                  f"{GREEN}Lower3 (SL Long): {last['lower3']:.2f}{RESET}")

            # =========================
            # ORDENAR 8 NIVELES EN ORIGEN
            # =========================

            levels = [
                (f"{RED}Upper3 (SL Short): {last['upper3']:.2f}{RESET}", last['upper3']),
                (f"{RED}Upper2: {last['upper2']:.2f}{RESET}", last['upper2']),
                (f"{PINK}Upper1 (SH entry): {last['upper1']:.2f}{RESET}", last['upper1']),
                (f"{WHITE}BTC Actual: {price:.2f}{RESET}", price),
                (f"{YELLOW}VWAP: {last['vwap']:.2f}{RESET}", last['vwap']),
                (f"{CYAN}Lower1 (LG entry): {last['lower1']:.2f}{RESET}", last['lower1']),
                (f"{GREEN}Lower2: {last['lower2']:.2f}{RESET}", last['lower2']),
                (f"{GREEN}Lower3 (SL Long): {last['lower3']:.2f}{RESET}", last['lower3']),
            ]

            # Ordenar mayor a menor
            levels_sorted = sorted(levels, key=lambda x: x[1], reverse=True)

            print()  # línea en blanco

            for text, _ in levels_sorted:
                print(text)

            print()  # línea en blanco

            
            # ======================================================
            # CIERRE AUTOMÁTICO 00:00 UTC
            # ======================================================
            if (position is not None and now.hour == 0 and now.minute == 0
                and last_forced_close_date != now.date()):

                if position == "SHORT":
                    pnl = ((entry_price - price) / entry_price) * 100
                else:
                    pnl = ((price - entry_price) / entry_price) * 100

                send_whatsapp(f"⏰ CIERRE AUTOMÁTICO 00:00 UTC\n{position} BTC\nResultado: {pnl:.2f}%")
                intraday_trades.append(f"{position} cerrado 00:00 | {pnl:.2f}%")
                position = None
                entry_price = None
                last_forced_close_date = now.date()

            # ======================================================
            # SEÑALES
            # ======================================================
            allowed = trading_hours()

            short_signal = prev["close"] > prev["upper1"] and last["close"] <= last["upper1"]
            long_signal = prev["close"] < prev["lower1"] and last["close"] >= last["lower1"]

            if position is None and allowed:

                if short_signal:
                    position = "SHORT"
                    entry_price = last["close"]
                    intraday_trades.append(f"SHORT abierto {entry_price:.2f}")
                    send_whatsapp(f"🔴 SHORT BTC\nEntrada: {entry_price:.2f}")

                elif long_signal:
                    position = "LONG"
                    entry_price = last["close"]
                    intraday_trades.append(f"LONG abierto {entry_price:.2f}")
                    send_whatsapp(f"🟢 LONG BTC\nEntrada: {entry_price:.2f}")

            elif position == "SHORT":

                pnl = ((entry_price - price) / entry_price) * 100

                print(f"{RED}\nSHORT ACTIVO | PnL: {pnl:.2f}% | TP: {last['vwap']:.2f} | SL: {last['upper3']:.2f}{RESET}")

                if allowed and (price <= last["vwap"] or price >= last["upper3"]):
                    send_whatsapp(f"❌ CIERRE SHORT BTC\nPnL: {pnl:.2f}%")
                    intraday_trades.append(f"SHORT cerrado | {pnl:.2f}%")
                    position = None

            elif position == "LONG":

                pnl = ((price - entry_price) / entry_price) * 100

                print(f"{GREEN}\nLONG ACTIVO | PnL: {pnl:.2f}% | TP: {last['vwap']:.2f} | SL: {last['lower3']:.2f}{RESET}")

                if allowed and (price >= last["vwap"] or price <= last["lower3"]):
                    send_whatsapp(f"❌ CIERRE LONG BTC\nPnL: {pnl:.2f}%")
                    intraday_trades.append(f"LONG cerrado | {pnl:.2f}%")
                    position = None

            # ======================================================
            # TRADES INTRADIARIOS
            # ======================================================
            print("\nTRADES INTRADIARIOS:")
            if intraday_trades:
                for t in intraday_trades:
                    print("-", t)
            else:
                print("Sin trades hoy.")

            # ======================================================
            # COUNTDOWN
            # ======================================================

            print(f"\nSiguiente actualización en: 180 segundos\n")
            time.sleep(180)

        except Exception as e:
            print("ERROR:", e)
            time.sleep(30)

# ==========================================================
# ROUTES
# ==========================================================
@app.route("/")
def home():
    return "VWAP BOT RUNNING"

import re

@app.route("/logs")
def logs():
    content = "".join(log_buffer)

    # === Convertir ANSI a HTML ===
    ansi_to_html = {
        "\033[91m": '<span style="color:#ff4c4c;">',
        "\033[92m": '<span style="color:#00ff88;">',
        "\033[93m": '<span style="color:#ffd700;">',
        "\033[95m": '<span style="color:#ff79c6;">',
        "\033[96m": '<span style="color:#00e5ff;">',
        "\033[0m":  "</span>"
    }

    for ansi, html in ansi_to_html.items():
        content = content.replace(ansi, html)

    # =========================
    # FORMATEO RSI EN COLUMNA
    # =========================
    def format_rsi(match):
        line = match.group(0)
        parts = line.split("|")
        return "<br>".join(p.strip() for p in parts)

    content = re.sub(r'RSI → .*?</span>', format_rsi, content)

    # =========================
    # ORDENAR 8 NIVELES CONSERVANDO COLORES
    # =========================

    pattern = re.findall(
        r'(<span style="color:[^"]+;">)([^:]+):\s*([\d]+\.[\d]+)(</span>)',
        content
    )

    levels = []

    keywords = [
        "BTC Actual",
        "Upper1",
        "Upper2",
        "Upper3",
        "VWAP",
        "Lower1",
        "Lower2",
        "Lower3"
    ]

    for start, name, value, end in pattern:
        if any(k in name for k in keywords):
            try:
                levels.append({
                    "value": float(value),
                    "html": f"{start}{name}: {value}{end}"
                })
            except:
                pass

    if len(levels) >= 8:

        # Ordenar mayor a menor
        levels_sorted = sorted(levels, key=lambda x: x["value"], reverse=True)

        # Construir bloque en columna con colores originales
        ordered_block = "<br>".join(level["html"] for level in levels_sorted)

        # Reemplazar bloque original completo
        content = re.sub(
            r'(<span style="color:[^"]+;">[^<]+:</span>\s*[\d]+\.[\d]+\s*){8}',
            ordered_block,
            content,
            count=1
        )
        
    html = f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="60">
        <style>
            body {{
                background-color: #0d1117;
                color: #e6edf3;
                font-family: monospace;
                white-space: pre-wrap;
                padding: 20px;
                margin: 0;
            }}
        </style>
    </head>
    <body>
{content}
        <script>
            window.scrollTo(0, document.body.scrollHeight);
        </script>
    </body>
    </html>
    """

    return html
# ==========================================================
# START
# ==========================================================
if __name__ == "__main__":
    thread = threading.Thread(target=trading_loop)
    thread.daemon = True
    thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)




















