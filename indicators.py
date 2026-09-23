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


def calculate_mfi(highs: pd.Series, lows: pd.Series, closes: pd.Series, volumes: pd.Series, period: int = 14):
    """
    يحسب Money Flow Index (MFI) — مؤشر زخم يدمج السعر مع الفوليوم (شبيه بـ RSI
    بس واخذ بعين الاعتبار حجم التداول). نطاقه 0-100:
    - أعلى من 80  → تشبع شرائي (خطر ارتداد هبوطي قريب)
    - أقل من 20   → تشبع بيعي
    يرجع القيمة الحالية (float) أو None لو تعذر الحساب.
    """
    try:
        import ta
        mfi_series = ta.volume.MFIIndicator(
            high=highs, low=lows, close=closes, volume=volumes, window=period
        ).money_flow_index()
        value = mfi_series.iloc[-1]
        if pd.isna(value):
            return None
        return round(float(value), 2)
    except Exception:
        return None


def calculate_adx(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14):
    """
    يحسب ADX (Average Directional Index) — قوة الاتجاه الحالي بغض النظر عن
    اتجاهه (صاعد أو هابط). عادةً:
    - أقل من 15-20 → سوق عرضي بدون اتجاه واضح (إشارات الشراء أكثر عرضة للكذب)
    - أعلى من 25   → اتجاه حقيقي وواضح
    يرجع القيمة الحالية (float) أو None لو تعذر الحساب.
    """
    try:
        import ta
        adx_series = ta.trend.ADXIndicator(high=highs, low=lows, close=closes, window=period).adx()
        value = adx_series.iloc[-1]
        if pd.isna(value):
            return None
        return round(float(value), 2)
    except Exception:
        return None


def calculate_beta(coin_closes: pd.Series, btc_closes: pd.Series):
    """
    يحسب Beta: معامل تقلب العملة النسبي مقابل BTC (نفس مفهوم Beta بالأسهم).
    Beta > 1  → العملة تتحرك أعنف من BTC (لو BTC طلع 1%، هاي تطلع أكثر)
    Beta < 1  → العملة أهدأ من BTC (تتحرك أقل)
    Beta ~ 1  → تتحرك تقريباً بنفس وتيرة BTC

    يستقبل سلسلتين متطابقتين بالطول (نفس الفريم الزمني ونفس عدد الشموع)،
    ويحسب العائد الدوري (% تغيّر كل شمعة) لكل وحدة، بعدين:
        Beta = التباين المشترك(عائد العملة, عائد BTC) / تباين(عائد BTC)

    يرجع float أو None لو تعذر الحساب (بيانات ناقصة، أو BTC بدون أي تذبذب).
    """
    try:
        if len(coin_closes) != len(btc_closes) or len(coin_closes) < 10:
            return None

        coin_returns = coin_closes.pct_change().dropna()
        btc_returns  = btc_closes.pct_change().dropna()

        # نتأكد إن الطول متطابق بعد إسقاط أول قيمة (NaN) من pct_change
        min_len = min(len(coin_returns), len(btc_returns))
        if min_len < 5:
            return None
        coin_returns = coin_returns.iloc[-min_len:]
        btc_returns  = btc_returns.iloc[-min_len:]

        btc_variance = btc_returns.var()
        if pd.isna(btc_variance) or btc_variance == 0:
            return None

        covariance = coin_returns.cov(btc_returns)
        if pd.isna(covariance):
            return None

        beta = covariance / btc_variance
        return round(float(beta), 3)
    except Exception:
        return None


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


def calculate_support_resistance(highs: pd.Series, lows: pd.Series, closes: pd.Series,
                                  swing_window: int = 3, cluster_pct: float = 0.015, max_levels: int = 3):
    """
    يكتشف مستويات الدعم والمقاومة آلياً من القمم والقيعان المحلية (Swing
    Highs/Lows) — مش خطوط مرسومة يدوياً.

    الطريقة:
    1) قمة/قاع محلي = شمعة أعلى/أدنى من swing_window شمعة قبلها وبعدها
    2) تجميع المستويات القريبة من بعض (ضمن cluster_pct) بمستوى واحد،
       وعدد مرات الارتداد عنه (touches) = قوته
    3) نرجع أقرب max_levels مستوى دعم (تحت السعر الحالي) وأقرب max_levels
       مقاومة (فوق السعر الحالي)، مرتبين بالقرب من السعر

    Returns:
        dict: {"current_price": float,
               "support": [{"level": float, "touches": int}, ...],
               "resistance": [{"level": float, "touches": int}, ...]}
        أو None لو البيانات غير كافية.
    """
    try:
        n = len(closes)
        if n < swing_window * 2 + 10:
            return None

        current_price = float(closes.iloc[-1])
        swing_highs, swing_lows = [], []

        for i in range(swing_window, n - swing_window):
            window_h = highs.iloc[i - swing_window:i + swing_window + 1]
            window_l = lows.iloc[i - swing_window:i + swing_window + 1]
            if highs.iloc[i] == window_h.max():
                swing_highs.append(float(highs.iloc[i]))
            if lows.iloc[i] == window_l.min():
                swing_lows.append(float(lows.iloc[i]))

        def cluster(points):
            """يجمع نقاط قريبة من بعض (ضمن cluster_pct) بمستوى واحد بعدد لمسات"""
            if not points:
                return []
            points = sorted(points)
            clusters = [[points[0]]]
            for p in points[1:]:
                if abs(p - clusters[-1][-1]) / clusters[-1][-1] <= cluster_pct:
                    clusters[-1].append(p)
                else:
                    clusters.append([p])
            return [{"level": round(sum(c) / len(c), 8), "touches": len(c)} for c in clusters]

        support_levels = [c for c in cluster(swing_lows) if c["level"] < current_price]
        resistance_levels = [c for c in cluster(swing_highs) if c["level"] > current_price]

        support_levels.sort(key=lambda c: current_price - c["level"])
        resistance_levels.sort(key=lambda c: c["level"] - current_price)

        return {
            "current_price": current_price,
            "support": support_levels[:max_levels],
            "resistance": resistance_levels[:max_levels],
        }
    except Exception:
        return None
