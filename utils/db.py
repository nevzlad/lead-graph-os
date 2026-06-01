from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

_db_url = settings.DATABASE_URL
_connect_args = {}
if "sslmode=require" in _db_url:
    _db_url = _db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _connect_args["ssl"] = "require"

_engine_kwargs = {}
if _connect_args:
    _engine_kwargs["connect_args"] = _connect_args

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=5,
    max_overflow=0,
    pool_pre_ping=True,
    pool_recycle=300,
    **_engine_kwargs,
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
