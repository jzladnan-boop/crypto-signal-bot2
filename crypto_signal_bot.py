"""
Crypto Trading Bot - RSI Auto Trader
نفس الكود الأصلي + إصلاح 5 أخطاء فقط بدون تغيير المنطق
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

# ──────────────────────────────────────────────
# ⚙️ الإعدادات الأساسية — نفس الأصلي
# ──────────────────────────────────────────────
DATA_DIR       = os.getenv("DATA_DIR", "/app/data")   # 📁 مجلد دائم (Volume) على Railway
SYMBOLS_FILE   = os.path.join(DATA_DIR, "symbols.txt")
TRADES_FILE    = os.path.join(DATA_DIR, "open_trades.json")
PROFIT_FILE    = os.path.join(DATA_DIR, f"profit_{time.strftime('%Y_%m')}.json")   # ✅ إصلاح #4: ملف شهري منفصل
CIRCUIT_FILE   = os.path.join(DATA_DIR, "circuit_breaker.json")   # 🛑 ملف لحفظ حالة التوقف التلقائي

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
STOP_LOSS_PCT      = 0.02
TRAIL_PCT          = 0.01
TRAIL_ACTIVATE_PCT = 0.01
TRADE_AMOUNT       = 15.0
RESERVE_USDT       = 2.0
MAX_TRADES         = 4
HEARTBEAT_INTERVAL = 3600
MA_PERIOD          = 20

SCAN_INTERVAL      = 120
WATCH_INTERVAL     = 10
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
# ✅ إصلاح #1: threading.Lock بدل Global مباشر
# ──────────────────────────────────────────────
_lock           = threading.Lock()
trading_enabled = True
watch_list      = set()
open_trades     = {}
ma20_enabled    = True
current_strategy   = "rsi"   # 🎯 الاستراتيجية الحالية: "rsi" أو "stoch_rsi"
consecutive_losses = 0   # 🛑 عدّاد الستوب لوز المتتالية
pause_until        = 0   # 🛑 timestamp لنهاية التوقف التلقائي (0 = مافي توقف)
_binance_client     = None   # 📊 مرجع لعميل بينانس، تستخدمه لوحة التحكم

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
                log.info(
                    f"📂 صفقة محملة: {coin} | دخول: {trade['entry_price']:.4f}$ | "
                    f"ستوب: {trade['stop_loss']:.4f}$ | Trailing: {trade['trailing_active']}"
                )
            log.info(f"✅ تم تحميل {len(open_trades)} صفقة من الذاكرة")
            save_trades()
        except Exception as e:
            log.error(f"❌ خطأ تحميل الصفقات: {e}")

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

def record_trade_result(symbol, entry_price, exit_price, qty, reason):
    """يسجل نتيجة كل صفقة في ملف الشهر الحالي"""
    month      = time.strftime("%Y_%m")
    profit_log = load_profit_log(month)
    profit     = round((exit_price - entry_price) * qty, 4)
    profit_log.append({
        "symbol"     : symbol,
        "entry_price": entry_price,
        "exit_price" : exit_price,
        "qty"        : qty,
        "profit"     : profit,
        "reason"     : reason,
        "time"       : time.strftime("%Y-%m-%d %H:%M:%S")
    })
    save_profit_log(profit_log, month)

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
    global pause_until, consecutive_losses, STOP_LOSS_PCT, TRAIL_ACTIVATE_PCT
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
                            trades_copy      = dict(open_trades)
                            pause_left       = pause_until
                            strategy_label   = "RSI العادي" if current_strategy == "rsi" else "Stochastic RSI"
                            interval_minutes = INTERVAL_TO_MINUTES.get(current_interval, 30)
                            ma20_status      = "✅ مفعّل" if ma20_enabled else "❌ مطفي"
                        msg = (
                            f"📊 <b>حالة البوت</b>\n"
                            f"🔘 التداول: {status}\n"
                            f"💰 USDT المتاح: ${usdt_balance:.2f}\n"
                            f"💼 صفقات: {trades_count}/{MAX_TRADES}\n"
                            f"👁️ يراقب: {len(SYMBOLS)} عملة\n"
                            f"🔍 مراقبة مكثفة: {len(watch_list)} عملة\n"
                            f"📊 الاستراتيجية: {strategy_label}\n"
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
                                send_admin(f"✅ أقصى صفقات: {MAX_TRADES}")
                        except:
                            send_admin("❌ مثال: /set_max_trades 5")

                    # ── /set_trail ────────────────────────────
                    elif text.startswith("/set_trail "):
                        try:
                            value = float(text.replace("/set_trail ", "")) / 100
                            if value <= 0:
                                send_admin("❌ القيمة لازم تكون أكبر من صفر.")
                            else:
                                with _lock:   # ✅ إصلاح: حماية race condition
                                    TRAIL_PCT = value
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
                                send_admin(f"✅ نقطة تفعيل Trailing: {TRAIL_ACTIVATE_PCT*100}%\nيعني البوت ما يبدأ يتتبع السعر إلا لما يربح {TRAIL_ACTIVATE_PCT*100}% أول.")
                        except:
                            send_admin("❌ مثال: /set_activate 0.5")

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
                                send_admin(f"✅ منطقة RSI: {RSI_WATCH_LOW} - {RSI_WATCH_HIGH}")
                        except:
                            send_admin("❌ مثال: /set_rsi_range 20 38")

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
                        send_admin("✅ تم تفعيل فيلتر MA20")

                    # ── /disable_ma20 ────────────────────────
                    elif text == "/disable_ma20":
                        with _lock:   # ✅ إصلاح #1
                            ma20_enabled = False
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
                        else:
                            trade_entry = open_trades.get(symbol, {}).get("entry_price", 0)
                            # ملاحظة: السعر جُلب بعد الحذف، نحسب PnL من السجل
                            profit_log = load_profit_log()
                            last       = next((r for r in reversed(profit_log) if r["symbol"] == symbol), None)
                            pnl        = f"{last['profit']:+.4f} USDT" if last else "—"
                            send_admin(
                                f"🔴 <b>إغلاق يدوي - {coin}</b>\n"
                                f"💵 سعر البيع: {sell_price:.4f}$\n"
                                f"💹 PnL: {pnl}"
                            )

                    # ── /set_strategy ─────────────────────────
                    elif text.startswith("/set_strategy "):
                        strategy = text.replace("/set_strategy ", "").strip().lower()
                        if strategy in ("rsi", "stoch_rsi"):
                            with _lock:
                                current_strategy = strategy
                            labels = {"rsi": "RSI العادي (ارتداد فوق 30)", "stoch_rsi": "Stochastic RSI (تقاطع K فوق D واختراق 20)"}
                            send_admin(f"✅ تم تغيير الاستراتيجية إلى: {labels[strategy]}")
                        else:
                            send_admin("❌ الاستراتيجيات المتاحة:\n/set_strategy rsi\n/set_strategy stoch_rsi")

                    # ── /config ───────────────────────────────
                    elif text == "/config":
                        with _lock:
                            strategy_label   = "RSI العادي" if current_strategy == "rsi" else "Stochastic RSI"
                            interval_minutes = INTERVAL_TO_MINUTES.get(current_interval, 30)
                            ma20_status      = "✅ مفعّل" if ma20_enabled else "❌ مطفي"
                            trade_amt        = TRADE_AMOUNT
                            max_tr           = MAX_TRADES
                            trail            = TRAIL_PCT * 100
                            stoploss         = STOP_LOSS_PCT * 100
                            activate         = TRAIL_ACTIVATE_PCT * 100
                        send_admin(
                            f"⚙️ <b>الإعدادات الحالية</b>\n\n"
                            f"📊 الاستراتيجية: {strategy_label}\n"
                            f"🕯️ الفريم: {interval_minutes} دقيقة\n"
                            f"📈 MA20: {ma20_status}\n"
                            f"💰 حجم الصفقة: ${trade_amt}\n"
                            f"💼 أقصى صفقات: {max_tr}\n"
                            f"🛑 حد الخسارة (Stop Loss): {stoploss}%\n"
                            f"🎯 تفعيل Trailing عند: {activate}% ربح\n"
                            f"🔍 مساحة Trailing Stop: {trail}%"
                        )

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
                            "/set_rsi_range 20 38 — نطاق RSI للمراقبة المكثفة\n"
                            "/set_interval 30 — الفريم الزمني للشموع (15/30/60/240 دقيقة)\n\n"
                            "<b>الاستراتيجية:</b>\n"
                            "/set_strategy rsi — شراء عند ارتداد RSI فوق 30\n"
                            "/set_strategy stoch_rsi — شراء عند تقاطع Stochastic RSI واختراق مستوى 20\n\n"
                            "<b>المؤشرات:</b>\n"
                            "/enable_ma20 — تشغيل فيلتر MA20 (مستقل عن الاستراتيجية)\n"
                            "/disable_ma20 — تعطيل فيلتر MA20\n\n"
                            "<b>التقارير:</b>\n"
                            "/config — عرض كل الإعدادات الحالية\n"
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
    except Exception as e:
        log.error(f"❌ RSI سريع {symbol}: {e}")
        return None

def scan_all_symbols(client):
    """المرحلة 1: فحص خفيف لكل العملات"""
    global watch_list
    new_watch = set()
    log.info(f"🔍 فحص خفيف لـ {len(SYMBOLS)} عملة...")
    for symbol in list(SYMBOLS):
        if symbol in open_trades:
            continue
        rsi = get_rsi_quick(client, symbol)
        if rsi is not None and RSI_WATCH_LOW <= rsi <= RSI_WATCH_HIGH:
            new_watch.add(symbol)
        time.sleep(0.15)  # ✅ إصلاح #5: تأخير أكبر لتجنب Rate Limit
    added = new_watch - watch_list
    if added:
        log.info(f"👀 مرشحون جدد: {[s.replace('USDT','') for s in added]}")
    with _lock:   # ✅ إصلاح #3: حماية watch_list من التعديل المتزامن
        watch_list = new_watch

# ──────────────────────────────────────────────
# جلب المؤشرات
# ──────────────────────────────────────────────
def get_current_price(client, symbol):
    """سعر لحظي فقط — طلب واحد خفيف لتتبع الصفقات المفتوحة (بدون RSI/MA20)"""
    try:
        return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except Exception as e:
        log.error(f"❌ سعر {symbol}: {e}")
        return None

def get_indicators(client, symbol):
    try:
        klines = client.get_klines(symbol=symbol, interval=current_interval, limit=100)
        closes = pd.Series([float(k[4]) for k in klines])
        rsi    = ta.momentum.RSIIndicator(close=closes, window=RSI_PERIOD).rsi()
        ma20   = closes.rolling(window=MA_PERIOD).mean().iloc[-1]
        price  = float(closes.iloc[-1])   # من الـ klines مباشرة، بدون طلب API ثاني
        return {
            "rsi"     : round(rsi.iloc[-1], 2),
            "rsi_prev": round(rsi.iloc[-2], 2),
            "price"   : price,
            "ma20"    : round(ma20, 8),
        }
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
        klines = client.get_klines(symbol=symbol, interval=current_interval, limit=100)
        closes = pd.Series([float(k[4]) for k in klines])

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
            return {
                "k_curr": k_curr,
                "k_prev": k_prev,
                "d_curr": d_curr,
                "d_prev": d_prev,
                "price" : price,
                "ma20"  : ma20,
            }
        return None
    except Exception as e:
        log.error(f"❌ Stoch RSI {symbol}: {e}")
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

# ──────────────────────────────────────────────
# 🔴 إغلاق صفقة يدوياً
# ──────────────────────────────────────────────
def close_trade(client, symbol):
    """يغلق صفقة مفتوحة يدوياً بأمر من المستخدم"""
    with _lock:
        if symbol not in open_trades:
            return None, "not_found"
        trade = open_trades[symbol]
    sell_price = sell_market(client, symbol, trade["qty"])
    if sell_price:
        record_trade_result(symbol, trade["entry_price"], sell_price, trade["qty"], "manual_close")
        with _lock:
            if symbol in open_trades:
                del open_trades[symbol]
        save_trades()
        return sell_price, "ok"
    return None, "sell_failed"

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
    """✅ إصلاح #2: البيع بالكمية الكاملة بدون طرح عمولة يدوي"""
    try:
        info      = client.get_symbol_info(symbol)
        step_size = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break
        sell_qty = qty  # ✅ إصلاح: بينانس يخصم العمولة تلقائياً من USDT
        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
            sell_qty  = round(sell_qty - (sell_qty % step_size), precision)
        if sell_qty <= 0:
            return None
        order = client.order_market_sell(symbol=symbol, quantity=sell_qty)
        # ✅ إصلاح #1: سعر التنفيذ الفعلي من الأوردر مباشرة (weighted average)
        fills = order.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            price     = sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
        else:
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        log.info(f"✅ بيع {symbol} | السعر: {price:.6f} | الكمية: {sell_qty}")
        return price
    except BinanceAPIException as e:
        log.error(f"❌ بيع {symbol}: {e.status_code} | {e.message}")
        return None
    except Exception as e:
        log.error(f"❌ بيع {symbol}: {e}")
        return None

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
        try:
            balance = float(_binance_client.get_asset_balance(asset="USDT")["free"])
        except Exception as e:
            log.error(f"❌ API رصيد: {e}")

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
        "max_trades": max_trades,
        "watch_count": len(watch_list),
        "symbols_count": len(SYMBOLS),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    })


# ── الصفقات المفتوحة ─────────────────────────────
@app.route("/api/trades")
@login_required
def api_trades():
    result = []
    for symbol, t in dict(open_trades).items():
        current_price = t["entry_price"]
        if _binance_client:
            try:
                current_price = float(_binance_client.get_symbol_ticker(symbol=symbol)["price"])
            except Exception:
                pass
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
        return jsonify({
            "trade_amount": TRADE_AMOUNT,
            "max_trades": MAX_TRADES,
            "trail_pct": round(TRAIL_PCT * 100, 4),
            "interval_minutes": INTERVAL_TO_MINUTES.get(current_interval, 30),
            "rsi_low": RSI_WATCH_LOW,
            "rsi_high": RSI_WATCH_HIGH,
            "ma20_enabled": ma20_enabled,
        })


@app.route("/api/settings", methods=["POST"])
@login_required
def api_set_settings():
    global TRADE_AMOUNT, MAX_TRADES, TRAIL_PCT, RSI_WATCH_LOW, RSI_WATCH_HIGH
    global current_interval, ma20_enabled
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

        if "rsi_low" in data and "rsi_high" in data:
            low, high = float(data["rsi_low"]), float(data["rsi_high"])
            if low <= 0 or high <= 0 or low >= high:
                errors.append("منطقة RSI غير صحيحة")
            else:
                RSI_WATCH_LOW, RSI_WATCH_HIGH = low, high

        if "interval_minutes" in data:
            minutes = int(data["interval_minutes"])
            if minutes in MINUTES_TO_INTERVAL:
                current_interval = MINUTES_TO_INTERVAL[minutes]
            else:
                errors.append("الفريم المسموح: 15, 30, 60, 240")

        if "ma20_enabled" in data:
            ma20_enabled = bool(data["ma20_enabled"])

    if errors:
        return jsonify({"error": "؛ ".join(errors)}), 400
    return jsonify({"ok": True})


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
    global _binance_client

    os.makedirs(DATA_DIR, exist_ok=True)   # 📁 تأكد إن مجلد البيانات (Volume) موجود
    log.info(f"📁 مجلد البيانات: {DATA_DIR}")
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    client     = Client(api_key, api_secret)
    _binance_client = client   # 📊 يخلي لوحة التحكم تقدر تستخدم نفس العميل

    load_symbols_from_txt()
    load_trades()
    load_circuit_state()   # 🛑 استرجاع حالة التوقف التلقائي لو موجودة

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

    dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
    dashboard_thread.start()

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
                    f"💼 صفقات مفتوحة: {len(open_trades)}/{MAX_TRADES}\n"
                    f"👁️ يراقب {len(SYMBOLS)} عملة"
                )
                last_heartbeat = now

            # ── 1. إدارة الصفقات المفتوحة ──
            for symbol in list(open_trades.keys()):
                trade = open_trades[symbol]
                try:
                    price = get_current_price(client, symbol)   # ← طلب واحد خفيف (تحسين)
                    if not price:
                        continue
                    coin  = symbol.replace("USDT", "")

                    if not trade["trailing_active"]:
                        if price >= trade["entry_price"] * (1 + TRAIL_ACTIVATE_PCT):
                            trade["trailing_active"] = True
                            trade["highest_price"]   = price
                            trade["stop_loss"]       = round(price * (1 - TRAIL_PCT), 8)
                            log.info(f"🎯 Trailing مفعّل لـ {coin} | ستوب: {trade['stop_loss']}")
                            save_trades()

                    if trade["trailing_active"]:
                        if price > trade["highest_price"]:
                            trade["highest_price"] = price
                            trade["stop_loss"]     = round(price * (1 - TRAIL_PCT), 8)
                            save_trades()
                        elif price <= trade["stop_loss"]:
                            sell_price = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                profit = round((sell_price - trade["entry_price"]) * trade["qty"], 4)
                                record_trade_result(symbol, trade["entry_price"], sell_price, trade["qty"], "trailing_stop")  # ✅ إصلاح #3
                                consecutive_losses = 0   # 🛑 صفقة رابحة → تصفير عدّاد الخسارات المتتالية
                                save_circuit_state()
                                send_telegram(
                                    f"💰 <b>جني أرباح - {coin}</b>\n"
                                    f"💵 دخول: {trade['entry_price']:.4f}$ → خروج: {sell_price:.4f}$\n"
                                    f"💹 PnL: {profit:+.4f} USDT"
                                )
                                del open_trades[symbol]
                                save_trades()
                                continue
                    else:
                        entry_sl = round(trade["entry_price"] * (1 - STOP_LOSS_PCT), 8)
                        if price <= entry_sl:
                            sell_price = sell_market(client, symbol, trade["qty"])
                            if sell_price:
                                loss = round((sell_price - trade["entry_price"]) * trade["qty"], 4)
                                record_trade_result(symbol, trade["entry_price"], sell_price, trade["qty"], "stop_loss")  # ✅ إصلاح #3
                                send_telegram(
                                    f"🚨 <b>ستوب لوز - {coin}</b>\n"
                                    f"📉 السعر: {sell_price:.4f}$\n"
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

            # ── 3. فحص مكثف للمرشحين ───
            with _lock:
                is_trading = trading_enabled

            if watch_list and is_trading and len(open_trades) < MAX_TRADES:
                for symbol in list(watch_list):
                    if symbol in open_trades:
                        watch_list.discard(symbol)
                        continue
                    if len(open_trades) >= MAX_TRADES:
                        break

                    with _lock:
                        ma20_on  = ma20_enabled
                        strategy = current_strategy

                    # ── استراتيجية RSI العادي ──────────────────
                    if strategy == "rsi":
                        ind = get_indicators(client, symbol)
                        if not ind:
                            continue
                        ma20_condition = (ind["price"] > ind["ma20"]) if ma20_on else True
                        buy_signal     = ind["rsi_prev"] < 32 and ind["rsi"] >= RSI_BUY and ma20_condition
                        signal_info    = f"📊 RSI: {ind['rsi_prev']} → {ind['rsi']}" if buy_signal else None
                        price          = ind["price"] if buy_signal else None

                    # ── استراتيجية Stochastic RSI ──────────────
                    elif strategy == "stoch_rsi":
                        stoch = check_stoch_rsi(client, symbol)
                        if not stoch:
                            time.sleep(0.2)
                            continue
                        ma20_condition = (stoch["price"] > stoch["ma20"]) if ma20_on else True
                        buy_signal     = ma20_condition
                        signal_info    = (
                            f"📊 Stoch K: {stoch['k_prev']} → {stoch['k_curr']} | D: {stoch['d_prev']} → {stoch['d_curr']}"
                        ) if buy_signal else None
                        price = stoch["price"] if buy_signal else None

                    else:
                        time.sleep(0.2)
                        continue

                    if buy_signal:
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
                                    f"{signal_info}\n"
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
