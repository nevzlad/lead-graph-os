import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple
from database_sync import SessionLocal
from models import RateLimit

logger = logging.getLogger(__name__)

class TenantRateLimiter:
    """
    Per-tenant rate limiter with Redis backing (optional fallback to DB).
    Enforces max tasks per hour, max concurrent tasks, etc.
    """
    
    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.db = SessionLocal()
    
    def check_rate_limit(self, tenant_id: str, task_type: str = "rewrite") -> Tuple[bool, Dict[str, any]]:
        """
        Check if tenant is within rate limits for a task type.
        
        Args:
            tenant_id: Tenant identifier
            task_type: Type of task ('rewrite', 'fetch', 'publish')
        
        Returns:
            (allowed: bool, info: dict with current_count, limit, reset_time)
        """
        try:
            # Try Redis first (if available)
            if self.redis:
                return self._check_redis(tenant_id, task_type)
            
            # Fallback to database
            return self._check_database(tenant_id, task_type)
        
        except Exception as e:
            logger.error(f"Rate limit check failed for tenant {tenant_id}: {e}")
            # Fail open: allow task if limiter fails
            return True, {"current_count": 0, "limit": 9999, "reset_time": None}
    
    def _check_redis(self, tenant_id: str, task_type: str) -> Tuple[bool, Dict]:
        """
        Check rate limit using Redis.
        """
        key = f"ratelimit:{tenant_id}:{task_type}"
        limit_key = f"{key}:limit"
        reset_key = f"{key}:reset"
        
        # Get current count and limits
        current = int(self.redis.get(key) or 0)
        limit = int(self.redis.get(limit_key) or 100)  # Default 100 per hour
        reset_time = self.redis.get(reset_key)
        
        if current >= limit:
            return False, {
                "current_count": current,
                "limit": limit,
                "reset_time": reset_time,
                "reason": f"Tenant {tenant_id} exceeded {task_type} rate limit"
            }
        
        # Increment counter
        self.redis.incr(key)
        self.redis.expire(key, 3600)  # 1 hour TTL
        
        return True, {
            "current_count": current + 1,
            "limit": limit,
            "reset_time": (datetime.now(timezone.utc) + timedelta(seconds=3600)).isoformat()
        }
    
    def _check_database(self, tenant_id: str, task_type: str) -> Tuple[bool, Dict]:
        """
        Check rate limit using database (fallback).
        """
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        
        # Query recent rate limit records
        record = self.db.query(RateLimit).filter(
            RateLimit.tenant_id == tenant_id,
            RateLimit.task_type == task_type,
            RateLimit.timestamp >= hour_ago
        ).first()
        
        if not record:
            record = RateLimit(
                tenant_id=tenant_id,
                task_type=task_type,
                count=1,
                limit=100,
                timestamp=now
            )
            self.db.add(record)
            self.db.commit()
            return True, {
                "current_count": 1,
                "limit": 100,
                "reset_time": (now + timedelta(hours=1)).isoformat()
            }
        
        # Check if limit exceeded
        if record.count >= record.limit:
            return False, {
                "current_count": record.count,
                "limit": record.limit,
                "reset_time": (record.timestamp + timedelta(hours=1)).isoformat(),
                "reason": f"Tenant {tenant_id} exceeded {task_type} rate limit"
            }
        
        # Increment count
        record.count += 1
        self.db.commit()
        
        return True, {
            "current_count": record.count,
            "limit": record.limit,
            "reset_time": (record.timestamp + timedelta(hours=1)).isoformat()
        }
    
    def set_custom_limit(self, tenant_id: str, task_type: str, limit: int) -> None:
        """
        Set custom rate limit for a tenant/task combination.
        """
        if self.redis:
            key = f"ratelimit:{tenant_id}:{task_type}:limit"
            self.redis.set(key, limit)
            logger.info(f"Set custom limit for {tenant_id}/{task_type}: {limit}")
        else:
            # Update in database
            record = self.db.query(RateLimit).filter(
                RateLimit.tenant_id == tenant_id,
                RateLimit.task_type == task_type
            ).first()
            if record:
                record.limit = limit
                self.db.commit()
    
    def close(self):
        self.db.close()
