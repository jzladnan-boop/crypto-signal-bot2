"""
Crypto Trading Bot - RSI Auto Trader (Full 135+ Tokens & Free Max Trades)
========================================================================
التعديل:
- وضع القائمة كاملة لـ 135 عملة بدون أي نقص.
- الشراء الحر طالما يتوفر رصيد فوق الـ 15$ بدون أي أقفال صلبة.
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
# ⚙️ الإعدادات (القائمة الكاملة 135 عملة دون نقص)
# ──────────────────────────────────────────────
BASE_SYMBOLS = [
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
SYMBOLS = [f"{s}USDT" for s in BASE_SYMBOLS]

INTERVAL         = Client.KLINE_INTERVAL_15MINUTE
RSI_PERIOD       = 14
RSI_BUY          = 30
RSI_SELL         = 70
STOP_LOSS_PCT    = 0.025    # 2.5% stop loss عند الدخول
TRAIL_PCT        = 0.01     # 1% trailing stop بعد RSI 70
TRAIL_ABOVE_PCT  = 0.05     # بيع لو السعر ارتفع 5% فوق سعر RSI 70
TRADE_AMOUNT     = 15.0
RESERVE_USDT     = 2.0      # احتياطي $2 للكل
CHECK_EVERY      = 60

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

def send_error(location, error):
    log.error(f"❌ خطأ في {location}: {error}")
    send_telegram(f"⚠️ <b>خطأ في البوت</b>\n📍 المكان: {location}\n❌ الخطأ: {error}")

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
            log.warning(f"⚠️ {symbol}: الكمية صفر، تخطي")
            return None
        order = client.order_market_buy(symbol=symbol, quantity=qty)
        log.info(f"✅ شراء {symbol} | الكمية: {qty} | السعر: {price}")
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
        log.info(f"✅ بيع {symbol} | الكمية: {actual_qty} | السعر: {price}")
        return price
    except BinanceAPIException as e:
        send_error(f"بيع {symbol}", e)
        return None

# ──────────────────────────────────────────────
# 🚀 البوت الرئيسي
# ──────────────────────────────────────────────
def run_bot():
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise EnvironmentError("❌ ضع BINANCE_API_KEY و BINANCE_API_SECRET في المتغيرات!")

    client = Client(api_key, api_secret)

    # فلترة العملات المتاحة
    log.info("🔍 جاري فحص العملات المتاحة...")
    exchange_info  = client.get_exchange_info()
    active_symbols = {s["symbol"] for s in exchange_info["symbols"] if s["status"] == "TRADING"}
    SYMBOLS[:]     = [s for s in SYMBOLS if s in active_symbols]
    log.info(f"✅ عملات متاحة للتداول: {len(SYMBOLS)}")

    log.info(f"🚀 بدء بوت التداول الشامل | {len(SYMBOLS)} عملة")
    send_telegram(
        f"🚀 <b>بوت التداول شغال بالقائمة الكاملة والنظام الحر!</b>\n"
        f"👁️ يراقب {len(SYMBOLS)} عملة بالتمام والكمال\n"
        f"💵 يشتري طالما يتوفر رصيد فوق الـ ${TRADE_AMOUNT}"
    )

    open_trades        = {}
    last_signal        = {s: False for s in SYMBOLS}
    last_heartbeat     = time.time()
    HEARTBEAT_INTERVAL = 3600

    while True:
        usdt_balance = 0.0
        try:
            usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
            # حسبة مرنة ومفتوحة لمجاراة الرصيد بدون أقفال
            max_trades = len(open_trades) + int((usdt_balance - RESERVE_USDT) / TRADE_AMOUNT)
            if max_trades == len(open_trades) and (usdt_balance - RESERVE_USDT) >= TRADE_AMOUNT:
                max_trades += 1
        except Exception as e:
            send_error("جلب الرصيد", e)
            max_trades = len(open_trades)

        # ── فحص الصفقات المفتوحة ──
        for symbol in list(open_trades.keys()):
            trade = open_trades[symbol]
            try:
                ind   = get_indicators(client, symbol)
                price = ind["price"]
                rsi   = ind["rsi"]
                coin  = symbol.replace("USDT", "")

                # تفعيل Trailing بعد RSI 70
                if not trade["trailing_active"] and rsi >= RSI_SELL:
                    trade["trailing_active"] = True
                    trade["highest_price"]   = price
                    trade["stop_loss"]       = round(price * (1 - TRAIL_PCT), 8)
                    log.info(f"🔶 {coin} وصل RSI 70 | SL Trailing: {trade
