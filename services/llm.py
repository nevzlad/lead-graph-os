import json
import logging
import time
from typing import Any

import requests

from config import settings


logger = logging.getLogger(__name__)

HF_API_URL = "https://api-inference.huggingface.co/models/{model}"

# Provider chain with fallback
PROVIDERS = []


def _init_providers():
    global PROVIDERS
    PROVIDERS = []

    if settings.HF_API_KEY:
        PROVIDERS.append({
            "name": "huggingface",
            "model": settings.LLM_MODEL or "mistralai/Mistral-7B-Instruct-v0.3",
            "key": settings.HF_API_KEY,
            "call": _call_hf,
        })

    if settings.OPENAI_API_KEY:
        PROVIDERS.append({
            "name": "openai",
            "model": settings.OPENAI_MODEL or "gpt-4o-mini",
            "key": settings.OPENAI_API_KEY,
            "call": _call_openai,
        })


PROMPT_TEMPLATES = {
    "news": (
        "Перепиши новость в стиле информационного Telegram-канала. "
        "Сделай текст уникальным, переформулируй, но сохрани все факты. "
        "Не копируй исходный текст. Напиши своими словами. "
        "Ответ верни в JSON: {\"content\": \"текст\"}"
    ),
    "blog": (
        "Перепиши пост для блога в разговорном, живом стиле. "
        "Сделай текст уникальным, добавь свои формулировки. "
        "Ответ верни в JSON: {\"content\": \"текст\"}"
    ),
    "shop": (
        "Перепиши описание товара в продающем стиле для Telegram. "
        "Сделай текст уникальным, переформулируй преимущества. "
        "Ответ верни в JSON: {\"content\": \"текст\"}"
    ),
}


class LLMClient:
    def __init__(self):
        _init_providers()

    def rewrite(self, tenant_id: str, niche: str, text: str) -> dict:
        if not text or not text.strip():
            return {"content": text, "status": "fallback"}

        prompt = PROMPT_TEMPLATES.get(niche, PROMPT_TEMPLATES["news"])
        full_prompt = f"{prompt}\n\nИсходный текст:\n{text[:2000]}"

        errors = []
        for provider in PROVIDERS:
            degraded = _is_degraded() and provider["name"] != "huggingface"
            if degraded:
                logger.info("Skipping provider %s (LLM degraded, using primary)", provider["name"])
                continue

            try:
                result = provider["call"](provider["key"], provider["model"], full_prompt, niche)
                if result and result.get("content"):
                    return result
                errors.append(f"{provider['name']}: empty response")
            except Exception as e:
                err_msg = str(e)[:200]
                logger.warning("Provider %s failed: %s", provider["name"], err_msg)
                errors.append(f"{provider['name']}: {err_msg}")

        logger.error("All LLM providers failed: %s", "; ".join(errors))
        return {"content": text, "status": "fallback"}


def _is_degraded() -> bool:
    from services.health import is_llm_degraded
    return is_llm_degraded()


def _call_hf(api_key: str, model: str, prompt: str, niche: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "inputs": f"<s>[INST] {prompt} [/INST]",
        "parameters": {
            "max_new_tokens": settings.LLM_MAX_TOKENS,
            "temperature": 0.7,
            "return_full_text": False,
        },
    }
    url = HF_API_URL.format(model=model)
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list) and data:
        raw = data[0].get("generated_text", "")
    elif isinstance(data, dict):
        raw = data.get("generated_text", "")
    else:
        raw = str(data)

    return _extract_json(raw, niche)


def _call_openai(api_key: str, model: str, prompt: str, niche: str) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": settings.LLM_MAX_TOKENS,
        "temperature": 0.7,
    }
    url = settings.OPENAI_BASE_URL or "https://api.openai.com/v1/chat/completions"
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _extract_json(raw, niche)


def _extract_json(raw: str, niche: str) -> dict:
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    if raw.startswith("```"):
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    break
    return {"content": raw[:4000], "status": "success"}
