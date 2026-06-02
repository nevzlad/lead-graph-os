import asyncio
import logging
import random
from datetime import datetime, timezone

from sqlalchemy import select

from collectors.rss import RSSCollector
from config import settings
from models import Post, Schedule, Source, TenantConfig
from services.llm import LLMClient
from services.telegram import _send_message_async
from utils.db import async_session_factory

logger = logging.getLogger(__name__)

_rss_collector = RSSCollector()
_llm_client = LLMClient()


async def collect_source(source_id: int, tenant_id: str) -> int:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.id == source_id,
                Source.tenant_id == tenant_id,
                Source.is_active == 1,
            )
        )
        source = result.scalar_one_or_none()
        if not source:
            return 0

        if source.source_type != "rss":
            return 0

        loop = asyncio.get_running_loop()
        raw_items = await loop.run_in_executor(
            None, lambda: _rss_collector.fetch(source.url, source.config or {})
        )

        inserted = 0
        for item in raw_items:
            exists = await session.execute(
                select(Post).where(
                    Post.source_id == source_id,
                    Post.tenant_id == tenant_id,
                    Post.title == item["title"],
                )
            )
            if exists.scalar_one_or_none():
                continue

            post = Post(
                tenant_id=tenant_id,
                source_id=source_id,
                title=item["title"],
                content=item["content"][:4000],
                status="raw",
                created_at=datetime.now(timezone.utc),
            )
            session.add(post)
            inserted += 1

        source.last_fetched = datetime.now(timezone.utc)
        await session.commit()

    if inserted:
        logger.info(f"Tenant {tenant_id}: collected {inserted} items from source {source_id}")
    return inserted


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

        tr = await session.execute(
            select(TenantConfig.niche).where(TenantConfig.tenant_id == tenant_id)
        )
        cfg_niche = tr.scalar_one_or_none()
        if cfg_niche:
            niche = cfg_niche

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: _llm_client.rewrite(tenant_id, niche, post.content or "")
    )

    async with async_session_factory() as session:
        reresult = await session.execute(
            select(Post).where(Post.id == post_id, Post.tenant_id == tenant_id)
        )
        post = reresult.scalar_one_or_none()
        if not post:
            return "skipped"
        post.content = result["content"]
        post.status = "rewritten" if result["status"] == "success" else "rewritten_fallback"
        post.updated_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info(f"Tenant {tenant_id}: post {post_id} rewritten ({result['status']})")
    return result["status"]


async def publish_post(post_id: int, tenant_id: str, chat_id: str) -> str:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Post).where(
                Post.id == post_id,
                Post.tenant_id == tenant_id,
                Post.status.in_(["rewritten", "rewritten_fallback"]),
            )
        )
        post = result.scalar_one_or_none()
        if not post:
            return "skipped"

        src_result = await session.execute(
            select(Source).where(Source.id == post.source_id, Source.tenant_id == tenant_id)
        )
        source = src_result.scalar_one_or_none()
        channel = (source.config or {}).get("tg_channel_id", chat_id)

    text = (post.content or "")[:4096]
    try:
        external_id = await _send_message_async(channel, text)
    except Exception as e:
        logger.error(f"Publish failed for post {post_id}: {e}")
        async with async_session_factory() as session:
            reresult = await session.execute(
                select(Post).where(Post.id == post_id, Post.tenant_id == tenant_id)
            )
            post = reresult.scalar_one_or_none()
            if post:
                post.status = "failed"
                post.updated_at = datetime.now(timezone.utc)
                await session.commit()
        return "failed"

    async with async_session_factory() as session:
        reresult = await session.execute(
            select(Post).where(Post.id == post_id, Post.tenant_id == tenant_id)
        )
        post = reresult.scalar_one_or_none()
        if post:
            post.external_id = external_id
            post.status = "published"
            post.scheduled_at = datetime.now(timezone.utc)
            post.updated_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info(f"Tenant {tenant_id}: post {post_id} published (msg_id={external_id})")
    return "published"


async def _should_publish_now(tenant_id: str) -> list[Schedule]:
    now = datetime.now(timezone.utc)
    current_minutes = now.hour * 60 + now.minute
    matches = []
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Schedule).where(
                Schedule.tenant_id == tenant_id,
                Schedule.is_active,
            )
        )
        for s in rows.scalars().all():
            if s.day_of_week is not None and s.day_of_week != now.weekday():
                continue
            sm = int(s.publish_time[:2]) * 60 + int(s.publish_time[3:])
            if abs(sm - current_minutes) <= 5:
                matches.append(s)
    return matches


async def _publish_one(tenant_id: str, chat_id: str, niche: str | None = None) -> int:
    async with async_session_factory() as session:
        q = select(Post).where(
            Post.tenant_id == tenant_id,
            Post.status.in_(["rewritten", "rewritten_fallback"]),
        )
        if niche:
            q = q.where(Post.title.ilike(f"%{niche}%"))
        q = q.order_by(Post.created_at).limit(1)
        row = await session.execute(q)
        post = row.scalar_one_or_none()

    if not post and niche:
        return await _publish_one(tenant_id, chat_id, None)

    if not post:
        return 0

    status = await publish_post(post.id, tenant_id, chat_id)
    return 1 if status == "published" else 0


async def run_tenant_pipeline(tenant_id: str, chat_id: str) -> dict:
    counts = {"collected": 0, "rewritten": 0, "published": 0}

    async with async_session_factory() as session:
        sources = await session.execute(
            select(Source).where(
                Source.tenant_id == tenant_id,
                Source.is_active == 1,
            )
        )
        source_list = sources.scalars().all()

    for source in source_list:
        collected = await collect_source(source.id, tenant_id)
        counts["collected"] += collected

    async with async_session_factory() as session:
        raw_posts = await session.execute(
            select(Post).where(
                Post.tenant_id == tenant_id,
                Post.status == "raw",
            )
        )
        for post in raw_posts.scalars().all():
            status = await rewrite_post(post.id, tenant_id)
            if status in ("rewritten", "rewritten_fallback"):
                counts["rewritten"] += 1

    schedules = await _should_publish_now(tenant_id)
    if not schedules:
        return counts

    jitter = random.uniform(settings.PUBLISHER_JITTER_MIN, settings.PUBLISHER_JITTER_MAX)
    await asyncio.sleep(jitter)

    for s in schedules:
        published = await _publish_one(tenant_id, chat_id, s.niche)
        counts["published"] += published

    return counts


async def pipeline_loop():
    logger.info("Pipeline loop started.")
    while True:
        try:
            async with async_session_factory() as session:
                tenants = await session.execute(
                    select(TenantConfig.tenant_id, TenantConfig.tg_chat_id)
                )
                all_tenants = tenants.all()

            for tenant_id, chat_id in all_tenants:
                try:
                    counts = await run_tenant_pipeline(tenant_id, chat_id)
                    if any(v for v in counts.values()):
                        logger.info(f"Tenant {tenant_id}: {counts}")
                except Exception as e:
                    logger.error(f"Pipeline error for tenant {tenant_id}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Pipeline loop error: {e}", exc_info=True)

        await asyncio.sleep(300)
