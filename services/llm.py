import json
import logging
import re

import requests

from config import settings

logger = logging.getLogger(__name__)

HF_API_URL = "https://api-inference.huggingface.co/models/{model}"

PROVIDERS = []
PROVIDER_HEALTH: dict[str, bool] = {}


def init_providers():
    global PROVIDERS, PROVIDER_HEALTH
    PROVIDERS = []
    PROVIDER_HEALTH = {}

    if settings.HF_API_KEY:
        PROVIDERS.append({
            "name": "huggingface",
            "model": settings.LLM_MODEL or "mistralai/Mistral-7B-Instruct-v0.3",
            "key": settings.HF_API_KEY,
            "call": _call_hf,
        })
        PROVIDER_HEALTH["huggingface"] = True

    if settings.OPENAI_API_KEY:
        PROVIDERS.append({
            "name": "openai",
            "model": settings.OPENAI_MODEL or "gpt-4o-mini",
            "key": settings.OPENAI_API_KEY,
            "base_url": settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
            "call": _call_openai_compat,
        })
        PROVIDER_HEALTH["openai"] = True

    if settings.GROQ_API_KEY:
        PROVIDERS.append({
            "name": "groq",
            "model": settings.GROQ_MODEL or "llama-3.3-70b-versatile",
            "key": settings.GROQ_API_KEY,
            "base_url": "https://api.groq.com/openai/v1",
            "call": _call_openai_compat,
        })
        PROVIDER_HEALTH["groq"] = True

    if settings.GEMINI_API_KEY:
        PROVIDERS.append({
            "name": "gemini",
            "model": settings.GEMINI_MODEL or "gemini-2.0-flash",
            "key": settings.GEMINI_API_KEY,
            "call": _call_gemini,
        })
        PROVIDER_HEALTH["gemini"] = True

    if settings.DEEPSEEK_API_KEY:
        PROVIDERS.append({
            "name": "deepseek",
            "model": settings.DEEPSEEK_MODEL or "deepseek-chat",
            "key": settings.DEEPSEEK_API_KEY,
            "base_url": "https://api.deepseek.com",
            "call": _call_openai_compat,
        })
        PROVIDER_HEALTH["deepseek"] = True

    if settings.COHERE_API_KEY:
        PROVIDERS.append({
            "name": "cohere",
            "model": settings.COHERE_MODEL or "command-r",
            "key": settings.COHERE_API_KEY,
            "call": _call_cohere,
        })
        PROVIDER_HEALTH["cohere"] = True

    if settings.TOGETHER_API_KEY:
        PROVIDERS.append({
            "name": "together",
            "model": settings.TOGETHER_MODEL or "mistralai/Mixtral-8x7B-Instruct-v0.1",
            "key": settings.TOGETHER_API_KEY,
            "base_url": "https://api.together.xyz/v1",
            "call": _call_openai_compat,
        })
        PROVIDER_HEALTH["together"] = True

    logger.info("LLM providers initialized: %s", [p["name"] for p in PROVIDERS])


PROMPT_TEMPLATES = {
    "news": (
        "Перепиши новость в стиле информационного Telegram-канала. "
        "Сделай текст уникальным, переформулируй, но сохрани все факты. "
        "Не копируй исходный текст. Напиши своими словами. "
        "Ответ верни ТОЛЬКО текстом, без JSON, без пояснений."
    ),
    "blog": (
        "Перепиши пост для блога в разговорном, живом стиле. "
        "Сделай текст уникальным, добавь свои формулировки. "
        "Ответ верни ТОЛЬКО текстом, без JSON, без пояснений."
    ),
    "shop": (
        "Перепиши описание товара в продающем стиле для Telegram. "
        "Сделай текст уникальным, переформулируй преимущества. "
        "Ответ верни ТОЛЬКО текстом, без JSON, без пояснений."
    ),
}


class LLMClient:
    def __init__(self):
        pass

    def rewrite(self, tenant_id: str, niche: str, text: str) -> dict:
        if not text or not text.strip():
            return {"content": text, "status": "fallback"}

        prompt = PROMPT_TEMPLATES.get(niche, PROMPT_TEMPLATES["news"])
        full_prompt = f"{prompt}\n\nИсходный текст:\n{text[:3000]}"

        errors = []
        for provider in PROVIDERS:
            if not PROVIDER_HEALTH.get(provider["name"], True):
                logger.info("Skipping unhealthy provider %s", provider["name"])
                continue

            try:
                result = provider["call"](provider, full_prompt)
                if result and result.get("content"):
                    content = result["content"]
                    if _check_post_completeness(content, text):
                        logger.info("Provider %s: valid response (%d chars)", provider["name"], len(content))
                        return {"content": content, "provider": provider["name"], "status": "success"}
                    else:
                        logger.warning("Provider %s: incomplete response, trying next", provider["name"])
                        errors.append(f"{provider['name']}: incomplete response")
                else:
                    errors.append(f"{provider['name']}: empty response")
                    logger.warning("Provider %s: empty response, trying next", provider["name"])
            except Exception as e:
                err_msg = str(e)[:200]
                logger.warning("Provider %s failed: %s", provider["name"], err_msg)
                errors.append(f"{provider['name']}: {err_msg}")
                PROVIDER_HEALTH[provider["name"]] = False

        logger.error("All LLM providers failed: %s", "; ".join(errors))
        return {"content": text, "status": "fallback", "errors": errors}


def _call_openai_compat(provider: dict, prompt: str) -> dict:
    headers = {
        "Authorization": f"Bearer {provider['key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": provider["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": settings.LLM_MAX_TOKENS,
        "temperature": 0.7,
    }
    url = f"{provider['base_url'].rstrip('/')}/chat/completions"
    resp = requests.post(url, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {"content": raw[:4000]}


def _call_hf(provider: dict, prompt: str) -> dict:
    headers = {"Authorization": f"Bearer {provider['key']}"}
    payload = {
        "inputs": f"<s>[INST] {prompt} [/INST]",
        "parameters": {
            "max_new_tokens": settings.LLM_MAX_TOKENS,
            "temperature": 0.7,
            "return_full_text": False,
        },
    }
    url = HF_API_URL.format(model=provider["model"])
    resp = requests.post(url, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list) and data:
        raw = data[0].get("generated_text", "")
    elif isinstance(data, dict):
        raw = data.get("generated_text", "")
    else:
        raw = str(data)

    return {"content": raw[:4000]}


def _call_gemini(provider: dict, prompt: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{provider['model']}:generateContent?key={provider['key']}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": settings.LLM_MAX_TOKENS,
            "temperature": 0.7,
        },
    }
    resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        raw = " ".join(p.get("text", "") for p in parts)
    else:
        raw = ""
    return {"content": raw[:4000]}


def _call_cohere(provider: dict, prompt: str) -> dict:
    headers = {
        "Authorization": f"Bearer {provider['key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": provider["model"],
        "message": prompt,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "temperature": 0.7,
    }
    resp = requests.post("https://api.cohere.com/v2/chat", headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("message", {}).get("content", [{}])[0].get("text", "") if isinstance(data.get("message"), dict) else data.get("text", "")
    return {"content": raw[:4000]}


def _check_post_completeness(content: str, original: str) -> bool:
    if not content or len(content.strip()) < 30:
        logger.warning("Content too short (%d chars), incomplete", len(content or ""))
        return False

    # Check if response looks like an error/refusal
    refusal_patterns = [
        r"(?i)^(sorry|i apologise|i cannot|i can't|i'm unable|i am unable|извините|не могу|не буду)",
        r"(?i)^(as an ai|as a language model|как ии|как языковая модель)",
    ]
    for pat in refusal_patterns:
        if re.match(pat, content.strip()):
            logger.warning("Refusal pattern detected in LLM response")
            return False

    # Check if content is just the original with minor changes (<30% different)
    from services.antiplag import similarity
    sim = similarity(content, original)
    if sim > 0.95:
        logger.warning("Content too similar to original (%.0f%%), incomplete rewrite", sim * 100)
        return False

    # Check for placeholder/content
    placeholder_patterns = [
        r"(?i)^\[.*(?:content|text|placeholder|заглушка).*\]",
        r"(?i)^(test|тест)$",
    ]
    for pat in placeholder_patterns:
        if re.match(pat, content.strip()[:100]):
            logger.warning("Placeholder content detected")
            return False

    return True


def get_healthy_providers() -> list[str]:
    return [p["name"] for p in PROVIDERS if PROVIDER_HEALTH.get(p["name"], True)]


def get_all_providers() -> list[str]:
    return [p["name"] for p in PROVIDERS]


init_providers()
