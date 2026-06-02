import json
import logging
import time
from datetime import datetime, timezone

import httpx

from config import settings

logger = logging.getLogger(__name__)


def _redis():
    try:
        import redis as _redis
        return _redis.from_url(settings.REDIS_URL, socket_timeout=3, decode_responses=True)
    except Exception:
        return None


def _cb_key(provider_name: str) -> str:
    return f"cb:{provider_name}"


def _metric_key(tenant_id: str, provider_name: str) -> str:
    return f"llm:{tenant_id}:{provider_name}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def is_circuit_open(provider_name: str) -> bool:
    r = _redis()
    if not r:
        return False
    try:
        return r.exists(_cb_key(provider_name)) > 0
    except Exception:
        return False


def record_error(provider_name: str):
    r = _redis()
    if not r:
        return
    try:
        key = _cb_key(provider_name)
        count = r.incr(key)
        if count == 1:
            r.expire(key, settings.LLM_CB_RECOVERY_SECONDS)
        if int(count) >= settings.LLM_CB_ERROR_THRESHOLD:
            ttl = r.ttl(key)
            if ttl < settings.LLM_CB_RECOVERY_SECONDS:
                r.expire(key, settings.LLM_CB_RECOVERY_SECONDS)
            logger.warning("Circuit BREAKER opened for %s (%d errors, recovery=%ds)",
                           provider_name, count, settings.LLM_CB_RECOVERY_SECONDS)
    except Exception as e:
        logger.error("CB record_error failed: %s", e)


def record_success(provider_name: str):
    r = _redis()
    if not r:
        return
    try:
        key = _cb_key(provider_name)
        r.delete(key)
        logger.debug("Circuit breaker reset for %s (success)", provider_name)
    except Exception as e:
        logger.error("CB record_success failed: %s", e)


def write_metric(tenant_id: str, provider_name: str, status: str, duration_ms: int):
    r = _redis()
    if not r:
        return
    try:
        key = _metric_key(tenant_id, provider_name)
        entry = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider_name,
            "tenant_id": tenant_id,
            "status": status,
            "duration_ms": duration_ms,
        })
        r.rpush(key, entry)
        r.expire(key, settings.LLM_REDIS_TTL)
    except Exception as e:
        logger.error("Metric write failed: %s", e)


def get_tenant_metrics(tenant_id: str, limit: int = 100) -> list[dict]:
    r = _redis()
    if not r:
        return []
    try:
        keys = r.keys(f"llm:{tenant_id}:*")
        results = []
        for key in keys:
            entries = r.lrange(key, -limit, -1)
            for e in entries:
                try:
                    results.append(json.loads(e))
                except Exception:
                    pass
        return sorted(results, key=lambda x: x.get("ts", ""), reverse=True)[:limit]
    except Exception as e:
        logger.error("Metric read failed: %s", e)
        return []


def get_provider_health() -> dict[str, bool]:
    health = {}
    for p in settings.PROVIDERS:
        health[p["name"]] = not is_circuit_open(p["name"])
    return health


def get_healthy_providers() -> list[str]:
    return [p["name"] for p in settings.PROVIDERS if not is_circuit_open(p["name"])]


def get_all_providers() -> list[str]:
    return [p["name"] for p in settings.PROVIDERS]


def _call_hf(provider: dict, prompt: str) -> dict:
    url = f"https://api-inference.huggingface.co/models/{provider['model']}"
    headers = {"Authorization": f"Bearer {provider['key']}"}
    payload = {
        "inputs": f"<s>[INST] {prompt} [/INST]",
        "parameters": {
            "max_new_tokens": settings.LLM_MAX_TOKENS,
            "temperature": 0.7,
            "return_full_text": False,
        },
    }
    resp = httpx.post(url, headers=headers, json=payload, timeout=settings.LLM_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data:
        raw = data[0].get("generated_text", "")
    elif isinstance(data, dict):
        raw = data.get("generated_text", "")
    else:
        raw = str(data)
    return {"content": raw[:4000]}


def _call_openai_compat(provider: dict, prompt: str) -> dict:
    base = provider.get("base_url", "").rstrip("/")
    url = f"{base}/chat/completions"
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
    resp = httpx.post(url, headers=headers, json=payload, timeout=settings.LLM_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
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
    resp = httpx.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=settings.LLM_TIMEOUT)
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
    resp = httpx.post("https://api.cohere.com/v2/chat", headers=headers, json=payload, timeout=settings.LLM_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    raw = ""
    if isinstance(data.get("message"), dict):
        raw = data["message"].get("content", [{}])[0].get("text", "")
    else:
        raw = data.get("text", "")
    return {"content": raw[:4000]}


_CALLERS = {
    "hf": _call_hf,
    "openai_compat": _call_openai_compat,
    "gemini": _call_gemini,
    "cohere": _call_cohere,
}


def route(tenant_id: str, niche: str, text: str, target_lang: str | None = None) -> dict:
    if not text or not text.strip():
        return {"content": text, "provider": None, "status": "fallback", "validation_errors": []}

    from services.prompts_engine import build_prompt

    errors = []
    for provider in settings.PROVIDERS:
        if is_circuit_open(provider["name"]):
            logger.info("Tenant %s: CB open for %s, skip", tenant_id, provider["name"])
            errors.append(f"{provider['name']}: circuit open")
            continue

        caller = _CALLERS.get(provider["type"])
        if not caller:
            errors.append(f"{provider['name']}: unknown type {provider['type']}")
            continue

        prompt = build_prompt(niche, text, target_lang=target_lang)
        start = time.monotonic()
        try:
            result = caller(provider, prompt)
            duration = int((time.monotonic() - start) * 1000)
            if result and result.get("content"):
                record_success(provider["name"])
                write_metric(tenant_id, provider["name"], "success", duration)
                logger.info("Tenant %s: provider=%s status=success duration=%dms chars=%d",
                            tenant_id, provider["name"], duration, len(result["content"]))
                return {
                    "content": result["content"],
                    "provider": provider["name"],
                    "status": "success",
                    "validation_errors": [],
                }
            else:
                record_error(provider["name"])
                write_metric(tenant_id, provider["name"], "empty_response", duration)
                errors.append(f"{provider['name']}: empty response")
                logger.warning("Tenant %s: provider=%s returned empty", tenant_id, provider["name"])
        except httpx.TimeoutException:
            record_error(provider["name"])
            write_metric(tenant_id, provider["name"], "timeout", int((time.monotonic() - start) * 1000))
            errors.append(f"{provider['name']}: timeout")
            logger.warning("Tenant %s: provider=%s timeout", tenant_id, provider["name"])
        except httpx.HTTPStatusError as e:
            record_error(provider["name"])
            code = e.response.status_code
            write_metric(tenant_id, provider["name"], f"http_{code}", int((time.monotonic() - start) * 1000))
            errors.append(f"{provider['name']}: HTTP {code}")
            logger.warning("Tenant %s: provider=%s HTTP %d", tenant_id, provider["name"], code)
        except Exception as e:
            record_error(provider["name"])
            duration = int((time.monotonic() - start) * 1000)
            write_metric(tenant_id, provider["name"], "error", duration)
            err_msg = str(e)[:200]
            errors.append(f"{provider['name']}: {err_msg}")
            logger.warning("Tenant %s: provider=%s error: %s", tenant_id, provider["name"], err_msg)

    logger.error("Tenant %s: all providers failed: %s", tenant_id, "; ".join(errors))
    return {
        "content": text,
        "provider": None,
        "status": "fallback",
        "validation_errors": errors,
    }
