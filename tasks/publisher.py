import logging
import random
from datetime import datetime, timezone

from celery.exceptions import Retry

from celery_app import celery_app
from config import settings
from database_sync import SessionLocal
from models import Post, Source
from services.telegram import send_message

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="tasks.publisher.publish_post",
    max_retries=3,
    default_retry_delay=120,
    queue="publisher",
    acks_late=True,
)
def publish_post_task(self, post_id: int, tenant_id: str, chat_id: str):
    db = SessionLocal()
    jitter = random.uniform(settings.PUBLISHER_JITTER_MIN, settings.PUBLISHER_JITTER_MAX)

    try:
        if not self.request.delivery_info.get("redelivered", False):
            logger.info(f"Tenant {tenant_id}: post {post_id} scheduled with jitter {jitter:.1f}s")
            raise self.retry(countdown=jitter, exc=RuntimeError("Initial jitter delay"))

        post = (
            db.query(Post)
            .filter(
                Post.id == post_id,
                Post.tenant_id == tenant_id,
                Post.status.in_(["rewritten", "rewritten_fallback"]),
            )
            .first()
        )

        if not post:
            logger.info(f"Post {post_id} not eligible for publishing tenant {tenant_id}")
            return "skipped"

        source = (
            db.query(Source)
            .filter(
                Source.id == post.source_id,
                Source.tenant_id == tenant_id,
            )
            .first()
        )
        channel = (source.config or {}).get("tg_channel_id", chat_id)

        text = (post.content or "")[:4096]
        external_id = send_message(channel, text)
        post.external_id = external_id
        post.status = "published"
        post.scheduled_at = datetime.now(timezone.utc)
        post.updated_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(f"Tenant {tenant_id}: post {post_id} published (msg_id={external_id})")
        return "published"

    except Retry:
        raise
    except Exception as exc:
        db.rollback()
        logger.error(f"Publish task failed: {exc}")

        if self.request.retries >= self.max_retries:
            post = (
                db.query(Post)
                .filter(
                    Post.id == post_id,
                    Post.tenant_id == tenant_id,
                )
                .first()
            )
            if post:
                post.status = "failed"
                post.updated_at = datetime.now(timezone.utc)
                db.commit()
            handle_dlq_task.apply_async(
                args=[post_id, tenant_id],
                queue="publisher_dlq",
            )
            logger.warning(f"Tenant {tenant_id}: post {post_id} moved to DLQ")
            return "moved_to_dlq"

        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
    finally:
        db.close()


@celery_app.task(
    name="tasks.publisher.handle_dlq",
    queue="publisher_dlq",
)
def handle_dlq_task(post_id: int, tenant_id: str):
    logger.warning(f"DLQ: manual intervention required for post {post_id}, tenant {tenant_id}")
    return "acknowledged_dlq"
