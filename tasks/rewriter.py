import logging
from datetime import datetime, timezone

from sqlalchemy import select

from config import settings
from models import Post, Source, TenantConfig
from services.llm_router import route as llm_route
from services.validators import validate_all
from utils.db import async_session_factory

logger = logging.getLogger(__name__)


async def rewrite_post(post_id: int, tenant_id: str) -> str:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Post).where(
                Post.id == post_id,
                Post.tenant_id == tenant_id,
                Post.status == "raw",
            )
        )
        post = result.scalar_one_or_none()
        if not post:
            return "skipped"

        src_result = await session.execute(
            select(Source).where(Source.id == post.source_id, Source.tenant_id == tenant_id)
        )
        source = src_result.scalar_one_or_none()
        niche = (source.config or {}).get("niche", "news") if source else "news"

        tc = await session.scalar(
            select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
        )
        if tc:
            if tc.niche:
                niche = tc.niche
            target_lang = tc.language or "ru"
        else:
            target_lang = "ru"

    original_content = post.content or ""
    if not original_content.strip():
        return "skipped"

    # --- Attempt 1: Router → Validator ---
    result1 = llm_route(tenant_id, niche, original_content, target_lang=target_lang)
    content = result1.get("content", original_content)
    provider = result1.get("provider")
    status = result1.get("status", "fallback")

    v_result = validate_all(content, original=original_content, target_lang=target_lang)

    if v_result["is_valid"]:
        is_valid = True
    else:
        # --- Attempt 2: Retry with validation error in prompt ---
        logger.info("Post %d: validation failed %s, retrying with error hint",
                    post_id, v_result["issues"])
        error_type = _first_error_type(v_result["issues"])
        from services.prompts_engine import build_prompt
        retry_prompt = build_prompt(
            niche, original_content,
            target_lang=target_lang,
            validation_error=error_type,
            min_len=settings.LLM_MIN_CONTENT_LENGTH,
        )
        result2 = llm_route(tenant_id, niche, retry_prompt, target_lang=target_lang)
        content2 = result2.get("content", original_content)
        provider2 = result2.get("provider")

        v_result2 = validate_all(content2, original=original_content, target_lang=target_lang)
        if v_result2["is_valid"]:
            content = content2
            provider = provider2
            status = result2.get("status", "fallback")
            is_valid = True
        else:
            logger.warning("Post %d: retry also failed validation %s, using fallback",
                           post_id, v_result2["issues"])
            is_valid = False
            content = _make_fallback_content(original_content)
            status = "fallback"

    # --- Status update ---
    from services.antiplag import add_attribution
    content = add_attribution(content, post.link)

    async with async_session_factory() as session:
        reresult = await session.execute(
            select(Post).where(Post.id == post_id, Post.tenant_id == tenant_id)
        )
        post = reresult.scalar_one_or_none()
        if not post:
            return "skipped"
        post.content = content
        if is_valid and status == "success":
            post.status = "rewritten"
        elif is_valid and status != "success":
            post.status = "rewritten_fallback"
        else:
            post.status = "rewritten_fallback"
        post.updated_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info("Tenant %s post %d: provider=%s is_valid=%s status=%s",
                tenant_id, post_id, provider, is_valid, post.status)
    return post.status


def _first_error_type(issues: list[str]) -> str:
    for iss in issues:
        if iss.startswith("too_short"):
            return "too_short"
        if iss.startswith("wrong_language"):
            return "wrong_language"
        if iss.startswith("too_similar"):
            return "too_similar"
    return "too_short"


def _make_fallback_content(original: str) -> str:
    if len(original) > 300:
        return original[:300] + "...\n\n[FREE_LIMIT_REACHED]"
    return original + "\n\n[FREE_LIMIT_REACHED]"
