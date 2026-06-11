"""
Crypto Signal Bot - RSI 30/70 Alerts
======================================
يرسل تنبيه فقط عندما:
- RSI ينزل تحت 30 (شراء محتمل)
- RSI يطلع فوق 70 (بيع محتمل)
"""

import os
import time
import logging
import requests
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta

# ──────────────────────────────────────────────
# ⚙️ الإعدادات
# ──────────────────────────────────────────────
SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","AVAXUSDT","XRPUSDT",
    "ONEUSDT","TRXUSDT","ENJUSDT","DOTUSDT","CHZUSDT",
    "TRBUSDT","CFXUSDT","ARPASUDT","FTMUSDT","ADAUSDT",
    "LINKUSDT","MATICUSDT","DOGEUSDT","ATOMUSDT","LTCUSDT",
    "ALGOUSDT","VETUSDT","HBARUSDT","EOSUSDT","ETCUSDT",
    "XMRUSDT","STXUSDT","BEAMUSDT","FETUSDT","GRTUSDT",
    "ENSUSDT","IOTAUSDT","XLMUSDT","ZENUSDT","BATUSDT",
    "ZECUSDT","DASHUSDT","IOSTUSDT","LRCUSDT","BANDUSDT",
    "CELRUSDT","STORJUSDT","CKBUSDT","XTZUSDT","QTUMUSDT",
    "DGBUSDT","ZILUSDT","SNTUSDT","NKNUSDT","POWRUSDT",
    "BCHUSDT","OMGUSDT","MINAUSDT","NEARUSDT","ROSAUSDT",
    "APTUSDT","ARBUSDT","MASKUSDT","GMTUSDT","KLAYUSDT",
]

INTERVAL     = Client.KLINE_INTERVAL_15MINUTE
RSI_PERIOD   = 14
RSI_BUY      = 30
RSI_SELL     = 70
CHECK_EVERY  = 60

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8912210093:AAFV9iO98UMgIMGJomHmNTdwM31lCABuLt8")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1001003541055173")
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("signal_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
def send_telegram(message, token, chat_id):
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        log.error(f"❌ خطأ تيليغرام: {e}")

def get_rsi(client, symbol):
    klines = client.get_klines(symbol=symbol, interval=INTERVAL, limit=RSI_PERIOD + 10)
    closes = pd.Series([float(k[4]) for k in klines])
    rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
    price  = float(client.get_symbol_ticker(symbol=symbol)["price"])
    return round(rsi.iloc[-1], 2), price

# ──────────────────────────────────────────────
def run_bot():
    # قراءة المتغيرات هنا داخل الدالة
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    tg_token   = os.getenv("TELEGRAM_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not api_key or not api_secret:
        raise EnvironmentError("❌ ضع BINANCE_API_KEY و BINANCE_API_SECRET!")
    if not tg_token or not tg_chat_id:
        raise EnvironmentError("❌ ضع TELEGRAM_TOKEN و TELEGRAM_CHAT_ID!")

    client = Client(api_key, api_secret)

    log.info(f"🚀 بدء بوت الإشارات | {len(SYMBOLS)} عملة")
    send_telegram(f"🚀 <b>بوت الإشارات شغال!</b>\nيراقب {len(SYMBOLS)} عملة\n📊 تنبيه عند RSI &lt; 30 أو RSI &gt; 70", tg_token, tg_chat_id)

    alerted = {s: {"buy": False, "sell": False} for s in SYMBOLS}

    while True:
        for symbol in SYMBOLS:
            try:
                rsi, price = get_rsi(client, symbol)
                coin       = symbol.replace("USDT", "")
                log.info(f"📊 {coin} | RSI: {rsi} | {price}")

                # تنبيه شراء RSI < 30
                if rsi < RSI_BUY and not alerted[symbol]["buy"]:
                    send_telegram(
                        f"🟢 <b>RSI منخفض - شراء محتمل!</b>\n"
                        f"🪙 <b>{coin}</b>\n"
                        f"💰 السعر: {price}\n"
                        f"📊 RSI: {rsi} (تحت 30)",
                        tg_token, tg_chat_id
                    )
                    alerted[symbol]["buy"]  = True
                    alerted[symbol]["sell"] = False
                    log.info(f"🟢 تنبيه شراء {coin}")

                # تنبيه بيع RSI > 70
                elif rsi > RSI_SELL and not alerted[symbol]["sell"]:
                    send_telegram(
                        f"🔴 <b>RSI مرتفع - بيع محتمل!</b>\n"
                        f"🪙 <b>{coin}</b>\n"
                        f"💰 السعر: {price}\n"
                        f"📊 RSI: {rsi} (فوق 70)",
                        tg_token, tg_chat_id
                    )
                    alerted[symbol]["sell"] = True
                    alerted[symbol]["buy"]  = False
                    log.info(f"🔴 تنبيه بيع {coin}")

                # إعادة ضبط التنبيه لما RSI يرجع للمنتصف
                elif 35 < rsi < 65:
                    alerted[symbol]["buy"]  = False
                    alerted[symbol]["sell"] = False

            except BinanceAPIException:
                pass  # عملة مو موجودة على Binance
            except Exception as e:
                log.error(f"⚠️ {symbol}: {e}")

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    run_bot()

