import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import billing, setup, template
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
    dp.include_router(billing.router)

    @dp.startup()
    async def on_startup() -> None:
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
