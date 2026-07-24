"""
🧠 Market Regime Detector
=========================
يحلل حالة السوق العامة (اتجاه + تذبذب + مزاج عام) بناءً على BTC ومؤشر الخوف
والجشع، ويقرر أنسب استراتيجية:

- ترند واضح  (BTC فوق MA50 على فريم 4 ساعات بهامش واضح)  → trend_stoch
- جانبي + تذبذب عالي (ATR% مرتفع على فريم الساعة)         → stoch_rsi
- جانبي + هادئ                                            → rsi

مؤشر الخوف والجشع (Fear & Greed) يُستخدم كعامل حذر إضافي: وقت مزاج متطرف
(خوف شديد أو جشع شديد)، نطلب هامش ترند أوضح (ضعف الطبيعي) قبل ما نثق فيه
ونحوّل لـ trend_stoch — لأن الأسواق بهالحالات أكثر عرضة لانعكاسات حادة.

فيه "فترة تبريد" بين كل تبديل وتاني لتجنب الرفرفة (Flip-flopping)
لما يكون السوق عالحافة بالضبط بين حالتين.

هذا الملف مستقل تماماً عن bot.py:
- ما يستورد أي شي من bot.py
- ما يلمس أي متغير عام (current_strategy, save_settings, send_telegram...)
- بس يرجع قرار (استراتيجية + سبب)، وbot.py هو اللي يطبقه ويبعت التنبيه

الاستخدام من bot.py:
    from market_regime import MarketRegimeDetector

    detector = MarketRegimeDetector(client)          # مرة وحدة عند بدء التشغيل

    strategy, reason, switched = detector.check()     # بكل دورة فحص
    if switched:
        current_strategy = strategy
        send_telegram(f"🔄 تبديل تلقائي → {strategy}\\n{reason}")
"""

import time
import pandas as pd
import ta
import requests
from binance.client import Client


class MarketRegimeDetector:
    def __init__(
        self,
        client,
        symbol="BTCUSDT",
        trend_interval=Client.KLINE_INTERVAL_4HOUR,
        trend_ma_length=50,
        trend_margin_pct=0.02,        # 2% هامش فوق MA50 عشان يعتبر "ترند واضح"
        volatility_interval=Client.KLINE_INTERVAL_1HOUR,
        atr_period=14,
        high_volatility_pct=0.015,    # 1.5% (ATR/السعر) يعتبر تذبذب عالي
        cooldown_minutes=45,
        fear_greed_extreme_low=20,    # تحت هذا الرقم = خوف شديد
        fear_greed_extreme_high=80,   # فوق هذا الرقم = جشع شديد
        fear_greed_cache_minutes=10,  # نكاش قيمة المؤشر عشان ما نضرب الـ API كل فحص
    ):
        self.client = client
        self.symbol = symbol
        self.trend_interval = trend_interval
        self.trend_ma_length = trend_ma_length
        self.trend_margin_pct = trend_margin_pct
        self.volatility_interval = volatility_interval
        self.atr_period = atr_period
        self.high_volatility_pct = high_volatility_pct
        self.cooldown_minutes = cooldown_minutes
        self.fear_greed_extreme_low = fear_greed_extreme_low
        self.fear_greed_extreme_high = fear_greed_extreme_high
        self.fear_greed_cache_minutes = fear_greed_cache_minutes

        # 🔄 إعدادات Inverse BTC للتبديل التلقائي
        self.inverse_btc_enabled = False   # يُحدد من bot.py
        self.inverse_btc_decline_threshold = 0.03  # 3% نزول BTC

        self.current_strategy = None   # آخر استراتيجية قررها الكاشف
        self.last_switch_time = 0      # timestamp لآخر تبديل فعلي طُبّق

        self._fg_cache_value = None    # آخر قيمة Fear&Greed معروفة
        self._fg_cache_time = 0        # وقت آخر تحديث للكاش

    # ──────────────────────────────────────────────
    # تحليل الاتجاه (Trend)
    # ──────────────────────────────────────────────
    def _get_trend_state(self):
        """يرجع (is_uptrend: bool|None, margin_pct: float|None) بناءً على آخر شمعة مغلقة"""
        try:
            klines = self.client.get_klines(
                symbol=self.symbol,
                interval=self.trend_interval,
                limit=self.trend_ma_length + 5,
            )
            if not klines or len(klines) < self.trend_ma_length:
                return None, None

            closes = pd.Series([float(k[4]) for k in klines])
            ma = closes.rolling(window=self.trend_ma_length).mean()

            last_close = closes.iloc[-2]   # آخر شمعة مغلقة (تجنب شمعة لسا مفتوحة)
            last_ma = ma.iloc[-2]
            if pd.isna(last_ma) or last_ma == 0:
                return None, None

            margin_pct = (last_close - last_ma) / last_ma
            return margin_pct > 0, margin_pct
        except Exception:
            return None, None

    # ──────────────────────────────────────────────
    # تحليل التذبذب (Volatility)
    # ──────────────────────────────────────────────
    def _get_volatility_pct(self):
        """يرجع نسبة ATR الحالية إلى السعر (ATR%)، أو None لو فشل الحساب"""
        try:
            klines = self.client.get_klines(
                symbol=self.symbol,
                interval=self.volatility_interval,
                limit=self.atr_period + 20,
            )
            if not klines or len(klines) < self.atr_period + 1:
                return None

            highs = pd.Series([float(k[2]) for k in klines])
            lows = pd.Series([float(k[3]) for k in klines])
            closes = pd.Series([float(k[4]) for k in klines])

            atr = ta.volatility.AverageTrueRange(
                high=highs, low=lows, close=closes, window=self.atr_period
            ).average_true_range().iloc[-1]

            price = closes.iloc[-1]
            if pd.isna(atr) or price == 0:
                return None

            return atr / price
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # مؤشر الخوف والجشع (Fear & Greed) — نفس المصدر المستخدم بالتطبيق
    # ──────────────────────────────────────────────
    def _get_fear_greed(self):
        """يرجع قيمة المؤشر (0-100) أو None لو تعذر الجلب. مكاش لتقليل عدد الطلبات."""
        now = time.time()
        if self._fg_cache_value is not None and (now - self._fg_cache_time) < (self.fear_greed_cache_minutes * 60):
            return self._fg_cache_value
        try:
            resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            data = resp.json()
            value = int(data["data"][0]["value"])
            self._fg_cache_value = value
            self._fg_cache_time = now
            return value
        except Exception:
            return self._fg_cache_value   # نرجع آخر قيمة معروفة لو الطلب فشل، أفضل من ولا شي

    # ──────────────────────────────────────────────
    # 🔄 فحص نزول BTC (لاستراتيجية Inverse BTC)
    # ──────────────────────────────────────────────
    def _get_btc_decline_for_inverse(self):
        """يحسب نسبة نزول BTC خلال آخر 6 شموع على فريم 1 ساعة."""
        try:
            klines = self.client.get_klines(
                symbol="BTCUSDT",
                interval=Client.KLINE_INTERVAL_1HOUR,
                limit=10,
            )
            if not klines or len(klines) < 7:
                return None
            closes = pd.Series([float(k[4]) for k in klines])
            old_price = closes.iloc[-7]   # قبل 6 شموع
            new_price = closes.iloc[-2]   # آخر شمعة مغلقة
            if old_price <= 0:
                return None
            return (new_price - old_price) / old_price
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # القرار الخام (بدون فترة تبريد)
    # ──────────────────────────────────────────────
    def decide(self):
        """
        يحسب أنسب استراتيجية حسب حالة السوق الحالية فقط (بدون تطبيق فترة التبريد).
        يرجع (strategy: str|None, reason: str)
        """
        is_uptrend, trend_margin = self._get_trend_state()
        volatility_pct = self._get_volatility_pct()
        fear_greed = self._get_fear_greed()

        if is_uptrend is None or volatility_pct is None:
            return None, "تعذر تحليل حالة السوق (بيانات غير كافية أو خطأ اتصال)"

        fg_suffix = f" | مزاج السوق: {fear_greed}/100" if fear_greed is not None else ""
        is_extreme_sentiment = fear_greed is not None and (
            fear_greed <= self.fear_greed_extreme_low or fear_greed >= self.fear_greed_extreme_high
        )
        # وقت مزاج متطرف (خوف شديد أو جشع شديد)، الأسواق أكثر عرضة لانعكاسات حادة —
        # نطلب هامش ترند أوضح (ضعف الطبيعي) قبل ما نثق فيه ونحوّل لـ trend_stoch
        effective_trend_margin = self.trend_margin_pct * (2 if is_extreme_sentiment else 1)

        # 🔄 فحص Inverse BTC (ضمن التنقل التلقائي): لو BTC نازل بقوة → نحوّل لـ inverse_btc
        # هذا القرار تلقائي — مش يدوي. لو المستخدم ما بدّه inverse_btc، يطفي التبديل التلقائي
        btc_decline = self._get_btc_decline_for_inverse()
        if btc_decline is not None and btc_decline <= -self.inverse_btc_decline_threshold:
            reason = (
                f"BTC نازل {abs(btc_decline)*100:.1f}% (آخر 6 شموع على 1 ساعة) — "
                f"البحث عن عملات مقاومة{fg_suffix}"
            )
            return "inverse_btc", reason

        # ترند واضح: BTC فوق MA50 (فريم 4 ساعات) بهامش أكبر من الحد المطلوب
        if is_uptrend and trend_margin >= effective_trend_margin:
            reason = f"BTC فوق MA50 (4 ساعات) بهامش {trend_margin*100:.2f}%{fg_suffix}"
            return "trend_stoch", reason

        # جانبي (لا يوجد ترند صعودي واضح بهامش كافٍ)
        if volatility_pct >= self.high_volatility_pct:
            reason = f"سوق جانبي + تذبذب عالٍ (ATR {volatility_pct*100:.2f}%){fg_suffix}"
            return "stoch_rsi", reason

        reason = f"سوق جانبي + هادئ (ATR {volatility_pct*100:.2f}%){fg_suffix}"
        return "rsi", reason

    # ──────────────────────────────────────────────
    # الفحص الرسمي (يطبّق فترة التبريد)
    # ──────────────────────────────────────────────
    def check(self):
        """
        يفحص حالة السوق ويقرر هل لازم نبدل الاستراتيجية الآن، مع احترام فترة التبريد.
        يرجع (strategy, reason, switched: bool)
        - strategy : الاستراتيجية الفعّالة الآن (سواء تبدلت أو ضلت متل ما هي)
        - reason   : سبب القرار أو سبب تعذر التحليل
        - switched : True فقط لما يصير تبديل فعلي بهذا الاستدعاء تحديداً
        """
        proposed_strategy, reason = self.decide()

        if proposed_strategy is None:
            # تعذر التحليل — نضل على آخر استراتيجية معروفة بدون أي تغيير
            return self.current_strategy, reason, False

        now = time.time()

        # أول فحص من عمر الكاشف: نطبق القرار مباشرة بدون فترة تبريد
        if self.current_strategy is None:
            self.current_strategy = proposed_strategy
            self.last_switch_time = now
            return proposed_strategy, reason, True

        # الاقتراح نفس الاستراتيجية الحالية — لا شي يتغير
        if proposed_strategy == self.current_strategy:
            return self.current_strategy, reason, False

        # اقتراح مختلف — نتأكد من انتهاء فترة التبريد قبل التبديل
        if (now - self.last_switch_time) < (self.cooldown_minutes * 60):
            return self.current_strategy, reason, False   # لسا بفترة تبريد، نتجاهل الاقتراح مؤقتاً

        # فترة التبريد خلصت — نطبق التبديل فعلياً
        self.current_strategy = proposed_strategy
        self.last_switch_time = now
        return proposed_strategy, reason, True

    def set_inverse_btc_enabled(self, enabled, decline_threshold=0.03):
        """
        يفعّل/يطفي استراتيجية Inverse BTC بالتبديل التلقائي.
        يُستدعى من bot.py لما المستخدم يغيّر الإعداد.
        """
        self.inverse_btc_enabled = enabled
        self.inverse_btc_decline_threshold = decline_threshold
