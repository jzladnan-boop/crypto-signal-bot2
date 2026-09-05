"""
منطق تقييم صياد — نسخة مطابقة (منقولة يدوياً) من مشروع "صياد" المنفصل
(github.com/jzladnan-boop/sayyad-bot)، مستخدمة هون بدل شروط StochRSI
التقليدية باستراتيجية Trend+Stoch الموازية.

⚠️ تنويه مهم: هاد نسخة مكرّرة (duplicate) من منطق صياد، مش ربط حي/تلقائي
بين المشروعين. أي تحسين مستقبلي بصياد (تعديل أوزان، حدود، فلاتر جديدة)
لازم يُنقل هون يدوياً حتى يضلوا متزامنين.

المصدر الأصلي: modules/binance_data.py, modules/scorer.py, modules/ai_analyzer.py
بمشروع صياد.
"""

import threading
import time

MOMENTUM_WINDOW_CANDLES = 8        # 8 شمعة × 30 دقيقة = 4 ساعات (نفس صياد بالضبط)
EXTREME_MOVE_THRESHOLD_PCT = 20    # حركة أكبر من كذا % (4س أو 24س) = رفض احترازي
LARGE_TRADE_USDT_THRESHOLD = 10_000
MIN_SCORE_TO_ACCEPT = 70   # ⬅️ رُفع من 60 لـ70 (بطلب المستخدم) — تحليل 11 صفقة فعلية (4-5 سبتمبر)
                            # طلع نسبة فوز 45% بس متوسط الخسارة (2.21%) أكبر من متوسط الربح (1.93%)،
                            # يعني حد 60 كان يسمح بدخول حدّي/ضعيف. رفعه لانتقائية أعلى وإشارات أقل بس أقوى.
RSI_OVERBOUGHT_THRESHOLD = 70      # RSI للعملة فوق هالرقم = تشبّع شرائي، رفض احترازي
MARKET_BREADTH_DANGER_THRESHOLD = 75  # % من العملات نازلة (24س) = سوق ضعيف عام، نوقف كل دخول هالدورة
MARKET_BREADTH_CACHE_SECONDS = 60     # ⬅️ إصلاح: تخزين مؤقت مشترك — البوت الأساسي + 3 استراتيجيات
                                        # موازية كل وحدة كانت تعمل client.get_ticker() (ثقيل، كل تيكرات
                                        # السوق دفعة وحدة) بشكل مستقل بفترات مختلفة (120/300/1800/1800 ثانية)
                                        # رغم إنه نفس البيانات بالضبط — هلق أول استراتيجية تطلبها بأي 60
                                        # ثانية "تجيبها لحالها"، والباقي بياخدوا نفس النسخة المخزنة بدل
                                        # ما يكرروا نفس الطلب الثقيل 4 مرات

_breadth_cache_lock = threading.Lock()
_breadth_cache = {"value": None, "time": 0.0}


def compute_momentum(klines, momentum_window=MOMENTUM_WINDOW_CANDLES):
    """يحسب الزخم الحديث (آخر X شمعة) + التغير الكامل + انفجار الحجم"""
    if len(klines) < momentum_window + 1:
        return None

    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    recent_closes = closes[-momentum_window:]
    price_change_pct = (
        ((recent_closes[-1] - recent_closes[0]) / recent_closes[0]) * 100
        if recent_closes[0] else 0
    )
    price_change_24h_pct = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0

    recent_volume = volumes[-1]
    avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else 1
    volume_spike_ratio = recent_volume / avg_volume if avg_volume else 1

    return {
        "price_change_pct": price_change_pct,
        "price_change_24h_pct": price_change_24h_pct,
        "volume_spike_ratio": volume_spike_ratio,
    }


def compute_order_book_imbalance(client, symbol, depth=50):
    """نسبة ضغط الشراء بدفتر الأوامر (> 0.5 يعني ميل شراء)"""
    try:
        ob = client.get_order_book(symbol=symbol, limit=depth)
        bid_volume = sum(float(b[0]) * float(b[1]) for b in ob["bids"])
        ask_volume = sum(float(a[0]) * float(a[1]) for a in ob["asks"])
        total = bid_volume + ask_volume
        return bid_volume / total if total else 0.5
    except Exception:
        return 0.5


def detect_whale_trades(client, symbol, usdt_threshold=LARGE_TRADE_USDT_THRESHOLD, lookback_trades=1000):
    """يفحص آخر الصفقات الفعلية ويحدد ضغط شراء/بيع الصفقات الكبيرة"""
    try:
        trades = client.get_aggregate_trades(symbol=symbol, limit=lookback_trades)
    except Exception:
        return {"whale_trade_count": 0, "whale_buy_ratio": 0.5}

    buy_usdt, sell_usdt, count = 0.0, 0.0, 0
    for t in trades:
        price = float(t["p"])
        qty = float(t["q"])
        usdt_value = price * qty
        if usdt_value >= usdt_threshold:
            count += 1
            if t["m"]:
                sell_usdt += usdt_value
            else:
                buy_usdt += usdt_value

    total = buy_usdt + sell_usdt
    buy_ratio = (buy_usdt / total) if total > 0 else 0.5
    return {"whale_trade_count": count, "whale_buy_ratio": buy_ratio}


def is_extreme_move(breakdown):
    """فلتر مستقل: حركة سعرية (4س أو 24س) تجاوزت الحد = رفض احترازي، بغض النظر عن النقاط"""
    pc = abs(breakdown["price_change_pct"])
    pc24 = abs(breakdown["price_change_24h_pct"])
    return pc >= EXTREME_MOVE_THRESHOLD_PCT or pc24 >= EXTREME_MOVE_THRESHOLD_PCT


def compute_rsi(closes, period=14):
    """
    مؤشر القوة النسبية (RSI) — قياسي، بطريقة Wilder's Smoothing (نفس صياد بالضبط)
    فوق 70 = تشبّع شرائي، تحت 30 = تشبّع بيعي
    """
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def is_overbought(rsi_value):
    """فلتر مستقل: RSI فوق الحد = تشبّع شرائي، رفض احترازي بغض النظر عن النقاط"""
    return rsi_value is not None and rsi_value >= RSI_OVERBOUGHT_THRESHOLD


def compute_market_breadth(client, symbols, threshold_pct=MARKET_BREADTH_DANGER_THRESHOLD):
    """
    يحسب نسبة العملات النازلة (24 ساعة رسمية) من أصل قائمة رموز محددة —
    طلب API واحد بس (get_ticker بدون رمز = كل الأسواق دفعة وحدة). لازم
    تمرير قائمة الرموز صراحة (متل قائمة SYMBOLS المشتركة تبع التطبيق)
    حتى يكون القياس متّسق مع باقي الاستراتيجيات. لو النسبة النازلة تجاوزت
    الحد، نعتبر السوق "ضعيف عام" ونوقف كل دخول هالدورة، حتى لو عملة معينة
    عندها إشارة قوية فردياً.

    🔒 مخزّن مؤقتاً (MARKET_BREADTH_CACHE_SECONDS) ومشترك بين كل الاستراتيجيات
    (thread-safe عبر _breadth_cache_lock) — يمنع 4 طلبات ثقيلة متكررة لنفس
    البيانات بالضبط كل دورة فحص.
    """
    now = time.time()
    with _breadth_cache_lock:
        cached_value = _breadth_cache["value"]
        cached_age = now - _breadth_cache["time"]
        if cached_value is not None and cached_age < MARKET_BREADTH_CACHE_SECONDS:
            return cached_value

    try:
        tickers = client.get_ticker()  # كل الرموز دفعة وحدة — طلب واحد فقط
        change_map = {t["symbol"]: float(t["priceChangePercent"]) for t in tickers}
        watched_changes = [change_map[s] for s in symbols if s in change_map]
        if not watched_changes:
            return None

        down_count = sum(1 for c in watched_changes if c <= 0)
        pct_down = round(down_count / len(watched_changes) * 100)

        result = {
            "pct_down_24h": pct_down,
            "total_checked": len(watched_changes),
            "is_bearish": pct_down >= threshold_pct,
        }
    except Exception:
        with _breadth_cache_lock:
            return _breadth_cache["value"]   # نرجع آخر قيمة مخزنة (حتى لو قديمة شوي) أفضل من ولا شي

    with _breadth_cache_lock:
        _breadth_cache["value"] = result
        _breadth_cache["time"] = now
    return result


def _normalize(value, low, high):
    if high == low:
        return 50
    pct = (value - low) / (high - low) * 100
    return max(0, min(100, pct))


def compute_score(momentum, order_book_imbalance, whale_data):
    """
    نفس منطق scorer.py بصياد، بفارق وحيد: وزّعنا وزن الأخبار (10% بصياد)
    على الزخم والحجم لأنه ما في مصدر أخبار متاح هون
    """
    price_change = momentum["price_change_pct"]
    momentum_score = _normalize(price_change, -8, 20)

    volume_spike = momentum["volume_spike_ratio"]
    volume_score = _normalize(volume_spike, 1, 5)

    ob_score = _normalize(order_book_imbalance, 0.4, 0.7)

    whale_buy_ratio = whale_data["whale_buy_ratio"]
    whale_trade_count = whale_data["whale_trade_count"]
    whale_pressure_score = _normalize(whale_buy_ratio, 0.4, 0.75)
    whale_confidence = min(1.0, whale_trade_count / 5)
    whale_score = whale_pressure_score * whale_confidence + 50 * (1 - whale_confidence)

    weights = {"momentum": 0.30, "volume": 0.30, "order_book": 0.15, "whale": 0.25}

    final_score = (
        momentum_score * weights["momentum"]
        + volume_score * weights["volume"]
        + ob_score * weights["order_book"]
        + whale_score * weights["whale"]
    )

    breakdown = {
        "price_change_pct": round(price_change, 2),
        "price_change_24h_pct": round(momentum.get("price_change_24h_pct", price_change), 2),
        "volume_spike_ratio": round(volume_spike, 2),
        "order_book_score": round(ob_score, 1),
        "whale_trade_count": whale_trade_count,
        "whale_buy_ratio": round(whale_buy_ratio, 2),
    }
    return round(final_score, 1), breakdown
