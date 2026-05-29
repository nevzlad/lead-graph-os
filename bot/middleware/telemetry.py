import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from models import TelemetryEvent, TenantConfig
from utils.db import async_session_factory

logger = logging.getLogger(__name__)

SKIP_COMMANDS = {"/start", "/setup"}


class TelemetryMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            cmd = event.text.split()[0].lower()
            if cmd in SKIP_COMMANDS:
                return await handler(event, data)

        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = str(event.from_user.id)
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = str(event.from_user.id)

        if user_id:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
                )
                config = result.scalar_one_or_none()
                if config:
                    data["tenant_id"] = config.tenant_id
                    if config.telemetry_opt_in:
                        event_name = "callback_query" if isinstance(event, CallbackQuery) else "message"
                        text_len = 0
                        if isinstance(event, Message) and event.text:
                            text_len = len(event.text)
                        telemetry = TelemetryEvent(
                            tenant_id=config.tenant_id,
                            event_type="bot_interaction",
                            payload={"event": event_name, "text_len": text_len},
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc),
                        )
                        session.add(telemetry)
                        await session.commit()

        return await handler(event, data)
