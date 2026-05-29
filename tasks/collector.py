import logging
from datetime import datetime, timezone
from celery_app import celery_app
from database_sync import SessionLocal
from models import Source, Post
from collectors.rss import RSSCollector
from config import settings

logger = logging.getLogger(__name__)

@celery_app.task(
    bind=True,
    name="tasks.collector.fetch_source",
    max_retries=3,
    default_retry_delay=120,
    queue="collector"
)
def fetch_source_task(self, source_id: int, tenant_id: str):
    db = SessionLocal()
    try:
        source = db.query(Source).filter(
            Source.id == source_id,
            Source.tenant_id == tenant_id,
            Source.is_active == 1
        ).first()
        
        if not source:
            logger.info(f"Source {source_id} inactive/not found for tenant {tenant_id}")
            return "skipped"

        if source.source_type == "rss":
            collector = RSSCollector()
            raw_items = collector.fetch(source.url, source.config or {})
        else:
            logger.warning(f"Unsupported source_type: {source.source_type}")
            return "unsupported_type"

        inserted = 0
        for item in raw_items:
            exists = db.query(Post).filter(
                Post.source_id == source_id,
                Post.tenant_id == tenant_id,
                Post.title == item["title"]
            ).first()
            
            if not exists:
                new_post = Post(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    title=item["title"],
                    content=item["content"][:4000],
                    status="raw",
                    created_at=datetime.now(timezone.utc)
                )
                db.add(new_post)
                inserted += 1

        db.commit()
        source.last_fetched = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Tenant {tenant_id}: collected {inserted} new items from source {source_id}")
        return f"collected_{inserted}"

    except Exception as exc:
        db.rollback()
        logger.error(f"Task fetch_source_task failed: {exc}")
        self.retry(exc=exc)
    finally:
        db.close()
