"""
coin_memory.py
==========================================================
نظام الذاكرة التاريخية الذكي (Adaptive Memory System)
==========================================================

المكونات:
  1) CoinMemory        -> تخزين واسترجاع سجل الأداء لكل عملة (SQLite)
  2) CorrelationEngine  -> تصنيف العملات (متناغمة مع BTC / مقاومة عكسية / محايدة)
  3) SmartRanker        -> ترتيب العملات المرشحة + وضع Strict Mode للطوارئ
  4) ATRGuard           -> صمام أمان SL مرتبط بحالة السوق (ATR ديناميكي)

قاعدة البيانات تُنشأ تلقائيًا في نفس مجلد التشغيل باسم coin_memory.db
(يمكن تغيير المسار عبر db_path عند إنشاء الكائن).
"""

import sqlite3
import time
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Dict, Tuple


# ----------------------------------------------------------------------
# 1) تعريفات عامة
# ----------------------------------------------------------------------

class CorrelationType(str, Enum):
    TREND_FOLLOWER = "TREND_FOLLOWER"      # عملة متناغمة مع تسارع BTC
    INVERSE_STRENGTH = "INVERSE_STRENGTH"  # عملة تبرز/تقاوم وقت هبوط BTC
    NEUTRAL = "NEUTRAL"                    # لا يوجد ارتباط واضح بعد


class MarketRegime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"


@dataclass
class SymbolStats:
    symbol: str
    wins: int
    losses: int
    avg_slippage_pct: float
    total_pnl: float
    correlation_type: str
    correlation_score: float  # -1..1  (موجب = يتبع BTC / سالب = عكسي)
    last_updated: float

    @property
    def total_trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades


# ----------------------------------------------------------------------
# 2) CoinMemory - طبقة التخزين
# ----------------------------------------------------------------------

class CoinMemory:
    """
    مسؤولة فقط عن القراءة/الكتابة في قاعدة البيانات.
    كل صفقة تُغلق (ربح أو خسارة) تُسجَّل هنا، ويتم تحديث ملخص العملة تلقائيًا.
    """

    def __init__(self, db_path: str = "coin_memory.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        # check_same_thread=False لأن أغلب البوتات تستدعي هذا من ثريدات متعددة
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # ⬅️ WAL Mode: يسمح بقراءة وكتابة متزامنة من كذا Thread بنفس الوقت (البوت
        # الأساسي + 3 استراتيجيات مستقلة كلهم ممكن يكتبوا بنفس اللحظة) بدون قفل
        # كامل لقاعدة البيانات — كان أكبر خطر تضارب كتابة حقيقي بالنظام الحالي.
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    is_win INTEGER NOT NULL,
                    pnl REAL NOT NULL,
                    slippage_pct REAL NOT NULL,
                    strategy TEXT NOT NULL DEFAULT 'unknown'
                )
            """)
            # ⬅️ ترحيل (Migration) لقواعد بيانات قديمة أُنشئت قبل عمود strategy —
            # لو الجدول موجود أصلاً بدون هالعمود، نضيفه. لو موجود أصلاً (قاعدة
            # جديدة أو ترحيل سابق)، sqlite بيرفض الأمر بخطأ "duplicate column"
            # ونتجاهله بهدوء (هذا هو السلوك المتوقع، مش خلل).
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN strategy TEXT NOT NULL DEFAULT 'unknown'")
            except sqlite3.OperationalError:
                pass   # العمود موجود أصلاً — تمام
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_stats (
                    symbol TEXT PRIMARY KEY,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    avg_slippage_pct REAL NOT NULL DEFAULT 0,
                    total_pnl REAL NOT NULL DEFAULT 0,
                    correlation_type TEXT NOT NULL DEFAULT 'NEUTRAL',
                    correlation_score REAL NOT NULL DEFAULT 0,
                    last_updated REAL NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    # ---------------- تسجيل نتيجة صفقة ----------------

    def record_trade(self, symbol: str, is_win: bool, pnl: float, slippage_pct: float, strategy: str = "unknown"):
        """
        يُستدعى مرة واحدة فقط عند إغلاق أي صفقة (ربح أو خسارة).
        strategy: اسم الاستراتيجية يلي فتحت الصفقة (مثلاً "rsi", "range_trading_btc",
        "trend_stoch_parallel", "squeeze_breakout") — حتى نقدر نحلل أداء كل
        استراتيجية لحالها لكل عملة، مش بس أداء العملة بشكل عام.
        """
        symbol = symbol.upper()
        now = time.time()

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO trades (symbol, timestamp, is_win, pnl, slippage_pct, strategy) VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, now, int(is_win), pnl, slippage_pct, strategy)
            )

            row = conn.execute(
                "SELECT wins, losses, avg_slippage_pct, total_pnl FROM symbol_stats WHERE symbol = ?",
                (symbol,)
            ).fetchone()

            if row is None:
                wins = 1 if is_win else 0
                losses = 0 if is_win else 1
                avg_slip = slippage_pct
                total_pnl = pnl
                conn.execute(
                    """INSERT INTO symbol_stats
                       (symbol, wins, losses, avg_slippage_pct, total_pnl, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (symbol, wins, losses, avg_slip, total_pnl, now)
                )
            else:
                wins, losses, avg_slip, total_pnl = row
                new_wins = wins + (1 if is_win else 0)
                new_losses = losses + (0 if is_win else 1)
                total_trades = new_wins + new_losses
                # متوسط متحرك للانزلاق
                new_avg_slip = ((avg_slip * (total_trades - 1)) + slippage_pct) / total_trades
                new_total_pnl = total_pnl + pnl

                conn.execute(
                    """UPDATE symbol_stats
                       SET wins = ?, losses = ?, avg_slippage_pct = ?, total_pnl = ?, last_updated = ?
                       WHERE symbol = ?""",
                    (new_wins, new_losses, new_avg_slip, new_total_pnl, now, symbol)
                )
            conn.commit()

    # ---------------- قراءة الإحصاءات ----------------

    def get_stats(self, symbol: str) -> Optional[SymbolStats]:
        symbol = symbol.upper()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT symbol, wins, losses, avg_slippage_pct, total_pnl,
                          correlation_type, correlation_score, last_updated
                   FROM symbol_stats WHERE symbol = ?""",
                (symbol,)
            ).fetchone()
        if row is None:
            return None
        return SymbolStats(*row)

    def get_all_stats(self) -> List[SymbolStats]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT symbol, wins, losses, avg_slippage_pct, total_pnl,
                          correlation_type, correlation_score, last_updated
                   FROM symbol_stats"""
            ).fetchall()
        return [SymbolStats(*r) for r in rows]

    def get_recent_trades(self, symbol: str = None, strategy: str = None, limit: int = 20) -> List[dict]:
        """
        يرجع آخر `limit` صفقة (الأحدث أولاً)، مع إمكانية الفلترة حسب العملة و/أو
        الاستراتيجية. أساس أي فلتر مستقبلي (نقطة 3 يلي اتفقنا نأجلها) — مثلاً
        "شو معدل نجاح هاي العملة بآخر 20 صفقة" أو "شو أداء Squeeze Breakout عموماً".
        """
        query = "SELECT symbol, timestamp, is_win, pnl, slippage_pct, strategy FROM trades WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {"symbol": r[0], "timestamp": r[1], "is_win": bool(r[2]), "pnl": r[3], "slippage_pct": r[4], "strategy": r[5]}
            for r in rows
        ]

    def get_strategy_stats(self, strategy: str) -> dict:
        """
        يرجع ملخص أداء استراتيجية معينة عبر كل العملات مجتمعة — عدد الصفقات،
        الرابحة، الخاسرة، إجمالي الربح/الخسارة، ونسبة النجاح.
        """
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*), SUM(is_win), SUM(pnl)
                   FROM trades WHERE strategy = ?""",
                (strategy,)
            ).fetchone()
        total, wins, total_pnl = row
        total = total or 0
        wins = wins or 0
        total_pnl = total_pnl or 0.0
        return {
            "strategy": strategy,
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate_pct": round((wins / total) * 100, 2) if total > 0 else None,
            "total_pnl": round(total_pnl, 4),
        }

    def _set_correlation(self, symbol: str, corr_type: str, corr_score: float):
        symbol = symbol.upper()
        with self._connect() as conn:
            # تأكد من وجود صف أساسي حتى لو ما فيه صفقات بعد
            conn.execute(
                """INSERT INTO symbol_stats (symbol, correlation_type, correlation_score, last_updated)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                       correlation_type = excluded.correlation_type,
                       correlation_score = excluded.correlation_score,
                       last_updated = excluded.last_updated""",
                (symbol, corr_type, corr_score, time.time())
            )
            conn.commit()


# ----------------------------------------------------------------------
# 3) CorrelationEngine - تصنيف العملات وفق سلوكها مع BTC
# ----------------------------------------------------------------------

class CorrelationEngine:
    """
    يحسب ارتباط تحرك العملة مع تسارع BTC عبر سلسلة عوائد (returns) متزامنة،
    ويحدّث تصنيفها في CoinMemory.

    التصنيف:
      score >= +0.35   -> TREND_FOLLOWER   (تمشي مع تسارع BTC)
      score <= -0.35   -> INVERSE_STRENGTH (تبرز/تقاوم وقت هبوط BTC)
      غير ذلك          -> NEUTRAL
    """

    TREND_THRESHOLD = 0.35
    INVERSE_THRESHOLD = -0.35

    def __init__(self, memory: CoinMemory):
        self.memory = memory

    @staticmethod
    def _pearson_correlation(x: List[float], y: List[float]) -> float:
        n = len(x)
        if n < 3 or n != len(y):
            return 0.0
        mean_x, mean_y = sum(x) / n, sum(y) / n
        cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
        var_x = sum((a - mean_x) ** 2 for a in x)
        var_y = sum((b - mean_y) ** 2 for b in y)
        denom = math.sqrt(var_x * var_y)
        if denom == 0:
            return 0.0
        return cov / denom

    def update_symbol_correlation(
        self,
        symbol: str,
        btc_returns: List[float],
        symbol_returns: List[float],
    ) -> CorrelationType:
        """
        btc_returns / symbol_returns: قوائم عوائد نسبية متزامنة زمنيًا
        (مثلاً عوائد كل شمعة 1h على مدى آخر 100-200 شمعة).
        """
        score = self._pearson_correlation(btc_returns, symbol_returns)

        if score >= self.TREND_THRESHOLD:
            corr_type = CorrelationType.TREND_FOLLOWER
        elif score <= self.INVERSE_THRESHOLD:
            corr_type = CorrelationType.INVERSE_STRENGTH
        else:
            corr_type = CorrelationType.NEUTRAL

        self.memory._set_correlation(symbol, corr_type.value, score)
        return corr_type


# ----------------------------------------------------------------------
# 4) SmartRanker - مصفاة الترتيب والذكاء البرمجي
# ----------------------------------------------------------------------

@dataclass
class RankedSymbol:
    symbol: str
    score: float
    strict_mode: bool
    stats: Optional[SymbolStats]
    reason: str


class SmartRanker:
    """
    يرتب قائمة العملات المرشحة اعتمادًا على تاريخها في الذاكرة.
    لا يقرر البوت مباشرة أي عملة يدخل بها؛ بل يمرر دائمًا عبر هذا المرشِّح.
    """

    # أقل عدد صفقات لاعتبار الإحصاء "موثوق" -> غير الملتزم بهذا الحد يُعامل بحذر
    MIN_SAMPLE_SIZE = 5

    # إذا كانت أفضل عملة متاحة أقل من هذا السقف -> تفعيل Strict Mode
    STRICT_MODE_SCORE_THRESHOLD = 0.35

    def __init__(self, memory: CoinMemory):
        self.memory = memory

    @staticmethod
    def _wilson_lower_bound(wins: int, total: int, z: float = 1.44) -> float:
        """
        Wilson score lower bound: يعاقب العملات ذات العينة الصغيرة
        بدل الاعتماد على win_rate الخام (اللي بيضلل مع 2-3 صفقات فقط).
        z=1.44 يقارب مستوى ثقة ~85% (مناسب لبيئة تداول عالية التقلب).
        """
        if total == 0:
            return 0.0
        p = wins / total
        denom = 1 + z ** 2 / total
        centre = p + z ** 2 / (2 * total)
        margin = z * math.sqrt((p * (1 - p) / total) + (z ** 2 / (4 * total ** 2)))
        return (centre - margin) / denom

    def _score_symbol(self, stats: Optional[SymbolStats], btc_trend: Optional[str] = None) -> float:
        """
        يحسب Score نهائي 0..1 لكل عملة بناءً على:
          - الأداء الموثوق (Wilson lower bound لنسبة النجاح)
          - عقوبة الانزلاق العالي
          - مكافأة/عقوبة حسب تصنيف الارتباط مع BTC (correlation_type) مقارنة باتجاه BTC الحالي:
              * BTC هابط + العملة INVERSE_STRENGTH (تقاوم/تبرز وقت الهبوط)  -> مكافأة
              * BTC صاعد  + العملة TREND_FOLLOWER  (تتماشى مع الصعود)       -> مكافأة
              * BTC هابط + العملة TREND_FOLLOWER (بتتبع BTC وقت هبوطه)      -> عقوبة خفيفة (خطر إضافي)
          `btc_trend` يُمرَّر من الخارج (BULL/BEAR/SIDEWAYS)؛ None = تجاهل هذا العامل تمامًا.

        عملة بدون أي سجل صفقات تُعطى Score حيادي متوسط (0.5) حتى لا تُستبعد ظلمًا،
        لكن يبقى بإمكانها الاستفادة من مكافأة الارتباط لو تصنيفها معروف من قبل.
        """
        if stats is None or stats.total_trades == 0:
            base_score = 0.5
        else:
            base = self._wilson_lower_bound(stats.wins, stats.total_trades)
            slippage_penalty = max(0.0, stats.avg_slippage_pct - 0.05) * 0.5
            base_score = max(0.0, min(1.0, base - slippage_penalty))

        corr_adjustment = 0.0
        if stats is not None and btc_trend in ("BULL", "BEAR"):
            corr_strength = min(abs(stats.correlation_score), 1.0)
            if btc_trend == "BEAR" and stats.correlation_type == CorrelationType.INVERSE_STRENGTH.value:
                corr_adjustment = 0.20 * corr_strength      # ✅ تُفضَّل وقت هبوط BTC
            elif btc_trend == "BULL" and stats.correlation_type == CorrelationType.TREND_FOLLOWER.value:
                corr_adjustment = 0.20 * corr_strength      # ✅ تُفضَّل وقت صعود BTC
            elif btc_trend == "BEAR" and stats.correlation_type == CorrelationType.TREND_FOLLOWER.value:
                corr_adjustment = -0.15 * corr_strength     # ⚠️ خطر إضافي: تتبع BTC وهو هابط

        return max(0.0, min(1.0, base_score + corr_adjustment))

    def rank(self, candidate_symbols: List[str], btc_trend: Optional[str] = None) -> List[RankedSymbol]:
        """
        يرجّع القائمة مرتبة تنازليًا. أي عنصر بدون سجل كافٍ أو بسجل سيء
        يُعلَّم strict_mode=True، ليقوم منطق الدخول بتطبيق قيود إضافية عليه.

        btc_trend: "BULL" / "BEAR" / "SIDEWAYS" (اختياري) — لو مُمرَّر، يُستخدم
        لتفضيل العملات المصنّفة INVERSE_STRENGTH وقت هبوط BTC، أو TREND_FOLLOWER وقت صعوده.
        """
        ranked: List[RankedSymbol] = []

        for symbol in candidate_symbols:
            stats = self.memory.get_stats(symbol)
            score = self._score_symbol(stats, btc_trend=btc_trend)

            has_enough_data = stats is not None and stats.total_trades >= self.MIN_SAMPLE_SIZE
            is_bad_history = has_enough_data and score < self.STRICT_MODE_SCORE_THRESHOLD
            is_unknown = not has_enough_data

            strict_mode = is_bad_history or is_unknown

            corr_note = ""
            if stats is not None and stats.correlation_type != CorrelationType.NEUTRAL.value:
                corr_note = f" | ارتباط BTC: {stats.correlation_type}"

            if is_unknown:
                reason = f"لا يوجد سجل كافٍ بعد (عملة غير مختبرة){corr_note}"
            elif is_bad_history:
                reason = f"تاريخ ضعيف (score={score:.2f}) - يتطلب Strict Mode{corr_note}"
            else:
                reason = f"تاريخ نظيف (score={score:.2f}){corr_note}"

            ranked.append(RankedSymbol(
                symbol=symbol, score=score, strict_mode=strict_mode,
                stats=stats, reason=reason
            ))

        ranked.sort(key=lambda r: r.score, reverse=True)
        return ranked

    def select_best(self, candidate_symbols: List[str], btc_trend: Optional[str] = None) -> Optional[RankedSymbol]:
        """
        يرجّع أفضل خيار وحيد. لو كل الخيارات المتاحة سيئة، يرجّع أفضلها
        مع strict_mode=True بدل رفض الدخول بالكامل (حسب طلب "التعامل مع
        العملات المشاكسة").
        """
        ranked = self.rank(candidate_symbols, btc_trend=btc_trend)
        return ranked[0] if ranked else None


# ----------------------------------------------------------------------
# 5) ATRGuard - صمام أمان SL/ATR المرتبط بحالة السوق
# ----------------------------------------------------------------------

class ATRGuard:
    """
    يحسب سقف الـ Stop-Loss ومعاملات الـ ATR بناءً على:
      - حالة السوق العامة (BULL / BEAR / SIDEWAYS)
      - وضع Strict Mode (عملة ذات تاريخ سيء)

    القاعدة الصلبة (Hard Cap):
      SL لا يمكن أن يتجاوز HARD_CAP_PCT من سعر الدخول مهما كانت قيمة ATR.
    """

    HARD_CAP_PCT = 3.0          # لا يمكن تجاوز 3% من سعر الدخول أبدًا
    DEFAULT_CAP_PCT = 2.5       # السقف الافتراضي المعتاد

    # معامل ATR الأساسي لكل حالة سوق (يُضرب في قيمة ATR الخام)
    REGIME_ATR_MULTIPLIER = {
        MarketRegime.BULL: 1.0,        # السوق صاعد -> ATR يعمل بشكل طبيعي
        MarketRegime.SIDEWAYS: 0.75,   # تباطؤ -> تقليل تأثير الـ ATR
        MarketRegime.BEAR: 0.6,        # هبوط حاد -> تخفيف إضافي لتجنب اصطياد SL
    }

    def __init__(self):
        pass

    def detect_market_regime(
        self,
        btc_price_change_pct_24h: float,
        btc_atr_slope: float,
    ) -> MarketRegime:
        """
        تصنيف مبسّط لحالة السوق:
          - تغيّر BTC 24h موجب وواضح + ATR في ارتفاع  -> BULL
          - تغيّر BTC 24h سالب وواضح                    -> BEAR
          - غير ذلك                                      -> SIDEWAYS
        (يمكن استبدال هذا المنطق بأي مؤشر اتجاه آخر لديك مثل EMA200/ADX).
        """
        if btc_price_change_pct_24h >= 1.5:
            return MarketRegime.BULL
        if btc_price_change_pct_24h <= -1.5:
            return MarketRegime.BEAR
        return MarketRegime.SIDEWAYS

    def compute_stop_loss(
        self,
        entry_price: float,
        raw_atr_value: float,
        market_regime: MarketRegime,
        strict_mode: bool = False,
        atr_sl_multiple: float = 1.5,
        strict_cap_pct: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        يرجّع:
          - sl_price: سعر وقف الخسارة النهائي بعد كل القيود
          - sl_pct: نسبته من سعر الدخول
          - applied_atr_multiplier: معامل ATR المطبَّق فعليًا
          - capped: هل تم تفعيل السقف الصلب (Hard Cap)؟

        strict_cap_pct: سقف مخصّص (%) لصفقات Strict Mode فقط — لو انمرّر، يُستخدم مباشرة
        بدل الافتراضي الداخلي (DEFAULT_CAP_PCT × 0.85). هيك بيصير سقف Strict تابع مباشرة
        لإعداد "حد الخسارة الثابت" (STOP_LOSS_PCT) يلي المستخدم متحكم فيه من التطبيق،
        بدل ما يكون رقم ثابت مبرمج هون. يبقى دائماً مقيّد بالسقف الصلب المطلق (HARD_CAP_PCT).
        """
        regime_multiplier = self.REGIME_ATR_MULTIPLIER[market_regime]

        # في Strict Mode: تقليل إضافي لـ ATR (تقليل الضربات) لتجنب اصطياد
        # SL على عملة ذات تاريخ سيء. (نقطة تفعيل الـ Trailing حاليًا 1× بكل الحالات،
        # ما فيه مضاعفة — راجع get_take_profit_multiplier أدناه).
        if strict_mode:
            regime_multiplier *= 0.7

        effective_atr = raw_atr_value * regime_multiplier * atr_sl_multiple
        raw_sl_pct = (effective_atr / entry_price) * 100

        if strict_mode:
            # لو انمرّر سقف مخصّص من المستخدم (STOP_LOSS_PCT بالإعدادات) نستخدمه مباشرة،
            # وإلا نرجع للاحتياطي الداخلي (أضيق من السقف العادي بـ 15%)
            cap = strict_cap_pct if strict_cap_pct is not None else (self.DEFAULT_CAP_PCT * 0.85)
            cap = max(cap, 0.1)   # حد أدنى بسيط لتفادي سقف صفري/سالب بالغلط
        else:
            cap = self.DEFAULT_CAP_PCT
        cap = min(cap, self.HARD_CAP_PCT)  # لا يمكن تجاوز السقف الصلب أبدًا مهما حصل

        capped = raw_sl_pct > cap
        final_sl_pct = min(raw_sl_pct, cap)

        sl_price = entry_price * (1 - final_sl_pct / 100)

        return {
            "sl_price": round(sl_price, 8),
            "sl_pct": round(final_sl_pct, 4),
            "applied_atr_multiplier": round(regime_multiplier * atr_sl_multiple, 4),
            "capped": capped,
        }

    def get_take_profit_multiplier(self, strict_mode: bool) -> float:
        """
        نقطة تفعيل Trailing (TP) تبقى نفسها (1×) بكل الحالات، بما فيها Strict Mode —
        الحماية بوضع Strict تعتمد فقط على تضييق الـ SL (compute_stop_loss)، بدون
        تعويض بمضاعفة صبر جني الربح.
        """
        return 1.0


# ----------------------------------------------------------------------
# مثال استخدام متكامل (مرجعي فقط - لن يُنفَّذ عند import)
# ----------------------------------------------------------------------

if __name__ == "__main__":
    memory = CoinMemory("coin_memory.db")
    corr_engine = CorrelationEngine(memory)
    ranker = SmartRanker(memory)
    atr_guard = ATRGuard()

    # 1) تسجيل نتيجة صفقة بعد إغلاقها
    memory.record_trade("ETHUSDT", is_win=True, pnl=12.4, slippage_pct=0.03)
    memory.record_trade("ETHUSDT", is_win=False, pnl=-5.1, slippage_pct=0.09)

    # 2) تحديث تصنيف الارتباط مع BTC (تُمرَّر عوائد فعلية من مصدر بياناتك)
    corr_engine.update_symbol_correlation(
        "ETHUSDT",
        btc_returns=[0.5, -0.2, 0.8, -0.1, 0.3],
        symbol_returns=[0.6, -0.3, 0.7, -0.05, 0.4],
    )

    # 3) عند وجود عدة مرشحين لصفقة جديدة
    candidates = ["ETHUSDT", "SOLUSDT", "PEPEUSDT"]
    best = ranker.select_best(candidates)
    print("أفضل خيار:", best.symbol, "| score:", round(best.score, 3),
          "| strict_mode:", best.strict_mode, "|", best.reason)

    # 4) حساب SL بحماية ATR وفق حالة السوق
    regime = atr_guard.detect_market_regime(btc_price_change_pct_24h=-2.3, btc_atr_slope=-0.4)
    sl_info = atr_guard.compute_stop_loss(
        entry_price=100.0,
        raw_atr_value=3.2,
        market_regime=regime,
        strict_mode=best.strict_mode,
    )
    tp_multiplier = atr_guard.get_take_profit_multiplier(best.strict_mode)
    print("حالة السوق:", regime.value, "| SL info:", sl_info, "| TP multiplier:", tp_multiplier)
