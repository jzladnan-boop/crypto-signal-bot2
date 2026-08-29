"""
🔧 Shared Trading Logic — منطق تنفيذ وحماية مشترك للاستراتيجيات الموازية الثلاث
====================================================================================
استُخرج من trend_stoch_parallel.py / squeeze_breakout.py / mean_reversion_parallel.py
بعد ما تأكدنا إنه نفس المنطق بالضبط (تنفيذ الشراء/البيع، حساب ATR، وحساب
الستوب/الـ Trailing بكل تعديلاتنا الأخيرة) كان مكرر حرفياً بالثلاث ملفات —
أي تعديل مستقبلي (زي التعديلات يلي عملناها اليوم على الـ Trailing) كان
لازم يتكرر يدوياً 3 مرات، وهذا خطر حقيقي لتعارض بسيط أو نسيان ملف.

⚠️ نفس مبدأ استقلالية باقي الملفات المشتركة بالمشروع (indicators.py,
market_regime.py, portfolio_manager.py, sayyad_logic.py): هذا الملف
مستقل تماماً — ما يستورد أي شي من crypto_signal_bot.py ولا من أي
استراتيجية موازية، وما يلمس أي حالة عامة مشتركة (Shared Mutable State).
كل الدوال هون إما دوال تنفيذ API بحتة (buy_market/sell_market) أو دوال
حسابية بحتة بدون حالة (compute_trail_stop/compute_trail_trigger/calculate_atr) —
بتستقبل قيم صريحة وترجع نتيجة، بدون أي اعتماد على self أو متغير عام.

الاستخدام من أي استراتيجية موازية:
    import shared_trading_logic as trading

    result, err = trading.buy_market(client, symbol, usdt_amount)
    price, qty, err = trading.sell_market(client, symbol, qty)
    atr_value = trading.calculate_atr(highs, lows, closes, period)
    new_stop = trading.compute_trail_stop(price, atr_val, entry_price, cfg)
    trigger_price = trading.compute_trail_trigger(entry_price, atr_val, cfg)
"""

import datetime

import pandas as pd
import ta
from binance.exceptions import BinanceAPIException


# ──────────────────────────────────────────────
# 🔧 تنفيذ شراء/بيع (بدون استيراد من bot.py ولا أي استراتيجية تانية)
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


def buy_market(client, symbol, usdt_amount):
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


def sell_market(client, symbol, qty):
    """
    يرجع (price, executed_qty, error_message).
    🔐 تبيع كامل الرصيد المتاح فعلياً (مش بس كمية الصفقة المسجّلة) — عشان
    تنكسح أي غبار (Dust) متراكم من تقريب LOT_SIZE بصفقات سابقة لنفس العملة.
    ⚠️ ملاحظة مهمة: لو عندك رصيد يدوي من نفس العملة خارج البوت (اشتريتها
    بنفسك من التطبيق مثلاً)، هالدالة بتبيعه هو كمان مع صفقة البوت — لأنها
    بتبيع "كل المتاح بالمحفظة"، مش بس كمية البوت.
    """
    try:
        asset = symbol.replace("USDT", "")
        balance = client.get_asset_balance(asset=asset)
        actual_qty = float(balance["free"])
        step_size = _get_step_size(client, symbol)
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


def utc_now_iso():
    """نفس آلية utc_now_iso() بـ crypto_signal_bot.py — إصلاح مشكلة عرض GMT بالتطبيق."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def calculate_atr(highs, lows, closes, period):
    try:
        atr_series = ta.volatility.AverageTrueRange(high=highs, low=lows, close=closes, window=period).average_true_range()
        value = atr_series.iloc[-1]
        if pd.isna(value) or value <= 0:
            return None
        return float(value)
    except Exception:
        return None


# ──────────────────────────────────────────────
# 🛡️ حساب الستوب/الـ Trailing — بكل التعديلات المتراكمة (بطلب المستخدم)
# ──────────────────────────────────────────────
def compute_trail_stop(price, atr_val, entry_price, cfg):
    """
    يحسب سعر ستوب جديد بناءً على أعلى سعر وصلته الصفقة (price).
    - لو الصفقة عندها ATR محفوظ من وقت الشراء: نستخدمه (تقلب العملة الحقيقي).
    - غير هيك: نرجع للنسبة الثابتة fallback_trail_pct كاحتياطي.
    ملاحظة: هذا السعر يُستخدم فقط لما يصير سعر قمة جديدة (price > highest_price)،
    فالستوب يتحرك لأعلى بس ولا ينزل أبداً مع نزول السعر.

    🔒 سقف مسافة التراجع (trail_distance_max_pct): مسافة الستوب عن القمة
    ما تتجاوز هالنسبة من السعر، بغض النظر عن قيمة ATR الخام — عملة متقلبة
    (ATR كبير) ما عاد تاخد مساحة واسعة تبتلع الربح.

    🔒 Minimum Profit Lock (min_profit_lock_pct): أول ما Trailing يتفعّل،
    الستوب ما ينزل أبداً تحت (سعر الدخول + min_profit_lock_pct%) — أسوأ
    سيناريو ممكن يصير هو الخروج بربح مضمون بهالنسبة على الأقل، مش الرجوع
    لخسارة فعلية بعد ما كانت الصفقة رابحة.

    يتوقع cfg (dict) فيه: trail_atr_multiplier, trail_distance_max_pct,
    fallback_trail_pct, min_profit_lock_pct.
    """
    if atr_val:
        atr_distance = cfg["trail_atr_multiplier"] * atr_val
        max_distance = price * (cfg["trail_distance_max_pct"] / 100)
        distance = min(atr_distance, max_distance)   # 🔒 الأصغر بين ATR والسقف النسبي
        candidate = price - distance
        if not (0 < candidate < price):
            candidate = price * (1 - cfg["fallback_trail_pct"])
    else:
        candidate = price * (1 - cfg["fallback_trail_pct"])

    min_locked_price = entry_price * (1 + cfg["min_profit_lock_pct"] / 100)
    candidate = max(candidate, min_locked_price)   # 🔒 Minimum Profit Lock
    return round(candidate, 8)


def compute_trail_trigger(entry_price, atr_val, cfg):
    """
    يحسب نقطة تفعيل Trailing — بمضاعف ATR بدل نسبة ثابتة (يتكيف مع تذبذب
    كل عملة)، مقيّدة بسقف أقصى (trail_trigger_max_pct) — يمنع عملة متقلبة
    من طلب ربح ضخم لتفعيل الحماية، رغم إن الـ Stop Loss النازل مقيّد بسقف
    أضيق بكثير.

    يتوقع cfg (dict) فيه: trail_activate_atr_multiple, trail_trigger_max_pct,
    trail_activate_pct.
    """
    if atr_val:
        raw_trigger = entry_price + (cfg["trail_activate_atr_multiple"] * atr_val)
        capped_trigger = entry_price * (1 + cfg["trail_trigger_max_pct"] / 100)
        return min(raw_trigger, capped_trigger)
    return entry_price * (1 + cfg["trail_activate_pct"])
