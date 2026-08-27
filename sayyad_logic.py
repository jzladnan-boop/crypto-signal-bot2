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

MOMENTUM_WINDOW_CANDLES = 8        # 8 شمعة × 30 دقيقة = 4 ساعات (نفس صياد بالضبط)
EXTREME_MOVE_THRESHOLD_PCT = 20    # حركة أكبر من كذا % (4س أو 24س) = رفض احترازي
LARGE_TRADE_USDT_THRESHOLD = 10_000
MIN_SCORE_TO_ACCEPT = 60


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
