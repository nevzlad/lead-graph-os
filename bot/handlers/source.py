import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from models import Source, TenantConfig
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)


class AddSourceStates(StatesGroup):
    waiting_url = State()
    waiting_name = State()


def _sources_keyboard(sources: list[Source]) -> InlineKeyboardMarkup:
    kb = []
    for s in sources:
        kb.append([
            InlineKeyboardButton(
                text=f"{'✅' if s.is_active else '❌'} {s.name[:20]}",
                callback_data=f"src:toggle:{s.id}",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"src:del:{s.id}"),
        ])
    kb.append([InlineKeyboardButton(text="➕ Добавить источник", callback_data="src:add")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def _get_tenant(user_id: str) -> TenantConfig | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )
        return result.scalar_one_or_none()


async def _get_sources(tenant_id: str) -> list[Source]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.tenant_id == tenant_id,
            ).order_by(Source.id)
        )
        return result.scalars().all()


@router.message(Command("source"))
async def cmd_source(message: Message):
    try:
        tenant = await _get_tenant(str(message.from_user.id))
        if not tenant:
            await message.answer("Сначала пройди онбординг: /setup")
            return

        sources = await _get_sources(tenant.tenant_id)
        if not sources:
            txt = "Нет добавленных источников.\nНажми «Добавить источник», чтобы добавить RSS-ленту."
        else:
            txt = f"У тебя {len(sources)} источник(ов):"
        await message.answer(txt, reply_markup=_sources_keyboard(sources))
    except Exception as e:
        logger.error("cmd_source error: %s", e, exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")


@router.callback_query(F.data == "src:add")
async def add_source_start(cb: CallbackQuery, state: FSMContext):
    tenant = await _get_tenant(str(cb.from_user.id))
    if not tenant:
        await cb.answer("Сначала пройди /setup", show_alert=True)
        return

    await state.update_data(tenant_id=tenant.tenant_id)
    await cb.answer()
    await cb.message.edit_text(
        "Отправь URL RSS-ленты (например, https://example.com/rss):"
    )
    await state.set_state(AddSourceStates.waiting_url)


@router.message(AddSourceStates.waiting_url)
async def add_source_url(message: Message, state: FSMContext):
    url = message.text.strip()
    await state.update_data(url=url)

    await message.answer(
        "Отправь название источника (например, «Habr» или любой текст):"
    )
    await state.set_state(AddSourceStates.waiting_name)


@router.message(AddSourceStates.waiting_name)
async def add_source_name(message: Message, state: FSMContext):
    data = await state.get_data()
    name = message.text.strip() or "RSS Source"
    url = data["url"]

    async with async_session_factory() as session:
        source = Source(
            tenant_id=data["tenant_id"],
            name=name,
            url=url,
            source_type="rss",
            is_active=1,
            config={},
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    await state.clear()
    await message.answer(
        f"✅ Источник «{name}» добавлен (id={source_id}).\n"
        f"Когда система сбора будет запущена, посты из этого RSS "
        f"будут автоматически собираться, обрабатываться и публиковаться."
    )
    logger.info(f"Source added: {name} ({url}) for tenant {data['tenant_id']}")


@router.callback_query(F.data.startswith("src:toggle:"))
async def toggle_source(cb: CallbackQuery):
    source_id = int(cb.data.split(":")[2])
    tenant = await _get_tenant(str(cb.from_user.id))
    if not tenant:
        await cb.answer("Сначала пройди /setup", show_alert=True)
        return

    async with async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.id == source_id,
                Source.tenant_id == tenant.tenant_id,
            )
        )
        source = result.scalar_one_or_none()
        if not source:
            await cb.answer("Источник не найден", show_alert=True)
            return

        source.is_active = 0 if source.is_active else 1
        await session.commit()
        was = "включён" if source.is_active else "выключен"

    sources = await _get_sources(tenant.tenant_id)
    await cb.answer(f"Источник {was}")
    await cb.message.edit_text(
        f"У тебя {len(sources)} источник(ов):",
        reply_markup=_sources_keyboard(sources),
    )


@router.callback_query(F.data.startswith("src:del:"))
async def delete_source(cb: CallbackQuery):
    source_id = int(cb.data.split(":")[2])
    tenant = await _get_tenant(str(cb.from_user.id))
    if not tenant:
        await cb.answer("Сначала пройди /setup", show_alert=True)
        return

    async with async_session_factory() as session:
        await session.execute(
            delete(Source).where(
                Source.id == source_id,
                Source.tenant_id == tenant.tenant_id,
            )
        )
        await session.commit()

    sources = await _get_sources(tenant.tenant_id)
    await cb.answer("Источник удалён")
    if sources:
        await cb.message.edit_text(
            f"У тебя {len(sources)} источник(ов):",
            reply_markup=_sources_keyboard(sources),
        )
    else:
        await cb.message.edit_text(
            "Нет добавленных источников.\nНажми /source, чтобы добавить RSS-ленту."
        )
