import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from celery_app import celery_app
from config import settings
from models import Post, Source, TenantConfig
from services.antiplag import add_attribution
from services.llm_router import LLMRouter
from services.telegram import strip_html
from services.validators import ContentValidator
from utils.db import async_session_factory, sync_session_factory

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.rewriter.process_post", queue="rewriter", max_retries=1)
def process_post(self, post_id: int, tenant_id: str):
    session = sync_session_factory()
    try:
        post = session.execute(
            select(Post).where(
                Post.id == post_id,
                Post.tenant_id == tenant_id,
                Post.status == "raw",
            )
        ).scalar_one_or_none()
        if not post:
            logger.info("Tenant %s post %d: not found or not raw, skip", tenant_id, post_id)
            return "skipped"

        source = session.get(Source, post.source_id) if post.source_id else None

        tc = session.execute(
            select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if tc:
            target_lang = tc.language or settings.TARGET_LANGUAGE
        else:
            target_lang = settings.TARGET_LANGUAGE

        raw_content = (post.content or "").strip()
        if not raw_content:
            logger.info("Tenant %s post %d: empty content, skip", tenant_id, post_id)
            return "skipped"

        post_link = post.link
        router = LLMRouter()
        validator = ContentValidator()

        result = router.rewrite_with_failover(tenant_id, raw_content, target_lang)
        rewritten = result.get("content", raw_content)
        provider = result.get("provider")

        validation = validator.validate(raw_content, rewritten, target_lang)
        is_valid = validation["is_valid"]

        if not is_valid:
            logger.info("Tenant %s post %d: validation failed %s, retry router",
                        tenant_id, post_id, validation["errors"])
            result2 = router.rewrite_with_failover(tenant_id, raw_content, target_lang)
            rewritten2 = result2.get("content", raw_content)
            provider2 = result2.get("provider")

            validation2 = validator.validate(raw_content, rewritten2, target_lang)
            if validation2["is_valid"]:
                rewritten = rewritten2
                provider = provider2
                is_valid = True
            else:
                logger.warning("Tenant %s post %d: retry also failed validation %s, fallback",
                               tenant_id, post_id, validation2["errors"])
                rewritten = strip_html(raw_content)

        rewritten = add_attribution(rewritten, post_link)

        post.content = rewritten
        if is_valid:
            post.status = "rewritten"
        else:
            post.status = "rewritten_fallback"
        post.updated_at = datetime.now(timezone.utc)
        session.commit()

        logger.info("Tenant %s post %d: provider=%s is_valid=%s status=%s length=%d",
                    tenant_id, post_id, provider, is_valid, post.status, len(rewritten))
        return post.status

    except Exception as exc:
        session.rollback()
        logger.error("Tenant %s post %d: error %s", tenant_id, post_id, str(exc)[:300], exc_info=True)
        raise self.retry(exc=exc, countdown=30)

    finally:
        session.close()


async def rewrite_post(post_id: int, tenant_id: str, force: bool = False) -> str:
    async with async_session_factory() as session:
        conditions = [Post.id == post_id, Post.tenant_id == tenant_id]
        if not force:
            conditions.append(Post.status == "raw")
        result = await session.execute(select(Post).where(*conditions))
        post = result.scalar_one_or_none()
        if not post:
            return "skipped"

        src_result = await session.execute(
            select(Source).where(Source.id == post.source_id, Source.tenant_id == tenant_id)
        )
        source = src_result.scalar_one_or_none()
        if source:
            niche = (source.config or {}).get("niche", "news")
        else:
            niche = "news"

        tc = await session.scalar(
            select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
        )
        if tc:
            if tc.niche:
                niche = tc.niche
            target_lang = tc.language or settings.TARGET_LANGUAGE
        else:
            target_lang = settings.TARGET_LANGUAGE

        raw_content = (post.content or "").strip()
        post_link = post.link

    if not raw_content:
        return "skipped"

    router = LLMRouter()
    validator = ContentValidator()

    loop = asyncio.get_running_loop()
    result = await asyncio.wait_for(
        loop.run_in_executor(None, router.rewrite_with_failover, tenant_id, raw_content, target_lang),
        timeout=90,
    )
    rewritten = result.get("content", raw_content)
    provider = result.get("provider")

    validation = validator.validate(raw_content, rewritten, target_lang)
    is_valid = validation["is_valid"]

    if not is_valid:
        logger.info("Post %d: validation failed %s, retry router", post_id, validation["errors"])
        result2 = await asyncio.wait_for(
            loop.run_in_executor(None, router.rewrite_with_failover, tenant_id, raw_content, target_lang),
            timeout=90,
        )
        rewritten2 = result2.get("content", raw_content)
        provider2 = result2.get("provider")

        validation2 = validator.validate(raw_content, rewritten2, target_lang)
        if validation2["is_valid"]:
            rewritten = rewritten2
            provider = provider2
            is_valid = True
        else:
            logger.warning("Post %d: retry also failed validation %s, fallback",
                           post_id, validation2["errors"])
            rewritten = strip_html(raw_content)

    rewritten = add_attribution(rewritten, post_link)

    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            return "skipped"
        post.content = rewritten
        if is_valid:
            post.status = "rewritten"
        else:
            post.status = "rewritten_fallback"
        post.updated_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info("Tenant %s post %d: provider=%s is_valid=%s status=%s length=%d",
                tenant_id, post_id, provider, is_valid, post.status, len(rewritten))
    return post.status
