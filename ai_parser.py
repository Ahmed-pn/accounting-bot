"""
وحدة الذكاء الاصطناعي - تفهم رسائل نصية وصوتية حرة وتحولها لعملية محاسبية
تستخدم Gemini من Google (مجاني بحدود سخية جدًا: https://aistudio.google.com)
"""
import os
import json
import logging

import google.generativeai as genai

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

MODEL_NAME = "gemini-2.0-flash"

SYSTEM_PROMPT = """
أنت مساعد محاسبة لمحل تجاري (أي نوع محل: بقالية، صيدلية، عيادة، محل ملابس، إلخ).
مهمتك تحليل رسالة المستخدم (نصية أو صوتية، بالعربية الفصحى أو العامية السورية)
واستخراج JSON فقط بدون أي شرح، بهالشكل بالضبط:

{
  "action": "sale" | "expense" | "purchase" | "debt_i_owe" | "debt_owed_to_me" | "settle" | "unknown",
  "amount": رقم أو null,
  "description": "وصف قصير للعملية أو الصنف" أو "",
  "customer_name": "اسم الزبون" أو null,
  "person_name": "اسم الشخص المرتبط بالدين" أو null
}

قواعد تحديد action:
- sale: المستخدم باع شي (بعت، بيع، صرفت بضاعة لزبون بمبلغ)
- expense: مصروف عام على المحل (كهرباء، إيجار، صيانة...)
- purchase: اشترى بضاعة/مواد ليعيد بيعها أو يستخدمها بالمحل
- debt_i_owe: المستخدم عليه دين لشخص معين (استخدم person_name)
- debt_owed_to_me: شخص معين عليه دين للمستخدم (استخدم person_name)
- settle: المستخدم بدو يسدد دين شخص معين (استخدم person_name، بدون amount لازم)
- unknown: إذا الرسالة مش واضحة أو مش متعلقة بمحاسبة إطلاقًا

أرجع JSON فقط، بدون ```json وبدون أي نص قبله أو بعده.
"""


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(text)
        return {
            "action": data.get("action", "unknown"),
            "amount": data.get("amount"),
            "description": data.get("description") or "",
            "customer_name": data.get("customer_name"),
            "person_name": data.get("person_name"),
        }
    except Exception as e:
        logger.warning(f"فشل تحليل رد الذكاء الاصطناعي: {e} — الرد: {raw_text}")
        return {"action": "unknown", "amount": None, "description": "", "customer_name": None, "person_name": None}


def _empty_result():
    return {"action": "unknown", "amount": None, "description": "", "customer_name": None, "person_name": None}


def is_configured() -> bool:
    return bool(GEMINI_API_KEY)


def parse_text(text: str) -> dict:
    if not is_configured():
        return _empty_result()
    try:
        model = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PROMPT)
        response = model.generate_content(text)
        return _extract_json(response.text)
    except Exception as e:
        logger.error(f"خطأ باستدعاء Gemini (نص): {e}")
        return _empty_result()


def parse_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> dict:
    if not is_configured():
        return _empty_result()
    try:
        model = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PROMPT)
        response = model.generate_content([
            {"mime_type": mime_type, "data": audio_bytes},
            "حلل هالرسالة الصوتية واستخرج الـ JSON المطلوب."
        ])
        return _extract_json(response.text)
    except Exception as e:
        logger.error(f"خطأ باستدعاء Gemini (صوت): {e}")
        return _empty_result()
