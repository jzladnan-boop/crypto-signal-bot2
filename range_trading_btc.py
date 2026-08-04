"""
📊 Range Trading BTC — استراتيجية موازية ومستقلة (تنفيذ حقيقي)
==================================================================
استراتيجية منفصلة تماماً عن باقي البوت — بتتاجر تذبذب BTC (Range Trading)
بين دعم ومقاومة حقيقيين، على فريم 30 دقيقة، حصراً على BTCUSDT، بمبلغ ثابت
لكل صفقة (15 USDT افتراضياً)، وصفقة وحدة بس مفتوحة بأي لحظة.

⚠️ استقلالية كاملة: ما بيستورد ولا بيلمس أي شي من market_regime.py أو أي
استراتيجية تانية بـ crypto_signal_bot.py. عنده تنفيذ شراء/بيع خاص فيه
(نسخة مستقلة من نفس منطق get_quantity/buy/sell المستخدم بالبوت الأساسي)،
وملف حفظ حالة (JSON) خاص فيه — ما بيلمس open_trades ولا ملفات البوت التانية.

الربط بـ Telegram/التطبيق (تشغيل/إيقاف) بيصير من crypto_signal_bot.py نفسه
عن طريق حقن دالة notify (send_telegram) ومتغير تفعيل خارجي — التفاصيل
بأسفل الملف.
"""

import time
import json
import os
import pandas as pd
import ta
from binance.client import Client
from binance.exceptions import BinanceAPIException


DEFAULT_CONFIG = {
    "symbol": "BTCUSDT",
    "interval": Client.KLINE_INTERVAL_30MINUTE,
    "lookback_candles": 12,         # ⬅️ بطلب المستخدم: كانت 96 (48 ساعة) → 12 شمعة × 30 دقيقة = 6 ساعات فقط (تفعيل أسرع بكثير)
    "swing_order": 3,              # القمة/القاع لازم تكون أعلى/أدنى من 3 شمعات على كل جنب (فراكتال)
    "cluster_tolerance_pct": 0.3,  # القمم/القيعان اللي فرق بينها أقل من 0.3% تعتبر نفس المنطقة
    "min_range_width_pct": 0.5,     # ⬅️ بطلب المستخدم: كانت 1.0% → 0.5% (نطاقات أضيق تنعتبر حقيقية الآن)
    "touch_tolerance_pct": 0.3,    # "لمس" الدعم = السعر وصل لحدود دعم±0.3%
    "confirm_margin_pct": 0.15,    # قفل الشمعة لازم يكون 0.15% فوق الدعم على الأقل (تأكيد ارتداد حقيقي)
    "rsi_period": 14,
    "stop_loss_below_support_pct": 0.3,     # وقف الخسارة تحت مستوى الدعم (مو تحت سعر الدخول)
    "resistance_touch_tolerance_pct": 0.3,  # "لمس" المقاومة = وصل لحدود مقاومة±0.3% → بيع فوري
    "usdt_per_trade": 15.0,        # المبلغ الثابت لكل صفقة
    "live_trading": False,         # ⚠️ لازم True صراحة عشان تنفتح صفقات حقيقية على المنصة
    "state_file": "range_trading_state.json",   # حفظ الصفقة المفتوحة (تنجو من إعادة تشغيل)
    "history_file": "range_trading_history.json",  # سجل الصفقات المغلقة (منفصل كلياً عن ملفات البوت)
}


# ──────────────────────────────────────────────
# 🔧 تنفيذ شراء/بيع مستقل (نسخة خاصة بهاي الاستراتيجية، ما بتستورد من bot.py)
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
        precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
        qty = round(qty - (qty % step_size), precision)
    return qty, price


def _buy_market(client, symbol, usdt_amount):
    """
    يشتري بمبلغ USDT ثابت.
    يرجع (result_dict, error_message):
      - نجاح: ({"qty":.., "entry_price":.., "order_id":..}, None)
      - فشل : (None, "نص الخطأ الفعلي من Binance أو من الكود")
    """
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
    """
    يبيع كمية محددة (بحد أقصى الرصيد الفعلي المتاح).
    يرجع (price, executed_qty, error_message):
      - نجاح: (السعر, الكمية المنفذة, None)
      - فشل : (None, None, "نص الخطأ الفعلي")
    """
    try:
        asset = symbol.replace("USDT", "")
        balance = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])
        step_size = _get_step_size(client, symbol)
        sell_qty = min(qty, actual_qty)
        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1]) if "." in str(step_size) else 0
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


class RangeTradingBTC:
    def __init__(self, client, notify_fn=None, **config_overrides):
        """
        client: عميل بينانس (نفس العميل يلي البوت الأساسي بيستخدمه)
        notify_fn: دالة اختيارية بتاخد نص (str) وترسله (مثلاً send_telegram من bot.py) —
                   حقن الدالة بدل الاستيراد المباشر، حتى يضل الملف مستقل تماماً عن bot.py.
        """
        self.client = client
        self.notify_fn = notify_fn
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(config_overrides)

        self.position = None       # صفقة وحدة بس ممكن تكون مفتوحة بأي لحظة (BTC فقط)
        self._load_state()

    def _notify(self, text):
        if self.notify_fn:
            try:
                self.notify_fn(text)
            except Exception:
                pass

    # ──────────────────────────────────────────────
    # 💾 حفظ/استرجاع حالة الصفقة المفتوحة (تنجو من إعادة تشغيل السكريبت/البوت)
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
                if self.position is None:
                    json.dump(None, f)
                else:
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
    # جلب الشموع وحساب المؤشرات
    # ──────────────────────────────────────────────
    def _get_klines(self):
        try:
            klines = self.client.get_klines(
                symbol=self.cfg["symbol"],
                interval=self.cfg["interval"],
                limit=self.cfg["lookback_candles"] + self.cfg["rsi_period"] + 10,
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

    def _compute_rsi(self, closes: pd.Series):
        try:
            return ta.momentum.RSIIndicator(close=closes, window=self.cfg["rsi_period"]).rsi()
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # 🔍 تحديد الدعم والمقاومة الحقيقيين (Swing Points + تجميع مناطق)
    # ──────────────────────────────────────────────
    def _find_swing_points(self, df: pd.DataFrame):
        order = self.cfg["swing_order"]
        lows = df["low"].values
        highs = df["high"].values
        n = len(df)
        swing_lows, swing_highs = [], []
        for i in range(order, n - order):
            window_low = lows[i - order: i + order + 1]
            window_high = highs[i - order: i + order + 1]
            if lows[i] == window_low.min():
                swing_lows.append(lows[i])
            if highs[i] == window_high.max():
                swing_highs.append(highs[i])
        return swing_lows, swing_highs

    def _cluster_levels(self, levels: list):
        if not levels:
            return None, 0
        tolerance = self.cfg["cluster_tolerance_pct"] / 100.0
        levels_sorted = sorted(levels)
        clusters = []
        for price in levels_sorted:
            placed = False
            for cluster in clusters:
                cluster_avg = sum(cluster) / len(cluster)
                if abs(price - cluster_avg) / cluster_avg <= tolerance:
                    cluster.append(price)
                    placed = True
                    break
            if not placed:
                clusters.append([price])
        best_cluster = max(clusters, key=len)
        level_price = sum(best_cluster) / len(best_cluster)
        return level_price, len(best_cluster)

    def get_support_resistance(self):
        df = self._get_klines()
        if df is None:
            return None
        window_df = df.iloc[:-1].tail(self.cfg["lookback_candles"]).reset_index(drop=True)
        swing_lows, swing_highs = self._find_swing_points(window_df)
        support, support_touches = self._cluster_levels(swing_lows)
        resistance, resistance_touches = self._cluster_levels(swing_highs)
        if support is None or resistance is None or resistance <= support:
            return None
        range_width_pct = ((resistance - support) / support) * 100
        if range_width_pct < self.cfg["min_range_width_pct"]:
            return None
        return {
            "support": support,
            "resistance": resistance,
            "support_touches": support_touches,
            "resistance_touches": resistance_touches,
            "range_width_pct": round(range_width_pct, 3),
        }

    # ──────────────────────────────────────────────
    # 🟢 فحص إشارة الشراء (دخول عند الدعم بعد تأكيد ارتداد)
    # ──────────────────────────────────────────────
    def check_entry_signal(self):
        levels = self.get_support_resistance()
        if levels is None:
            return None
        df = self._get_klines()
        if df is None or len(df) < self.cfg["rsi_period"] + 3:
            return None
        rsi_series = self._compute_rsi(df["close"])
        if rsi_series is None:
            return None

        closed_idx, prev_idx = -2, -3
        last_low = df["low"].iloc[closed_idx]
        last_close = df["close"].iloc[closed_idx]
        last_open = df["open"].iloc[closed_idx]
        rsi_now = rsi_series.iloc[closed_idx]
        rsi_prev = rsi_series.iloc[prev_idx]
        if pd.isna(rsi_now) or pd.isna(rsi_prev):
            return None

        support = levels["support"]
        resistance = levels["resistance"]
        touch_tol = self.cfg["touch_tolerance_pct"] / 100.0
        confirm_margin = self.cfg["confirm_margin_pct"] / 100.0

        if not (last_low <= support * (1 + touch_tol)):
            return None
        if not (last_close >= support * (1 + confirm_margin)):
            return None
        # ⬅️ بطلب المستخدم: شرط "RSI كان بتشبع بيعي وارتد" أُلغي بالكامل — الدخول
        # هلق يعتمد بس على لمسة الدعم + قفل الشمعة فوقه بهامش واضح. RSI لسا محسوب
        # ومعروض بالإشارة للمعلومية بس، مو شرط دخول.
        if last_close <= last_open:
            return None

        potential_gain_pct = ((resistance - last_close) / last_close) * 100
        if potential_gain_pct < (self.cfg["min_range_width_pct"] * 0.5):
            return None

        stop_loss_price = support * (1 - self.cfg["stop_loss_below_support_pct"] / 100)

        return {
            "action": "buy",
            "symbol": self.cfg["symbol"],
            "price": float(last_close),
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "stop_loss_price": round(stop_loss_price, 2),
            "rsi": round(float(rsi_now), 2),
            "potential_gain_pct": round(potential_gain_pct, 3),
            "signal_info": (
                f"📊 <b>Range Trading BTC — ارتداد من الدعم</b>\n"
                f"💰 السعر: {last_close:.2f} | الدعم: {support:.2f} ({levels['support_touches']} لمسات)\n"
                f"🎯 المقاومة: {resistance:.2f} ({levels['resistance_touches']} لمسات) | هامش ربح محتمل: {potential_gain_pct:.2f}%\n"
                f"📈 RSI: {rsi_now:.1f} (للمعلومية فقط، مو شرط دخول)\n"
                f"🛡️ وقف الخسارة: {stop_loss_price:.2f} (تحت الدعم بـ {self.cfg['stop_loss_below_support_pct']}%)"
            ),
        }

    # ──────────────────────────────────────────────
    # 🔴 فحص إشارة الخروج (بيع فوري عند لمس المقاومة، أو وقف خسارة)
    # ──────────────────────────────────────────────
    def check_exit_signal(self):
        if self.position is None:
            return None
        try:
            current_price = float(self.client.get_symbol_ticker(symbol=self.cfg["symbol"])["price"])
        except Exception:
            return None

        resistance = self.position["resistance"]
        stop_loss_price = self.position["stop_loss_price"]
        touch_tol = self.cfg["resistance_touch_tolerance_pct"] / 100.0

        if current_price <= stop_loss_price:
            return {
                "action": "sell", "reason": "stop_loss", "price": current_price,
                "signal_info": (
                    f"🛑 <b>Range Trading BTC — وقف خسارة</b>\n"
                    f"السعر {current_price:.2f} تحت وقف الخسارة {stop_loss_price:.2f} (الدعم {self.position['support']:.2f} انكسر)"
                ),
            }
        if current_price >= resistance * (1 - touch_tol):
            return {
                "action": "sell", "reason": "take_profit", "price": current_price,
                "signal_info": (
                    f"🎯 <b>Range Trading BTC — وصول للمقاومة</b>\n"
                    f"السعر {current_price:.2f} وصل للمقاومة {resistance:.2f} — بيع فوري"
                ),
            }
        return None

    # ──────────────────────────────────────────────
    # ⚡ تنفيذ فعلي: شراء/بيع حقيقي (أو محاكاة Paper لو live_trading=False)
    # ──────────────────────────────────────────────
    def _execute_buy(self, signal):
        symbol = self.cfg["symbol"]
        amount = self.cfg["usdt_per_trade"]

        if self.cfg["live_trading"]:
            result, error = _buy_market(self.client, symbol, amount)
            if result is None:
                self._notify(
                    f"❌ <b>Range Trading BTC — فشل تنفيذ أمر الشراء</b>\n"
                    f"السعر وقت الإشارة: {signal['price']:.2f}\n"
                    f"⚠️ السبب: {error}"
                )
                return
            entry_price = result["entry_price"]
            qty = result["qty"]
        else:
            entry_price = signal["price"]
            qty = round(amount / entry_price, 6)

        self.position = {
            "entry_price": entry_price,
            "qty": qty,
            "support": signal["support"],
            "resistance": signal["resistance"],
            "stop_loss_price": signal["stop_loss_price"],
            "entry_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "live": self.cfg["live_trading"],
        }
        self._save_state()

        mode_tag = "" if self.cfg["live_trading"] else " (Paper — بدون تنفيذ حقيقي)"
        self._notify(
            f"🟢 <b>Range Trading BTC — دخول{mode_tag}</b>\n"
            f"{signal['signal_info']}\n"
            f"💵 المبلغ: {amount} USDT | الكمية: {qty}"
        )

    def _execute_sell(self, signal):
        symbol = self.cfg["symbol"]
        entry_price = self.position["entry_price"]
        qty = self.position["qty"]

        if self.cfg["live_trading"]:
            exit_price, executed_qty, error = _sell_market(self.client, symbol, qty)
            if exit_price is None:
                self._notify(
                    f"❌ <b>Range Trading BTC — فشل تنفيذ أمر البيع</b>\n"
                    f"السبب المحاول: {signal['reason']}\n"
                    f"⚠️ الخطأ: {error}"
                )
                return   # ما نصفّر الصفقة — نحاول تاني بالدورة الجاية
            final_qty = executed_qty
        else:
            exit_price = signal["price"]
            final_qty = qty

        pnl = round((exit_price - entry_price) * final_qty, 4)
        pnl_pct = round((exit_price - entry_price) / entry_price * 100, 3)

        self._log_history({
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": final_qty,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": signal["reason"],
            "entry_time": self.position.get("entry_time"),
            "exit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "live": self.cfg["live_trading"],
        })

        icon = "✅" if pnl > 0 else "❌"
        mode_tag = "" if self.cfg["live_trading"] else " (Paper)"
        self._notify(
            f"{icon} <b>Range Trading BTC — خروج{mode_tag}</b>\n"
            f"{signal['signal_info']}\n"
            f"💰 الربح/الخسارة: {pnl:+.4f} USDT ({pnl_pct:+.3f}%)"
        )

        self.position = None
        self._save_state()

    # ──────────────────────────────────────────────
    # 🔄 الفحص والتنفيذ الرئيسي — يُستدعى بكل دورة (كل 30 دقيقة)
    # ──────────────────────────────────────────────
    def check_and_act(self):
        """
        فحص + تنفيذ فوري (مو بس إشارة). صفقة وحدة بس مسموحة بأي لحظة — لو فيه
        صفقة مفتوحة أصلاً، ما رح يفحص إشارة دخول جديدة إطلاقاً لغاية ما توقف
        الحالية (بيع عند المقاومة أو وقف خسارة)، وبعدها بيرجع يدور على دخول
        جديد تلقائياً بالدورة يلي بعدها.
        """
        if self.position is None:
            signal = self.check_entry_signal()
            if signal is not None:
                self._execute_buy(signal)
                return {"status": "entered", "signal": signal}
            return {"status": "waiting_for_entry"}
        else:
            signal = self.check_exit_signal()
            if signal is not None:
                self._execute_sell(signal)
                return {"status": "exited", "signal": signal}
            return {"status": "holding", "position": self.position}

    def run(self, poll_seconds=1800, max_iterations=None, is_enabled_fn=None):
        """
        حلقة تشغيل مستقلة (لو بدك تشغل هالملف لحاله كسكريبت منفصل، مش عن طريق
        دمجه بـ bot.py). is_enabled_fn: دالة اختيارية ترجع True/False لتفعيل/
        إيقاف الفحص من مصدر خارجي (تيليغرام/تطبيق) بدون ما توقف الحلقة نفسها.
        """
        mode = "🔴 LIVE (صفقات حقيقية)" if self.cfg["live_trading"] else "🧪 Paper (تجربة بدون تنفيذ)"
        print(f"📊 بدء Range Trading BTC — الوضع: {mode} — المبلغ: {self.cfg['usdt_per_trade']} USDT/صفقة")

        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            if is_enabled_fn is None or is_enabled_fn():
                try:
                    self.check_and_act()
                except Exception as e:
                    print(f"❌ خطأ بدورة الفحص: {e}")
                sleep_time = poll_seconds
            else:
                sleep_time = 10   # نفحص التفعيل بشكل متكرر لما يكون مطفي، حتى نستجيب بسرعة لو اتفعّل

            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                time.sleep(sleep_time)


if __name__ == "__main__":
    import os as _os
    api_key = _os.environ.get("BINANCE_API_KEY", "")
    api_secret = _os.environ.get("BINANCE_API_SECRET", "")
    client = Client(api_key, api_secret)

    # ⚠️ افتراضياً live_trading=False هون — لازم تفعّلها صراحة بعد ما تتأكد من النتائج
    strategy = RangeTradingBTC(client, usdt_per_trade=15.0, live_trading=False)
    levels = strategy.get_support_resistance()
    if levels:
        print(f"📊 الدعم: {levels['support']:.2f} ({levels['support_touches']} لمسات) | "
              f"المقاومة: {levels['resistance']:.2f} ({levels['resistance_touches']} لمسات) | "
              f"عرض النطاق: {levels['range_width_pct']:.2f}%")
    else:
        print("⚠️ ما لقيت نطاق تداول حقيقي حالياً")
