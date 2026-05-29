from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class Source(Base):
    __tablename__ = "sources"
    
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    source_type = Column(String(50), nullable=False)  # 'rss', 'api', etc.
    url = Column(String(2048), nullable=False)
    config = Column(JSON, nullable=True)  # max_items, headers, etc.
    is_active = Column(Integer, default=1)  # 1 = active, 0 = inactive
    last_fetched = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f"<Source(id={self.id}, tenant_id={self.tenant_id}, name={self.name}, type={self.source_type})>"

class Post(Base):
    __tablename__ = "posts"
    
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    source_id = Column(Integer, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=True)
    link = Column(String(2048), nullable=True)
    status = Column(String(50), default="raw")  # 'raw', 'rewritten', 'published', etc.
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f"<Post(id={self.id}, tenant_id={self.tenant_id}, source_id={self.source_id}, title={self.title[:50]})>"
