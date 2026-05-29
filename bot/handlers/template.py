import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from models import TenantConfig
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)


def _niche_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📰 Новости", callback_data="niche:news")],
            [InlineKeyboardButton(text="📝 Блог", callback_data="niche:blog")],
            [InlineKeyboardButton(text="🛒 Магазин", callback_data="niche:shop")],
        ]
    )


@router.message(Command("template"))
async def cmd_template(message: Message):
    await message.answer("Выбери шаблон ниши:", reply_markup=_niche_keyboard())


@router.callback_query(F.data.startswith("niche:"))
async def process_niche_change(cb: CallbackQuery):
    niche = cb.data.split(":")[1]
    user_id = str(cb.from_user.id)

    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            await cb.answer("Сначала пройди /setup", show_alert=True)
            return
        config.niche = niche
        await session.commit()
        tenant_id = config.tenant_id

    await cb.answer()
    await cb.message.edit_text(
        f"Шаблон изменён на: {niche}.\n"
        f"Tenant: {tenant_id}\n"
        f"Новые посты будут генерироваться в этом стиле."
    )
    logger.info(f"Tenant {tenant_id}: template changed to {niche}")
