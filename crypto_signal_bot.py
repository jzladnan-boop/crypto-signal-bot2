"""
Crypto Trading Bot - RSI + MACD Auto Trader (Fixed Version)
===========================================================
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
# ⚙️ الإعدادات (قائمة العملات مدمجة في سطر نظيف لمنع خطأ الـ 316)
# ──────────────────────────────────────────────
BASE_SYMBOLS = [
    "ACE", "ACH", "ADA", "AERGO", "ASI", "AIOZ", "AKT", "ALGO", "ALT", "ANKR",
    "ANT", "APT", "ARB", "ARKM", "ARPA", "ASTR", "ATA", "ATOM", "AVA", "AVAX",
    "AZERO", "BANANA", "BAND", "BAT", "BCH", "BEAM", "BFC", "BIFI", "BLZ", "BNK",
    "BORA", "BSV", "BTC", "CELO", "CELR", "CFX", "CHR", "CHZ", "CKB", "CLV",
    "COOKIE", "CSPR", "CTK", "CTSI", "CTXC", "CVC", "DAG", "DASH", "DATA", "DENT",
    "DERO", "DEXT", "DGB", "DIA", "DOCK", "DOGE", "DOT", "DUSK", "DVPN", "EDEN",
    "EDU", "EGLD", "EIGEN", "ELF", "ENJ", "ENS", "EOS", "ETC", "ETH", "ETHW",
    "EURI", "EWT", "FET", "FIL", "FIO", "FIRO", "FLUX", "FTM", "FX", "GLM",
    "GMT", "GRT", "GTC", "HBAR", "HERO", "HIVE", "HNT", "ICP", "IMX", "IOST",
    "IOTA", "IOTX", "IQ", "KAS", "KEY", "KLAY", "KMD", "KRL", "KSM", "LINK",
    "LIT", "LOOM", "LRC", "LSK", "LTC", "LTO", "LUMIA", "MASK", "MATIC", "MDT",
    "METIS", "MINA", "MOVR", "MTL", "NEAR", "NEO", "NKN", "NTRN", "NULS", "OGN",
    "OMG", "ONE", "ONG", "ORAI", "OXT", "PAAL", "PHA", "PHB", "PIVX", "POND"
]
# توليد أزواج USDT تلقائياً لتجنب أخطاء المنصة
SYMBOLS = [f"{s}USDT" for s in BASE_SYMBOLS]

INTERVAL        = Client.KLINE_INTERVAL_15MINUTE
RSI_PERIOD      = 14
RSI_BUY         = 30       # شراء عند RSI ≤ 30
RSI_SELL        = 70       # تفعيل trailing عند RSI ≥ 70
STOP_LOSS_PCT   = 0.025    # 2.5% stop loss
TRAIL_ABOVE_PCT = 0.05     # بيع لو السعر ارتفع 5% فوق سعر RSI 70
TRADE_AMOUNT    = 15.0     # دولار لكل صفقة
RESERVE_USDT    = 5.0      # سيولة احتياطية دايماً محجوزة
CHECK_EVERY     = 60       # ثانية بين كل دورة فحص

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# تيليغرام
# ──────────────────────────────────────────────
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
# جلب البيانات والمؤشرات
# ──────────────────────────────────────────────
def get_indicators(client, symbol):
    """يرجع RSI، MACD، سعر الإغلاق الأخير"""
    klines = client.get_klines(symbol=symbol, interval=INTERVAL, limit=100)
    closes = pd.Series([float(k[4]) for k in klines])

    # RSI
    rsi = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()

    # MACD
    macd_obj    = ta.trend.MACD(close=closes)
    macd_line   = macd_obj.macd()
    signal_line = macd_obj.macd_signal()

    price = float(client.get_symbol_ticker(symbol=symbol)["price"])

    return {
        "rsi"         : round(rsi.iloc[-1], 2),
        "rsi_prev"    : round(rsi.iloc[-2], 2),
        "macd"        : macd_line.iloc[-1],
        "macd_prev"   : macd_line.iloc[-2],
        "signal"      : signal_line.iloc[-1],
        "signal_prev" : signal_line.iloc[-2],
        "price"       : price,
    }

def is_macd_crossover(ind):
    """MACD قطع خط الإشارة من تحت (في المنطقة السلبية)"""
    crossed = (ind["macd_prev"] < ind["signal_prev"]) and (ind["macd"] > ind["signal"])
    negative_zone = ind["macd"] < 0
    return crossed and negative_zone

# ──────────────────────────────────────────────
# تنفيذ الصفقات
# ──────────────────────────────────────────────
def get_quantity(client, symbol, usdt_amount):
    """يحسب الكمية المناسبة حسب قواعد Binance"""
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
    """يشتري بسعر السوق"""
    try:
        qty, price = get_quantity(client, symbol, usdt_amount)
        if qty <= 0:
            log.warning(f"⚠️ {symbol}: الكمية صفر، تخطي")
            return None

        order = client.order_market_buy(symbol=symbol, quantity=qty)
        log.info(f"✅ شراء {symbol} | الكمية: {qty} | السعر: {price}")
        return {"qty": qty, "entry_price": price, "order_id": order["orderId"]}
    except BinanceAPIException as e:
        log.error(f"❌ خطأ شراء {symbol}: {e}")
        return None

def sell_market(client, symbol, qty):
    """يبيع بسعر السوق - يجيب الكمية الفعلية من المحفظة"""
    try:
        asset = symbol.replace("USDT", "")
        balance = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])

        if actual_qty <= 0:
            log.warning(f"⚠️ {symbol}: رصيد العملة صفر، تخطي البيع")
            return None

        info = client.get_symbol_info(symbol)
        step_size = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break

        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            actual_qty = round(actual_qty - (actual_qty % step_size), precision)

        order = client.order_market_sell(symbol=symbol, quantity=actual_qty)
        price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        log.info(f"✅ بيع {symbol} | الكمية: {actual_qty} | السعر: {price}")
        return price
    except BinanceAPIException as e:
        log.error(f"❌ خطأ بيع {symbol}: {e}")
        return None

# ──────────────────────────────────────────────
# البوت الرئيسي
# ──────────────────────────────────────────────
def run_bot():
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        raise EnvironmentError("❌ ضع BINANCE_API_KEY و BINANCE_API_SECRET في المتغيرات!")

    client = Client(api_key, api_secret)
    log.info(f"🚀 بدء بوت التداول | {len(SYMBOLS)} عملة")
    send_telegram(f"🚀 <b>بوت التداول شغال!</b>\nيراقب {len(SYMBOLS)} عملة\n💵 ${TRADE_AMOUNT} لكل صفقة")

    open_trades = {}
    last_signal = {s: False for s in SYMBOLS}
    last_heartbeat = time.time()
    HEARTBEAT_INTERVAL = 3600  

    while True:
        usdt_balance = 0.0
        try:
            usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
            max_trades   = max(1, int((usdt_balance - RESERVE_USDT) / TRADE_AMOUNT))
        except Exception as e:
            log.error(f"⚠️ خطأ جلب الرصيد: {e}")
            max_trades = len(open_trades)  

        # ── فحص الصفقات المفتوحة أولاً ──
        for symbol in list(open_trades.keys()):
            trade = open_trades[symbol]
            try:
                ind   = get_indicators(client, symbol)
                price = ind["price"]
                rsi   = ind["rsi"]
                coin  = symbol.replace("USDT", "")

                if not trade["trailing_active"] and rsi >= RSI_SELL:
                    trade["trailing_active"] = True
                    trade["rsi70_price"]     = price
                    trade["stop_loss"]       = price  
                    log.info(f"🔶 {coin} وصل RSI 70 | SL انتقل لـ {price}")
                    send_telegram(f"🔶 <b>{coin}</b> وصل RSI 70!\n💰 السعر: {price}\n🛡️ Stop Loss انتقل لـ {price}")

                sell_reason = None
                if price <= trade["stop_loss"]:
                    sell_reason = f"🛑 Stop Loss عند {trade['stop_loss']:.4f}"
                elif trade["trailing_active"] and rsi < RSI_SELL and ind["rsi_prev"] >= RSI_SELL:
                    sell_reason = f"📉 RSI نزل تحت 70 (RSI: {rsi})"
                elif trade["trailing_active"] and price >= trade["rsi70_price"] * (1 + TRAIL_ABOVE_PCT):
                    sell_reason = f"🎯 السعر ارتفع 5% فوق سعر RSI 70"

                if sell_reason:
                    sell_price = sell_market(client, symbol, trade["qty"])
                    if sell_price:
                        pnl     = (sell_price - trade["entry_price"]) * trade["qty"]
                        pnl_pct = ((sell_price - trade["entry_price"]) / trade["entry_price"]) * 100
                        emoji   = "🟢" if pnl >= 0 else "🔴"
                        send_telegram(
                            f"{emoji} <b>بيع {coin}</b>\n📌 السبب: {sell_reason}\n"
                            f"💰 دخول: {trade['entry_price']:.4f} | خروج: {sell_price:.4f}\n"
                            f"📊 P&L: {pnl:+.2f}$ ({pnl_pct:+.2f}%)"
                        )
                        del open_trades[symbol]
                        last_signal[symbol] = False

            except Exception as e:
                log.error(f"⚠️ خطأ فحص صفقة {symbol}: {e}")

        # ── البحث عن صفقات جديدة ──
        if len(open_trades) < max_trades:
            for symbol in SYMBOLS:
                if symbol in open_trades:
                    continue
                if len(open_trades) >= max_trades:
                    break
                try:
                    ind  = get_indicators(client, symbol)
                    rsi  = ind["rsi"]
                    coin = symbol.replace("USDT", "")

                    if rsi <= RSI_BUY and is_macd_crossover(ind) and not last_signal[symbol]:
                        log.info(f"🟢 إشارة شراء {coin} | RSI: {rsi}")
                        result = buy_market(client, symbol, TRADE_AMOUNT)

                        if result:
                            stop_loss_price = result["entry_price"] * (1 - STOP_LOSS_PCT)
                            open_trades[symbol] = {
                                "qty"             : result["qty"],
                                "entry_price"     : result["entry_price"],
                                "stop_loss"       : stop_loss_price,
                                "rsi70_price"     : None,
                                "trailing_active" : False,
                            }
                            last_signal[symbol] = True
                            send_telegram(
                                f"🟢 <b>شراء {coin}</b>\n💰 السعر: {result['entry_price']:.4f}\n"
                                f"📊 RSI: {rsi} | MACD crossover ✅\n"
                                f"🛡️ Stop Loss: {stop_loss_price:.4f} (-2.5%)\n"
                                f"📂 الصفقات: {len(open_trades)}/{max_trades}"
                            )
                    elif 35 < rsi < 65:
                        last_signal[symbol] = False

                except BinanceAPIException:
                    pass
                except Exception as e:
                    log.error(f"⚠️ {symbol}: {e}")

        log.info(f"⏳ صفقات مفتوحة: {len(open_trades)}/{max_trades} | رصيد USDT: {usdt_balance:.2f}$")

        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_telegram(f"💚 <b>البوت شغال</b>\n💰 رصيد USDT: {usdt_balance:.2f}$\n📂 صفقات مفتوحة: {len(open_trades)}/{max_trades}")
            last_heartbeat = time.time()

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    run_bot()
