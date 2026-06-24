"""
Crypto Trading Bot - RSI Auto Trader (Dynamic TXT Symbols & Telegram Commands)
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
# ⚙️ الإعدادات الأساسية وملف العملات
# ──────────────────────────────────────────────
SYMBOLS_FILE = "symbols.txt"  # الملف النصي الذي سيحتوي على العملات

# القائمة الافتراضية الـ 164 عملة (تُستخدم فقط في أول تشغيل لإنشاء الملف)
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

SYMBOLS = []  # القائمة الحية التي سيقرأها ويعدلها البوت برمجياً

INTERVAL         = Client.KLINE_INTERVAL_30MINUTE  # شمعة النصف ساعة
RSI_PERIOD       = 14
RSI_BUY          = 30
RSI_SELL         = 70
STOP_LOSS_PCT    = 0.02     # وقف خسارة 2% بسعر السوق
TRAIL_PCT        = 0.005    # ملاحقة أرباح لصيقة 0.5% بعد RSI 70
TRADE_AMOUNT     = 15.0
RESERVE_USDT     = 2.0      
CHECK_EVERY      = 60

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ──────────────────────────────────────────────
# 📂 دالات إدارة الملف النصي (TXT)
# ──────────────────────────────────────────────
def load_symbols_from_txt():
    """تحميل العملات من الملف النصي أو إنشائه بالقائمة الافتراضية إذا لم يكن موجوداً"""
    global SYMBOLS
    if not os.path.exists(SYMBOLS_FILE):
        with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(DEFAULT_BASE_SYMBOLS))
        log.info(f"📁 تم إنشاء ملف {SYMBOLS_FILE} جديد ووضع العملات الافتراضية به.")
        SYMBOLS = [f"{s}USDT" for s in DEFAULT_BASE_SYMBOLS]
    else:
        with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip().upper() for line in f.readlines() if line.strip()]
        # إزالة أي تكرارات قد تحدث بالخطأ داخل الملف النصي
        lines = list(dict.fromkeys(lines))
        SYMBOLS = [f"{s}USDT" for s in lines]
        log.info(f"📁 تم تحميل {len(SYMBOLS)} عملة من الملف النصي بنجاح.")

def save_symbols_to_txt():
    """حفظ القائمة الحالية المحدثة إلى الملف النصي"""
    base_names = [s.replace("USDT", "") for s in SYMBOLS]
    with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(base_names))

# ──────────────────────────────────────────────
# Logging & Telegram Communications
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

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        log.error(f"❌ خطأ تيليغرام: {e}")

def send_error(location, error):
    log.error(f"❌ خطأ في {location}: {error}")
    send_telegram(f"⚠️ <b>خطأ في البوت</b>\n📍 المكان: {location}\n❌ الخطأ: {error}")

# ──────────────────────────────────────────────
# 💬 خادم استقبال أوامر التليجرام (خلفية حية)
# ──────────────────────────────────────────────
def telegram_command_listener(client):
    """الاستماع للأوامر المرسلة من الموبايل وتحديث ملف التكسات فوراً بدون ريستارت"""
    offset = 0
    log.info("💬 تم تشغيل خادم الاستماع لأوامر التليجرام في الخلفية...")
    
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
                        
                        # التأكد من أنكِ أنتِ فقط من تتحكمين بالبوت لحماية الحساب
                        if chat_id != TELEGRAM_CHAT_ID:
                            continue
                        
                        global SYMBOLS
                        
                        # 1. أمر الإضافة: /add SOL
                        if text.startswith("/add "):
                            coin = text.replace("/add ", "").strip().upper()
                            symbol = f"{coin}USDT"
                            
                            # التأكد أولاً من أن المنصة تدعم العملة في السوق الفوري
                            try:
                                client.get_symbol_info(symbol)
                                if symbol not in SYMBOLS:
                                    SYMBOLS.append(symbol)
                                    save_symbols_to_txt()
                                    send_telegram(f"✅ <b>تمت الإضافة:</b> العملة <code>{coin}</code> مضافة الآن للمراقبة وللملف النصي!")
                                else:
                                    send_telegram(f"⚠️ العملة <code>{coin}</code> موجودة مسبقاً في قائمة الفحص.")
                            except:
                                send_telegram(f"❌ <b>خطأ:</b> العملة <code>{coin}</code> غير مدعومة للتداول الفوري في بينانس.")
                        
                        # 2. أمر الحذف: /remove BTC
                        elif text.startswith("/remove "):
                            coin = text.replace("/remove ", "").strip().upper()
                            symbol = f"{coin}USDT"
                            if symbol in SYMBOLS:
                                SYMBOLS.remove(symbol)
                                save_symbols_to_txt()
                                send_telegram(f"❌ <b>تم الحذف:</b> العملة <code>{coin}</code> أُزيلت تماماً من المراقبة ومن الملف النصي.")
                            else:
                                send_telegram(f"⚠️ العملة <code>{coin}</code> غير موجودة في قائمتكِ أصلاً.")
                        
                        # 3. أمر عرض القائمة الحالية: /list
                        elif text == "/list":
                            base_names = [s.replace("USDT", "") for s in SYMBOLS]
                            chunk_size = 50  # تقسيم الرسالة لكي لا تتجاوز حد التليجرام
                            send_telegram(f"📋 <b>إجمالي العملات المراقبة حالياً: {len(SYMBOLS)} عملة</b>")
                            for i in range(0, len(base_names), chunk_size):
                                send_telegram(f"<code>{', '.join(base_names[i:i+chunk_size])}</code>")
                                
        except Exception as e:
            log.error(f"خطأ في خادم أوامر التليجرام: {e}")
        time.sleep(1)

# ──────────────────────────────────────────────
# جلب البيانات والمؤشرات
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

def is_rsi_crossover(ind):
    return ind["rsi_prev"] < RSI_BUY and ind["rsi"] >= RSI_BUY

# ──────────────────────────────────────────────
# تنفيذ صفقات السوق الفورية
# ──────────────────────────────────────────────
def get_quantity(client, symbol, usdt_amount):
    info      = client.get_symbol_info(symbol)
    price     = float(client.get_symbol_ticker(symbol=symbol)["price"])
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
        if qty <= 0:
            log.warning(f"⚠️ {symbol}: الكمية صفر، تخطي")
            return None
        order = client.order_market_buy(symbol=symbol, quantity=qty)
        log.info(f"✅ شراء سوقي {symbol} | الكمية: {qty} | السعر: {price}")
        return {"qty": qty, "entry_price": price, "order_id": order["orderId"]}
    except BinanceAPIException as e:
        send_error(f"شراء {symbol}", e)
        return None

def sell_market(client, symbol, qty):
    try:
        asset      = symbol.replace("USDT", "")
        balance    = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])
        if actual_qty <= 0:
            log.warning(f"⚠️ {symbol}: رصيد العملة صفر، تخطي البيع")
            return None
        info      = client.get_symbol_info(symbol)
        step_size = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break
        if step_size:
            precision  = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            actual_qty = round(actual_qty - (actual_qty % step_size), precision)
        
        order = client.order_market_sell(symbol=symbol, quantity=actual_qty)
        price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        log.info(f"✅ بيع سوقي {symbol} | الكمية: {actual_qty} | السعر: {price}")
        return price
    except BinanceAPIException as e:
        send_error(f"بيع {symbol}", e)
        return None

# ──────────────────────────────────────────────
# 🚀 البوت الرئيسي التشغيلي
# ──────────────────────────────────────────────
def run_bot():
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise EnvironmentError("❌ ضع BINANCE_API_KEY و BINANCE_API_SECRET في المتغيرات!")

    client = Client(api_key, api_secret)

    # 1. تحميل العملات من ملف الـ TXT المستقل لأول مرة
    load_symbols_from_txt()

    # 2. فلترة وتأكيد العملات النشطة والمدعومة للتداول الفوري حالياً
    log.info("🔍 جاري مطابقة وتصفية القائمة مع أسواق الـ Spot الرسمية...")
    exchange_info  = client.get_exchange_info()
    active_symbols = {s["symbol"] for s in exchange_info["symbols"] if s["status"] == "TRADING"}
    
    global SYMBOLS
    SYMBOLS = [s for s in SYMBOLS if s in active_symbols]
    save_symbols_to_txt()  # حفظ القائمة المصفاة والجاهزة
    log.info(f"✅ تم تفعيل الملف وجاهزية فحص {len(SYMBOLS)} عملة فورا.")

    # 3. تشغيل خادم استقبال أوامر التليجرام اللامركزي في خيط منفصل (Thread) ليعمل بالتوازي
    telegram_thread = threading.Thread(target=telegram_command_listener, args=(client,), daemon=True)
    telegram_thread.start()

    send_telegram(
        f"🚀 <b>تم إطلاق البوت بنظام لوحة التحكم الحية وملف الـ TXT!</b>\n"
        f"⏱️ فريم الفحص الثابت: 30 دقيقة\n"
        f"🎯 تتبع أرباح لصيق جداً: 0.5% عند RSI 70\n"
        f"🛡️ حماية المحفظة: ستوب لوز 2% بأمر السوق المباشر\n"
        f"📱 يمكنك الآن استخدام الأوامر: <code>/list</code> أو <code>/add</code> أو <code>/remove</code> من موبايلك مباشرة!"
    )

    open_trades = {}

    while True:
        try:
            usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
        except Exception as e:
            send_error("جلب الرصيد", e)
            usdt_balance = 0.0

        # أ. إدارة ومراقبة الصفقات المفتوحة
        for symbol in list(open_trades.keys()):
            trade = open_trades[symbol]
            try:
                ind   = get_indicators(client, symbol)
                price = ind["price"]
                rsi   = ind["rsi"]
                coin  = symbol.replace("USDT", "")

                # تفعيل التتبع اللصيق 0.5% عند ملامسة الـ RSI 70 لحجز أقصى قمة
                if not trade["trailing_active"] and rsi >= RSI_SELL:
                    trade["trailing_active"] = True
                    trade["highest_price"]   = price
                    trade["stop_loss"]       = round(price * (1 - TRAIL_PCT), 8)
                    log.info(f"🔶 {coin} ضرب القمة RSI 70 | تم قفل التتبع على مسافة 0.5% لأسفل")

                if trade["trailing_active"]:
                    if price > trade["highest_price"]:
                        trade["highest_price"] = price
                        trade["stop_loss"]     = round(price * (1 - TRAIL_PCT), 8)
                    elif price <= trade["stop_loss"]:
                        log.info(f"🔻 ضرب الـ Trailing Stop (0.5%) لعملة {symbol}")
                        sell_market(client, symbol, trade["qty"])
                        send_telegram(f"💰 <b>تم قنص الأرباح بنجاح واقتراب القمة (Trailing 0.5%):</b> {coin}")
                        del open_trades[symbol]
                        continue
                else:
                    # الستوب لوز الأساسي الحذر (2%)
                    entry_sl = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                    if price <= entry_sl:
                        log.info(f"🔻 ضرب الـ Stop Loss الحذر (2%) لعملة {symbol}")
                        sell_market(client, symbol, trade["qty"])
                        send_telegram(f"📉 <b>إغلاق حماية الحساب (Stop Loss 2%):</b> {coin}\nتم الخروج ماركت.")
                        del open_trades[symbol]
                        continue

            except Exception as e:
                send_error(f"إدارة صفقة {symbol}", e)

        # ب. فحص العملات واقتناص صفقات الشراء بناءً على مصفوفة SYMBOLS الحية
        # أي عملة تضيفها أو تحذفها من التليجرام تنعكس في هذه الحلقة فوراً بالدورة القادمة
        for symbol in list(SYMBOLS):
            if symbol in open_trades:
                continue
            try:
                ind = get_indicators(client, symbol)
                if is_rsi_crossover(ind):
                    if usdt_balance >= (TRADE_AMOUNT + RESERVE_USDT):
                        res = buy_market(client, symbol, TRADE_AMOUNT)
                        if res:
                            res["trailing_active"] = False
                            res["highest_price"]   = res["entry_price"]
                            open_trades[symbol]    = res
                            usdt_balance          -= TRADE_AMOUNT
                            send_telegram(f"🎯 <b>صفقة قاع جديدة صاعدة:</b> {symbol.replace('USDT', '')}\nتم الدخول فوراً بناءً على ارتداد الـ RSI.")
            except Exception as e:
                continue

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    run_bot()
