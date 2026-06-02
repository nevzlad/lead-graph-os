import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from models import Source, TenantConfig
from utils.db import async_session_factory

router = Router()
logger = logging.getLogger(__name__)

CURATED_SOURCES = {
    "news": {
        "title": "📰 Новостные RSS",
        "feeds": [
            ("Lenta.ru", "https://lenta.ru/rss"),
            ("РИА Новости", "https://ria.ru/export/rss2/archive/index.xml"),
            ("ТАСС", "https://tass.ru/rss/v2.xml"),
            ("Meduza", "https://meduza.io/rss/all"),
            ("BBC Russian", "https://feeds.bbci.co.uk/russian/rss.xml"),
            ("Habr — все", "https://habr.com/ru/rss/all/all/"),
            ("VC.ru — новости", "https://vc.ru/rss/news"),
            ("Forbes Russia", "https://www.forbes.ru/rss"),
            ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
            ("Коммерсантъ", "https://www.kommersant.ru/rss/main.xml"),
        ],
    },
    "blog": {
        "title": "📝 Блог-ленты",
        "feeds": [
            ("Habr — популярное", "https://habr.com/ru/rss/popular/all/"),
            ("DTF", "https://dtf.ru/rss/all"),
            ("TJournal", "https://tjournal.ru/rss"),
            ("Medium — Top Stories", "https://medium.com/feed/top-stories"),
            ("Dev.to", "https://dev.to/feed"),
            ("Spark.ru", "https://spark.ru/rss/all"),
            ("Lifehacker Russia", "https://lifehacker.ru/feed/"),
            ("The Village", "https://www.the-village.ru/rss"),
        ],
    },
    "shop": {
        "title": "🛒 Магазин/товары",
        "feeds": [
            ("Ecommerce News", "https://ecommercenews.eu/feed/"),
            ("Retail & Loyalty", "https://retail-loyalty.org/rss/articles/"),
            ("Shopify Blog", "https://www.shopify.com/blog/feed.xml"),
            ("BigCommerce Blog", "https://www.bigcommerce.com/blog/feed/"),
            ("Product Hunt", "https://www.producthunt.com/feed"),
        ],
    },
}


async def _get_user_tenants(user_id: str):
    async with async_session_factory() as session:
        rows = await session.execute(select(TenantConfig).where(TenantConfig.tg_user_id == user_id))
        return rows.scalars().all()


async def _source_exists(tenant_id: str, url: str) -> bool:
    async with async_session_factory() as session:
        row = await session.scalar(
            select(Source.id).where(
                Source.tenant_id == tenant_id,
                Source.url == url,
            )
        )
        return row is not None


async def _add_source(tenant_id: str, url: str, title: str, niche: str) -> int | None:
    exists = await _source_exists(tenant_id, url)
    if exists:
        return None

    source = Source(
        tenant_id=tenant_id,
        source_type="rss",
        url=url,
        name=title,
        config={"niche": niche},
        is_active=1,
        created_at=datetime.now(timezone.utc),
    )
    async with async_session_factory() as session:
        session.add(source)
        await session.commit()
        return source.id


@router.message(Command("find"))
async def cmd_find(message: Message):
    user_id = str(message.from_user.id)
    tenants = await _get_user_tenants(user_id)
    if not tenants:
        await message.answer("Сначала настрой канал: /setup")
        return

    if len(tenants) == 1:
        t = tenants[0]
        niche = t.niche
        kb_rows = []
        feeds = CURATED_SOURCES.get(niche, CURATED_SOURCES["news"])["feeds"]
        for i, (feed_title, feed_url) in enumerate(feeds):
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"➕ {feed_title}",
                        callback_data=f"find:add:{t.tenant_id}:{i}",
                    )
                ]
            )
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await message.answer(
            f"Поиск RSS для ниши «{niche}»:",
            reply_markup=kb,
        )
        return

    # Multiple tenants — ask which one
    kb_rows = []
    for t in tenants:
        niche_icon = {"news": "📰", "blog": "📝", "shop": "🛒"}.get(t.niche, "📄")
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=f"{niche_icon} {t.tg_chat_id}",
                    callback_data=f"find:list:{t.tenant_id}",
                )
            ]
        )
    await message.answer(
        "Выбери канал для поиска источников:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(F.data.startswith("find:list:"))
async def find_list(callback: CallbackQuery):
    await callback.answer()
    tenant_id = callback.data.split(":", 2)[2]
    async with async_session_factory() as session:
        tenant = await session.scalar(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
        if not tenant:
            await callback.message.answer("Канал не найден.")
            return

    feeds = CURATED_SOURCES.get(tenant.niche, CURATED_SOURCES["news"])["feeds"]
    kb_rows = []
    for i, (feed_title, feed_url) in enumerate(feeds):
        exists = await _source_exists(tenant_id, feed_url)
        prefix = "✅" if exists else "➕"
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix} {feed_title}",
                    callback_data=f"find:add:{tenant_id}:{i}",
                )
            ]
        )
    await callback.message.edit_text(
        f"Рекомендованные RSS для ниши «{tenant.niche}»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(F.data.startswith("find:add:"))
async def find_add(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":", 3)
    tenant_id = parts[2]
    feed_index = int(parts[3])

    async with async_session_factory() as session:
        tenant = await session.scalar(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
        if not tenant:
            await callback.message.answer("Канал не найден.")
            return

    feeds = CURATED_SOURCES.get(tenant.niche, CURATED_SOURCES["news"])["feeds"]
    if feed_index < 0 or feed_index >= len(feeds):
        await callback.message.answer("Источник не найден.")
        return

    feed_title, feed_url = feeds[feed_index]
    source_id = await _add_source(tenant_id, feed_url, feed_title, tenant.niche)
    if source_id:
        await callback.message.answer(f"✅ «{feed_title}» добавлен в RSS-источники!")
        logger.info(f"Tenant {tenant_id}: added curated source {feed_title} ({feed_url})")
    else:
        await callback.message.answer(f"ℹ️ «{feed_title}» уже есть в твоих источниках.")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К списку", callback_data=f"find:list:{tenant_id}")],
        ]
    )
    await callback.message.answer("Что дальше?", reply_markup=kb)
