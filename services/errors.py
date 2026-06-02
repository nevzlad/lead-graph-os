import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from models import ServiceError, Source
from utils.db import async_session_factory

logger = logging.getLogger(__name__)


async def analyze_and_fix():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=6)

    async with async_session_factory() as session:
        # Group errors by service
        rows = await session.execute(
            select(ServiceError.service_name, func.count(ServiceError.id)).where(
                ServiceError.created_at >= cutoff,
                ServiceError.resolved == False,
            ).group_by(ServiceError.service_name)
        )
        error_counts = {r[0]: r[1] for r in rows.all()}

        # Auto-disable sources with >5 consecutive errors
        src_rows = await session.execute(
            select(Source).where(Source.is_active == 1)
        )
        for src in src_rows.scalars().all():
            err_count = await session.scalar(
                select(func.count(ServiceError.id)).where(
                    ServiceError.service_name == "rss",
                    ServiceError.resolved == False,
                    ServiceError.created_at >= cutoff,
                )
            )
            if err_count and err_count >= 5:
                src.is_active = 0
                logger.warning("Auto-disabled source %s (%s): %d errors", src.id, src.name, err_count)

        await session.commit()

    if error_counts:
        for svc, count in error_counts.items():
            logger.info("Error stats: %s — %d unresolved errors in last 6h", svc, count)

    return error_counts
