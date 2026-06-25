"""
Crypto Trading Bot - RSI Auto Trader
نفس الكود الأصلي + كل التحسينات المتفق عليها
"""

import os
import time
import json
import logging
import requests
import threading
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta

# ──────────────────────────────────────────────
# ⚙️ الإعدادات الأساسية — نفس الأصلي
# ──────────────────────────────────────────────
SYMBOLS_FILE   = "symbols.txt"
TRADES_FILE    = "open_trades.json"

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

SYMBOLS            = []

INTERVAL           = Client.KLINE_INTERVAL_30MINUTE
current_interval   = INTERVAL  # متغير قابل للتعديل

RSI_PERIOD         = 14
RSI_BUY            = 30
RSI_SELL           = 70
STOP_LOSS_PCT      = 0.02
TRAIL_PCT          = 0.01
TRAIL_ACTIVATE_PCT = 0.01
TRADE_AMOUNT       = 15.0
RESERVE_USDT       = 2.0
MAX_TRADES         = 4
HEARTBEAT_INTERVAL = 3600
MA_PERIOD          = 20  # ✅ جديد: المتوسط المتحرك 20 شمعة

# ✅ جديد: فحص ذكي مرحلتين
SCAN_INTERVAL      = 120       # فحص خفيف لكل العملات كل دقيقتين
WATCH_INTERVAL     = 10        # فحص مكثف للمرشحين كل 10 ثواني
RSI_WATCH_LOW      = 20        # توسيع منطقة المراقبة
RSI_WATCH_HIGH     = 38        # توسيع منطقة المراقبة

TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")    # ID القناة — التنبيهات
TELEGRAM_ADMIN_ID  = os.getenv("TELEGRAM_ADMIN_ID", "")   # ID شاتك — الأوامر

# ──────────────────────────────────────────────
# متغيرات التحكم العامة
# ──────────────────────────────────────────────
trading_enabled = True    # يتحكم فيه /stop و /start
watch_list      = set()   # العملات في منطقة الارتداد
open_trades     = {}      # الصفقات المفتوحة
ma20_enabled    = True    # ✅ جديد: تفعيل/تعطيل MA20

# ──────────────────────────────────────────────
# Logging — نفس الأصلي
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
# 📂 إدارة ملف العملات — نفس الأصلي
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
        lines   = list(dict.fromkeys(lines))
        SYMBOLS = [f"{s}USDT" for s in lines]

def save_symbols_to_txt():
    base_names = [s.replace("USDT", "") for s in SYMBOLS]
    with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(base_names))

# ──────────────────────────────────────────────
# ✅ جديد: حفظ وتحميل الصفقات JSON
# ──────────────────────────────────────────────
def save_trades():
    try:
        with open(TRADES_FILE, "w", encoding="utf-8") as f:
            json.dump(open_trades, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ حفظ الصفقات: {e}")

def load_trades():
    global open_trades
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, "r", encoding="utf-8") as f:
                open_trades = json.load(f)

            # ✅ تحقق من كل صفقة وأصلح الحقول الناقصة
            for symbol, trade in open_trades.items():
                coin = symbol.replace("USDT", "")

                # لو ما في stop_loss احسبه من جديد
                if "stop_loss" not in trade or not trade["stop_loss"]:
                    trade["stop_loss"] = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)

                # لو ما في trailing_active
                if "trailing_active" not in trade:
                    trade["trailing_active"] = False

                # لو ما في highest_price
                if "highest_price" not in trade:
                    trade["highest_price"] = trade["entry_price"]

                log.info(
                    f"📂 صفقة محملة: {coin} | دخول: {trade['entry_price']:.4f}$ | "
                    f"ستوب: {trade['stop_loss']:.4f}$ | Trailing: {trade['trailing_active']}"
                )

            log.info(f"✅ تم تحميل {len(open_trades)} صفقة من الذاكرة")
            save_trades()  # حفظ فوري بعد الإصلاح

        except Exception as e:
            log.error(f"❌ خطأ تحميل الصفقات: {e}")
            open_trades = {}

# ──────────────────────────────────────────────
# 📨 تيليغرام — نفس الأصلي + تسجيل الخطأ
# ──────────────────────────────────────────────
def send_telegram(message):
    """يرسل التنبيهات للقناة"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data, timeout=10)
        if r.status_code != 200:
            log.error(f"❌ تيليغرام: {r.status_code} | {r.text}")
    except Exception as e:
        log.error(f"❌ خطأ تيليغرام: {e}")

def send_admin(message):
    """يرسل ردود الأوامر لشاتك الشخصي"""
    chat = TELEGRAM_ADMIN_ID or TELEGRAM_CHAT_ID  # لو ما في admin يرسل للقناة
    if not TELEGRAM_TOKEN or not chat:
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data, timeout=10)
        if r.status_code != 200:
            log.error(f"❌ تيليغرام admin: {r.status_code} | {r.text}")
    except Exception as e:
        log.error(f"❌ خطأ تيليغرام admin: {e}")

# ──────────────────────────────────────────────
# ✅ أوامر تيليغرام — نفس الأصلي + /stop /start /status /help
# ──────────────────────────────────────────────
def telegram_command_listener(client):
    global SYMBOLS, trading_enabled
    offset = 0

    # تجاهل الرسائل القديمة
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", timeout=10).json()
        if r.get("result"):
            offset = r["result"][-1]["update_id"] + 1
    except Exception as e:
        log.error(f"❌ خطأ offset تيليغرام: {e}")

    while True:
        if not TELEGRAM_TOKEN:
            time.sleep(10)
            continue
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=10"
            r   = requests.get(url, timeout=15).json()

            if "result" in r:
                for update in r["result"]:
                    offset = update["update_id"] + 1
                    if "message" not in update or "text" not in update["message"]:
                        continue

                    text    = update["message"]["text"].strip()
                    chat_id = str(update["message"]["chat"]["id"])

                    # ✅ يقبل أوامر من ADMIN_ID فقط — لو ما محدد يقبل من CHAT_ID
                    allowed_id = TELEGRAM_ADMIN_ID if TELEGRAM_ADMIN_ID else TELEGRAM_CHAT_ID
                    if chat_id != allowed_id:
                        continue

                    # ── /add ──────────────────────────────────
                    if text.startswith("/add "):
                        coin   = text.replace("/add ", "").strip().upper()
                        symbol = f"{coin}USDT"
                        try:
                            info = client.get_symbol_info(symbol)
                            if info is None:
                                send_admin(f"❌ {coin} غير موجودة على بينانس.")
                            elif symbol in SYMBOLS:
                                send_admin(f"⚠️ {coin} موجودة أصلاً بالقائمة.")
                            else:
                                SYMBOLS.append(symbol)
                                save_symbols_to_txt()
                                send_admin(f"✅ تم إضافة {coin} للمراقبة.")
                        except Exception as e:
                            log.error(f"❌ /add {coin}: {e}")
                            send_admin(f"❌ فشل إضافة {coin}.")

                    # ── /remove ───────────────────────────────
                    elif text.startswith("/remove "):
                        coin   = text.replace("/remove ", "").strip().upper()
                        symbol = f"{coin}USDT"
                        if symbol in SYMBOLS:
                            SYMBOLS.remove(symbol)
                            save_symbols_to_txt()
                            send_admin(f"🗑️ تم حذف {coin} من القائمة.")
                        else:
                            send_admin(f"⚠️ {coin} مش موجودة بالقائمة.")

                    # ── /list ─────────────────────────────────
                    elif text == "/list":
                        base_names = [s.replace("USDT", "") for s in SYMBOLS]
                        chunks = [base_names[i:i+30] for i in range(0, len(base_names), 30)]
                        for chunk in chunks:
                            send_admin(f"📋 القائمة ({len(base_names)} عملة):\n{', '.join(chunk)}")

                    # ── /stop ─────────────────────────────────
                    elif text == "/stop":
                        trading_enabled = False
                        send_admin("⏸️ <b>تم إيقاف التداول.</b>\nالصفقات المفتوحة لا تزال تحت المراقبة.")

                    # ── /start ────────────────────────────────
                    elif text == "/start":
                        trading_enabled = True
                        send_admin("▶️ <b>تم استئناف التداول.</b>")

                    # ── /status ───────────────────────────────
                    elif text == "/status":
                        try:
                            usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                        except:
                            usdt_balance = 0.0
                        status = "▶️ شغال" if trading_enabled else "⏸️ موقوف"
                        msg = (
                            f"📊 <b>حالة البوت</b>\n"
                            f"🔘 التداول: {status}\n"
                            f"💰 USDT المتاح: ${usdt_balance:.2f}\n"
                            f"💼 صفقات: {len(open_trades)}/{MAX_TRADES}\n"
                            f"👁️ يراقب: {len(SYMBOLS)} عملة\n"
                            f"🔍 مراقبة مكثفة: {len(watch_list)} عملة\n"
                        )
                        if open_trades:
                            msg += "\n<b>الصفقات المفتوحة:</b>\n"
                            for sym, t in open_trades.items():
                                coin  = sym.replace("USDT", "")
                                trail = "✅" if t.get("trailing_active") else "⏳"
                                msg  += f"  #{coin} | دخول: {t['entry_price']:.4f}$ | Trailing: {trail}\n"
                        send_admin(msg)

                    # ── /set_interval ────────────────────────
                    elif text.startswith("/set_interval "):
                        try:
                            minutes = int(text.replace("/set_interval ", ""))
                            global current_interval
                            # تحويل الدقائق لفريم بينانس
                            intervals = {
                                15: Client.KLINE_INTERVAL_15MINUTE,
                                30: Client.KLINE_INTERVAL_30MINUTE,
                                60: Client.KLINE_INTERVAL_1HOUR,
                                240: Client.KLINE_INTERVAL_4HOUR,
                            }
                            if minutes in intervals:
                                current_interval = intervals[minutes]
                                send_admin(f"✅ تم تغيير الفريم إلى {minutes} دقيقة")
                                log.info(f"📊 الفريم الجديد: {minutes} دقيقة")
                            else:
                                send_admin(f"❌ الفريم المسموح: 15, 30, 60, 240 دقيقة")
                        except Exception as e:
                            send_admin(f"❌ خطأ: {e}")

                    # ── /enable_ma20 ─────────────────────────
                    elif text == "/enable_ma20":
                        global ma20_enabled
                        ma20_enabled = True
                        send_admin("✅ تم تفعيل فيلتر MA20")
                        log.info("✅ MA20 مفعّل الآن")

                    # ── /disable_ma20 ────────────────────────
                    elif text == "/disable_ma20":
                        global ma20_enabled
                        ma20_enabled = False
                        send_admin("❌ تم تعطيل فيلتر MA20 — الشراء بناءً على RSI فقط")
                        log.info("❌ MA20 معطّل الآن")

                    # ── /help ─────────────────────────────────
                    elif text == "/help":
                        send_admin(
                            "📖 <b>الأوامر المتاحة (18 أمر):</b>\n\n"
                            "<b>إدارة العملات:</b>\n"
                            "/add ETH — إضافة عملة\n"
                            "/remove ETH — حذف عملة\n"
                            "/list — عرض القائمة\n\n"
                            "<b>التحكم بالتداول:</b>\n"
                            "/stop — إيقاف التداول\n"
                            "/start — استئناف التداول\n"
                            "/status — حالة البوت\n\n"
                            "<b>تعديل الإعدادات:</b>\n"
                            "/set_trade_amount 20 — حجم الصفقة\n"
                            "/set_max_trades 5 — أقصى صفقات\n"
                            "/set_trail 1.5 — Trailing Stop\n"
                            "/set_rsi_range 20 38 — منطقة RSI\n"
                            "/set_interval 30 — الفريم (15/30/60/240)\n\n"
                            "<b>الموشرات:</b>\n"
                            "/enable_ma20 — تشغيل MA20\n"
                            "/disable_ma20 — تعطيل MA20\n\n"
                            "<b>التقارير:</b>\n"
                            "/profit today — أرباح اليوم\n"
                            "/summary — ملخص الأداء\n"
                            "/help — عرض الأوامر"
                        )

        except Exception as e:
            log.error(f"❌ خطأ listener: {e}")
        time.sleep(1)

# ──────────────────────────────────────────────
# ✅ جديد: فحص ذكي مرحلتين
# ──────────────────────────────────────────────
def get_rsi_quick(client, symbol):
    """فحص خفيف — RSI سريع"""
    try:
        klines = client.get_klines(symbol=symbol, interval=INTERVAL, limit=RSI_PERIOD + 2)
        closes = pd.Series([float(k[4]) for k in klines])
        rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
        return round(rsi.iloc[-1], 2)
    except Exception as e:
        log.error(f"❌ RSI سريع {symbol}: {e}")
        return None

def scan_all_symbols(client):
    """المرحلة 1: فحص خفيف لكل العملات كل 5 دقائق"""
    global watch_list
    new_watch = set()
    log.info(f"🔍 فحص خفيف لـ {len(SYMBOLS)} عملة...")
    for symbol in list(SYMBOLS):
        if symbol in open_trades:
            continue
        rsi = get_rsi_quick(client, symbol)
        if rsi is not None and RSI_WATCH_LOW <= rsi <= RSI_WATCH_HIGH:
            new_watch.add(symbol)
        time.sleep(0.1)
    added = new_watch - watch_list
    if added:
        log.info(f"👀 مرشحون جدد: {[s.replace('USDT','') for s in added]}")
    watch_list = new_watch

# ──────────────────────────────────────────────
# جلب المؤشرات — نفس الأصلي + معالجة الخطأ
# ──────────────────────────────────────────────
def get_indicators(client, symbol):
    try:
        klines = client.get_klines(symbol=symbol, interval=current_interval, limit=100)
        closes = pd.Series([float(k[4]) for k in klines])
        rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
        
        # ✅ جديد: حساب MA20
        ma20   = closes.rolling(window=MA_PERIOD).mean().iloc[-1]
        
        price  = float(client.get_symbol_ticker(symbol=symbol)["price"])
        return {
            "rsi"     : round(rsi.iloc[-1], 2),
            "rsi_prev": round(rsi.iloc[-2], 2),
            "price"   : price,
            "ma20"    : round(ma20, 8),  # ✅ جديد
        }
    except Exception as e:
        log.error(f"❌ مؤشرات {symbol}: {e}")
        return None

# ──────────────────────────────────────────────
# تنفيذ الصفقات — نفس الأصلي + إصلاح البيع
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
            return None
        order = client.order_market_buy(symbol=symbol, quantity=qty)
        log.info(f"✅ شراء {symbol} | السعر: {price} | الكمية: {qty}")
        return {"qty": qty, "entry_price": price, "order_id": order["orderId"]}
    except BinanceAPIException as e:
        log.error(f"❌ شراء {symbol}: {e.status_code} | {e.message}")
        return None
    except Exception as e:
        log.error(f"❌ شراء {symbol}: {e}")
        return None

def sell_market(client, symbol, qty):
    """✅ إصلاح: يبيع الكمية المحددة فقط مش كل الرصيد"""
    try:
        info      = client.get_symbol_info(symbol)
        step_size = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break
        sell_qty = qty * (1 - 0.001)  # طرح 0.1% عمولة
        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            sell_qty  = round(sell_qty - (sell_qty % step_size), precision)
        if sell_qty <= 0:
            return None
        client.order_market_sell(symbol=symbol, quantity=sell_qty)
        price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        log.info(f"✅ بيع {symbol} | السعر: {price} | الكمية: {sell_qty}")
        return price
    except BinanceAPIException as e:
        log.error(f"❌ بيع {symbol}: {e.status_code} | {e.message}")
        return None
    except Exception as e:
        log.error(f"❌ بيع {symbol}: {e}")
        return None

# ──────────────────────────────────────────────
# 🚀 البوت الرئيسي
# ──────────────────────────────────────────────
def run_bot():
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    client     = Client(api_key, api_secret)

    load_symbols_from_txt()
    load_trades()   # ✅ جديد: تحميل الصفقات من JSON

    try:
        log.info("🔍 جاري مطابقة وتصفية القائمة مع أسواق الـ Spot الرسمية...")
        exchange_info  = client.get_exchange_info()
        active_symbols = {s["symbol"] for s in exchange_info["symbols"] if s["status"] == "TRADING"}
        global SYMBOLS
        SYMBOLS = [s for s in SYMBOLS if s in active_symbols]
        save_symbols_to_txt()
        log.info("✅ تم فلترة وتأكيد العملات النشطة بنجاح.")
    except Exception as e:
        log.warning(f"⚠️ تأخر رد بينانس. تم اعتماد القائمة كاملة: {e}")

    telegram_thread = threading.Thread(target=telegram_command_listener, args=(client,), daemon=True)
    telegram_thread.start()

    last_heartbeat = time.time()
    last_scan      = 0

    log.info(f"🚀 البوت انطلق | {len(SYMBOLS)} عملة | {len(open_trades)} صفقة محملة")
    send_telegram(
        f"🚀 <b>تم تشغيل البوت بنجاح!</b>\n"
        f"⏱️ فريم الفحص: 30 دقيقة\n"
        f"👁️ يراقب {len(SYMBOLS)} عملة\n"
        f"💼 صفقات محملة من الذاكرة: {len(open_trades)}\n"
        f"📖 اكتب /help لعرض الأوامر"
    )

    while True:
        try:
            now = time.time()
            log.info(f"🔄 فحص دوري | صفقات: {len(open_trades)}/{MAX_TRADES} | مراقبة مكثفة: {len(watch_list)}")

            # ── Heartbeat كل ساعة — نفس الأصلي ─────────
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                try:
                    usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                except:
                    usdt_balance = 0.0
                status = "▶️ شغال" if trading_enabled else "⏸️ موقوف"
                send_telegram(
                    f"💚 <b>البوت شغال</b>\n"
                    f"🔘 التداول: {status}\n"
                    f"💰 رصيد USDT: ${usdt_balance:.2f}\n"
                    f"💼 صفقات مفتوحة: {len(open_trades)}/{MAX_TRADES}\n"
                    f"👁️ يراقب {len(SYMBOLS)} عملة"
                )
                last_heartbeat = now

            # ── 1. إدارة الصفقات المفتوحة — نفس الأصلي + Trailing محسّن ──
            for symbol in list(open_trades.keys()):
                trade = open_trades[symbol]
                try:
                    ind   = get_indicators(client, symbol)
                    if not ind:
                        continue
                    price = ind["price"]
                    rsi   = ind["rsi"]
                    coin  = symbol.replace("USDT", "")

                    # ✅ تعديل: Trailing يشتغل بعد 1% ربح بدل انتظار RSI 70
                    if not trade["trailing_active"]:
                        if price >= trade["entry_price"] * (1 + TRAIL_ACTIVATE_PCT):
                            trade["trailing_active"] = True
                            trade["highest_price"]   = price
                            trade["stop_loss"]       = round(price * (1 - TRAIL_PCT), 8)
                            log.info(f"🎯 Trailing مفعّل لـ {coin} | ستوب: {trade['stop_loss']}")
                            save_trades()

                    # ✅ Trailing يتبع الصعود
                    if trade["trailing_active"]:
                        if price > trade["highest_price"]:
                            trade["highest_price"] = price
                            trade["stop_loss"]     = round(price * (1 - TRAIL_PCT), 8)
                            save_trades()
                        elif price <= trade["stop_loss"]:
                            sell_price = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                profit = round((sell_price - trade["entry_price"]) * trade["qty"], 4)
                                send_telegram(
                                    f"💰 <b>جني أرباح - {coin}</b>\n"
                                    f"📉 RSI: {rsi}\n"
                                    f"💵 دخول: {trade['entry_price']:.4f}$ → خروج: {sell_price:.4f}$\n"
                                    f"💹 PnL: {profit:+.4f} USDT"
                                )
                                del open_trades[symbol]
                                save_trades()
                                continue
                    else:
                        # ستوب لوز ثابت قبل تفعيل Trailing
                        entry_sl = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                        if price <= entry_sl:
                            sell_price = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                loss = round((sell_price - trade["entry_price"]) * trade["qty"], 4)
                                send_telegram(
                                    f"🚨 <b>ستوب لوز - {coin}</b>\n"
                                    f"📉 السعر: {sell_price:.4f}$\n"
                                    f"💸 خسارة: {loss:.4f} USDT"
                                )
                                del open_trades[symbol]
                                save_trades()
                                continue

                except Exception as e:
                    log.error(f"❌ إدارة {symbol}: {e}")

            # ── 2. فحص خفيف لكل العملات كل 5 دقائق ──────
            if now - last_scan >= SCAN_INTERVAL:
                scan_all_symbols(client)
                last_scan = now

            # ── 3. فحص مكثف للمرشحين — يصطاد الارتداد ───
            if watch_list and trading_enabled and len(open_trades) < MAX_TRADES:
                for symbol in list(watch_list):
                    if symbol in open_trades:
                        watch_list.discard(symbol)
                        continue
                    if len(open_trades) >= MAX_TRADES:
                        break

                    ind = get_indicators(client, symbol)
                    if not ind:
                        continue

                    # ✅ شرط الشراء: تقاطع RSI + فيلتر MA20 (إن كان مفعّل)
                    ma20_condition = (ind["price"] > ind["ma20"]) if ma20_enabled else True
                    if ind["rsi_prev"] < 32 and ind["rsi"] >= RSI_BUY and ma20_condition:
                        try:
                            usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                        except Exception as e:
                            log.error(f"❌ رصيد USDT: {e}")
                            continue

                        if usdt_balance >= (TRADE_AMOUNT + RESERVE_USDT):
                            res = buy_market(client, symbol, TRADE_AMOUNT)
                            if res:
                                res["trailing_active"] = False
                                res["highest_price"]   = res["entry_price"]
                                res["stop_loss"]       = round(res["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                                open_trades[symbol]    = res
                                save_trades()
                                watch_list.discard(symbol)

                                coin_name = symbol.replace("USDT", "")
                                sl_value  = round(res["entry_price"] * (1 - STOP_LOSS_PCT), 4)
                                send_telegram(
                                    f"🟢 <b>شراء {coin_name}</b>\n"
                                    f"📊 RSI: {ind['rsi_prev']} → {ind['rsi']}\n"
                                    f"💵 السعر: {res['entry_price']}\n"
                                    f"🛡️ Stop Loss: {sl_value}\n"
                                    f"💼 صفقات مفتوحة: {len(open_trades)}/{MAX_TRADES}"
                                )
                        else:
                            log.warning(f"⚠️ رصيد غير كافٍ: {usdt_balance:.2f} USDT")

                    time.sleep(0.2)

        except BinanceAPIException as e:
            log.error(f"❌ بينانس: {e.status_code} | {e.message}")
        except Exception as e:
            log.error(f"❌ خطأ عام: {e}")

        time.sleep(WATCH_INTERVAL)

if __name__ == "__main__":
    run_bot()
