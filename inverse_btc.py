"""
🔄 Inverse BTC Strategy
========================
استراتيجية رابعة مستقلة — تشتري العملات اللي بتقاوم نزول BTC.

المنطق:
1. BTC لازم يكون نازل بقوة (آخر 6 شموع على فريم 1 ساعة)
2. العملة لازم تكون أقوى من BTC بفارق واضح (Relative Strength)
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
    "btc_interval": Client.KLINE_INTERVAL_1HOUR,      # ✅ فريم 1 ساعة
    "lookback_candles": 6,                             # ✅ 6 شموع
    "btc_decline_threshold_pct": 3.0,                  # BTC لازم ينزل 3% على الأقل
    "rs_min_threshold_pct": 5.0,                       # العملة لازم تكون أقوى من BTC بـ 5%
    "rsi_max_for_entry": 40,                           # RSI لازم يكون تحت 40
    "stoch_k_max_for_entry": 30,                       # StochRSI K تحت 30
    "volume_ma_period": 20,                            # فترة متوسط الفوليوم
    "min_volume_ratio": 1.2,                           # الفوليوم الحالي 1.2× المتوسط
}

# الكاشف بيستدعي load_settings() وبيمرر الإعدادات هون
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


def _calculate_returns(closes, lookback):
    """يحسب نسبة التغيّر خلال آخر `lookback` شموع مغلقة."""
    if len(closes) < lookback + 1:
        return None
    old_price = closes.iloc[-lookback - 1]
    new_price = closes.iloc[-2]  # آخر شمعة مغلقة
    if old_price <= 0:
        return None
    return (new_price - old_price) / old_price


# ── الفحص الرئيسي ───────────────────────────────────────────────────────

def get_btc_decline(client):
    """
    يحسب نسبة نزول BTC خلال آخر N شمعة.
    يرجع (decline_pct, is_declining) أو (None, False).
    """
    cfg = get_config()
    klines = _get_klines_safe(
        client, cfg["btc_symbol"], cfg["btc_interval"], cfg["lookback_candles"] + 5
    )
    if klines is None:
        return None, False

    closes = pd.Series([float(k[4]) for k in klines])
    decline_pct = _calculate_returns(closes, cfg["lookback_candles"])

    if decline_pct is None:
        return None, False

    threshold = cfg["btc_decline_threshold_pct"] / 100.0
    is_declining = decline_pct <= -threshold  # سالب وتحت الحد

    return decline_pct, is_declining


def get_relative_strength(client, symbol):
    """
    يحسب قوة العملة النسبية مقابل BTC خلال نفس الفترة.
    يرجع (rs_score, coin_return, btc_return) أو (None, None, None).
    """
    cfg = get_config()
    limit = cfg["lookback_candles"] + 5

    coin_klines = _get_klines_safe(client, symbol, cfg["btc_interval"], limit)
    btc_klines = _get_klines_safe(client, cfg["btc_symbol"], cfg["btc_interval"], limit)

    if coin_klines is None or btc_klines is None:
        return None, None, None

    coin_closes = pd.Series([float(k[4]) for k in coin_klines])
    btc_closes = pd.Series([float(k[4]) for k in btc_klines])

    coin_return = _calculate_returns(coin_closes, cfg["lookback_candles"])
    btc_return = _calculate_returns(btc_closes, cfg["lookback_candles"])

    if coin_return is None or btc_return is None:
        return None, coin_return, btc_return

    # لو BTC ما نازل — ما بنشتري بهالاستراتيجية
    if btc_return >= 0:
        return None, coin_return, btc_return

    # Relative Strength
    # لو العملة نازلة أقل من BTC → RS إيجابي
    # لو العملة صاعدة وBTC نازل → RS إيجابي بقوة
    rs_score = ((1 + coin_return) / (1 + btc_return)) - 1

    return rs_score, coin_return, btc_return


def get_inverse_indicators(client, symbol, interval=Client.KLINE_INTERVAL_30MINUTE):
    """
    يجلب المؤشرات التقنية للعملة على فريم الدخول (30 دقيقة افتراضياً).
    يرجع dict أو None.
    """
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=100)
        closes = pd.Series([float(k[4]) for k in klines])
        highs = pd.Series([float(k[2]) for k in klines])
        lows = pd.Series([float(k[3]) for k in klines])
        volumes = pd.Series([float(k[5]) for k in klines])

        # RSI
        rsi = ta.momentum.RSIIndicator(close=closes, window=14).rsi().iloc[-1]

        # StochRSI
        stoch = ta.momentum.StochRSIIndicator(close=closes, window=14, smooth1=3, smooth2=3)
        k_line = stoch.stochrsi_k().iloc[-1] * 100
        d_line = stoch.stochrsi_d().iloc[-1] * 100

        # MA20
        ma20 = closes.rolling(window=20).mean().iloc[-1]

        # ATR
        atr = ta.volatility.AverageTrueRange(
            high=highs, low=lows, close=closes, window=14
        ).average_true_range().iloc[-1]

        # Volume
        cfg = get_config()
        vol_ma = volumes.rolling(window=cfg["volume_ma_period"]).mean().iloc[-1]
        vol_ratio = volumes.iloc[-1] / vol_ma if vol_ma > 0 else 0

        price = float(closes.iloc[-1])

        return {
            "rsi": round(float(rsi), 2) if not pd.isna(rsi) else None,
            "stoch_k": round(float(k_line), 2) if not pd.isna(k_line) else None,
            "stoch_d": round(float(d_line), 2) if not pd.isna(d_line) else None,
            "ma20": round(float(ma20), 8) if not pd.isna(ma20) else None,
            "atr": float(atr) if not pd.isna(atr) and atr > 0 else None,
            "vol_ratio": round(float(vol_ratio), 2),
            "price": price,
        }

    except Exception:
        return None


def check_inverse_btc(client, symbol):
    """
    الفحص الرئيسي لاستراتيجية Inverse BTC.
    يرجع dict بالبيانات لو فيه إشارة شراء، أو None لو مافي.
    """
    cfg = get_config()

    # ── الخطوة 1: BTC لازم يكون نازل ──
    btc_decline, is_btc_declining = get_btc_decline(client)
    if not is_btc_declining or btc_decline is None:
        return None

    # ── الخطوة 2: العملة لازم تكون صاعدة فعلياً (مو بس أداء نسبي أفضل من BTC) ──
    rs_score, coin_return, btc_return = get_relative_strength(client, symbol)
    if coin_return is None or btc_return is None:
        return None

    rs_threshold = cfg["rs_min_threshold_pct"] / 100.0
    # العملة لازم تكون صاعدة فعلياً بنسبة >= الحد المطلوب (مو بس أبطأ نزولاً من BTC)
    if coin_return < rs_threshold:
        return None

    # ── الخطوة 3: المؤشرات التقنية ──
    ind = get_inverse_indicators(client, symbol)
    if ind is None:
        return None

    # RSI لازم يكون تحت الحد
    if ind["rsi"] is None or ind["rsi"] >= cfg["rsi_max_for_entry"]:
        return None

    # StochRSI لازم يكون بمنطقة تشبع بيعي
    if ind["stoch_k"] is None or ind["stoch_k"] >= cfg["stoch_k_max_for_entry"]:
        return None

    # فوليوم أعلى من المتوسط
    if ind["vol_ratio"] < cfg["min_volume_ratio"]:
        return None

    # ── كل الشروط تحققت ──
    coin_name = symbol.replace("USDT", "")
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
            f"📉 BTC نازل {abs(btc_decline)*100:.1f}% | العملة صاعدة: +{coin_return*100:.1f}%\n"
            f"📊 RSI: {ind['rsi']} | Stoch K: {ind['stoch_k']}\n"
            f"📈 فوليوم: {ind['vol_ratio']:.1f}× المتوسط"
        ),
    }
