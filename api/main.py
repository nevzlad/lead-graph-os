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


@app.on_event("startup")
async def on_startup():
    global bot_task
    logger.info("Starting Telegram bot as background task...")
    bot_task = asyncio.create_task(bot_main())
    logger.info("Bot background task created.")


@app.on_event("shutdown")
async def on_shutdown():
    global bot_task
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        bot_task = None
        logger.info("Bot background task cancelled.")


@app.get("/debug")
async def debug():
    global bot_task
    return {
        "bot_running": bot_task is not None and not bot_task.done(),
        "bot_done": bot_task is not None and bot_task.done(),
        "bot_cancelled": bot_task is not None and bot_task.cancelled(),
        "mode": settings.MODE,
    }


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
