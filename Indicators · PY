"""
📐 Indicators
=============
دوال حساب رياضية بحتة، مستقلة تماماً عن bot.py — بدون أي حالة (State)،
بدون أي استدعاء API، بس تستقبل بيانات (pandas Series) وترجع رقم أو مجموعة أرقام.

هذا يخليها سهلة الاختبار لحالها، وقابلة لإعادة الاستخدام بأي استراتيجية مستقبلية
بدون ما تعتمد على أي متغير عام من bot.py.

الاستخدام من bot.py:
    from indicators import calculate_vwap, calculate_bollinger_bands, calculate_momentum_score
"""

import pandas as pd


def calculate_vwap(highs: pd.Series, lows: pd.Series, closes: pd.Series, volumes: pd.Series, period: int = 20):
    """
    يحسب VWAP (متوسط السعر المرجّح بالحجم) على نافذة متحركة من آخر `period` شمعة.
    يرجع القيمة الحالية (float) أو None لو تعذر الحساب.
    ملاحظة: هذا VWAP "متحرك" (Rolling) مو "يومي" (Session) — مناسب أكثر لسوق يتداول 24/7.
    """
    try:
        typical_price = (highs + lows + closes) / 3
        pv = typical_price * volumes
        vwap_series = pv.rolling(window=period).sum() / volumes.rolling(window=period).sum()
        value = vwap_series.iloc[-1]
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def calculate_bollinger_bands(closes: pd.Series, period: int = 20, std_dev: float = 2.0):
    """
    يحسب نطاقات بولينجر (Bollinger Bands).
    يرجع (upper, middle, lower) كـ float، أو (None, None, None) لو تعذر الحساب.
    """
    try:
        sma = closes.rolling(window=period).mean()
        std = closes.rolling(window=period).std()
        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)

        u, m, l = upper.iloc[-1], sma.iloc[-1], lower.iloc[-1]
        if pd.isna(u) or pd.isna(m) or pd.isna(l):
            return None, None, None
        return float(u), float(m), float(l)
    except Exception:
        return None, None, None


def calculate_momentum_score(closes: pd.Series, volumes: pd.Series, volume_period: int = 20, price_lookback: int = 5):
    """
    يحسب "درجة زخم" عامة لمرشح شراء — تُستخدم لترتيب أولوية الشراء لما يكون
    فيه أكثر من مرشح بنفس دورة الفحص (الأقوى يُشترى أولاً).

    الفكرة: زخم قوي = فوليوم أعلى من المعتاد + حركة سعر واضحة أخر عدة شموع.
    القيمة نسبية (مو نسبة مئوية أو وحدة قياس معينة) — تُستخدم فقط للمقارنة والترتيب
    بين المرشحين بنفس اللحظة، مو كقيمة مطلقة لها معنى منفرد.

    يرجع float (كلما زاد = زخم أقوى)، أو 0.0 لو تعذر الحساب.
    """
    try:
        if len(volumes) < volume_period or len(closes) <= price_lookback:
            return 0.0

        vol_ma = volumes.rolling(window=volume_period).mean().iloc[-1]
        if pd.isna(vol_ma) or vol_ma <= 0:
            return 0.0
        volume_ratio = volumes.iloc[-1] / vol_ma

        old_price = closes.iloc[-1 - price_lookback]
        current_price = closes.iloc[-1]
        if old_price <= 0:
            return 0.0
        price_change_pct = abs((current_price - old_price) / old_price)

        # فوليوم أعلى من المعتاد × حركة سعر أوضح = درجة أعلى
        score = volume_ratio * (1 + price_change_pct * 10)
        return round(float(score), 4)
    except Exception:
        return 0.0
