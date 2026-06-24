"""
Crypto Trading Bot - RSI Auto Trader (With Active Live Logs & Hourly Heartbeat)
========================================================================
"""

import os
import time
import logging
import requests
import threading
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta

# ──────────────────────────────────────────────
# ⚙️ الإعدادات الأساسية
# ──────────────────────────────────────────────
SYMBOLS_FILE = "symbols.txt"

DEFAULT_BASE_SYMBOLS = [
    "WLD", "VANA", "BIO", "AIXBT", "S", "GPS", "SHELL", "IMX", "BMT", "NIL", 
    "XVG", "APE", "AMP", "ADA", "AGLD", "SCR", "POL", "KAIA", "BANANA", "ME", 
    "ARB", "WAXP", "VANRY", "POLYX", "DOT", "GRT", "PHA", "BAND", "LINK", "ZIL", 
    "GAS", "APT", "VET", "TWT", "FIL", "MOVR", "GMT", "OP", "RIF", "ENS", 
    "DIA", "ROSE", "QNT", "POWR", "RLC", "ZEN", "CELR", "FIDA", "SEI", "FET", 
    "LPT", "IOTA", "LTC", "RVN", "CTSI", "TFUEL", "THETA", "CELO", "ICP", "SAND", 
    "SOL", "MANTRA", "XLM", "XRP", "AVAX", "ONE", "CFX", "TLM", "BTC", "IQ", 
    "BCH", "AVA", "MEGA", "EURI", "ETC", "BAT", "HBAR", "PORTAL", "CHZ", "CKB", 
    "CHR", "ID", "CTK", "DUSK", "ARPA", "KAITO", "ENJ", "HIVE", "GTC", "2Z", 
    "ENSO", "KITE", "AT", "NIGHT", "EIGEN", "ZKP", "SENT", "LUMIA", "BREV", "ZAMA", 
    "ESP", "AZTEC", "QAIT", "STRAX", "ARX", "ATOM", "SUI", "NEAR", "TRX", "DOGE", 
    "ZEC", "TAO", "ETH", "OPG", "EDU", "DEXE", "HEI", "ALGO", "ACH", "INIT", 
    "TOWNS", "PROVE", "GALA", "SOMI", "OPEN", "HOLO", "LINEA", "OG", "XPL", "SXT", 
    "SOON", "SOPH", "LA", "SSV", "RONIN", "NEWT", "CGPT", "C", "ERA", "PARTI", 
    "WAL", "WCT", "HYPER", "ARKM", "ANKR", "ALT", "SIGN", "PUNDIX", "MAGIC"
]

SYMBOLS = []

INTERVAL         = Client.KLINE_INTERVAL_30MINUTE  # فريم النصف ساعة المعتمد
RSI_PERIOD       = 14
RSI_BUY          = 30
RSI_SELL         = 70
STOP_LOSS_PCT    = 0.02     # ستوب لوز 2% ماركت
TRAIL_PCT        = 0.005    # تتبع أرباح لصيق 0.5%
TRADE_AMOUNT     = 15.0
RESERVE_USDT     = 2.0      
CHECK_EVERY      = 60       # الفحص كل دقيقة
HEARTBEAT_INTERVAL = 3600   # تنبيه التليجرام الدوري (كل ساعة)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ──────────────────────────────────────────────
# 📂 إدارة الملف النصي (TXT)
# ──────────────────────────────────────────────
def load_symbols_from_txt():
    global SYMBOLS
    if not os.path.exists(SYMBOLS_FILE):
        with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(DEFAULT_BASE_SYMBOLS))
        SYMBOLS = [f"{s}USDT" for s in DEFAULT_BASE_SYMBOLS]
    else:
        with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip().upper() for line in f.readlines() if line.strip()]
        lines = list(dict.fromkeys(lines))
        SYMBOLS = [f"{s}USDT" for s in lines]

def save_symbols_to_txt():
    base_names = [s.replace("USDT", "") for s in SYMBOLS]
    with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(base_names))

# ──────────────────────────────────────────────
# Logging & Telegram
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("trading_bot.log", encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        log.error(f"❌ خطأ تيليغرام: {e}")

# ──────────────────────────────────────────────
# 💬 أوامر التليجرام الخلفية
# ──────────────────────────────────────────────
def telegram_command_listener(client):
    offset = 0
    while True:
        if not TELEGRAM_TOKEN:
            time.sleep(10)
            continue
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=10"
            r = requests.get(url, timeout=15).json()
            if "result" in r:
                for update in r["result"]:
                    offset = update["update_id"] + 1
                    if "message" in update and "text" in update["message"]:
                        text = update["message"]["text"].strip()
                        chat_id = str(update["message"]["chat"]["id"])
                        if chat_id != TELEGRAM_CHAT_ID:
                            continue
                        
                        global SYMBOLS
                        if text.startswith("/add "):
                            coin = text.replace("/add ", "").strip().upper()
                            symbol = f"{coin}USDT"
                            try:
                                client.get_symbol_info(symbol)
                                if symbol not in SYMBOLS:
                                    SYMBOLS.append(symbol)
                                    save_symbols_to_txt()
                                    send_telegram(f"✅ تم إضافة {coin} بنجاح للـ TXT والمراقبة.")
                            except:
                                send_telegram(f"❌ {coin} غير مدعومة فوري.")
                        elif text.startswith("/remove "):
                            coin = text.replace("/remove ", "").strip().upper()
                            symbol = f"{coin}USDT"
                            if symbol in SYMBOLS:
                                SYMBOLS.remove(symbol)
                                save_symbols_to_txt()
                                send_telegram(f"❌ تم حذف {coin} تماماً.")
                        elif text == "/list":
                            base_names = [s.replace("USDT", "") for s in SYMBOLS]
                            send_telegram(f"📋 القائمة الحالية المراقبة: {', '.join(base_names)}")
        except:
            pass
        time.sleep(1)

# ──────────────────────────────────────────────
# جلب المؤشرات وتنفيذ الصفقات
# ──────────────────────────────────────────────
def get_indicators(client, symbol):
    klines = client.get_klines(symbol=symbol, interval=INTERVAL, limit=100)
    closes = pd.Series([float(k[4]) for k in klines])
    rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
    price  = float(client.get_symbol_ticker(symbol=symbol)["price"])
    return {
        "rsi"      : round(rsi.iloc[-1], 2),
        "rsi_prev" : round(rsi.iloc[-2], 2),
        "price"    : price,
    }

def get_quantity(client, symbol, usdt_amount):
    info = client.get_symbol_info(symbol)
    price = float(client.get_symbol_ticker(symbol=symbol)["price"])
    step_size = None
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step_size = float(f["stepSize"])
    qty = usdt_amount / price
    if step_size:
        precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
        qty = round(qty - (qty % step_size), precision)
    return qty, price

def buy_market(client, symbol, usdt_amount):
    try:
        qty, price = get_quantity(client, symbol, usdt_amount)
        if qty <= 0: return None
        order = client.order_market_buy(symbol=symbol, quantity=qty)
        log.info(f"✅ شراء سوقي {symbol} | السعر: {price} | الكمية: {qty}")
        return {"qty": qty, "entry_price": price, "order_id": order["orderId"]}
    except:
        return None

def sell_market(client, symbol, qty):
    try:
        asset = symbol.replace("USDT", "")
        balance = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])
        if actual_qty <= 0: return None
        info = client.get_symbol_info(symbol)
        step_size = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break
        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            actual_qty = round(actual_qty - (actual_qty % step_size), precision)
        client.order_market_sell(symbol=symbol, quantity=actual_qty)
        price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        log.info(f"✅ بيع سوقي {symbol} | السعر: {price}")
        return price
    except:
        return None

# ──────────────────────────────────────────────
# 🚀 البوت الرئيسي
# ──────────────────────────────────────────────
def run_bot():
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    client     = Client(api_key, api_secret)

    load_symbols_from_txt()
    exchange_info  = client.get_exchange_info()
    active_symbols = {s["symbol"] for s in exchange_info["symbols"] if s["status"] == "TRADING"}
    
    global SYMBOLS
    SYMBOLS = [s for s in SYMBOLS if s in active_symbols]
    save_symbols_to_txt()

    telegram_thread = threading.Thread(target=telegram_command_listener, args=(client,), daemon=True)
    telegram_thread.start()

    open_trades = {}
    last_heartbeat = time.time()

    log.info(f"🚀 تم بدء تشغيل البوت بنجاح ومراقبة {len(SYMBOLS)} عملة فوري.")

    while True:
        # طباعة سطر تأكيدي في شاشة اللوق كل دقيقة لكي تطمئني أن الكود يتحرك ولا يتجمد
        log.info(f"🔄 جاري الفحص الدوري المستمر... عدد الصفقات الحالية: {len(open_trades)}")

        # ── إرسال إشعار التليجرام الدوري (البوت شغال 💚) كل ساعة بالتمام والكمال ──
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
            except:
                usdt_balance = 0.0
            total_slots = len(open_trades)
            heartbeat_msg = (
                f"💚 البوت شغال\n"
                f"💰 رصيد USDT المتاح: ${usdt_balance:.2f}\n"
                f"💼 صفقات مفتوحة حالياً: {total_slots}\n"
                f"👁️ يراقب {len(SYMBOLS)} عملة"
            )
            send_telegram(heartbeat_msg)
            last_heartbeat = time.time()

        # 1. ── إدارة الصفقات المفتوحة ──
        for symbol in list(open_trades.keys()):
            trade = open_trades[symbol]
            try:
                ind   = get_indicators(client, symbol)
                price = ind["price"]
                rsi   = ind["rsi"]
                coin  = symbol.replace("USDT", "")

                if not trade["trailing_active"] and rsi >= RSI_SELL:
                    trade["trailing_active"] = True
                    trade["highest_price"]   = price
                    trade["stop_loss"]       = round(price * (1 - TRAIL_PCT), 8)

                if trade["trailing_active"]:
                    if price > trade["highest_price"]:
                        trade["highest_price"] = price
                        trade["stop_loss"]     = round(price * (1 - TRAIL_PCT), 8)
                    elif price <= trade["stop_loss"]:
                        sell_market(client, symbol, trade["qty"])
                        send_telegram(f"🔴 بيع {coin}\n📉 RSI: {rsi}\n💰 السعر: {price}")
                        del open_trades[symbol]
                        continue
                else:
                    entry_sl = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                    if price <= entry_sl:
                        sell_market(client, symbol, trade["qty"])
                        send_telegram(f"🚨 ضرب ستوب لوز {coin}\n📉 السعر: {price}")
                        del open_trades[symbol]
                        continue
            except:
                pass

        # 2. ── فحص إشارات الشراء للعملات الفورية ──
        for symbol in list(SYMBOLS):
            if symbol in open_trades:
                continue
            try:
                ind = get_indicators(client, symbol)
                if ind["rsi_prev"] < RSI_BUY and ind["rsi"] >= RSI_BUY:
                    try:
                        usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
                    except:
                        usdt_balance = 0.0

                    if usdt_balance >= (TRADE_AMOUNT + RESERVE_USDT):
                        res = buy_market(client, symbol, TRADE_AMOUNT)
                        if res:
                            res["trailing_active"] = False
                            res["highest_price"]   = res["entry_price"]
                            open_trades[symbol]    = res
                            
                            coin_name = symbol.replace("USDT", "")
                            sl_value = round(res["entry_price"] * (1 - STOP_LOSS_PCT), 4)
                            total_slots = len(open_trades)
                            
                            notification = (
                                f"🟢 شراء {coin_name}\n"
                                f"📊 RSI: {ind['rsi_prev']} → {ind['rsi']}\n"
                                f"🛡️ Stop Loss: {sl_value}\n"
                                f"💼 صفقات مفتوحة: {total_slots}"
                            )
                            send_telegram(notification)
            except:
                continue

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    run_bot()
```[cite: 1]
