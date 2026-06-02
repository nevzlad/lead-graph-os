from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    __abstract__ = True
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class TenantMixin:
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)


class TenantConfig(Base, TenantMixin):
    __tablename__ = "tenant_configs"
    tg_user_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    tg_chat_id: Mapped[str] = mapped_column(String(50), nullable=False)
    niche: Mapped[str] = mapped_column(String(30), default="news", nullable=False)
    billing_status: Mapped[str] = mapped_column(String(20), default="trial", nullable=False)
    telemetry_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    api_limit_bonus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (Index("ix_tenant_configs_tenant", "tenant_id", unique=True),)


class Source(Base, TenantMixin):
    __tablename__ = "sources"
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="RSS Source")
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_fetched: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    __table_args__ = (Index("ix_sources_tenant_active", "tenant_id", "is_active"),)


class Post(Base, TenantMixin):
    __tablename__ = "posts"
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="raw", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'raw', 'rewritten', 'rewritten_fallback', 'published', 'failed')",
            name="ck_post_status",
        ),
        Index("ix_posts_tenant_status", "tenant_id", "status"),
        Index("ix_posts_tenant_scheduled", "tenant_id", "scheduled_at"),
    )


class TelemetryEvent(Base, TenantMixin):
    __tablename__ = "telemetry_events"
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    __table_args__ = (Index("ix_telemetry_tenant_type", "tenant_id", "event_type"),)


class Schedule(Base, TenantMixin):
    __tablename__ = "schedules"
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publish_time: Mapped[str] = mapped_column(String(5), nullable=False)
    niche: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (Index("ix_schedules_tenant", "tenant_id"),)
