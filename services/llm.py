import requests
import redis
import logging
from typing import Optional, Dict
from config import settings
from prompts import get_system_prompt

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.headers = {"Authorization": f"Bearer {settings.HF_API_KEY}"}
        self.api_url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"

    def _check_rate_limit(self, tenant_id: str) -> bool:
        key = f"rl:tenant:{tenant_id}"
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, 3600)
        current, _ = pipe.execute()
        if current > settings.RATE_LIMIT_PER_HOUR:
            logger.warning(f"Rate limit exceeded for tenant {tenant_id}")
            return False
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
