"""
🐍 Squeeze Breakout — استراتيجية موازية ومستقلة (كاشف الانضغاط والانفجار المبكر)
====================================================================================
استراتيجية منفصلة تماماً — بتصطاد العملات الهادية بشكل غير طبيعي (منكمشة، ما
بتنزل عميق ولا بتطلع) قبل ما تنفجر بحركة قوية — بدل ما تنتظر لغاية ما توصل +10%
وتصير مطاردة قمة.

المنطق (Bollinger Band Squeeze):
----------------------------------
1) الانكماش: نحسب عرض بولينجر (Upper-Lower)/Middle لكل شمعة، ونحتفظ بتاريخه لآخر
   100 شمعة. لو القيمة الحالية من ضمن أقل 15% قيمة سُجّلت بهالفترة → العملة
   "منكمشة" حالياً (تذبذب منخفض جداً بشكل غير طبيعي، مؤشر على انفجار قادم).

2) تأكيد الانفجار (بس للعملات المنكمشة): الشمعة المغلقة تقفل فوق الحد العلوي
   لبولينجر + فوليوم ≥ 1.5× متوسط آخر 20 شمعة (تأكيد دخول سيولة حقيقي).

3) حد أمان ضد مطاردة القمة: السعر الحالي ما يكون ابتعد أكتر من 3% عن سعر آخر
   شمعة كانت بوضع انكماش (يعني نمسك أول لحظات الانفجار، مش بعد ما يركض بعيد).

4) الحماية بعد الشراء: نفس نظام البوت الحقيقي بالضبط (ATR Stop Loss + Trailing)
   — زي باقي الاستراتيجيات الموازية، بلا تكرار أو اختراع نظام جديد.

⚠️ بدون أي LLM أو تحليل نصي — رياضيات بحتة قابلة للحساب الفوري على آلاف العملات.
⚠️ استقلالية "المشتركات المتغيّرة": نفس مبدأ
trend_stoch_parallel.py بالضبط — PortfolioManager لمنع تضارب الشراء، ATRGuard/
MarketRegimeDetector كأدوات حساب بلا حالة، config_fn/symbols_fn كحقن تبعيات
بدل استيراد مباشر من bot.py.
"""

import time
import json
import os
import datetime
import pandas as pd
import ta
from binance.client import Client
from binance.exceptions import BinanceAPIException

from coin_memory import ATRGuard, CoinMemory
from market_regime import MarketRegimeDetector
from portfolio_manager import get_portfolio_manager

_PORTFOLIO_OWNER = "squeeze_breakout"


DEFAULT_CONFIG = {
    "interval": Client.KLINE_INTERVAL_30MINUTE,
    "symbols": None,   # None = يجيب قائمة عملات USDT Spot النشطة تلقائياً (احتياطي لو ما انمررت symbols_fn)

    # ── كشف الانكماش (Squeeze) ──
    "bb_period": 20,
    "bb_std_dev": 2.0,
    "bbw_history_lookback": 100,     # عدد الشموع لحساب تاريخ عرض بولينجر
    "squeeze_percentile": 15,        # العرض الحالي لازم يكون من ضمن أقل 15% تاريخياً

    # ── تأكيد الانفجار ──
    "volume_ma_length": 20,
    "volume_multiplier": 1.5,
    "max_rise_from_squeeze_pct": 3.0,  # حد الأمان — ما نلحق لو ابتعد أكتر من 3% عن نقطة الانكماش

    # ── الحماية (نفس نظام البوت بالضبط) ──
    "atr_enabled": True,   # ⬅️ بطلب المستخدم: زر تشغيل/إيقاف ATR — ينعكس من إعدادات البوت الأساسي عبر config_fn
    "atr_period": 14,
    "atr_multiplier": 2.0,
    "trail_atr_multiplier": 1.5,
    "trail_activate_atr_multiple": 1.0,     # ⬅️ بدل نسبة ثابتة: تفعيل Trailing عند ربح = 1.0×ATR الحالي
    "trail_activate_pct": 0.01,        # احتياطي فقط — يُستخدم لو تعذر حساب ATR
    "trail_trigger_max_pct": 3.0,      # ⬅️ إصلاح: سقف أقصى (%) لنسبة الربح المطلوبة لتفعيل Trailing
    "min_profit_lock_pct": 0.80,       # ⬅️ بطلب المستخدم: ربح مضمون بعد تفعيل Trailing، مش مجرد Breakeven
    "trail_distance_max_pct": 1.0,     # ⬅️ بطلب المستخدم: سقف أقصى لمسافة تراجع Trailing عن القمة
    "stop_loss_fallback_pct": 0.02,
    "fallback_trail_pct": 0.01,

    "usdt_per_trade": 20.0,
    "live_trading": False,
    "state_file": "squeeze_breakout_state.json",
    "history_file": "squeeze_breakout_history.json",
    "coin_memory_db_path": "coin_memory.db",   # ⬅️ نفس قاعدة الذاكرة الموحّدة يلي البوت الأساسي يستخدمها
    "scan_pause_seconds": 0.3,
}


def _utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ──────────────────────────────────────────────
# 🔧 تنفيذ شراء/بيع مستقل (نفس نمط باقي الاستراتيجيات الموازية)
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
        precision = len(format(step_size, ".10f").rstrip("0").split(".")[-1]) if "." in format(step_size, ".10f").rstrip("0") else 0
        qty = round(qty - (qty % step_size), precision)
    return qty, price


def _buy_market(client, symbol, usdt_amount):
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
    try:
        asset = symbol.replace("USDT", "")
        balance = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])
        step_size = _get_step_size(client, symbol)
        sell_qty = actual_qty   # 🔐 نبيع كامل الرصيد المتاح (نفس آلية باقي الاستراتيجيات الموازية — تنظيف الغبار)
        if step_size:
            precision = len(format(step_size, ".10f").rstrip("0").split(".")[-1]) if "." in format(step_size, ".10f").rstrip("0") else 0
            sell_qty = round(sell_qty - (sell_qty % step_size), precision)
        if sell_qty <= 0:
            return None, None, f"الكمية المتاحة للبيع صفر أو أقل (رصيد {asset}: {actual_qty})"
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


def _calculate_atr(highs, lows, closes, period):
    try:
        atr_series = ta.volatility.AverageTrueRange(high=highs, low=lows, close=closes, window=period).average_true_range()
        value = atr_series.iloc[-1]
        if pd.isna(value) or value <= 0:
            return None
        return float(value)
    except Exception:
        return None


class SqueezeBreakout:
    def __init__(self, client, notify_fn=None, config_fn=None, symbols_fn=None, **config_overrides):
        self.client = client
        self.notify_fn = notify_fn
        self.config_fn = config_fn
        self.symbols_fn = symbols_fn
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(config_overrides)

        self.atr_guard = ATRGuard()
        self.coin_memory = CoinMemory(self.cfg["coin_memory_db_path"])   # ⬅️ نفس قاعدة الذاكرة الموحّدة (كتابة بس هون)
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
    # 🔍 كشف الانكماش والانفجار لعملة وحدة
    # ──────────────────────────────────────────────
    def check_symbol_entry(self, symbol):
        try:
            limit = self.cfg["bbw_history_lookback"] + self.cfg["bb_period"] + self.cfg["atr_period"] + 10
            klines = self.client.get_klines(symbol=symbol, interval=self.cfg["interval"], limit=limit)
            if not klines or len(klines) < self.cfg["bbw_history_lookback"] + self.cfg["bb_period"]:
                return None
            closes = pd.Series([float(k[4]) for k in klines])
            highs = pd.Series([float(k[2]) for k in klines])
            lows = pd.Series([float(k[3]) for k in klines])
            volumes = pd.Series([float(k[5]) for k in klines])

            # ── الخطوة 1: حساب عرض بولينجر وتاريخه ──
            sma = closes.rolling(window=self.cfg["bb_period"]).mean()
            std = closes.rolling(window=self.cfg["bb_period"]).std()
            upper = sma + (self.cfg["bb_std_dev"] * std)
            lower = sma - (self.cfg["bb_std_dev"] * std)
            bbw = (upper - lower) / sma   # عرض بولينجر (Bollinger Band Width)

            closed_idx = -2   # آخر شمعة مغلقة
            bbw_history = bbw.iloc[-(self.cfg["bbw_history_lookback"] + 1):closed_idx + 1].dropna()
            if len(bbw_history) < self.cfg["bbw_history_lookback"] * 0.8:
                return None   # بيانات ناقصة كتير — نتجاهل بدل ما نقرر بثقة زايفة

            bbw_current = bbw.iloc[closed_idx]
            if pd.isna(bbw_current):
                return None

            squeeze_cutoff = bbw_history.quantile(self.cfg["squeeze_percentile"] / 100.0)
            is_squeezed_now = bbw_current <= squeeze_cutoff
            if not is_squeezed_now:
                prev_bbw = bbw.iloc[closed_idx - 1]
                if pd.isna(prev_bbw) or prev_bbw > squeeze_cutoff:
                    return None

            # ── الخطوة 2: تأكيد الانفجار (الشمعة المغلقة الحالية) ──
            candle_close = float(closes.iloc[closed_idx])
            candle_upper_band = float(upper.iloc[closed_idx])
            if candle_close <= candle_upper_band:
                return None

            vol_ma = volumes.rolling(window=self.cfg["volume_ma_length"]).mean().iloc[closed_idx]
            if pd.isna(vol_ma) or vol_ma <= 0:
                return None
            volume_ratio = float(volumes.iloc[closed_idx]) / vol_ma
            if volume_ratio < self.cfg["volume_multiplier"]:
                return None

            # ── الخطوة 3: حد الأمان ضد مطاردة القمة ──
            squeeze_reference_price = None
            for i in range(closed_idx, closed_idx - self.cfg["bbw_history_lookback"], -1):
                if pd.isna(bbw.iloc[i]):
                    break
                if bbw.iloc[i] <= squeeze_cutoff:
                    squeeze_reference_price = float(closes.iloc[i])
                else:
                    break
            if squeeze_reference_price is None:
                return None

            rise_pct = ((candle_close - squeeze_reference_price) / squeeze_reference_price) * 100
            if rise_pct > self.cfg["max_rise_from_squeeze_pct"]:
                return None

            atr_value = _calculate_atr(highs, lows, closes, self.cfg["atr_period"]) if self.cfg.get("atr_enabled", True) else None

            return {
                "symbol": symbol,
                "price": candle_close,
                "squeeze_reference_price": round(squeeze_reference_price, 8),
                "rise_from_squeeze_pct": round(rise_pct, 3),
                "bbw_current": round(float(bbw_current), 5),
                "bbw_percentile_cutoff": round(float(squeeze_cutoff), 5),
                "volume_ratio": round(volume_ratio, 2),
                "atr": atr_value,
                "signal_info": (
                    f"🐍 <b>Squeeze Breakout — {symbol.replace('USDT','')}</b>\n"
                    f"💰 السعر: {candle_close:.6f} | ارتفاع من نقطة الانكماش: {rise_pct:.2f}%\n"
                    f"📊 فوليوم: {volume_ratio:.2f}× المتوسط | BBW: {bbw_current:.5f} (عتبة الانكماش: {squeeze_cutoff:.5f})"
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
        portfolio = get_portfolio_manager()
        for symbol in symbols:
            if portfolio.is_claimed_by_other(symbol, _PORTFOLIO_OWNER):
                continue
            signal = self.check_symbol_entry(symbol)
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
            self._notify(f"⚠️ Squeeze Breakout: تراجعت عن شراء {symbol.replace('USDT','')} — محجوزة لاستراتيجية تانية حالياً")
            return

        if self.cfg["live_trading"]:
            result, error = _buy_market(self.client, symbol, amount)
            if result is None:
                get_portfolio_manager().release(symbol, _PORTFOLIO_OWNER)
                self._notify(
                    f"❌ <b>Squeeze Breakout — فشل تنفيذ أمر الشراء ({symbol.replace('USDT','')})</b>\n"
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
            f"🟢 <b>Squeeze Breakout{mode_tag} — دخول {symbol.replace('USDT','')}</b>\n"
            f"{signal['signal_info']}\n"
            f"🛡️ وقف الخسارة الأولي: {stop_loss:.6f} | حالة السوق: {market_regime}\n"
            f"💵 المبلغ: {amount} USDT"
        )

    # ──────────────────────────────────────────────
    # 🔄 ستوب الـ Trailing
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
        🔒 سقف مسافة التراجع (trail_distance_max_pct): مسافة الستوب عن القمة
        ما تتجاوز هالنسبة من السعر، بغض النظر عن قيمة ATR الخام.

        🔒 Minimum Profit Lock: أول ما Trailing يتفعّل، الستوب ما ينزل أبداً
        تحت (سعر الدخول + min_profit_lock_pct%).
        """
        atr_val = self.position.get("atr")
        if atr_val:
            atr_distance = self.cfg["trail_atr_multiplier"] * atr_val
            max_distance = price * (self.cfg["trail_distance_max_pct"] / 100)
            distance = min(atr_distance, max_distance)   # 🔒 الأصغر بين ATR والسقف النسبي
            candidate = price - distance
            if not (0 < candidate < price):
                candidate = price * (1 - self.cfg["fallback_trail_pct"])
        else:
            candidate = price * (1 - self.cfg["fallback_trail_pct"])

        min_locked_price = self.position["entry_price"] * (1 + self.cfg["min_profit_lock_pct"] / 100)
        candidate = max(candidate, min_locked_price)   # 🔒 Minimum Profit Lock
        return round(candidate, 8)

    # ──────────────────────────────────────────────
    # 🔍 إدارة الصفقة المفتوحة (Trailing + Stop Loss)
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

        # ⬅️ نقطة تفعيل Trailing بمضاعف ATR بدل نسبة ثابتة، مقيّدة بسقف أقصى
        if atr_val:
            raw_trigger = self.position["entry_price"] + (self.cfg["trail_activate_atr_multiple"] * atr_val)
            capped_trigger = self.position["entry_price"] * (1 + self.cfg["trail_trigger_max_pct"] / 100)
            trail_trigger = min(raw_trigger, capped_trigger)
        else:
            trail_trigger = self.position["entry_price"] * (1 + self.cfg["trail_activate_pct"])

        if not self.position["trailing_active"]:
            if price >= trail_trigger:
                self.position["trailing_active"] = True
                self.position["highest_price"] = price
                self.position["stop_loss"] = self._compute_trail_stop(price)
                self._save_state()
                self._notify(f"🎯 Squeeze Breakout ({symbol.replace('USDT','')}): تفعيل Trailing | ستوب: {self.position['stop_loss']}")

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
                    f"❌ <b>Squeeze Breakout — فشل تنفيذ أمر البيع ({symbol.replace('USDT','')})</b>\n"
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
                slippage_pct=0.0, strategy="squeeze_breakout",
            )
        except Exception:
            pass

        icon = "✅" if pnl > 0 else "❌"
        reason_map = {"trailing_stop": "Trailing Stop", "stop_loss": "وقف خسارة", "manual_close": "إغلاق يدوي"}
        reason_ar = reason_map.get(reason, reason)
        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"{icon} <b>Squeeze Breakout{mode_tag} — خروج {symbol.replace('USDT','')} ({reason_ar})</b>\n"
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
        نفس مبدأ فصل الترددين المطبّق بباقي الاستراتيجيات: صفقة مفتوحة تُدار كل
        دقيقة (position_check_seconds)، والبحث عن دخول جديد كل poll_seconds
        (30 دقيقة، نفس فريم الاستراتيجية).

        ⬅️ إصلاح باگ حقيقي: لو الاستراتيجية موقوفة وعندها صفقة مفتوحة، كان الكود
        يوقف check_and_act() بالكامل — الصفقة تضل بدون مراقبة (لا ستوب لوز، لا
        Trailing) لحد ما ترجع تتفعّل. هلق إدارة الصفقة المفتوحة شغالة دايماً بغض
        النظر عن حالة التفعيل — الإيقاف بس بيمنع البحث عن صفقات جديدة.
        """
        mode = "🔴 LIVE (صفقات حقيقية)" if self.cfg["live_trading"] else "🧪 Paper"
        print(f"🐍 بدء Squeeze Breakout — الوضع: {mode} — المبلغ: {self.cfg['usdt_per_trade']} USDT/صفقة — فريم: 30 دقيقة")

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

    strategy = SqueezeBreakout(client, usdt_per_trade=15.0, live_trading=False)
    print("🔍 فحص فوري لأفضل إشارة انضغاط/انفجار حالياً (قد يأخذ دقيقة لكل العملات)...")
    signal = strategy.scan_for_entry()
    if signal:
        print(f"✅ إشارة: {signal['symbol']} @ {signal['price']} | ارتفاع من الانكماش: {signal['rise_from_squeeze_pct']}%")
    else:
        print("⚠️ مافي إشارة انضغاط/انفجار حالياً")
