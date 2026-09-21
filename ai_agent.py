"""
🤖 Gemini AI Agent — مصفاة استشارية على إشارات الشراء
========================================================
يستقبل بيانات المؤشرات الفنية لإشارة شراء مرشحة (Volume, MFI, ADX,
Bollinger Bands, VWAP, Momentum)، ويسأل Gemini: هل هاي الصفقة آمنة
ومنطقية للدخول فيها الآن، أم في مؤشرات خطر واضحة (فوليوم ضعيف، تشبع
شرائي، اتجاه غير واضح...)؟

مستقل تماماً عن crypto_signal_bot.py من ناحية الحالة — بس دالة وحدة
(evaluate_signal) بتستقبل dict وبترجع dict. ما بيلمس أي state أو ملفات.

⚠️ سلوك الأخطاء (Fail-Closed) — حسب تفضيل المستخدم الصريح:
لو صار أي خطأ فعلي أثناء الاستشارة (timeout، انقطاع نت، رد غير مفهوم من
Gemini) → نرجع قرار "رفض" احتياطاً. أفضل نخسر صفقة محتملة من ندخل على
عمى بدون رأي الوكيل.

لتشغيل الوكيل، أضف بمتغيرات البيئة (Railway → Variables):
    GEMINI_API_KEY=<مفتاحك من Google AI Studio>
اختياري:
    AI_AGENT_ENABLED=true            # false لتعطيل الوكيل مؤقتاً (يوافق تلقائياً)
    GEMINI_MODEL=gemini-3.1-flash-lite
    AI_AGENT_TIMEOUT_SECONDS=12
"""

import os
import json
import logging
import requests

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
AI_AGENT_ENABLED = os.getenv("AI_AGENT_ENABLED", "true").strip().lower() == "true"
AI_AGENT_TIMEOUT = float(os.getenv("AI_AGENT_TIMEOUT_SECONDS", "12"))

_GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

_SYSTEM_RULES = """أنت مستشار مخاطر لصفقات تداول عملات رقمية قصيرة/متوسطة المدى.
هدفك الوحيد: تقييم إشارة شراء اقترحها بوت آلي بناءً على مؤشرات فنية، وتقرر
إذا كانت آمنة للدخول الآن أو فيها خطر واضح يستدعي الرفض.

اعتبر الصفقة خطرة (ارفضها) لو:
- الفوليوم (volume / volume_ratio) ضعيف أو أقل من المعتاد رغم إشارة الشراء
  (يعني الحركة مش مدعومة بشراء حقيقي كافي)
- MFI متطرف جداً بمنطقة تشبع شرائي (أعلى من 80 تقريباً) — احتمال ارتداد هبوطي قريب
- ADX ضعيف جداً (أقل من 15 تقريباً) — يعني السوق عرضي بدون اتجاه واضح، والإشارة
  ممكن تكون كاذبة
- السعر قريب جداً من حد Bollinger العلوي بشكل مبالغ (تمدد زايد عن اللزوم) أو
  قريب من الحد السفلي بشكل يوحي بانهيار مستمر مش ارتداد
- أي تناقض واضح وقوي بين المؤشرات (مثلاً momentum قوي جداً بس فوليوم هابط بوضوح)

اعتبرها آمنة (وافق عليها) لو المؤشرات المتوفرة متوافقة مع بعضها بشكل معقول
وتدعم دخول بزخم حقيقي. لو بعض المؤشرات مفقودة (null)، اعتمد على المتوفر منها
فقط وما تخترع بيانات.

رد حصراً بصيغة JSON صحيحة بدون أي نص إضافي قبلها أو بعدها وبدون ```، بالشكل
التالي بالضبط:
{"approved": true أو false, "reason_ar": "جملة أو جملتين بالعربي تشرح السبب بلغة بسيطة ومباشرة"}
"""


def evaluate_signal(symbol: str, signal_data: dict) -> dict:
    """
    يرسل بيانات إشارة الشراء لـ Gemini ويرجع قرار الوكيل.

    Args:
        symbol: رمز العملة (مثلاً "SUIUSDT")
        signal_data: dict فيه المؤشرات المتوفرة، مثلاً:
            {"price": 1.23, "volume": 5_000_000, "mfi": 62.4, "adx": 28.1,
             "bb_upper": 1.30, "bb_mid": 1.20, "bb_lower": 1.10,
             "vwap": 1.19, "momentum_score": 0.72, "strategy": "rsi"}

    Returns:
        dict: {"approved": bool, "reason_ar": str, "source": "gemini"|"fallback"|"error"}
    """
    if not AI_AGENT_ENABLED:
        return {
            "approved": True,
            "reason_ar": "الوكيل مطفي حالياً (AI_AGENT_ENABLED=false) — تمت الموافقة تلقائياً",
            "source": "fallback",
        }

    if not GEMINI_API_KEY:
        log.warning("⚠️ AI Agent: GEMINI_API_KEY غير موجود بمتغيرات البيئة — تمرير الصفقة بدون استشارة")
        return {
            "approved": True,
            "reason_ar": "لا يوجد مفتاح Gemini مضبوط — تمت الموافقة تلقائياً بدون استشارة",
            "source": "fallback",
        }

    user_payload = {"symbol": symbol, **signal_data}

    body = {
        "system_instruction": {"parts": [{"text": _SYSTEM_RULES}]},
        "contents": [
            {"role": "user", "parts": [{"text": json.dumps(user_payload, ensure_ascii=False, default=str)}]}
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = requests.post(
            _GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=body,
            timeout=AI_AGENT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        approved = bool(parsed.get("approved"))
        reason = str(parsed.get("reason_ar") or "").strip() or "بدون توضيح من الوكيل"
        return {"approved": approved, "reason_ar": reason, "source": "gemini"}
    except Exception as e:
        log.error(f"❌ AI Agent (Gemini) لـ {symbol}: {e}")
        return {
            "approved": False,
            "reason_ar": f"تعذر التواصل مع وكيل Gemini ({type(e).__name__}) — تم رفض الصفقة احتياطاً",
            "source": "error",
        }


_COMPARE_RULES = """أنت مستشار مخاطر لصفقات تداول عملات رقمية قصيرة/متوسطة المدى.
بتوصلك قائمة بعدة عملات مرشحة مع مؤشراتها الفنية. مهمتك: قارن بينهم واختار
**عملة واحدة بس** — الأقوى والأنظف فرصة دخول الآن من بين كل القائمة — أو
لا تختار ولا وحدة لو كلهم ضعاف أو فيهم مخاطر واضحة.

اعتبر المؤشرات دي بالمقارنة:
- الفوليوم (volume / momentum_score): كلما أعلى وأكثر دعم للحركة، أفضل
- MFI: تجنب المناطق المتطرفة (أعلى من 80 تقريباً = خطر ارتداد هبوطي)
- ADX: كلما أعلى (فوق 20-25 تقريباً)، اتجاه أوضح وأوثق
- تناسق عام بين المؤشرات (بدون تناقضات صارخة)

اختار الأفضل نسبياً من القائمة المعطاة، مش معيار مطلق منعزل — يعني ممكن
توافق على عملة مؤشراتها متوسطة لو باقي القائمة أضعف منها بوضوح، والعكس:
ممكن ترفض القائمة كلها لو كل الخيارات فيها مخاطر واضحة.

رد حصراً بصيغة JSON صحيحة بدون أي نص إضافي وبدون ```، بالشكل التالي بالضبط:
{"best_symbol": "BTCUSDT" أو null لو ولا وحدة مناسبة, "reason_ar": "جملة أو جملتين بالعربي تشرح ليش هاي الأفضل (أو ليش رفضت الكل)"}
"""


def compare_candidates(candidates: list) -> dict:
    """
    يقارن بين عدة مرشحين بطلب واحد ويختار الأقوى، بدل تقييمهم وحدة وحدة.

    Args:
        candidates: list من dicts، كل وحدة فيها على الأقل "symbol" وباقي
            المؤشرات المتوفرة (نفس شكل signal_data بـ evaluate_signal).

    Returns:
        dict: {"approved": bool, "symbol": str|None, "reason_ar": str,
               "source": "gemini"|"fallback"|"error"}
    """
    if not AI_AGENT_ENABLED or not GEMINI_API_KEY or not candidates:
        return {
            "approved": False, "symbol": None,
            "reason_ar": "الوكيل مطفي أو لا يوجد مرشحين للمقارنة",
            "source": "fallback",
        }

    body = {
        "system_instruction": {"parts": [{"text": _COMPARE_RULES}]},
        "contents": [
            {"role": "user", "parts": [{"text": json.dumps(candidates, ensure_ascii=False, default=str)}]}
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = requests.post(
            _GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=body,
            timeout=AI_AGENT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        best_symbol = parsed.get("best_symbol") or None
        reason = str(parsed.get("reason_ar") or "").strip() or "بدون توضيح من الوكيل"
        valid_symbols = {c.get("symbol") for c in candidates}
        if best_symbol not in valid_symbols:
            best_symbol = None
        return {"approved": best_symbol is not None, "symbol": best_symbol, "reason_ar": reason, "source": "gemini"}
    except Exception as e:
        log.error(f"❌ AI Agent (Gemini) مقارنة مرشحين: {e}")
        return {
            "approved": False, "symbol": None,
            "reason_ar": f"تعذر التواصل مع وكيل Gemini ({type(e).__name__}) — تم التجاوز احتياطاً",
            "source": "error",
        }


_CHAT_RULES = """أنت مساعد ذكي جوا تطبيق بوت تداول عملات رقمية آلي. بتحكي مع صاحب البوت
مباشرة بمحادثة حرة (مو تقييم صفقة محددة). جاوب بالعربي العامي بأسلوب طبيعي.

مع كل رسالة، بتوصلك معلومات حية حقيقية عن حالة البوت (الإعدادات، الصفقات
المفتوحة، آخر 10 صفقات، إحصائيات الأداء لكل عملة). استخدم هاي المعلومات
حصراً للإجابة عن أسئلة عنها — ما تخترع أرقام أو تواريخ أو أسماء عملات مش
موجودة فيها حرفياً.

⚠️ قاعدة حاسمة: لو السؤال عن تفصيل مش موجود بالمعلومات المعطاة (مثلاً
صفقة أقدم من آخر 10، أو رقم غير مذكور)، قول صراحة إنه هذا التفصيل مش
متوفر عندك بالمعطيات الحالية — **لا تخترعه أبداً ولا تقدر قيمة تقريبية
وكأنها حقيقية**. اختلاق رقم أو تاريخ خطأ فادح أخطر بكثير من قول "ما
عندي هاي المعلومة".

⚠️ قاعدة حاسمة ثانية: المعلومات الحية يلي بتوصلك بكل رسالة **محدّثة
باللحظة** وهي المصدر الوحيد الموثوق للحقائق (أرقام، تواريخ، أسماء عملات).
لو رديت قبل هيك بنفس المحادثة على سؤال مشابه بمعلومة مختلفة، **تجاهل
جوابك القديم والتزم فقط بالمعلومات الحية الجديدة** — لا تكرر أو تبني على
إجابة سابقة لك حتى لو بدت متناسقة أكتر.

قاعدة مهمة جداً: **جوابك دايماً مختصر جداً** — سطر أو سطرين كحد أقصى، بدون
مقدمات أو حشو أو تفاصيل زايدة عن المطلوب. لو السؤال يحتاج توضيح أكتر، اختصر
لأهم نقطة بس واسأل هو إذا بده تفصيل أكتر بدل ما تفصّل من نفسك.

ما تعطي نصيحة استثمارية قاطعة ("اشتري الآن") ولا تضمن نتائج — أنت مساعد
تحليلي بس، القرار النهائي للمستخدم."""


def chat(message: str, context: str = "", history: list = None) -> dict:
    """
    محادثة حرة عامة مع الوكيل (مو تقييم صفقة). الجواب مختصر دايماً.

    Args:
        message: رسالة المستخدم الحالية
        context: سياق حي عن حالة البوت (صفقات مفتوحة، سجل، إحصائيات) —
            يُعاد بناؤه بكل طلب عشان يكون محدّث دايماً، مش جزء من history
        history: قائمة الرسائل السابقة بنفس الجلسة، كل عنصر
            {"role": "user"|"model", "text": str}، بالترتيب الزمني

    Returns:
        dict: {"ok": bool, "reply": str, "source": "gemini"|"fallback"|"error"}
    """
    if not AI_AGENT_ENABLED or not GEMINI_API_KEY:
        return {"ok": False, "reply": "الوكيل مطفي حالياً أو لا يوجد مفتاح Gemini مضبوط.", "source": "fallback"}

    contents = []
    for h in (history or []):
        contents.append({"role": h["role"], "parts": [{"text": h["text"]}]})

    user_text = f"[معلومات حية عن البوت الآن:\n{context}]\n\n{message}" if context else message
    contents.append({"role": "user", "parts": [{"text": user_text}]})

    body = {
        "system_instruction": {"parts": [{"text": _CHAT_RULES}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 200},
    }

    try:
        resp = requests.post(_GEMINI_URL, params={"key": GEMINI_API_KEY}, json=body, timeout=AI_AGENT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return {"ok": True, "reply": text, "source": "gemini"}
    except Exception as e:
        log.error(f"❌ AI Agent (Gemini) شات: {e}")
        return {"ok": False, "reply": f"تعذر التواصل مع الوكيل ({type(e).__name__})", "source": "error"}
