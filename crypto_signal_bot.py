"""
Crypto Trading Bot - RSI Auto Trader
=====================================
التعديلات:
1. احتياطي $2 بدل $5
2. RSI Crossover - يشتري لما RSI يرتد فوق 30
3. Trailing SL 1% بعد RSI 70
4. تنبيه تيليغرام على أي خطأ
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
    "QNT", "TWT", "VET", "SKL", "PYTH", "INTT", "VIRTUAL", "STARAX",
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
    """يرسل تنبيه خطأ على تيليغرام"""
    log.error(f"❌ خطأ في {location}: {error}")
    send_telegram(f"⚠️ <b>خطأ في البوت</b>\n📍 المكان: {location}\n❌ الخطأ: {error}")

# ──────────────────────────────────────────────
# جلب البيانات والمؤشرات
# ──────────────────────────────────────────────
def get_indicators(client, symbol):
    """يرجع RSI وسعر الإغلاق الأخير"""
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
    """RSI كان تحت 30 وارتد فوق 30 = إشارة شراء"""
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
# البوت الرئيسي
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

    log.info(f"🚀 بدء بوت التداول | {len(SYMBOLS)} عملة")
    send_telegram(
        f"🚀 <b>بوت التداول شغال!</b>\n"
        f"👁️ يراقب {len(SYMBOLS)} عملة\n"
        f"💵 ${TRADE_AMOUNT} لكل صفقة\n"
        f"🛡️ Trailing SL: {int(TRAIL_PCT*100)}% | احتياطي: ${RESERVE_USDT}"
    )

    open_trades        = {}
    last_signal        = {s: False for s in SYMBOLS}
    last_heartbeat     = time.time()
    HEARTBEAT_INTERVAL = 3600

    while True:
        usdt_balance = 0.0
        try:
            usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
            max_trades   = max(1, int((usdt_balance - RESERVE_USDT) / TRADE_AMOUNT))
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
                    log.info(f"🔶 {coin} وصل RSI 70 | SL Trailing: {trade['stop_loss']}")
                    send_telegram(
                        f"🔶 <b>{coin}</b> وصل RSI 70!\n"
                        f"💰 السعر: {price}\n"
                        f"🛡️ Trailing SL: {trade['stop_loss']:.4f} (-{int(TRAIL_PCT*100)}%)"
                    )

                # تحديث Trailing SL مع الصعود
                if trade["trailing_active"] and price > trade["highest_price"]:
                    trade["highest_price"] = price
                    trade["stop_loss"]     = round(price * (1 - TRAIL_PCT), 8)
                    log.info(f"📈 {coin} SL تحدّث لـ {trade['stop_loss']}")

                # شروط البيع
                sell_reason = None
                if price <= trade["stop_loss"]:
                    if trade["trailing_active"]:
                        sell_reason = f"🛑 Trailing SL عند {trade['stop_loss']:.4f}"
                    else:
                        sell_reason = f"🛑 Stop Loss عند {trade['stop_loss']:.4f}"
                elif trade["trailing_active"] and price >= trade["highest_price"] * (1 + TRAIL_ABOVE_PCT):
                    sell_reason = f"🎯 السعر ارتفع 5% فوق أعلى سعر"

                if sell_reason:
                    sell_price = sell_market(client, symbol, trade["qty"])
                    if sell_price:
                        pnl     = (sell_price - trade["entry_price"]) * trade["qty"]
                        pnl_pct = ((sell_price - trade["entry_price"]) / trade["entry_price"]) * 100
                        emoji   = "🟢" if pnl >= 0 else "🔴"
                        send_telegram(
                            f"{emoji} <b>بيع {coin}</b>\n"
                            f"📌 السبب: {sell_reason}\n"
                            f"💰 دخول: {trade['entry_price']:.4f} | خروج: {sell_price:.4f}\n"
                            f"📊 P&L: {pnl:+.2f}$ ({pnl_pct:+.2f}%)"
                        )
                        del open_trades[symbol]
                        last_signal[symbol] = False

            except Exception as e:
                send_error(f"فحص صفقة {symbol}", e)

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

                    # شرط الدخول: RSI كان تحت 30 وارتد فوق 30
                    if is_rsi_crossover(ind) and not last_signal[symbol]:
                        log.info(f"🟢 إشارة شراء {coin} | RSI: {ind['rsi_prev']} → {rsi}")
                        result = buy_market(client, symbol, TRADE_AMOUNT)
                        if result:
                            stop_loss_price = result["entry_price"] * (1 - STOP_LOSS_PCT)
                            open_trades[symbol] = {
                                "qty"             : result["qty"],
                                "entry_price"     : result["entry_price"],
                                "stop_loss"       : stop_loss_price,
                                "highest_price"   : result["entry_price"],
                                "trailing_active" : False,
                            }
                            last_signal[symbol] = True
                            send_telegram(
                                f"🟢 <b>شراء {coin}</b>\n"
                                f"💰 السعر: {result['entry_price']:.4f}\n"
                                f"📊 RSI: {ind['rsi_prev']} → {rsi} (ارتداد ✅)\n"
                                f"🛡️ Stop Loss: {stop_loss_price:.4f} (-2.5%)\n"
                                f"📂 الصفقات: {len(open_trades)}/{max_trades}"
                            )

                    # إعادة ضبط الإشارة
                    elif rsi < RSI_BUY:
                        last_signal[symbol] = False

                except BinanceAPIException:
                    pass
                except Exception as e:
                    log.error(f"⚠️ {symbol}: {e}")

        log.info(f"⏳ {len(open_trades)}/{max_trades} صفقات | USDT: {usdt_balance:.2f}$ | استنى {CHECK_EVERY}ث")

        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_telegram(
                f"💚 <b>البوت شغال</b>\n"
                f"💰 رصيد USDT: {usdt_balance:.2f}$\n"
                f"📂 صفقات مفتوحة: {len(open_trades)}/{max_trades}\n"
                f"👁️ يراقب {len(SYMBOLS)} عملة"
            )
            last_heartbeat = time.time()

        time.sleep(CHECK_EVERY)

if __name__ == "__main__":
    run_bot()
