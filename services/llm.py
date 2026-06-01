import logging
import time
from typing import Dict, Optional

import redis
import requests
from sqlalchemy import select

from config import settings
from database_sync import SessionLocal
from models import TenantConfig
from prompts import get_system_prompt

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        self._redis = None
        try:
            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
            self._redis.ping()
        except Exception as e:
            logger.warning(f"Redis unavailable, rate limiting skipped: {e}")
            self._redis = None
        self.headers = {"Authorization": f"Bearer {settings.HF_API_KEY}"}
        self.api_url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"

    def _effective_rate_limit(self, tenant_id: str) -> int:
        limit = settings.RATE_LIMIT_PER_HOUR
        db = SessionLocal()
        try:
            config = db.execute(
                select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
            ).scalar_one_or_none()
            if config:
                limit += config.api_limit_bonus
        except Exception as e:
            logger.warning(f"Could not load api_limit_bonus for {tenant_id}: {e}")
        finally:
            db.close()
        return limit

    def _check_rate_limit(self, tenant_id: str) -> bool:
        """Sliding-window limit: effective limit requests per tenant per hour."""
        if self._redis is None:
            return True
        effective_limit = self._effective_rate_limit(tenant_id)
        key = f"rl:tenant:{tenant_id}"
        now = time.time()
        window_start = now - 3600
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        current, = pipe.execute()[1:]
        if current >= effective_limit:
            logger.warning(f"Rate limit exceeded for tenant {tenant_id}")
            return False
        self._redis.zadd(key, {str(time.time_ns()): now})
        self._redis.expire(key, 3600)
        return True

    def _call_api(self, prompt: str) -> Optional[str]:
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": settings.LLM_MAX_TOKENS,
                "return_full_text": False,
                "temperature": 0.7,
                "top_p": 0.95
            }
        }
        for attempt in range(2):
            try:
                resp = requests.post(self.api_url, json=payload, headers=self.headers, timeout=45)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0]["generated_text"].strip()
                return None
            except requests.exceptions.RequestException as e:
                logger.error(f"LLM API error (attempt {attempt+1}): {e}")
                if attempt == 0:
                    continue
                return None
        return None

    def rewrite(self, tenant_id: str, niche: str, raw_text: str) -> Dict[str, str]:
        raw_text = raw_text or ""
        if not self._check_rate_limit(tenant_id):
            return {
                "status": "fallback",
                "content": f"[АВТО-РЕРАЙТ] {raw_text[:150]}...\n(Лимит API исчерпан, текст очищен)"
            }

        system_prompt = get_system_prompt(niche)
        full_prompt = f"<s>[INST] {system_prompt} [/INST]\n{raw_text}"
        result = self._call_api(full_prompt)

        if result:
            return {"status": "success", "content": result[:4000]}
        
        return {
            "status": "fallback",
            "content": f"[FALLBACK LLM ERROR] {raw_text[:200]}...\n(Сервис временно недоступен)"
        }
