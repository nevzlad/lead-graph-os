import asyncio
import logging

from fastapi import FastAPI

from api.routes import public
from bot.main import main as bot_main
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lead-Graph OS API", version="1.0.0")

bot_task: asyncio.Task | None = None
pipeline_task: asyncio.Task | None = None
health_task: asyncio.Task | None = None
error_task: asyncio.Task | None = None
repair_task: asyncio.Task | None = None


@app.on_event("startup")
async def on_startup():
    global bot_task, pipeline_task, health_task, error_task, repair_task
    from models import Base
    from utils.db import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text
        for stmt in [
            "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS auto_publish BOOLEAN DEFAULT TRUE NOT NULL",
            "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS interval_minutes INTEGER DEFAULT 1440 NOT NULL",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS image TEXT DEFAULT NULL",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS paused BOOLEAN DEFAULT FALSE NOT NULL",
            "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'ru' NOT NULL",
        ]:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass
    logger.info("Database tables created (if not existing).")

    logger.info("Starting Telegram bot as background task...")

    async def _run_bot():
        try:
            await bot_main()
        except asyncio.CancelledError:
            logger.info("Bot task cancelled.")
        except Exception as e:
            logger.error("Bot task crashed: %s", e, exc_info=True)

    bot_task = asyncio.create_task(_run_bot(), name="telegram-bot")
    bot_task.add_done_callback(
        lambda t: logger.error("Bot task done unexpectedly, exception=%s", t.exception())
        if t.done() and not t.cancelled() and t.exception()
        else None
    )
    logger.info("Bot background task created (id=%s).", bot_task.get_name())

    logger.info("Starting pipeline loop...")
    from services.pipeline import pipeline_loop
    pipeline_task = asyncio.create_task(pipeline_loop())
    logger.info("Pipeline loop created.")

    logger.info("Starting health check loop...")
    from services.health import health_loop
    health_task = asyncio.create_task(health_loop())
    logger.info("Health check loop created.")

    logger.info("Starting error analyzer loop...")
    from services.errors import analyze_and_fix
    async def _error_loop():
        while True:
            try:
                await analyze_and_fix()
            except Exception as e:
                logger.error("Error analyzer failed: %s", e, exc_info=True)
            await asyncio.sleep(600)
    error_task = asyncio.create_task(_error_loop())
    logger.info("Error analyzer loop created.")

    logger.info("Starting post repair loop...")
    from services.repair import pipeline_repair_loop
    repair_task = asyncio.create_task(pipeline_repair_loop())
    logger.info("Post repair loop created.")


@app.on_event("shutdown")
async def on_shutdown():
    global bot_task, pipeline_task
    for t in (pipeline_task, bot_task, health_task, error_task, repair_task):
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    for v in ("bot_task", "pipeline_task", "health_task", "error_task", "repair_task"):
        globals()[v] = None
    logger.info("Background tasks cancelled.")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/debug")
async def debug():
    global bot_task, pipeline_task, health_task, error_task
    from services.health import is_llm_degraded, is_tg_degraded
    from services.llm_router import get_all_providers, get_provider_health

    def _exc(t):
        if t is not None and t.done() and not t.cancelled():
            try:
                return str(t.exception())[:500]
            except:
                pass
        return None

    import bot.main as bot_mod
    bot_info = None
    if bot_mod.bot:
        try:
            me = await asyncio.wait_for(bot_mod.bot.get_me(), timeout=5)
            bot_info = {"id": me.id, "username": me.username, "first_name": me.first_name}
        except Exception as e:
            bot_info = {"error": str(e)[:200]}

    return {
        "bot": bot_info,
        "bot_running": bot_task is not None and not bot_task.done(),
        "bot_exception": _exc(bot_task),
        "pipeline_running": pipeline_task is not None and not pipeline_task.done(),
        "pipeline_exception": _exc(pipeline_task),
        "health_running": health_task is not None and not health_task.done(),
        "health_exception": _exc(health_task),
        "error_analyzer_running": error_task is not None and not error_task.done(),
        "repair_running": repair_task is not None and not repair_task.done(),
        "llm_degraded": is_llm_degraded(),
        "tg_degraded": is_tg_degraded(),
        "mode": settings.MODE,
        "providers": {
            "all": get_all_providers(),
            "health": get_provider_health(),
            "count": len(settings.PROVIDERS),
        },
    }


@app.get("/debug/cleanup")
async def debug_cleanup():
    from sqlalchemy import select

    from models import TenantConfig
    from utils.db import async_session_factory

    try:
        deleted = 0
        async with async_session_factory() as session:
            rows = await session.execute(
                select(TenantConfig).order_by(TenantConfig.created_at)
            )
            seen = {}
            for row in rows.scalars().all():
                uid = row.tg_user_id
                if uid in seen:
                    await session.delete(row)
                    deleted += 1
                else:
                    seen[uid] = True
            await session.commit()
        return {"deleted": deleted}
    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}


@app.get("/debug/process-queue")
async def debug_process_queue():
    from services.pipeline import run_tenant_pipeline
    from sqlalchemy import select
    from models import TenantConfig
    from utils.db import async_session_factory

    try:
        async with async_session_factory() as session:
            tenants = await session.execute(
                select(TenantConfig.tenant_id, TenantConfig.tg_chat_id)
            )
            all_tenants = tenants.all()

        if not all_tenants:
            return {"error": "no_tenants"}

        async def _run_all():
            for tenant_id, chat_id in all_tenants:
                try:
                    counts = await run_tenant_pipeline(tenant_id, chat_id)
                    logger.info("process-queue tenant %s done: %s", tenant_id, counts)
                except Exception as inner:
                    logger.error("pipeline error for %s: %s", tenant_id, inner, exc_info=True)

        task = asyncio.create_task(_run_all())
        return {"status": "started", "tenants": [t[0] for t in all_tenants], "task": str(id(task))}
    except Exception as e:
        logger.error("process-queue error: %s", e, exc_info=True)
        return {"error": str(e)[:500], "type": type(e).__name__}


@app.get("/debug/tenants")
async def debug_tenants():
    from sqlalchemy import func, select

    from models import TenantConfig
    from utils.db import async_session_factory

    try:
        async with async_session_factory() as session:
            cnt = await session.scalar(select(func.count(TenantConfig.tenant_id)))
            rows = await session.execute(
                select(
                    TenantConfig.tenant_id,
                    TenantConfig.tg_user_id,
                    TenantConfig.niche,
                    TenantConfig.created_at,
                ).limit(20)
            )
            tenants = [
                {
                    "tenant_id": row[0],
                    "tg_user_id": row[1],
                    "niche": row[2],
                    "created_at": str(row[3]) if row[3] else None,
                }
                for row in rows
            ]
        return {"count": cnt or 0, "tenants": tenants}
    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}


app.include_router(public.router)

if settings.MODE == "internal":
    try:
        from api.routes import internal

        app.include_router(internal.router)
        logger.info("Internal mode: private routes and Big Data module loaded.")
    except ImportError as e:
        logger.error(f"Failed to load internal module: {e}")
        raise RuntimeError("Internal module required in MODE=internal") from e
else:
    logger.info("Commercial mode: internal routes and Big Data module DISABLED.")


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
