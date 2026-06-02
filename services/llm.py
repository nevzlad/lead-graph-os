import logging

from config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    def rewrite(self, tenant_id: str, niche: str, text: str, target_lang: str | None = None) -> dict:
        if not text or not text.strip():
            return {"content": text, "status": "fallback"}

        from services.llm_router import route
        return route(tenant_id, niche, text, target_lang)
