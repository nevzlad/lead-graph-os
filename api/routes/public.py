from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from config import settings
from models import Post, TenantConfig

router = APIRouter(tags=["public"])


@router.get("/health")
async def health_check():
    return {"status": "ok", "mode": settings.MODE}


@router.get("/tenant/{tenant_id}/stats")
async def tenant_stats(tenant_id: str, db: AsyncSession = Depends(get_db)):
    count_stmt = select(func.count(Post.id)).where(
        Post.tenant_id == tenant_id,
        Post.status == "published",
    )
    count_result = await db.execute(count_stmt)
    published_count = count_result.scalar() or 0

    cfg_stmt = select(TenantConfig.niche).where(TenantConfig.tenant_id == tenant_id)
    cfg_result = await db.execute(cfg_stmt)
    niche = cfg_result.scalar_one_or_none()

    return {
        "tenant_id": tenant_id,
        "published_posts": published_count,
        "niche": niche or "unknown",
    }
