import time
import os
from binance.client import Client     
from twilio.rest import Client as TwilioClient

# ==========================================================
# CLIENTE SIN PING (FIX BLOQUEO)
# ==========================================================
class NoPingClient(Client):
    def ping(self):
        return {"msg": "pong"}  # override → no llama a Binance
binance = NoPingClient(BINANCE_API_KEY, BINANCE_API_SECRET, testnet=True)

binance.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
     
# ==========================================================
# CONFIG
# ==========================================================
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

SYMBOL = "BTCUSDT"
LEVERAGE = 10
USDT_SIZE = 20

binance = NoPingClient(BINANCE_API_KEY, BINANCE_API_SECRET)

# ==========================================================
# TWILIO
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
        twilio.messages.create(body=msg, from_=FROM_WHATSAPP, to=TO_WHATSAPP)
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
    step = 0.001
    qty = (raw_qty // step) * step
    return round(qty, 3)

def set_leverage():
    try:
        binance.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    except Exception as e:
        print("Leverage error:", e)

# ==========================================================
# TRADING
# ==========================================================
def open_long():
    set_leverage()

    price = get_price()
    qty = get_qty(USDT_SIZE)

    print("[OPEN]", price, qty)

    order = binance.futures_create_order(
        symbol=SYMBOL,
        side="BUY",
        type="MARKET",
        quantity=qty
    )

    time.sleep(1)

    status = binance.futures_get_order(
        symbol=SYMBOL,
        orderId=order["orderId"]
    )

    print("[OPEN STATUS]", status)
    return qty, status.get("avgPrice")

def close_position():
    pos = binance.futures_position_information(symbol=SYMBOL)

    for p in pos:
        amt = float(p["positionAmt"])
        if amt != 0:
            side = "SELL" if amt > 0 else "BUY"

            order = binance.futures_create_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=abs(amt),
                reduceOnly=True
            )

            time.sleep(1)

            status = binance.futures_get_order(
                symbol=SYMBOL,
                orderId=order["orderId"]
            )

            print("[CLOSE STATUS]", status)
            return status.get("avgPrice")

# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":

    print("START TEST BOT")

    send_whatsapp("🚀 TEST START")

    qty, open_price = open_long()
    send_whatsapp(f"🟢 OPEN\nQty: {qty}\nPrice: {open_price}")

    time.sleep(30)

    close_price = close_position()
    send_whatsapp(f"❌ CLOSE\nPrice: {close_price}")

    print("END")
