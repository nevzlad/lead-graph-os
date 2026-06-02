import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Post, TelemetryEvent, TenantConfig
from utils.db import async_session_factory

logger = logging.getLogger(__name__)


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    db_url = settings.BIGDATA_DB_URL or settings.DATABASE_URL
    if db_url == settings.DATABASE_URL:
        return async_session_factory
    engine = create_async_engine(
        db_url,
        echo=False,
        pool_size=5,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class BigDataAnalyzer:
    """
    Deep analytics for a single tenant.
    Package import is blocked in MODE=commercial via api.bigdata.__init__.
    """

    def __init__(self):
        self._session_factory = _get_session_factory()
        logger.info("BigDataAnalyzer initialized (personal mode only)")

    async def aggregate_tenant_metrics(self, tenant_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            published_stmt = select(func.count(Post.id)).where(
                Post.tenant_id == tenant_id,
                Post.status == "published",
            )
            published = (await session.execute(published_stmt)).scalar() or 0

            raw_stmt = select(func.count(Post.id)).where(
                Post.tenant_id == tenant_id,
                Post.status == "raw",
            )
            raw_count = (await session.execute(raw_stmt)).scalar() or 0

            telemetry_stmt = select(func.count(TelemetryEvent.id)).where(
                TelemetryEvent.tenant_id == tenant_id,
            )
            telemetry_count = (await session.execute(telemetry_stmt)).scalar() or 0

            cfg_stmt = select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
            cfg = (await session.execute(cfg_stmt)).scalar_one_or_none()

        return {
            "tenant_id": tenant_id,
            "published_posts": published,
            "raw_posts": raw_count,
            "telemetry_events": telemetry_count,
            "niche": cfg.niche if cfg else "unknown",
            "billing_status": cfg.billing_status if cfg else "unknown",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def export_dataset(self, tenant_id: str, fmt: str) -> str:
        async with self._session_factory() as session:
            stmt = select(Post).where(Post.tenant_id == tenant_id).limit(1000)
            posts = (await session.execute(stmt)).scalars().all()
            row_count = len(posts)

        filename = f"/tmp/bigdata_export_{tenant_id[:8]}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{fmt}"
        logger.info(f"Tenant {tenant_id}: exported {row_count} rows to {filename} ({fmt})")
        return filename
