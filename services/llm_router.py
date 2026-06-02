import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx

from config import FREE_LLM_PROVIDERS, settings
from services.prompts_engine import RewritePromptBuilder
from services.telegram import strip_html

logger = logging.getLogger(__name__)


def _redis():
    try:
        import redis as _redis
        return _redis.from_url(settings.REDIS_URL, socket_timeout=3, decode_responses=True)
    except Exception:
        return None


class LLMRouter:
    CB_PREFIX = "llm:circuit"
    FAILS_PREFIX = "llm:fails"
    CB_TTL = 300
    FAIL_THRESHOLD = 3
    MIN_CONTENT_LENGTH = 200

    DISABLED_PREFIX = "llm:disabled"
    OPENC0UNT_PREFIX = "llm:opencount"
    MAX_CIRCUIT_OPENS = 2
    DISABLED_TTL = 604800

    def __init__(self):
        self._prompt_builder = RewritePromptBuilder()

    def _is_circuit_open(self, name: str) -> bool:
        r = _redis()
        if not r:
            return False
        try:
            return r.exists(f"{self.CB_PREFIX}:{name}") > 0
        except Exception:
            return False

    def _is_permanently_disabled(self, name: str) -> bool:
        r = _redis()
        if not r:
            return False
        try:
            return r.exists(f"{self.DISABLED_PREFIX}:{name}") > 0
        except Exception:
            return False

    def _permanently_disable(self, name: str):
        r = _redis()
        if not r:
            return
        try:
            r.setex(f"{self.DISABLED_PREFIX}:{name}", self.DISABLED_TTL, "1")
            logger.warning("Provider %s PERMANENTLY DISABLED after repeated circuit opens", name)
        except Exception as e:
            logger.error("permanently_disable failed for %s: %s", name, e)

    def _reenable(self, name: str):
        r = _redis()
        if not r:
            return
        try:
            r.delete(f"{self.DISABLED_PREFIX}:{name}")
            r.delete(f"{self.OPENC0UNT_PREFIX}:{name}")
            r.delete(f"{self.CB_PREFIX}:{name}")
            r.delete(f"{self.FAILS_PREFIX}:{name}")
            logger.info("Provider %s re-enabled", name)
        except Exception as e:
            logger.error("reenable failed for %s: %s", name, e)

    def _record_success(self, name: str):
        r = _redis()
        if not r:
            return
        try:
            r.delete(f"{self.CB_PREFIX}:{name}")
            r.delete(f"{self.FAILS_PREFIX}:{name}")
            r.delete(f"{self.OPENC0UNT_PREFIX}:{name}")
            r.delete(f"{self.DISABLED_PREFIX}:{name}")
        except Exception as e:
            logger.error("record_success failed for %s: %s", name, e)

    def _record_failure(self, name: str):
        r = _redis()
        if not r:
            return
        try:
            fails_key = f"{self.FAILS_PREFIX}:{name}"
            count = r.incr(fails_key)
            r.expire(fails_key, self.CB_TTL)
            if int(count) >= self.FAIL_THRESHOLD:
                r.setex(f"{self.CB_PREFIX}:{name}", self.CB_TTL, "1")
                r.delete(fails_key)
                logger.warning("Circuit BREAKER opened for %s (%d failures, blocking %ds)",
                               name, count, self.CB_TTL)
                open_key = f"{self.OPENC0UNT_PREFIX}:{name}"
                open_count = r.incr(open_key)
                r.expire(open_key, self.DISABLED_TTL)
                if int(open_count) >= self.MAX_CIRCUIT_OPENS:
                    self._permanently_disable(name)
        except Exception as e:
            logger.error("record_failure failed for %s: %s", name, e)

    def _call_provider(self, provider: dict, prompt: str) -> str | None:
        ptype = provider.get("type")
        if ptype == "openai_compat":
            return self._call_openai_compat(provider, prompt)
        elif ptype == "hf":
            return self._call_huggingface(provider, prompt)
        else:
            logger.warning("Provider %s: unsupported type %s, skipping", provider["name"], ptype)
            return None

    def _call_openai_compat(self, provider: dict, prompt: str) -> str | None:
        base = provider.get("base_url", "").rstrip("/")
        if not base:
            logger.error("Provider %s: no base_url", provider["name"])
            return None
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
        try:
            with httpx.Client(timeout=settings.LLM_TIMEOUT) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return raw[:4000] if raw else None
        except Exception as e:
            logger.warning("Provider %s openai_compat call failed: %s", provider["name"], str(e)[:200])
            raise

    def _call_huggingface(self, provider: dict, prompt: str) -> str | None:
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
        try:
            with httpx.Client(timeout=settings.LLM_TIMEOUT) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and data:
                    raw = data[0].get("generated_text", "")
                elif isinstance(data, dict):
                    raw = data.get("generated_text", "")
                else:
                    raw = str(data)
                return raw[:4000] if raw else None
        except Exception as e:
            logger.warning("Provider %s huggingface call failed: %s", provider["name"], str(e)[:200])
            raise

    def _build_prompt(self, text: str, target_lang: str) -> str:
        return self._prompt_builder.build_rewrite_prompt(
            text=text,
            target_lang=target_lang,
            niche="news",
        )

    def rewrite_with_failover(self, tenant_id: str, raw_text: str, target_lang: str) -> dict:
        if not raw_text or not raw_text.strip():
            return {"status": "fallback", "content": "", "provider": None}

        prompt = self._build_prompt(raw_text, target_lang)
        errors = []

        for provider_cfg in FREE_LLM_PROVIDERS:
            name = provider_cfg["name"]
            key = os.getenv(provider_cfg["key_env"], "")
            if not key:
                logger.info("Tenant %s: %s no key, skip", tenant_id, name)
                continue

            if self._is_permanently_disabled(name):
                logger.info("Tenant %s: %s permanently disabled, skip", tenant_id, name)
                errors.append(f"{name}: permanently disabled")
                continue

            if self._is_circuit_open(name):
                logger.info("Tenant %s: CB open for %s, skip", tenant_id, name)
                errors.append(f"{name}: circuit open")
                continue

            provider = dict(provider_cfg)
            provider["key"] = key

            start = time.monotonic()
            try:
                content = self._call_provider(provider, prompt)
                duration = int((time.monotonic() - start) * 1000)

                if content and len(content) >= self.MIN_CONTENT_LENGTH:
                    self._record_success(name)
                    logger.info("Tenant %s: provider=%s status=success duration=%dms chars=%d",
                                tenant_id, name, duration, len(content))
                    return {"status": "success", "content": content, "provider": name}
                else:
                    self._record_failure(name)
                    reason = "empty" if not content else f"too_short:{len(content)}"
                    errors.append(f"{name}: {reason}")
                    logger.warning("Tenant %s: provider=%s %s (duration=%dms)",
                                   tenant_id, name, reason, duration)
            except httpx.TimeoutException:
                self._record_failure(name)
                duration = int((time.monotonic() - start) * 1000)
                errors.append(f"{name}: timeout")
                logger.warning("Tenant %s: provider=%s timeout (%dms)", tenant_id, name, duration)
            except httpx.HTTPStatusError as e:
                self._record_failure(name)
                code = e.response.status_code
                duration = int((time.monotonic() - start) * 1000)
                errors.append(f"{name}: HTTP {code}")
                logger.warning("Tenant %s: provider=%s HTTP %d (%dms)", tenant_id, name, code, duration)
            except Exception as e:
                self._record_failure(name)
                duration = int((time.monotonic() - start) * 1000)
                err_msg = str(e)[:200]
                errors.append(f"{name}: {err_msg}")
                logger.warning("Tenant %s: provider=%s error: %s (%dms)", tenant_id, name, err_msg, duration)

        logger.error("Tenant %s: all providers failed: %s", tenant_id, "; ".join(errors))
        fallback_text = strip_html(raw_text)[:4000]
        return {"status": "fallback", "content": fallback_text, "provider": None}


_router = LLMRouter()


def route(tenant_id: str, niche: str, text: str, target_lang: str | None = None) -> dict:
    lang = target_lang or settings.TARGET_LANGUAGE
    result = _router.rewrite_with_failover(tenant_id, text, lang)
    if result["status"] == "success":
        return {
            "content": result["content"],
            "provider": result["provider"],
            "status": "success",
            "validation_errors": [],
        }
    return {
        "content": result.get("content", text),
        "provider": None,
        "status": "fallback",
        "validation_errors": [],
    }


def is_circuit_open(provider_name: str) -> bool:
    return _router._is_circuit_open(provider_name)


def record_success(provider_name: str):
    _router._record_success(provider_name)


def record_error(provider_name: str):
    _router._record_failure(provider_name)


def get_provider_health() -> dict[str, str]:
    health = {}
    for p in FREE_LLM_PROVIDERS:
        name = p["name"]
        if _router._is_permanently_disabled(name):
            health[name] = "disabled"
        elif _router._is_circuit_open(name):
            health[name] = "broken"
        else:
            health[name] = "ok"
    return health


def get_disabled_providers() -> list[str]:
    return [p["name"] for p in FREE_LLM_PROVIDERS if _router._is_permanently_disabled(p["name"])]


def get_broken_providers() -> list[str]:
    return [p["name"] for p in FREE_LLM_PROVIDERS if _router._is_circuit_open(p["name"])]


def get_healthy_providers() -> list[str]:
    return [p["name"] for p in FREE_LLM_PROVIDERS
            if not _router._is_circuit_open(p["name"])
            and not _router._is_permanently_disabled(p["name"])]


def get_all_providers() -> list[str]:
    return [p["name"] for p in FREE_LLM_PROVIDERS]


def reenable_provider(name: str) -> bool:
    for p in FREE_LLM_PROVIDERS:
        if p["name"] == name:
            _router._reenable(name)
            return True
    return False


def write_metric(tenant_id: str, provider_name: str, status: str, duration_ms: int):
    r = _redis()
    if not r:
        return
    try:
        day_key = f"llm:health:{tenant_id}:{provider_name}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        entry = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider_name,
            "tenant_id": tenant_id,
            "status": status,
            "duration_ms": duration_ms,
        })
        r.rpush(day_key, entry)
        r.expire(day_key, settings.LLM_REDIS_TTL)
    except Exception as e:
        logger.error("Metric write failed: %s", e)


def get_tenant_metrics(tenant_id: str, limit: int = 100) -> list[dict]:
    r = _redis()
    if not r:
        return []
    try:
        keys = r.keys(f"llm:health:{tenant_id}:*")
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
