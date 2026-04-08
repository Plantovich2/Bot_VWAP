# ==========================================================
# TEST BOT - BINANCE + TWILIO
# Abre posición → espera 30s → cierra → notifica
# ==========================================================

import time
import os
from binance.client import Client
from twilio.rest import Client as TwilioClient

# ==========================================================
# CONFIG BINANCE
# ==========================================================
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

binance = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
binance.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

SYMBOL = "BTCUSDT"
LEVERAGE = 10
USDT_SIZE = 20   # 🔥 Ajustado (antes 10)

# ==========================================================
# CONFIG TWILIO
# ==========================================================
ACCOUNT_SID = os.environ.get("ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
FROM_WHATSAPP = os.environ.get("FROM_WHATSAPP")
TO_WHATSAPP = os.environ.get("TO_WHATSAPP")

twilio = TwilioClient(ACCOUNT_SID, AUTH_TOKEN) if ACCOUNT_SID else None

def send_whatsapp(msg):
    if not twilio:
        print("[TWILIO OFF]", msg)
        return
    try:
        twilio.messages.create(
            body=msg,
            from_=FROM_WHATSAPP,
            to=TO_WHATSAPP
        )
    except Exception as e:
        print("Twilio error:", e)

# ==========================================================
# HELPERS
# ==========================================================
def get_price():
    return float(binance.futures_mark_price(symbol=SYMBOL)['markPrice'])

def get_qty(usdt):
    price = get_price()
    raw_qty = usdt / price

    # Ajuste step size BTCUSDT
    step_size = 0.001
    qty = (raw_qty // step_size) * step_size

    return round(qty, 3)

def set_leverage():
    try:
        binance.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        print(f"[LEVERAGE] x{LEVERAGE}")
    except Exception as e:
        print("Leverage error:", e)

# ==========================================================
# TRADING
# ==========================================================
def open_long():
    set_leverage()

    price = get_price()
    qty = get_qty(USDT_SIZE)

    print(f"[OPEN] Price: {price} | Qty: {qty}")

    order = binance.futures_create_order(
        symbol=SYMBOL,
        side="BUY",
        type="MARKET",
        quantity=qty
    )

    print("[ORDER OPEN RAW]", order)

    time.sleep(1)

    status = binance.futures_get_order(
        symbol=SYMBOL,
        orderId=order["orderId"]
    )

    print("[ORDER OPEN STATUS]", status)

    return qty, status.get("avgPrice")

def close_position():
    pos = binance.futures_position_information(symbol=SYMBOL)

    for p in pos:
        amt = float(p["positionAmt"])

        if amt != 0:
            side = "SELL" if amt > 0 else "BUY"

            print(f"[CLOSE] Amt: {amt} | Side: {side}")

            order = binance.futures_create_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=abs(amt),
                reduceOnly=True
            )

            print("[ORDER CLOSE RAW]", order)

            time.sleep(1)

            status = binance.futures_get_order(
                symbol=SYMBOL,
                orderId=order["orderId"]
            )

            print("[ORDER CLOSE STATUS]", status)

            return status.get("avgPrice")

# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":

    print("\n===== TEST BOT START =====")

    send_whatsapp("🚀 TEST BOT INICIADO")

    # OPEN
    qty, open_price = open_long()
    send_whatsapp(f"🟢 LONG OPEN\nQty: {qty}\nPrice: {open_price}")

    # WAIT
    print("⏳ Esperando 30 segundos...")
    time.sleep(30)

    # CLOSE
    close_price = close_position()
    send_whatsapp(f"❌ POSITION CLOSED\nPrice: {close_price}")

    print("===== TEST BOT END =====")