"""
🧠 Market Regime Detector
=========================
يصنّف حالة السوق (BULL / BEAR / SIDEWAYS) من اتجاه BTC على فريم 4 ساعات
(السعر مقابل MA50 بهامش 2%)، لاستخدامها مع ATRGuard بذاكرة العملات
وبتسمية حالة السوق بالصفقات.

⚠️ التبديل التلقائي بين الاستراتيجيات انحذف نهائياً (بطلب المستخدم) —
هذا الملف بس بيرجع تصنيف حالة السوق، ما بيقرر أي استراتيجية.
"""

import pandas as pd
from binance.client import Client


class MarketRegimeDetector:
    def __init__(
        self,
        client,
        symbol="BTCUSDT",
        trend_interval=Client.KLINE_INTERVAL_4HOUR,
        trend_ma_length=50,
        trend_margin_pct=0.02,        # 2% هامش فوق MA50 عشان يعتبر "ترند واضح"
    ):
        self.client = client
        self.symbol = symbol
        self.trend_interval = trend_interval
        self.trend_ma_length = trend_ma_length
        self.trend_margin_pct = trend_margin_pct



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
    # ──────────────────────────────────────────────
    # 🧩 تصنيف مبسّط لصمام أمان ATR (coin_memory.ATRGuard)
    # يعيد "BULL" / "BEAR" / "SIDEWAYS" بالاعتماد على تحليل الترند
    # (4 ساعات، MA50).
    # ──────────────────────────────────────────────
    def get_regime_label(self):
        """
        يرجع str من {"BULL", "BEAR", "SIDEWAYS"} لاستخدامه مباشرة مع
        coin_memory.ATRGuard.compute_stop_loss(market_regime=...).
        - BULL     : اتجاه صاعد واضح (السعر فوق MA50 بهامش كافٍ)
        - BEAR     : اتجاه هابط واضح (تحت MA50 بهامش سلبي واضح)
        - SIDEWAYS : غير ذلك (ترند غير واضح، أو تعذر التحليل)
        """
        is_uptrend, trend_margin = self._get_trend_state()
        if is_uptrend is None or trend_margin is None:
            return "SIDEWAYS"   # تعذر التحليل — نتعامل بحذر افتراضي

        if is_uptrend and trend_margin >= self.trend_margin_pct:
            return "BULL"
        if (not is_uptrend) and abs(trend_margin) >= self.trend_margin_pct:
            return "BEAR"
        return "SIDEWAYS"
