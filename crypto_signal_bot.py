"""
Crypto Trading Bot - RSI Auto Trader
نفس الكود الأصلي + إصلاح 5 أخطاء فقط بدون تغيير المنطق
+ إصلاح إضافي: التحقق من الرصيد الفعلي قبل البيع لتجنب خطأ "insufficient balance"
+ إصلاح إضافي: تفعيل إرسال Push Notification تلقائياً مع كل رسالة تيليغرام
+ إصلاح إضافي: إضافة إعدادات ATR (atr_period, atr_multiplier, trail_atr_multiplier) لواجهة API
  الخاصة بالتطبيق/لوحة التحكم (GET و POST /api/settings) — كانت موجودة فقط بأوامر تيليغرام
+ إصلاح إضافي: سعر الدخول الفعلي (Executed Price) من رد بينانس مباشرة عند الشراء
  (actual_entry_price = total_spent / total_quantity) بدل سعر الشمعة التقريبي
+ إصلاح إضافي: حماية من حظر بينانس بسبب كثرة الطلبات (Error -1003)
  - استخدام get_all_tickers() لجلب كل الأسعار بطلب واحد بدل حلقة لكل عملة
  - تخزين مؤقت (cache) لمعلومات الرموز (step size) بدل طلبها بكل عملية شراء/بيع
  - معالج خاص لخطأ -1003: إيقاف مؤقت للطلبات بدل إعادة المحاولة فوراً
  - تكبير التأخير بين طلبات الفحص الخفيف لتقليل الوزن المستهلك بالدقيقة
"""

import os
import time
import json
import logging
import requests
import threading
import secrets
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta
from flask import Flask, request, jsonify, session, send_from_directory
from functools import wraps
from market_regime import MarketRegimeDetector
from inverse_btc import check_inverse_btc, get_config as get_inverse_config, set_config as set_inverse_config, get_24h_change_pct
from range_trading_btc import RangeTradingBTC
from trend_stoch_parallel import TrendStochParallel


def _get_live_risk_config():
    """
    يرجع أحدث قيم ATR/Trailing/Breakeven من إعدادات البوت الأساسي الحية —
    تُمرَّر لاستراتيجية Trend+Stoch الموازية (trend_stoch_parallel.py) عشان
    تشتغل دايماً بنفس القيم الحية، بدون ما تحتاج إعادة تشغيل عند أي تعديل
    من التطبيق أو تيليغرام.
    """
    with _lock:
        return {
            "atr_multiplier": ATR_MULTIPLIER,
            "trail_atr_multiplier": TRAIL_ATR_MULTIPLIER,
            "trail_activate_pct": TRAIL_ACTIVATE_PCT,
            "breakeven_activate_pct": BREAKEVEN_ACTIVATE_PCT,
            "breakeven_margin_pct": BREAKEVEN_MARGIN_PCT,
        }
from indicators import calculate_vwap, calculate_bollinger_bands, calculate_momentum_score, calculate_beta
from coin_memory import CoinMemory, CorrelationEngine, SmartRanker, ATRGuard, MarketRegime

# ──────────────────────────────────────────────
# ⚙️ الإعدادات الأساسية — نفس الأصلي
# ──────────────────────────────────────────────
DATA_DIR       = os.getenv("DATA_DIR", "/app/data")   # 📁 مجلد دائم (Volume) على Railway
SYMBOLS_FILE   = os.path.join(DATA_DIR, "symbols.txt")
TRADES_FILE    = os.path.join(DATA_DIR, "open_trades.json")
PROFIT_FILE    = os.path.join(DATA_DIR, f"profit_{time.strftime('%Y_%m')}.json")   # ✅ إصلاح #4: ملف شهري منفصل
CIRCUIT_FILE   = os.path.join(DATA_DIR, "circuit_breaker.json")   # 🛑 ملف لحفظ حالة التوقف التلقائي
SETTINGS_FILE  = os.path.join(DATA_DIR, "settings.json")   # ⚙️ ملف حفظ الإعدادات (تنجو من إعادة التشغيل)
PUSH_TOKENS_FILE = os.path.join(DATA_DIR, "push_tokens.json")   # 📱 ملف حفظ Push Tokens
NOTIFICATIONS_FILE = os.path.join(DATA_DIR, "notifications.json")   # 🔔 سجل الإشعارات لعرضه بالتطبيق
COIN_MEMORY_FILE   = os.path.join(DATA_DIR, "coin_memory.db")   # 🧠 ذاكرة الأداء التاريخي لكل عملة (SQLite)

# 🧠 نظام الذاكرة الذكية (Adaptive Memory System): تخزين الأداء + التصنيف + الترتيب + صمام أمان ATR
CORRELATION_UPDATE_INTERVAL = 30 * 60   # كل 30 دقيقة نعيد تصنيف ارتباط العملات مع BTC (تجنّب حمل زائد على الـ API)
MAX_NOTIFICATIONS = 50

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
current_interval   = INTERVAL

RSI_PERIOD         = 14
RSI_BUY            = 30
RSI_SELL           = 70
RSI_BUY_PREV       = 25
RSI_BUY_CURR       = 30
STOP_LOSS_PCT      = 0.02
TRAIL_PCT          = 0.01
TRAIL_ACTIVATE_PCT = 0.01

# ──────────────────────────────────────────────
# 🛡️ حماية التعادل (Breakeven Stop) — أرضية دائمة تُفعّل قبل التريلينج الكامل
# لما تربح الصفقة نسبة بسيطة، الستوب ينتقل لسعر الدخول + هامش يغطي العمولة،
# ويُخزَّن كـ"أرضية" لا يقدر أي حساب لاحق (حتى التريلينج) ينزل تحتها أبداً.
# هذا يمنع الحالة يلي الستوب بينقفل بالضبط على سعر الدخول (بدون هامش)
# ويتحول لخسارة بسيطة بعد العمولة والانزلاق عند التنفيذ الفعلي.
# ──────────────────────────────────────────────
BREAKEVEN_ACTIVATE_PCT = 0.005   # 0.5% ربح → تفعيل أرضية التعادل
BREAKEVEN_MARGIN_PCT   = 0.002   # 0.2% فوق سعر الدخول (يغطي عمولة بينانس القياسية 0.1%×2)

TRADE_AMOUNT       = 15.0
RESERVE_USDT       = 0.0   # ✅ إصلاح: تم إلغاء الاحتياطي بناءً على طلب المستخدم (كان 2.0)
MAX_TRADES         = 2   # 🎯 أقصى عدد صفقات "نشطة" (لسا ما فعّلت Trailing) بالتزامن — لا يوجد سقف على العدد الكلي للصفقات المفتوحة
HEARTBEAT_INTERVAL = 3600
MA_PERIOD          = 20

SCAN_INTERVAL      = 120
WATCH_INTERVAL      = 10
RSI_WATCH_LOW      = 20
RSI_WATCH_HIGH     = 38

TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ADMIN_ID  = os.getenv("TELEGRAM_ADMIN_ID", "")

# ──────────────────────────────────────────────
# 📊 لوحة التحكم (Dashboard) — إعدادات
# ──────────────────────────────────────────────
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "")   # ⚠️ لازم تحددهم، وإلا اللوحة ما بتشتغل
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_SECRET    = os.getenv("DASHBOARD_SECRET", secrets.token_hex(16))
DASHBOARD_PORT      = int(os.getenv("DASHBOARD_PORT", "5000"))

# ──────────────────────────────────────────────
# 🛑 Circuit Breaker: توقف تلقائي بعد خسارات متتالية
# ──────────────────────────────────────────────
MAX_CONSECUTIVE_LOSSES = 3            # عدد الستوب لوز المتتالية المسموح
PAUSE_DURATION_SECONDS = 2 * 60 * 60  # مدة التوقف (ساعتين)

# ──────────────────────────────────────────────
# 📐 ATR: ستوب لوس متحرك حسب تقلب كل عملة
# ──────────────────────────────────────────────
ATR_PERIOD     = 14    # عدد الشموع لحساب ATR
ATR_MULTIPLIER = 2.0   # مضاعف ATR لتحديد مسافة الستوب الأولي (كلما زاد، اتسعت مساحة التنفس)
TRAIL_ATR_MULTIPLIER = 1.5   # مضاعف ATR لمسافة الـ Trailing بعد التفعيل (عادة أضيق من الستوب الأولي)

# ──────────────────────────────────────────────
# 🚀 Trend + StochRSI: استراتيجية ثالثة مستقلة (StochRSI + فوليوم + فلتر Beta مقابل BTC)
# ──────────────────────────────────────────────
TREND_STOCH_INTERVAL   = Client.KLINE_INTERVAL_4HOUR   # (لم يعد يُستخدم لتأكيد الاتجاه — أُبقي للتوافق)
TREND_STOCH_MA_LENGTH  = 50    # (لم يعد يُستخدم — أُبقي للتوافق مع إعدادات قديمة محفوظة)
VOLUME_MA_LENGTH        = 20    # طول متوسط الفوليوم للمقارنة (فريم الدخول)
VOLUME_MULTIPLIER       = 1.0   # الفوليوم الحالي لازم يكون أعلى من (أو يساوي) هذا المضاعف × المتوسط

# ✅ فلتر Beta: بدل "تأكيد ترند العملة لحالها"، نطلب إن العملة تتحرك أعنف من BTC
# (Beta > 1.2 يعني: لو BTC طلع 1%، العملة عادة بتطلع أكثر — استغلال زخم الترند الصاعد)
TREND_STOCH_BETA_THRESHOLD = 1.2   # أقل Beta مقبول للشراء بهالاستراتيجية
TREND_STOCH_BETA_LOOKBACK  = 30    # عدد الشموع (على نفس فريم الدخول الحالي) لحساب Beta
BETA_CACHE_TTL_SECONDS      = 120   # نكاش شموع BTC 120 ثانية — نفس البيانات تُستخدم لكل عملات الدورة

# ──────────────────────────────────────────────
# 🚦 حماية من حظر بينانس بسبب كثرة الطلبات (Error -1003)
# ──────────────────────────────────────────────
API_BLOCK_PAUSE_SECONDS = 90   # مدة الإيقاف المؤقت لكل الطلبات لما نصطدم بخطأ -1003
_api_blocked_until       = 0   # timestamp لنهاية فترة الإيقاف المؤقت (0 = مافي حظر حالياً)
SYMBOL_INFO_CACHE        = {}  # 🗄️ تخزين مؤقت لمعلومات الرموز (step size...) بدل طلبها كل مرة

def is_api_blocked():
    """يتحقق إذا كنا بفترة إيقاف مؤقت بسبب حظر سابق، وينتظر لو لسا الوقت ما خلص"""
    global _api_blocked_until
    if _api_blocked_until and time.time() < _api_blocked_until:
        return True
    return False

def register_api_block(source=""):
    """يسجل حظر جديد من بينانس (-1003) ويوقف الطلبات مؤقتاً لتفادي تشديد الحظر"""
    global _api_blocked_until
    _api_blocked_until = time.time() + API_BLOCK_PAUSE_SECONDS
    log.warning(f"🚦 تم اكتشاف حظر مؤقت من بينانس (-1003) عند {source} — إيقاف كل الطلبات لمدة {API_BLOCK_PAUSE_SECONDS} ثانية")

def is_rate_limit_error(e):
    """يتحقق إذا كان الخطأ من نوع -1003 (Too much request weight)"""
    return isinstance(e, BinanceAPIException) and getattr(e, "code", None) == -1003

def get_symbol_info_cached(client, symbol):
    """يرجع معلومات الرمز من الكاش لو موجودة، وإلا يطلبها مرة وحدة ويخزنها"""
    if symbol in SYMBOL_INFO_CACHE:
        return SYMBOL_INFO_CACHE[symbol]
    info = client.get_symbol_info(symbol)
    if info:
        SYMBOL_INFO_CACHE[symbol] = info
    return info

def get_step_size(client, symbol):
    info = get_symbol_info_cached(client, symbol)
    if not info:
        return None
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return float(f["stepSize"])
    return None

# ──────────────────────────────────────────────
# 🗄️ كاش قصير المدة لـ endpoints الداشبورد اللي بتنادي بينانس
# (مثل /api/status و /api/trades) — عشان أي عدد أجهزة/تبويبات
# فاتحة بنفس الوقت (موبايل + تاب + متصفح) تشارك نفس الرد المخزن
# بدل ما كل جهاز يعمل طلب مستقل لبينانس، وهذا يحمي من حظر -1003
# لما يكون فيه أكثر من جهاز يعمل auto-refresh بنفس اللحظة.
# ──────────────────────────────────────────────
DASHBOARD_CACHE_TTL = 8   # ثانية — مدة صلاحية الكاش قبل ما يُطلب رد جديد من بينانس
_dashboard_cache_lock = threading.Lock()
_dashboard_cache = {}   # key -> {"data": ..., "ts": epoch_seconds}

def get_cached_or_fetch(key, fetch_fn, ttl=DASHBOARD_CACHE_TTL):
    """
    يرجع القيمة المخزنة بالكاش لو لسا صالحة (أصغر من ttl ثانية)،
    وإلا بيستدعي fetch_fn() مرة وحدة، يخزن النتيجة، ويرجعها.
    لو صار أكثر من طلب بنفس اللحظة، أول واحد بس بيروح لبينانس والباقي بياخدوا من الكاش.
    """
    now = time.time()
    with _dashboard_cache_lock:
        cached = _dashboard_cache.get(key)
        if cached and (now - cached["ts"]) < ttl:
            return cached["data"]
    # خارج القفل عشان ما نعلّق باقي الطلبات وقت استدعاء بينانس
    data = fetch_fn()
    with _dashboard_cache_lock:
        _dashboard_cache[key] = {"data": data, "ts": time.time()}
    return data

# ──────────────────────────────────────────────
# ✅ إصلاح #1: threading.Lock بدل Global مباشر
# ──────────────────────────────────────────────
_lock           = threading.Lock()
trading_enabled = True
watch_list      = set()
open_trades     = {}
ma20_enabled    = True
current_strategy   = "rsi"   # 🎯 الاستراتيجية الحالية: "rsi" أو "stoch_rsi" أو "trend_stoch"

STRATEGY_LABELS = {
    "rsi"        : "RSI العادي",
    "stoch_rsi"  : "Stochastic RSI",
    "trend_stoch": "Trend + StochRSI",
    "inverse_btc": "Inverse BTC 🔄",
}
consecutive_losses = 0   # 🛑 عدّاد الستوب لوز المتتالية
pause_until        = 0   # 🛑 timestamp لنهاية التوقف التلقائي (0 = مافي توقف)
_binance_client     = None   # 📊 مرجع لعميل بينانس، تستخدمه لوحة التحكم

# ──────────────────────────────────────────────
# 🧠 Market Regime Detector: تبديل تلقائي بين الاستراتيجيات حسب حالة السوق (اتجاه + تذبذب)
# ──────────────────────────────────────────────
AUTO_STRATEGY_ENABLED   = False   # 🔘 مطفي افتراضياً — لازم تفعّله يدوياً بأمر /set_auto_strategy on
AUTO_STRATEGY_INTERVAL  = 15 * 60 # فحص كل 15 دقيقة
_regime_detector        = None    # مرجع الكاشف (يُنشأ عند بدء تشغيل البوت)

# ──────────────────────────────────────────────
# 🧠 Adaptive Memory System: ذاكرة أداء العملات + تصنيف الارتباط + الترتيب الذكي + صمام أمان ATR
# لا تحتاج اتصال بينانس، فتُنشأ فوراً عند استيراد الملف (بعكس _regime_detector).
# ──────────────────────────────────────────────
coin_memory   = CoinMemory(COIN_MEMORY_FILE)
corr_engine   = CorrelationEngine(coin_memory)
smart_ranker  = SmartRanker(coin_memory)
atr_guard     = ATRGuard()
_last_correlation_update = 0   # timestamp لآخر تحديث لتصنيف الارتباط مع BTC

# ──────────────────────────────────────────────
# 📐 فلاتر تأكيد إضافية: VWAP + Bollinger Bands (اختيارية، مطفية افتراضياً)
# ──────────────────────────────────────────────
VWAP_FILTER_ENABLED = False   # لو مفعّل: نشتري بس لو السعر فوق VWAP (تأكيد قوة شراء حقيقية)
BB_FILTER_ENABLED   = False   # لو مفعّل: نشتري بس لو السعر قريب من الحد السفلي لبولينجر (ارتداد حقيقي من قاع)
BB_LOWER_MARGIN_PCT = 0.01    # هامش القرب المسموح من الحد السفلي (1% فوقه يُعتبر "قريب كفاية")

# ──────────────────────────────────────────────
# 🔄 إعدادات Inverse BTC (قابلة للتعديل من التطبيق)
# ──────────────────────────────────────────────
INVERSE_BTC_ENABLED = False   # 🔘 مطفي افتراضياً — تفعّله من التطبيق أو تيليغرام
INVERSE_BTC_CONFIG = {
    "btc_decline_threshold_pct": 3.0,   # BTC لازم ينزل 3% على الأقل
    "rs_min_threshold_pct": 5.0,        # العملة أقوى من BTC بـ 5%
    "min_volume_ratio": 1.2,            # فوليوم 1.2× المتوسط
}

# ──────────────────────────────────────────────
# 📊 استراتيجية Range Trading BTC — مستقلة تماماً (ملف range_trading_btc.py)
# صفقات حقيقية بمبلغ ثابت 15 USDT، صفقة وحدة بس بأي لحظة. مطفية افتراضياً —
# تفعّل/تطفى من تيليغرام (/set_range_trading on|off) أو التطبيق (/api/settings).
# ──────────────────────────────────────────────
RANGE_TRADING_ENABLED = False
RANGE_TRADING_USDT_PER_TRADE = 15.0   # المبلغ الثابت لكل صفقة Range Trading (صفقة وحدة بس بأي لحظة)
_range_trading_strategy = None   # ⬅️ نسخة RangeTradingBTC الوحيدة — تتنشئ عند تشغيل البوت (main)

# ──────────────────────────────────────────────
# 📈 استراتيجية Trend + StochRSI الموازية المستقلة (ملف trend_stoch_parallel.py)
# نفس منطق دخول Trend+Stoch الأصلي + نفس نظام ATR/Breakeven/Trailing الحقيقي،
# بس بصفقة وحدة بمبلغ ثابت 15 USDT، مستقلة كلياً عن حلقة الفحص الرئيسية.
# مطفية افتراضياً — تفعّل/تطفى من تيليغرام (/set_trend_parallel on|off) أو التطبيق.
# ──────────────────────────────────────────────
TREND_PARALLEL_ENABLED = False
TREND_PARALLEL_USDT_PER_TRADE = 15.0
_trend_parallel_strategy = None   # ⬅️ نسخة TrendStochParallel الوحيدة

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
# حفظ وتحميل الصفقات JSON
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
                coin = symbol.replace("USDT", "")
                if "stop_loss" not in trade or not trade["stop_loss"]:
                    trade["stop_loss"] = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                if "trailing_active" not in trade:
                    trade["trailing_active"] = False
                if "highest_price" not in trade:
                    trade["highest_price"] = trade["entry_price"]
                if "breakeven_floor" not in trade:
                    # 🛡️ صفقة قديمة من قبل إضافة حماية التعادل: لو الستوب المحفوظ فعلياً
                    # أعلى من أو يساوي سعر الدخول (يعني كانت وصلت مرحلة "قفل على الدخول"
                    # القديمة بدون هامش)، نرقّيها فوراً لأرضية تعادل صحيحة بهامش العمولة،
                    # بدل ما تضل عالقة عند سعر الدخول الخام بدون حماية فعلية.
                    trade["breakeven_floor"] = None
                    if trade.get("stop_loss") and trade["stop_loss"] >= trade["entry_price"]:
                        floor_price = round(trade["entry_price"] * (1 + BREAKEVEN_MARGIN_PCT), 8)
                        trade["breakeven_floor"] = floor_price
                        trade["stop_loss"] = max(trade["stop_loss"], floor_price)
                        log.info(f"🛡️ ترقية صفقة قديمة {coin} لأرضية تعادل صحيحة: {floor_price}")
                log.info(
                    f"📂 صفقة محملة: {coin} | دخول: {trade['entry_price']:.4f}$ | "
                    f"ستوب: {trade['stop_loss']:.4f}$ | Trailing: {trade['trailing_active']}"
                )
            log.info(f"✅ تم تحميل {len(open_trades)} صفقة من الذاكرة")
            save_trades()
        except Exception as e:
            log.error(f"❌ خطأ تحميل الصفقات: {e}")

def count_active_trades():
    """
    🎯 يرجع عدد الصفقات 'النشطة' (اللي لسا ما فعّلت Trailing).
    هذا هو العدد اللي يتحكم بفتح صفقات جديدة (حده الأقصى MAX_TRADES) —
    مو العدد الكلي للصفقات المفتوحة، اللي ما فيه سقف عليه إطلاقاً.
    الصفقات اللي فعّلت Trailing تعتبر "آمنة" ولا تحجز مكان لصفقة جديدة.
    """
    return sum(1 for t in open_trades.values() if not t.get("trailing_active"))

# ──────────────────────────────────────────────
# 🛑 حفظ وتحميل حالة Circuit Breaker (تنجو من إعادة تشغيل السيرفر)
# ──────────────────────────────────────────────
def save_circuit_state():
    try:
        with open(CIRCUIT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "consecutive_losses": consecutive_losses,
                "pause_until"       : pause_until,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ حفظ حالة التوقف التلقائي: {e}")

def load_circuit_state():
    global consecutive_losses, pause_until, trading_enabled
    if os.path.exists(CIRCUIT_FILE):
        try:
            with open(CIRCUIT_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            consecutive_losses = state.get("consecutive_losses", 0)
            pause_until        = state.get("pause_until", 0)
            if pause_until and pause_until > time.time():
                # لسا فيه توقف نشط من قبل إعادة التشغيل — يبقى متوقف
                trading_enabled = False
                remaining_min = int((pause_until - time.time()) / 60)
                log.warning(f"🛑 تم استرجاع توقف تلقائي نشط — باقي {remaining_min} دقيقة")
                send_telegram(f"🛑 <b>تنبيه بعد إعادة التشغيل</b>\nيوجد توقف تلقائي نشط من قبل — باقي {remaining_min} دقيقة تقريبًا.")
            elif pause_until:
                # التوقف كان منتهي أصلاً وقت إعادة التشغيل
                pause_until = 0
                save_circuit_state()
        except Exception as e:
            log.error(f"❌ خطأ تحميل حالة التوقف التلقائي: {e}")

# ──────────────────────────────────────────────
# ⚙️ حفظ وتحميل الإعدادات (تنجو من إعادة التشغيل)
# ──────────────────────────────────────────────
def save_settings():
    global TRADE_AMOUNT, MAX_TRADES, TRAIL_PCT, RSI_WATCH_LOW, RSI_WATCH_HIGH
    global current_interval, ma20_enabled, current_strategy, STOP_LOSS_PCT, TRAIL_ACTIVATE_PCT
    global RSI_BUY_PREV, RSI_BUY_CURR
    global ATR_PERIOD, ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER
    global AUTO_STRATEGY_ENABLED
    global VWAP_FILTER_ENABLED, BB_FILTER_ENABLED, INVERSE_BTC_ENABLED, INVERSE_BTC_CONFIG
    global BREAKEVEN_ACTIVATE_PCT, BREAKEVEN_MARGIN_PCT
    global RANGE_TRADING_ENABLED
    global TREND_PARALLEL_ENABLED
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "trade_amount"     : TRADE_AMOUNT,
                "max_trades"       : MAX_TRADES,
                "trail_pct"        : TRAIL_PCT,
                "rsi_watch_low"    : RSI_WATCH_LOW,
                "rsi_watch_high"   : RSI_WATCH_HIGH,
                "interval_minutes" : INTERVAL_TO_MINUTES.get(current_interval, 30),
                "ma20_enabled"     : ma20_enabled,
                "current_strategy" : current_strategy,
                "stop_loss_pct"    : STOP_LOSS_PCT,
                "trail_activate_pct": TRAIL_ACTIVATE_PCT,
                "breakeven_activate_pct": BREAKEVEN_ACTIVATE_PCT,
                "breakeven_margin_pct"  : BREAKEVEN_MARGIN_PCT,
                "rsi_buy_prev"     : RSI_BUY_PREV,
                "rsi_buy_curr"     : RSI_BUY_CURR,
                "atr_period"       : ATR_PERIOD,
                "atr_multiplier"   : ATR_MULTIPLIER,
                "trail_atr_multiplier": TRAIL_ATR_MULTIPLIER,
                "auto_strategy_enabled": AUTO_STRATEGY_ENABLED,
                "vwap_filter_enabled": VWAP_FILTER_ENABLED,
                "bb_filter_enabled"  : BB_FILTER_ENABLED,
                "inverse_btc_enabled": INVERSE_BTC_ENABLED,
                "inverse_btc_config" : INVERSE_BTC_CONFIG,
                "range_trading_enabled": RANGE_TRADING_ENABLED,
                "trend_parallel_enabled": TREND_PARALLEL_ENABLED,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ حفظ الإعدادات: {e}")

def load_settings():
    global TRADE_AMOUNT, MAX_TRADES, TRAIL_PCT, RSI_WATCH_LOW, RSI_WATCH_HIGH
    global current_interval, ma20_enabled, current_strategy, STOP_LOSS_PCT, TRAIL_ACTIVATE_PCT
    global RSI_BUY_PREV, RSI_BUY_CURR
    global ATR_PERIOD, ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER
    global AUTO_STRATEGY_ENABLED
    global VWAP_FILTER_ENABLED, BB_FILTER_ENABLED, INVERSE_BTC_ENABLED, INVERSE_BTC_CONFIG
    global BREAKEVEN_ACTIVATE_PCT, BREAKEVEN_MARGIN_PCT
    global RANGE_TRADING_ENABLED
    global TREND_PARALLEL_ENABLED
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        TRADE_AMOUNT       = s.get("trade_amount", TRADE_AMOUNT)
        MAX_TRADES          = s.get("max_trades", MAX_TRADES)
        TRAIL_PCT           = s.get("trail_pct", TRAIL_PCT)
        RSI_WATCH_LOW       = s.get("rsi_watch_low", RSI_WATCH_LOW)
        RSI_WATCH_HIGH      = s.get("rsi_watch_high", RSI_WATCH_HIGH)
        minutes             = s.get("interval_minutes")
        intervals_map = {
            15 : Client.KLINE_INTERVAL_15MINUTE,
            30 : Client.KLINE_INTERVAL_30MINUTE,
            60 : Client.KLINE_INTERVAL_1HOUR,
            240: Client.KLINE_INTERVAL_4HOUR,
        }
        if minutes in intervals_map:
            current_interval = intervals_map[minutes]
        ma20_enabled        = s.get("ma20_enabled", ma20_enabled)
        current_strategy    = s.get("current_strategy", current_strategy)
        STOP_LOSS_PCT       = s.get("stop_loss_pct", STOP_LOSS_PCT)
        TRAIL_ACTIVATE_PCT  = s.get("trail_activate_pct", TRAIL_ACTIVATE_PCT)
        BREAKEVEN_ACTIVATE_PCT = s.get("breakeven_activate_pct", BREAKEVEN_ACTIVATE_PCT)
        BREAKEVEN_MARGIN_PCT   = s.get("breakeven_margin_pct", BREAKEVEN_MARGIN_PCT)
        RSI_BUY_PREV        = s.get("rsi_buy_prev", RSI_BUY_PREV)
        RSI_BUY_CURR        = s.get("rsi_buy_curr", RSI_BUY_CURR)
        ATR_PERIOD          = s.get("atr_period", ATR_PERIOD)
        ATR_MULTIPLIER      = s.get("atr_multiplier", ATR_MULTIPLIER)
        TRAIL_ATR_MULTIPLIER = s.get("trail_atr_multiplier", TRAIL_ATR_MULTIPLIER)
        AUTO_STRATEGY_ENABLED = s.get("auto_strategy_enabled", AUTO_STRATEGY_ENABLED)
        VWAP_FILTER_ENABLED = s.get("vwap_filter_enabled", VWAP_FILTER_ENABLED)
        BB_FILTER_ENABLED   = s.get("bb_filter_enabled", BB_FILTER_ENABLED)
        INVERSE_BTC_ENABLED = s.get("inverse_btc_enabled", INVERSE_BTC_ENABLED)
        loaded_inv_config = s.get("inverse_btc_config")
        if loaded_inv_config:
            INVERSE_BTC_CONFIG.update(loaded_inv_config)
            set_inverse_config(**loaded_inv_config)
        RANGE_TRADING_ENABLED = s.get("range_trading_enabled", RANGE_TRADING_ENABLED)
        TREND_PARALLEL_ENABLED = s.get("trend_parallel_enabled", TREND_PARALLEL_ENABLED)
        log.info("✅ تم تحميل الإعدادات المحفوظة من قبل")
    except Exception as e:
        log.error(f"❌ خطأ تحميل الإعدادات: {e}")

# ──────────────────────────────────────────────
# ✅ إصلاح #3+#4: ملف تتبع الأرباح — شهري
# ──────────────────────────────────────────────
def get_profit_file(month=None):
    """يعيد مسار ملف الأرباح للشهر المطلوب (الحالي افتراضياً)"""
    label = month or time.strftime("%Y_%m")
    return os.path.join(DATA_DIR, f"profit_{label}.json")

def load_profit_log(month=None):
    path = get_profit_file(month)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []

def load_all_profit_log():
    """يحمّل كل الملفات الشهرية ويدمجها — للأوامر اللي بتحتاج كل السجلات"""
    all_records = []
    try:
        for fname in sorted(os.listdir(DATA_DIR)):
            if fname.startswith("profit_") and fname.endswith(".json"):
                fpath = os.path.join(DATA_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        all_records.extend(json.load(f))
                except:
                    pass
    except Exception as e:
        log.error(f"❌ خطأ تحميل كل الأرباح: {e}")
    return all_records

def save_profit_log(log_data, month=None):
    path = get_profit_file(month)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ حفظ الأرباح: {e}")

def record_trade_result(symbol, entry_price, exit_price, qty, reason, entry_slippage_pct=0.0):
    """يسجل نتيجة كل صفقة في ملف الشهر الحالي"""
    month       = time.strftime("%Y_%m")
    profit_log  = load_profit_log(month)
    profit      = round((exit_price - entry_price) * qty, 4)
    # ✅ إصلاح: نخزن مبلغ الشراء/البيع الكامل ونسبة التغيّر % مع كل صفقة،
    # عشان تكون جاهزة لعرضها بالإشعارات وبالداشبورد بدون إعادة حساب بأكثر من مكان
    buy_amount  = round(entry_price * qty, 4)
    sell_amount = round(exit_price * qty, 4)
    pct         = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0.0
    profit_log.append({
        "symbol"     : symbol,
        "entry_price": entry_price,
        "exit_price" : exit_price,
        "qty"        : qty,
        "profit"     : profit,
        "buy_amount" : buy_amount,
        "sell_amount": sell_amount,
        "pct"        : pct,
        "reason"     : reason,
        "time"       : time.strftime("%Y-%m-%d %H:%M:%S")
    })
    save_profit_log(profit_log, month)

    # 🧠 نُسجّل نفس النتيجة بذاكرة العملات (coin_memory): فوز/خسارة + الانزلاق،
    # ليستخدمها SmartRanker لاحقاً بترتيب المرشحين. أي خطأ هون ما لازم يوقف تسجيل الربح نفسه.
    try:
        coin_memory.record_trade(
            symbol=symbol,
            is_win=(profit > 0),
            pnl=profit,
            slippage_pct=entry_slippage_pct,
        )
    except Exception as e:
        log.error(f"❌ تسجيل ذاكرة العملة {symbol}: {e}")

# ──────────────────────────────────────────────
# 📱 Push Notifications (Expo) — لازم تكون معرّفة قبل send_telegram
# ──────────────────────────────────────────────
def load_push_tokens():
    if os.path.exists(PUSH_TOKENS_FILE):
        try:
            with open(PUSH_TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []


def save_push_token(token):
    tokens = load_push_tokens()
    if token not in tokens:
        tokens.append(token)
        with open(PUSH_TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 🔔 سجل الإشعارات (يخزن بالسيرفر عشان التطبيق يجيبه حتى لو كان مسكّر وقت الإرسال)
# ──────────────────────────────────────────────
def load_notification_log():
    if os.path.exists(NOTIFICATIONS_FILE):
        try:
            with open(NOTIFICATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []


def save_notification_log(title, body):
    try:
        notifications = load_notification_log()
        new_item = {
            "id"  : str(int(time.time() * 1000)),
            "title": title,
            "body" : body,
            "time" : time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        notifications = [new_item] + notifications
        notifications = notifications[:MAX_NOTIFICATIONS]
        with open(NOTIFICATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(notifications, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ حفظ سجل الإشعارات: {e}")


def clear_notification_log():
    try:
        with open(NOTIFICATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ مسح سجل الإشعارات: {e}")


def send_push_notification(title, body):
    tokens = load_push_tokens()
    if not tokens:
        return []

    results = []
    for token in tokens:
        try:
            r = requests.post(
                "https://exp.host/--/api/v2/push/send",
                json={"to": token, "title": title, "body": body, "sound": "default"},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = r.json()
            # ✅ Expo يرجع status 200 حتى لو التوكن غلط أو منتهي — لازم نفحص محتوى الرد
            ticket = data.get("data", {})
            if isinstance(ticket, list):
                ticket = ticket[0] if ticket else {}
            status = ticket.get("status", "unknown")
            if status != "ok":
                err_msg = ticket.get("message", "بدون تفاصيل")
                err_type = ticket.get("details", {}).get("error", "")
                log.error(f"❌ Expo رفض التوكن {token[:20]}...: {err_type} | {err_msg}")
                results.append({"token": token, "ok": False, "error": f"{err_type}: {err_msg}"})
            else:
                results.append({"token": token, "ok": True})
        except Exception as e:
            log.error(f"❌ خطأ إرسال إشعار: {e}")
            results.append({"token": token, "ok": False, "error": str(e)})
    return results

# ──────────────────────────────────────────────
# 📨 تيليغرام
# ──────────────────────────────────────────────
def send_telegram(message):
    # ✅ إصلاح: إرسال Push Notification تلقائياً مع كل رسالة تيليغرام
    clean = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")

    # 🔔 تسجيل الإشعار بسجل السيرفر عشان التطبيق يقدر يجيبه حتى لو كان مسكّر وقت الإرسال
    try:
        save_notification_log("Crypto Bot", clean)
    except Exception as e:
        log.error(f"❌ خطأ حفظ سجل الإشعار من send_telegram: {e}")

    try:
        send_push_notification("Crypto Bot", clean)
    except Exception as e:
        log.error(f"❌ خطأ push من send_telegram: {e}")

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
    chat = TELEGRAM_ADMIN_ID or TELEGRAM_CHAT_ID
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
# أوامر تيليغرام
# ──────────────────────────────────────────────
def telegram_command_listener(client):
    global SYMBOLS, trading_enabled, ma20_enabled, current_interval, current_strategy
    global TRADE_AMOUNT, MAX_TRADES, TRAIL_PCT, RSI_WATCH_LOW, RSI_WATCH_HIGH
    global pause_until, consecutive_losses, STOP_LOSS_PCT, TRAIL_ACTIVATE_PCT, RSI_BUY_PREV, RSI_BUY_CURR
    global BREAKEVEN_ACTIVATE_PCT, BREAKEVEN_MARGIN_PCT
    global ATR_PERIOD, ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER
    global AUTO_STRATEGY_ENABLED
    global VWAP_FILTER_ENABLED, BB_FILTER_ENABLED
    global INVERSE_BTC_ENABLED, INVERSE_BTC_CONFIG
    global RANGE_TRADING_ENABLED
    global TREND_PARALLEL_ENABLED
    offset = None  # ✅ إصلاح: None يعني "لسا ما تأكدنا من offset الصحيح"

    for attempt in range(3):   # ✅ إصلاح: 3 محاولات بدل محاولة وحيدة
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", timeout=10).json()
            if r.get("result"):
                offset = r["result"][-1]["update_id"] + 1
            else:
                offset = 0
            break
        except Exception as e:
            log.error(f"❌ خطأ offset تيليغرام (محاولة {attempt+1}/3): {e}")
            time.sleep(2)

    if offset is None:
        # ✅ إصلاح: فشلت كل المحاولات — لا نبدأ من 0 لأن هذا يعيد تنفيذ
        # كل الأوامر القديمة المعلّقة (مثل /stop أو /add قديمة). أفضل نتجاهلها
        # ونبدأ نستقبل من اللحظة الحالية فقط، بدل تنفيذ أوامر قديمة بالغلط.
        offset = -1
        log.warning("⚠️ تعذّر تأكيد offset تيليغرام — سيتم تجاهل أي رسائل قديمة معلّقة لتجنب تنفيذها بالغلط.")

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
                        with _lock:   # ✅ إصلاح #1
                            trading_enabled = False
                        send_admin("⏸️ <b>تم إيقاف التداول.</b>\nالصفقات المفتوحة لا تزال تحت المراقبة.")

                    # ── /start ────────────────────────────────
                    elif text == "/start":
                        with _lock:   # ✅ إصلاح #1
                            trading_enabled = True
                            pause_until     = 0   # 🛑 إلغاء أي توقف تلقائي معلّق
                        consecutive_losses = 0
                        save_circuit_state()
                        send_admin("▶️ <b>تم استئناف التداول.</b>")

                    # ── /status ───────────────────────────────
                    elif text == "/status":
                        try:
                            usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                        except:
                            usdt_balance = 0.0
                        with _lock:
                            status           = "▶️ شغال" if trading_enabled else "⏸️ موقوف"
                            trades_count     = len(open_trades)
                            active_count     = count_active_trades()
                            trades_copy      = dict(open_trades)
                            pause_left       = pause_until
                            strategy_label   = STRATEGY_LABELS.get(current_strategy, current_strategy)
                            interval_minutes = INTERVAL_TO_MINUTES.get(current_interval, 30)
                            ma20_status      = "✅ مفعّل" if ma20_enabled else "❌ مطفي"
                            auto_status      = "🧠 تلقائي" if AUTO_STRATEGY_ENABLED else "✋ يدوي"
                        msg = (
                            f"📊 <b>حالة البوت</b>\n"
                            f"🔘 التداول: {status}\n"
                            f"💰 USDT المتاح: ${usdt_balance:.2f}\n"
                            f"💼 صفقات نشطة: {active_count}/{MAX_TRADES} | إجمالي مفتوحة: {trades_count}\n"
                            f"👁️ يراقب: {len(SYMBOLS)} عملة\n"
                            f"🔍 مراقبة مكثفة: {len(watch_list)} عملة\n"
                            f"📊 الاستراتيجية: {strategy_label} ({auto_status})\n"
                            f"🕯️ الفريم: {interval_minutes} دقيقة\n"
                            f"📈 MA20: {ma20_status}\n"
                        )
                        if pause_left and pause_left > time.time():
                            remaining_min = int((pause_left - time.time()) / 60)
                            msg += f"🛑 توقف تلقائي مفعّل — يُستأنف بعد {remaining_min} دقيقة\n"
                        if trades_copy:
                            msg += "\n<b>الصفقات المفتوحة:</b>\n"
                            for sym, t in trades_copy.items():
                                coin  = sym.replace("USDT", "")
                                trail = "✅" if t.get("trailing_active") else "⏳"
                                msg  += f"  #{coin} | دخول: {t['entry_price']:.4f}$ | Trailing: {trail}\n"
                        send_admin(msg)

                    # ── /set_trade_amount ────────────────────
                    elif text.startswith("/set_trade_amount "):
                        try:
                            amount = float(text.replace("/set_trade_amount ", ""))
                            if amount <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:   # ✅ إصلاح: حماية race condition
                                    TRADE_AMOUNT = amount
                                save_settings()
                                send_admin(f"✅ حجم الصفقة الجديد: ${TRADE_AMOUNT}")
                        except:
                            send_admin("❌ مثال: /set_trade_amount 20")

                    # ── /set_max_trades ───────────────────────
                    elif text.startswith("/set_max_trades "):
                        try:
                            value = int(text.replace("/set_max_trades ", ""))
                            if value <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:   # ✅ إصلاح: حماية race condition
                                    MAX_TRADES = value
                                save_settings()
                                send_admin(f"✅ أقصى صفقات نشطة (قبل تفعيل Trailing) بنفس الوقت: {MAX_TRADES}")
                        except:
                            send_admin("❌ مثال: /set_max_trades 2")

                    # ── /set_trail ────────────────────────────
                    elif text.startswith("/set_trail "):
                        try:
                            value = float(text.replace("/set_trail ", "")) / 100
                            if value <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:   # ✅ إصلاح: حماية race condition
                                    TRAIL_PCT = value
                                save_settings()
                                send_admin(f"✅ Trailing Stop: {TRAIL_PCT*100}%")
                        except:
                            send_admin("❌ مثال: /set_trail 1.5")

                    # ── /set_stoploss ─────────────────────────
                    elif text.startswith("/set_stoploss "):
                        try:
                            value = float(text.replace("/set_stoploss ", "")) / 100
                            if value <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:
                                    STOP_LOSS_PCT = value
                                save_settings()
                                send_admin(f"✅ حد الخسارة الثابت (Stop Loss): {STOP_LOSS_PCT*100}%\nيعني لو السعر نزل {STOP_LOSS_PCT*100}% من سعر الدخول، البوت يبيع تلقائياً.")
                        except:
                            send_admin("❌ مثال: /set_stoploss 1.5")

                    # ── /set_activate ─────────────────────────
                    elif text.startswith("/set_activate "):
                        try:
                            value = float(text.replace("/set_activate ", "")) / 100
                            if value <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:
                                    TRAIL_ACTIVATE_PCT = value
                                save_settings()
                                send_admin(f"✅ نقطة تفعيل Trailing: {TRAIL_ACTIVATE_PCT*100}%\nيعني البوت ما يبدأ يتتبع السعر إلا لما يربح {TRAIL_ACTIVATE_PCT*100}% أول.")
                        except:
                            send_admin("❌ مثال: /set_activate 0.5")

                    # ── /set_breakeven ─────────────────────────
                    elif text.startswith("/set_breakeven "):
                        try:
                            parts    = text.replace("/set_breakeven ", "").split()
                            activate = float(parts[0]) / 100
                            margin   = float(parts[1]) / 100
                            if activate <= 0 or margin < 0:
                                send_admin("❌ نقطة التفعيل لازم أكبر من صفر، والهامش أكبر من أو يساوي صفر.")
                            else:
                                with _lock:
                                    BREAKEVEN_ACTIVATE_PCT = activate
                                    BREAKEVEN_MARGIN_PCT   = margin
                                save_settings()
                                send_admin(
                                    f"✅ حماية التعادل: تفعيل عند {BREAKEVEN_ACTIVATE_PCT*100}% ربح، "
                                    f"هامش {BREAKEVEN_MARGIN_PCT*100}% فوق سعر الدخول\n"
                                    f"يعني أول ما تربح الصفقة {BREAKEVEN_ACTIVATE_PCT*100}%، الستوب ينتقل فوراً "
                                    f"لسعر الدخول + {BREAKEVEN_MARGIN_PCT*100}% (يغطي العمولة)، ولا ينزل تحته أبداً."
                                )
                        except:
                            send_admin("❌ مثال: /set_breakeven 0.5 0.2  (تفعيل 0.5%، هامش 0.2%)")

                    # ── /set_rsi_range ────────────────────────
                    elif text.startswith("/set_rsi_range "):
                        try:
                            parts = text.replace("/set_rsi_range ", "").split()
                            low  = float(parts[0])
                            high = float(parts[1])
                            if low <= 0 or high <= 0 or low >= high:
                                send_admin("❌ لازم القيمة الأولى أصغر من الثانية وكلاهما أكبر من صفر.")
                            else:
                                with _lock:   # ✅ إصلاح: حماية race condition
                                    RSI_WATCH_LOW  = low
                                    RSI_WATCH_HIGH = high
                                save_settings()
                                send_admin(f"✅ منطقة RSI: {RSI_WATCH_LOW} - {RSI_WATCH_HIGH}")
                        except:
                            send_admin("❌ مثال: /set_rsi_range 20 38")

                    # ── /set_atr_period ────────────────────────
                    elif text.startswith("/set_atr_period "):
                        try:
                            value = int(text.replace("/set_atr_period ", ""))
                            if value <= 1:
                                send_admin("❌ القيمة لازم تكون أكبر من 1.")
                            else:
                                with _lock:
                                    ATR_PERIOD = value
                                save_settings()
                                send_admin(f"✅ فترة حساب ATR: {ATR_PERIOD} شمعة")
                        except:
                            send_admin("❌ مثال: /set_atr_period 14")

                    # ── /set_atr_multiplier ────────────────────
                    elif text.startswith("/set_atr_multiplier "):
                        try:
                            value = float(text.replace("/set_atr_multiplier ", ""))
                            if value <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:
                                    ATR_MULTIPLIER = value
                                save_settings()
                                send_admin(f"✅ مضاعف ATR لمسافة الستوب الأولي: {ATR_MULTIPLIER}x")
                        except:
                            send_admin("❌ مثال: /set_atr_multiplier 2")

                    # ── /set_trail_atr_multiplier ──────────────
                    elif text.startswith("/set_trail_atr_multiplier "):
                        try:
                            value = float(text.replace("/set_trail_atr_multiplier ", ""))
                            if value <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:
                                    TRAIL_ATR_MULTIPLIER = value
                                save_settings()
                                send_admin(f"✅ مضاعف ATR لمسافة الـ Trailing: {TRAIL_ATR_MULTIPLIER}x")
                        except:
                            send_admin("❌ مثال: /set_trail_atr_multiplier 1.5")

                    # ── /set_vwap_filter ────────────────────────
                    elif text.startswith("/set_vwap_filter "):
                        value = text.replace("/set_vwap_filter ", "").strip().lower()
                        if value in ("on", "off"):
                            with _lock:
                                VWAP_FILTER_ENABLED = (value == "on")
                            save_settings()
                            status_txt = "✅ مفعّل" if VWAP_FILTER_ENABLED else "❌ مطفي"
                            send_admin(f"{status_txt} فلتر VWAP — الشراء هلق يشترط السعر فوق VWAP" if VWAP_FILTER_ENABLED else "❌ تم إطفاء فلتر VWAP")
                        else:
                            send_admin("❌ مثال: /set_vwap_filter on  أو  /set_vwap_filter off")

                    # ── /set_bb_filter ──────────────────────────
                    elif text.startswith("/set_bb_filter "):
                        value = text.replace("/set_bb_filter ", "").strip().lower()
                        if value in ("on", "off"):
                            with _lock:
                                BB_FILTER_ENABLED = (value == "on")
                            save_settings()
                            status_txt = "✅ مفعّل" if BB_FILTER_ENABLED else "❌ مطفي"
                            send_admin(f"{status_txt} فلتر Bollinger Bands — الشراء هلق يشترط قرب السعر من الحد السفلي" if BB_FILTER_ENABLED else "❌ تم إطفاء فلتر Bollinger Bands")
                        else:
                            send_admin("❌ مثال: /set_bb_filter on  أو  /set_bb_filter off")

                    # ── /set_range_trading (تشغيل/إيقاف استراتيجية Range Trading BTC المستقلة) ──
                    elif text.startswith("/set_range_trading "):
                        value = text.replace("/set_range_trading ", "").strip().lower()
                        if value in ("on", "off"):
                            with _lock:
                                RANGE_TRADING_ENABLED = (value == "on")
                            save_settings()
                            status_txt = "✅ مفعّلة" if RANGE_TRADING_ENABLED else "❌ متوقفة"
                            send_admin(
                                f"{status_txt} استراتيجية Range Trading BTC\n"
                                f"↳ صفقات حقيقية على BTCUSDT بمبلغ {RANGE_TRADING_USDT_PER_TRADE} USDT/صفقة، صفقة وحدة بس بأي لحظة"
                                if RANGE_TRADING_ENABLED else
                                "❌ تم إيقاف Range Trading BTC — أي صفقة مفتوحة حالياً بتضل مفتوحة لحد ما توقف عادي (مقاومة/وقف خسارة)، بس ما رح تنفتح صفقة جديدة"
                            )
                        else:
                            send_admin("❌ مثال: /set_range_trading on  أو  /set_range_trading off")

                    # ── /set_trend_parallel (تشغيل/إيقاف استراتيجية Trend+Stoch الموازية المستقلة) ──
                    elif text.startswith("/set_trend_parallel "):
                        value = text.replace("/set_trend_parallel ", "").strip().lower()
                        if value in ("on", "off"):
                            with _lock:
                                TREND_PARALLEL_ENABLED = (value == "on")
                            save_settings()
                            status_txt = "✅ مفعّلة" if TREND_PARALLEL_ENABLED else "❌ متوقفة"
                            send_admin(
                                f"{status_txt} استراتيجية Trend+Stoch الموازية\n"
                                f"↳ صفقات حقيقية بمبلغ {TREND_PARALLEL_USDT_PER_TRADE} USDT/صفقة، فريم ساعة، صفقة وحدة بس بأي لحظة"
                                if TREND_PARALLEL_ENABLED else
                                "❌ تم إيقاف Trend+Stoch الموازية — أي صفقة مفتوحة حالياً بتضل تكمل لحد ما توقف عادي (ستوب/ترايلنك)، بس ما رح تنفتح صفقة جديدة"
                            )
                        else:
                            send_admin("❌ مثال: /set_trend_parallel on  أو  /set_trend_parallel off")

                    # ── /trend_parallel_status ──────────────────────
                    elif text == "/trend_parallel_status":
                        with _lock:
                            tp_status = "✅ مفعّلة" if TREND_PARALLEL_ENABLED else "❌ متوقفة"
                        pos = _trend_parallel_strategy.position if _trend_parallel_strategy else None
                        if pos:
                            pos_line = (
                                f"📍 صفقة مفتوحة: {pos['symbol'].replace('USDT','')} | دخول {pos['entry_price']:.6f} | "
                                f"ستوب حالي {pos['stop_loss']:.6f} | Trailing: {'مفعّل' if pos['trailing_active'] else 'غير مفعّل'}"
                            )
                        else:
                            pos_line = "📍 لا يوجد صفقة مفتوحة حالياً"
                        send_admin(
                            f"📈 <b>Trend+Stoch الموازية</b>\n"
                            f"الحالة: {tp_status}\n"
                            f"المبلغ لكل صفقة: {TREND_PARALLEL_USDT_PER_TRADE} USDT\n"
                            f"{pos_line}"
                        )

                    # ── /range_trading_status ──────────────────────
                    elif text == "/range_trading_status":
                        with _lock:
                            rt_status = "✅ مفعّلة" if RANGE_TRADING_ENABLED else "❌ متوقفة"
                        pos = _range_trading_strategy.position if _range_trading_strategy else None
                        if pos:
                            pos_line = (
                                f"📍 صفقة مفتوحة: دخول {pos['entry_price']:.2f} | "
                                f"دعم {pos['support']:.2f} | مقاومة {pos['resistance']:.2f} | "
                                f"وقف خسارة {pos['stop_loss_price']:.2f}"
                            )
                        else:
                            pos_line = "📍 لا يوجد صفقة مفتوحة حالياً"
                        send_admin(
                            f"📊 <b>Range Trading BTC</b>\n"
                            f"الحالة: {rt_status}\n"
                            f"المبلغ لكل صفقة: {RANGE_TRADING_USDT_PER_TRADE} USDT\n"
                            f"{pos_line}"
                        )

                    # ── /filters_status ──────────────────────────
                    elif text == "/filters_status":
                        with _lock:
                            vwap_status = "✅ مفعّل" if VWAP_FILTER_ENABLED else "❌ مطفي"
                            bb_status   = "✅ مفعّل" if BB_FILTER_ENABLED else "❌ مطفي"
                        send_admin(
                            f"📐 <b>فلاتر التأكيد الإضافية</b>\n\n"
                            f"VWAP: {vwap_status}\n"
                            f"↳ لو مفعّل: نشتري بس لو السعر فوق VWAP (تأكيد قوة شراء حقيقية بالحجم)\n\n"
                            f"Bollinger Bands: {bb_status}\n"
                            f"↳ لو مفعّل: نشتري بس لو السعر قريب من الحد السفلي (ارتداد حقيقي من قاع، مو نزول مستمر)\n\n"
                            f"🏆 ترتيب الزخم (Momentum Score): شغال دائماً — لو فيه أكثر من مرشح شراء بنفس دورة الفحص، "
                            f"البوت يشتري الأقوى أولاً (فوليوم أعلى + حركة سعر أوضح)."
                        )

                    # ── /set_inverse_btc ─────────────────────────
                    elif text.startswith("/set_inverse_btc "):
                        value = text.replace("/set_inverse_btc ", "").strip().lower()
                        if value in ("on", "off"):
                            with _lock:
                                INVERSE_BTC_ENABLED = (value == "on")
                            save_settings()
                            status_txt = "✅ مفعّل" if INVERSE_BTC_ENABLED else "❌ مطفي"
                            send_admin(f"{status_txt} استراتيجية Inverse BTC")
                        else:
                            send_admin("❌ مثال: /set_inverse_btc on  أو  /set_inverse_btc off")

                    # ── /set_inverse_config ──────────────────────
                    elif text.startswith("/set_inverse_config "):
                        try:
                            parts = text.replace("/set_inverse_config ", "").strip().split()
                            key = parts[0]
                            val = float(parts[1])
                            valid_keys = ["btc_decline_threshold_pct", "rs_min_threshold_pct", "min_volume_ratio"]
                            if key not in valid_keys:
                                send_admin(f"❌ المفتاح غير صحيح. الصح: {', '.join(valid_keys)}")
                            else:
                                with _lock:
                                    INVERSE_BTC_CONFIG[key] = val
                                    set_inverse_config(**{key: val})
                                save_settings()
                                send_admin(f"✅ Inverse BTC — {key} = {val}")
                        except Exception as e:
                            send_admin(f"❌ خطأ: {e}\nمثال: /set_inverse_config btc_decline_threshold_pct 3.0")

                    # ── /inverse_status ──────────────────────────
                    elif text == "/inverse_status":
                        with _lock:
                            enabled = INVERSE_BTC_ENABLED
                            cfg = dict(INVERSE_BTC_CONFIG)
                        status_txt = "✅ مفعّل" if enabled else "❌ مطفي"
                        send_admin(
                            f"🔄 <b>Inverse BTC</b>\n\n"
                            f"الحالة: {status_txt}\n\n"
                            f"الإعدادات:\n"
                            f"📉 حد نزول BTC: {cfg['btc_decline_threshold_pct']}%\n"
                            f"💪 قوة نسبية min: {cfg['rs_min_threshold_pct']}%\n"
                            f"🔊 فوليوم min: {cfg['min_volume_ratio']}×\n\n"
                            f"المنطق: لما BTC ينزل {cfg['btc_decline_threshold_pct']}% أو أكثر، "
                            f"البوت يبحث عن عملات أقوى من BTC بـ {cfg['rs_min_threshold_pct']}% على الأقل."
                        )

                    # ── /atr_status ─────────────────────────────
                    elif text == "/atr_status":
                        with _lock:
                            period          = ATR_PERIOD
                            multiplier      = ATR_MULTIPLIER
                            trail_multiplier = TRAIL_ATR_MULTIPLIER
                        send_admin(
                            f"📐 <b>الستوب لوس المتحرك حسب ATR</b>\n\n"
                            f"⏱️ فترة الحساب: {period} شمعة\n"
                            f"✖️ مضاعف الستوب الأولي: {multiplier}x\n"
                            f"✖️ مضاعف Trailing بعد التفعيل: {trail_multiplier}x\n\n"
                            f"عند الشراء: Stop Loss = سعر الدخول - ({multiplier} × ATR)\n"
                            f"بعد تفعيل Trailing: Stop Loss = أعلى سعر - ({trail_multiplier} × ATR)\n"
                            f"الستوب يتحرك لأعلى بس (يحمي الأرباح)، وما ينزل أبداً مع نزول السعر.\n"
                            f"كل عملة تاخذ مساحة تنفس تناسب تقلبها الخاص بدل نسبة ثابتة للكل.\n"
                            f"لو تعذّر حساب ATR لأي سبب، يرجع البوت للنسب الثابتة (Stop Loss % / Trailing %) كاحتياطي."
                        )

                    # ── /set_interval ────────────────────────
                    elif text.startswith("/set_interval "):
                        try:
                            minutes = int(text.replace("/set_interval ", ""))
                            intervals = {
                                15 : Client.KLINE_INTERVAL_15MINUTE,
                                30 : Client.KLINE_INTERVAL_30MINUTE,
                                60 : Client.KLINE_INTERVAL_1HOUR,
                                240: Client.KLINE_INTERVAL_4HOUR,
                            }
                            if minutes in intervals:
                                with _lock:   # ✅ إصلاح: حماية race condition
                                    current_interval = intervals[minutes]
                                save_settings()
                                send_admin(f"✅ تم تغيير الفريم إلى {minutes} دقيقة")
                                log.info(f"📊 الفريم الجديد: {minutes} دقيقة")
                            else:
                                send_admin(f"❌ الفريم المسموح: 15, 30, 60, 240 دقيقة")
                        except Exception as e:
                            send_admin(f"❌ خطأ: {e}")

                    # ── /enable_ma20 ─────────────────────────
                    elif text == "/enable_ma20":
                        with _lock:   # ✅ إصلاح #1
                            ma20_enabled = True
                        save_settings()
                        send_admin("✅ تم تفعيل فيلتر MA20")

                    # ── /disable_ma20 ────────────────────────
                    elif text == "/disable_ma20":
                        with _lock:   # ✅ إصلاح #1
                            ma20_enabled = False
                        save_settings()
                        send_admin("❌ تم تعطيل فيلتر MA20 — الشراء بناءً على RSI فقط")

                    # ── /profit ──────────────────────────────
                    elif text.startswith("/profit"):
                        period = "today" if "today" in text else "all"
                        today  = time.strftime("%Y-%m-%d")

                        if period == "today":
                            records = [r for r in load_profit_log() if r["time"].startswith(today)]
                            label   = "اليوم"
                        else:
                            records = load_all_profit_log()   # ✅ كل الشهور
                            label   = "الكل"

                        if not records:
                            send_admin(f"📊 لا توجد صفقات مغلقة ({label})")
                        else:
                            total  = round(sum(r["profit"] for r in records), 4)
                            wins   = sum(1 for r in records if r["profit"] > 0)
                            losses = len(records) - wins
                            msg    = (
                                f"💰 <b>الأرباح ({label})</b>\n"
                                f"📈 إجمالي: {total:+.4f} USDT\n"
                                f"✅ رابحة: {wins} | ❌ خاسرة: {losses}\n"
                                f"📊 إجمالي صفقات: {len(records)}"
                            )
                            send_admin(msg)

                    # ── /summary ──────────────────────────────
                    elif text == "/summary":
                        profit_log = load_all_profit_log()   # ✅ كل الشهور
                        if not profit_log:
                            send_admin("📊 لا توجد صفقات مسجلة بعد.")
                        else:
                            total      = round(sum(r["profit"] for r in profit_log), 4)
                            wins       = [r for r in profit_log if r["profit"] > 0]
                            losses     = [r for r in profit_log if r["profit"] <= 0]
                            best       = max(profit_log, key=lambda x: x["profit"])
                            worst      = min(profit_log, key=lambda x: x["profit"])
                            win_rate   = round(len(wins) / len(profit_log) * 100, 1)
                            avg_win    = round(sum(r["profit"] for r in wins) / len(wins), 4) if wins else 0
                            avg_loss   = round(sum(r["profit"] for r in losses) / len(losses), 4) if losses else 0
                            msg = (
                                f"📊 <b>ملخص الأداء الكامل</b>\n\n"
                                f"💼 إجمالي الصفقات: {len(profit_log)}\n"
                                f"💰 إجمالي الربح: {total:+.4f} USDT\n"
                                f"🎯 نسبة الفوز: {win_rate}%\n"
                                f"✅ صفقات رابحة: {len(wins)}\n"
                                f"❌ صفقات خاسرة: {len(losses)}\n"
                                f"📈 متوسط الربح: {avg_win:+.4f} USDT\n"
                                f"📉 متوسط الخسارة: {avg_loss:+.4f} USDT\n"
                                f"🏆 أفضل صفقة: {best['symbol']} ({best['profit']:+.4f})\n"
                                f"💔 أسوأ صفقة: {worst['symbol']} ({worst['profit']:+.4f})"
                            )
                            send_admin(msg)

                    # ── /close ───────────────────────────────
                    elif text.startswith("/close "):
                        coin   = text.replace("/close ", "").strip().upper()
                        symbol = f"{coin}USDT"
                        sell_price, status = close_trade(client, symbol)
                        if status == "not_found":
                            send_admin(f"⚠️ مافي صفقة مفتوحة لـ {coin}.")
                        elif status == "sell_failed":
                            send_admin(f"❌ فشل إغلاق صفقة {coin}. تحقق من اللوق.")
                        elif status == "no_balance_removed":
                            # ✅ إصلاح: ما كان فيه رصيد فعلي للعملة، تم تنظيف الصفقة من السجل فقط
                            send_admin(f"⚠️ {coin}: لا يوجد رصيد فعلي لهذي العملة في المحفظة.\nتم حذف الصفقة من سجل البوت لتجنب التعليق.")
                        else:
                            # ملاحظة: السعر جُلب بعد الحذف، نحسب PnL ومبلغ الشراء/البيع والنسبة % من السجل
                            profit_log = load_profit_log()
                            last       = next((r for r in reversed(profit_log) if r["symbol"] == symbol), None)
                            if last:
                                pnl_line     = f"💹 PnL: {last['profit']:+.4f} USDT ({last.get('pct', 0):+.2f}%)"
                                amounts_line = f"🧾 مبلغ الشراء: {last.get('buy_amount', 0):.4f} USDT → مبلغ البيع: {last.get('sell_amount', 0):.4f} USDT\n"
                            else:
                                pnl_line     = "💹 PnL: —"
                                amounts_line = ""
                            send_admin(
                                f"🔴 <b>إغلاق يدوي - {coin}</b>\n"
                                f"💵 سعر البيع: {sell_price:.4f}$\n"
                                f"{amounts_line}"
                                f"{pnl_line}"
                            )

                    # ── /set_buy_rsi ──────────────────────────
                    elif text.startswith("/set_buy_rsi "):
                        try:
                            parts = text.replace("/set_buy_rsi ", "").split()
                            prev  = float(parts[0])
                            curr  = float(parts[1])
                            if prev <= 0 or curr <= 0 or prev >= curr:
                                send_admin("❌ القيمة الأولى لازم أصغر من الثانية.")
                            else:
                                with _lock:
                                    RSI_BUY_PREV = prev
                                    RSI_BUY_CURR = curr
                                save_settings()
                                send_admin(f"✅ شرط الشراء: RSI السابق أقل من {RSI_BUY_PREV} والحالي أكبر من {RSI_BUY_CURR}")
                        except:
                            send_admin("❌ مثال: /set_buy_rsi 25 30")

                    # ── /set_strategy ─────────────────────────
                    elif text.startswith("/set_strategy "):
                        strategy = text.replace("/set_strategy ", "").strip().lower()
                        if strategy in ("rsi", "stoch_rsi", "trend_stoch", "inverse_btc"):
                            with _lock:
                                current_strategy = strategy
                                auto_on = AUTO_STRATEGY_ENABLED
                            # ✅ إصلاح خلل: نزامن ذاكرة الكاشف مع الاختيار اليدوي، وإلا يضل تايه
                            # عن الواقع ويتوقف عن التبديل الفعلي (يظهر "متجمد")
                            if _regime_detector:
                                _regime_detector.current_strategy = strategy
                                _regime_detector.last_switch_time = time.time()
                            save_settings()
                            labels = {
                                "rsi"        : "RSI العادي (ارتداد فوق 30)",
                                "stoch_rsi"  : "Stochastic RSI (تقاطع K فوق D واختراق 20)",
                                "trend_stoch": "Trend + StochRSI (تأكيد اتجاه 4 ساعات + فوليوم)",
                                "inverse_btc": "Inverse BTC (شراء العملات اللي بتقاوم نزول BTC)",
                            }
                            msg = f"✅ تم تغيير الاستراتيجية إلى: {labels[strategy]}"
                            if auto_on:
                                msg += "\n⚠️ التبديل التلقائي مفعّل — ممكن يبدلها تلقائياً بعد فترة التبريد (20 دقيقة) لو حالة السوق تغيّرت. أوقفه بـ /set_auto_strategy off لو تبي تثبيتها يدوياً."
                            send_admin(msg)
                        else:
                            send_admin("❌ الاستراتيجيات المتاحة:\n/set_strategy rsi\n/set_strategy stoch_rsi\n/set_strategy trend_stoch\n/set_strategy inverse_btc")

                    # ── /set_auto_strategy ─────────────────────
                    elif text.startswith("/set_auto_strategy "):
                        value = text.replace("/set_auto_strategy ", "").strip().lower()
                        if value in ("on", "off"):
                            with _lock:
                                AUTO_STRATEGY_ENABLED = (value == "on")
                                strategy_now = current_strategy
                            save_settings()
                            if value == "on":
                                # ✅ إصلاح خلل: نزامن ذاكرة الكاشف مع الاستراتيجية الحالية الفعلية
                                # وقت التفعيل (ممكن تكون تغيّرت يدوياً وقت ما كان التبديل مطفي)،
                                # ونبدأ فترة تبريد جديدة من هاللحظة عشان نعطي فرصة للاختيار الحالي.
                                if _regime_detector:
                                    _regime_detector.current_strategy = strategy_now
                                    _regime_detector.last_switch_time = time.time()
                                send_admin(
                                    "✅ <b>تم تفعيل التبديل التلقائي بين الاستراتيجيات</b>\n"
                                    "البوت رح يحلل حالة السوق (اتجاه + تذبذب) كل 15 دقيقة، "
                                    "ويبدل الاستراتيجية تلقائياً لما يلزم، مع تنبيه فوري بكل تبديل."
                                )
                            else:
                                send_admin("⏸️ تم إيقاف التبديل التلقائي — الاستراتيجية صارت يدوية بالكامل (/set_strategy).")
                        else:
                            send_admin("❌ مثال: /set_auto_strategy on  أو  /set_auto_strategy off")

                    # ── /auto_strategy_status ──────────────────
                    elif text == "/auto_strategy_status":
                        with _lock:
                            auto_on = AUTO_STRATEGY_ENABLED
                            strategy_now = current_strategy
                        status_txt = "✅ مفعّل" if auto_on else "⏸️ مطفي"
                        msg = (
                            f"🧠 <b>التبديل التلقائي بين الاستراتيجيات</b>\n\n"
                            f"الحالة: {status_txt}\n"
                            f"الاستراتيجية الحالية: {STRATEGY_LABELS.get(strategy_now, strategy_now)}\n\n"
                            f"المنطق:\n"
                            f"📈 ترند واضح (BTC فوق MA50 على 4س) → Trend+StochRSI\n"
                            f"📊 جانبي + تذبذب عالٍ → Stochastic RSI\n"
                            f"😴 جانبي + هادئ → RSI العادي\n\n"
                            f"فحص كل 15 دقيقة، مع فترة تبريد 20 دقيقة بين كل تبديل وتاني."
                        )
                        send_admin(msg)

                    # ── /config ───────────────────────────────
                    elif text == "/config":
                        with _lock:
                            strategy_label   = STRATEGY_LABELS.get(current_strategy, current_strategy)
                            interval_minutes = INTERVAL_TO_MINUTES.get(current_interval, 30)
                            ma20_status      = "✅ مفعّل" if ma20_enabled else "❌ مطفي"
                            trade_amt        = TRADE_AMOUNT
                            max_tr           = MAX_TRADES
                            trail            = TRAIL_PCT * 100
                            stoploss         = STOP_LOSS_PCT * 100
                            activate         = TRAIL_ACTIVATE_PCT * 100
                            be_activate      = BREAKEVEN_ACTIVATE_PCT * 100
                            be_margin        = BREAKEVEN_MARGIN_PCT * 100
                            rsi_prev         = RSI_BUY_PREV
                            rsi_curr         = RSI_BUY_CURR
                            atr_period_val   = ATR_PERIOD
                            atr_mult_val     = ATR_MULTIPLIER
                            trail_atr_val    = TRAIL_ATR_MULTIPLIER
                            auto_strategy_status = "✅ مفعّل" if AUTO_STRATEGY_ENABLED else "❌ مطفي"
                            vwap_status_cfg  = "✅" if VWAP_FILTER_ENABLED else "❌"
                            bb_status_cfg    = "✅" if BB_FILTER_ENABLED else "❌"
                            inv_status_cfg   = "✅" if INVERSE_BTC_ENABLED else "❌"
                            inv_cfg = dict(INVERSE_BTC_CONFIG)
                        send_admin(
                            f"⚙️ <b>الإعدادات الحالية</b>\n\n"
                            f"📊 الاستراتيجية: {strategy_label}\n"
                            f"🧠 التبديل التلقائي: {auto_strategy_status}\n"
                            f"🕯️ الفريم: {interval_minutes} دقيقة\n"
                            f"📈 MA20: {ma20_status}\n"
                            f"💰 حجم الصفقة: ${trade_amt}\n"
                            f"💼 أقصى صفقات: {max_tr}\n"
                            f"🛑 حد الخسارة الاحتياطي (Stop Loss %): {stoploss}%\n"
                            f"🎯 تفعيل Trailing عند: {activate}% ربح\n"
                            f"🛡️ حماية التعادل: تفعيل عند {be_activate}% ربح | هامش {be_margin}% فوق الدخول\n"
                            f"🔍 مساحة Trailing الاحتياطية: {trail}%\n"
                            f"📩 شرط الشراء: RSI السابق أصغر من {RSI_BUY_PREV} | الحالي >= {RSI_BUY_CURR}\n"
                            f"📐 ATR: فترة {atr_period_val} شمعة | مضاعف الستوب {atr_mult_val}x | مضاعف Trailing {trail_atr_val}x\n"
                            f"📊 فلاتر تأكيد: VWAP {vwap_status_cfg} | Bollinger {bb_status_cfg}\n"
                            f"🔄 Inverse BTC: {inv_status_cfg} | نزول BTC: {inv_cfg['btc_decline_threshold_pct']}% | قوة نسبية: {inv_cfg['rs_min_threshold_pct']}%"
                        )

                    # ── /push_status ──────────────────────────
                    elif text == "/push_status":
                        tokens = load_push_tokens()
                        if not tokens:
                            send_admin("📵 لا يوجد أي جهاز مسجل لاستقبال Push Notifications بعد.\nلازم تفتح تطبيق الموبايل وتوافق على إذن الإشعارات.")
                        else:
                            send_admin(f"📱 عدد الأجهزة المسجلة لاستقبال Push: {len(tokens)}")

                    # ── /test_push ─────────────────────────────
                    elif text == "/test_push":
                        results = send_push_notification("🧪 اختبار", "هذا إشعار تجريبي من البوت")
                        if not results:
                            send_admin("📵 لا يوجد أي جهاز مسجل — التوكن الأول لسا ما انسجل.")
                        else:
                            lines = [f"🧪 نتيجة اختبار Push لـ {len(results)} جهاز:\n"]
                            for i, r in enumerate(results, 1):
                                if r["ok"]:
                                    lines.append(f"{i}. ✅ نجح")
                                else:
                                    lines.append(f"{i}. ❌ فشل — {r['error']}")
                            send_admin("\n".join(lines))

                    # ── /help ─────────────────────────────────
                    elif text == "/help":
                        send_admin(
                            "📖 <b>الأوامر المتاحة:</b>\n\n"
                            "<b>إدارة العملات:</b>\n"
                            "/add ETH — إضافة عملة للمراقبة\n"
                            "/remove ETH — حذف عملة من القائمة\n"
                            "/list — عرض كل العملات المراقبة\n\n"
                            "<b>التحكم بالتداول:</b>\n"
                            "/stop — إيقاف التداول\n"
                            "/start — استئناف التداول (يلغي أي توقف تلقائي)\n"
                            "/status — حالة البوت مع الفريم والاستراتيجية وMA20\n"
                            "/close AVAX — إغلاق صفقة يدوياً فوراً بسعر السوق\n\n"
                            "🛑 توقف تلقائي: لو 3 صفقات ستوب لوز متتالية، يتوقف التداول تلقائيًا ساعتين.\n\n"
                            "<b>تعديل الإعدادات:</b>\n"
                            "/set_trade_amount 20 — حجم كل صفقة بالدولار\n"
                            "/set_max_trades 5 — أقصى عدد صفقات مفتوحة في نفس الوقت\n"
                            "/set_trail 1.5 — مساحة تنفس Trailing Stop (كلما كبرت، أعطيت العملة مجال أكبر)\n"
                            "/set_stoploss 1.5 — حد الخسارة الثابت قبل تفعيل Trailing\n"
                            "/set_activate 0.5 — نسبة الربح المطلوبة لتفعيل Trailing Stop\n"
                            "/set_breakeven 0.5 0.2 — تفعيل حماية التعادل عند 0.5% ربح، بهامش 0.2% فوق الدخول\n"
                            "/set_rsi_range 20 38 — نطاق RSI للمراقبة المكثفة\n"
                            "/set_interval 30 — الفريم الزمني للشموع (15/30/60/240 دقيقة)\n"
                            "/set_buy_rsi 25 30 — شرط الشراء: RSI السابق أصغر من 25 والحالي أكبر من 30\n\n"
                            "<b>📐 الستوب لوس المتحرك (ATR):</b>\n"
                            "/set_atr_period 14 — عدد الشموع لحساب تقلب العملة\n"
                            "/set_atr_multiplier 2 — مضاعف مسافة الستوب الأولي\n"
                            "/set_trail_atr_multiplier 1.5 — مضاعف مسافة الـ Trailing بعد التفعيل\n"
                            "/atr_status — شرح وعرض إعدادات ATR الحالية\n\n"
                            "<b>الاستراتيجية:</b>\n"
                            "/set_strategy rsi — شراء عند ارتداد RSI فوق 30\n"
                            "/set_strategy stoch_rsi — شراء عند تقاطع Stochastic RSI واختراق مستوى 20\n"
                            "/set_strategy trend_stoch — StochRSI + فوليوم قوي + تأكيد اتجاه صاعد على فريم 4 ساعات\n\n"
                            "<b>🧠 التبديل التلقائي بين الاستراتيجيات:</b>\n"
                            "/set_auto_strategy on — تفعيل التبديل التلقائي حسب حالة السوق\n"
                            "/set_auto_strategy off — إيقافه (يرجع كل شي يدوي)\n"
                            "/auto_strategy_status — عرض الحالة والمنطق الحالي\n\n"
                            "<b>🔄 Inverse BTC:</b>\n"
                            "/set_strategy inverse_btc — تفعيل استراتيجية عكس BTC\n"
                            "/set_inverse_btc on — تفعيل/إطفاء الاستراتيجية\n"
                            "/set_inverse_config btc_decline_threshold_pct 3.0 — تعديل حد نزول BTC\n"
                            "/set_inverse_config rs_min_threshold_pct 5.0 — تعديل الحد الأدنى للقوة النسبية\n"
                            "/set_inverse_config min_volume_ratio 1.2 — تعديل حد الفوليوم\n"
                            "/inverse_status — عرض إعدادات Inverse BTC\n\n"
                            "<b>📐 فلاتر تأكيد إضافية:</b>\n"
                            "/set_vwap_filter on — الشراء يشترط السعر فوق VWAP\n"
                            "/set_bb_filter on — الشراء يشترط قرب السعر من حد بولينجر السفلي\n"
                            "/filters_status — عرض حالة الفلاتر وشرح ترتيب الزخم\n\n"
                            "<b>📊 Range Trading BTC (استراتيجية موازية مستقلة):</b>\n"
                            "/set_range_trading on — تشغيل (صفقات حقيقية 15 USDT، BTC فقط، فريم 30 دقيقة)\n"
                            "/set_range_trading off — إيقاف (أي صفقة مفتوحة بتضل تكمل لحد ما تقفل عادي)\n"
                            "/range_trading_status — عرض الحالة والصفقة المفتوحة إن وجدت\n\n"
                            "<b>📈 Trend+Stoch الموازية (استراتيجية موازية مستقلة):</b>\n"
                            "/set_trend_parallel on — تشغيل (صفقات حقيقية 15 USDT، سلة العملات، فريم ساعة)\n"
                            "/set_trend_parallel off — إيقاف (أي صفقة مفتوحة بتضل تكمل لحد ما تقفل عادي)\n"
                            "/trend_parallel_status — عرض الحالة والصفقة المفتوحة إن وجدت\n\n"
                            "<b>المؤشرات:</b>\n"
                            "/enable_ma20 — تشغيل فيلتر MA20 (مستقل عن الاستراتيجية)\n"
                            "/disable_ma20 — تعطيل فيلتر MA20\n\n"
                            "<b>التقارير:</b>\n"
                            "/config — عرض كل الإعدادات الحالية\n"
                            "/push_status — عدد الأجهزة المسجلة لاستقبال Push Notifications\n"
                            "/test_push — إرسال إشعار تجريبي وعرض نتيجة النجاح/الفشل\n"
                            "/profit today — أرباح اليوم\n"
                            "/profit — كل الأرباح من البداية\n"
                            "/summary — ملخص كامل للأداء\n"
                            "/help — عرض هذه القائمة"
                        )

        except Exception as e:
            log.error(f"❌ خطأ listener: {e}")
        time.sleep(1)

# ──────────────────────────────────────────────
# فحص ذكي مرحلتين
# ──────────────────────────────────────────────
def get_rsi_quick(client, symbol):
    """✅ إصلاح #4: يستخدم current_interval بدل INTERVAL الثابت"""
    try:
        klines = client.get_klines(symbol=symbol, interval=current_interval, limit=RSI_PERIOD + 2)
        closes = pd.Series([float(k[4]) for k in klines])
        rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
        return round(rsi.iloc[-1], 2)
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block(f"get_rsi_quick({symbol})")
        else:
            log.error(f"❌ RSI سريع {symbol}: {e}")
        return None
    except Exception as e:
        log.error(f"❌ RSI سريع {symbol}: {e}")
        return None

def scan_all_symbols(client):
    """المرحلة 1: فحص خفيف لكل العملات حسب الاستراتيجية الحالية"""
    global watch_list
    if is_api_blocked():
        log.warning("🚦 تخطي دورة الفحص الخفيف — البوت بفترة إيقاف مؤقت بسبب حظر -1003")
        return
    new_watch = set()

    with _lock:
        strategy = current_strategy

    if strategy == "inverse_btc":
        # 🔄 فحص خفيف لـ Inverse BTC: العملات الصاعدة رغم نزول BTC
        try:
            btc_change = get_24h_change_pct(client, "BTCUSDT")
            if btc_change is None or btc_change > -3.0:
                # BTC ما نازل 3%+ — لا داعي للمراقبة
                with _lock:
                    watch_list = set()
                log.info("🔄 Inverse BTC: BTC ما نازل 3%+ — إفراغ قائمة المراقبة")
                return

            # جلب كل التغيرات بطلب واحد
            tickers = client.get_ticker()
            ticker_map = {t["symbol"]: float(t.get("priceChangePercent", 0)) for t in tickers}

            for symbol in SYMBOLS:
                if symbol in open_trades:
                    continue
                change_pct = ticker_map.get(symbol)
                if change_pct is None:
                    continue
                # مرشح خفيف: العملة صاعدة 1%+ (الفحص المكثف بيفحص 5%+)
                if change_pct >= 1.0:
                    new_watch.add(symbol)

            log.info(f"🔄 مرشحون Inverse BTC: {len(new_watch)} عملة صاعدة رغم نزول BTC {btc_change:.1f}%")
        except Exception as e:
            log.error(f"❌ فحص Inverse BTC: {e}")
    else:
        log.info(f"🔍 فحص خفيف لـ {len(SYMBOLS)} عملة...")
        for symbol in list(SYMBOLS):
            if is_api_blocked():
                log.warning("🚦 تم اكتشاف حظر أثناء الفحص — إيقاف باقي الدورة الحالية")
                break
            if symbol in open_trades:
                continue
            rsi = get_rsi_quick(client, symbol)
            if rsi is not None and RSI_WATCH_LOW <= rsi <= RSI_WATCH_HIGH:
                new_watch.add(symbol)
            time.sleep(0.35)

    added = new_watch - watch_list
    if added:
        log.info(f"👀 مرشحون جدد: {[s.replace('USDT','') for s in added]}")
    with _lock:
        watch_list = new_watch

# ──────────────────────────────────────────────
# جلب المؤشرات
# ──────────────────────────────────────────────
def get_current_price(client, symbol):
    """سعر لحظي فقط — طلب واحد خفيف لتتبع الصفقات المفتوحة (بدون RSI/MA20)"""
    try:
        return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block(f"get_current_price({symbol})")
        else:
            log.error(f"❌ سعر {symbol}: {e}")
        return None
    except Exception as e:
        log.error(f"❌ سعر {symbol}: {e}")
        return None

def get_all_prices(client, symbols=None):
    """
    ✅ إصلاح إضافي: يجلب أسعار كل العملات بطلب واحد فقط (get_all_tickers)
    بدل ما يطلب سعر كل عملة لحالها بحلقة — هذا يحمي البوت من حظر -1003
    لأن وزن هذا الطلب ثابت وقليل بغض النظر عن عدد العملات.
    يرجع dict: {symbol: price}. لو فشل الطلب، يرجع dict فاضي.
    """
    try:
        tickers = client.get_all_tickers()
        prices  = {t["symbol"]: float(t["price"]) for t in tickers}
        if symbols is not None:
            return {s: prices[s] for s in symbols if s in prices}
        return prices
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block("get_all_prices")
        else:
            log.error(f"❌ جلب كل الأسعار: {e}")
        return {}
    except Exception as e:
        log.error(f"❌ جلب كل الأسعار: {e}")
        return {}

# ──────────────────────────────────────────────
# 📐 ATR: يقيس التقلب الطبيعي لكل عملة، يُستخدم لبناء ستوب لوس متحرك بدل نسبة ثابتة
# ──────────────────────────────────────────────
def calculate_atr(highs, lows, closes):
    """يحسب قيمة ATR الحالية (آخر شمعة) لسلسلة high/low/close معطاة"""
    try:
        atr_series = ta.volatility.AverageTrueRange(
            high=highs, low=lows, close=closes, window=ATR_PERIOD
        ).average_true_range()
        value = atr_series.iloc[-1]
        if pd.isna(value) or value <= 0:
            return None
        return float(value)
    except Exception:
        return None

def get_indicators(client, symbol):
    try:
        klines  = client.get_klines(symbol=symbol, interval=current_interval, limit=100)
        closes  = pd.Series([float(k[4]) for k in klines])
        highs   = pd.Series([float(k[2]) for k in klines])
        lows    = pd.Series([float(k[3]) for k in klines])
        volumes = pd.Series([float(k[5]) for k in klines])
        rsi     = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
        ma20    = closes.rolling(window=MA_PERIOD).mean().iloc[-1]
        price   = float(closes.iloc[-1])   # من الـ klines مباشرة، بدون طلب API ثاني
        vwap_value = calculate_vwap(highs, lows, closes, volumes)
        bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(closes)
        return {
            "rsi"     : round(rsi.iloc[-1], 2),
            "rsi_prev": round(rsi.iloc[-2], 2),
            "price"   : price,
            "ma20"    : round(ma20, 8),
            "atr"     : calculate_atr(highs, lows, closes),
            "vwap"    : vwap_value,
            "bb_lower": bb_lower,
            "momentum_score": calculate_momentum_score(closes, volumes),
        }
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block(f"get_indicators({symbol})")
        else:
            log.error(f"❌ مؤشرات {symbol}: {e}")
        return None
    except Exception as e:
        log.error(f"❌ مؤشرات {symbol}: {e}")
        return None

# ──────────────────────────────────────────────
# 🎯 Stochastic RSI — إشارة الارتداد الصاعد المؤكد
# ──────────────────────────────────────────────
def check_stoch_rsi(client, symbol):
    """
    إشارة الشراء:
    - K و D كانوا تحت 20 (تشبع بيعي)
    - K قطع D لأعلى (تقاطع إيجابي)
    - K اخترق مستوى 20 من تحت لفوق
    يعيد dict بالقيم أو None لو مافي إشارة
    """
    try:
        klines  = client.get_klines(symbol=symbol, interval=current_interval, limit=100)
        closes  = pd.Series([float(k[4]) for k in klines])
        highs   = pd.Series([float(k[2]) for k in klines])
        lows    = pd.Series([float(k[3]) for k in klines])
        volumes = pd.Series([float(k[5]) for k in klines])

        # حساب Stochastic RSI بالإعدادات الافتراضية: period=14, K=3, D=3
        stoch  = ta.momentum.StochRSIIndicator(close=closes, window=14, smooth1=3, smooth2=3)
        k_line = stoch.stochrsi_k() * 100   # تحويل لنطاق 0-100
        d_line = stoch.stochrsi_d() * 100

        k_curr = round(k_line.iloc[-1], 2)
        k_prev = round(k_line.iloc[-2], 2)
        d_curr = round(d_line.iloc[-1], 2)
        d_prev = round(d_line.iloc[-2], 2)
        price  = float(closes.iloc[-1])
        ma20   = round(closes.rolling(window=MA_PERIOD).mean().iloc[-1], 8)

        # شرط الإشارة: كانوا تحت 20 + K قطع D لأعلى + K اخترق 20
        signal = (
            k_prev < 20 and d_prev < 20 and   # كانوا في منطقة التشبع البيعي
            k_curr >= 20 and                   # K اخترق الـ 20 لأعلى
            k_prev < d_prev and                # قبل: K تحت D
            k_curr > d_curr                    # بعد: K فوق D (تقاطع إيجابي)
        )

        if signal:
            bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(closes)
            return {
                "k_curr": k_curr,
                "k_prev": k_prev,
                "d_curr": d_curr,
                "d_prev": d_prev,
                "price" : price,
                "ma20"  : ma20,
                "atr"   : calculate_atr(highs, lows, closes),
                "vwap"    : calculate_vwap(highs, lows, closes, volumes),
                "bb_lower": bb_lower,
                "momentum_score": calculate_momentum_score(closes, volumes),
            }
        return None
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block(f"check_stoch_rsi({symbol})")
        else:
            log.error(f"❌ Stoch RSI {symbol}: {e}")
        return None
    except Exception as e:
        log.error(f"❌ Stoch RSI {symbol}: {e}")
        return None

# ──────────────────────────────────────────────
# 🚀 Trend + StochRSI — استراتيجية ثالثة مستقلة
# الشروط: StochRSI بمنطقة تشبع بيعي + تقاطع إيجابي فوق السعر > MA20،
#         فوليوم أعلى من متوسطه، وتأكيد اتجاه صاعد على فريم 4 ساعات (السعر > MA50)
# ──────────────────────────────────────────────
def get_btc_closes_cached(client):
    """
    يجيب آخر شموع BTC (بنفس الفريم الحالي current_interval) لاستخدامها بحساب Beta.
    مكاشة لمدة BETA_CACHE_TTL_SECONDS — نفس البيانات تنعاد استخدامها لكل عملات
    نفس دورة الفحص، بدل ما نطلب شموع BTC من جديد لكل عملة لحالها.
    """
    def _fetch():
        try:
            klines = client.get_klines(
                symbol="BTCUSDT",
                interval=current_interval,
                limit=TREND_STOCH_BETA_LOOKBACK + 5,
            )
            if not klines:
                return None
            return pd.Series([float(k[4]) for k in klines])
        except Exception as e:
            log.error(f"❌ شموع BTC لحساب Beta: {e}")
            return None

    cache_key = f"btc_closes_beta_{current_interval}"
    return get_cached_or_fetch(cache_key, _fetch, ttl=BETA_CACHE_TTL_SECONDS)

# ──────────────────────────────────────────────
# 🧠 تحديث تصنيف الارتباط مع BTC لكل عملة (ذاكرة العملات) — دوري وليس بكل دورة فحص
# ──────────────────────────────────────────────
CORRELATION_LOOKBACK = 60   # عدد الشموع المستخدمة بحساب الارتباط (نفس الفريم الحالي)

def update_symbol_correlations(client, symbols):
    """
    يُحدّث تصنيف كل عملة بذاكرة العملات: TREND_FOLLOWER (تمشي مع BTC) أو
    INVERSE_STRENGTH (تقاوم/تبرز وقت هبوط BTC) أو NEUTRAL.
    يُستدعى دورياً (كل CORRELATION_UPDATE_INTERVAL) وليس بكل دورة فحص مكثف،
    لتفادي حمل إضافي على وزن الـ API.
    """
    try:
        btc_closes = get_btc_closes_cached(client)
        if btc_closes is None or len(btc_closes) < 10:
            return
        btc_returns = btc_closes.pct_change().dropna()

        for symbol in symbols:
            try:
                klines = client.get_klines(symbol=symbol, interval=current_interval, limit=CORRELATION_LOOKBACK + 5)
                if not klines or len(klines) < 10:
                    continue
                coin_closes = pd.Series([float(k[4]) for k in klines])
                coin_returns = coin_closes.pct_change().dropna()

                min_len = min(len(btc_returns), len(coin_returns))
                if min_len < 10:
                    continue

                corr_engine.update_symbol_correlation(
                    symbol,
                    btc_returns=btc_returns.iloc[-min_len:].tolist(),
                    symbol_returns=coin_returns.iloc[-min_len:].tolist(),
                )
                time.sleep(0.15)   # تفادي ضغط سريع على الـ API
            except BinanceAPIException as e:
                if is_rate_limit_error(e):
                    register_api_block(f"update_symbol_correlations({symbol})")
                    break
            except Exception as e:
                log.error(f"❌ تحديث ارتباط {symbol}: {e}")
    except Exception as e:
        log.error(f"❌ تحديث ارتباط العملات (عام): {e}")


def check_trend_stoch(client, symbol):
    """
    إشارة الشراء (الثلاث شروط لازم تتحقق كلها):
    - نفس شرط Stochastic RSI (تشبع بيعي + تقاطع إيجابي + اختراق 20) + السعر فوق MA20
    - الفوليوم الحالي أعلى من متوسطه (VOLUME_MULTIPLIER × المتوسط)
    - Beta العملة مقابل BTC أعلى من TREND_STOCH_BETA_THRESHOLD (تتحرك أعنف من BTC،
      فلو BTC بترند صاعد، هاي العملة عادة بتطلع أكثر منه — استغلال الزخم)
    يعيد dict بالقيم أو None لو مافي إشارة
    """
    try:
        klines = client.get_klines(symbol=symbol, interval=current_interval, limit=100)
        closes  = pd.Series([float(k[4]) for k in klines])
        highs   = pd.Series([float(k[2]) for k in klines])
        lows    = pd.Series([float(k[3]) for k in klines])
        volumes = pd.Series([float(k[5]) for k in klines])

        stoch  = ta.momentum.StochRSIIndicator(close=closes, window=14, smooth1=3, smooth2=3)
        k_line = stoch.stochrsi_k() * 100
        d_line = stoch.stochrsi_d() * 100

        k_curr = round(k_line.iloc[-1], 2)
        k_prev = round(k_line.iloc[-2], 2)
        d_curr = round(d_line.iloc[-1], 2)
        d_prev = round(d_line.iloc[-2], 2)
        price  = float(closes.iloc[-1])
        ma20   = round(closes.rolling(window=MA_PERIOD).mean().iloc[-1], 8)

        base_condition = (
            k_prev < 20 and d_prev < 20 and
            k_curr >= 20 and
            k_prev < d_prev and
            k_curr > d_curr and
            price > ma20
        )
        if not base_condition:
            return None

        # فلتر الفوليوم: لازم فوليوم آخر شمعة أعلى من متوسطه
        vol_ma = volumes.rolling(window=VOLUME_MA_LENGTH).mean().iloc[-1]
        if pd.isna(vol_ma) or volumes.iloc[-1] <= (vol_ma * VOLUME_MULTIPLIER):
            return None

        # فلتر Beta: العملة لازم تكون أعنف حركة من BTC
        btc_closes = get_btc_closes_cached(client)
        if btc_closes is None or len(btc_closes) < TREND_STOCH_BETA_LOOKBACK:
            return None   # ما قدرنا نتأكد من Beta — نتجاهل الإشارة احتياطاً

        coin_closes_for_beta = closes.iloc[-len(btc_closes):]   # نفس عدد الشموع بالضبط
        beta_value = calculate_beta(coin_closes_for_beta, btc_closes)
        if beta_value is None or beta_value < TREND_STOCH_BETA_THRESHOLD:
            return None

        bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(closes)
        return {
            "k_curr": k_curr,
            "k_prev": k_prev,
            "d_curr": d_curr,
            "d_prev": d_prev,
            "price" : price,
            "ma20"  : ma20,
            "beta"  : beta_value,
            "atr"   : calculate_atr(highs, lows, closes),
            "vwap"    : calculate_vwap(highs, lows, closes, volumes),
            "bb_lower": bb_lower,
            "momentum_score": calculate_momentum_score(closes, volumes),
        }
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block(f"check_trend_stoch({symbol})")
        else:
            log.error(f"❌ Trend+StochRSI {symbol}: {e}")
        return None
    except Exception as e:
        log.error(f"❌ Trend+StochRSI {symbol}: {e}")
        return None

# ──────────────────────────────────────────────
# 📐 فحص فلاتر التأكيد الاختيارية (VWAP + Bollinger) — مشتركة بين الاستراتيجيات الثلاث
# ──────────────────────────────────────────────
def passes_confirmation_filters(signal_data):
    """
    يفحص فلاتر VWAP وBollinger الاختيارية (لو مفعّلة) على نتيجة أي إشارة شراء.
    لو الفلتر مطفي، ما يأثر بشي. لو مفعّل وتعذر حساب القيمة (None)، نرفض الإشارة
    احتياطاً (أفضل نتجاهل صفقة مشكوك فيها من نشتري بدون تأكيد).
    """
    if VWAP_FILTER_ENABLED:
        vwap  = signal_data.get("vwap")
        price = signal_data.get("price")
        if vwap is None or price is None or price <= vwap:
            return False

    if BB_FILTER_ENABLED:
        bb_lower = signal_data.get("bb_lower")
        price    = signal_data.get("price")
        if bb_lower is None or price is None:
            return False
        if price > bb_lower * (1 + BB_LOWER_MARGIN_PCT):
            return False

    return True

# ──────────────────────────────────────────────
# 📐 حساب ستوب الـ Trailing (بناءً على ATR المخزن بالصفقة، أو النسبة الثابتة كاحتياطي)
# ──────────────────────────────────────────────
def compute_trail_stop(price, trade):
    """
    يحسب سعر ستوب جديد بناءً على أعلى سعر وصلته الصفقة (price).
    - لو الصفقة عندها ATR محفوظ من وقت الشراء: نستخدمه (تقلب العملة الحقيقي).
    - غير هيك: نرجع للنسبة الثابتة TRAIL_PCT كاحتياطي.
    ملاحظة: هذا السعر يُستخدم فقط لما يصير سعر قمة جديدة (price > highest_price)،
    فالستوب يتحرك لأعلى بس ولا ينزل أبداً مع نزول السعر.
    """
    atr_val = trade.get("atr")
    if atr_val:
        with _lock:
            multiplier = TRAIL_ATR_MULTIPLIER
        candidate = price - (multiplier * atr_val)
        if 0 < candidate < price:
            return round(candidate, 8)
    return round(price * (1 - TRAIL_PCT), 8)

# ──────────────────────────────────────────────
# تنفيذ الصفقات
# ──────────────────────────────────────────────
def get_quantity(client, symbol, usdt_amount):
    step_size = get_step_size(client, symbol)   # ✅ إصلاح: من الكاش بدل طلب API بكل مرة
    price     = float(client.get_symbol_ticker(symbol=symbol)["price"])
    qty = usdt_amount / price
    if step_size:
        precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
        qty = round(qty - (qty % step_size), precision)
    return qty, price

# ──────────────────────────────────────────────
# 🔴 إغلاق صفقة يدوياً
# ──────────────────────────────────────────────
def close_trade(client, symbol):
    """يغلق صفقة مفتوحة يدوياً بأمر من المستخدم"""
    with _lock:
        if symbol not in open_trades:
            return None, "not_found"
        trade = open_trades[symbol]

    sell_price, sold_qty = sell_market(client, symbol, trade["qty"])
    if sell_price:
        # ✅ إصلاح: نستخدم الكمية الفعلية المُنفَّذة (sold_qty) لحساب الربح،
        # مش الكمية المسجلة بالذاكرة، عشان الربح يطابق تمامًا اللي صار على بينانس
        record_trade_result(symbol, trade["entry_price"], sell_price, sold_qty, "manual_close",
                             entry_slippage_pct=trade.get("entry_slippage_pct", 0.0))
        with _lock:
            if symbol in open_trades:
                del open_trades[symbol]
        save_trades()
        return sell_price, "ok"

    # ✅ إصلاح: لو فشل البيع لأنه لا يوجد رصيد فعلي للعملة في المحفظة،
    # ننظف الصفقة من السجل لتجنب تكرار محاولة البيع كل دقيقة (مشكلة TLM)
    asset = symbol.replace("USDT", "")
    try:
        balance = client.get_asset_balance(asset=asset)
        if float(balance["free"]) <= 0:
            with _lock:
                if symbol in open_trades:
                    del open_trades[symbol]
            save_trades()
            log.warning(f"⚠️ {symbol}: لا يوجد رصيد فعلي، تم حذف الصفقة من السجل")
            return None, "no_balance_removed"
    except Exception as e:
        log.error(f"❌ فحص رصيد {symbol} بعد فشل البيع: {e}")

    return None, "sell_failed"

def buy_market(client, symbol, usdt_amount):
    """
    ✅ إصلاح إضافي: سعر الدخول الفعلي (Executed Price) من رد بينانس مباشرة،
    بدل سعر الشمعة/التيكر التقريبي وقت إرسال الأمر.
    actual_entry_price = total_spent (USDT) / total_quantity (كمية العملة الفعلية المشتراة)
    هذا يضمن إن الـ stop_loss والـ trailing stop يُبنوا على السعر الحقيقي اللي دخلت فيه المحفظة،
    مش على سعر تقديري ممكن يكون مختلف شوي عن التنفيذ الفعلي (slippage).
    """
    try:
        qty, price = get_quantity(client, symbol, usdt_amount)
        if qty <= 0:
            return None
        order = client.order_market_buy(symbol=symbol, quantity=qty)

        # ── انتظار وقراءة رد المنصة الفعلي (fills) ─────────────
        fills = order.get("fills", [])
        if fills:
            total_qty   = sum(float(f["qty"]) for f in fills)
            total_spent = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            # ✅ عمولة الشراء أحياناً تُخصم من نفس العملة المشتراة (BNB مو مستخدم)،
            # فنطرح العمولة من الكمية الفعلية لو كانت بنفس عملة الأصل (asset) لدقة أكبر
            asset = symbol.replace("USDT", "")
            commission_in_asset = sum(
                float(f.get("commission", 0)) for f in fills
                if f.get("commissionAsset") == asset
            )
            net_qty = total_qty - commission_in_asset
            if net_qty > 0 and total_qty > 0:
                actual_entry_price = total_spent / total_qty   # سعر التنفيذ الفعلي (weighted average)
            else:
                actual_entry_price = price
                net_qty = qty
        else:
            # احتياط لو الرد ما رجع fills لأي سبب
            actual_entry_price = price
            net_qty             = qty

        # 🧠 الانزلاق الفعلي عند الدخول = الفرق بين السعر التقديري (قبل الأمر)
        # وسعر التنفيذ الفعلي (weighted average من fills). يُخزَّن بالصفقة
        # ليُستخدم لاحقاً بذاكرة العملات (coin_memory) عند إغلاقها.
        entry_slippage_pct = abs(actual_entry_price - price) / price * 100 if price else 0.0

        log.info(
            f"✅ شراء {symbol} | سعر تقديري: {price} | سعر تنفيذ فعلي: {actual_entry_price:.8f} | "
            f"الكمية الفعلية: {net_qty} | انزلاق: {entry_slippage_pct:.3f}%"
        )
        return {
            "qty": net_qty,
            "entry_price": actual_entry_price,
            "order_id": order["orderId"],
            "entry_slippage_pct": round(entry_slippage_pct, 4),
        }
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block(f"buy_market({symbol})")
        log.error(f"❌ شراء {symbol}: {e.status_code} | {e.message}")
        send_admin(f"خطأ شراء {symbol}: {e.message}")
        return None
    except Exception as e:
        log.error(f"❌ شراء {symbol}: {e}")
        send_admin(f"خطأ شراء {symbol}: {e}")
        return None

def sell_market(client, symbol, qty):
    """
    ✅ إصلاح #2: البيع بالكمية الكاملة بدون طرح عمولة يدوي
    ✅ إصلاح إضافي: التحقق من الرصيد الفعلي في المحفظة قبل البيع،
       واستخدام أصغر قيمة بين الكمية المسجلة في الذاكرة والكمية الفعلية،
       لتجنب خطأ "Account has insufficient balance" (مشكلة TLM المتكررة)
    ✅ إصلاح إضافي: يرجّع الكمية الفعلية المُنفَّذة (من fills) مع السعر،
       عشان حساب الربح (record_trade_result) يعتمد على اللي انباع فعلياً
       على بينانس، مو على الكمية المسجلة بالذاكرة وقت الشراء — كان فيه
       فرق بسيط أحياناً بين الاثنين بسبب تقريب step_size أو "غبار" بالرصيد،
       وهاد كان يخلي الربح المعروض بالتطبيق يختلف شوي عن الربح الحقيقي ببينانس.
    يرجع (price, executed_qty) عند النجاح، أو (None, None) عند الفشل.
    """
    try:
        asset      = symbol.replace("USDT", "")
        balance    = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])

        step_size = get_step_size(client, symbol)   # ✅ إصلاح: من الكاش بدل طلب API بكل مرة

        # ✅ نستخدم أصغر قيمة بين المسجل بالذاكرة والمتاح فعلياً بالمحفظة
        sell_qty = min(qty, actual_qty)

        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            sell_qty  = round(sell_qty - (sell_qty % step_size), precision)

        if sell_qty <= 0:
            log.warning(f"⚠️ {symbol}: لا يوجد رصيد كافٍ للبيع (مسجل: {qty}, فعلي: {actual_qty})")
            return None, None

        order = client.order_market_sell(symbol=symbol, quantity=sell_qty)
        # ✅ إصلاح #1: سعر التنفيذ الفعلي من الأوردر مباشرة (weighted average)
        fills = order.get("fills", [])
        if fills:
            executed_qty = sum(float(f["qty"]) for f in fills)
            price        = sum(float(f["price"]) * float(f["qty"]) for f in fills) / executed_qty
        else:
            executed_qty = sell_qty
            price        = float(client.get_symbol_ticker(symbol=symbol)["price"])
        log.info(f"✅ بيع {symbol} | السعر: {price:.6f} | الكمية المطلوبة: {sell_qty} | الكمية المنفذة فعلياً: {executed_qty}")
        return price, executed_qty
    except BinanceAPIException as e:
        if is_rate_limit_error(e):
            register_api_block(f"sell_market({symbol})")
        log.error(f"❌ بيع {symbol}: {e.status_code} | {e.message}")
        send_admin(f"خطأ بيع {symbol}: {e.message}")
        return None, None
    except Exception as e:
        log.error(f"❌ بيع {symbol}: {e}")
        send_admin(f"خطأ بيع {symbol}: {e}")
        return None, None

# ──────────────────────────────────────────────
# 📊 لوحة التحكم (Dashboard) — Flask API
# ──────────────────────────────────────────────
DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")

app = Flask(__name__)
app.secret_key = DASHBOARD_SECRET
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
)

INTERVAL_TO_MINUTES = {
    Client.KLINE_INTERVAL_15MINUTE: 15,
    Client.KLINE_INTERVAL_30MINUTE: 30,
    Client.KLINE_INTERVAL_1HOUR   : 60,
    Client.KLINE_INTERVAL_4HOUR   : 240,
}
MINUTES_TO_INTERVAL = {v: k for k, v in INTERVAL_TO_MINUTES.items()}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── تقديم ملفات الواجهة ─────────────────────────
@app.route("/")
def dashboard_index():
    return send_from_directory(DASHBOARD_DIR, "index.html")


@app.route("/<path:filename>")
def dashboard_static(filename):
    return send_from_directory(DASHBOARD_DIR, filename)


# ── تسجيل الدخول ─────────────────────────────────
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if not DASHBOARD_USERNAME or not DASHBOARD_PASSWORD:
        return jsonify({"error": "الوحة غير مفعّلة على السيرفر"}), 503
    if username == DASHBOARD_USERNAME and password == DASHBOARD_PASSWORD:
        session.permanent = True
        session["logged_in"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "بيانات الدخول غير صحيحة"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    return jsonify({"logged_in": bool(session.get("logged_in"))})


# ── الحالة العامة ────────────────────────────────
@app.route("/api/status")
@login_required
def api_status():
    with _lock:
        is_trading   = trading_enabled
        cb_pause     = pause_until
        max_trades   = MAX_TRADES
    now = time.time()
    cb_active = bool(cb_pause and cb_pause > now)

    balance = 0.0
    if _binance_client:
        def _fetch_balance():
            try:
                return float(_binance_client.get_asset_balance(asset="USDT")["free"])
            except Exception as e:
                log.error(f"❌ API رصيد: {e}")
                return 0.0
        # ✅ كاش قصير المدة: أي عدد أجهزة فاتحة بنفس اللحظة بتشارك نفس النداء
        balance = get_cached_or_fetch("usdt_balance", _fetch_balance)

    profit_log = load_all_profit_log()   # ✅ كل الشهور
    total_pnl  = round(sum(r["profit"] for r in profit_log), 4)
    wins       = sum(1 for r in profit_log if r["profit"] > 0)
    win_rate   = round(wins / len(profit_log) * 100, 1) if profit_log else 0.0

    return jsonify({
        "trading_enabled": is_trading,
        "circuit_breaker": {
            "active": cb_active,
            "resume_in_minutes": int((cb_pause - now) / 60) if cb_active else 0,
        },
        "balance_usdt": round(balance, 2),
        "open_trades_count": len(open_trades),
        "active_trades_count": count_active_trades(),
        "max_trades": max_trades,
        "watch_count": len(watch_list),
        "symbols_count": len(SYMBOLS),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "strategy": current_strategy,
        "strategy_label": STRATEGY_LABELS.get(current_strategy, current_strategy),
        "auto_strategy_enabled": AUTO_STRATEGY_ENABLED,
    })


# ── الصفقات المفتوحة ─────────────────────────────
@app.route("/api/trades")
@login_required
def api_trades():
    result       = []
    trades_copy  = dict(open_trades)
    # ✅ إصلاح: جلب كل الأسعار بطلب واحد بدل طلب لكل عملة بحلقة
    # ✅ كاش قصير المدة: أي عدد أجهزة فاتحة بنفس اللحظة بتشارك نفس النداء لبينانس
    def _fetch_prices():
        return get_all_prices(_binance_client, set(trades_copy.keys())) if _binance_client else {}
    all_prices   = get_cached_or_fetch("all_open_trade_prices", _fetch_prices)
    for symbol, t in trades_copy.items():
        current_price = all_prices.get(symbol, t["entry_price"])
        pnl_pct = round((current_price - t["entry_price"]) / t["entry_price"] * 100, 2)
        result.append({
            "symbol": symbol,
            "entry_price": t["entry_price"],
            "current_price": current_price,
            "trailing_active": t.get("trailing_active", False),
            "stop_loss": t.get("stop_loss"),
            "pnl_pct": pnl_pct,
        })
    return jsonify(result)


# ── سجل الصفقات المغلقة ──────────────────────────
@app.route("/api/history")
@login_required
def api_history():
    period = request.args.get("period", "all")
    now    = time.strftime("%Y-%m-%d")
    if period == "today":
        records = [r for r in load_profit_log() if r["time"].startswith(now)]
    elif period == "week":
        cutoff  = time.time() - 7 * 86400
        records = [r for r in load_all_profit_log()
                   if time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S")) >= cutoff]
    else:
        records = load_all_profit_log()   # ✅ كل الشهور
    records = sorted(records, key=lambda r: r["time"], reverse=True)
    return jsonify(records)


# ── الإعدادات ─────────────────────────────────────
@app.route("/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    with _lock:
        inv_cfg = dict(INVERSE_BTC_CONFIG)
        return jsonify({
            "trade_amount": TRADE_AMOUNT,
            "max_trades": MAX_TRADES,
            "trail_pct": round(TRAIL_PCT * 100, 4),
            "interval_minutes": INTERVAL_TO_MINUTES.get(current_interval, 30),
            "rsi_low": RSI_WATCH_LOW,
            "rsi_high": RSI_WATCH_HIGH,
            "ma20_enabled": ma20_enabled,
            "rsi_enabled": current_strategy == "rsi",
            "stochastic_enabled": current_strategy == "stoch_rsi",
            "trend_stoch_enabled": current_strategy == "trend_stoch",
            "auto_strategy_enabled": AUTO_STRATEGY_ENABLED,
            "stop_loss_pct": round(STOP_LOSS_PCT * 100, 4),
            "activate_trailing_pct": round(TRAIL_ACTIVATE_PCT * 100, 4),
            "breakeven_activate_pct": round(BREAKEVEN_ACTIVATE_PCT * 100, 4),
            "breakeven_margin_pct": round(BREAKEVEN_MARGIN_PCT * 100, 4),
            "rsi_buy_prev": RSI_BUY_PREV,
            "rsi_buy_curr": RSI_BUY_CURR,
            "atr_period": ATR_PERIOD,
            "atr_multiplier": ATR_MULTIPLIER,
            "trail_atr_multiplier": TRAIL_ATR_MULTIPLIER,
            "vwap_filter_enabled": VWAP_FILTER_ENABLED,
            "bb_filter_enabled": BB_FILTER_ENABLED,
            "inverse_btc_enabled": current_strategy == "inverse_btc",
            "current_strategy": current_strategy,
            # ✅ إعدادات Inverse BTC منفصلة (سهلة على التطبيق)
            "inverse_btc_threshold": inv_cfg.get("btc_decline_threshold_pct", 3.0),
            "inverse_btc_rs_min": inv_cfg.get("rs_min_threshold_pct", 5.0),
            "inverse_btc_volume_min": inv_cfg.get("min_volume_ratio", 1.2),
            # ✅ للتوافق مع النسخ القديمة من التطبيق
            "inverse_btc_config": inv_cfg,
            # ✅ Range Trading BTC — استراتيجية موازية مستقلة (صفقات حقيقية BTC فقط)
            "range_trading_enabled": RANGE_TRADING_ENABLED,
            "range_trading_usdt_per_trade": RANGE_TRADING_USDT_PER_TRADE,
            "range_trading_position": _range_trading_strategy.position if _range_trading_strategy else None,
            # ✅ Trend+Stoch الموازية — استراتيجية موازية مستقلة (صفقات حقيقية، سلة العملات، فريم ساعة)
            "trend_parallel_enabled": TREND_PARALLEL_ENABLED,
            "trend_parallel_usdt_per_trade": TREND_PARALLEL_USDT_PER_TRADE,
            "trend_parallel_position": _trend_parallel_strategy.position if _trend_parallel_strategy else None,
        })


@app.route("/api/settings", methods=["POST"])
@login_required
def api_set_settings():
    global TRADE_AMOUNT, MAX_TRADES, TRAIL_PCT, RSI_WATCH_LOW, RSI_WATCH_HIGH
    global current_interval, ma20_enabled, current_strategy, STOP_LOSS_PCT, TRAIL_ACTIVATE_PCT, RSI_BUY_PREV, RSI_BUY_CURR
    global ATR_PERIOD, ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER
    global AUTO_STRATEGY_ENABLED
    global VWAP_FILTER_ENABLED, BB_FILTER_ENABLED
    global INVERSE_BTC_ENABLED, INVERSE_BTC_CONFIG
    global BREAKEVEN_ACTIVATE_PCT, BREAKEVEN_MARGIN_PCT
    global RANGE_TRADING_ENABLED
    global TREND_PARALLEL_ENABLED
    data = request.get_json(silent=True) or {}
    errors = []

    with _lock:
        if "trade_amount" in data:
            v = float(data["trade_amount"])
            if v <= 0: errors.append("trade_amount لازم أكبر من صفر")
            else: TRADE_AMOUNT = v

        if "max_trades" in data:
            v = int(data["max_trades"])
            if v <= 0: errors.append("max_trades لازم أكبر من صفر")
            else: MAX_TRADES = v

        if "trail_pct" in data:
            v = float(data["trail_pct"]) / 100
            if v <= 0: errors.append("trail_pct لازم أكبر من صفر")
            else: TRAIL_PCT = v

        if "rsi_low" in data or "rsi_high" in data:
            new_low  = float(data["rsi_low"]) if "rsi_low" in data else RSI_WATCH_LOW
            new_high = float(data["rsi_high"]) if "rsi_high" in data else RSI_WATCH_HIGH
            if new_low <= 0 or new_high <= 0 or new_low >= new_high:
                errors.append("منطقة RSI غير صحيحة: RSI-Low لازم أصغر من RSI-High")
            else:
                RSI_WATCH_LOW, RSI_WATCH_HIGH = new_low, new_high

        if "interval_minutes" in data:
            minutes = int(data["interval_minutes"])
            if minutes in MINUTES_TO_INTERVAL:
                current_interval = MINUTES_TO_INTERVAL[minutes]
            else:
                errors.append("الفريم المسموح: 15, 30, 60, 240")

        if "ma20_enabled" in data:
            ma20_enabled = bool(data["ma20_enabled"])

        if "rsi_enabled" in data and data["rsi_enabled"]:
            current_strategy = "rsi"
        if "stochastic_enabled" in data and data["stochastic_enabled"]:
            current_strategy = "stoch_rsi"
        if "trend_stoch_enabled" in data and data["trend_stoch_enabled"]:
            current_strategy = "trend_stoch"
        if "inverse_btc_enabled" in data and data["inverse_btc_enabled"]:
            current_strategy = "inverse_btc"

        # ✅ إصلاح خلل: نزامن ذاكرة الكاشف مع أي تغيير يدوي من التطبيق أيضاً
        # (بما فيها inverse_btc — كانت ناقصة من القائمة، وهذا سبب "الزرار يضل مثبت")
        if any(k in data for k in ("rsi_enabled", "stochastic_enabled", "trend_stoch_enabled", "inverse_btc_enabled")):
            if _regime_detector:
                _regime_detector.current_strategy = current_strategy
                _regime_detector.last_switch_time = time.time()

        if "auto_strategy_enabled" in data:
            AUTO_STRATEGY_ENABLED = bool(data["auto_strategy_enabled"])
            if AUTO_STRATEGY_ENABLED and _regime_detector:
                # ✅ إصلاح خلل: نزامن الكاشف مع الاستراتيجية الحالية وقت التفعيل من التطبيق
                _regime_detector.current_strategy = current_strategy
                _regime_detector.last_switch_time = time.time()
        if "stop_loss_pct" in data:
            v = float(data["stop_loss_pct"]) / 100
            if v > 0: STOP_LOSS_PCT = v
        if "activate_trailing_pct" in data:
            v = float(data["activate_trailing_pct"]) / 100
            if v >= 0: TRAIL_ACTIVATE_PCT = v

        if "breakeven_activate_pct" in data:
            v = float(data["breakeven_activate_pct"]) / 100
            if v > 0: BREAKEVEN_ACTIVATE_PCT = v
            else: errors.append("breakeven_activate_pct لازم أكبر من صفر")
        if "breakeven_margin_pct" in data:
            v = float(data["breakeven_margin_pct"]) / 100
            if v >= 0: BREAKEVEN_MARGIN_PCT = v
            else: errors.append("breakeven_margin_pct لازم صفر أو أكبر")

        if "rsi_buy_prev" in data or "rsi_buy_curr" in data:
            new_prev = float(data["rsi_buy_prev"]) if "rsi_buy_prev" in data else RSI_BUY_PREV
            new_curr = float(data["rsi_buy_curr"]) if "rsi_buy_curr" in data else RSI_BUY_CURR
            if new_prev <= 0 or new_curr <= 0 or new_prev >= new_curr:
                errors.append("شرط الشراء غير صحيح: القيمة السابقة لازم أصغر من الحالية")
            else:
                RSI_BUY_PREV, RSI_BUY_CURR = new_prev, new_curr

        # ✅ إصلاح: قبول إعدادات ATR من التطبيق (كانت مفقودة كلياً)
        if "atr_period" in data:
            v = int(data["atr_period"])
            if v <= 1: errors.append("atr_period لازم أكبر من 1")
            else: ATR_PERIOD = v

        if "atr_multiplier" in data:
            v = float(data["atr_multiplier"])
            if v <= 0: errors.append("atr_multiplier لازم أكبر من صفر")
            else: ATR_MULTIPLIER = v

        if "trail_atr_multiplier" in data:
            v = float(data["trail_atr_multiplier"])
            if v <= 0: errors.append("trail_atr_multiplier لازم أكبر من صفر")
            else: TRAIL_ATR_MULTIPLIER = v

        if "vwap_filter_enabled" in data:
            VWAP_FILTER_ENABLED = bool(data["vwap_filter_enabled"])
        if "bb_filter_enabled" in data:
            BB_FILTER_ENABLED = bool(data["bb_filter_enabled"])
        if "inverse_btc_enabled" in data and data["inverse_btc_enabled"]:
            current_strategy = "inverse_btc"

        # ✅ إعدادات Inverse BTC منفصلة (سهلة على التطبيق)
        if "inverse_btc_threshold" in data:
            INVERSE_BTC_CONFIG["btc_decline_threshold_pct"] = float(data["inverse_btc_threshold"])
        if "inverse_btc_rs_min" in data:
            INVERSE_BTC_CONFIG["rs_min_threshold_pct"] = float(data["inverse_btc_rs_min"])
        if "inverse_btc_volume_min" in data:
            INVERSE_BTC_CONFIG["min_volume_ratio"] = float(data["inverse_btc_volume_min"])

        # ✅ للتوافق مع النسخ القديمة من التطبيق
        if "inverse_btc_config" in data:
            inv_cfg = data["inverse_btc_config"]
            if isinstance(inv_cfg, dict):
                for key, val in inv_cfg.items():
                    if key in INVERSE_BTC_CONFIG:
                        INVERSE_BTC_CONFIG[key] = float(val)

        # ✅ نزامن إعدادات inverse_btc مع المكتبة
        set_inverse_config(**INVERSE_BTC_CONFIG)

        # ✅ Range Trading BTC — تشغيل/إيقاف من التطبيق (صفقة حقيقية بمبلغ ثابت مسبقاً)
        if "range_trading_enabled" in data:
            RANGE_TRADING_ENABLED = bool(data["range_trading_enabled"])

        # ✅ Trend+Stoch الموازية — تشغيل/إيقاف من التطبيق
        if "trend_parallel_enabled" in data:
            TREND_PARALLEL_ENABLED = bool(data["trend_parallel_enabled"])

    if errors:
        return jsonify({"error": "؛ ".join(errors)}), 400
    save_settings()
    return jsonify({"ok": True})

# ── الاستراتيجيات ───────────────────────────────────
@app.route("/api/strategies")
@login_required
def api_strategies():
    """يرجع حالة كل استراتيجية (للـ Toggleات بالتطبيق)"""
    with _lock:
        return jsonify({
            "current_strategy": current_strategy,
            "rsi_enabled": current_strategy == "rsi",
            "stochastic_enabled": current_strategy == "stoch_rsi",
            "trend_stoch_enabled": current_strategy == "trend_stoch",
            "inverse_btc_enabled": current_strategy == "inverse_btc",
            "auto_strategy_enabled": AUTO_STRATEGY_ENABLED,
            "strategy_label": STRATEGY_LABELS.get(current_strategy, current_strategy),
        })



# ── التحكم بالتداول ───────────────────────────────
@app.route("/api/control", methods=["POST"])
@login_required
def api_control():
    global trading_enabled, pause_until, consecutive_losses
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action == "stop":
        with _lock:
            trading_enabled = False
        return jsonify({"ok": True})
    elif action == "start":
        with _lock:
            trading_enabled = True
            pause_until = 0
        consecutive_losses = 0
        save_circuit_state()
        return jsonify({"ok": True})
    return jsonify({"error": "action لازم تكون start أو stop"}), 400


@app.route("/api/symbols", methods=["GET"])
@login_required
def api_get_symbols():
    base_names = [s.replace("USDT", "") for s in SYMBOLS]
    return jsonify(base_names)


@app.route("/api/symbols", methods=["POST"])
@login_required
def api_add_symbol():
    global SYMBOLS
    data   = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"error": "symbol مفقود"}), 400
    full = f"{symbol}USDT"
    if full in SYMBOLS:
        return jsonify({"error": "العملة موجودة أصلاً"}), 400
    SYMBOLS.append(full)
    save_symbols_to_txt()
    return jsonify({"ok": True})


@app.route("/api/symbols/<symbol>", methods=["DELETE"])
@login_required
def api_delete_symbol(symbol):
    global SYMBOLS
    full = f"{symbol.upper()}USDT"
    if full not in SYMBOLS:
        return jsonify({"error": "العملة غير موجودة"}), 404
    SYMBOLS.remove(full)
    save_symbols_to_txt()
    return jsonify({"ok": True})


@app.route("/api/close_trade", methods=["POST"])
@login_required
def api_close_trade():
    data   = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"error": "symbol مفقود"}), 400
    if not _binance_client:
        return jsonify({"error": "البوت غير متصل ببينانس"}), 503
    sell_price, status = close_trade(_binance_client, f"{symbol}USDT")
    if status == "not_found":
        return jsonify({"error": "لا توجد صفقة مفتوحة لهذه العملة"}), 404
    elif status == "sell_failed":
        return jsonify({"error": "فشل إغلاق الصفقة"}), 500
    elif status == "no_balance_removed":
        return jsonify({"ok": True, "note": "لا يوجد رصيد، تم حذف الصفقة من السجل"})
    return jsonify({"ok": True, "sell_price": sell_price})



# ── قائمة الصفقات القابلة للإغلاق ───────────────────
@app.route("/api/closeable_trades")
@login_required
def api_closeable_trades():
    """يرجع الصفقات المفتوحة مع PnL (لشاشة الصفقات — إغلاق يدوي)"""
    result = []
    trades_copy = dict(open_trades)

    def _fetch_prices():
        return get_all_prices(_binance_client, set(trades_copy.keys())) if _binance_client else {}

    all_prices = get_cached_or_fetch("closeable_trade_prices", _fetch_prices)

    for symbol, t in trades_copy.items():
        current_price = all_prices.get(symbol, t["entry_price"])
        pnl_pct = round((current_price - t["entry_price"]) / t["entry_price"] * 100, 2)
        result.append({
            "symbol": symbol,
            "coin": symbol.replace("USDT", ""),
            "entry_price": t["entry_price"],
            "current_price": current_price,
            "pnl_pct": pnl_pct,
            "trailing_active": t.get("trailing_active", False),
            "qty": t.get("qty", 0),
        })

    return jsonify(result)

@app.route("/api/register_push", methods=["POST"])
@login_required
def api_register_push():
    data  = request.get_json(silent=True) or {}
    token = data.get("token", "")
    if not token:
        return jsonify({"error": "token مفقود"}), 400
    save_push_token(token)
    return jsonify({"ok": True})


@app.route("/api/notifications", methods=["GET"])
@login_required
def api_get_notifications():
    return jsonify(load_notification_log())


@app.route("/api/notifications", methods=["DELETE"])
@login_required
def api_clear_notifications():
    clear_notification_log()
    return jsonify({"ok": True})


def start_dashboard():
    if not DASHBOARD_USERNAME or not DASHBOARD_PASSWORD:
        log.warning("⚠️ لوحة التحكم معطّلة: حدّد DASHBOARD_USERNAME و DASHBOARD_PASSWORD بمتغيرات البيئة لتفعيلها")
        return
    try:
        log.info(f"📊 لوحة التحكم شغالة على المنفذ {DASHBOARD_PORT}")
        app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)
    except Exception as e:
        log.error(f"❌ خطأ تشغيل لوحة التحكم: {e}")


# ──────────────────────────────────────────────
# 🚀 البوت الرئيسي
# ──────────────────────────────────────────────
def run_bot():
    global consecutive_losses, pause_until, trading_enabled
    global _binance_client, _regime_detector, _last_correlation_update
    global current_strategy, INVERSE_BTC_ENABLED

    os.makedirs(DATA_DIR, exist_ok=True)   # 📁 تأكد إن مجلد البيانات (Volume) موجود
    log.info(f"📁 مجلد البيانات: {DATA_DIR}")
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    client     = Client(api_key, api_secret)
    _binance_client = client   # 📊 يخلي لوحة التحكم تقدر تستخدم نفس العميل

    load_symbols_from_txt()
    load_trades()
    load_circuit_state()   # 🛑 استرجاع حالة التوقف التلقائي لو موجودة
    load_settings()        # ⚙️ استرجاع الإعدادات المحفوظة (حجم الصفقة، الاستراتيجية، ...) لو موجودة

    # تزامن إعدادات Inverse BTC مع المكتبة (فرض القيم الصحيحة)
    set_inverse_config(**INVERSE_BTC_CONFIG)

    # 🧠 إنشاء كاشف حالة السوق (يُستخدم فقط لو AUTO_STRATEGY_ENABLED مفعّل)
    _regime_detector = MarketRegimeDetector(client, cooldown_minutes=20)   # ⬅️ بطلب المستخدم: كانت 45، خُفّفت لـ 20 دقيقة
    # تفعيل/إطفاء Inverse BTC بالتبديل التلقائي حسب الإعدادات المحفوظة
    _regime_detector.set_inverse_btc_enabled(
        INVERSE_BTC_ENABLED,
        INVERSE_BTC_CONFIG.get("btc_decline_threshold_pct", 3.0) / 100.0
    )

    try:
        log.info("🔍 جاري مطابقة وتصفية القائمة مع أسواق الـ Spot الرسمية...")
        exchange_info  = client.get_exchange_info()
        active_symbols = {s["symbol"] for s in exchange_info["symbols"] if s["status"] == "TRADING"}
        global SYMBOLS
        SYMBOLS = [s for s in SYMBOLS if s in active_symbols]
        save_symbols_to_txt()
        # ✅ إصلاح: تخزين مؤقت (cache) لمعلومات كل الرموز من نفس رد exchange_info،
        # بدل ما نطلب get_symbol_info لكل رمز لحاله لاحقاً بكل عملية شراء/بيع
        for s in exchange_info["symbols"]:
            if s["symbol"] in SYMBOLS:
                SYMBOL_INFO_CACHE[s["symbol"]] = s
        log.info("✅ تم فلترة وتأكيد العملات النشطة بنجاح.")
    except Exception as e:
        log.warning(f"⚠️ تأخر رد بينانس. تم اعتماد القائمة كاملة: {e}")

    telegram_thread = threading.Thread(target=telegram_command_listener, args=(client,), daemon=True)
    telegram_thread.start()

    dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
    dashboard_thread.start()

    # ── 📊 Range Trading BTC — Thread مستقل تماماً عن حلقة الفحص الرئيسية ──
    global _range_trading_strategy
    _range_trading_strategy = RangeTradingBTC(
        client,
        notify_fn=send_telegram,
        usdt_per_trade=RANGE_TRADING_USDT_PER_TRADE,
        live_trading=True,   # ⚠️ صفقات حقيقية — التفعيل الفعلي محكوم بـ RANGE_TRADING_ENABLED (مطفي افتراضياً)
        state_file=os.path.join(DATA_DIR, "range_trading_state.json"),
        history_file=os.path.join(DATA_DIR, "range_trading_history.json"),
    )
    range_trading_thread = threading.Thread(
        target=_range_trading_strategy.run,
        kwargs={"poll_seconds": 1800, "is_enabled_fn": lambda: RANGE_TRADING_ENABLED},
        daemon=True,
    )
    range_trading_thread.start()

    # ── 📈 Trend+Stoch الموازية — Thread مستقل تماماً كمان ──
    global _trend_parallel_strategy
    _trend_parallel_strategy = TrendStochParallel(
        client,
        notify_fn=send_telegram,
        config_fn=_get_live_risk_config,   # ⬅️ قيم ATR/Trailing/Breakeven حية من إعدادات البوت الأساسي
        usdt_per_trade=TREND_PARALLEL_USDT_PER_TRADE,
        live_trading=True,   # ⚠️ صفقات حقيقية — التفعيل الفعلي محكوم بـ TREND_PARALLEL_ENABLED (مطفي افتراضياً)
        state_file=os.path.join(DATA_DIR, "trend_stoch_state.json"),
        history_file=os.path.join(DATA_DIR, "trend_stoch_history.json"),
    )
    trend_parallel_thread = threading.Thread(
        target=_trend_parallel_strategy.run,
        kwargs={"poll_seconds": 3600, "is_enabled_fn": lambda: TREND_PARALLEL_ENABLED},
        daemon=True,
    )
    trend_parallel_thread.start()

    last_heartbeat = time.time()
    last_scan      = 0
    last_regime_check = 0

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

            # 🚦 لو البوت بفترة إيقاف مؤقت بسبب حظر -1003، ننام ونتخطى هالدورة بالكامل
            if is_api_blocked():
                remaining = int(_api_blocked_until - now)
                log.warning(f"🚦 البوت بفترة إيقاف مؤقت (-1003) — باقي {remaining} ثانية تقريباً")
                time.sleep(min(remaining, 30) if remaining > 0 else 5)
                continue

            log.info(f"🔄 فحص دوري | صفقات نشطة: {count_active_trades()}/{MAX_TRADES} | إجمالي مفتوحة: {len(open_trades)} | مراقبة مكثفة: {len(watch_list)}")

            # 🛑 استئناف تلقائي بعد انتهاء فترة التوقف
            with _lock:
                should_resume = (pause_until != 0 and now >= pause_until and not trading_enabled)
            if should_resume:
                with _lock:
                    trading_enabled = True
                    pause_until     = 0
                save_circuit_state()
                log.info("✅ انتهت فترة التوقف التلقائي — استئناف التداول")
                send_telegram("✅ <b>انتهت فترة التوقف التلقائي</b>\nتم استئناف التداول بشكل تلقائي.")

            # 🧠 فحص حالة السوق وتبديل الاستراتيجية تلقائياً (لو مفعّل)
            with _lock:
                auto_on = AUTO_STRATEGY_ENABLED
                inv_cfg = dict(INVERSE_BTC_CONFIG)
            # 🧠 نزامن حالة Inverse BTC بالكاشف — تلقائي حسب الاستراتيجية الحالية
            inv_enabled = (current_strategy == "inverse_btc")
            INVERSE_BTC_ENABLED = inv_enabled
            new_threshold = inv_cfg.get("btc_decline_threshold_pct", 3.0) / 100.0
            if _regime_detector and (
                _regime_detector.inverse_btc_enabled != inv_enabled
                or abs(_regime_detector.inverse_btc_decline_threshold - new_threshold) > 1e-9
            ):
                _regime_detector.set_inverse_btc_enabled(inv_enabled, new_threshold)
            if auto_on and _regime_detector and (now - last_regime_check >= AUTO_STRATEGY_INTERVAL):
                last_regime_check = now
                try:
                    new_strategy, reason, switched = _regime_detector.check()
                    if switched and new_strategy:
                        with _lock:
                            current_strategy = new_strategy
                        save_settings()
                        log.info(f"🧠 تبديل تلقائي → {new_strategy} | {reason}")
                        send_telegram(
                            f"🔄 <b>تبديل تلقائي للاستراتيجية</b>\n"
                            f"➡️ {STRATEGY_LABELS.get(new_strategy, new_strategy)}\n"
                            f"📋 السبب: {reason}"
                        )
                except Exception as e:
                    log.error(f"❌ خطأ فحص حالة السوق: {e}")

            # ── Heartbeat كل ساعة ─────────
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                try:
                    usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                except:
                    usdt_balance = 0.0
                with _lock:
                    status = "▶️ شغال" if trading_enabled else "⏸️ موقوف"
                send_telegram(
                    f"💚 <b>البوت شغال</b>\n"
                    f"🔘 التداول: {status}\n"
                    f"💰 رصيد USDT: ${usdt_balance:.2f}\n"
                    f"💼 صفقات نشطة: {count_active_trades()}/{MAX_TRADES} | إجمالي مفتوحة: {len(open_trades)}\n"
                    f"👁️ يراقب {len(SYMBOLS)} عملة"
                )
                last_heartbeat = now

            # ── 1. إدارة الصفقات المفتوحة ──
            # ✅ إصلاح: جلب كل الأسعار بطلب واحد مجمّع (get_all_tickers) بدل طلب سعر
            # كل عملة لحالها بحلقة — هذا يقلل استهلاك وزن الـ API بشكل كبير ويحمي من -1003
            open_symbols_now = list(open_trades.keys())
            prices_map        = get_all_prices(client, open_symbols_now) if open_symbols_now else {}

            for symbol in open_symbols_now:
                if symbol not in open_trades:
                    continue
                trade = open_trades[symbol]
                try:
                    price = prices_map.get(symbol)
                    if not price:
                        # احتياط: لو ما رجع بالطلب المجمّع لأي سبب، نطلبه لحاله كحل أخير
                        price = get_current_price(client, symbol)
                    if not price:
                        continue
                    coin  = symbol.replace("USDT", "")

                    # 🛡️ مرحلة حماية التعادل (Breakeven): تُفعّل بربح بسيط (BREAKEVEN_ACTIVATE_PCT)
                    # قبل التريلينج الكامل بكتير. الستوب ينتقل لسعر الدخول + هامش يغطي العمولة،
                    # ويُخزَّن كـ"أرضية" (breakeven_floor) بالصفقة — لا يقدر أي حساب لاحق (حتى
                    # التريلينج نفسه لو مسافة ATR أوسع من الربح الحالي) ينزل تحتها أبداً طول عمر الصفقة.
                    # هذا يمنع حالة "الستوب بينقفل بالضبط على سعر الدخول الخام (بدون هامش)"
                    # يلي كانت بتتحول لخسارة بسيطة بعد العمولة والانزلاق عند التنفيذ الفعلي.
                    if trade.get("breakeven_floor") is None:
                        breakeven_activate_pct = trade.get("breakeven_activate_pct", BREAKEVEN_ACTIVATE_PCT)
                        breakeven_margin_pct   = trade.get("breakeven_margin_pct", BREAKEVEN_MARGIN_PCT)
                        if price >= trade["entry_price"] * (1 + breakeven_activate_pct):
                            floor_price = round(trade["entry_price"] * (1 + breakeven_margin_pct), 8)
                            trade["breakeven_floor"] = floor_price
                            # الستوب ينتقل فوراً للأرضية الجديدة، بس فقط لو هيك بيرفعه (ما ينزل الستوب أبداً)
                            if floor_price > trade["stop_loss"]:
                                trade["stop_loss"] = floor_price
                            log.info(f"🛡️ حماية تعادل مفعّلة لـ {coin} | أرضية: {floor_price} (دخول + هامش عمولة)")
                            save_trades()

                    # الأرضية الفعّالة لأي حساب ستوب لاحق: أرضية التعادل لو اتفعّلت، وإلا سعر الدخول الخام
                    stop_floor = trade.get("breakeven_floor") or trade["entry_price"]

                    if not trade["trailing_active"]:
                        # 🧠 لو الصفقة دخلت بوضع Strict Mode (عملة ذات تاريخ ضعيف بذاكرة العملات)،
                        # نقطة تفعيل الـ Trailing تكون مضاعفة (تعويض تضييق الـ SL بمهلة ربح أوسع).
                        trail_activate_pct = trade.get("trail_activate_pct", TRAIL_ACTIVATE_PCT)
                        if price >= trade["entry_price"] * (1 + trail_activate_pct):
                            trade["trailing_active"] = True
                            trade["highest_price"]   = price
                            # ✅ الستوب وقت تفعيل التريلينج ما ينزل تحت أرضية التعادل (دخول+هامش)،
                            # حتى لو مسافة الـ ATR كانت أوسع من الربح الحالي وقت التفعيل.
                            trade["stop_loss"]       = max(compute_trail_stop(price, trade), stop_floor)
                            log.info(f"🎯 Trailing مفعّل لـ {coin} | ستوب: {trade['stop_loss']} (محمي عند {('أرضية التعادل' if trade.get('breakeven_floor') else 'سعر الدخول')} كحد أدنى)")
                            save_trades()

                    if trade["trailing_active"]:
                        if price > trade["highest_price"]:
                            trade["highest_price"] = price
                            # ✅ نفس الحماية: الستوب بعد التفعيل ما ينزل تحت أرضية التعادل أبداً
                            trade["stop_loss"]     = max(compute_trail_stop(price, trade), stop_floor)
                            save_trades()
                        elif price <= trade["stop_loss"]:
                            sell_price, sold_qty = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                # ✅ إصلاح: الربح ومبلغ الشراء/البيع محسوبين على الكمية الفعلية المُنفَّذة
                                # (sold_qty) بدل الكمية المسجلة بالذاكرة، عشان تطابق بينانس تمامًا
                                profit      = round((sell_price - trade["entry_price"]) * sold_qty, 4)
                                buy_amount  = round(trade["entry_price"] * sold_qty, 4)
                                sell_amount = round(sell_price * sold_qty, 4)
                                pct         = round((sell_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)
                                record_trade_result(symbol, trade["entry_price"], sell_price, sold_qty, "trailing_stop",
                                                     entry_slippage_pct=trade.get("entry_slippage_pct", 0.0))  # ✅ إصلاح #3
                                consecutive_losses = 0   # 🛑 صفقة رابحة → تصفير عدّاد الخسارات المتتالية
                                save_circuit_state()
                                send_telegram(
                                    f"💰 <b>جني أرباح - {coin}</b>\n"
                                    f"💵 دخول: {trade['entry_price']:.6f}$ → خروج: {sell_price:.6f}$\n"
                                    f"🧾 مبلغ الشراء: {buy_amount:.4f} USDT → مبلغ البيع: {sell_amount:.4f} USDT\n"
                                    f"📊 النسبة: {pct:+.2f}%\n"
                                    f"💹 PnL: {profit:+.4f} USDT"
                                )
                                del open_trades[symbol]
                                save_trades()
                                continue
                    else:
                        # ✅ إصلاح خلل: نستخدم trade["stop_loss"] المحفوظ فعلياً وقت الشراء
                        # (المبني على ATR أو الاحتياطي الثابت)، بدل إعادة حسابه من الصفر بالنسبة الثابتة
                        # في كل دورة — كان هذا يلغي فائدة ATR قبل تفعيل Trailing.
                        if price <= trade["stop_loss"]:
                            sell_price, sold_qty = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                # ✅ إصلاح: الخسارة ومبلغ الشراء/البيع محسوبين على الكمية الفعلية المُنفَّذة
                                # (sold_qty) بدل الكمية المسجلة بالذاكرة، عشان تطابق بينانس تمامًا
                                loss        = round((sell_price - trade["entry_price"]) * sold_qty, 4)
                                buy_amount  = round(trade["entry_price"] * sold_qty, 4)
                                sell_amount = round(sell_price * sold_qty, 4)
                                pct         = round((sell_price - trade["entry_price"]) / trade["entry_price"] * 100, 2)
                                record_trade_result(symbol, trade["entry_price"], sell_price, sold_qty, "stop_loss",
                                                     entry_slippage_pct=trade.get("entry_slippage_pct", 0.0))  # ✅ إصلاح #3
                                send_telegram(
                                    f"🚨 <b>ستوب لوز - {coin}</b>\n"
                                    f"📉 السعر: {sell_price:.6f}$\n"
                                    f"🧾 مبلغ الشراء: {buy_amount:.4f} USDT → مبلغ البيع: {sell_amount:.4f} USDT\n"
                                    f"📊 النسبة: {pct:+.2f}%\n"
                                    f"💸 خسارة: {loss:.4f} USDT"
                                )

                                # 🛑 Circuit Breaker: عدّ الخسارات المتتالية
                                consecutive_losses += 1
                                save_circuit_state()
                                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                                    with _lock:
                                        trading_enabled = False
                                        pause_until     = time.time() + PAUSE_DURATION_SECONDS
                                    consecutive_losses = 0
                                    save_circuit_state()
                                    log.warning(f"🛑 توقف تلقائي: {MAX_CONSECUTIVE_LOSSES} ستوب لوز متتالية — توقف لمدة {PAUSE_DURATION_SECONDS//3600} ساعة")
                                    send_telegram(
                                        f"🛑 <b>توقف تلقائي للتداول!</b>\n"
                                        f"⚠️ {MAX_CONSECUTIVE_LOSSES} صفقات ستوب لوز متتالية\n"
                                        f"⏸️ التداول متوقف لمدة {PAUSE_DURATION_SECONDS//3600} ساعة\n"
                                        f"✅ سيُستأنف تلقائيًا، أو اكتب /start لاستئنافه يدويًا"
                                    )

                                del open_trades[symbol]
                                save_trades()
                                continue

                except Exception as e:
                    log.error(f"❌ إدارة {symbol}: {e}")

            # ── 2. فحص خفيف لكل العملات ──────
            if now - last_scan >= SCAN_INTERVAL:
                scan_all_symbols(client)
                last_scan = now

            # 🧠 تحديث تصنيف ارتباط العملات مع BTC دورياً (وليس كل دورة) — ذاكرة العملات
            if not is_api_blocked() and (now - _last_correlation_update >= CORRELATION_UPDATE_INTERVAL):
                update_symbol_correlations(client, list(SYMBOLS))
                _last_correlation_update = now

            # ── 3. فحص مكثف للمرشحين ───
            with _lock:
                is_trading = trading_enabled

            if watch_list and is_trading and count_active_trades() < MAX_TRADES and not is_api_blocked():
                with _lock:
                    ma20_on  = ma20_enabled
                    strategy = current_strategy

                # ══ المرحلة 1: تقييم كل المرشحين وتجميع من نجح منهم بإشارة شراء ══
                # (بدل شراء أول مرشح نلاقيه، نجمعهم كلهم أول، ونرتبهم بعدين حسب قوة الزخم)
                candidates = []   # كل عنصر: (momentum_score, symbol, price, atr_value, signal_info)

                for symbol in list(watch_list):
                    if is_api_blocked():
                        log.warning("🚦 تم اكتشاف حظر أثناء الفحص المكثف — إيقاف باقي الدورة الحالية")
                        break
                    if symbol in open_trades:
                        watch_list.discard(symbol)
                        continue

                    # ── استراتيجية RSI العادي ──────────────────
                    if strategy == "rsi":
                        ind = get_indicators(client, symbol)
                        if not ind:
                            time.sleep(0.2)
                            continue
                        ma20_condition = (ind["price"] > ind["ma20"]) if ma20_on else True
                        buy_signal     = ind["rsi_prev"] < RSI_BUY_PREV and ind["rsi"] >= RSI_BUY_CURR and ma20_condition
                        if buy_signal and not passes_confirmation_filters(ind):
                            buy_signal = False
                        if buy_signal:
                            signal_info = f"📊 RSI: {ind['rsi_prev']} → {ind['rsi']}"
                            candidates.append((ind.get("momentum_score", 0.0), symbol, ind["price"], ind.get("atr"), signal_info))

                    # ── استراتيجية Stochastic RSI ──────────────
                    elif strategy == "stoch_rsi":
                        stoch = check_stoch_rsi(client, symbol)
                        if not stoch:
                            time.sleep(0.2)
                            continue
                        ma20_condition = (stoch["price"] > stoch["ma20"]) if ma20_on else True
                        buy_signal     = ma20_condition
                        if buy_signal and not passes_confirmation_filters(stoch):
                            buy_signal = False
                        if buy_signal:
                            signal_info = f"📊 Stoch K: {stoch['k_prev']} → {stoch['k_curr']} | D: {stoch['d_prev']} → {stoch['d_curr']}"
                            candidates.append((stoch.get("momentum_score", 0.0), symbol, stoch["price"], stoch.get("atr"), signal_info))

                    # ── استراتيجية Trend + StochRSI ─────────────
                    elif strategy == "trend_stoch":
                        trend_sig = check_trend_stoch(client, symbol)
                        if not trend_sig:
                            time.sleep(0.2)
                            continue
                        # كل شروط trend_stoch الأساسية اتفحصت جوا الدالة نفسها؛ يضل بس فلاتر VWAP/BB الاختيارية
                        if passes_confirmation_filters(trend_sig):
                            signal_info = (
                                f"🚀 Trend+Stoch K: {trend_sig['k_prev']} → {trend_sig['k_curr']} | "
                                f"D: {trend_sig['d_prev']} → {trend_sig['d_curr']} | "
                                f"β={trend_sig.get('beta', '؟')} (أعنف من BTC) + فوليوم قوي"
                            )
                            candidates.append((trend_sig.get("momentum_score", 0.0), symbol, trend_sig["price"], trend_sig.get("atr"), signal_info))

                    # ── استراتيجية Inverse BTC ──────────────────
                    elif strategy == "inverse_btc":
                        inv_sig = check_inverse_btc(client, symbol)
                        if not inv_sig:
                            time.sleep(0.2)
                            continue
                        # كل الشروط اتفحصت جوا check_inverse_btc
                        buy_signal = True
                        signal_info = inv_sig["signal_info"]
                        price = inv_sig["price"]
                        atr_value = inv_sig.get("atr")
                        # نستخدم نسبة صعود العملة الفعلية كـ momentum score للترتيب
                        momentum = inv_sig.get("coin_return_pct", 0.0) / 100.0
                        candidates.append((momentum, symbol, price, atr_value, signal_info))

                    else:
                        time.sleep(0.2)
                        continue

                    time.sleep(0.2)

                # ══ المرحلة 2: ترتيب المرشحين — ذاكرة العملات أولاً (تاريخ نظيف يتقدّم)، والزخم كمُرجِّح ثانوي ══
                if candidates:
                    cand_map = {c[1]: c for c in candidates}   # symbol -> (momentum, symbol, price, atr_value, signal_info)

                    # 🌡️ حالة السوق الحالية (BULL/BEAR/SIDEWAYS) — نحسبها أولاً، مرة واحدة لكل دورة،
                    # ونمرّرها لمحرك الترتيب عشان يُفعّل فعليًا مكافأة/عقوبة تصنيف الارتباط مع BTC:
                    # وقت هبوط BTC (BEAR) تُفضَّل العملات المصنّفة INVERSE_STRENGTH (تقاوم/تبرز)،
                    # ووقت صعوده (BULL) تُفضَّل العملات المصنّفة TREND_FOLLOWER (تتماشى معه).
                    regime_label = _regime_detector.get_regime_label() if _regime_detector else "SIDEWAYS"
                    try:
                        market_regime = MarketRegime(regime_label)
                    except ValueError:
                        market_regime = MarketRegime.SIDEWAYS

                    # 🧠 لا نختار مباشرة بالزخم فقط؛ نرجع لذاكرة العملات لتصنيف كل مرشح
                    # (تاريخ نظيف / تاريخ ضعيف يتطلب Strict Mode / بدون سجل كافٍ بعد) + مكافأة الارتباط مع BTC
                    ranked = smart_ranker.rank(list(cand_map.keys()), btc_trend=regime_label)

                    # ترتيب نهائي: غير-الـ Strict أولاً (تاريخ نظيف)، وداخل كل مجموعة الأعلى score
                    # ثم الأعلى زخماً كمُرجِّح أخير عند تساوي score تقريباً
                    ranked.sort(key=lambda r: (r.strict_mode, -r.score, -cand_map[r.symbol][0]))

                    if len(ranked) > 1:
                        log.info(
                            "🏆 %d مرشح بنفس الدورة — الترتيب الذكي (%s): %s",
                            len(ranked), regime_label,
                            [(r.symbol, round(r.score, 2), "STRICT" if r.strict_mode else "OK") for r in ranked],
                        )

                    for ranked_symbol in ranked:
                        symbol = ranked_symbol.symbol
                        momentum_score, _, price, atr_value, signal_info = cand_map[symbol]
                        is_strict = ranked_symbol.strict_mode

                        if is_api_blocked():
                            break
                        if count_active_trades() >= MAX_TRADES:
                            break
                        if symbol in open_trades:
                            watch_list.discard(symbol)
                            continue

                        try:
                            usdt_balance = float(client.get_asset_balance(asset="USDT")["free"])
                        except Exception as e:
                            log.error(f"❌ رصيد USDT: {e}")
                            continue

                        if usdt_balance >= (TRADE_AMOUNT + RESERVE_USDT):   # ✅ RESERVE_USDT = 0.0 الآن، أي بدون احتياطي جانبي
                            res = buy_market(client, symbol, TRADE_AMOUNT)
                            if res:
                                res["trailing_active"] = False
                                res["highest_price"]   = res["entry_price"]
                                res["breakeven_floor"]  = None
                                res["strict_mode"]     = is_strict

                                # 📐 صمام أمان ATR/SL: مرتبط بحالة السوق (BULL/BEAR/SIDEWAYS)، مع سقف
                                # صلب لا يتجاوز 3% مطلقًا. سقف Strict Mode تحديدًا يتبع مباشرة إعداد
                                # "حد الخسارة الثابت" (STOP_LOSS_PCT) اللي المستخدم متحكم فيه من التطبيق/تيليغرام،
                                # بدل رقم مبرمج بالكود — فيخفف الضربات تلقائيًا حسب حالة السوق فوق هالسقف.
                                with _lock:
                                    multiplier = ATR_MULTIPLIER
                                    strict_cap_pct = STOP_LOSS_PCT * 100
                                if atr_value:
                                    sl_info = atr_guard.compute_stop_loss(
                                        entry_price=res["entry_price"],
                                        raw_atr_value=atr_value,
                                        market_regime=market_regime,
                                        strict_mode=is_strict,
                                        atr_sl_multiple=multiplier,
                                        strict_cap_pct=strict_cap_pct,
                                    )
                                    stop_price = sl_info["sl_price"]
                                    used_atr   = atr_value
                                    stop_method = f"ATR×{sl_info['applied_atr_multiplier']}"
                                    if sl_info["capped"]:
                                        stop_method += " (سقف صلب)"
                                else:
                                    # لا يوجد ATR متاح: احتياطي بنسبة ثابتة، مقيّد بنفس السقف الصلب لضمان الاتساق
                                    fallback_pct = min(STOP_LOSS_PCT * 100, atr_guard.HARD_CAP_PCT) / 100
                                    stop_price   = res["entry_price"] * (1 - fallback_pct)
                                    used_atr     = None
                                    stop_method  = "ثابت (سقف صلب)"

                                res["stop_loss"] = round(stop_price, 8)
                                res["atr"]       = used_atr   # None لو استخدمنا الاحتياطي الثابت

                                # 🧠 مضاعفة نقطة تفعيل Trailing (تقبّليات أوسع) بوضع Strict Mode فقط
                                tp_multiplier = atr_guard.get_take_profit_multiplier(is_strict)
                                res["trail_activate_pct"] = round(TRAIL_ACTIVATE_PCT * tp_multiplier, 6)

                                # 🛡️ قفل إعدادات حماية التعادل الحالية وقت الشراء بالصفقة نفسها،
                                # عشان أي تعديل لاحق على الإعدادات العامة ما يأثر على صفقة مفتوحة أصلاً
                                with _lock:
                                    res["breakeven_activate_pct"] = BREAKEVEN_ACTIVATE_PCT
                                    res["breakeven_margin_pct"]   = BREAKEVEN_MARGIN_PCT

                                open_trades[symbol]    = res
                                save_trades()
                                watch_list.discard(symbol)

                                coin_name   = symbol.replace("USDT", "")
                                sl_value    = res["stop_loss"]
                                strict_line = "\n⚠️ Strict Mode: تاريخ ضعيف/غير مختبر — ATR مخفّض وتقبّل مضاعف" if is_strict else ""
                                momentum_line = f"🏆 زخم: {momentum_score:.2f}\n" if len(ranked) > 1 else ""
                                send_telegram(
                                    f"🟢 <b>شراء {coin_name}</b>\n"
                                    f"{signal_info}\n"
                                    f"{momentum_line}"
                                    f"💵 السعر: {res['entry_price']}\n"
                                    f"🛡️ Stop Loss ({stop_method}): {sl_value}"
                                    f"{strict_line}\n"
                                    f"💼 صفقات نشطة: {count_active_trades()}/{MAX_TRADES} | إجمالي مفتوحة: {len(open_trades)}"
                                )
                        else:
                            log.warning(f"⚠️ رصيد غير كافٍ: {usdt_balance:.2f} USDT")
                            break   # الرصيد مش كافي أصلاً، ما فيه داعي نكمل نفحص باقي المرشحين

        except BinanceAPIException as e:
            if is_rate_limit_error(e):
                register_api_block("run_bot main loop")
            log.error(f"❌ بينانس: {e.status_code} | {e.message}")
            send_admin(f"خطأ بينانس: {e.status_code} | {e.message}")
        except Exception as e:
            log.error(f"❌ خطأ عام: {e}")

        time.sleep(WATCH_INTERVAL)

if __name__ == "__main__":
    run_bot()
