"""
🤖 Gemini AI Agent — رأي الوكيل بسؤال العملة + المحادثة العامة (شرح فقط)
==============================================================
هاد الملف للشرح والاستشارة بس: "سؤال عن عملة" (evaluate_signal) و"محادثة عامة"
(chat). ما إله أي علاقة بقرارات الشراء الفعلية — هاي صارت بقواعد ثابتة بالكود
(risk_gate.py)، فتعطل Gemini ما بيأثر على التداول.

لتشغيل المحادثة، أضف بمتغيرات البيئة (Railway → Variables):
    GEMINI_API_KEY=<مفتاحك من Google AI Studio>
اختياري:
    AI_AGENT_ENABLED=true            # false لتعطيل المحادثة
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
# 🔎 البحث بالإنترنت (Google Search Grounding) لسؤال العملة والمحادثة عن عملة — اختياري:
#     GEMINI_SEARCH_MODEL=<نموذج يدعم البحث>   (افتراضياً نفس GEMINI_MODEL)
#     AI_AGENT_SEARCH_TIMEOUT_SECONDS=25       (البحث أبطأ من الطلب العادي)
GEMINI_SEARCH_MODEL = os.getenv("GEMINI_SEARCH_MODEL", "").strip() or GEMINI_MODEL
AI_AGENT_SEARCH_TIMEOUT = float(os.getenv("AI_AGENT_SEARCH_TIMEOUT_SECONDS", "25"))
_GEMINI_SEARCH_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_SEARCH_MODEL}:generateContent"
)
# أخطاء بتدل إنه Gemini نفسه ما بيرد (مو إنه البحث غير مدعوم) — ما في فايدة نعيد المحاولة بدون بحث
UNREACHABLE_ERRORS = ("Timeout", "ReadTimeout", "ConnectTimeout", "ConnectionError")

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
- Volume Profile (vp_position + vp_poc/vp_val/vp_vah): vp_position يوضح وين السعر
  بالنسبة لمناطق تركز الحجم. "above_vah" أو "poc_to_vah" بقرب vp_vah = السعر ممتد
  قرب مقاومة حجمية وغالباً الحركة خلصت (دخول متأخر/مطاردة) — خطر. "below_val" =
  السعر تحت منطقة القيمة، ضعف ما لم يكن في ارتداد واضح. "val_to_poc" = قرب دعم
  حجمي ومساحة صعود لحد الـ POC — وضع دخول أفضل. هاي معلومة داعمة للحكم، مش شرط
  إجباري لوحدها.

اعتبرها آمنة (وافق عليها) لو المؤشرات المتوفرة متوافقة مع بعضها بشكل معقول
وتدعم دخول بزخم حقيقي. لو بعض المؤشرات مفقودة (null)، اعتمد على المتوفر منها
فقط وما تخترع بيانات.

اكتب السبب بالعربي الطبيعي، ولا تذكر أسماء الحقول أو القيم الإنجليزية الداخلية (مثل above_vah أو val_to_poc) — صفها بكلمات عربية.

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


_SEARCH_RULES = """

🔎 عندك أداة بحث بالإنترنت. قبل ما تحكم، ابحث عن آخر أخبار هاي العملة (آخر أيام): اختراق أو
ثغرة أمنية، إلغاء أو تعليق إدراج من منصة، فك قفل كمية كبيرة من التوكنز، قرار تنظيمي، أو خبر
إيجابي مهم. حقائق الخبر لازم تكون من نتائج البحث فعلاً — لا تخترع خبراً، ولو ما لقيت خبر مؤثر
قول هيك. خبر سلبي مؤكد وجديد = سبب كافي للرفض حتى لو المؤشرات حلوة. غياب الأخبار لوحده
ما يغيّر حكمك المبني على المؤشرات.

الصيغة تصير: {"approved": true أو false, "reason_ar": "...", "news_ar": "جملة وحدة عن الأخبار اللي لقيتها، أو ما في أخبار مؤثرة"}
رد بـ JSON فقط بدون أي نص قبله أو بعده."""


def _extract_json(text: str):
    """يطلّع أول كائن JSON من نص الرد (الرد المبني على بحث ما بيقبل responseMimeType، فممكن يجي بأسوار ``` أو نص حواليه)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("ما في JSON بالرد")
    return json.loads(t[start:end + 1])


def _candidate_text(data: dict) -> str:
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def _grounding_sources(data: dict, limit: int = 3) -> list:
    """أسماء المواقع اللي اعتمد عليها البحث (بدون تكرار)."""
    try:
        chunks = data["candidates"][0].get("groundingMetadata", {}).get("groundingChunks", [])
    except Exception:
        return []
    out = []
    for c in chunks:
        title = (c.get("web") or {}).get("title")
        if title and title not in out:
            out.append(title)
        if len(out) >= limit:
            break
    return out


def evaluate_signal_with_search(symbol: str, signal_data: dict) -> dict:
    """
    نفس evaluate_signal بس مع بحث Google عن أخبار العملة (Grounding).
    Returns: {"approved", "reason_ar", "source": "gemini_search"|"fallback"|"error", "sources": [...], "error_type": str (عند الخطأ)}
    الاستشارة فقط — قرارات الشراء الفعلية بتمر على risk_gate.
    """
    if not AI_AGENT_ENABLED or not GEMINI_API_KEY:
        return {"approved": True, "reason_ar": "الوكيل مطفي أو ما في مفتاح Gemini", "source": "fallback", "sources": []}

    user_payload = {"symbol": symbol, **signal_data}
    body = {
        "system_instruction": {"parts": [{"text": _SYSTEM_RULES + _SEARCH_RULES}]},
        "contents": [
            {"role": "user", "parts": [{"text": json.dumps(user_payload, ensure_ascii=False, default=str)}]}
        ],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},   # بدون responseMimeType: مو متوافق مع أداة البحث
    }
    try:
        resp = requests.post(
            _GEMINI_SEARCH_URL, params={"key": GEMINI_API_KEY}, json=body, timeout=AI_AGENT_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        parsed = _extract_json(_candidate_text(data))
        reason = str(parsed.get("reason_ar") or "").strip() or "بدون توضيح من الوكيل"
        news = str(parsed.get("news_ar") or "").strip()
        if news:
            reason = f"{reason}\n📰 {news}"
        return {
            "approved": bool(parsed.get("approved")),
            "reason_ar": reason,
            "source": "gemini_search",
            "sources": _grounding_sources(data),
        }
    except Exception as e:
        log.error(f"❌ AI Agent (Gemini + بحث) لـ {symbol}: {e}")
        return {
            "approved": False,
            "reason_ar": f"تعذر البحث عبر Gemini ({type(e).__name__})",
            "source": "error",
            "sources": [],
            "error_type": type(e).__name__,
        }


_CHAT_SEARCH_NOTE = """

🔎 لما المستخدم يسأل عن عملة، عندك أداة بحث بالإنترنت: استخدمها لآخر أخبار العملة بس (اختراق، إلغاء
إدراج، فك قفل، قرار تنظيمي، خبر مهم). اذكر الخبر بجملة وحدة وابدأها بـ «حسب الأخبار:»، ولو ما لقيت
خبر مؤثر قول هيك. أرقام البوت وصفقاته وإعداداته تضل من «المعلومات الحية» حصراً — ما تاخدها من الإنترنت.
تطبق عليها قاعدة الاختصار نفسها (سطر أو سطرين)."""


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

لو المستخدم ذكر اسم عملة أو أكتر (لحد 3)، ممكن توصلك كمان Volume Profile محسوب فعلياً
(POC = أكتر سعر انتداول عليه، VAL/VAH = حدود منطقة القيمة، وأقرب دعم/هدف
حجمي). استخدمه بثقة لو موجود، ولو سأل عنه وما وصلتك بيانات قول إنه تعذر حسابه.

⚠️ قاعدة حاسمة: أنت مستشار بالمحادثة فقط — **ما عندك أي صلاحية تنفذ إجراء**: ما بتقدر
تضيف عملة لقائمة مراقبة، ولا تشتري أو تبيع، ولا تغيّر إعدادات، ولا تفعّل شي. لا تقول أبداً
"تم" أو "ضفت" أو "فعّلت" أو "اشتريت" لأي إجراء — هاد كذب لأنك ما نفّذت شي. لو طلب المستخدم
إجراء، وضّح إنك ما بتقدر تنفذه من هون، ووجّهه للطريقة الصحيحة: للمراقبة مع شراء يكتب
مثلاً «راقب AVAX واشتري» (فبيطلع له طلب تأكيد من النظام)، أو يستخدم الأوامر على تيليغرام
(/watch AVAX SOL). والعملات اسمها بالضبط متل ما كتبها المستخدم — لا تبدّل عملة بعملة تانية؛
لو ما عرفت العملة قول إنك ما تعرفها.

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


def chat(message: str, context: str = "", history: list = None, use_search: bool = False) -> dict:
    """
    محادثة حرة عامة مع الوكيل (مو تقييم صفقة). الجواب مختصر دايماً.

    Args:
        message: رسالة المستخدم الحالية
        context: سياق حي عن حالة البوت (صفقات مفتوحة، سجل، إحصائيات) —
            يُعاد بناؤه بكل طلب عشان يكون محدّث دايماً، مش جزء من history
        history: قائمة الرسائل السابقة بنفس الجلسة، كل عنصر
            {"role": "user"|"model", "text": str}، بالترتيب الزمني
        use_search: لو True (المستخدم ذكر عملة) نجرّب أول شي مع بحث بالإنترنت لأخبار العملة؛
            لو البحث ما اشتغل (مو مدعوم مثلاً) بنرجع للمحادثة العادية بدون بحث

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

    if use_search:
        search_body = {
            "system_instruction": {"parts": [{"text": _CHAT_RULES + _CHAT_SEARCH_NOTE}]},
            "contents": contents,
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.15, "maxOutputTokens": 400},
        }
        try:
            resp = requests.post(_GEMINI_SEARCH_URL, params={"key": GEMINI_API_KEY}, json=search_body, timeout=AI_AGENT_SEARCH_TIMEOUT)
            resp.raise_for_status()
            text = _candidate_text(resp.json())
            if text:
                return {"ok": True, "reply": text, "source": "gemini_search"}
            log.warning("⚠️ AI Agent شات + بحث: رد فاضي — نرجع للمحادثة بدون بحث")
        except Exception as e:
            log.error(f"❌ AI Agent (Gemini + بحث) شات: {e}")
            if type(e).__name__ in UNREACHABLE_ERRORS:
                return {"ok": False, "reply": f"تعذر التواصل مع الوكيل ({type(e).__name__})", "source": "error"}

    try:
        resp = requests.post(_GEMINI_URL, params={"key": GEMINI_API_KEY}, json=body, timeout=AI_AGENT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return {"ok": True, "reply": text, "source": "gemini"}
    except Exception as e:
        log.error(f"❌ AI Agent (Gemini) شات: {e}")
        return {"ok": False, "reply": f"تعذر التواصل مع الوكيل ({type(e).__name__})", "source": "error"}
