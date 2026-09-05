"""
🎯 صياد الزخم — استراتيجية موازية ومستقلة (منطق صياد: زخم + حجم + دفتر أوامر + حيتان)
=====================================================================================
⚠️ ملاحظة تسمية: اسم الملف والكلاس (TrendStochParallel) ضلوا "trend_stoch" لأسباب
توافقية بس (أوامر تيليغرام /set_trend_parallel، مفتاح coin_memory
"trend_stoch_parallel"، ملفات الحالة/التاريخ المحفوظة) — المنطق الفعلي هلق
لا علاقة له إطلاقاً بـ StochRSI أو الترند، صار بالكامل منطق "صياد"
(sayyad_logic.py: زخم + انفجار حجم + ضغط دفتر أوامر + صفقات حيتان + RSI تشبع).
اسم العرض للمستخدم بالتطبيق/تيليغرام هو "صياد الزخم الموازية".

استراتيجية منفصلة تماماً عن حلقة الفحص الرئيسية بـ crypto_signal_bot.py — بس بتستخدم
نفس نظام الحماية (ATR Stop Loss + Trailing + كل تعديلات الربح المضمون وسقف
مسافة التراجع) يلي البوت الأساسي مستخدمه فعلياً، حتى يكون سلوك الحماية مطابق ومجرب.

⚠️ استقلالية "المشتركات المتغيّرة" (Shared Mutable State): هالملف ما بيلمس أي من
open_trades / current_strategy / coin_memory (تاريخ العملة يلي بيتحكم بـ Strict Mode)
أو نسخة market_regime المستخدمة بالتبديل التلقائي — عنده صفقته الخاصة وملف حالته
الخاص. بس بستورد دوال رياضية بحتة بلا حالة (sayyad_logic.py, shared_trading_logic.py)
وحاسبة الـ ATR/SL بلا حالة (ATRGuard من coin_memory.py، نسخة جديدة لحاله) — هذول
أدوات حساب بحتة، استيرادها آمن 100% ولا يأثر على أي استراتيجية تانية.

المنطق:
--------
1) الدخول (منطق صياد بالكامل — فلترة على مرحلتين لتخفيف حمل الـ API):
   - مرحلة خفيفة: زخم آخر 8 شمعات (30 دقيقة) لكل عملات السوق
   - مرحلة ثقيلة (لأفضل max_deep_scan_candidates مرشح بس): درجة صياد =
     وزن الزخم + انفجار الحجم + ضغط دفتر الأوامر + صفقات الحيتان الكبيرة
   - فلاتر رفض احترازي: حركة سعرية متطرفة (24س)، RSI تشبع شرائي، اتساع
     سوق هابط عام (compute_market_breadth)، وBTC بترند هابط واضح (BEAR)
   لو أكتر من عملة حققت الشروط بنفس دورة الفحص، نختار الأعلى درجة صياد (momentum_score).

2) الحماية (نفس نظام البوت بالضبط):
   - Stop Loss أولي: ATR × مضاعف، مربوط بحالة السوق (BULL/BEAR/SIDEWAYS) عبر
     نفس ATRGuard، بسقف صلب 3% ما ينكسر أبداً
   - Trailing: عتبة تفعيل مقيّدة بسقف أقصى (trail_trigger_max_pct)، مسافة
     تراجع مقيّدة بسقف نسبي (trail_distance_max_pct)، وربح أدنى مضمون بعد
     التفعيل (min_profit_lock_pct) — كل هذول محسوبين عبر shared_trading_logic.py

3) صفقة وحدة بس بأي لحظة، بمبلغ ثابت 20 USDT.

الاستخدام: TrendStochParallel(client, notify_fn=...).run(...)
"""

import time
import datetime
import json
import os
import pandas as pd
import ta
from binance.client import Client
from binance.exceptions import BinanceAPIException

from indicators import calculate_bollinger_bands, calculate_vwap, calculate_momentum_score
from coin_memory import ATRGuard, CoinMemory
from market_regime import MarketRegimeDetector
from portfolio_manager import get_portfolio_manager
import sayyad_logic
import shared_trading_logic as trading

_PORTFOLIO_OWNER = "trend_parallel"


DEFAULT_CONFIG = {
    "interval": Client.KLINE_INTERVAL_30MINUTE,   # منطق صياد: شمعة 30 دقيقة
    "kline_lookback": 48,                          # 48 شمعة × 30 دقيقة = 24 ساعة (نظرة صياد)
    "symbols": None,   # None = يجيب قائمة عملات USDT Spot النشطة تلقائياً (نفس نطاق البوت)
    "exclude_leveraged": True,   # يستبعد UP/DOWN/BULL/BEAR (توكنز رافعة مالية)

    # ── شروط الدخول (نفس check_trend_stoch بالضبط) ──
    "ma_period": 20,
    "stoch_window": 14, "stoch_smooth1": 3, "stoch_smooth2": 3,
    "stoch_entry_level": 20,
    "volume_ma_length": 20,
    "volume_multiplier": 1.0,
    # ⬅️ بطلب المستخدم: فلتر Beta (مقارنة الزخم بـ BTC) أُلغي بالكامل من شروط الدخول
    "atr_enabled": True,   # ⬅️ بطلب المستخدم: زر تشغيل/إيقاف ATR — ينعكس من إعدادات البوت الأساسي عبر config_fn
    "atr_period": 14,

    # ── الحماية (نفس نظام البوت بالضبط) ──
    "atr_multiplier": 2.0,             # مضاعف ATR للستوب الأولي
    "trail_atr_multiplier": 1.5,       # مضاعف ATR لمسافة الـ Trailing
    "trail_activate_atr_multiple": 1.0,     # ⬅️ بدل نسبة ثابتة: تفعيل Trailing عند ربح = 1.0×ATR الحالي
    "trail_activate_pct": 0.01,        # احتياطي فقط — يُستخدم لو تعذر حساب ATR
    "trail_trigger_max_pct": 3.0,      # ⬅️ إصلاح: سقف أقصى (%) لنسبة الربح المطلوبة لتفعيل Trailing — يمنع
                                        # عملة متقلبة (ATR خام كبير) من طلب ربح ضخم لتفعيل الحماية، رغم إن
                                        # الـ Stop Loss النازل مقيّد بسقف أضيق بكثير (atr_multiplier + الحد الصلب)
    "min_profit_lock_pct": 0.80,       # ⬅️ بطلب المستخدم: أول ما Trailing يتفعّل، الستوب ما ينزل تحت
                                        # (سعر الدخول + هالنسبة) — ربح مضمون على الأقل، مش مجرد Breakeven
    "trail_distance_max_pct": 2.0,     # ⬅️ رُفع من 1.0 لـ2.0 (بطلب المستخدم) — سقف أقصى لمسافة تراجع Trailing عن القمة، بغض
                                        # النظر عن ATR — يمنع عملة متقلبة من "أكل" ربح كبير بمسافة واسعة
    "stop_loss_fallback_pct": 0.02,    # احتياطي لو ما قدرنا نحسب ATR
    "fallback_trail_pct": 0.01,        # احتياطي Trailing لو ما قدرنا نحسب ATR

    "usdt_per_trade": 20.0,
    "live_trading": False,             # ⚠️ لازم True صراحة لتنفيذ صفقات حقيقية
    "state_file": "trend_stoch_state.json",
    "history_file": "trend_stoch_history.json",
    "coin_memory_db_path": "coin_memory.db",   # ⬅️ نفس قاعدة الذاكرة الموحّدة يلي البوت الأساسي يستخدمها
    "scan_pause_seconds": 0.4,         # ⬅️ بطلب المستخدم: كانت 0.15 — بطّأنا الفحص (144 عملة كل ساعة) لتخفيف الضغط على مفتاح API المشترك مع البوت الأساسي
    "max_deep_scan_candidates": 8,      # ⬅️ إصلاح: بعد فلترة الزخم الخفيفة على كل السوق، بس أفضل هالعدد
                                         # من المرشحين بياخدوا الفحص الثقيل (دفتر أوامر + حيتان) — يمنع
                                         # مئات الاستدعاءات الثقيلة كل دورة فحص (كل 5 دقايق هلق بدل ساعة)
}


# ──────────────────────────────────────────────
# 🔧 تنفيذ شراء/بيع/ATR — مستوردة من shared_trading_logic.py (بدل التكرار
# اليدوي بالثلاث ملفات الموازية — أي تعديل مستقبلي هلق بمكان واحد بس)
# ──────────────────────────────────────────────
_buy_market = trading.buy_market
_sell_market = trading.sell_market
_calculate_atr = trading.calculate_atr
_utc_now_iso = trading.utc_now_iso


class TrendStochParallel:
    def __init__(self, client, notify_fn=None, config_fn=None, symbols_fn=None, **config_overrides):
        """
        config_fn: دالة اختيارية بدون معاملات، ترجع dict فيه قيم حية (زي
        atr_multiplier, trail_atr_multiplier, trail_activate_pct) — بتُستدعى
        بأول كل دورة فحص، فأي تغيير عالإعدادات بالتطبيق/تيليغرام (على البوت
        الأساسي) بينعكس هون فوراً بدون إعادة تشغيل.

        symbols_fn: دالة اختيارية بدون معاملات، ترجع القائمة الحية لعملات البوت
        الأساسي (SYMBOLS، الـ144 عملة المختارة) — بتُستدعى بكل فحص، حتى الاستراتيجية
        تفحص **نفس** سلة العملات المعتمدة بالبوت، مش كل أزواج USDT الموجودة ببينانس
        (مئات العملات، فيها ضعيفة/غير مدروسة). لو ما انمررت (تشغيل مستقل للاختبار)،
        بترجع تلقائياً لجلب كل أزواج USDT من بينانس مباشرة.
        """
        self.client = client
        self.notify_fn = notify_fn
        self.config_fn = config_fn
        self.symbols_fn = symbols_fn
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(config_overrides)

        self.atr_guard = ATRGuard()                    # نسخة خاصة، بدون أي علاقة بنسخة bot.py
        self.coin_memory = CoinMemory(self.cfg["coin_memory_db_path"])   # ⬅️ نفس قاعدة الذاكرة الموحّدة (كتابة بس هون)
        self.regime_detector = MarketRegimeDetector(client)   # نفس الشي — نسخة خاصة لحالها

        self.position = None   # صفقة وحدة بس بأي لحظة
        self._load_state()

        self._symbols_cache = None
        self._symbols_cache_time = 0

    def _notify(self, text):
        if self.notify_fn:
            try:
                self.notify_fn(text)
            except Exception:
                pass

    # ──────────────────────────────────────────────
    # 💾 حفظ/استرجاع الحالة
    # ──────────────────────────────────────────────
    def _load_state(self):
        path = self.cfg["state_file"]
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.position = json.load(f)
            except Exception:
                self.position = None
        if self.position is not None:
            get_portfolio_manager().try_claim(self.position["symbol"], _PORTFOLIO_OWNER)

    def _save_state(self):
        path = self.cfg["state_file"]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.position, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _log_history(self, record):
        path = self.cfg["history_file"]
        history = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.append(record)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # 🌐 قائمة العملات (نفس نطاق البوت — USDT Spot النشطة، بدون توكنز رافعة مالية)
    # ──────────────────────────────────────────────
    def _get_symbols(self):
        if self.symbols_fn is not None:
            try:
                live_symbols = self.symbols_fn()
                if live_symbols:
                    return list(live_symbols)
            except Exception:
                pass   # فشل الجلب الحي — نكمل على الاحتياطي بالأسفل

        if self.cfg["symbols"]:
            return self.cfg["symbols"]

        # نكاش القائمة لمدة ساعة (ما تتغير كتير) — تقليل ضغط API
        if self._symbols_cache and (time.time() - self._symbols_cache_time) < 3600:
            return self._symbols_cache

        try:
            info = self.client.get_exchange_info()
            symbols = []
            for s in info["symbols"]:
                if (s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
                        and s.get("isSpotTradingAllowed", True)):
                    base = s["baseAsset"]
                    if self.cfg["exclude_leveraged"] and any(tag in base for tag in ("UP", "DOWN", "BULL", "BEAR")):
                        continue
                    symbols.append(s["symbol"])
            self._symbols_cache = symbols
            self._symbols_cache_time = time.time()
            return symbols
        except Exception:
            return self._symbols_cache or []

    # ──────────────────────────────────────────────
    # 🟢 فحص إشارة الدخول لعملة وحدة (منطق صياد: زخم + حجم + دفتر أوامر + حيتان)
    # ──────────────────────────────────────────────
    def check_symbol_momentum(self, symbol):
        """
        🟢 المرحلة الأولى (خفيفة): استدعاء API وحيد (klines) بس — تحسب
        الزخم الأولي وترجع (klines, momentum) خام بدون أي استدعاء ثقيل
        (دفتر أوامر / صفقات حيتان). تُستخدم لفلترة كل عملات السوق بسرعة
        قبل ما نصرف استدعاءات ثقيلة إلا على أفضل مرشحين بس.
        """
        try:
            klines_raw = self.client.get_klines(
                symbol=symbol, interval=self.cfg["interval"], limit=self.cfg["kline_lookback"] + 1
            )
            if len(klines_raw) < sayyad_logic.MOMENTUM_WINDOW_CANDLES + 2:
                return None
            klines = klines_raw[:-1]  # نتجاهل الشمعة الجارية بالحسابات

            momentum = sayyad_logic.compute_momentum(klines)
            if momentum is None or momentum["price_change_pct"] <= 0:
                return None

            return {"symbol": symbol, "klines": klines, "momentum": momentum}
        except BinanceAPIException:
            return None
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # 🟢 فحص إشارة الدخول لعملة وحدة (منطق صياد: زخم + حجم + دفتر أوامر + حيتان)
    # ──────────────────────────────────────────────
    def check_symbol_entry(self, symbol, klines=None, momentum=None):
        """
        🔴 المرحلة الثانية (ثقيلة): دفتر الأوامر + صفقات الحيتان — تُستدعى
        بس لأفضل مرشحين (max_deep_scan_candidates) بعد فلترة الزخم الأولية،
        مش لكل عملة عندها زخم إيجابي. لو klines/momentum غير ممررة (استخدام
        مباشر لعملة وحدة)، بتحسبهم من الصفر بنفسها.
        """
        try:
            if klines is None or momentum is None:
                pre = self.check_symbol_momentum(symbol)
                if pre is None:
                    return None
                klines, momentum = pre["klines"], pre["momentum"]

            ob_imbalance = sayyad_logic.compute_order_book_imbalance(self.client, symbol)
            whale_data = sayyad_logic.detect_whale_trades(self.client, symbol)
            score, breakdown = sayyad_logic.compute_score(momentum, ob_imbalance, whale_data)

            if score < sayyad_logic.MIN_SCORE_TO_ACCEPT:
                return None
            if sayyad_logic.is_extreme_move(breakdown):
                return None

            closes_list = [float(k[4]) for k in klines]
            rsi_value = sayyad_logic.compute_rsi(closes_list, period=14)
            if sayyad_logic.is_overbought(rsi_value):
                return None

            closes = pd.Series(closes_list)
            highs = pd.Series([float(k[2]) for k in klines])
            lows = pd.Series([float(k[3]) for k in klines])
            price = float(closes.iloc[-1])
            atr_value = _calculate_atr(highs, lows, closes, self.cfg["atr_period"]) if self.cfg.get("atr_enabled", True) else None

            return {
                "symbol": symbol, "price": price, "atr": atr_value,
                "momentum_score": score,
                "sayyad_breakdown": breakdown,
            }
        except BinanceAPIException:
            return None
        except Exception:
            return None

    def scan_for_entry(self):
        """يفحص عملات قائمة SYMBOLS المشتركة (نفس قائمة كل الاستراتيجيات)، ويرجع أفضل إشارة (أعلى momentum_score، وغير محجوزة لاستراتيجية تانية) أو None."""
        # تصحيح: نستخدم self._get_symbols() الأصلية (نفس قائمة SYMBOLS
        # المشتركة يلي Squeeze Breakout وMean Reversion بيستخدموها) بدل
        # قائمة صياد المنفصلة — حتى الاستراتيجيات الثلاث تضل متّسقة على
        # نفس مصدر واحد قابل للتعديل من إعدادات التطبيق
        symbols = self._get_symbols()
        if not symbols:
            return None

        # فحص اتساع السوق أول شي — طلب واحد بس لكل دورة، على نفس قائمة
        # SYMBOLS المشتركة. لو أغلب السوق نازل (24 ساعة)، نوقف كل محاولة
        # دخول هالدورة بغض النظر عن قوة أي إشارة فردية — تجنّب "السباحة
        # عكس التيار" (زي صفقة ENSO يلي خسرت بيوم كان السوق أحمر بشكل عام).
        breadth = sayyad_logic.compute_market_breadth(self.client, symbols)
        if breadth and breadth["is_bearish"]:
            return None

        # فحص إضافي: BTC نفسه بترند هابط واضح (BEAR) — وقف احترازي مستقل
        # عن اتساع السوق (ممكن BTC يكون هابط بوضوح رغم إن الألتكوينز لسا
        # ما انعكس أثرها بشكل واسع بعد).
        if self.regime_detector.get_regime_label() == "BEAR":
            return None

        portfolio = get_portfolio_manager()

        # ⬅️ إصلاح (حمل API): المرحلة الأولى — فحص خفيف (klines بس) لكل
        # عملات السوق. المرحلة الثانية الثقيلة (دفتر أوامر + حيتان) ما
        # بتصير إلا لأفضل عدد محدود من المرشحين (max_deep_scan_candidates)،
        # مش لكل عملة عندها زخم إيجابي — كان هذا يولّد مئات الاستدعاءات
        # الثقيلة كل 5 دقايق على مفتاح API مشترك مع 3 استراتيجيات تانية.
        momentum_candidates = []
        for symbol in symbols:
            if symbol == "BTCUSDT":   # مستبعدة من هالاستراتيجية دائماً (BTC ما إلها استراتيجية موازية مخصصة حالياً)
                continue
            if portfolio.is_claimed_by_other(symbol, _PORTFOLIO_OWNER):
                continue   # عملة محجوزة لاستراتيجية تانية حالياً — نتجاوزها
            pre = self.check_symbol_momentum(symbol)
            if pre is not None:
                momentum_candidates.append(pre)
            time.sleep(self.cfg["scan_pause_seconds"])

        if not momentum_candidates:
            return None

        # نرتب تنازلياً بقوة الزخم (% التغيّر) ونكتفي بأفضل عدد محدود
        # للمرحلة الثقيلة — الفلترة السريعة الأولى كافية لاستبعاد الضعيف
        momentum_candidates.sort(key=lambda c: c["momentum"]["price_change_pct"], reverse=True)
        top_candidates = momentum_candidates[: self.cfg["max_deep_scan_candidates"]]

        best_signal = None
        for cand in top_candidates:
            signal = self.check_symbol_entry(cand["symbol"], klines=cand["klines"], momentum=cand["momentum"])
            if signal and (best_signal is None or signal["momentum_score"] > best_signal["momentum_score"]):
                best_signal = signal
        return best_signal

    # ──────────────────────────────────────────────
    # ⚡ تنفيذ الشراء (مع نفس نظام حساب الستوب الأولي بالضبط)
    # ──────────────────────────────────────────────
    def _execute_buy(self, signal):
        symbol = signal["symbol"]
        amount = self.cfg["usdt_per_trade"]

        # 🔐 حجز أخير قبل الشراء الفعلي — لو استراتيجية تانية حجزت نفس العملة
        # بالفترة يلي بين scan_for_entry والتنفيذ، نتراجع فوراً بدل ما نشتري.
        if not get_portfolio_manager().try_claim(symbol, _PORTFOLIO_OWNER):
            self._notify(f"⚠️ صياد الزخم: تراجعت عن شراء {symbol.replace('USDT','')} — محجوزة لاستراتيجية تانية حالياً")
            return

        if self.cfg["live_trading"]:
            result, error = _buy_market(self.client, symbol, amount)
            if result is None:
                get_portfolio_manager().release(symbol, _PORTFOLIO_OWNER)   # ما اشترينا فعلياً — نحرر الحجز
                self._notify(f"❌ <b>صياد الزخم — فشل تنفيذ أمر الشراء لـ {symbol}</b>\n⚠️ السبب: {error}")
                return
            entry_price, qty = result["entry_price"], result["qty"]
        else:
            entry_price = signal["price"]
            qty = round(amount / entry_price, 6)

        # 📐 نفس صمام أمان ATR/SL بالضبط (ATRGuard، مربوط بحالة السوق، strict_mode=False
        # دائماً هون — ما بنربط هالاستراتيجية بتاريخ العملة بذاكرة البوت الأساسي)
        market_regime = self.regime_detector.get_regime_label()
        atr_value = signal.get("atr")
        if atr_value:
            sl_info = self.atr_guard.compute_stop_loss(
                entry_price=entry_price,
                raw_atr_value=atr_value,
                market_regime=market_regime,
                strict_mode=False,
                atr_sl_multiple=self.cfg["atr_multiplier"],
                strict_cap_pct=self.cfg["stop_loss_fallback_pct"] * 100,
            )
            stop_loss = sl_info["sl_price"]
        else:
            stop_loss = entry_price * (1 - self.cfg["stop_loss_fallback_pct"])

        self.position = {
            "symbol": symbol,
            "entry_price": entry_price,
            "qty": qty,
            "atr": atr_value,
            "stop_loss": round(stop_loss, 8),
            "trailing_active": False,
            "highest_price": entry_price,
            "entry_time": _utc_now_iso(),
            "market_regime_at_entry": market_regime,
        }
        self._save_state()

        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"🟢 <b>صياد الزخم{mode_tag} — دخول {symbol.replace('USDT','')}</b>\n"
            f"💰 السعر: {entry_price:.6f} | حالة السوق: {market_regime}\n"
            f"🛡️ وقف الخسارة الأولي: {stop_loss:.6f}\n"
            f"💵 المبلغ: {amount} USDT"
        )

    # ──────────────────────────────────────────────
    # 🔄 حساب ستوب الـ Trailing (نفس compute_trail_stop بالضبط)
    # ──────────────────────────────────────────────
    def _refresh_position_atr(self, symbol):
        """⬅️ تحسين 1: يعيد حساب ATR الحالي لعملة الصفقة المفتوحة كل دورة فحص."""
        # ⬅️ بطلب المستخدم: لو زر ATR مطفي، لازم نمسح أي قيمة ATR قديمة محفوظة
        # بالصفقة صراحة (مش بس نوقف حسابها) — وإلا الصفقة بتضل تستخدم قيمة
        # عالقة من قبل التطفية، وما بترجع فعلياً للقيم الاحتياطية الثابتة.
        if not self.cfg.get("atr_enabled", True):
            self.position["atr"] = None
            return None
        try:
            klines = self.client.get_klines(
                symbol=symbol, interval=self.cfg["interval"],
                limit=self.cfg["atr_period"] + 10,
            )
            if not klines or len(klines) < self.cfg["atr_period"] + 2:
                return None
            highs = pd.Series([float(k[2]) for k in klines])
            lows = pd.Series([float(k[3]) for k in klines])
            closes = pd.Series([float(k[4]) for k in klines])
            atr_value = _calculate_atr(highs, lows, closes, self.cfg["atr_period"])
            if atr_value:
                self.position["atr"] = atr_value
                self._save_state()
            return atr_value
        except Exception:
            return None

    def _compute_trail_stop(self, price):
        """
        🔒 محسوبة عبر shared_trading_logic.compute_trail_stop() — نفس المنطق
        (سقف مسافة التراجع trail_distance_max_pct + Minimum Profit Lock)
        مشترك مع باقي الاستراتيجيات الموازية، بمكان واحد بدل التكرار.
        """
        atr_val = self.position.get("atr")
        entry_price = self.position["entry_price"]
        return trading.compute_trail_stop(price, atr_val, entry_price, self.cfg)

    # ──────────────────────────────────────────────
    # 🔍 إدارة الصفقة المفتوحة (Trailing + Stop Loss — نفس منطق البوت بالضبط)
    # ──────────────────────────────────────────────
    def manage_open_position(self):
        symbol = self.position["symbol"]
        try:
            price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            return

        # ⬅️ تحسين 1: إعادة حساب ATR كل دورة فحص بدل ما يضل مجمّد على قيمة الشراء
        fresh_atr = self._refresh_position_atr(symbol)
        atr_val = fresh_atr if fresh_atr else self.position.get("atr")

        # ⬅️ نقطة تفعيل Trailing — محسوبة عبر shared_trading_logic.compute_trail_trigger()
        trail_trigger = trading.compute_trail_trigger(self.position["entry_price"], atr_val, self.cfg)

        # 🎯 تفعيل Trailing
        if not self.position["trailing_active"]:
            if price >= trail_trigger:
                self.position["trailing_active"] = True
                self.position["highest_price"] = price
                self.position["stop_loss"] = self._compute_trail_stop(price)
                self._save_state()
                self._notify(f"🎯 صياد الزخم: تفعيل Trailing لـ {symbol.replace('USDT','')} | ستوب: {self.position['stop_loss']}")

        # تحديث/فحص الخروج
        if self.position["trailing_active"]:
            if price > self.position["highest_price"]:
                self.position["highest_price"] = price
                self.position["stop_loss"] = self._compute_trail_stop(price)
                self._save_state()
            elif price <= self.position["stop_loss"]:
                self._execute_sell(price, "trailing_stop")
        else:
            if price <= self.position["stop_loss"]:
                self._execute_sell(price, "stop_loss")

    def close_manually(self):
        """
        إغلاق يدوي (من التطبيق/API) — يبيع فوراً بالسعر الحالي بغض النظر عن
        الستوب أو الترايلنك. يرجع (sell_price, status).
        """
        if self.position is None:
            return None, "not_found"
        symbol = self.position["symbol"]
        try:
            current_price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            return None, "price_fetch_failed"

        self._execute_sell(current_price, "manual_close")
        if self.position is not None:
            return None, "sell_failed"
        return current_price, "ok"

    def _execute_sell(self, current_price, reason):
        symbol = self.position["symbol"]
        entry_price = self.position["entry_price"]
        qty = self.position["qty"]

        if self.cfg["live_trading"]:
            exit_price, executed_qty, error = _sell_market(self.client, symbol, qty)
            if exit_price is None:
                self._notify(f"❌ <b>صياد الزخم — فشل تنفيذ أمر البيع لـ {symbol}</b>\nالسبب المحاول: {reason}\n⚠️ الخطأ: {error}")
                return
            final_qty = executed_qty
        else:
            exit_price = current_price
            final_qty = qty

        pnl = round((exit_price - entry_price) * final_qty, 4)
        pnl_pct = round((exit_price - entry_price) / entry_price * 100, 3)

        self._log_history({
            "symbol": symbol, "entry_price": entry_price, "exit_price": exit_price,
            "qty": final_qty, "pnl": pnl, "pnl_pct": pnl_pct, "reason": reason,
            "entry_time": self.position.get("entry_time"),
            "exit_time": _utc_now_iso(),
            "live": self.cfg["live_trading"],
        })

        try:
            self.coin_memory.record_trade(
                symbol=symbol, is_win=(pnl > 0), pnl=pnl,
                slippage_pct=0.0, strategy="trend_stoch_parallel",
            )
        except Exception:
            pass

        icon = "✅" if pnl > 0 else "❌"
        reason_map = {"trailing_stop": "Trailing Stop", "stop_loss": "وقف خسارة", "manual_close": "إغلاق يدوي"}
        reason_ar = reason_map.get(reason, reason)
        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"{icon} <b>صياد الزخم{mode_tag} — خروج {symbol.replace('USDT','')} ({reason_ar})</b>\n"
            f"💰 دخول: {entry_price:.6f} → خروج: {exit_price:.6f}\n"
            f"📊 PnL: {pnl:+.4f} USDT ({pnl_pct:+.3f}%)"
        )

        self.position = None
        get_portfolio_manager().release(symbol, _PORTFOLIO_OWNER)   # 🔐 نحرر الحجز — العملة صارت متاحة لأي استراتيجية تانية
        self._save_state()

    def _refresh_live_config(self):
        """يجيب أحدث قيم من البوت الأساسي (لو config_fn متوفرة) ويحدّث self.cfg بيهم."""
        if self.config_fn is None:
            return
        try:
            live_values = self.config_fn()
            if live_values:
                self.cfg.update(live_values)
        except Exception:
            pass   # لو فشل الجلب لأي سبب، نكمل بآخر قيم معروفة بدل ما نوقف الاستراتيجية

    # ──────────────────────────────────────────────
    # 🔄 دورة فحص/تنفيذ واحدة
    # ──────────────────────────────────────────────
    def check_and_act(self):
        self._refresh_live_config()   # ⬅️ نتأكد إننا شغالين بآخر إعدادات ATR/Trailing من البوت الأساسي

        if self.position is not None:
            self.manage_open_position()
            return {"status": "holding" if self.position else "exited"}

        signal = self.scan_for_entry()
        if signal is not None:
            self._execute_buy(signal)
            return {"status": "entered", "signal": signal}
        return {"status": "waiting_for_entry"}

    def run(self, poll_seconds=3600, position_check_seconds=60, max_iterations=None, is_enabled_fn=None):
        """
        ⬅️ إصلاح مهم 1: قبل هيك كانت الحلقة تفحص كل شي (بما فيها صفقة مفتوحة) كل
        poll_seconds (ساعة كاملة) — يعني لو السعر طلع وحقق شرط تفعيل Trailing
        وبعدين رجع نزل، كله ممكن يصير **بين فحصين متتاليين** بدون ما الكود يشوفه
        أصلاً، فيبدو "Trailing ما عم يشتغل" رغم إنه المنطق نفسه سليم.
        هلق: لو فيه صفقة مفتوحة، نفحصها كل position_check_seconds (دقيقة افتراضياً)
        — نفس فكرة حلقة المراقبة بالبوت الأساسي. لو ما فيه صفقة، نفحص إشارة دخول
        جديدة كل poll_seconds (ساعة) بس — منطقي، لأنه هيك أصلاً فريم الشموع.

        ⬅️ إصلاح مهم 2 (باگ حقيقي): لو الاستراتيجية موقوفة وعندها صفقة مفتوحة،
        كان الكود يوقف check_and_act() بالكامل — الصفقة تضل بدون مراقبة (لا ستوب
        لوز، لا Trailing) لحد ما ترجع تتفعّل. هلق إدارة الصفقة المفتوحة شغالة
        دايماً بغض النظر عن حالة التفعيل — الإيقاف بس بيمنع البحث عن صفقات جديدة.
        """
        mode = "🔴 LIVE (صفقات حقيقية)" if self.cfg["live_trading"] else "🧪 Paper"
        print(f"📈 بدء صياد الزخم المستقلة — الوضع: {mode} — المبلغ: {self.cfg['usdt_per_trade']} USDT/صفقة — فريم: 30 دقيقة")

        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            has_open_position = self.position is not None
            is_enabled = is_enabled_fn is None or is_enabled_fn()

            if has_open_position or is_enabled:
                try:
                    self.check_and_act()
                except Exception as e:
                    print(f"❌ خطأ بدورة الفحص: {e}")
                sleep_time = position_check_seconds if self.position is not None else poll_seconds
            else:
                sleep_time = 10
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                time.sleep(sleep_time)


if __name__ == "__main__":
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    client = Client(api_key, api_secret)

    strategy = TrendStochParallel(client, usdt_per_trade=15.0, live_trading=False)
    print("🔍 فحص فوري لأفضل إشارة دخول حالياً (قد يأخذ دقيقة لكل العملات)...")
    signal = strategy.scan_for_entry()
    if signal:
        print(f"✅ إشارة: {signal['symbol']} @ {signal['price']} | Score: {signal['momentum_score']}")
    else:
        print("⚠️ مافي إشارة دخول حالياً")
