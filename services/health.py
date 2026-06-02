import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from config import settings
from models import ServiceError, Source
from utils.db import async_session_factory

logger = logging.getLogger(__name__)

_llm_degraded = False
_tg_degraded = False
_db_healthy = True


async def check_db() -> bool:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        global _db_healthy
        if not _db_healthy:
            _db_healthy = True
            logger.info("DB recovered")
            await _resolve_errors("database")
        return True
    except Exception as e:
        _db_healthy = False
        logger.error("DB health check failed: %s", e)
        await _log_error("database", "connection", str(e))
        return False


async def check_llm() -> bool:
    try:
        import requests
        if not settings.HF_API_KEY:
            return True
        headers = {"Authorization": f"Bearer {settings.HF_API_KEY}"}
        url = f"https://api-inference.huggingface.co/models/{settings.LLM_MODEL}"
        payload = {"inputs": "ping", "parameters": {"max_new_tokens": 5}}
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: requests.post(url, headers=headers, json=payload, timeout=30)
        )
        ok = resp.status_code == 200
        global _llm_degraded
        if ok and _llm_degraded:
            _llm_degraded = False
            logger.info("LLM recovered")
            await _resolve_errors("llm")
        elif not ok and not _llm_degraded:
            _llm_degraded = True
            logger.warning("LLM degraded (HTTP %s)", resp.status_code)
            await _log_error("llm", "http", f"HTTP {resp.status_code}")
        return ok
    except Exception as e:
        _llm_degraded = True
        logger.error("LLM health check failed: %s", e)
        await _log_error("llm", "request", str(e)[:500])
        return False


async def check_tg_bot() -> bool:
    try:
        from aiogram import Bot
        async with Bot(token=settings.TG_BOT_TOKEN) as bot:
            me = await bot.get_me()
        ok = bool(me and me.username)
        global _tg_degraded
        if ok and _tg_degraded:
            _tg_degraded = False
            logger.info("TG bot recovered")
            await _resolve_errors("telegram")
        elif not ok and not _tg_degraded:
            _tg_degraded = True
            logger.warning("TG bot degraded")
            await _log_error("telegram", "auth", "Bot getMe returned no username")
        return ok
    except Exception as e:
        _tg_degraded = True
        logger.error("TG bot health check failed: %s", e)
        await _log_error("telegram", "connection", str(e)[:500])
        return False


async def check_dead_sources() -> int:
    recovered = 0
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Source).where(Source.is_active == 0)
        )
        for src in rows.scalars().all():
            errors = await session.execute(
                select(ServiceError).where(
                    ServiceError.service_name == "rss",
                    ServiceError.tenant_id == src.tenant_id,
                    ServiceError.resolved == False,
                ).order_by(ServiceError.created_at.desc()).limit(3)
            )
            err_list = errors.scalars().all()
            if not err_list:
                # No recent unresolved errors — safe to reactivate
                src.is_active = 1
                src.last_fetched = datetime.now(timezone.utc)
                recovered += 1
        await session.commit()
    if recovered:
        logger.info("Auto-recovered %d dead sources", recovered)
    return recovered


async def _log_error(service: str, etype: str, msg: str):
    async with async_session_factory() as session:
        err = ServiceError(
            service_name=service,
            error_type=etype,
            error_msg=msg[:1000],
            created_at=datetime.now(timezone.utc),
        )
        session.add(err)
        await session.commit()


async def _resolve_errors(service: str):
    async with async_session_factory() as session:
        rows = await session.execute(
            select(ServiceError).where(
                ServiceError.service_name == service,
                ServiceError.resolved == False,
            )
        )
        for err in rows.scalars().all():
            err.resolved = True
            err.resolved_at = datetime.now(timezone.utc)
        await session.commit()


def is_llm_degraded() -> bool:
    return _llm_degraded


def is_tg_degraded() -> bool:
    return _tg_degraded


async def health_loop():
    logger.info("Health check loop started.")
    while True:
        try:
            await check_db()
            await check_llm()
            await check_tg_bot()
            await check_dead_sources()
        except Exception as e:
            logger.error("Health loop error: %s", e, exc_info=True)
        await asyncio.sleep(120)
