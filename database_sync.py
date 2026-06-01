from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import settings

_sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
_connect_args = {}
if "sslmode=require" in _sync_url:
    _sync_url = _sync_url.replace("?sslmode=require", "").replace("&sslmode=require", "")

sync_engine = create_engine(
    _sync_url,
    pool_size=5,
    max_overflow=0,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=_connect_args or None,
)

SessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
