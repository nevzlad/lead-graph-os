import logging
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy import func, select

from config import settings
from models import Source, TenantConfig
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)

NICHE_BUTTONS = ["📰 Новости", "📝 Блог", "🛒 Магазин"]
NICHE_MAP = {"📰 Новости": "news", "📝 Блог": "blog", "🛒 Магазин": "shop"}


class SetupStates(StatesGroup):
    waiting_chat_id = State()
    waiting_niche = State()


async def _show_tenants_list(user_id: str, message: Message):
    async with async_session_factory() as session:
        rows = await session.execute(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )
        tenants = rows.scalars().all()

    if not tenants:
        await message.answer(
            "У тебя пока нет настроенных каналов.\n"
            "Отправь ID Telegram-канала или группы, "
            "куда будем постить (начинается с -100 или @username канала)."
        )
        return

    kb = []
    for t in tenants:
        niche_icon = {"news": "📰", "blog": "📝", "shop": "🛒"}.get(t.niche, "📄")
        label = f"{niche_icon} {t.tg_chat_id} ({t.niche})"
        kb.append([InlineKeyboardButton(text=label, callback_data=f"tenant:select:{t.tenant_id}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="tenant:add")])

    await message.answer(
        "📋 Твои каналы:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await _show_tenants_list(str(message.from_user.id), message)


@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext):
    await state.clear()
    await _show_tenants_list(str(message.from_user.id), message)


@router.callback_query(F.data.startswith("tenant:"))
async def on_tenant_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":", 2)

    if action[1] == "add":
        await state.update_data(adding_new=True)
        await callback.message.answer(
            "Отправь ID Telegram-канала или группы, "
            "куда будем постить (начинается с -100 или @username канала)."
        )
        await state.set_state(SetupStates.waiting_chat_id)
        return

    if action[1] == "select":
        tenant_id = action[2]
        async with async_session_factory() as session:
            result = await session.execute(
                select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
            )
            tenant = result.scalar_one_or_none()
            if not tenant:
                await callback.message.answer("Канал не найден.")
                return
            src_count = await session.scalar(
                select(func.count(Source.id)).where(
                    Source.tenant_id == tenant_id, Source.is_active == 1
                )
            )

        trial_end = tenant.created_at + timedelta(days=settings.TRIAL_DAYS)
        text = (
            f"📌 Канал: {tenant.tg_chat_id}\n"
            f"🏷 Ниша: {tenant.niche}\n"
            f"📊 Статус: {tenant.billing_status}\n"
            f"📡 Активных RSS: {src_count or 0}\n"
            f"📅 Trial до: {trial_end.strftime('%d.%m.%Y')}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📡 Управлять RSS", callback_data="cmd:source")],
            [InlineKeyboardButton(text="🏷 Сменить нишу", callback_data="cmd:template")],
            [InlineKeyboardButton(text="💳 Тарифы", callback_data="cmd:billing")],
        ])
        await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("cmd:"))
async def on_cmd_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    cmd = callback.data.split(":", 1)[1]
    await callback.message.answer(f"Используй команду /{cmd} в чате.")


@router.message(SetupStates.waiting_chat_id)
async def process_chat_id(message: Message, state: FSMContext):
    from aiogram import Bot
    from aiogram.exceptions import TelegramBadRequest

    raw = str(message.text).strip()
    if not (raw.startswith("-100") or raw.startswith("@")):
        await message.answer(
            "❌ Неверный формат. ID канала должен начинаться с -100 или @username.\n"
            "Попробуй ещё раз:"
        )
        return

    user_id = str(message.from_user.id)
    async with async_session_factory() as session:
        dup = await session.scalar(
            select(TenantConfig.tg_chat_id).where(
                TenantConfig.tg_user_id == user_id,
                TenantConfig.tg_chat_id == raw,
            )
        )
        if dup:
            await message.answer(
                "❌ Этот канал уже добавлен. Используй /start чтобы увидеть список каналов."
            )
            await state.clear()
            return

    try:
        chat = await message.bot.get_chat(raw)
        chat_title = chat.title or chat.username or raw
    except TelegramBadRequest:
        chat_title = None

    pub_bot_username = None
    pub_bot_id = None
    try:
        async with Bot(token=settings.TG_BOT_TOKEN) as pub_bot:
            me = await pub_bot.get_me()
            pub_bot_id = me.id
            pub_bot_username = me.username
    except Exception:
        pass

    if chat_title and pub_bot_id:
        try:
            member = await message.bot.get_chat_member(raw, pub_bot_id)
            is_admin = member.status in ("administrator", "creator")
            can_post = getattr(member, "can_post_messages", False) if member.status == "administrator" else True
            can_edit = getattr(member, "can_edit_messages", False) if member.status == "administrator" else True
        except TelegramBadRequest:
            is_admin = False
            can_post = False
            can_edit = False
    else:
        is_admin = False
        can_post = False
        can_edit = False

    if not chat_title:
        await message.answer(
            f"❌ Не удалось найти канал `{raw}`.\n\n"
            f"1. Добавь бота @{pub_bot_username or 'бота'} в канал как администратор\n"
            f"2. Выдай права: «Отправлять сообщения», «Редактировать сообщения»\n"
            f"3. Отправь ID канала ещё раз",
            parse_mode="Markdown",
        )
        return

    if not is_admin or not can_post:
        perms = []
        if not can_post:
            perms.append("📝 Отправлять сообщения")
        if not can_edit:
            perms.append("✏️ Редактировать сообщения")
        await message.answer(
            f"❌ Бот @{pub_bot_username or 'бота'} не имеет прав в канале «{chat_title}».\n\n"
            f"1. Открой настройки канала → Администраторы\n"
            f"2. Добавь @{pub_bot_username or 'бота'} как администратора\n"
            "3. Включи права:\n" + "\n".join(f"   • {p}" for p in (perms or ["все права"])) + "\n\n"
            "4. Отправь ID канала ещё раз"
        )
        return

    await message.answer(
        f"✅ Канал «{chat_title}» найден, бот имеет все права."
    )

    await state.update_data(chat_id=raw)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📰 Новости"), KeyboardButton(text="📝 Блог")],
            [KeyboardButton(text="🛒 Магазин")],
        ],
        resize_keyboard=True,
    )
    await message.answer("Выбери нишу для шаблона промптов:", reply_markup=kb)
    await state.set_state(SetupStates.waiting_niche)


@router.message(SetupStates.waiting_niche, F.text.in_(NICHE_BUTTONS))
async def process_niche(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    data = await state.get_data()
    niche = NICHE_MAP.get(message.text, "news")
    now = datetime.now(timezone.utc)
    adding_new = data.get("adding_new", False)

    async with async_session_factory() as session:
        if not adding_new:
            existing = await session.scalar(
                select(TenantConfig.tenant_id).where(TenantConfig.tg_user_id == user_id)
            )
            if existing:
                result = await session.execute(
                    select(TenantConfig).where(TenantConfig.tenant_id == existing)
                )
                config = result.scalar_one()
                config.niche = niche
                config.updated_at = now
                await session.commit()
                tenant_id = config.tenant_id
                trial_end = config.created_at + timedelta(days=settings.TRIAL_DAYS)
                await state.clear()
                await message.answer(
                    f"✅ Ниша обновлена на «{niche}»!\n"
                    f"Твой Tenant ID: `{tenant_id}`\n"
                    f"Trial активен до {trial_end.strftime('%d.%m.%Y %H:%M')}",
                    parse_mode="Markdown",
                )
                logger.info(f"Tenant {tenant_id}: niche updated to {niche}")
                return

        tenant_id = str(uuid.uuid4())
        trial_end = now + timedelta(days=settings.TRIAL_DAYS)

        new_cfg = TenantConfig(
            tenant_id=tenant_id,
            tg_user_id=user_id,
            tg_chat_id=data["chat_id"],
            niche=niche,
            billing_status="trial",
            telemetry_opt_in=False,
            api_limit_bonus=0,
            created_at=now,
            updated_at=now,
        )
        session.add(new_cfg)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Настройка завершена!\n"
        f"Твой Tenant ID: `{tenant_id}`\n"
        f"Ниша: {niche}\n"
        f"Trial активен до {trial_end.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Команды:\n"
        f"/source — добавить RSS-источник\n"
        f"/template — смена ниши\n"
        f"/billing — тарифы\n"
        f"/telemetry — opt-in телеметрия (+100 к лимиту API, один раз)",
        parse_mode="Markdown",
    )
    logger.info(f"Tenant {tenant_id} onboarded successfully.")


@router.message(SetupStates.waiting_niche)
async def process_niche_invalid(message: Message):
    await message.answer("Пожалуйста, выбери нишу из предложенных кнопок ниже.")


@router.message(Command("telemetry"))
async def cmd_telemetry(message: Message):
    user_id = str(message.from_user.id)
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )
        config = result.scalars().first()
        if not config:
            await message.answer("Сначала пройди онбординг: /setup")
            return

        if config.telemetry_opt_in:
            await message.answer(
                f"Телеметрия уже включена. Бонус к лимиту API: +{config.api_limit_bonus}"
            )
            return

        config.telemetry_opt_in = True
        if config.api_limit_bonus == 0:
            config.api_limit_bonus = 100
        config.updated_at = now
        tenant_id = config.tenant_id
        bonus = config.api_limit_bonus
        await session.commit()

    await message.answer(
        f"✅ Спасибо! Включена обезличенная телеметрия.\n"
        f"Начислен бонус к лимиту API: +{bonus} запросов/час (один раз)."
    )
    logger.info(f"Tenant {tenant_id}: telemetry opt-in, bonus={bonus}")
