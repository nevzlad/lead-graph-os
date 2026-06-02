import logging
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
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


async def _get_pub_bot_info() -> tuple[str | None, int | None]:
    try:
        async with Bot(token=settings.TG_BOT_TOKEN) as pub_bot:
            me = await pub_bot.get_me()
            return me.username, me.id
    except Exception as e:
        logger.error(f"Failed to get pub bot info: {e}")
        return None, None


PERMISSION_GUIDE = """🔐 *Как дать права боту:*

1. Открой настройки канала
2. Перейди в «Администраторы»
3. Нажми «Добавить администратора»
4. Выбери @{bot_username}
5. Включи права:
   • 📝 *Отправлять сообщения*
   • ✏️ *Редактировать сообщения*
   • 📎 *Прикреплять сообщения* (опционально)
6. Нажми «Сохранить»

После этого нажми кнопку «🔄 Проверить права»"""


async def _check_bot_perms(chat_id: str) -> dict:
    pub_bot_username, pub_bot_id = await _get_pub_bot_info()
    if not pub_bot_id:
        return {"ok": False, "error": "not_available"}

    result = {
        "ok": False,
        "exists": False,
        "is_admin": False,
        "can_post": False,
        "can_edit": False,
        "title": None,
        "pub_bot_username": pub_bot_username,
        "pub_bot_id": pub_bot_id,
        "error": None,
    }

    try:
        async with Bot(token=settings.TG_BOT_TOKEN) as bot:
            chat = await bot.get_chat(chat_id)
            result["title"] = chat.title or chat.username or chat_id
            result["exists"] = True

            member = await bot.get_chat_member(chat_id, pub_bot_id)
            result["is_admin"] = member.status in ("administrator", "creator")
            if result["is_admin"]:
                result["can_post"] = (
                    True if member.status == "creator"
                    else getattr(member, "can_post_messages", False)
                )
                result["can_edit"] = (
                    True if member.status == "creator"
                    else getattr(member, "can_edit_messages", False)
                )
            result["ok"] = result["can_post"]
    except TelegramBadRequest:
        result["error"] = "not_found"
    except Exception as e:
        result["error"] = str(e)

    return result


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


@router.callback_query(F.data == "tenant:cancel")
async def tenant_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _show_tenants_list(str(callback.from_user.id), callback.message)


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

        perms = await _check_bot_perms(tenant.tg_chat_id)

        if perms["ok"]:
            perm_icon = "✅"
            perm_detail = "бот администратор, права есть"
        elif perms["exists"] and perms["is_admin"] and not perms["can_edit"]:
            perm_icon = "⚠️"
            perm_detail = "нет права редактировать"
        elif perms["exists"] and not perms["is_admin"]:
            perm_icon = "❌"
            perm_detail = "бот не администратор"
        elif not perms["exists"]:
            perm_icon = "❌"
            perm_detail = "канал недоступен"
        else:
            perm_icon = "❌"
            perm_detail = f"ошибка: {perms['error'] or 'неизвестная'}"

        trial_end = tenant.created_at + timedelta(days=settings.TRIAL_DAYS)
        text = (
            f"📌 Канал: {tenant.tg_chat_id}\n"
            f"🏷 Ниша: {tenant.niche}\n"
            f"🤖 Права бота: {perm_icon} {perm_detail}\n"
            f"📊 Статус: {tenant.billing_status}\n"
            f"📡 Активных RSS: {src_count or 0}\n"
            f"📅 Trial до: {trial_end.strftime('%d.%m.%Y')}"
        )

        kb_rows = []
        if not perms["ok"]:
            guide = PERMISSION_GUIDE.format(bot_username=perms.get("pub_bot_username") or "бота")
            kb_rows.append([InlineKeyboardButton(text="🔐 Инструкция", callback_data=f"perm:guide:{tenant_id}")])
            kb_rows.append([InlineKeyboardButton(text="🔄 Проверить права", callback_data=f"perm:recheck:{tenant_id}")])
        else:
            kb_rows.append([InlineKeyboardButton(text="📡 Управлять RSS", callback_data="cmd:source")])
            kb_rows.append([InlineKeyboardButton(text="🏷 Сменить нишу", callback_data="cmd:template")])
            kb_rows.append([InlineKeyboardButton(text="💳 Тарифы", callback_data="cmd:billing")])

        await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("perm:guide:"))
async def perm_show_guide(callback: CallbackQuery):
    await callback.answer()
    tenant_id = callback.data.split(":", 2)[2]
    pub_bot_username, _ = await _get_pub_bot_info()
    guide = PERMISSION_GUIDE.format(bot_username=pub_bot_username or "бота")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить права", callback_data=f"perm:recheck:{tenant_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"tenant:select:{tenant_id}")],
    ])
    await callback.message.answer(guide, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data.startswith("perm:recheck:"))
async def perm_recheck(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tenant_id = callback.data.split(":", 2)[2]

    async with async_session_factory() as session:
        tenant = await session.scalar(
            select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
        )
        if not tenant:
            await callback.message.answer("Канал не найден.")
            return

    msg = await callback.message.answer("⏳ Проверяю права...")
    perms = await _check_bot_perms(tenant.tg_chat_id)

    if perms["ok"]:
        await msg.edit_text(f"✅ Права подтверждены! Бот @{perms.get('pub_bot_username') or 'бот'} может публиковать в канал.")
        await state.clear()
        # Redirect back to tenant view
        await callback.message.answer("Можешь продолжить настройку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📡 Управлять RSS", callback_data="cmd:source")],
            [InlineKeyboardButton(text="⏰ Расписание", callback_data="cmd:schedule")],
            [InlineKeyboardButton(text="🔙 К каналу", callback_data=f"tenant:select:{tenant_id}")],
        ]))
        return

    guide = PERMISSION_GUIDE.format(bot_username=perms.get("pub_bot_username") or "бота")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"perm:recheck:{tenant_id}")],
        [InlineKeyboardButton(text="🔙 К каналу", callback_data=f"tenant:select:{tenant_id}")],
    ])
    await msg.edit_text(
        f"❌ Права ещё не настроены.\n\n{guide}",
        parse_mode="Markdown",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("cmd:"))
async def on_cmd_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    cmd = callback.data.split(":", 1)[1]
    await callback.message.answer(f"Используй команду /{cmd} в чате.")


@router.message(SetupStates.waiting_chat_id)
async def process_chat_id(message: Message, state: FSMContext):
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

    perms = await _check_bot_perms(raw)

    if not perms["exists"]:
        guide = PERMISSION_GUIDE.format(bot_username=perms.get("pub_bot_username") or "бота")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"chan:recheck:{raw}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tenant:cancel")],
        ])
        await message.answer(
            f"❌ Не удалось найти канал `{raw}`.\n\n{guide}",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return

    if not perms["ok"]:
        missing = []
        if not perms["can_post"]:
            missing.append("📝 Отправлять сообщения")
        if not perms["can_edit"]:
            missing.append("✏️ Редактировать сообщения")
        guide = PERMISSION_GUIDE.format(bot_username=perms.get("pub_bot_username") or "бота")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"chan:recheck:{raw}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tenant:cancel")],
        ])
        await message.answer(
            f"❌ Бот @{perms.get('pub_bot_username') or 'бота'} не имеет прав в канале «{perms['title']}».\n\n"
            + guide,
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return

    await message.answer(
        f"✅ Канал «{perms['title']}» найден, бот имеет все права."
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


@router.callback_query(F.data.startswith("chan:recheck:"))
async def chan_recheck(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    chat_id = callback.data.split(":", 2)[2]
    msg = await callback.message.answer("⏳ Проверяю права...")
    perms = await _check_bot_perms(chat_id)

    if perms["ok"]:
        await msg.edit_text(f"✅ Права подтверждены для «{perms['title']}»!")
        await state.update_data(chat_id=chat_id)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📰 Новости"), KeyboardButton(text="📝 Блог")],
                [KeyboardButton(text="🛒 Магазин")],
            ],
            resize_keyboard=True,
        )
        await callback.message.answer("Выбери нишу для шаблона промптов:", reply_markup=kb)
        await state.set_state(SetupStates.waiting_niche)
        return

    missing = []
    if not perms["can_post"]:
        missing.append("📝 Отправлять сообщения")
    if not perms["can_edit"]:
        missing.append("✏️ Редактировать сообщения")
    guide = PERMISSION_GUIDE.format(bot_username=perms.get("pub_bot_username") or "бота")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"chan:recheck:{chat_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="tenant:cancel")],
    ])
    await msg.edit_text(
        f"❌ Всё ещё нет прав.\n\n{guide}",
        parse_mode="Markdown",
        reply_markup=kb,
    )


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
            language="ru",
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
        f"/find — найти качественные источники\n"
        f"/template — смена ниши\n"
        f"/schedule — расписание публикаций\n"
        f"/stats — статистика\n"
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
