"""
🔄 Inverse BTC Strategy
========================
استراتيجية رابعة مستقلة — تشتري العملات اللي بتقاوم نزول BTC.

المنطق:
1. BTC لازم يكون نازل بقوة (خلال 24 ساعة من Binance مباشرة)
2. العملة لازم تكون صاعدة بـ 1% أو أكثر (مقاومة لنزول BTC)
3. العملة لازم تكون بمنطقة شراء تقنياً (RSI < 40 أو StochRSI بمنطقة تشبع بيعي)
4. فوليوم أعلى من المتوسط (تأكيد إن فيه اهتمام حقيقي)

هذا الملف مستقل تماماً — بس يستقبل client وsymbol ويرجع dict أو None.
"""

import time
import pandas as pd
import numpy as np
import ta
from binance.client import Client


# ── إعدادات افتراضية (قابلة للتعديل من التطبيق) ─────────────────────────
DEFAULT_CONFIG = {
    "btc_symbol": "BTCUSDT",
    # شروط سوق BTC للتفعيل التلقائي. تُستخدم أيضاً من market_regime.py.
    "btc_decline_threshold_pct": 3.0,   # BTC لازم ينزل 3% على الأقل خلال 24 ساعة
    "btc_trend_margin_pct": 2.0,        # BTC تحت MA50 (4 ساعات) بهامش 2% على الأقل
    "market_confirmations_required": 2, # عدد فحوص السوق المتتالية قبل التحويل لـ Inverse
    "market_recovery_threshold_pct": 1.5, # الخروج من Inverse عند تعافي BTC فوق -1.5%
    # شروط اختيار العملة والدخول بعد ارتداد مؤكد.
    "rs_min_threshold_pct": 5.0,        # العملة لازم تكون صاعدة 5% على الأقل
    "rsi_max_for_entry": 40,            # RSI لازم يكون تحت 40
    "rsi_rebound_min": 30,              # RSI لازم يرتد صعوداً ويصل لهذا الحد
    "stoch_k_max_for_entry": 30,        # StochRSI K السابق كان بمنطقة التشبع
    "stoch_k_rebound_min": 20,          # StochRSI K الحالي لازم يرتد فوق هذا الحد
    "volume_ma_period": 20,             # فترة متوسط الفوليوم
    "min_volume_ratio": 1.2,            # الفوليوم الحالي 1.2× المتوسط
    "max_price_below_ma20_pct": 0.5,    # لا نشتري لو السعر تحت MA20 بأكثر من هذه النسبة
}

_current_config = dict(DEFAULT_CONFIG)


def set_config(**kwargs):
    """يحدث الإعدادات من التطبيق أو التيليغرام."""
    global _current_config
    for key, value in kwargs.items():
        if key in _current_config:
            _current_config[key] = value


def get_config():
    """يرجع نسخة من الإعدادات الحالية."""
    return dict(_current_config)


# ── دوال مساعدة ─────────────────────────────────────────────────────────

def _get_klines_safe(client, symbol, interval, limit):
    """يجلب الشموع مع معالجة الأخطاء."""
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        if not klines or len(klines) < limit - 5:
            return None
        return klines
    except Exception:
        return None


def get_24h_change_pct(client, symbol):
    """
    📊 يرجع نسبة تغيّر السعر خلال 24 ساعة مباشرة من Binance (get_ticker).
    لا يحتاج شموع — قيمة جاهزة من البورصة.
    """
    try:
        ticker = client.get_ticker(symbol=symbol)
        return float(ticker.get("priceChangePercent", 0))
    except Exception:
        return None


# ── الفحص الرئيسي ───────────────────────────────────────────────────────

def get_btc_decline(client):
    """
    يحسب نسبة نزول BTC خلال 24 ساعة من Binance مباشرة.
    يرجع (decline_pct, is_declining) أو (None, False).
    """
    change_pct = get_24h_change_pct(client, _current_config["btc_symbol"])
    if change_pct is None:
        return None, False

    # change_pct سالبة لو نازل (مثلاً -1.5). نحولها لصيغة return (-0.015)
    decline_pct = change_pct / 100.0
    threshold = _current_config["btc_decline_threshold_pct"] / 100.0
    is_declining = decline_pct <= -threshold
    return decline_pct, is_declining


def get_relative_strength(client, symbol):
    """
    يحسب قوة العملة النسبية مقابل BTC خلال 24 ساعة (من get_ticker).
    يرجع (rs_score, coin_return, btc_return) أو (None, None, None).
    """
    coin_change = get_24h_change_pct(client, symbol)
    btc_change = get_24h_change_pct(client, _current_config["btc_symbol"])

    if coin_change is None or btc_change is None:
        return None, None, None

    coin_return = coin_change / 100.0
    btc_return = btc_change / 100.0

    # لو BTC ما نازل — ما بنشتري بهالاستراتيجية
    if btc_return >= 0:
        return None, coin_return, btc_return

    # ✅ العملة لازم تكون صاعدة فعلياً بنسبة >= الحد المطلوب (1%)
    rs_threshold = _current_config["rs_min_threshold_pct"] / 100.0
    if coin_return < rs_threshold:
        return None, coin_return, btc_return

    # Relative Strength: كم العملة أقوى من BTC نسبياً
    rs_score = ((1 + coin_return) / (1 + btc_return)) - 1

    return rs_score, coin_return, btc_return


def get_inverse_indicators(client, symbol, interval=Client.KLINE_INTERVAL_30MINUTE):
    """
    يجلب المؤشرات التقنية للعملة على فريم الدخول (30 دقيقة افتراضياً).
    يرجع dict أو None.
    """
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=100)
        cfg = get_config()
        min_required = max(20, cfg["volume_ma_period"]) + 3
        if not klines or len(klines) < min_required:
            return None
        closes = pd.Series([float(k[4]) for k in klines])
        highs = pd.Series([float(k[2]) for k in klines])
        lows = pd.Series([float(k[3]) for k in klines])
        volumes = pd.Series([float(k[5]) for k in klines])

        # نعتمد آخر شمعة مغلقة فقط، والشمعة التي قبلها للمقارنة.
        # هذا يمنع دخولاً بسبب إشارة تختفي قبل إغلاق الشمعة الحالية.
        closed_idx = -2
        previous_idx = -3

        # RSI
        rsi_series = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
        rsi = rsi_series.iloc[closed_idx]
        rsi_prev = rsi_series.iloc[previous_idx]

        # StochRSI
        stoch = ta.momentum.StochRSIIndicator(close=closes, window=14, smooth1=3, smooth2=3)
        k_series = stoch.stochrsi_k() * 100
        d_series = stoch.stochrsi_d() * 100
        k_line = k_series.iloc[closed_idx]
        d_line = d_series.iloc[closed_idx]
        k_prev = k_series.iloc[previous_idx]
        d_prev = d_series.iloc[previous_idx]

        # MA20
        ma20 = closes.rolling(window=20).mean().iloc[closed_idx]

        # ATR
        atr = ta.volatility.AverageTrueRange(
            high=highs, low=lows, close=closes, window=14
        ).average_true_range().iloc[closed_idx]

        # Volume
        vol_ma = volumes.rolling(window=cfg["volume_ma_period"]).mean().iloc[closed_idx]
        vol_ratio = volumes.iloc[closed_idx] / vol_ma if vol_ma > 0 else 0

        price = float(closes.iloc[closed_idx])
        price_prev = float(closes.iloc[previous_idx])

        return {
            "rsi": round(float(rsi), 2) if not pd.isna(rsi) else None,
            "rsi_prev": round(float(rsi_prev), 2) if not pd.isna(rsi_prev) else None,
            "stoch_k": round(float(k_line), 2) if not pd.isna(k_line) else None,
            "stoch_d": round(float(d_line), 2) if not pd.isna(d_line) else None,
            "stoch_k_prev": round(float(k_prev), 2) if not pd.isna(k_prev) else None,
            "stoch_d_prev": round(float(d_prev), 2) if not pd.isna(d_prev) else None,
            "ma20": round(float(ma20), 8) if not pd.isna(ma20) else None,
            "atr": float(atr) if not pd.isna(atr) and atr > 0 else None,
            "vol_ratio": round(float(vol_ratio), 2),
            "price": price,
            "price_prev": price_prev,
        }

    except Exception:
        return None


def check_inverse_btc(client, symbol):
    """
    الفحص الرئيسي لاستراتيجية Inverse BTC.
    يرجع dict بالبيانات لو فيه إشارة شراء، أو None لو مافي.
    """
    cfg = get_config()

    # ── الخطوة 1: BTC لازم يكون نازل 1% أو أكثر (24 ساعة) ──
    btc_decline, is_btc_declining = get_btc_decline(client)
    if not is_btc_declining or btc_decline is None:
        return None

    # ── الخطوة 2: العملة لازم تكون صاعدة 1% أو أكثر ──
    rs_score, coin_return, btc_return = get_relative_strength(client, symbol)
    if coin_return is None or btc_return is None:
        return None

    # ✅ إصلاح خلل: rs_score يرجع None لو BTC مو نازل فعلياً أو العملة ما صعدت كفاية
    # (حتى لو coin_return/btc_return مليانين) — لازم نتأكد منه هون قبل ما نكمل،
    # وإلا العملية {rs_score*100} بآخر الدالة تنكسر بـ TypeError على قيمة None
    if rs_score is None:
        return None

    # ── الخطوة 3: المؤشرات التقنية ──
    ind = get_inverse_indicators(client, symbol)
    if ind is None:
        return None

    # ندخل فقط بعد ارتداد RSI صعوداً داخل منطقة دخول منضبطة.
    if (
        ind["rsi"] is None or ind["rsi_prev"] is None
        or not (cfg["rsi_rebound_min"] <= ind["rsi"] <= cfg["rsi_max_for_entry"])
        or ind["rsi"] <= ind["rsi_prev"]
    ):
        return None

    # تأكيد ارتداد: K كان بمنطقة التشبع، ثم عبر D وصعد فوق مستوى الارتداد.
    if (
        ind["stoch_k"] is None or ind["stoch_d"] is None
        or ind["stoch_k_prev"] is None or ind["stoch_d_prev"] is None
        or ind["stoch_k_prev"] > cfg["stoch_k_max_for_entry"]
        or ind["stoch_k"] < cfg["stoch_k_rebound_min"]
        or ind["stoch_k"] <= ind["stoch_d"]
        or ind["stoch_k_prev"] > ind["stoch_d_prev"]
        or ind["stoch_k"] <= ind["stoch_k_prev"]
    ):
        return None

    # فوليوم أعلى من المتوسط
    if ind["vol_ratio"] < cfg["min_volume_ratio"]:
        return None

    # ✅ تأكيد الارتداد: الشمعة الأخيرة المغلقة لازم تكون صاعدة
    # (السعر الحالي أعلى من السعر السابق) — هذا يضمن إن العملة بدأت ترتد فعلياً
    if ind["price"] <= ind["price_prev"]:
        return None

    # لا نشتري عملة ما زالت بعيدة تحت متوسطها؛ نسمح بهامش صغير فقط.
    if ind["ma20"] is None or ind["price"] < ind["ma20"] * (1 - cfg["max_price_below_ma20_pct"] / 100):
        return None

    # ── كل الشروط تحققت ──
    coin_name = symbol.replace("USDT", "")
    rs_display = f"{rs_score*100:.1f}" if rs_score is not None else "؟"   # ✅ حماية إضافية احتياطية
    return {
        "price": ind["price"],
        "ma20": ind["ma20"],
        "atr": ind["atr"],
        "rsi": ind["rsi"],
        "stoch_k": ind["stoch_k"],
        "stoch_d": ind["stoch_d"],
        "vol_ratio": ind["vol_ratio"],
        "btc_decline_pct": round(abs(btc_decline) * 100, 2),
        "coin_return_pct": round(coin_return * 100, 2),
        "btc_return_pct": round(btc_return * 100, 2),
        "rs_score_pct": round(rs_score * 100, 2) if rs_score is not None else None,
        "signal_info": (
            f"🔄 <b>Inverse BTC — {coin_name}</b>\n"
            f"📉 BTC نازل {abs(btc_decline)*100:.1f}% (24 ساعة) | العملة صاعدة: +{coin_return*100:.1f}%\n"
            f"💪 قوة نسبية: +{rs_display}% (أقوى من BTC)\n"
            f"📊 ارتداد مؤكد — RSI: {ind['rsi_prev']} → {ind['rsi']} | Stoch K: {ind['stoch_k_prev']} → {ind['stoch_k']}\n"
            f"📈 فوليوم: {ind['vol_ratio']:.1f}× المتوسط"
        ),
    }
