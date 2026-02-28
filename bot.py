# ==========================================================
# IMPORTS
# ==========================================================
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime as dt, timezone
from twilio.rest import Client

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

# ==========================================================
# MENSAJE DE INICIO
# ==========================================================
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

# >>> NUEVO: registro trades intradiarios
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

        # RSI MTF
        rsi_3m  = rsi_tv(df_3m["close"]).iloc[-1]
        rsi_5m  = rsi_tv(get_klines("5m")["close"]).iloc[-1]   # <<< NUEVO
        rsi_15m = rsi_tv(get_klines("15m")["close"]).iloc[-1]
        rsi_1h  = rsi_tv(get_klines("1h")["close"]).iloc[-1]
        rsi_4h  = rsi_tv(get_klines("4h")["close"]).iloc[-1]

        print(f"{YELLOW}RSI → 4H: {rsi_4h:.2f} | 1H: {rsi_1h:.2f} | 15m: {rsi_15m:.2f} | 5m: {rsi_5m:.2f} | 3m: {rsi_3m:.2f}{RESET}\n")

        print(f"BTC Actual: {price:.2f}")
        print(f"VWAP: {last['vwap']:.2f}\n")

        print(f"{PINK}Upper1 (SH entry): {last['upper1']:.2f}{RESET} | "
              f"{RED}Upper2: {last['upper2']:.2f}{RESET} | "
              f"{RED}Upper3 (SL Short): {last['upper3']:.2f}{RESET}")

        print(f"{CYAN}Lower1 (LG entry): {last['lower1']:.2f}{RESET} | "
              f"{GREEN}Lower2: {last['lower2']:.2f}{RESET} | "
              f"{GREEN}Lower3 (SL Long): {last['lower3']:.2f}{RESET}")

        # ======================================================
        # CIERRE AUTOMÁTICO 00:00 UTC
        # ======================================================
        if (position is not None and
            now.hour == 0 and
            now.minute == 0 and
            last_forced_close_date != now.date()):

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
        long_signal  = prev["close"] < prev["lower1"] and last["close"] >= last["lower1"]

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
        # >>> PRINT TRADES INTRADIARIOS (TEXTO SIMPLE)
        # ======================================================
        print("\nTRADES INTRADIARIOS:")
        if intraday_trades:
            for t in intraday_trades:
                print("-", t)
        else:
            print("Sin trades hoy.")

        # ======================================================
        # >>> CUENTA REGRESIVA HASTA SIGUIENTE LOOP
        # ======================================================
        for i in range(180, 0, -1):
            if i % 30 == 0:  # imprime cada 30 segundos
              print(f"⏳ Próxima actualización en {i} segundos")
            time.sleep(1)

    except Exception as e:
        print("ERROR:", e)
        time.sleep(30)