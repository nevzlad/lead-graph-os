import logging
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import select

from config import settings
from models import TenantConfig
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)

NICHE_BUTTONS = ["📰 Новости", "📝 Блог", "🛒 Магазин"]
NICHE_MAP = {"📰 Новости": "news", "📝 Блог": "blog", "🛒 Магазин": "shop"}


class SetupStates(StatesGroup):
    waiting_chat_id = State()
    waiting_niche = State()


async def _start_setup(message: Message, state: FSMContext):
    await message.answer(
        "Привет! Настроим твой канал. Отправь ID Telegram-канала или группы, "
        "куда будем постить (начинается с -100 или @username канала)."
    )
    await state.set_state(SetupStates.waiting_chat_id)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await _start_setup(message, state)


@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext):
    await _start_setup(message, state)


@router.message(SetupStates.waiting_chat_id)
async def process_chat_id(message: Message, state: FSMContext):
    await state.update_data(chat_id=str(message.text).strip())
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
    data = await state.get_data()
    niche = NICHE_MAP.get(message.text, "news")
    tenant_id = str(uuid.uuid4())
    trial_end = datetime.now(timezone.utc) + timedelta(days=settings.TRIAL_DAYS)
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        new_cfg = TenantConfig(
            tenant_id=tenant_id,
            tg_user_id=str(message.from_user.id),
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
        config = result.scalar_one_or_none()
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
