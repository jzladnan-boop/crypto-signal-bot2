"""
🚀 Breakout Parallel — استراتيجية موازية ومستقلة (اختراق قمة N شمعة — Donchian)
====================================================================================
استراتيجية منفصلة تماماً — بتشتري العملة لما تقفل شمعة فوق أعلى قمة سعرية
بآخر N شمعة (نافذة Donchian)، يعني قمة جديدة فعلياً — بدل ما تنتظر انكماش
تذبذب زي Squeeze Breakout، هاي بتلاحق أي كسر حقيقي لسقف سعري واضح.

المنطق:
----------
1) نافذة Donchian: أعلى قمة (high) بآخر lookback_candles شمعة **قبل** الشمعة
   الحالية المغلقة (يعني ما بتحسب الشمعة نفسها ضمن السقف — لازم تكسره فعلياً).

2) تأكيد الاختراق: الشمعة المغلقة تقفل فوق هالسقف + فوليوم ≥ volume_multiplier
   × متوسط آخر volume_ma_length شمعة (تأكيد دخول سيولة حقيقي، مش اختراق وهمي).

3) حد أمان ضد مطاردة القمة: قفل الشمعة ما يكون ابتعد أكتر من
   max_rise_from_breakout_pct% فوق سقف الاختراق (نمسك أول لحظات الكسر،
   مش بعد ما يركض بعيد).

4) الحماية بعد الشراء: نفس نظام البوت الحقيقي بالضبط (ATR Stop Loss + Trailing)
   — زي باقي الاستراتيجيات الموازية، بلا تكرار أو اختراع نظام جديد.

⚠️ استقلالية "المشتركات المتغيّرة": نفس مبدأ trend_stoch_parallel.py و
squeeze_breakout.py بالضبط — PortfolioManager لمنع تضارب الشراء، ATRGuard/
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

_PORTFOLIO_OWNER = "breakout_parallel"


DEFAULT_CONFIG = {
    "interval": Client.KLINE_INTERVAL_1HOUR,
    "symbols": None,   # None = يجيب قائمة عملات USDT Spot النشطة تلقائياً (احتياطي لو ما انمررت symbols_fn)

    # ── نافذة الاختراق (Donchian) ──
    "lookback_candles": 50,          # عدد الشموع لحساب أعلى قمة سابقة (فريم ساعة = ~50 ساعة تاريخ)
    "volume_ma_length": 20,
    "volume_multiplier": 1.2,        # الفوليوم الحالي لازم يكون 1.2× المتوسط على الأقل
    "max_rise_from_breakout_pct": 3.0,  # حد الأمان — ما نلحق لو ابتعد أكتر من 3% فوق سقف الاختراق

    # ⬅️ بطلب المستخدم: تعديلين ضد الاختراق الوهمي (False Breakout) — كتير
    # صفقات كانت تضرب ستوب لوز بسرعة بأول ساعة-ساعتين، بالذات وقت BTC جانبي.
    "require_confirmation_candle": True,   # لازم شمعتين متتاليتين فوق السقف، مش وحدة بس
    "require_bull_regime": True,           # ما نشتري اختراق إلا لو حالة السوق العامة BULL (ترند صاعد واضح)

    # ── الحماية (نفس نظام البوت بالضبط) ──
    "atr_period": 14,
    "atr_multiplier": 2.0,
    "trail_atr_multiplier": 1.5,
    "trail_activate_atr_multiple": 1.0,     # ⬅️ تفعيل Trailing عند ربح = 1.0×ATR الحالي
    "trail_activate_pct": 0.01,        # احتياطي فقط — يُستخدم لو تعذر حساب ATR
    "stop_loss_fallback_pct": 0.02,    # احتياطي لو ما قدرنا نحسب ATR
    "fallback_trail_pct": 0.01,        # احتياطي Trailing لو ما قدرنا نحسب ATR

    "usdt_per_trade": 15.0,
    "live_trading": False,
    "state_file": "breakout_parallel_state.json",
    "history_file": "breakout_parallel_history.json",
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


class BreakoutParallel:
    def __init__(self, client, notify_fn=None, config_fn=None, symbols_fn=None, **config_overrides):
        """
        config_fn: دالة اختيارية بدون معاملات، ترجع dict فيه قيم حية (atr_multiplier,
        trail_atr_multiplier, trail_activate_pct) من إعدادات البوت الأساسي — بتُستدعى
        بأول كل دورة.

        symbols_fn: دالة اختيارية بدون معاملات، ترجع القائمة الحية لعملات البوت الأساسي.
        لو ما انمررت (تشغيل مستقل للاختبار)، بترجع تلقائياً لجلب كل أزواج USDT من بينانس.
        """
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
    # 🔍 كشف اختراق قمة N شمعة (Donchian) لعملة وحدة
    # ──────────────────────────────────────────────
    def check_symbol_entry(self, symbol):
        try:
            # ⬅️ لو تأكيد الشمعتين مفعّل، محتاجين شمعة وحدة زيادة للخلف (عشان نقدر
            # نتحقق من شمعة الاختراق الأصلية + الشمعة التالية يلي تؤكدها).
            confirm_offset = 1 if self.cfg["require_confirmation_candle"] else 0
            limit = self.cfg["lookback_candles"] + self.cfg["volume_ma_length"] + self.cfg["atr_period"] + 10 + confirm_offset
            klines = self.client.get_klines(symbol=symbol, interval=self.cfg["interval"], limit=limit)
            if not klines or len(klines) < self.cfg["lookback_candles"] + 5 + confirm_offset:
                return None
            closes = pd.Series([float(k[4]) for k in klines])
            highs = pd.Series([float(k[2]) for k in klines])
            lows = pd.Series([float(k[3]) for k in klines])
            volumes = pd.Series([float(k[5]) for k in klines])

            closed_idx = -2   # آخر شمعة مغلقة (تجنب شمعة لسا مفتوحة)
            # ⬅️ شمعة الاختراق "المرشحة": لو تأكيد الشمعتين مفعّل، هي الشمعة يلي
            # قبل آخر شمعة مغلقة (يعني آخر شمعة مغلقة هلق هي "شمعة التأكيد")،
            # وإلا هي نفسها آخر شمعة مغلقة (نفس السلوك القديم بدون تأكيد إضافي).
            breakout_idx = closed_idx - 1 if self.cfg["require_confirmation_candle"] else closed_idx

            # ── الخطوة 1: سقف Donchian — أعلى قمة بآخر lookback_candles شمعة
            # **قبل** شمعة الاختراق نفسها (ما بتدخل هي بحساب السقف، وإلا أي شمعة
            # رح تكون "دايماً" فوق سقف يشملها).
            window_start = breakout_idx - self.cfg["lookback_candles"]
            prior_highs = highs.iloc[window_start:breakout_idx]
            if len(prior_highs) < self.cfg["lookback_candles"] * 0.8:
                return None   # بيانات ناقصة كتير — نتجاهل بدل ما نقرر بثقة زايفة
            donchian_high = float(prior_highs.max())
            if donchian_high <= 0:
                return None

            # ── الخطوة 2: تأكيد الاختراق — شمعة الاختراق تقفل فوق السقف ──
            breakout_close = float(closes.iloc[breakout_idx])
            if breakout_close <= donchian_high:
                return None

            # ⬅️ بطلب المستخدم: تأكيد بشمعتين ضد الاختراق الوهمي — نتحقق إن
            # الشمعة التالية (آخر شمعة مغلقة فعلياً، closed_idx) لسا صامدة فوق
            # نفس السقف. لو رجعت تحته، يعني الاختراق كان وهمي ونرفض الإشارة.
            if self.cfg["require_confirmation_candle"]:
                confirm_close = float(closes.iloc[closed_idx])
                if confirm_close <= donchian_high:
                    return None   # الاختراق ما صمد — رجع تحت السقف بالشمعة التالية

            candle_close = float(closes.iloc[closed_idx])   # سعر الدخول الفعلي = آخر شمعة مغلقة

            # ── الخطوة 3: تأكيد الفوليوم (على شمعة الاختراق الأصلية) ──
            vol_ma = volumes.rolling(window=self.cfg["volume_ma_length"]).mean().iloc[breakout_idx]
            if pd.isna(vol_ma) or vol_ma <= 0:
                return None
            volume_ratio = float(volumes.iloc[breakout_idx]) / vol_ma
            if volume_ratio < self.cfg["volume_multiplier"]:
                return None

            # ── الخطوة 4: حد الأمان ضد مطاردة القمة (نقيسه من سعر الدخول الفعلي) ──
            rise_pct = ((candle_close - donchian_high) / donchian_high) * 100
            if rise_pct > self.cfg["max_rise_from_breakout_pct"]:
                return None

            # ⬅️ بطلب المستخدم: فلتر حالة السوق — ما نشتري اختراق إلا وقت ترند
            # صاعد واضح (BULL). الاختراقات بسوق جانبي/هابط أضعف بطبيعتها وأكتر
            # عرضة للانعكاس السريع (Whipsaw).
            if self.cfg["require_bull_regime"]:
                try:
                    regime = self.regime_detector.get_regime_label()
                except Exception:
                    regime = "SIDEWAYS"   # تعذر التحليل — نتعامل بحذر ونرفض
                if regime != "BULL":
                    return None

            atr_value = _calculate_atr(highs, lows, closes, self.cfg["atr_period"])

            confirm_note = " (مؤكد بشمعتين)" if self.cfg["require_confirmation_candle"] else ""
            return {
                "symbol": symbol,
                "price": candle_close,
                "donchian_high": round(donchian_high, 8),
                "rise_from_breakout_pct": round(rise_pct, 3),
                "volume_ratio": round(volume_ratio, 2),
                "atr": atr_value,
                "signal_info": (
                    f"🚀 <b>Breakout — {symbol.replace('USDT','')}</b>{confirm_note}\n"
                    f"💰 السعر: {candle_close:.6f} | كسر قمة {self.cfg['lookback_candles']} شمعة: {donchian_high:.6f} (+{rise_pct:.2f}%)\n"
                    f"📊 فوليوم: {volume_ratio:.2f}× المتوسط"
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
            self._notify(f"⚠️ Breakout: تراجعت عن شراء {symbol.replace('USDT','')} — محجوزة لاستراتيجية تانية حالياً")
            return

        if self.cfg["live_trading"]:
            result, error = _buy_market(self.client, symbol, amount)
            if result is None:
                get_portfolio_manager().release(symbol, _PORTFOLIO_OWNER)
                self._notify(
                    f"❌ <b>Breakout — فشل تنفيذ أمر الشراء ({symbol.replace('USDT','')})</b>\n"
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
            f"🟢 <b>Breakout{mode_tag} — دخول {symbol.replace('USDT','')}</b>\n"
            f"{signal['signal_info']}\n"
            f"🛡️ وقف الخسارة الأولي: {stop_loss:.6f} | حالة السوق: {market_regime}\n"
            f"💵 المبلغ: {amount} USDT"
        )

    # ──────────────────────────────────────────────
    # 🔄 ستوب الـ Trailing
    # ──────────────────────────────────────────────
    def _refresh_position_atr(self, symbol):
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

        if atr_val:
            trail_trigger = self.position["entry_price"] + (self.cfg["trail_activate_atr_multiple"] * atr_val)
        else:
            trail_trigger = self.position["entry_price"] * (1 + self.cfg["trail_activate_pct"])

        if not self.position["trailing_active"]:
            if price >= trail_trigger:
                self.position["trailing_active"] = True
                self.position["highest_price"] = price
                self.position["stop_loss"] = self._compute_trail_stop(price)
                self._save_state()
                self._notify(f"🎯 Breakout ({symbol.replace('USDT','')}): تفعيل Trailing | ستوب: {self.position['stop_loss']}")

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
                    f"❌ <b>Breakout — فشل تنفيذ أمر البيع ({symbol.replace('USDT','')})</b>\n"
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
                slippage_pct=0.0, strategy="breakout_parallel",
            )
        except Exception:
            pass

        icon = "✅" if pnl > 0 else "❌"
        reason_map = {"trailing_stop": "Trailing Stop", "stop_loss": "وقف خسارة", "manual_close": "إغلاق يدوي"}
        reason_ar = reason_map.get(reason, reason)
        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"{icon} <b>Breakout{mode_tag} — خروج {symbol.replace('USDT','')} ({reason_ar})</b>\n"
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

    def run(self, poll_seconds=3600, position_check_seconds=60, max_iterations=None, is_enabled_fn=None):
        """
        صفقة مفتوحة تُدار كل دقيقة (position_check_seconds)، والبحث عن دخول جديد
        كل poll_seconds (ساعة، نفس فريم الاستراتيجية). لو الاستراتيجية موقوفة
        وعندها صفقة مفتوحة، إدارة الصفقة (ستوب لوز/Trailing) تضل شغالة دايماً —
        الإيقاف بس بيمنع البحث عن صفقات جديدة.
        """
        mode = "🔴 LIVE (صفقات حقيقية)" if self.cfg["live_trading"] else "🧪 Paper"
        print(f"🚀 بدء Breakout — الوضع: {mode} — المبلغ: {self.cfg['usdt_per_trade']} USDT/صفقة — فريم: ساعة")

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

    strategy = BreakoutParallel(client, usdt_per_trade=15.0, live_trading=False)
    print("🔍 فحص فوري لأفضل إشارة اختراق حالياً (قد يأخذ دقيقة لكل العملات)...")
    signal = strategy.scan_for_entry()
    if signal:
        print(f"✅ إشارة: {signal['symbol']} @ {signal['price']} | كسر قمة: {signal['donchian_high']}")
    else:
        print("⚠️ مافي إشارة اختراق حالياً")
