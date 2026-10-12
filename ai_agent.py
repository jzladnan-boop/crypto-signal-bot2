"""
🤖 Gemini AI Agent — محادثة الوكيل بالتطبيق (شرح وأسئلة فقط)
==============================================================
هذا الملف بس لمحادثة الوكيل داخل التطبيق (تبويب "محادثة عامة"): بيسأل Gemini
ويرجع رد نصي. ما إله أي علاقة بقرارات الشراء — قرار الشراء صار بقواعد ثابتة
بالكود (risk_gate.py)، فتعطل Gemini ما بيأثر على التداول.

لتشغيل المحادثة، أضف بمتغيرات البيئة (Railway → Variables):
    GEMINI_API_KEY=<مفتاحك من Google AI Studio>
اختياري:
    AI_AGENT_ENABLED=true            # false لتعطيل المحادثة
    GEMINI_MODEL=gemini-3.1-flash-lite
    AI_AGENT_TIMEOUT_SECONDS=12
"""

import os
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

_CHAT_RULES = """أنت مساعد ذكي جوا تطبيق بوت تداول عملات رقمية آلي. بتحكي مع صاحب البوت
مباشرة بمحادثة حرة (مو تقييم صفقة محددة). جاوب بالعربي العامي بأسلوب طبيعي.

مع كل رسالة، بتوصلك معلومات حية حقيقية عن حالة البوت (الإعدادات، الصفقات
المفتوحة، آخر 10 صفقات، إحصائيات الأداء لكل عملة). استخدم هاي المعلومات
حصراً للإجابة عن أسئلة عنها — ما تخترع أرقام أو تواريخ أو أسماء عملات مش
موجودة فيها حرفياً.

لو المستخدم ذكر اسم عملة معينة بسؤاله، ممكن توصلك كمان مستويات دعم/مقاومة
محسوبة فعلياً من الشموع الحقيقية (مش تخمين) — استخدمها بثقة لو موجودة.
لو سأل عن دعم/مقاومة عملة وما وصلتك بيانات عنها بالسياق، قول إنه تعذر
حساب المستويات لهاي العملة حالياً (بدل ما تخترع أرقام).

لو المستخدم ذكر اسم عملة، ممكن توصلك كمان Volume Profile محسوب فعلياً
(POC = أكتر سعر انتداول عليه، VAL/VAH = حدود منطقة القيمة، وأقرب دعم/هدف
حجمي). استخدمه بثقة لو موجود، ولو سأل عنه وما وصلتك بيانات قول إنه تعذر حسابه.

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
