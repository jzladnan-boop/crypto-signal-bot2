"""
🔻 Mean Reversion Parallel — استراتيجية موازية ومستقلة (ارتداد من تشبع بيعي)
====================================================================================
استراتيجية منفصلة تماماً وعكس فكرة Breakout: بدل ما تلاحق عملة طالعة بقوة،
هاي بتشتري عملة نزلت كتير بسرعة وبعيدة عن قيمتها الطبيعية، على أساس إنها رح
ترتد لفوق (Oversold Bounce) — بس بحذر شديد، لأن "شراء نزول" أخطر بطبيعته من
"شراء صعود" (خطر السكين الساقطة / Falling Knife).

⚠️ عشان هيك هاي الاستراتيجية محملة بطبقات حماية إضافية ما موجودة بباقي
الاستراتيجيات الموازية:

1) تشبع بيعي مزدوج: RSI تحت المستوى المطلوب + السعر تحت/قريب من الحد السفلي
   لبولينجر — مو مؤشر واحد لحاله.

2) تأكيد ارتداد فعلي (مو توقّع): الشمعة المغلقة لازم تكون صاعدة (Close > Open)
   وأعلى من إغلاق الشمعة يلي قبلها — يعني الارتداد بدأ فعلياً، مش بس متوقع.

3) فحص فوليوم البيع: متوسط فوليوم الشموع الهابطة الأخيرة لازم يكون أخف من
   متوسط الشموع يلي قبلها (يعني ضغط البيع عم يخف/يستهلك حاله) — إشارة على
   احتمال انعكاس حقيقي، مش استمرار تصفية.

4) فلتر ضد "السكين الساقطة": ما نشتري لو السعر بعيد كتير تحت المتوسط المتحرك
   الطويل (نزول حاد ومستمر = ترند هابط حقيقي، مش تصحيح مؤقت).

5) فلتر حالة BTC العام: وقت BTC هابط بقوة (BEAR)، نشترط إضافي إن العملة
   مصنّفة INVERSE_STRENGTH بذاكرة العملات (coin_memory) — يعني عندها سجل
   فعلي إنها تقاوم/تبرز وقت هبوط BTC، مش أي عملة عشوائية.

6) الحماية بعد الشراء (ATR Stop Loss + Trailing): مربوطة بالكامل بإعدادات
   البوت الأساسي الحية (config_fn) — نفس مضاعف ATR، نفس نقطة تفعيل الـ
   Trailing، ونفس السقف الاحتياطي — بدل إعدادات مستقلة أضيق. نفس مستوى
   المخاطرة متل باقي الاستراتيجيات الموازية؛ الحماية الإضافية الحقيقية هون
   جايّة من طبقات الفحص الخمسة اللي فوق (1-5)، مش من تضييق الستوب لحاله.

⚠️ استقلالية "المشتركات المتغيّرة": نفس مبدأ باقي الاستراتيجيات الموازية —
PortfolioManager لمنع تضارب الشراء، ATRGuard/MarketRegimeDetector/CoinMemory
كأدوات حساب/قراءة بلا حالة خاصة فينا، config_fn/symbols_fn كحقن تبعيات.
"""

import time
import json
import os
import datetime
import pandas as pd
import ta
from binance.client import Client
from binance.exceptions import BinanceAPIException

from coin_memory import ATRGuard, CoinMemory, CorrelationType
from market_regime import MarketRegimeDetector
from portfolio_manager import get_portfolio_manager
import sayyad_logic
import shared_trading_logic as trading

_PORTFOLIO_OWNER = "mean_reversion_parallel"


DEFAULT_CONFIG = {
    "interval": Client.KLINE_INTERVAL_30MINUTE,
    "symbols": None,   # None = يجيب قائمة عملات USDT Spot النشطة تلقائياً (احتياطي لو ما انمررت symbols_fn)

    # ── التشبع البيعي ──
    "rsi_period": 14,
    "rsi_oversold_level": 30,        # RSI لازم يكون تحت هالمستوى
    "bb_period": 20,
    "bb_std_dev": 2.0,
    "bb_lower_margin_pct": 1.0,      # السعر مسموح يكون لغاية 1% فوق الحد السفلي لسا يُعتبر "قريب كفاية"

    # ── فحص فوليوم البيع (تأكيد استهلاك ضغط البيع) ──
    "sell_volume_recent_window": 3,   # متوسط فوليوم آخر 3 شموع هابطة قبل الارتداد
    "sell_volume_prior_window": 6,    # مقارنة مع متوسط الـ 6 شموع يلي قبلها

    # ── فلتر ضد السكين الساقطة ──
    "trend_ma_period": 100,           # متوسط متحرك طويل (على نفس فريم الدخول) لتحديد "ترند هابط حقيقي"
    "max_downtrend_pct": 15.0,        # ما نشتري لو السعر تحت هالمتوسط بأكتر من 15%

    # ── فلتر حالة BTC العام (BEAR) ──
    "require_inverse_strength_in_bear": True,  # وقت BTC هابط بقوة، نشترط تصنيف INVERSE_STRENGTH بذاكرة العملات

    # ── الحماية بعد الشراء (أضيق من العادي — منطقة ضعف) ──
    "atr_enabled": True,   # ⬅️ بطلب المستخدم: زر تشغيل/إيقاف ATR — ينعكس من إعدادات البوت الأساسي عبر config_fn
    "atr_period": 14,
    "atr_multiplier": 1.3,            # ⬅️ أضيق من باقي الاستراتيجيات (كانت 2.0) — تقبّل أقل، بمنطقة ضعف
    "trail_atr_multiplier": 1.2,      # ⬅️ أضيق كمان
    "trail_activate_atr_multiple": 0.8,   # ⬅️ تفعيل Trailing أبكر (نقفل الربح بسرعة أكبر)
    "trail_activate_pct": 0.008,      # احتياطي فقط — يُستخدم لو تعذر حساب ATR
    "trail_trigger_max_pct": 3.0,     # ⬅️ إصلاح: سقف أقصى (%) لنسبة الربح المطلوبة لتفعيل Trailing
    "min_profit_lock_pct": 0.80,      # ⬅️ بطلب المستخدم: ربح مضمون بعد تفعيل Trailing، مش مجرد Breakeven
    "trail_distance_max_pct": 2.0,    # ⬅️ رُفع من 1.0 لـ2.0 (بطلب المستخدم) — سقف أقصى لمسافة تراجع Trailing عن القمة
    "stop_loss_fallback_pct": 0.015,  # احتياطي أضيق لو ما قدرنا نحسب ATR
    "fallback_trail_pct": 0.008,      # احتياطي Trailing أضيق

    "usdt_per_trade": 20.0,
    "live_trading": False,
    "state_file": "mean_reversion_parallel_state.json",
    "history_file": "mean_reversion_parallel_history.json",
    "coin_memory_db_path": "coin_memory.db",   # ⬅️ نفس قاعدة الذاكرة الموحّدة يلي البوت الأساسي يستخدمها
    "scan_pause_seconds": 0.3,
}


# ──────────────────────────────────────────────
# 🔧 تنفيذ شراء/بيع/ATR/utc_now — مستوردة من shared_trading_logic.py (بدل
# التكرار اليدوي بالثلاث ملفات الموازية — أي تعديل مستقبلي هلق بمكان واحد بس)
# ──────────────────────────────────────────────
_buy_market = trading.buy_market
_sell_market = trading.sell_market
_calculate_atr = trading.calculate_atr
_utc_now_iso = trading.utc_now_iso

class MeanReversionParallel:
    def __init__(self, client, notify_fn=None, config_fn=None, symbols_fn=None, **config_overrides):
        """
        config_fn: دالة اختيارية بدون معاملات، ترجع dict فيه قيم حية (atr_multiplier,
        trail_atr_multiplier, trail_activate_pct, trail_activate_atr_multiple,
        stop_loss_fallback_pct) من إعدادات البوت الأساسي — بتُستدعى بأول كل دورة.
        ⬅️ بطلب المستخدم: مربوطة بالكامل بإعدادات الحماية الحية للبوت الأساسي —
        نفس مستوى المخاطرة متل باقي الاستراتيجيات الموازية، بدل إعداداتها
        المستقلة الأضيق يلي كانت افتراضياً. لو ما انمرر config_fn (تشغيل مستقل
        للاختبار مثلاً)، بترجع تلقائياً للقيم الأضيق بـ DEFAULT_CONFIG فوق.

        symbols_fn: دالة اختيارية بدون معاملات، ترجع القائمة الحية لعملات البوت الأساسي.
        """
        self.client = client
        self.notify_fn = notify_fn
        self.config_fn = config_fn
        self.symbols_fn = symbols_fn
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(config_overrides)

        self.atr_guard = ATRGuard()
        self.coin_memory = CoinMemory(self.cfg["coin_memory_db_path"])   # ⬅️ نفس قاعدة الذاكرة الموحّدة (قراءة + كتابة)
        self.regime_detector = MarketRegimeDetector(client)

        self.position = None
        self._load_state()

        self._symbols_cache = None
        self._symbols_cache_time = 0

    def _notify(self, text):
        if self.notify_fn:
            try:
                self.notify_fn(text)
            except Exception:
                pass

    def _refresh_live_config(self):
        if self.config_fn is None:
            return
        try:
            live_values = self.config_fn()
            if live_values:
                self.cfg.update(live_values)
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
    # 🌐 قائمة العملات
    # ──────────────────────────────────────────────
    def _get_symbols(self):
        if self.symbols_fn is not None:
            try:
                live_symbols = self.symbols_fn()
                if live_symbols:
                    return list(live_symbols)
            except Exception:
                pass

        if self.cfg["symbols"]:
            return self.cfg["symbols"]

        if self._symbols_cache and (time.time() - self._symbols_cache_time) < 3600:
            return self._symbols_cache
        try:
            info = self.client.get_exchange_info()
            symbols = []
            for s in info["symbols"]:
                if (s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
                        and s.get("isSpotTradingAllowed", True)):
                    base = s["baseAsset"]
                    if any(tag in base for tag in ("UP", "DOWN", "BULL", "BEAR")):
                        continue
                    symbols.append(s["symbol"])
            self._symbols_cache = symbols
            self._symbols_cache_time = time.time()
            return symbols
        except Exception:
            return self._symbols_cache or []

    # ──────────────────────────────────────────────
    # 🔍 كشف ارتداد من تشبع بيعي لعملة وحدة (كل طبقات الحماية الستة)
    # ──────────────────────────────────────────────
    def check_symbol_entry(self, symbol, btc_regime=None):
        try:
            if not sayyad_logic.has_sufficient_liquidity(self.client, symbol):
                return None

            cfg = self.cfg
            limit = max(cfg["trend_ma_period"], cfg["bb_period"]) + cfg["sell_volume_prior_window"] + cfg["atr_period"] + 15
            klines = self.client.get_klines(symbol=symbol, interval=cfg["interval"], limit=limit)
            if not klines or len(klines) < cfg["trend_ma_period"] + 5:
                return None
            closes = pd.Series([float(k[4]) for k in klines])
            opens = pd.Series([float(k[1]) for k in klines])
            highs = pd.Series([float(k[2]) for k in klines])
            lows = pd.Series([float(k[3]) for k in klines])
            volumes = pd.Series([float(k[5]) for k in klines])

            closed_idx = -2   # آخر شمعة مغلقة (تجنب شمعة لسا مفتوحة)
            prev_idx = -3

            # ── 1) التشبع البيعي المزدوج: RSI + بولينجر ──
            rsi_series = ta.momentum.RSIIndicator(close=closes, window=cfg["rsi_period"]).rsi()
            rsi_value = rsi_series.iloc[closed_idx]
            if pd.isna(rsi_value) or rsi_value >= cfg["rsi_oversold_level"]:
                return None

            sma = closes.rolling(window=cfg["bb_period"]).mean()
            std = closes.rolling(window=cfg["bb_period"]).std()
            bb_lower = (sma - cfg["bb_std_dev"] * std).iloc[closed_idx]
            if pd.isna(bb_lower):
                return None

            candle_close = float(closes.iloc[closed_idx])
            candle_open = float(opens.iloc[closed_idx])
            if candle_close > bb_lower * (1 + cfg["bb_lower_margin_pct"] / 100.0):
                return None   # مو قريب كفاية من الحد السفلي

            # ── 2) تأكيد ارتداد فعلي: شمعة صاعدة + أعلى من إغلاق يلي قبلها ──
            if candle_close <= candle_open:
                return None   # الشمعة لسا هابطة — ما بدأ الارتداد فعلياً
            prev_close = float(closes.iloc[prev_idx])
            if candle_close <= prev_close:
                return None

            # ── 3) فحص فوليوم البيع: عم يخف ولا لسا قوي؟ ──
            recent_w = cfg["sell_volume_recent_window"]
            prior_w = cfg["sell_volume_prior_window"]
            recent_vol_avg = volumes.iloc[closed_idx - recent_w:closed_idx].mean()
            prior_vol_avg = volumes.iloc[closed_idx - recent_w - prior_w:closed_idx - recent_w].mean()
            if pd.isna(recent_vol_avg) or pd.isna(prior_vol_avg) or prior_vol_avg <= 0:
                return None
            if recent_vol_avg >= prior_vol_avg:
                return None   # ضغط البيع لسا قوي أو زايد — مو إشارة استهلاك

            # ── 4) فلتر ضد السكين الساقطة: مو بعيد كتير تحت الترند الطويل ──
            trend_ma = closes.rolling(window=cfg["trend_ma_period"]).mean().iloc[closed_idx]
            if pd.isna(trend_ma) or trend_ma <= 0:
                return None
            downtrend_pct = ((trend_ma - candle_close) / trend_ma) * 100
            if downtrend_pct > cfg["max_downtrend_pct"]:
                return None   # نزول حاد جداً — ترند هابط حقيقي، مش تصحيح مؤقت

            # ── 5) فلتر حالة BTC العام: وقت BEAR، لازم العملة INVERSE_STRENGTH ──
            if cfg["require_inverse_strength_in_bear"] and btc_regime == "BEAR":
                stats = self.coin_memory.get_stats(symbol)
                if stats is None or stats.correlation_type != CorrelationType.INVERSE_STRENGTH.value:
                    return None

            atr_value = _calculate_atr(highs, lows, closes, cfg["atr_period"]) if cfg.get("atr_enabled", True) else None

            return {
                "symbol": symbol,
                "price": candle_close,
                "rsi": round(float(rsi_value), 2),
                "bb_lower": round(float(bb_lower), 8),
                "downtrend_vs_trend_ma_pct": round(downtrend_pct, 2),
                "sell_volume_recent_avg": round(float(recent_vol_avg), 2),
                "sell_volume_prior_avg": round(float(prior_vol_avg), 2),
                "atr": atr_value,
                "signal_info": (
                    f"🔻 <b>Mean Reversion — {symbol.replace('USDT','')}</b>\n"
                    f"💰 السعر: {candle_close:.6f} | RSI: {rsi_value:.1f} | تحت بولينجر السفلي: {bb_lower:.6f}\n"
                    f"📉 فوليوم البيع يخف: {recent_vol_avg:.1f} < {prior_vol_avg:.1f}\n"
                    f"📊 المسافة عن الترند الطويل: -{downtrend_pct:.2f}%"
                ),
            }
        except BinanceAPIException:
            return None
        except Exception:
            return None

    def scan_for_entry(self):
        symbols = self._get_symbols()
        if not symbols:
            return None

        # 🛑 وقف احترازي — بس عند انهيار سوق شامل ومتطرف (75%+ من العملات
        # نازلة خلال 24 ساعة). ملاحظة: ما نوقف عند BTC BEAR عادي، لأنه هاي
        # الاستراتيجية أصلاً مصممة تصطاد ارتدادات وقت الهبوط (btc_regime
        # بالأسفل بيُستخدم كفلتر تفضيل، مش كمنع) — إيقافها بمجرد BEAR
        # بيلغي وظيفتها الأساسية.
        breadth = sayyad_logic.compute_market_breadth(self.client, symbols)
        if breadth and breadth["is_bearish"]:
            return None

        # 🌡️ نحسب حالة BTC العامة مرة وحدة لكل دورة فحص (مش لكل عملة) — تقليل حمل API
        try:
            btc_regime = self.regime_detector.get_regime_label()
        except Exception:
            btc_regime = "SIDEWAYS"

        portfolio = get_portfolio_manager()
        for symbol in symbols:
            if portfolio.is_claimed_by_other(symbol, _PORTFOLIO_OWNER):
                continue
            signal = self.check_symbol_entry(symbol, btc_regime=btc_regime)
            if signal is not None:
                return signal
            time.sleep(self.cfg["scan_pause_seconds"])
        return None

    # ──────────────────────────────────────────────
    # ⚡ تنفيذ الشراء
    # ──────────────────────────────────────────────
    def _execute_buy(self, signal):
        symbol = signal["symbol"]
        amount = self.cfg["usdt_per_trade"]

        if not get_portfolio_manager().try_claim(symbol, _PORTFOLIO_OWNER):
            self._notify(f"⚠️ Mean Reversion: تراجعت عن شراء {symbol.replace('USDT','')} — محجوزة لاستراتيجية تانية حالياً")
            return

        if self.cfg["live_trading"]:
            result, error = _buy_market(self.client, symbol, amount)
            if result is None:
                get_portfolio_manager().release(symbol, _PORTFOLIO_OWNER)
                self._notify(
                    f"❌ <b>Mean Reversion — فشل تنفيذ أمر الشراء ({symbol.replace('USDT','')})</b>\n"
                    f"⚠️ السبب: {error}"
                )
                return
            entry_price, qty = result["entry_price"], result["qty"]
        else:
            entry_price = signal["price"]
            qty = round(amount / entry_price, 6)

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
            f"🟢 <b>Mean Reversion{mode_tag} — دخول {symbol.replace('USDT','')}</b>\n"
            f"{signal['signal_info']}\n"
            f"🛡️ وقف الخسارة الأولي (أضيق من العادي): {stop_loss:.6f} | حالة السوق: {market_regime}\n"
            f"💵 المبلغ: {amount} USDT"
        )

    # ──────────────────────────────────────────────
    # 🔄 ستوب الـ Trailing
    # ──────────────────────────────────────────────
    def _refresh_position_atr(self, symbol):
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
        🔒 محسوبة عبر shared_trading_logic.compute_trail_stop() — مشترك مع
        باقي الاستراتيجيات الموازية.
        """
        atr_val = self.position.get("atr")
        entry_price = self.position["entry_price"]
        return trading.compute_trail_stop(price, atr_val, entry_price, self.cfg)

    # ──────────────────────────────────────────────
    # 🔍 إدارة الصفقة المفتوحة (Trailing + Stop Loss)
    # ──────────────────────────────────────────────
    def manage_open_position(self):
        symbol = self.position["symbol"]
        try:
            price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            return

        fresh_atr = self._refresh_position_atr(symbol)
        atr_val = fresh_atr if fresh_atr else self.position.get("atr")

        # ⬅️ نقطة تفعيل Trailing — محسوبة عبر shared_trading_logic.compute_trail_trigger()
        trail_trigger = trading.compute_trail_trigger(self.position["entry_price"], atr_val, self.cfg)

        if not self.position["trailing_active"]:
            if price >= trail_trigger:
                self.position["trailing_active"] = True
                self.position["highest_price"] = price
                self.position["stop_loss"] = self._compute_trail_stop(price)
                self._save_state()
                self._notify(f"🎯 Mean Reversion ({symbol.replace('USDT','')}): تفعيل Trailing | ستوب: {self.position['stop_loss']}")

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

    def _execute_sell(self, current_price, reason):
        symbol = self.position["symbol"]
        entry_price = self.position["entry_price"]
        qty = self.position["qty"]

        if self.cfg["live_trading"]:
            exit_price, executed_qty, error = _sell_market(self.client, symbol, qty)
            if exit_price is None:
                self._notify(
                    f"❌ <b>Mean Reversion — فشل تنفيذ أمر البيع ({symbol.replace('USDT','')})</b>\n"
                    f"السبب المحاول: {reason}\n⚠️ الخطأ: {error}"
                )
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
                slippage_pct=0.0, strategy="mean_reversion_parallel",
            )
        except Exception:
            pass

        icon = "✅" if pnl > 0 else "❌"
        reason_map = {"trailing_stop": "Trailing Stop", "stop_loss": "وقف خسارة", "manual_close": "إغلاق يدوي"}
        reason_ar = reason_map.get(reason, reason)
        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"{icon} <b>Mean Reversion{mode_tag} — خروج {symbol.replace('USDT','')} ({reason_ar})</b>\n"
            f"💰 دخول: {entry_price:.6f} → خروج: {exit_price:.6f}\n"
            f"📊 PnL: {pnl:+.4f} USDT ({pnl_pct:+.3f}%)"
        )

        self.position = None
        get_portfolio_manager().release(symbol, _PORTFOLIO_OWNER)
        self._save_state()

    def close_manually(self):
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

    # ──────────────────────────────────────────────
    # 🔄 دورة فحص/تنفيذ واحدة
    # ──────────────────────────────────────────────
    def check_and_act(self):
        self._refresh_live_config()

        if self.position is not None:
            self.manage_open_position()
            return {"status": "holding" if self.position else "exited"}

        signal = self.scan_for_entry()
        if signal is not None:
            self._execute_buy(signal)
            return {"status": "entered", "signal": signal}
        return {"status": "waiting_for_entry"}

    def run(self, poll_seconds=1800, position_check_seconds=60, max_iterations=None, is_enabled_fn=None):
        """
        صفقة مفتوحة تُدار كل دقيقة (position_check_seconds)، والبحث عن دخول جديد
        كل poll_seconds (30 دقيقة، نفس فريم الاستراتيجية). لو الاستراتيجية موقوفة
        وعندها صفقة مفتوحة، إدارة الصفقة (ستوب لوز/Trailing) تضل شغالة دايماً —
        الإيقاف بس بيمنع البحث عن صفقات جديدة.
        """
        mode = "🔴 LIVE (صفقات حقيقية)" if self.cfg["live_trading"] else "🧪 Paper"
        print(f"🔻 بدء Mean Reversion — الوضع: {mode} — المبلغ: {self.cfg['usdt_per_trade']} USDT/صفقة — فريم: 30 دقيقة")

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

    strategy = MeanReversionParallel(client, usdt_per_trade=15.0, live_trading=False)
    print("🔍 فحص فوري لأفضل إشارة ارتداد حالياً (قد يأخذ دقيقة لكل العملات)...")
    signal = strategy.scan_for_entry()
    if signal:
        print(f"✅ إشارة: {signal['symbol']} @ {signal['price']} | RSI: {signal['rsi']}")
    else:
        print("⚠️ مافي إشارة ارتداد حالياً")
