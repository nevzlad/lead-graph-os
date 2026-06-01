import logging
from datetime import datetime, timezone

from celery_app import celery_app
from database_sync import SessionLocal
from models import Post, Source
from services.llm import LLMClient

logger = logging.getLogger(__name__)

@celery_app.task(
    bind=True,
    name="tasks.rewriter.process_post",
    max_retries=2,
    default_retry_delay=60,
    queue="rewriter"
)
def rewrite_post_task(self, post_id: int, tenant_id: str):
    db = SessionLocal()
    try:
        post = db.query(Post).filter(
            Post.id == post_id,
            Post.tenant_id == tenant_id,
            Post.status == "raw"
        ).first()
        if not post:
            logger.info(f"Post {post_id} not found or already processed for tenant {tenant_id}")
            return "skipped"

        source = db.query(Source).filter(
            Source.id == post.source_id,
            Source.tenant_id == tenant_id
        ).first()
        niche = (source.config or {}).get("niche", "news") if source else "news"

        client = LLMClient()
        result = client.rewrite(tenant_id, niche, post.content or "")

        post.content = result["content"]
        post.status = "rewritten" if result["status"] == "success" else "rewritten_fallback"
        post.updated_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(f"Tenant {tenant_id}: post {post_id} rewritten (status={result['status']})")
        return result["status"]

    except Exception as exc:
        db.rollback()
        logger.error(f"Rewrite task failed: {exc}")
        self.retry(exc=exc)
    finally:
        db.close()
