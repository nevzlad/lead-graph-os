import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from models import Post, TenantConfig  # noqa: E402
from services.repair import retry_publish  # noqa: E402
from tasks.rewriter import rewrite_post  # noqa: E402
from utils.db import async_session_factory  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("process_queue")


async def process_raw_posts(tenant_id: str) -> int:
    async with async_session_factory() as session:
        raw_posts = await session.execute(
            select(Post).where(
                Post.tenant_id == tenant_id,
                Post.status == "raw",
            )
        )
        posts = raw_posts.scalars().all()

    if not posts:
        return 0

    count = 0
    for post in posts:
        status = await rewrite_post(post.id, tenant_id)
        if status in ("rewritten", "rewritten_fallback"):
            count += 1
            logger.info("Post %d -> %s", post.id, status)
        else:
            logger.info("Post %d skipped (%s)", post.id, status)

    return count


async def process_failed_posts(tenant_id: str) -> int:
    async with async_session_factory() as session:
        failed_posts = await session.execute(
            select(Post).where(
                Post.tenant_id == tenant_id,
                Post.status == "failed",
            )
        )
        posts = failed_posts.scalars().all()

    if not posts:
        return 0

    count = 0
    for post in posts:
        result = await retry_publish(post.id)
        if result["success"]:
            count += 1
            logger.info("Post %d republished successfully", post.id)
        else:
            logger.warning("Post %d retry failed: %s", post.id, result.get("error"))

    return count


async def main():
    logger.info("=== Queue Processing Start ===")

    async with async_session_factory() as session:
        tenants = await session.execute(select(TenantConfig.tenant_id, TenantConfig.tg_chat_id))
        all_tenants = tenants.all()

    if not all_tenants:
        logger.warning("No tenants found")
        return

    total_raw = 0
    total_failed = 0

    for tenant_id, chat_id in all_tenants:
        logger.info("Processing tenant %s (chat=%s)", tenant_id, chat_id)

        raw_count = await process_raw_posts(tenant_id)
        total_raw += raw_count

        failed_count = await process_failed_posts(tenant_id)
        total_failed += failed_count

        logger.info("Tenant %s: %d rewritten, %d retried", tenant_id, raw_count, failed_count)

    logger.info("=== Done: %d rewritten, %d retried ===", total_raw, total_failed)


if __name__ == "__main__":
    asyncio.run(main())
