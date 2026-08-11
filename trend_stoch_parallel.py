"""
📈 Trend + StochRSI — استراتيجية موازية ومستقلة (نفس منطق الدخول والحماية الحقيقي)
======================================================================================
استراتيجية منفصلة تماماً عن حلقة الفحص الرئيسية بـ crypto_signal_bot.py — بس بتستخدم
بالضبط نفس منطق الدخول (Trend + StochRSI + فوليوم) ونفس نظام الحماية
(ATR Stop Loss + Breakeven + Trailing) يلي البوت الأساسي مستخدمه فعلياً، حتى يكون
سلوك الحماية مطابق ومجرب.

⚠️ استقلالية "المشتركات المتغيّرة" (Shared Mutable State): هالملف ما بيلمس أي من
open_trades / current_strategy / coin_memory (تاريخ العملة يلي بيتحكم بـ Strict Mode)
أو نسخة market_regime المستخدمة بالتبديل التلقائي — عنده صفقته الخاصة وملف حالته
الخاص. بس بستورد دوال رياضية بحتة بلا حالة (indicators.py) وحاسبة الـ ATR/SL بلا حالة
(ATRGuard من coin_memory.py، نسخة جديدة لحاله) — هذول أدوات حساب بحتة، استيرادها آمن
100% ولا يأثر على أي استراتيجية تانية.

المنطق:
--------
1) الدخول (نفس check_trend_stoch بالضبط):
   - StochRSI: K وD كانوا بمنطقة تشبع بيعي (تحت 20)، وK عبر D صعوداً وطلع فوق 20
   - السعر فوق MA20
   - الفوليوم الحالي أعلى من متوسطه (فلتر تأكيد)
   # ⬅️ بطلب المستخدم: شرط "Beta العملة مقابل BTC" أُلغي بالكامل — الاستراتيجية
   # هلق تفحص كل عملة بمعزل تام عن BTC، بدون أي مقارنة بحركته
   لو أكتر من عملة حققت الشروط بنفس دورة الفحص، نختار الأعلى "درجة زخم" (Momentum Score)
   — نفس منطق ترتيب المرشحين بالبوت الأساسي.

2) الحماية (نفس نظام البوت بالضبط):
   - Stop Loss أولي: ATR × مضاعف، مربوط بحالة السوق (BULL/BEAR/SIDEWAYS) عبر
     نفس ATRGuard، بسقف صلب 3% ما ينكسر أبداً
   - Breakeven: عند ربح 0.5%، الستوب ينتقل لسعر الدخول + 0.2% (يغطي العمولة) — أرضية
     ما تنزل تحتها أبداً طول عمر الصفقة
   - Trailing: يتفعّل عند ربح 1%، وبعدها يلاحق أعلى سعر بمسافة ATR × 1.5 (أو نسبة
     ثابتة احتياطية لو ATR غير متاح)

3) صفقة وحدة بس بأي لحظة، بمبلغ ثابت 15 USDT.

الاستخدام: نفس نمط range_trading_btc.py — TrendStochParallel(client, notify_fn=...).run(...)
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
from coin_memory import ATRGuard
from market_regime import MarketRegimeDetector
from portfolio_manager import get_portfolio_manager

_PORTFOLIO_OWNER = "trend_parallel"


DEFAULT_CONFIG = {
    "interval": Client.KLINE_INTERVAL_1HOUR,   # ⬅️ بطلب المستخدم: فريم ساعة
    "symbols": None,   # None = يجيب قائمة عملات USDT Spot النشطة تلقائياً (نفس نطاق البوت)
    "exclude_leveraged": True,   # يستبعد UP/DOWN/BULL/BEAR (توكنز رافعة مالية)

    # ── شروط الدخول (نفس check_trend_stoch بالضبط) ──
    "ma_period": 20,
    "stoch_window": 14, "stoch_smooth1": 3, "stoch_smooth2": 3,
    "stoch_entry_level": 20,
    "volume_ma_length": 20,
    "volume_multiplier": 1.0,
    # ⬅️ بطلب المستخدم: فلتر Beta (مقارنة الزخم بـ BTC) أُلغي بالكامل من شروط الدخول
    "atr_period": 14,

    # ── الحماية (نفس نظام البوت بالضبط) ──
    "atr_multiplier": 2.0,             # مضاعف ATR للستوب الأولي
    "trail_atr_multiplier": 1.5,       # مضاعف ATR لمسافة الـ Trailing
    "trail_activate_atr_multiple": 1.0,     # ⬅️ بدل نسبة ثابتة: تفعيل Trailing عند ربح = 1.0×ATR الحالي
    "breakeven_activate_atr_multiple": 0.5, # ⬅️ بدل نسبة ثابتة: تفعيل Breakeven عند ربح = 0.5×ATR الحالي
    "trail_activate_pct": 0.01,        # احتياطي فقط — يُستخدم لو تعذر حساب ATR
    "breakeven_activate_pct": 0.005,   # احتياطي فقط — يُستخدم لو تعذر حساب ATR
    "breakeven_margin_pct": 0.002,     # 0.2% فوق الدخول (يغطي عمولة بينانس 0.1%×2)
    "stop_loss_fallback_pct": 0.02,    # احتياطي لو ما قدرنا نحسب ATR
    "fallback_trail_pct": 0.01,        # احتياطي Trailing لو ما قدرنا نحسب ATR

    "usdt_per_trade": 15.0,
    "live_trading": False,             # ⚠️ لازم True صراحة لتنفيذ صفقات حقيقية
    "state_file": "trend_stoch_state.json",
    "history_file": "trend_stoch_history.json",
    "scan_pause_seconds": 0.4,         # ⬅️ بطلب المستخدم: كانت 0.15 — بطّأنا الفحص (144 عملة كل ساعة) لتخفيف الضغط على مفتاح API المشترك مع البوت الأساسي
}


# ──────────────────────────────────────────────
# 🔧 تنفيذ شراء/بيع مستقل (نفس نمط range_trading_btc.py — بدون استيراد من bot.py)
# ──────────────────────────────────────────────
def _get_step_size(client, symbol):
    try:
        info = client.get_symbol_info(symbol)
        if not info:
            return None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return float(f["stepSize"])
    except Exception:
        pass
    return None


def _get_quantity(client, symbol, usdt_amount):
    step_size = _get_step_size(client, symbol)
    price = float(client.get_symbol_ticker(symbol=symbol)["price"])
    qty = usdt_amount / price
    if step_size:
        precision = len(format(step_size, ".10f").rstrip("0").split(".")[-1]) if "." in format(step_size, ".10f").rstrip("0") else 0   # ⬅️ إصلاح باگ: str(0.00001) تطلع "1e-05" بدون نقطة، فيصفّر الكمية غلط
        qty = round(qty - (qty % step_size), precision)
    return qty, price


def _buy_market(client, symbol, usdt_amount):
    """يرجع (result_dict, error_message) — نفس نمط range_trading_btc.py."""
    try:
        qty, price = _get_quantity(client, symbol, usdt_amount)
        if qty <= 0:
            return None, f"الكمية المحسوبة صفر أو أقل (السعر: {price}, المبلغ: {usdt_amount})"
        order = client.order_market_buy(symbol=symbol, quantity=qty)
        fills = order.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            total_spent = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            asset = symbol.replace("USDT", "")
            commission_in_asset = sum(
                float(f.get("commission", 0)) for f in fills
                if f.get("commissionAsset") == asset
            )
            net_qty = total_qty - commission_in_asset
            actual_price = total_spent / total_qty if total_qty > 0 else price
            if net_qty <= 0:
                net_qty = qty
        else:
            actual_price = price
            net_qty = qty
        return {"qty": net_qty, "entry_price": actual_price, "order_id": order["orderId"]}, None
    except BinanceAPIException as e:
        return None, f"Binance API error {e.code}: {e.message}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _sell_market(client, symbol, qty):
    """يرجع (price, executed_qty, error_message) — نفس نمط range_trading_btc.py."""
    try:
        asset = symbol.replace("USDT", "")
        balance = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])
        step_size = _get_step_size(client, symbol)
        # ⬅️ بطلب المستخدم: كانت min(qty, actual_qty) — تبيع بس كمية الصفقة المسجّلة
        # وتسيب أي غبار (Dust) متراكم من تقريب LOT_SIZE بصفقات سابقة لنفس العملة.
        # هلق تبيع كامل الرصيد المتاح فعلياً، فينكسح أي غبار قديم مع كل عملية بيع.
        # ⚠️ هذا التغيير بهالملف المستقل بس — bot.py الأساسي ما انلمس.
        sell_qty = actual_qty
        if step_size:
            precision = len(format(step_size, ".10f").rstrip("0").split(".")[-1]) if "." in format(step_size, ".10f").rstrip("0") else 0   # ⬅️ إصلاح باگ: str(0.00001) تطلع "1e-05" بدون نقطة، فيصفّر الكمية غلط
            sell_qty = round(sell_qty - (sell_qty % step_size), precision)
        if sell_qty <= 0:
            return None, None, f"الكمية المتاحة للبيع صفر أو أقل (رصيد {asset}: {actual_qty}, مطلوب: {qty})"
        order = client.order_market_sell(symbol=symbol, quantity=sell_qty)
        fills = order.get("fills", [])
        if fills:
            executed_qty = sum(float(f["qty"]) for f in fills)
            price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / executed_qty
        else:
            executed_qty = sell_qty
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        return price, executed_qty, None
    except BinanceAPIException as e:
        return None, None, f"Binance API error {e.code}: {e.message}"
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"


def _utc_now_iso():
    """نفس آلية utc_now_iso() بـ crypto_signal_bot.py — إصلاح مشكلة عرض GMT بالتطبيق."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _calculate_atr(highs, lows, closes, period):
    try:
        atr_series = ta.volatility.AverageTrueRange(high=highs, low=lows, close=closes, window=period).average_true_range()
        value = atr_series.iloc[-1]
        if pd.isna(value) or value <= 0:
            return None
        return float(value)
    except Exception:
        return None


class TrendStochParallel:
    def __init__(self, client, notify_fn=None, config_fn=None, symbols_fn=None, **config_overrides):
        """
        config_fn: دالة اختيارية بدون معاملات، ترجع dict فيه قيم حية (زي
        atr_multiplier, trail_atr_multiplier, trail_activate_pct, breakeven_activate_pct,
        breakeven_margin_pct) — بتُستدعى بأول كل دورة فحص، فأي تغيير عالإعدادات
        بالتطبيق/تيليغرام (على البوت الأساسي) بينعكس هون فوراً بدون إعادة تشغيل.

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
    # 🟢 فحص إشارة الدخول لعملة وحدة (نفس check_trend_stoch، بدون فلتر Beta)
    # ──────────────────────────────────────────────
    def check_symbol_entry(self, symbol):
        try:
            klines = self.client.get_klines(symbol=symbol, interval=self.cfg["interval"], limit=100)
            if not klines or len(klines) < 30:
                return None
            closes  = pd.Series([float(k[4]) for k in klines])
            highs   = pd.Series([float(k[2]) for k in klines])
            lows    = pd.Series([float(k[3]) for k in klines])
            volumes = pd.Series([float(k[5]) for k in klines])

            stoch = ta.momentum.StochRSIIndicator(
                close=closes, window=self.cfg["stoch_window"],
                smooth1=self.cfg["stoch_smooth1"], smooth2=self.cfg["stoch_smooth2"],
            )
            k_line = stoch.stochrsi_k() * 100
            d_line = stoch.stochrsi_d() * 100

            k_curr, k_prev = round(k_line.iloc[-1], 2), round(k_line.iloc[-2], 2)
            d_curr, d_prev = round(d_line.iloc[-1], 2), round(d_line.iloc[-2], 2)
            price = float(closes.iloc[-1])
            ma20  = round(closes.rolling(window=self.cfg["ma_period"]).mean().iloc[-1], 8)
            level = self.cfg["stoch_entry_level"]

            base_condition = (
                k_prev < level and d_prev < level and
                k_curr >= level and
                k_prev < d_prev and k_curr > d_curr and
                price > ma20
            )
            if not base_condition:
                return None

            vol_ma = volumes.rolling(window=self.cfg["volume_ma_length"]).mean().iloc[-1]
            if pd.isna(vol_ma) or volumes.iloc[-1] <= (vol_ma * self.cfg["volume_multiplier"]):
                return None

            atr_value = _calculate_atr(highs, lows, closes, self.cfg["atr_period"])

            return {
                "symbol": symbol, "price": price, "ma20": ma20,
                "k_curr": k_curr, "d_curr": d_curr, "atr": atr_value,
                "momentum_score": calculate_momentum_score(closes, volumes),
            }
        except BinanceAPIException:
            return None
        except Exception:
            return None

    def scan_for_entry(self):
        """يفحص كل العملات، ويرجع أفضل إشارة (أعلى momentum_score، وغير محجوزة لاستراتيجية تانية) أو None."""
        symbols = self._get_symbols()
        if not symbols:
            return None

        portfolio = get_portfolio_manager()
        best_signal = None
        for symbol in symbols:
            if symbol == "BTCUSDT":   # مستبعدة أصلاً — مغطاة بستراتيجية Range Trading المنفصلة
                continue
            if portfolio.is_claimed_by_other(symbol, _PORTFOLIO_OWNER):
                continue   # عملة محجوزة لاستراتيجية تانية حالياً — نتجاوزها
            signal = self.check_symbol_entry(symbol)
            if signal and (best_signal is None or signal["momentum_score"] > best_signal["momentum_score"]):
                best_signal = signal
            time.sleep(self.cfg["scan_pause_seconds"])
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
            self._notify(f"⚠️ Trend+Stoch: تراجعت عن شراء {symbol.replace('USDT','')} — محجوزة لاستراتيجية تانية حالياً")
            return

        if self.cfg["live_trading"]:
            result, error = _buy_market(self.client, symbol, amount)
            if result is None:
                get_portfolio_manager().release(symbol, _PORTFOLIO_OWNER)   # ما اشترينا فعلياً — نحرر الحجز
                self._notify(f"❌ <b>Trend+Stoch — فشل تنفيذ أمر الشراء لـ {symbol}</b>\n⚠️ السبب: {error}")
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
            "breakeven_floor": None,
            "entry_time": _utc_now_iso(),
            "market_regime_at_entry": market_regime,
        }
        self._save_state()

        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"🟢 <b>Trend+Stoch{mode_tag} — دخول {symbol.replace('USDT','')}</b>\n"
            f"💰 السعر: {entry_price:.6f} | حالة السوق: {market_regime}\n"
            f"🛡️ وقف الخسارة الأولي: {stop_loss:.6f}\n"
            f"💵 المبلغ: {amount} USDT"
        )

    # ──────────────────────────────────────────────
    # 🔄 حساب ستوب الـ Trailing (نفس compute_trail_stop بالضبط)
    # ──────────────────────────────────────────────
    def _refresh_position_atr(self, symbol):
        """⬅️ تحسين 1: يعيد حساب ATR الحالي لعملة الصفقة المفتوحة كل دورة فحص."""
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
        atr_val = self.position.get("atr")
        if atr_val:
            candidate = price - (self.cfg["trail_atr_multiplier"] * atr_val)
            if 0 < candidate < price:
                return round(candidate, 8)
        return round(price * (1 - self.cfg["fallback_trail_pct"]), 8)

    # ──────────────────────────────────────────────
    # 🔍 إدارة الصفقة المفتوحة (Breakeven + Trailing + Stop Loss — نفس منطق البوت بالضبط)
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

        # ⬅️ تحسين 2: نقاط تفعيل Breakeven/Trailing بمضاعف ATR بدل نسبة ثابتة
        if atr_val:
            breakeven_trigger = self.position["entry_price"] + (self.cfg["breakeven_activate_atr_multiple"] * atr_val)
            trail_trigger = self.position["entry_price"] + (self.cfg["trail_activate_atr_multiple"] * atr_val)
        else:
            breakeven_trigger = self.position["entry_price"] * (1 + self.cfg["breakeven_activate_pct"])
            trail_trigger = self.position["entry_price"] * (1 + self.cfg["trail_activate_pct"])

        # 🛡️ Breakeven
        if self.position.get("breakeven_floor") is None:
            if price >= breakeven_trigger:
                floor_price = round(self.position["entry_price"] * (1 + self.cfg["breakeven_margin_pct"]), 8)
                self.position["breakeven_floor"] = floor_price
                if floor_price > self.position["stop_loss"]:
                    self.position["stop_loss"] = floor_price
                self._save_state()

        stop_floor = self.position.get("breakeven_floor") or self.position["entry_price"]

        # 🎯 تفعيل Trailing
        if not self.position["trailing_active"]:
            if price >= trail_trigger:
                self.position["trailing_active"] = True
                self.position["highest_price"] = price
                self.position["stop_loss"] = max(self._compute_trail_stop(price), stop_floor)
                self._save_state()
                self._notify(f"🎯 Trend+Stoch: تفعيل Trailing لـ {symbol.replace('USDT','')} | ستوب: {self.position['stop_loss']}")

        # تحديث/فحص الخروج
        if self.position["trailing_active"]:
            if price > self.position["highest_price"]:
                self.position["highest_price"] = price
                self.position["stop_loss"] = max(self._compute_trail_stop(price), stop_floor)
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
                self._notify(f"❌ <b>Trend+Stoch — فشل تنفيذ أمر البيع لـ {symbol}</b>\nالسبب المحاول: {reason}\n⚠️ الخطأ: {error}")
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

        icon = "✅" if pnl > 0 else "❌"
        reason_map = {"trailing_stop": "Trailing Stop", "stop_loss": "وقف خسارة", "manual_close": "إغلاق يدوي"}
        reason_ar = reason_map.get(reason, reason)
        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"{icon} <b>Trend+Stoch{mode_tag} — خروج {symbol.replace('USDT','')} ({reason_ar})</b>\n"
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
        self._refresh_live_config()   # ⬅️ نتأكد إننا شغالين بآخر إعدادات ATR/Trailing/Breakeven من البوت الأساسي

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
        ⬅️ إصلاح مهم: قبل هيك كانت الحلقة تفحص كل شي (بما فيها صفقة مفتوحة) كل
        poll_seconds (ساعة كاملة) — يعني لو السعر طلع وحقق شرط تفعيل Trailing
        وبعدين رجع نزل، كله ممكن يصير **بين فحصين متتاليين** بدون ما الكود يشوفه
        أصلاً، فيبدو "Trailing ما عم يشتغل" رغم إنه المنطق نفسه سليم.
        هلق: لو فيه صفقة مفتوحة، نفحصها كل position_check_seconds (دقيقة افتراضياً)
        — نفس فكرة حلقة المراقبة بالبوت الأساسي. لو ما فيه صفقة، نفحص إشارة دخول
        جديدة كل poll_seconds (ساعة) بس — منطقي، لأنه هيك أصلاً فريم الشموع.
        """
        mode = "🔴 LIVE (صفقات حقيقية)" if self.cfg["live_trading"] else "🧪 Paper"
        print(f"📈 بدء Trend+Stoch المستقلة — الوضع: {mode} — المبلغ: {self.cfg['usdt_per_trade']} USDT/صفقة — فريم: ساعة")

        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            if is_enabled_fn is None or is_enabled_fn():
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
