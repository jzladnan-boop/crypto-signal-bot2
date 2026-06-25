"""
Crypto Trading Bot - RSI Auto Trader (Full 135+ Tokens & Free Max Trades)
========================================================================
التعديل الشامل: إصلاح خطأ الـ global + دمج MA20 + الفحص الذكي مرحلتين + مرونة الفريم
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
# ⚙️ الإعدادات الأساسية
# ──────────────────────────────────────────────
SYMBOLS_FILE   = "symbols.txt"
TRADES_FILE    = "open_trades.json"

DEFAULT_BASE_SYMBOLS = [
    "ACE", "ACH", "ADA", "AERGO", "ASI", "AIOZ", "AKT", "ALGO", "ALT", "ANKR",
    "ANT", "APT", "ARB", "ARKM", "ARPA", "ASTR", "ATA", "ATOM", "AVA", "AVAX",
    "AZERO", "BANANA", "BAND", "BAT", "BCH", "BEAM", "BFC", "BIFI", "BLZ", "BNK",
    "BORA", "BSV", "BTC", "CELO", "CELR", "CFX", "CHR", "CHZ", "CKB", "CLV",
    "COOKIE", "CSPR", "CTK", "CTSI", "CTXC", "CVC", "DAG", "DASH", "DATA", "DENT",
    "DERO", "DEXT", "DGB", "DIA", "DOCK", "DOGE", "DOT", "DUSK", "DVPN", "EDEN",
    "EDU", "EGLD", "EIGEN", "ELF", "ENJ", "ENS", "EOS", "ETC", "ETH", "ETHW",
    "EWT", "FET", "FIL", "FIO", "FIRO", "FLUX", "FTM", "FX", "GLM", "GMT",
    "GRT", "GTC", "HBAR", "HERO", "HIVE", "HNT", "ICP", "IMX", "IOST", "IOTA",
    "IOTX", "IQ", "KAS", "KEY", "KLAY", "KMD", "KRL", "KSM", "LINK", "LIT",
    "LOOM", "LRC", "LSK", "LTC", "LTO", "LUMIA", "MASK", "MATIC", "MDT", "METIS",
    "MINA", "MOVR", "MTL", "NEAR", "NEO", "NKN", "NTRN", "NULS", "OGN", "OMG",
    "ONE", "ONG", "ORAI", "OXT", "PAAL", "PHA", "PHB", "PIVX", "POND", "STRAX",
    "KONET", "QAIT", "WALLI", "SPC", "SHARE", "BALL", "BLEND", "MEGA", "PROS", "ACN",
    "STAY", "OPG", "ST", "LWP", "DUPE", "WL", "USAT", "PRL", "ADI", "ION",
    "BTCB", "XMN", "TX", "ARCSOL", "SUP", "IDOS", "KIN", "INI", "TAKE", "ZKP",
    "NOCK", "AZTEC", "ESP", "TIMI", "WAI", "XCX", "GAIN", "WBAI", "VDR", "RNBW",
    "DIN", "SOGNI", "ZAMA", "KULA", "EVDC", "REAL", "SSS", "SPACE", "BDCA", "IMU",
    "PRO", "GWEI", "SKR", "ELSA", "ACU", "GRIN", "DN", "ARTFI", "OWL", "RZR",
    "RAIL", "ENX", "EDGE", "CAI", "AIAV", "DGRAM", "BYTE", "ZTC", "TAT", "BREV",
    "ESIM", "MORE", "ART", "VITA", "JOJO", "SNS", "KGST", "DMD", "SENT", "ZENT",
    "CTY", "RIZE", "TTD", "LISA", "VAIX", "ARVEX", "POWER", "CYS", "LIGHT", "STAR",
    "JCT", "TRUTH", "NIGHT", "RCHV", "STABLE", "SUT", "UMBRA", "GAIX", "OBI", "SHR",
    "MON", "IRYS", "AT", "LIFE", "GHOST", "ZERA", "XSWAP", "LAVA", "PAYAI", "LITKEY",
    "BOS", "KITE", "MNTC", "SWTCH", "SUI", "PIGGY", "EAT", "CLANKER", "BLUAI", "DGC",
    "XAN", "SIMAI", "FLK", "MTP", "BOOST", "PUSH", "ENSO", "DMCP", "VFY", "PIPE",
    "BOOM", "ASP", "SHX", "AOP", "LYN", "KGEN", "COAI", "AFT", "OPENX", "NETX",
    "XVM", "LGCT", "BLESS", "SNIFT", "GATA", "POP", "SAPIEN", "NUMI", "MIRA", "WMTX",
    "XPL", "AIO", "AVN", "SYND", "STOP", "AICELL", "ONI", "AIA", "MAIGA", "TANSSI",
    "ZKC", "AUKI", "PAL", "AGON", "ZTX", "QZN", "PIP", "HOLO", "LINEA", "IDEA",
    "NND", "SOMI", "SDV", "STFX", "DREYAI", "OASC", "ZKWASM", "CAMP", "KNET", "LIVE",
    "NODE", "APTM", "VRSC", "NEURON", "VARA", "AKE", "GAME", "SHIDO", "DARK", "LIORA",
    "MOR", "LOT", "TALE", "GAIA", "XNY", "CROSS", "AIX", "ZKL", "IKA", "NAORIS",
    "PROVE", "TOWNS", "RIO", "MIX", "STREAM", "PLAY", "FCT", "NERO", "PHY", "NODL",
    "INIT", "OBOL", "CESS", "TRT", "QBX", "EIN", "TOKAMAK", "KASTA", "CELDATA", "ERA",
    "RCADE", "STBU", "VAL", "AIN", "PUNDIAI", "SABAI", "BLPT", "CBK", "CGPT", "MAPO",
    "VELO", "PRGN", "REX", "MGO", "SAHARA", "NEWT", "CMD", "CORAL", "SMRT", "BOTIFY",
    "BRIC", "BSAI", "TAG", "BEE", "NAM", "MAT", "ESX", "GAG", "ARENA", "RDO",
    "TMAI", "ASTRA", "SKATE", "DLC", "PIN", "RON", "MYTH", "FLY", "ZEC", "SSV",
    "AB", "LENS", "INF", "NFTAI", "NERTA", "NRN", "BDXN", "EVER", "SQD", "BVT",
    "BOX", "SHM", "DTVC", "ASRR", "OBT", "PATEX", "SOPH", "RESCUE", "AWE", "EPIC",
    "RYO", "QUAI", "SOON", "REEF", "KTA", "SERV", "LOCK", "KEEP", "PRAI", "MINT",
    "AGT", "DUCK", "NEON", "ORT", "RWAI", "SIX", "SKYAI", "DOMIN", "GPUS", "SXT",
    "HEI", "FRIC", "AGC", "CRAI", "CARR", "OBOT", "FITFI", "PROPS", "AIOT", "VITE",
    "NEXUS", "AMB", "NAI", "AGIXT", "AQA", "PUNDIX", "SIGN", "XAR", "ROY", "EPT",
    "HYPER", "FHE", "WCT", "PROMPT", "MLK", "FLAI", "WAL", "PARTI", "GINI", "NIL",
    "UQC", "ROAM", "BMT", "OBX", "XTER", "SKEY", "ARC", "HELIO", "SNAI", "SC",
    "KAITO", "ETN", "ALL", "ETHO", "SCP", "CLS", "NXS", "DIAM", "ANLOG", "SUKU",
    "VEE", "MFG", "NEBL", "HPB", "BITS", "SOLVE", "FUND", "CENNZ", "BOSON", "BLY",
    "AVT", "DESCI", "MPC", "XCN", "XYM", "ALCH", "BID", "YNE", "CREO", "VVV",
    "CHEX", "DAOX", "SONIC", "HAT", "PHI", "DRGN", "AIAI", "VIS", "NC", "CGAI",
    "LMT", "NEUR", "CHEQ", "FUEL", "AIXBT", "SOAI", "OCN", "YOM", "RDN", "MYST",
    "MOBI", "BIO", "FLOCK", "RAI", "SETAI", "PEP", "AUTOS", "HPO", "RSC", "CIRX",
    "PRX", "CRU", "HNS", "KRO", "BTF", "STOS", "IOT", "SRX", "ELA", "QRL",
    "PPC", "RARI", "DPR", "CDX", "RWA", "BEPRO", "SKAI", "VANA", "MARSH", "GURU",
    "WLD", "AGENT", "KIP", "AGIX", "SXP", "WAXP", "DREP", "SAND", "RVN", "RLC",
    "QNT", "TWT", "VET", "SKL", "PYTH", "INTT", "VIRTUAL", "STARAX"
]

SYMBOLS            = []

INTERVAL           = Client.KLINE_INTERVAL_15MINUTE  # الشمعة المفضلة 15 دقيقة
current_interval   = INTERVAL

RSI_PERIOD         = 14
RSI_BUY            = 30
RSI_SELL           = 70
STOP_LOSS_PCT      = 0.025    
TRAIL_PCT          = 0.01     
TRAIL_ACTIVATE_PCT = 0.01     
TRADE_AMOUNT       = 15.0
RESERVE_USDT       = 2.0      
MAX_TRADES         = 10       
HEARTBEAT_INTERVAL = 3600
MA_PERIOD          = 20       

# فحص ذكي مرحلتين
SCAN_INTERVAL      = 120      
WATCH_INTERVAL     = 10       
RSI_WATCH_LOW      = 20       
RSI_WATCH_HIGH     = 38       

TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ADMIN_ID  = os.getenv("TELEGRAM_ADMIN_ID", "")

# متغيرات التحكم العامة
trading_enabled = True    
watch_list      = set()   
open_trades     = {}      
ma20_enabled    = True    

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
# 📂 إدارة ملف العملات
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
# 📂 إدارة الصفقات JSON
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

            for symbol, trade in open_trades.items():
                if "stop_loss" not in trade or not trade["stop_loss"]:
                    trade["stop_loss"] = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                if "trailing_active" not in trade:
                    trade["trailing_active"] = False
                if "highest_price" not in trade:
                    trade["highest_price"] = trade["entry_price"]

            log.info(f"✅ تم تحميل {len(open_trades)} صفقة من الذاكرة")
            save_trades()
        except Exception as e:
            log.error(f"❌ خطأ تحميل الصفقات: {e}")
            open_trades = {}

# ──────────────────────────────────────────────
# 📨 تيليغرام
# ──────────────────────────────────────────────
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data, timeout=10)
    except Exception as e:
        log.error(f"❌ خطأ تيليغرام: {e}")

def send_admin(message):
    chat = TELEGRAM_ADMIN_ID or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not chat:
        return
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data, timeout=10)
    except Exception as e:
        log.error(f"❌ خطأ تيليغرام admin: {e}")

# ──────────────────────────────────────────────
# ✅ أوامر تيليغرام - مصلحة بالملي
# ──────────────────────────────────────────────
def telegram_command_listener(client):
    global SYMBOLS, trading_enabled, ma20_enabled  # ✅ تم تصحيح الـ global هنا فوق خالص
    offset = 0

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

                    allowed_id = TELEGRAM_ADMIN_ID if TELEGRAM_ADMIN_ID else TELEGRAM_CHAT_ID
                    if chat_id != allowed_id:
                        continue

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
                            send_admin(f"❌ فشل إضافة {coin}.")

                    elif text.startswith("/remove "):
                        coin   = text.replace("/remove ", "").strip().upper()
                        symbol = f"{coin}USDT"
                        if symbol in SYMBOLS:
                            SYMBOLS.remove(symbol)
                            save_symbols_to_txt()
                            send_admin(f"🗑️ تم حذف {coin} من القائمة.")
                        else:
                            send_admin(f"⚠️ {coin} مش موجودة بالقائمة.")

                    elif text == "/list":
                        base_names = [s.replace("USDT", "") for s in SYMBOLS]
                        chunks = [base_names[i:i+30] for i in range(0, len(base_names), 30)]
                        for chunk in chunks:
                            send_admin(f"📋 القائمة ({len(base_names)} عملة):\n{', '.join(chunk)}")

                    elif text == "/stop":
                        trading_enabled = False
                        send_admin("⏸️ <b>تم إيقاف التداول.</b>")

                    elif text == "/start":
                        trading_enabled = True
                        send_admin("▶️ <b>تم استئناف التداول.</b>")

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

                    elif text.startswith("/set_interval "):
                        try:
                            minutes = int(text.replace("/set_interval ", ""))
                            global current_interval
                            intervals = {
                                15: Client.KLINE_INTERVAL_15MINUTE,
                                30: Client.KLINE_INTERVAL_30MINUTE,
                                60: Client.KLINE_INTERVAL_1HOUR,
                                240: Client.KLINE_INTERVAL_4HOUR,
                            }
                            if minutes in intervals:
                                current_interval = intervals[minutes]
                                send_admin(f"✅ تم تغيير الفريم إلى {minutes} دقيقة")
                            else:
                                send_admin(f"❌ الفريم المسموح: 15, 30, 60, 240 دقيقة")
                        except Exception as e:
                            send_admin(f"❌ خطأ: {e}")

                    elif text == "/enable_ma20":
                        ma20_enabled = True  # 🗑️ تم حذف سطر global من هنا لأنه مصلح فوق
                        send_admin("✅ تم تفعيل فيلتر MA20")

                    elif text == "/disable_ma20":
                        ma20_enabled = False  # 🗑️ تم حذف سطر global من هنا لأنه مصلح فوق
                        send_admin("❌ تم تعطيل فيلتر MA20")

                    elif text == "/help":
                        send_admin("📖 اكتب الأوامر للتحكم بالعملات والفريمات والـ MA20.")

        except Exception as e:
            log.error(f"❌ خطأ listener: {e}")
        time.sleep(1)

# ──────────────────────────────────────────────
# جلب البيانات والمؤشرات
# ──────────────────────────────────────────────
def get_rsi_quick(client, symbol):
    try:
        klines = client.get_klines(symbol=symbol, interval=current_interval, limit=RSI_PERIOD + 2) # ✅ مصلحة للمتغير المرن
        closes = pd.Series([float(k[4]) for k in klines])
        rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
        return round(rsi.iloc[-1], 2)
    except Exception as e:
        return None

def scan_all_symbols(client):
    global watch_list
    new_watch = set()
    for symbol in list(SYMBOLS):
        if symbol in open_trades:
            continue
        rsi = get_rsi_quick(client, symbol)
        if rsi is not None and RSI_WATCH_LOW <= rsi <= RSI_WATCH_HIGH:
            new_watch.add(symbol)
        time.sleep(0.05)
    watch_list = new_watch

def get_indicators(client, symbol):
    try:
        klines = client.get_klines(symbol=symbol, interval=current_interval, limit=100)
        closes = pd.Series([float(k[4]) for k in klines])
        rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
        ma20   = closes.rolling(window=MA_PERIOD).mean().iloc[-1]
        price  = float(client.get_symbol_ticker(symbol=symbol)["price"])
        return {
            "rsi"      : round(rsi.iloc[-1], 2),
            "rsi_prev" : round(rsi.iloc[-2], 2),
            "price"    : price,
            "ma20"     : round(ma20, 8),
        }
    except Exception as e:
        return None

# ──────────────────────────────────────────────
# تنفيذ الصفقات
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
        return {"qty": qty, "entry_price": price, "order_id": order["orderId"]}
    except Exception as e:
        log.error(f"❌ شراء {symbol}: {e}")
        return None

def sell_market(client, symbol, qty):
    try:
        info      = client.get_symbol_info(symbol)
        step_size = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break
        sell_qty  = qty * (1 - 0.001)  
        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            sell_qty  = round(sell_qty - (sell_qty % step_size), precision)
        if sell_qty <= 0:
            return None
        client.order_market_sell(symbol=symbol, quantity=sell_qty)
        price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        return price
    except Exception as e:
        log.error(f"❌ بيع {symbol}: {e}")
        return None

# ──────────────────────────────────────────────
# التشغيل الرئيسي
# ──────────────────────────────────────────────
def run_bot():
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    client     = Client(api_key, api_secret)

    load_symbols_from_txt()
    load_trades()   

    try:
        exchange_info  = client.get_exchange_info()
        active_symbols = {s["symbol"] for s in exchange_info["symbols"] if s["status"] == "TRADING"}
        global SYMBOLS
        SYMBOLS = [s for s in SYMBOLS if s in active_symbols]
        save_symbols_to_txt()
    except Exception as e:
        log.warning(f"⚠️ تأخر رد بينانس العادي: {e}")

    telegram_thread = threading.Thread(target=telegram_command_listener, args=(client,), daemon=True)
    telegram_thread.start()

    last_heartbeat = time.time()
    last_scan      = 0

    log.info(f"🚀 البوت انطلق بالنسخة الشاملة | {len(SYMBOLS)} عملة مجهزة.")

    while True:
        try:
            now = time.time()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                try:
                    usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                except:
                    usdt_balance = 0.0
                status = "▶️ شغال" if trading_enabled else "⏸️ موقوف"
                send_telegram(f"💚 <b>البوت شغال بأمان</b>\n💰 رصيد: ${usdt_balance:.2f}")
                last_heartbeat = now

            # 1. إدارة الصفقات المفتوحة
            for symbol in list(open_trades.keys()):
                trade = open_trades[symbol]
                try:
                    ind   = get_indicators(client, symbol)
                    if not ind:
                        continue
                    price = ind["price"]
                    rsi   = ind["rsi"]
                    coin  = symbol.replace("USDT", "")

                    if not trade["trailing_active"]:
                        if price >= trade["entry_price"] * (1 + TRAIL_ACTIVATE_PCT):
                            trade["trailing_active"] = True
                            trade["highest_price"]   = price
                            trade["stop_loss"]       = round(price * (1 - TRAIL_PCT), 8)
                            save_trades()

                    if trade["trailing_active"]:
                        if price > trade["highest_price"]:
                            trade["highest_price"] = price
                            trade["stop_loss"]       = round(price * (1 - TRAIL_PCT), 8)
                            save_trades()
                        elif price <= trade["stop_loss"]:
                            sell_price = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                profit = round((sell_price - trade["entry_price"]) * trade["qty"], 4)
                                send_telegram(f"💰 <b>جني أرباح - {coin}</b>\n💹 PnL: {profit:+.4f} USDT")
                                del open_trades[symbol]
                                save_trades()
                    else:
                        entry_sl = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                        if price <= entry_sl:
                            sell_price = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                loss = round((sell_price - trade["entry_price"]) * trade["qty"], 4)
                                send_telegram(f"🚨 <b>ستوب لوز - {coin}</b>\n💸 خسارة: {loss:.4f} USDT")
                                del open_trades[symbol]
                                save_trades()

                except Exception as e:
                    log.error(f"❌ إدارة {symbol}: {e}")

            # 2. الفحص الخفيف
            if now - last_scan >= SCAN_INTERVAL:
                scan_all_symbols(client)
                last_scan = now

            # 3. الفحص المكثف والشراء
            if watch_list and trading_enabled and len(open_trades) < MAX_TRADES:
                for symbol in list(watch_list):
                    if len(open_trades) >= MAX_TRADES:
                        break

                    ind = get_indicators(client, symbol)
                    if not ind:
                        continue

                    ma20_condition = (ind["price"] > ind["ma20"]) if ma20_enabled else True
                    if ind["rsi_prev"] < 32 and ind["rsi"] >= RSI_BUY and ma20_condition:
                        try:
                            usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                        except:
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
                                send_telegram(f"🟢 <b>شراء {symbol.replace('USDT','')}</b>\n💵 السعر: {res['entry_price']}")

                    time.sleep(0.1)

        except Exception as e:
            time.sleep(10)
        time.sleep(WATCH_INTERVAL)

if __name__ == "__main__":
    run_bot()
