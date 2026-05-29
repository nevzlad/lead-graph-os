import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from config import settings
from models import TenantConfig
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)

PAYMENT_LINK_MOCK = "https://yookassa.ru/mock_payment?amount=990&currency=RUB"

STATUS_LABELS = {
    "trial": "Trial",
    "active": "Active",
    "expired": "Expired",
}


@router.message(Command("billing"))
async def cmd_billing(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить подписку ($9/мес)", url=PAYMENT_LINK_MOCK)],
            [InlineKeyboardButton(text="📊 Статус", callback_data="billing_status")],
        ]
    )
    await message.answer(
        "💎 Тарифы Lead-Graph OS:\n"
        f"• Trial: {settings.TRIAL_DAYS} дней бесплатно\n"
        "• Basic: $9/мес (1000 постов)\n"
        "• Pro: $15/мес (безлимит)\n\n"
        "Оплата через ЮKassa/Stripe (mock). Статус проверяется по tenant.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "billing_status")
async def check_status(cb: CallbackQuery):
    user_id = str(cb.from_user.id)

    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantConfig).where(TenantConfig.tg_user_id == user_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            await cb.answer("Сначала пройди /setup", show_alert=True)
            return
        status = config.billing_status
        tenant_id = config.tenant_id

    label = STATUS_LABELS.get(status, status)
    await cb.answer(f"✅ Статус: {label}\nTenant: {tenant_id[:8]}…", show_alert=True)
    logger.info(f"Tenant {tenant_id}: billing status checked ({status})")
