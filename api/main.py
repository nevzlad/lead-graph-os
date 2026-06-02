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


@app.on_event("startup")
async def on_startup():
    global bot_task, pipeline_task
    from models import Base
    from utils.db import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text
        for stmt in [
            "ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS auto_publish BOOLEAN DEFAULT TRUE NOT NULL",
            "ALTER TABLE schedules ADD COLUMN IF NOT EXISTS interval_minutes INTEGER DEFAULT 1440 NOT NULL",
            "ALTER TABLE posts ADD COLUMN IF NOT EXISTS image TEXT DEFAULT NULL",
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


@app.on_event("shutdown")
async def on_shutdown():
    global bot_task, pipeline_task
    for t in (pipeline_task, bot_task):
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    bot_task = None
    pipeline_task = None
    logger.info("Background tasks cancelled.")


@app.get("/debug")
async def debug():
    global bot_task, pipeline_task
    return {
        "bot_running": bot_task is not None and not bot_task.done(),
        "bot_done": bot_task is not None and bot_task.done(),
        "bot_cancelled": bot_task is not None and bot_task.cancelled(),
        "pipeline_running": pipeline_task is not None and not pipeline_task.done(),
        "mode": settings.MODE,
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
