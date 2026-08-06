"""
📊 Range Trading BTC — استراتيجية موازية ومستقلة (نطاق سعري بسيط + حماية موحّدة)
====================================================================================
استراتيجية منفصلة تماماً عن باقي البوت — بتشتري BTC قرب أدنى سعر بآخر فترة زمنية
(نطاق سعري بسيط، مو دعم/مقاومة معقدة)، وبمجرد ما تشتري، بتدير الصفقة بنفس نظام
الحماية الحقيقي يلي البوت الأساسي مستخدمه (ATR Stop Loss + Breakeven + Trailing) —
تماماً متل trend_stoch_parallel.py.

⚠️ استقلالية "المشتركات المتغيّرة": نفس مبدأ trend_stoch_parallel.py — بنستورد
دوال/حاسبات بلا حالة بس (ATRGuard من coin_memory.py، MarketRegimeDetector من
market_regime.py)، ما بنلمس open_trades ولا أي حالة مشتركة مع الاستراتيجيات التانية.

المنطق:
--------
1) النطاق السعري: أعلى وأدنى سعر بآخر N شمعة (30 دقيقة) — بسيط ومباشر (Donchian)،
   بيتحدث تلقائياً كل فحص، بدون أي تحليل قمم/قيعان معقد.

2) الدخول: السعر يلمس أدنى الحدين (أو قريب منه) وتقفل الشمعة فوقه بهامش واضح +
   شمعة صاعدة (تأكيد ارتداد بسيط، بدون شرط RSI).

3) الحماية بعد الدخول — نفس نظام البوت الحقيقي بالضبط (زي Trend+Stoch الموازية):
   - Stop Loss أولي: ATR × مضاعف، مربوط بحالة السوق (BULL/BEAR/SIDEWAYS)
   - Breakeven عند ربح 0.5%، Trailing يتفعّل عند ربح 1%
   - ما في "بيع فوري عند حد أعلى" — الخروج بالكامل بيد نظام الحماية

4) صفقة وحدة بس بأي لحظة، بمبلغ ثابت 15 USDT، BTC حصراً، فريم 30 دقيقة.
"""

import time
import datetime
import json
import os
import pandas as pd
import ta
from binance.client import Client
from binance.exceptions import BinanceAPIException

from coin_memory import ATRGuard
from market_regime import MarketRegimeDetector


DEFAULT_CONFIG = {
    # ⬅️ بطلب المستخدم: كانت BTC حصراً، صارت سلة عملات — صفقة وحدة بس عبر الكل
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "CFXUSDT", "HEIUSDT"],
    "interval": Client.KLINE_INTERVAL_30MINUTE,

    # ── النطاق السعري (Donchian بسيط) ──
    "lookback_candles": 12,        # 12 شمعة × 30 دقيقة = 6 ساعات — نافذة تحديد أعلى/أدنى سعر
    "touch_tolerance_pct": 0.3,    # "لمس" الحد الأدنى = وصل لحدود الحد الأدنى ±0.3%
    "confirm_margin_pct": 0.15,    # قفل الشمعة لازم يكون 0.15% فوق الحد الأدنى (تأكيد ارتداد)

    # ── الحماية (نفس نظام البوت بالضبط — مطابقة trend_stoch_parallel.py) ──
    "atr_period": 14,
    "atr_multiplier": 2.0,             # مضاعف ATR للستوب الأولي
    "trail_atr_multiplier": 1.5,       # مضاعف ATR لمسافة الـ Trailing
    "trail_activate_pct": 0.01,        # 1% ربح → تفعيل Trailing
    "breakeven_activate_pct": 0.005,   # 0.5% ربح → تفعيل أرضية التعادل
    "breakeven_margin_pct": 0.002,     # 0.2% فوق الدخول (يغطي عمولة بينانس 0.1%×2)
    "stop_loss_fallback_pct": 0.02,    # احتياطي لو ما قدرنا نحسب ATR
    "fallback_trail_pct": 0.01,        # احتياطي Trailing لو ما قدرنا نحسب ATR

    "usdt_per_trade": 15.0,
    "live_trading": False,             # ⚠️ لازم True صراحة لتنفيذ صفقات حقيقية
    "state_file": "range_trading_state.json",
    "history_file": "range_trading_history.json",
}


# ──────────────────────────────────────────────
# 🔧 تنفيذ شراء/بيع مستقل (نفس نمط trend_stoch_parallel.py)
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
    """يرجع (result_dict, error_message)."""
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
    """يرجع (price, executed_qty, error_message)."""
    try:
        asset = symbol.replace("USDT", "")
        balance = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])
        step_size = _get_step_size(client, symbol)
        sell_qty = min(qty, actual_qty)
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


class RangeTradingBTC:
    def __init__(self, client, notify_fn=None, config_fn=None, **config_overrides):
        """
        config_fn: دالة اختيارية بدون معاملات، ترجع dict فيه قيم حية (atr_multiplier,
        trail_atr_multiplier, trail_activate_pct, breakeven_activate_pct,
        breakeven_margin_pct) من إعدادات البوت الأساسي — بتُستدعى بأول كل دورة.
        """
        self.client = client
        self.notify_fn = notify_fn
        self.config_fn = config_fn
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(config_overrides)

        self.atr_guard = ATRGuard()                          # نسخة خاصة
        self.regime_detector = MarketRegimeDetector(client)   # نسخة خاصة

        self.position = None   # صفقة وحدة بس بأي لحظة
        self._load_state()

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
    # جلب الشموع
    # ──────────────────────────────────────────────
    def _get_klines(self, symbol):
        try:
            klines = self.client.get_klines(
                symbol=symbol,
                interval=self.cfg["interval"],
                limit=self.cfg["lookback_candles"] + self.cfg["atr_period"] + 10,
            )
            if not klines or len(klines) < self.cfg["lookback_candles"]:
                return None
            df = pd.DataFrame(klines, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "tbbav", "tbqav", "ignore",
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # 🔍 النطاق السعري البسيط (أعلى/أدنى سعر بآخر N شمعة — Donchian)
    # ──────────────────────────────────────────────
    def get_price_range(self, symbol):
        df = self._get_klines(symbol)
        if df is None:
            return None
        # نستبعد آخر شمعة (لسا مفتوحة) من نافذة تحديد النطاق
        window_df = df.iloc[:-1].tail(self.cfg["lookback_candles"])
        range_low = float(window_df["low"].min())
        range_high = float(window_df["high"].max())
        if range_high <= range_low:
            return None
        return {"range_low": range_low, "range_high": range_high}

    # ──────────────────────────────────────────────
    # 🟢 فحص إشارة الدخول (لمسة الحد الأدنى + قفل فوقه + شمعة صاعدة)
    # ──────────────────────────────────────────────
    def check_entry_signal(self, symbol):
        price_range = self.get_price_range(symbol)
        if price_range is None:
            return None
        df = self._get_klines(symbol)
        if df is None:
            return None

        closed_idx = -2
        last_low = df["low"].iloc[closed_idx]
        last_close = df["close"].iloc[closed_idx]
        last_open = df["open"].iloc[closed_idx]

        range_low = price_range["range_low"]
        touch_tol = self.cfg["touch_tolerance_pct"] / 100.0
        confirm_margin = self.cfg["confirm_margin_pct"] / 100.0

        if not (last_low <= range_low * (1 + touch_tol)):
            return None
        if not (last_close >= range_low * (1 + confirm_margin)):
            return None
        if last_close <= last_open:
            return None

        highs = df["high"]
        lows = df["low"]
        closes = df["close"]
        atr_value = _calculate_atr(highs, lows, closes, self.cfg["atr_period"])
        coin_name = symbol.replace("USDT", "")

        return {
            "symbol": symbol,
            "price": float(last_close),
            "range_low": round(range_low, 8),
            "range_high": round(price_range["range_high"], 8),
            "atr": atr_value,
            "signal_info": (
                f"📊 <b>Range Trading — ارتداد من أدنى النطاق ({coin_name})</b>\n"
                f"💰 السعر: {last_close:.6f} | أدنى النطاق (6 ساعات): {range_low:.6f}\n"
                f"🔝 أعلى النطاق: {price_range['range_high']:.6f}"
            ),
        }

    def scan_for_entry(self):
        """يفحص كل عملات السلة بالترتيب، ويرجع أول إشارة تتحقق أو None."""
        for symbol in self.cfg["symbols"]:
            signal = self.check_entry_signal(symbol)
            if signal is not None:
                return signal
        return None

    # ──────────────────────────────────────────────
    # ⚡ تنفيذ الشراء (نفس نظام حساب الستوب الأولي بالضبط — ATRGuard)
    # ──────────────────────────────────────────────
    def _execute_buy(self, signal):
        symbol = signal["symbol"]
        amount = self.cfg["usdt_per_trade"]

        if self.cfg["live_trading"]:
            result, error = _buy_market(self.client, symbol, amount)
            if result is None:
                self._notify(
                    f"❌ <b>Range Trading — فشل تنفيذ أمر الشراء ({symbol.replace('USDT','')})</b>\n"
                    f"السعر وقت الإشارة: {signal['price']:.6f}\n"
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
            "breakeven_floor": None,
            "entry_time": _utc_now_iso(),
            "market_regime_at_entry": market_regime,
            "range_low_at_entry": signal["range_low"],
            "range_high_at_entry": signal["range_high"],
        }
        self._save_state()

        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"🟢 <b>Range Trading{mode_tag} — دخول</b>\n"
            f"{signal['signal_info']}\n"
            f"🛡️ وقف الخسارة الأولي: {stop_loss:.6f} | حالة السوق: {market_regime}\n"
            f"💵 المبلغ: {amount} USDT"
        )

    # ──────────────────────────────────────────────
    # 🔄 حساب ستوب الـ Trailing (نفس compute_trail_stop بالضبط)
    # ──────────────────────────────────────────────
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

        if self.position.get("breakeven_floor") is None:
            if price >= self.position["entry_price"] * (1 + self.cfg["breakeven_activate_pct"]):
                floor_price = round(self.position["entry_price"] * (1 + self.cfg["breakeven_margin_pct"]), 8)
                self.position["breakeven_floor"] = floor_price
                if floor_price > self.position["stop_loss"]:
                    self.position["stop_loss"] = floor_price
                self._save_state()

        stop_floor = self.position.get("breakeven_floor") or self.position["entry_price"]

        if not self.position["trailing_active"]:
            if price >= self.position["entry_price"] * (1 + self.cfg["trail_activate_pct"]):
                self.position["trailing_active"] = True
                self.position["highest_price"] = price
                self.position["stop_loss"] = max(self._compute_trail_stop(price), stop_floor)
                self._save_state()
                self._notify(f"🎯 Range Trading ({symbol.replace('USDT','')}): تفعيل Trailing | ستوب: {self.position['stop_loss']}")

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

    def _execute_sell(self, current_price, reason):
        symbol = self.position["symbol"]
        entry_price = self.position["entry_price"]
        qty = self.position["qty"]

        if self.cfg["live_trading"]:
            exit_price, executed_qty, error = _sell_market(self.client, symbol, qty)
            if exit_price is None:
                self._notify(
                    f"❌ <b>Range Trading — فشل تنفيذ أمر البيع ({symbol.replace('USDT','')})</b>\n"
                    f"السبب المحاول: {reason}\n"
                    f"⚠️ الخطأ: {error}"
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

        icon = "✅" if pnl > 0 else "❌"
        reason_map = {"trailing_stop": "Trailing Stop", "stop_loss": "وقف خسارة", "manual_close": "إغلاق يدوي"}
        reason_ar = reason_map.get(reason, reason)
        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"{icon} <b>Range Trading{mode_tag} — خروج {symbol.replace('USDT','')} ({reason_ar})</b>\n"
            f"💰 دخول: {entry_price:.6f} → خروج: {exit_price:.6f}\n"
            f"📊 PnL: {pnl:+.4f} USDT ({pnl_pct:+.3f}%)"
        )

        self.position = None
        self._save_state()

    def close_manually(self):
        """إغلاق يدوي (من التطبيق/API) — يبيع فوراً بالسعر الحالي."""
        if self.position is None:
            return None, "not_found"
        try:
            current_price = float(self.client.get_symbol_ticker(symbol=self.position["symbol"])["price"])
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

    def run(self, poll_seconds=1800, max_iterations=None, is_enabled_fn=None):
        mode = "🔴 LIVE (صفقات حقيقية)" if self.cfg["live_trading"] else "🧪 Paper"
        print(f"📊 بدء Range Trading (سلة: {', '.join(s.replace('USDT','') for s in self.cfg['symbols'])}) — الوضع: {mode} — المبلغ: {self.cfg['usdt_per_trade']} USDT/صفقة")

        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            if is_enabled_fn is None or is_enabled_fn():
                try:
                    self.check_and_act()
                except Exception as e:
                    print(f"❌ خطأ بدورة الفحص: {e}")
                sleep_time = poll_seconds
            else:
                sleep_time = 10
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                time.sleep(sleep_time)


if __name__ == "__main__":
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    client = Client(api_key, api_secret)

    strategy = RangeTradingBTC(client, usdt_per_trade=15.0, live_trading=False)
    for sym in strategy.cfg["symbols"]:
        price_range = strategy.get_price_range(sym)
        if price_range:
            print(f"📊 {sym}: {price_range['range_low']:.6f} - {price_range['range_high']:.6f}")
        else:
            print(f"⚠️ {sym}: ما قدرت أحسب النطاق حالياً")
