import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

from config import settings

logger = logging.getLogger(__name__)
bot = Bot(token=settings.TG_BOT_TOKEN)

TG_API_TIMEOUT = 15


async def _send_message_async(
    chat_id: str, text: str, parse_mode: str = "HTML"
) -> Optional[str]:
    try:
        msg = await asyncio.wait_for(
            bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode),
            timeout=TG_API_TIMEOUT,
        )
        return str(msg.message_id)
    except TelegramRetryAfter as e:
        logger.warning(f"TG rate limit: retry after {e.retry_after}s")
        await asyncio.sleep(e.retry_after + 5)
        return await _send_message_async(chat_id, text, parse_mode)
    except (TelegramForbiddenError, TelegramAPIError) as e:
        logger.error(f"TG API fatal error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected TG error: {e}")
        raise


def send_message(chat_id: str, text: str) -> Optional[str]:
    return asyncio.run(_send_message_async(chat_id, text))
