"""
🛡️ Risk Gate — بوابة المخاطر بالكود (بديل وكيل Gemini بقرار الشراء)
====================================================================
نفس منطق الوكيل المكتوب بتعليماته (ai_agent._SYSTEM_RULES) لكن بقواعد ثابتة:
ما في اتصال شبكة، ولا timeout، ولا تكلفة، ونفس المدخلات بتعطي نفس القرار دائماً.

القرار:
1) أي "رفض فوري" (Veto) ينطبق → رفض مباشرة مع ذكر السبب.
2) غير هيك: نجمع نقاط (سلبية/إيجابية) وبنوافق لو المجموع >= MIN_APPROVE_SCORE.

⚠️ كل الحدود (0.7 / 80 / 15 / 0.95 / نسبة العائد للمخاطرة ...) تقديرية مني
ومأخوذة من نص قواعد الوكيل — مو مضبوطة على بيانات. عدّل الثوابت هون بعد
مراقبة النتائج. المؤشرات الناقصة (None) بتنتجاهل قاعدتها بدل ما تخترع بيانات.

الواجهة نفس واجهة ai_agent: evaluate_signal(symbol, ind) و compare_candidates(list)
وبترجع source="rules".
"""

# ── الحدود (عدّلها هون) ──
VOL_RATIO_VETO = 0.7          # فوليوم أقل من 0.7× المتوسط = ضعيف → رفض
VOL_RATIO_LOW = 1.0           # بين 0.7 و1.0 = أقل من المتوسط → نقطة سلبية
VOL_RATIO_STRONG = 1.5        # 1.5× فأكثر = فوليوم قوي → نقطة إيجابية
MFI_VETO = 80                 # تشبع شرائي → رفض
MFI_HIGH = 65                 # مرتفع → نقطة سلبية
ADX_VETO = 15                 # سوق عرضي بدون اتجاه → رفض
ADX_LOW = 20                  # 15-20 ضعيف نسبياً → نقطة سلبية
ADX_STRONG = 25               # اتجاه قوي → نقطة إيجابية
BB_UPPER_VETO = 0.95          # موقع السعر داخل نطاق بولينجر (0 = الحد السفلي، 1 = العلوي)
BB_UPPER_HIGH = 0.85
BB_LOWER_VETO = 0.05          # عند الحد السفلي + Supertrend هابط = انهيار مستمر
RR_MIN_RISK_PCT = 1.0         # نطبق فحص العائد/المخاطرة فقط لو المخاطرة (للدعم) >= 1%
RR_VETO_RATIO = 0.3           # عائد/مخاطرة أقل من هيك → رفض
RR_GOOD = 2.0
MIN_APPROVE_SCORE = 1         # أقل مجموع نقاط للموافقة (إذا ما في Veto)


def _num(x):
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None   # استبعاد NaN
    except (TypeError, ValueError):
        return None


def _vol_ratio(ind):
    r = _num(ind.get("volume_ratio"))
    if r is not None:
        return r
    v, m = _num(ind.get("volume")), _num(ind.get("volume_mean"))
    return (v / m) if v is not None and m else None


def _bb_position(ind):
    price, up, lo = _num(ind.get("price")), _num(ind.get("bb_upper")), _num(ind.get("bb_lower"))
    if price is None or up is None or lo is None or up <= lo:
        return None
    return (price - lo) / (up - lo)


def _risk_reward(ind):
    """(risk_pct, reward_pct) من مستويات Volume Profile أو (None, None)"""
    price = _num(ind.get("price"))
    stop, target = _num(ind.get("vp_stop_hint")), _num(ind.get("vp_target_hint"))
    if not price or price <= 0 or stop is None or target is None:
        return None, None
    return (price - stop) / price * 100, (target - price) / price * 100


def evaluate_signal(symbol: str, ind: dict) -> dict:
    """
    يرجع: {"approved": bool, "reason_ar": str, "source": "rules", "score": int,
           "vetoes": [str], "notes": [(points, str)]}
    """
    ind = ind or {}
    vetoes, notes = [], []   # notes: (نقاط, نص)

    vr = _vol_ratio(ind)
    mfi, adx = _num(ind.get("mfi")), _num(ind.get("adx"))
    pct_b = _bb_position(ind)
    st = ind.get("supertrend_direction")
    vp = ind.get("vp_position")
    risk, reward = _risk_reward(ind)

    # ── رفض فوري ──
    if vr is not None and vr < VOL_RATIO_VETO:
        vetoes.append(f"الفوليوم ضعيف ({vr:.1f}× المتوسط) والحركة مش مدعومة بشراء كافي")
    if mfi is not None and mfi > MFI_VETO:
        vetoes.append(f"MFI بمنطقة تشبع شرائي ({mfi:.0f}) واحتمال الارتداد الهبوطي قريب")
    if adx is not None and adx < ADX_VETO:
        vetoes.append(f"ADX ضعيف جداً ({adx:.0f}) يعني سوق عرضي والإشارة ممكن تكون كاذبة")
    if pct_b is not None and pct_b > BB_UPPER_VETO:
        vetoes.append("السعر ملاصق للحد العلوي لنطاق بولينجر (تمدد زايد عن اللزوم)")
    if pct_b is not None and pct_b < BB_LOWER_VETO and st == "down":
        vetoes.append("السعر عند الحد السفلي لبولينجر مع اتجاه هابط، يوحي بانهيار مستمر مش ارتداد")
    if vp == "above_vah":
        vetoes.append("السعر ممتد فوق منطقة القيمة الحجمية (مطاردة بعد ما خلصت الحركة غالباً)")
    if risk is not None and reward is not None and risk >= RR_MIN_RISK_PCT and reward < risk * RR_VETO_RATIO:
        vetoes.append(f"العائد للمخاطرة سيئ: أقرب هدف حجمي +{reward:.1f}% مقابل دعم -{risk:.1f}%")

    # ── نقاط ──
    if vr is not None:
        if VOL_RATIO_VETO <= vr < VOL_RATIO_LOW:
            notes.append((-1, "الفوليوم أقل من المتوسط"))
        elif vr >= VOL_RATIO_STRONG:
            notes.append((1, f"فوليوم قوي ({vr:.1f}× المتوسط)"))
    if adx is not None:
        if ADX_VETO <= adx < ADX_LOW:
            notes.append((-1, f"ADX ضعيف نسبياً ({adx:.0f})"))
        elif adx >= ADX_STRONG:
            notes.append((1, f"اتجاه قوي (ADX {adx:.0f})"))
    if mfi is not None and MFI_HIGH < mfi <= MFI_VETO:
        notes.append((-1, f"MFI مرتفع ({mfi:.0f})"))
    if pct_b is not None and BB_UPPER_HIGH < pct_b <= BB_UPPER_VETO:
        notes.append((-1, "السعر قريب من الحد العلوي لبولينجر"))
    if st == "up":
        notes.append((1, "Supertrend صاعد"))
    elif st == "down":
        notes.append((-2, "Supertrend هابط"))
    if vp == "val_to_poc":
        notes.append((2, "السعر قرب دعم حجمي ومساحة صعود لحد POC"))
    elif vp == "poc_to_vah":
        notes.append((-1, "السعر قريب من مقاومة حجمية (VAH)"))
    elif vp == "below_val":
        notes.append((-2, "السعر تحت منطقة القيمة الحجمية"))
    if risk is not None and reward is not None and not vetoes and risk > 0:
        ratio = reward / risk
        if ratio >= RR_GOOD:
            notes.append((1, f"عائد/مخاطرة جيد (هدف +{reward:.1f}% مقابل دعم -{risk:.1f}%)"))
        elif ratio < 1:
            notes.append((-1, f"عائد/مخاطرة ضعيف (هدف +{reward:.1f}% مقابل دعم -{risk:.1f}%)"))

    score = sum(p for p, _ in notes)

    if vetoes:
        reason = "رفض: " + "، ".join(vetoes[:3]) + "."
        approved = False
    elif score >= MIN_APPROVE_SCORE:
        top = [t for p, t in sorted(notes, key=lambda x: -x[0]) if p > 0][:3]
        reason = "موافقة: " + ("، ".join(top) if top else "المؤشرات متوافقة بشكل معقول") + f" (نقاط \u200e{score})."
        approved = True
    else:
        neg = [t for p, t in sorted(notes, key=lambda x: x[0]) if p < 0][:3]
        reason = "رفض: الصورة ما بتدعم الدخول بوضوح — " + ("، ".join(neg) if neg else "نقاط غير كافية") + f" (نقاط \u200e{score})."
        approved = False

    return {
        "approved": approved, "reason_ar": reason, "source": "rules",
        "score": score, "vetoes": vetoes, "notes": notes,
    }


def compare_candidates(candidates: list) -> dict:
    """
    يقيّم كل مرشح بنفس القواعد ويختار الأعلى نقاطاً بين المقبولين.
    candidates: [{"symbol": ..., **indicators}]
    يرجع: {"approved": bool, "symbol": str|None, "reason_ar": str, "source": "rules"}
    """
    if not candidates:
        return {"approved": False, "symbol": None, "reason_ar": "ما في مرشحين للمقارنة", "source": "rules"}

    results = []
    for c in candidates:
        v = evaluate_signal(c.get("symbol", ""), c)
        results.append((c.get("symbol"), c, v))

    accepted = [r for r in results if r[2]["approved"]]
    if accepted:
        accepted.sort(key=lambda r: (r[2]["score"], _vol_ratio(r[1]) or 0, _num(r[1].get("momentum_score")) or 0), reverse=True)
        sym, _, v = accepted[0]
        others = len(accepted) - 1
        extra = f" (وفي {others} مرشح مقبول كمان بنقاط أقل)" if others > 0 else ""
        return {"approved": True, "symbol": sym, "reason_ar": v["reason_ar"] + extra, "source": "rules"}

    # ما في ولا وحدة مقبولة — نوضح ليش أقرب مرشح انرفض
    results.sort(key=lambda r: r[2]["score"], reverse=True)
    sym, _, v = results[0]
    return {
        "approved": False, "symbol": None, "source": "rules",
        "reason_ar": f"أقرب مرشح ({str(sym).replace('USDT', '')}) انرفض. {v['reason_ar']}",
    }
