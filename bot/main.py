import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.types.error_event import ErrorEvent

from bot.handlers import billing, find, post, schedule, setup, source, template
from bot.middleware.telemetry import TelemetryMiddleware
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot: Bot | None = None
dp: Dispatcher | None = None


async def main() -> None:
    global bot, dp
    logger.info("Initializing bot with token prefix=%s...", settings.ONBOARDING_BOT_TOKEN[:8] if settings.ONBOARDING_BOT_TOKEN else "NONE")
    bot = Bot(token=settings.ONBOARDING_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(TelemetryMiddleware())
    dp.include_router(setup.router)
    logger.info("setup.router included")
    dp.include_router(template.router)
    logger.info("template.router included")
    dp.include_router(source.router)
    logger.info("source.router included")
    dp.include_router(billing.router)
    logger.info("billing.router included")
    dp.include_router(find.router)
    logger.info("find.router included")
    dp.include_router(post.router)
    logger.info("post.router included")
    dp.include_router(schedule.router)
    logger.info("schedule.router included")

    @dp.startup()
    async def on_startup() -> None:
        from models import Base
        from utils.db import engine

        logger.info("Bot startup: creating tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Bot startup: tables OK.")

        logger.info("Bot startup: setting commands...")
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать настройку"),
            BotCommand(command="setup", description="Настроить канал"),
            BotCommand(command="template", description="Сменить нишу"),
            BotCommand(command="source", description="Управлять RSS"),
            BotCommand(command="schedule", description="Расписание публикаций"),
            BotCommand(command="queue", description="Очередь постов"),
            BotCommand(command="find", description="Найти RSS-источники"),
            BotCommand(command="post", description="Создать пост вручную"),
            BotCommand(command="stats", description="Статистика и аналитика"),
            BotCommand(command="billing", description="Тарифы"),
            BotCommand(command="telemetry", description="Телеметрия + бонус"),
            BotCommand(command="help", description="Помощь"),
        ])
        logger.info("Onboarding bot started.")

    @dp.shutdown()
    async def on_shutdown() -> None:
        await bot.session.close()

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        logger.error("Bot error: %s", event.exception, exc_info=event.exception)
        cq = event.update.callback_query
        if cq:
            try:
                await cq.answer("❌ Ошибка обработки", show_alert=True)
            except Exception:
                pass

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
