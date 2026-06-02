from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings

_db_url = settings.DATABASE_URL
_connect_args = {}
if "sslmode=require" in _db_url:
    _db_url = _db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _connect_args["ssl"] = "require"

_engine_kwargs = {}
if _connect_args:
    _engine_kwargs["connect_args"] = _connect_args

async_engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=5,
    max_overflow=0,
    pool_pre_ping=True,
    pool_recycle=300,
    **_engine_kwargs,
)

async_session_factory = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)

_sync_db_url = settings.DATABASE_URL
if _sync_db_url.startswith("postgresql+asyncpg://"):
    _sync_db_url = _sync_db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
elif _sync_db_url.startswith("postgres+asyncpg://"):
    _sync_db_url = _sync_db_url.replace("postgres+asyncpg://", "postgresql://", 1)

_sync_connect_args = {}
if "sslmode=require" in _sync_db_url:
    _sync_db_url = _sync_db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _sync_connect_args["ssl"] = "require"

sync_engine = create_engine(
    _sync_db_url,
    echo=False,
    pool_size=3,
    max_overflow=0,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=_sync_connect_args if _sync_connect_args else {},
)

sync_session_factory = sessionmaker(
    sync_engine, class_=Session, expire_on_commit=False
)

engine = async_engine


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
