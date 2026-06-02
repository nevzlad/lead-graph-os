import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from models import Post, Schedule, TenantConfig
from services.telegram import strip_html
from services.validators import check_post_for_queue
from tasks.rewriter import rewrite_post
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAY_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
NICHE_NAMES = {"news": "📰 Новости", "blog": "📝 Блог", "shop": "🛒 Магазин"}
TIME_OPTIONS = [
    ("Через 30 мин", 30),
    ("Через 1 час", 60),
    ("Через 2 часа", 120),
    ("Через 4 часа", 240),
    ("Через 8 часов", 480),
    ("Через 24 часа", 1440),
    ("Своё время", -1),
]
INTERVALS = [
    (60, "Каждый час"),
    (120, "Каждые 2 часа"),
    (240, "Каждые 4 часа"),
    (360, "Каждые 6 часов"),
    (480, "Каждые 8 часов"),
    (720, "Каждые 12 часов"),
    (1440, "Раз в день"),
    (2880, "Раз в 2 дня"),
    (0, "✏️ Свой период"),
]


class ScheduleStates(StatesGroup):
    choosing_time = State()
    choosing_interval = State()
    choosing_custom_interval = State()
    choosing_niche = State()
    editing_title = State()
    editing_content = State()
    scheduling_time = State()
    scheduling_custom_time = State()


async def _get_user_tenants(user_id: str):
    async with async_session_factory() as session:
        rows = await session.execute(select(TenantConfig).where(TenantConfig.tg_user_id == user_id))
        return rows.scalars().all()


async def _get_schedules(tenant_id: str):
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Schedule).where(Schedule.tenant_id == tenant_id).order_by(Schedule.publish_time)
        )
        return rows.scalars().all()


def _day_label(d: int | None) -> str:
    if d is None:
        return "📅 Каждый день"
    return WEEKDAYS[d]


def _niche_label(n: str) -> str:
    return NICHE_NAMES.get(n, n)


def _interval_label(m: int) -> str:
    for val, label in INTERVALS:
        if val == m:
            return label
    if m < 60:
        return f"Каждые {m} мин"
    return f"Каждые {m // 60} ч"


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if not tenants:
        await message.answer("Сначала настрой канал: /setup")
        return

    for t in tenants:
        ap = "✅" if getattr(t, "auto_publish", True) else "❌"
        schedules = await _get_schedules(t.tenant_id)
        lines = [
            f"📋 {t.tg_chat_id} ({_niche_label(t.niche)})",
            f"Автопубликация: {ap}",
        ]
        if schedules:
            for s in schedules:
                day = _day_label(s.day_of_week)
                status = "✅" if s.is_active else "❌"
                interval = _interval_label(s.interval_minutes)
                lines.append(f"  {status} {day} {s.publish_time} ({interval}) — {_niche_label(s.niche)}")
        else:
            lines.append("  Нет расписаний")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🔄 Автопубликация: {'Вкл' if getattr(t, 'auto_publish', True) else 'Выкл'}",
                        callback_data=f"autopub:toggle:{t.tenant_id}",
                    )
                ],
                [InlineKeyboardButton(text="➕ Добавить расписание", callback_data=f"sched:add:{t.tenant_id}")],
                [InlineKeyboardButton(text="🗑 Удалить расписание", callback_data=f"sched:del_list:{t.tenant_id}")],
            ]
        )
        await message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("autopub:toggle:"))
async def autopub_toggle(callback: CallbackQuery):
    await callback.answer()
    tenant_id = callback.data.split(":", 2)[2]
    async with async_session_factory() as session:
        tc = await session.scalar(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
        if tc:
            tc.auto_publish = not tc.auto_publish
            await session.commit()
            status = "включена" if tc.auto_publish else "выключена"
            await callback.message.answer(f"Автопубликация {status}.")
        else:
            await callback.message.answer("Канал не найден.")


@router.message(Command("queue"))
async def cmd_queue(message: Message, page: int = 0):
    user_id = str(message.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if not tenants:
        await message.answer("Сначала настрой канал: /setup")
        return

    for t in tenants:
        await _show_queue_page(message, t, page)


async def _show_queue_page(msg_or_cb, tenant: TenantConfig, page: int = 0):
    limit = 10
    target_lang = tenant.language or "ru"
    offset = page * limit
    async with async_session_factory() as session:
        total = await session.scalar(
            select(func.count(Post.id)).where(
                Post.tenant_id == tenant.tenant_id,
                Post.status.in_(["rewritten", "rewritten_fallback", "scheduled", "draft", "raw"]),
            )
        )
        rows = await session.execute(
            select(Post)
            .where(
                Post.tenant_id == tenant.tenant_id,
                Post.status.in_(["rewritten", "rewritten_fallback", "scheduled", "draft", "raw"]),
            )
            .order_by(Post.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        posts = rows.scalars().all()

    valid_posts = []
    hidden_count = 0
    for p in posts:
        valid, _ = check_post_for_queue(p.content, target_lang, p.status)
        if valid:
            valid_posts.append(p)
        else:
            hidden_count += 1

    if not valid_posts:
        if hidden_count > 0:
            text = f"📭 {tenant.tg_chat_id}: все {hidden_count} постов невалидны"
        else:
            text = f"📭 {tenant.tg_chat_id}: нет постов в очереди"
        if isinstance(msg_or_cb, CallbackQuery):
            await msg_or_cb.answer()
            await msg_or_cb.message.edit_text(text)
        else:
            await msg_or_cb.answer(text)
        return

    max_page = (max(total, 1) - 1) // limit
    total_pages = max_page + 1 if total else 0
    niche_icon = {"news": "📰", "blog": "📝", "shop": "🛒"}.get(tenant.niche, "📄")
    total_str = str(total)
    if hidden_count:
        total_str += f", {hidden_count} скрыто"
    lines = [f"{niche_icon} {tenant.tg_chat_id} — очередь ({total_str})"]
    for p in valid_posts:
        pause_icon = "⏸" if p.paused else ""
        status_icon = {"rewritten": "✅", "rewritten_fallback": "⚠️", "raw": "📝", "scheduled": "⏰", "draft": "📄"}.get(
            p.status, "❓"
        )
        tag = pause_icon if p.paused else status_icon
        title = (p.title or "—")[:50]
        extra = ""
        if p.status == "scheduled" and p.scheduled_at:
            extra = f" ⤵ {p.scheduled_at.strftime('%d.%m %H:%M')}"
        lines.append(f"  {tag} [{p.created_at.strftime('%d.%m %H:%M')}] {title}{extra}")

    kb = []
    for p in valid_posts:
        pause_icon = "⏸" if p.paused else ""
        status_icon = {"rewritten": "✅", "rewritten_fallback": "⚠️", "raw": "📝", "scheduled": "⏰", "draft": "📄"}.get(
            p.status, "❓"
        )
        tag = pause_icon or status_icon
        title = (p.title or "—")[:40]
        badge = ""
        if p.status == "scheduled" and p.scheduled_at:
            badge = f" ⤵{p.scheduled_at.strftime('%H:%M')}"
        kb.append(
            [
                InlineKeyboardButton(text=f"{tag} {title}{badge}", callback_data=f"queue:post:{p.id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"queue:del:{p.id}"),
            ]
        )

    action_row = [
        InlineKeyboardButton(text="🌐 Перевести все", callback_data=f"queue:retranslate_all:{tenant.tenant_id}")
    ]
    kb.append(action_row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"queue:page:{page - 1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="queue:noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"queue:page:{page + 1}"))
    if nav:
        kb.append(nav)

    reply = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(msg_or_cb, CallbackQuery):
        await msg_or_cb.answer()
        await msg_or_cb.message.edit_text("\n".join(lines), reply_markup=reply)
    else:
        await msg_or_cb.answer("\n".join(lines), reply_markup=reply)


@router.callback_query(F.data.startswith("queue:page:"))
async def queue_page(callback: CallbackQuery):
    page = int(callback.data.split(":", 2)[2])
    user_id = str(callback.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if tenants:
        await _show_queue_page(callback, tenants[0], page)


@router.callback_query(F.data == "queue:noop")
async def queue_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("queue:post:"))
async def queue_show_post(callback: CallbackQuery):
    try:
        post_id = int(callback.data.split(":", 2)[2])
        async with async_session_factory() as session:
            post = await session.get(Post, post_id)
            if not post:
                await callback.answer("Пост не найден")
                return

        status_icon = {
            "rewritten": "✅",
            "rewritten_fallback": "⚠️",
            "published": "📤",
            "raw": "📝",
            "scheduled": "⏰",
            "draft": "📄",
        }.get(post.status, "❓")
        pause_label = "⏸ Приостановлен" if post.paused else ""
        created = post.created_at.strftime("%d.%m.%Y %H:%M")
        scheduled_label = (
            f"\n⏰ Запланирован: {post.scheduled_at.strftime('%d.%m.%Y %H:%M')} UTC" if post.scheduled_at else ""
        )
        preview = (post.content or "")[:300]
        text = (
            f"{status_icon} *{post.title or 'Без названия'}* {pause_label}\n"
            f"📊 Статус: {post.status}\n"
            f"📅 Создан: {created}{scheduled_label}\n"
            f"📝 {preview}{'…' if len((post.content or '')) > 300 else ''}"
        )
        kb_rows = [
            [
                InlineKeyboardButton(text="👁 Предпросмотр", callback_data=f"queue:preview:{post.id}"),
                InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"queue:publish:{post.id}"),
            ],
            [
                InlineKeyboardButton(text="✏️ Заголовок", callback_data=f"queue:edit_title:{post.id}"),
                InlineKeyboardButton(text="✏️ Текст", callback_data=f"queue:edit_content:{post.id}"),
            ],
            [
                InlineKeyboardButton(text="⏰ Отложить", callback_data=f"queue:schedule:{post.id}"),
                InlineKeyboardButton(
                    text="⏸ Пауза" if not post.paused else "▶️ Возобновить", callback_data=f"queue:toggle:{post.id}"
                ),
            ],
            [
                InlineKeyboardButton(text="🔧 Починить", callback_data=f"queue:repair:{post.id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"queue:del:{post.id}"),
            ],
            [InlineKeyboardButton(text="🌐 Перевести", callback_data=f"queue:retranslate:{post.id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="queue:list")],
        ]
        await callback.answer()
        await callback.message.edit_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
        )
    except Exception as e:
        logger.error("queue:post error: %s", e, exc_info=True)
        await callback.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data == "queue:list")
async def queue_back_to_list(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if tenants:
        await _show_queue_page(callback, tenants[0], 0)


@router.callback_query(F.data.startswith("queue:preview:"))
async def queue_preview_post(callback: CallbackQuery):
    try:
        post_id = int(callback.data.split(":", 2)[2])
        async with async_session_factory() as session:
            post = await session.get(Post, post_id)
            if not post:
                await callback.answer("Пост не найден")
                return

        from services.formatter import format_post

        formatted = format_post(post.title, post.content, post.link)

        await callback.answer()
        if post.image:
            await callback.message.answer_photo(
                photo=post.image,
                caption=formatted,
                parse_mode="HTML",
            )
        else:
            await callback.message.answer(formatted, parse_mode="HTML")
    except Exception as e:
        logger.error("queue:preview error: %s", e, exc_info=True)
        await callback.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data.startswith("queue:publish:"))
async def queue_publish_post(callback: CallbackQuery):
    await callback.answer()
    post_id = int(callback.data.split(":", 2)[2])
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            await callback.message.answer("Пост не найден.")
            return
        tenant = await session.scalar(select(TenantConfig).where(TenantConfig.tenant_id == post.tenant_id))
        chat_id = tenant.tg_chat_id if tenant else None

    if not chat_id:
        await callback.message.answer("Канал не найден.")
        return

    await callback.message.answer("⏳ Публикую...")
    from services.telegram import _send_message_async, _send_photo_async

    text = strip_html((post.content or "")[:4096])
    try:
        if post.image:
            external_id = await _send_photo_async(chat_id, post.image, text)
        else:
            external_id = await _send_message_async(chat_id, text)
    except Exception as e:
        logger.error(f"Manual publish failed: {e}")
        async with async_session_factory() as session:
            p = await session.get(Post, post_id)
            if p:
                p.status = "failed"
                await session.commit()
        await callback.message.answer(f"❌ Ошибка публикации: {e}")
        return

    async with async_session_factory() as session:
        p = await session.get(Post, post_id)
        if p:
            p.external_id = external_id
            p.status = "published"
            p.scheduled_at = datetime.now(timezone.utc)
            await session.commit()

    await callback.message.answer("✅ Пост опубликован!")


@router.callback_query(F.data.startswith("queue:repair:"))
async def queue_repair_post(callback: CallbackQuery):
    try:
        post_id = int(callback.data.split(":", 2)[2])
        await callback.answer()
        status_msg = await callback.message.answer("🔧 Диагностика поста...")

        from services.repair import check_post_published, retry_publish

        check = await check_post_published(post_id)
        if check.get("published") and check.get("verified"):
            await status_msg.edit_text("✅ Пост уже опубликован, проблем нет.")
            return

        issues = check.get("issues", [])
        if issues:
            lines = ["🔍 Найденные проблемы:", ""]
            for iss in issues:
                lines.append(f"  • {iss}")
            await status_msg.edit_text("\n".join(lines))
        else:
            await status_msg.edit_text("🔍 Видимых проблем нет, пробую перепубликацию...")

        result = await retry_publish(post_id)
        if result["success"]:
            fixes = result.get("fixes_applied", [])
            fix_text = f"✅ Перепубликация успешна (msg_id={result['external_id']})"
            if fixes:
                fix_text += f"\n🔧 Применены исправления: {', '.join(fixes)}"
            await status_msg.edit_text(fix_text)
        else:
            await status_msg.edit_text(f"❌ Не удалось исправить: {result.get('error', 'неизвестная ошибка')}")

    except Exception as e:
        logger.error("queue:repair error: %s", e, exc_info=True)
        await callback.answer(f"❌ {e}", show_alert=True)


@router.callback_query(F.data.startswith("queue:toggle:"))
async def queue_toggle_post(callback: CallbackQuery):
    post_id = int(callback.data.split(":", 2)[2])
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if post:
            post.paused = not post.paused
            await session.commit()
            status = "приостановлен" if post.paused else "возобновлён"
            await callback.answer(f"Пост {status}")
    # Refresh detail view
    await queue_show_post(callback)


@router.callback_query(F.data.startswith("queue:del:"))
async def queue_confirm_delete(callback: CallbackQuery):
    post_id = int(callback.data.split(":", 2)[2])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"queue:deldone:{post_id}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"queue:post:{post_id}"),
            ],
        ]
    )
    await callback.answer()
    await callback.message.edit_text("🗑 Удалить пост навсегда?", reply_markup=kb)


@router.callback_query(F.data.startswith("queue:deldone:"))
async def queue_delete_post(callback: CallbackQuery):
    post_id = int(callback.data.split(":", 2)[2])
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if post:
            await session.delete(post)
            await session.commit()
    await callback.answer("🗑 Пост удалён")
    user_id = str(callback.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if tenants:
        await _show_queue_page(callback, tenants[0], 0)


@router.callback_query(F.data.startswith("queue:retranslate:"))
async def queue_retranslate_post(callback: CallbackQuery):
    post_id = int(callback.data.split(":", 2)[2])
    await callback.answer("⏳ Перевожу...")

    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            await callback.message.edit_text("❌ Пост не найден")
            return
        tenant_id = post.tenant_id

    try:
        await rewrite_post(post_id, tenant_id, force=True)
        await callback.answer("✅ Перевод завершён")
    except Exception as e:
        logger.error("retranslate error: %s", e, exc_info=True)
        await callback.answer(f"❌ {e}", show_alert=True)

    await queue_show_post(callback)


@router.callback_query(F.data.startswith("queue:retranslate_all:"))
async def queue_retranslate_all(callback: CallbackQuery):
    tenant_id = callback.data.split(":", 2)[2]
    await callback.answer("⏳ Перевожу все посты...")

    async with async_session_factory() as session:
        posts = await session.execute(
            select(Post).where(
                Post.tenant_id == tenant_id,
                Post.status.in_(["raw", "rewritten", "rewritten_fallback", "scheduled", "draft"]),
            )
        )
        all_posts = posts.scalars().all()

    if not all_posts:
        await callback.message.edit_text("Нет постов для перевода")
        return

    total = len(all_posts)
    ok = 0
    fail = 0

    for p in all_posts:
        try:
            result = await rewrite_post(p.id, tenant_id, force=True)
            if result in ("rewritten", "rewritten_fallback"):
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1

    await callback.message.edit_text(
        f"🌐 Перевод завершён: {ok}/{total} успешно{' · ' + str(fail) + ' ошибок' if fail else ''}"
    )
    tenants = await _get_user_tenants(str(callback.from_user.id))
    if tenants:
        await _show_queue_page(callback, tenants[0], 0)


@router.callback_query(F.data.startswith("queue:edit_title:"))
async def queue_edit_title_start(callback: CallbackQuery, state: FSMContext):
    post_id = int(callback.data.split(":", 2)[2])
    await state.update_data(edit_post_id=post_id)
    await callback.answer()
    await callback.message.answer("Введи новый заголовок:")
    await state.set_state(ScheduleStates.editing_title)


@router.message(ScheduleStates.editing_title)
async def queue_edit_title_done(message: Message, state: FSMContext):
    data = await state.get_data()
    post_id = data["edit_post_id"]
    title = str(message.text).strip()
    if not title:
        await message.answer("Заголовок не может быть пустым.")
        return
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if post:
            post.title = title
            await session.commit()
    await state.clear()
    await message.answer("✅ Заголовок обновлён!")


@router.callback_query(F.data.startswith("queue:edit_content:"))
async def queue_edit_content_start(callback: CallbackQuery, state: FSMContext):
    post_id = int(callback.data.split(":", 2)[2])
    await state.update_data(edit_post_id=post_id)
    await callback.answer()
    await callback.message.answer("Введи новый текст поста (или /skip чтобы оставить как есть):")
    await state.set_state(ScheduleStates.editing_content)


@router.message(ScheduleStates.editing_content)
async def queue_edit_content_done(message: Message, state: FSMContext):
    data = await state.get_data()
    post_id = data["edit_post_id"]
    content = str(message.text).strip()
    if content == "/skip":
        await state.clear()
        await message.answer("Текст не изменён.")
        return
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if post:
            post.content = content
            await session.commit()
    await state.clear()
    await message.answer("✅ Текст обновлён!")


@router.callback_query(F.data.startswith("queue:schedule:"))
async def queue_schedule_start(callback: CallbackQuery, state: FSMContext):
    post_id = int(callback.data.split(":", 2)[2])
    await state.update_data(schedule_post_id=post_id)
    await callback.answer()
    from aiogram.types import InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"queue:sched_time:{val}:{post_id}")]
            for label, val in TIME_OPTIONS
        ]
        + [[InlineKeyboardButton(text="❌ Отмена", callback_data=f"queue:post:{post_id}")]]
    )
    await callback.message.answer("⏰ Через сколько опубликовать?", reply_markup=kb)
    await state.set_state(ScheduleStates.scheduling_time)


@router.callback_query(ScheduleStates.scheduling_time, F.data.startswith("queue:sched_time:"))
async def queue_schedule_time(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split(":")
    val = int(parts[2])
    post_id = int(parts[3])

    if val < 0:
        await callback.message.answer("Введи время в формате ЧЧ:ММ (UTC), например 14:30:")
        await state.update_data(schedule_post_id=post_id)
        await state.set_state(ScheduleStates.scheduling_custom_time)
        return

    now = datetime.now(timezone.utc)
    scheduled_at = now + timedelta(minutes=val) if val > 0 else now
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if post:
            post.scheduled_at = scheduled_at
            post.status = "scheduled"
            post.paused = False
            await session.commit()
    await state.clear()
    await callback.message.answer(f"✅ Пост запланирован на {scheduled_at.strftime('%d.%m.%Y %H:%M')} UTC")


@router.message(ScheduleStates.scheduling_custom_time)
async def queue_schedule_custom_time(message: Message, state: FSMContext):
    t = str(message.text).strip()
    if not (len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit()):
        await message.answer("Неверный формат. Используй ЧЧ:ММ, например 14:30")
        return
    h, m = int(t[:2]), int(t[3:])
    if h < 0 or h > 23 or m < 0 or m > 59:
        await message.answer("Время вне диапазона.")
        return
    data = await state.get_data()
    post_id = data["schedule_post_id"]
    now = datetime.now(timezone.utc)
    scheduled_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if scheduled_at <= now:
        scheduled_at += timedelta(days=1)
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if post:
            post.scheduled_at = scheduled_at
            post.status = "scheduled"
            post.paused = False
            await session.commit()
    await state.clear()
    await message.answer(f"✅ Пост запланирован на {scheduled_at.strftime('%d.%m.%Y %H:%M')} UTC")


@router.callback_query(F.data.startswith("sched:add:"))
async def sched_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tenant_id = callback.data.split(":", 2)[2]
    await state.update_data(tenant_id=tenant_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Каждый день", callback_data="sched_day:-1")],
            *[[InlineKeyboardButton(text=WEEKDAY_FULL[i], callback_data=f"sched_day:{i}")] for i in range(7)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel")],
        ]
    )
    await callback.message.answer("Выбери день недели:", reply_markup=kb)


@router.callback_query(F.data.startswith("sched_day:"))
async def sched_choose_day(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    day = int(callback.data.split(":", 1)[1])
    await state.update_data(day_of_week=None if day == -1 else day)
    await callback.message.answer("Время первой публикации в UTC (формат ЧЧ:ММ, например 08:00):")
    await state.set_state(ScheduleStates.choosing_time)


@router.message(ScheduleStates.choosing_time)
async def sched_choose_time(message: Message, state: FSMContext):
    t = str(message.text).strip()
    if not (len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit()):
        await message.answer("Неверный формат. Используй ЧЧ:ММ, например 08:00")
        return
    h, m = int(t[:2]), int(t[3:])
    if h < 0 or h > 23 or m < 0 or m > 59:
        await message.answer("Время вне диапазона.")
        return
    await state.update_data(publish_time=t)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"sched_int:{val}")] for val, label in INTERVALS
        ]
        + [[InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel")]]
    )
    await message.answer("Периодичность публикаций:", reply_markup=kb)
    await state.set_state(ScheduleStates.choosing_interval)


@router.callback_query(F.data.startswith("sched_int:"))
async def sched_choose_interval(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    interval = int(callback.data.split(":", 1)[1])

    if interval == 0:
        await callback.message.answer("Введи периодичность в минутах (например: 30, 90, 180):")
        await state.set_state(ScheduleStates.choosing_custom_interval)
        return

    await state.update_data(interval_minutes=interval)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📰 Новости", callback_data="sched_niche:news")],
            [InlineKeyboardButton(text="📝 Блог", callback_data="sched_niche:blog")],
            [InlineKeyboardButton(text="🛒 Магазин", callback_data="sched_niche:shop")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel")],
        ]
    )
    await callback.message.answer("Выбери тематику:", reply_markup=kb)
    await state.set_state(ScheduleStates.choosing_niche)


@router.message(ScheduleStates.choosing_custom_interval)
async def sched_choose_custom_interval(message: Message, state: FSMContext):
    raw = str(message.text).strip()
    if not raw.isdigit() or int(raw) < 10 or int(raw) > 43200:
        await message.answer("Введи число от 10 до 43200 (минут), например: 30, 180, 1440")
        return
    interval = int(raw)
    await state.update_data(interval_minutes=interval)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📰 Новости", callback_data="sched_niche:news")],
            [InlineKeyboardButton(text="📝 Блог", callback_data="sched_niche:blog")],
            [InlineKeyboardButton(text="🛒 Магазин", callback_data="sched_niche:shop")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel")],
        ]
    )
    await message.answer("Выбери тематику:", reply_markup=kb)
    await state.set_state(ScheduleStates.choosing_niche)


@router.callback_query(F.data.startswith("sched_niche:"))
async def sched_choose_niche(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    niche = callback.data.split(":", 1)[1]
    data = await state.get_data()
    day_label = "Каждый день" if data["day_of_week"] is None else WEEKDAY_FULL[data["day_of_week"]]
    text = (
        f"Подтверди расписание:\n"
        f"📅 {day_label}\n"
        f"⏰ {data['publish_time']} UTC\n"
        f"🔄 {_interval_label(data['interval_minutes'])}\n"
        f"🏷 {_niche_label(niche)}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="sched:save"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel"),
            ],
        ]
    )
    await state.update_data(niche=niche)
    await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "sched:save")
async def sched_save(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    sched = Schedule(
        tenant_id=data["tenant_id"],
        day_of_week=data["day_of_week"],
        publish_time=data["publish_time"],
        niche=data["niche"],
        interval_minutes=data.get("interval_minutes", 1440),
        is_active=True,
    )
    async with async_session_factory() as session:
        session.add(sched)
        await session.commit()
    await state.clear()
    await callback.message.answer("✅ Расписание добавлено!")


@router.callback_query(F.data == "sched:cancel")
async def sched_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("❌ Отменено.")


@router.callback_query(F.data.startswith("sched:del_list:"))
async def sched_del_list(callback: CallbackQuery):
    await callback.answer()
    tenant_id = callback.data.split(":", 2)[2]
    schedules = await _get_schedules(tenant_id)
    if not schedules:
        await callback.message.answer("Нет расписаний для удаления.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{_day_label(s.day_of_week)} {s.publish_time} — {_niche_label(s.niche)}",
                    callback_data=f"sched:del:{s.id}",
                )
            ]
            for s in schedules
        ]
    )
    await callback.message.answer("Выбери расписание для удаления:", reply_markup=kb)


@router.callback_query(F.data.startswith("sched:del:"))
async def sched_delete(callback: CallbackQuery):
    await callback.answer()
    sched_id = int(callback.data.split(":", 2)[2])
    async with async_session_factory() as session:
        s = await session.get(Schedule, sched_id)
        if s:
            await session.delete(s)
            await session.commit()
    await callback.message.answer("🗑 Расписание удалено.")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = str(message.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if not tenants:
        await message.answer("Сначала настрой канал: /setup")
        return

    from models import Source

    for t in tenants:
        async with async_session_factory() as session:
            total_posts = await session.scalar(select(func.count(Post.id)).where(Post.tenant_id == t.tenant_id))
            published = await session.scalar(
                select(func.count(Post.id)).where(Post.tenant_id == t.tenant_id, Post.status == "published")
            )
            failed = await session.scalar(
                select(func.count(Post.id)).where(Post.tenant_id == t.tenant_id, Post.status == "failed")
            )
            queued = await session.scalar(
                select(func.count(Post.id)).where(
                    Post.tenant_id == t.tenant_id,
                    Post.status.in_(["rewritten", "rewritten_fallback"]),
                )
            )
            raw = await session.scalar(
                select(func.count(Post.id)).where(Post.tenant_id == t.tenant_id, Post.status == "raw")
            )
            src_count = await session.scalar(
                select(func.count(Source.id)).where(Source.tenant_id == t.tenant_id, Source.is_active == 1)
            )
            sched_count = await session.scalar(
                select(func.count(Schedule.id)).where(Schedule.tenant_id == t.tenant_id, Schedule.is_active)
            )
            ap = getattr(t, "auto_publish", True)

        text = (
            f"📊 *Статистика {t.tg_chat_id}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📰 Всего постов: {total_posts or 0}\n"
            f"📤 Опубликовано: {published or 0}\n"
            f"📥 В очереди: {queued or 0}\n"
            f"📝 Черновики: {raw or 0}\n"
            f"❌ Ошибки: {failed or 0}\n"
            f"━━━━━━━━━━━━━━\n"
            f"📡 Активных RSS: {src_count or 0}\n"
            f"⏰ Расписаний: {sched_count or 0}\n"
            f"🔄 Автопубликация: {'✅' if ap else '❌'}\n"
            f"🏷 Ниша: {_niche_label(t.niche)}"
        )
        await message.answer(text, parse_mode="Markdown")
