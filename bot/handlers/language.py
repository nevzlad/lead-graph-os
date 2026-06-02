import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from models import TenantConfig
from services.language import LANG_NAMES
from utils.db import async_session_factory

logger = logging.getLogger(__name__)
router = Router()


class LanguageStates(StatesGroup):
    choosing = State()


async def _get_tenant(user_id: str) -> TenantConfig | None:
    async with async_session_factory() as session:
        return await session.scalar(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )


@router.message(Command("language"))
async def cmd_language(message: Message, state: FSMContext):
    tenant = await _get_tenant(str(message.from_user.id))
    if not tenant:
        await message.answer("Сначала настрой канал: /setup")
        return

    await state.update_data(tenant_id=tenant.tenant_id)
    current = tenant.language or "ru"
    lang_label = LANG_NAMES.get(current, current)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"lang:set:{code}")]
        for code, label in LANG_NAMES.items()
    ] + [[InlineKeyboardButton(text="❌ Отмена", callback_data="lang:cancel")]])

    await message.answer(
        f"🌍 Текущий язык: {lang_label} ({current})\n"
        f"Выбери язык для постов:",
        reply_markup=kb,
    )
    await state.set_state(LanguageStates.choosing)


@router.callback_query(LanguageStates.choosing, F.data.startswith("lang:set:"))
async def lang_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.split(":", 2)[2]
    data = await state.get_data()
    tenant_id = data["tenant_id"]

    async with async_session_factory() as session:
        tc = await session.scalar(
            select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
        )
        if tc:
            tc.language = code
            await session.commit()

    await state.clear()
    label = LANG_NAMES.get(code, code)
    await callback.message.edit_text(f"✅ Язык постов изменён на {label} ({code})")


@router.callback_query(F.data == "lang:cancel")
async def lang_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")
