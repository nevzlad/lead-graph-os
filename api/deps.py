from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from utils.db import async_session_factory

internal_token_header = APIKeyHeader(name="X-Internal-Token", auto_error=False)


async def verify_internal_token(
    token: str | None = Depends(internal_token_header),
) -> bool:
    if settings.MODE != "internal":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal endpoints disabled",
        )
    if not token or token != settings.INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )
    return True


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
