import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from models import Post, TenantConfig
from services.formatter import format_post
from services.telegram import _send_message_async, strip_html
from utils.db import async_session_factory

logger = logging.getLogger(__name__)
router = Router()

TIME_OPTIONS = [
    ("Сейчас", 0),
    ("Через 30 мин", 30),
    ("Через 1 час", 60),
    ("Через 2 часа", 120),
    ("Через 4 часа", 240),
    ("Через 8 часов", 480),
    ("Через 24 часа", 1440),
    ("Своё время", -1),
]


class PostStates(StatesGroup):
    choosing_action = State()
    entering_title = State()
    entering_content = State()
    choosing_image = State()
    setting_time = State()
    setting_custom_time = State()
    confirming = State()


async def _get_tenant(user_id: str) -> TenantConfig | None:
    async with async_session_factory() as session:
        return await session.scalar(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )


@router.message(Command("post"))
async def cmd_post(message: Message, state: FSMContext):
    tenant = await _get_tenant(str(message.from_user.id))
    if not tenant:
        await message.answer("Сначала настрой канал: /setup")
        return
    await state.update_data(tenant_id=tenant.tenant_id)
    await state.set_state(PostStates.entering_title)
    await message.answer("Введи заголовок поста:")


@router.message(PostStates.entering_title)
async def post_enter_title(message: Message, state: FSMContext):
    title = str(message.text).strip()
    if not title:
        await message.answer("Заголовок не может быть пустым.")
        return
    await state.update_data(title=title)
    await state.set_state(PostStates.entering_content)
    await message.answer("Введи текст поста (или отправь /skip чтобы оставить пустым):")


@router.message(PostStates.entering_content)
async def post_enter_content(message: Message, state: FSMContext):
    if str(message.text).strip() == "/skip":
        content = None
    else:
        content = str(message.text).strip()
    await state.update_data(content=content)
    await state.set_state(PostStates.choosing_image)
    await message.answer(
        "Отправь изображение (необязательно) или /skip:"
    )


@router.message(PostStates.choosing_image, F.photo)
async def post_enter_image(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(image=file_id)
    await _ask_time(message, state)


@router.message(PostStates.choosing_image)
async def post_skip_image(message: Message, state: FSMContext):
    if str(message.text).strip() == "/skip":
        await state.update_data(image=None)
    else:
        await message.answer("Отправь фото или /skip:")
        return
    await _ask_time(message, state)


async def _ask_time(message: Message, state: FSMContext):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"post_time:{val}")]
        for label, val in TIME_OPTIONS
    ])
    await state.set_state(PostStates.setting_time)
    await message.answer("Когда опубликовать?", reply_markup=kb)


@router.callback_query(PostStates.setting_time, F.data.startswith("post_time:"))
async def post_choose_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    val = int(callback.data.split(":", 1)[1])
    if val < 0:
        await callback.message.answer("Введи время в формате ЧЧ:ММ (UTC), например 14:30:")
        await state.set_state(PostStates.setting_custom_time)
        return

    now = datetime.now(timezone.utc)
    scheduled = now + timedelta(minutes=val) if val > 0 else now
    await state.update_data(scheduled_at=scheduled)
    await _show_preview(callback.message, state)


@router.message(PostStates.setting_custom_time)
async def post_custom_time(message: Message, state: FSMContext):
    t = str(message.text).strip()
    if not (len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit()):
        await message.answer("Неверный формат. Используй ЧЧ:ММ, например 14:30")
        return
    h, m = int(t[:2]), int(t[3:])
    if h < 0 or h > 23 or m < 0 or m > 59:
        await message.answer("Время вне диапазона.")
        return
    now = datetime.now(timezone.utc)
    scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    await state.update_data(scheduled_at=scheduled)
    await _show_preview(message, state)


async def _show_preview(message: Message, state: FSMContext):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    data = await state.get_data()
    title = data["title"]
    content = data.get("content")
    image = data.get("image")
    scheduled = data["scheduled_at"]
    tenant_id = data["tenant_id"]

    formatted = format_post(title, content)
    when = "сейчас" if scheduled <= datetime.now(timezone.utc) else scheduled.strftime("%d.%m %H:%M UTC")

    if image:
        await message.answer_photo(
            photo=image,
            caption=f"📄 {formatted}\n\n⏰ Публикация: {when}",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"📄 {formatted}\n\n⏰ Публикация: {when}",
            parse_mode="HTML",
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="post:confirm")],
        [InlineKeyboardButton(text="✏️ Изменить заголовок", callback_data="post:edit_title")],
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="post:edit_content")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel")],
    ])
    await state.set_state(PostStates.confirming)
    await message.answer("Подтверди публикацию:", reply_markup=kb)


@router.callback_query(PostStates.confirming, F.data == "post:edit_title")
async def post_edit_title(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PostStates.entering_title)
    await callback.message.answer("Введи новый заголовок:")


@router.callback_query(PostStates.confirming, F.data == "post:edit_content")
async def post_edit_content(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PostStates.entering_content)
    await callback.message.answer("Введи новый текст (или /skip):")


@router.callback_query(PostStates.confirming, F.data == "post:cancel")
async def post_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")


@router.callback_query(PostStates.confirming, F.data == "post:confirm")
async def post_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    scheduled = data["scheduled_at"]
    publish_now = scheduled <= datetime.now(timezone.utc)
    status = "published" if publish_now else "scheduled"

    post = Post(
        tenant_id=data["tenant_id"],
        source_id=0,
        title=data["title"],
        content=data.get("content"),
        image=data.get("image"),
        status=status,
        scheduled_at=scheduled,
    )
    async with async_session_factory() as session:
        session.add(post)
        await session.commit()
        post_id = post.id

    await state.clear()

    if publish_now:
        await callback.message.answer("⏳ Публикую...")
        async with async_session_factory() as session:
            tenant = await session.scalar(
                select(TenantConfig).where(TenantConfig.tenant_id == data["tenant_id"])
            )
            chat_id = tenant.tg_chat_id if tenant else None

        if not chat_id:
            await callback.message.answer("❌ Канал не найден.")
            return

        formatted = format_post(data["title"], data.get("content"))
        try:
            ext_id = await _send_message_async(chat_id, strip_html(formatted)[:4096])
            async with async_session_factory() as session:
                p = await session.get(Post, post_id)
                if p:
                    p.status = "published"
                    p.external_id = ext_id
                    p.scheduled_at = datetime.now(timezone.utc)
                    await session.commit()
            await callback.message.answer("✅ Пост опубликован!")
        except Exception as e:
            logger.error("Manual publish failed: %s", e, exc_info=True)
            async with async_session_factory() as session:
                p = await session.get(Post, post_id)
                if p:
                    p.status = "failed"
                    await session.commit()
            await callback.message.answer(f"❌ Ошибка публикации: {e}")
    else:
        await callback.message.answer(
            f"✅ Пост запланирован на {scheduled.strftime('%d.%m.%Y %H:%M')} UTC"
        )
