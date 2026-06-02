import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.handlers import billing, setup, source, template
from bot.middleware.telemetry import TelemetryMiddleware
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot: Bot | None = None
dp: Dispatcher | None = None


async def main() -> None:
    global bot, dp
    bot = Bot(token=settings.ONBOARDING_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(TelemetryMiddleware())
    dp.include_router(setup.router)
    dp.include_router(template.router)
    dp.include_router(source.router)
    dp.include_router(billing.router)

    @dp.startup()
    async def on_startup() -> None:
        from models import Base
        from utils.db import engine

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        await bot.set_my_commands([
            BotCommand(command="start", description="Начать настройку"),
            BotCommand(command="setup", description="Настроить канал"),
            BotCommand(command="template", description="Сменить нишу"),
            BotCommand(command="source", description="Управлять RSS"),
            BotCommand(command="billing", description="Тарифы"),
            BotCommand(command="telemetry", description="Телеметрия + бонус"),
            BotCommand(command="help", description="Помощь"),
        ])
        logger.info("Onboarding bot started.")

    @dp.shutdown()
    async def on_shutdown() -> None:
        await bot.session.close()

    @dp.errors()
    async def on_error(event: Exception, data: dict) -> None:
        logger.error("Bot error: %s", event, exc_info=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
