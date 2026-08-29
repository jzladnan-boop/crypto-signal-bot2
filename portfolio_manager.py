"""
🔐 Portfolio Manager — يمنع تضارب المحافظ بين الاستراتيجيات
================================================================
البوت عنده هلق 4 "مشترين" مستقلين ممكن يشتغلوا بنفس اللحظة على نفس الحساب:
  1) البوت الأساسي (حلقة الفحص الرئيسية بـ crypto_signal_bot.py)
  2) Trend+Stoch الموازية (trend_stoch_parallel.py)
  3) Squeeze Breakout (squeeze_breakout.py)
  4) Mean Reversion (mean_reversion_parallel.py)

المشكلة: لو اثنين منهم قرروا يشتروا نفس العملة بنفس الوقت (وارد فعلياً، لأنه
فيه تداخل بالعملات بين الاستراتيجيات الثلاث الموازية والبوت الأساسي)،
بيصير تضارب حقيقي — أخطرها إنه بعض دوال البيع صارت تبيع "كامل الرصيد
المتاح" (لتنظيف الغبار)، فلو استراتيجية باعت، ممكن تبيع رصيد استراتيجية
تانية معاها بالغلط.

الحل: سجل مشترك (Thread-safe) بيحجز كل عملة لمالك وحيد بأي لحظة. قبل أي
شراء، الاستراتيجية لازم "تحجز" العملة أولاً — لو محجوزة لمالك تاني، ما تشتري
وتكمل تدور على عملة تانية. وبعد البيع، لازم "تحرر" الحجز.

⚠️ هذا الملف مستقل تماماً (بدون أي استيراد من bot.py أو الاستراتيجيات) —
بس بيصير Singleton مشترك (نفس الـ Process، 3 Threads) عن طريق
get_portfolio_manager().
"""

import threading
import time


class PortfolioManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._claims = {}   # symbol -> {"owner": str, "claimed_at": float}

    def try_claim(self, symbol, owner):
        """
        يحاول يحجز العملة لهالمالك (owner: اسم الاستراتيجية، مثلاً "main_bot").
        يرجع True لو نجح الحجز (أو كانت محجوزة أصلاً لنفس المالك)،
        و False لو محجوزة لمالك تاني — بهاي الحالة ما لازم تشتري.
        """
        with self._lock:
            current = self._claims.get(symbol)
            if current is not None and current["owner"] != owner:
                return False
            self._claims[symbol] = {"owner": owner, "claimed_at": time.time()}
            return True

    def release(self, symbol, owner):
        """
        يحرر الحجز — بس لو نفس المالك يلي طالب التحرير (حماية إضافية،
        حتى ما تقدر استراتيجية تحرر حجز استراتيجية تانية بالغلط).
        """
        with self._lock:
            current = self._claims.get(symbol)
            if current is not None and current["owner"] == owner:
                del self._claims[symbol]

    def is_claimed_by_other(self, symbol, owner):
        with self._lock:
            current = self._claims.get(symbol)
            return current is not None and current["owner"] != owner

    def owner_of(self, symbol):
        with self._lock:
            current = self._claims.get(symbol)
            return current["owner"] if current else None

    def snapshot(self):
        """نسخة كاملة من الحجوزات الحالية — لعرضها بلوج/تيليغرام عند الحاجة."""
        with self._lock:
            return {sym: info["owner"] for sym, info in self._claims.items()}


# ──────────────────────────────────────────────
# Singleton مشترك — نفس الـ Process (3 Threads) بيستخدموا نفس النسخة
# ──────────────────────────────────────────────
_global_portfolio_manager = PortfolioManager()


def get_portfolio_manager():
    return _global_portfolio_manager
