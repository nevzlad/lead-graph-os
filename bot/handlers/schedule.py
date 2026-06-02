import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from models import Post, Schedule, TenantConfig
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAY_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
NICHE_NAMES = {"news": "📰 Новости", "blog": "📝 Блог", "shop": "🛒 Магазин"}


class ScheduleStates(StatesGroup):
    choosing_time = State()
    choosing_niche = State()


async def _get_user_tenants(user_id: str):
    async with async_session_factory() as session:
        rows = await session.execute(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )
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


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if not tenants:
        await message.answer("Сначала настрой канал: /setup")
        return

    for t in tenants:
        schedules = await _get_schedules(t.tenant_id)
        lines = [f"📋 {t.tg_chat_id} ({_niche_label(t.niche)})"]
        if schedules:
            for s in schedules:
                day = _day_label(s.day_of_week)
                status = "✅" if s.is_active else "❌"
                lines.append(f"  {status} {day} {s.publish_time} — {_niche_label(s.niche)}")
        else:
            lines.append("  Нет расписаний")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить расписание", callback_data=f"sched:add:{t.tenant_id}")],
            [InlineKeyboardButton(text="🗑 Удалить расписание", callback_data=f"sched:del_list:{t.tenant_id}")],
        ])
        await message.answer("\n".join(lines), reply_markup=kb)


@router.message(Command("queue"))
async def cmd_queue(message: Message):
    user_id = str(message.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if not tenants:
        await message.answer("Сначала настрой канал: /setup")
        return

    for t in tenants:
        async with async_session_factory() as session:
            rows = await session.execute(
                select(Post).where(
                    Post.tenant_id == t.tenant_id,
                    Post.status.in_(["rewritten", "rewritten_fallback"]),
                ).order_by(Post.created_at).limit(10)
            )
            posts = rows.scalars().all()

        if not posts:
            await message.answer(f"📭 {t.tg_chat_id}: нет постов в очереди")
            continue

        lines = [f"📌 {t.tg_chat_id} — очередь ({len(posts)})"]
        for p in posts:
            tag = "✅" if p.status == "rewritten" else "⚠️"
            title = (p.title or "")[:60]
            time = p.created_at.strftime("%d.%m %H:%M")
            lines.append(f"  {tag} [{time}] {title}")
        await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("sched:add:"))
async def sched_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tenant_id = callback.data.split(":", 2)[2]
    await state.update_data(tenant_id=tenant_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Каждый день", callback_data="sched_day:-1")],
        *[[InlineKeyboardButton(text=WEEKDAY_FULL[i], callback_data=f"sched_day:{i}")] for i in range(7)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel")],
    ])
    await callback.message.answer("Выбери день недели:", reply_markup=kb)


@router.callback_query(F.data.startswith("sched_day:"))
async def sched_choose_day(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    day = int(callback.data.split(":", 1)[1])
    await state.update_data(day_of_week=None if day == -1 else day)
    await callback.message.answer(
        "Время публикации в UTC (формат ЧЧ:ММ, например 14:00):"
    )
    await state.set_state(ScheduleStates.choosing_time)


@router.message(ScheduleStates.choosing_time)
async def sched_choose_time(message: Message, state: FSMContext):
    t = str(message.text).strip()
    if not (len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit()):
        await message.answer("Неверный формат. Используй ЧЧ:ММ, например 14:00")
        return
    h, m = int(t[:2]), int(t[3:])
    if h < 0 or h > 23 or m < 0 or m > 59:
        await message.answer("Время вне диапазона. Используй ЧЧ:ММ от 00:00 до 23:59")
        return
    await state.update_data(publish_time=t)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📰 Новости", callback_data="sched_niche:news")],
        [InlineKeyboardButton(text="📝 Блог", callback_data="sched_niche:blog")],
        [InlineKeyboardButton(text="🛒 Магазин", callback_data="sched_niche:shop")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel")],
    ])
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
        f"🏷 {_niche_label(niche)}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="sched:save"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="sched:cancel")],
    ])
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{_day_label(s.day_of_week)} {s.publish_time} — {_niche_label(s.niche)}",
            callback_data=f"sched:del:{s.id}",
        )]
        for s in schedules
    ])
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
