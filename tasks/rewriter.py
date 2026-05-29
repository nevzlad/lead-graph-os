import logging
from datetime import datetime, timezone
from celery_app import celery_app
from database_sync import SessionLocal
from models import Post
from rewriters.niche import NicheRewriter
from rate_limiter import TenantRateLimiter

logger = logging.getLogger(__name__)

@celery_app.task(
    bind=True,
    name="tasks.rewriter.rewrite_post",
    max_retries=3,
    default_retry_delay=60,
    queue="rewriter"
)
def rewrite_post_task(self, post_id: int, tenant_id: str, niche: str = "tech"):
    """
    Rewrite a post content based on niche preset.
    Enforces per-tenant rate limits.
    
    Args:
        post_id: ID of post to rewrite
        tenant_id: Tenant identifier (for isolation + rate limiting)
        niche: Target niche preset (tech, finance, health, lifestyle, business)
    
    Returns:
        Status string or dict with result info
    """
    db = SessionLocal()
    limiter = TenantRateLimiter()
    
    try:
        # Check rate limit
        allowed, limit_info = limiter.check_rate_limit(tenant_id, "rewrite")
        if not allowed:
            logger.warning(f"Rate limit exceeded for tenant {tenant_id}: {limit_info}")
            return {
                "status": "rate_limited",
                "limit_info": limit_info
            }
        
        # Fetch post
        post = db.query(Post).filter(
            Post.id == post_id,
            Post.tenant_id == tenant_id,
            Post.status == "raw"
        ).first()
        
        if not post:
            logger.info(f"Post {post_id} not found or already rewritten for tenant {tenant_id}")
            return {"status": "skipped", "reason": "post_not_found_or_already_rewritten"}
        
        # Rewrite content
        rewriter = NicheRewriter()
        rewritten_content = rewriter.rewrite(post.content, {"niche": niche})
        
        # Update post
        post.content = rewritten_content
        post.status = "rewritten"
        post.updated_at = datetime.now(timezone.utc)
        db.commit()
        
        logger.info(f"Tenant {tenant_id}: rewritten post {post_id} with niche '{niche}'")
        
        return {
            "status": "success",
            "post_id": post_id,
            "niche": niche,
            "rate_limit_remaining": limit_info["limit"] - limit_info["current_count"]
        }
    
    except Exception as exc:
        db.rollback()
        logger.error(f"Task rewrite_post_task failed: {exc}")
        self.retry(exc=exc)
    
    finally:
        db.close()
        limiter.close()

@celery_app.task(
    bind=True,
    name="tasks.rewriter.batch_rewrite",
    max_retries=2,
    default_retry_delay=120,
    queue="rewriter"
)
def batch_rewrite_task(self, tenant_id: str, niche: str = "tech", batch_size: int = 10):
    """
    Batch rewrite raw posts for a tenant.
    Respects rate limits by limiting batch size dynamically.
    
    Args:
        tenant_id: Tenant identifier
        niche: Target niche preset
        batch_size: Max number of posts to rewrite in one batch
    
    Returns:
        Summary dict with rewritten count and rate limit info
    """
    db = SessionLocal()
    limiter = TenantRateLimiter()
    
    try:
        # Fetch raw posts for tenant
        raw_posts = db.query(Post).filter(
            Post.tenant_id == tenant_id,
            Post.status == "raw"
        ).limit(batch_size).all()
        
        if not raw_posts:
            logger.info(f"No raw posts found for tenant {tenant_id}")
            return {"status": "skipped", "rewritten_count": 0}
        
        rewriter = NicheRewriter()
        rewritten_count = 0
        
        for post in raw_posts:
            # Check rate limit before each post
            allowed, limit_info = limiter.check_rate_limit(tenant_id, "rewrite")
            if not allowed:
                logger.warning(f"Rate limit hit during batch for tenant {tenant_id}. Stopping batch.")
                break
            
            try:
                rewritten_content = rewriter.rewrite(post.content, {"niche": niche})
                post.content = rewritten_content
                post.status = "rewritten"
                post.updated_at = datetime.now(timezone.utc)
                db.add(post)
                rewritten_count += 1
            except Exception as e:
                logger.error(f"Failed to rewrite post {post.id}: {e}")
                continue
        
        db.commit()
        logger.info(f"Tenant {tenant_id}: batch rewritten {rewritten_count} posts with niche '{niche}'")
        
        return {
            "status": "success",
            "rewritten_count": rewritten_count,
            "niche": niche,
            "rate_limit_remaining": limit_info["limit"] - limit_info["current_count"]
        }
    
    except Exception as exc:
        db.rollback()
        logger.error(f"Task batch_rewrite_task failed: {exc}")
        self.retry(exc=exc)
    
    finally:
        db.close()
        limiter.close()
