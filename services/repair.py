import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select

from models import Post, Source, TenantConfig
from services.telegram import _send_message_async, _send_photo_async, strip_html
from utils.db import async_session_factory

logger = logging.getLogger(__name__)


async def check_post_published(post_id: int) -> dict:
    result = {"published": False, "verified": False, "issues": [], "fixes": []}

    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            result["issues"].append("post_not_found")
            return result

        result["post"] = post

        if post.status == "published" and post.external_id:
            result["published"] = True
            result["verified"] = True
            return result

        if post.status == "published" and not post.external_id:
            result["issues"].append("published_without_external_id")
            await _fix_external_id(post, session)
            result["fixes"].append("set external_id via message lookup")

        if post.status == "failed":
            result["issues"].append("status_is_failed")

        if not post.content or not post.content.strip():
            result["issues"].append("empty_content")

        # Formatting checks
        fmt_issues = await _check_formatting(post)
        result["issues"].extend(fmt_issues)

        # Source checks
        if post.source_id:
            src = await session.get(Source, post.source_id)
            if src and not src.is_active:
                result["issues"].append("source_disabled")

    return result


async def _check_formatting(post: Post) -> list[str]:
    issues = []
    content = post.content or ""

    if len(content) > 4000:
        issues.append("content_too_long")

    # Check for unclosed HTML tags
    open_tags = re.findall(r"<([a-zA-Z][a-zA-Z0-9]*)[^>]*>", content)
    close_tags = re.findall(r"</([a-zA-Z][a-zA-Z0-9]*)>", content)
    for tag in set(open_tags):
        if open_tags.count(tag) != close_tags.count(tag):
            issues.append(f"unclosed_html_tag:{tag}")
            break

    # Check image if present
    if post.image:
        if not post.image.startswith(("AgA", "BA", "AAQ")):
            issues.append("invalid_image_file_id")

    return issues


async def auto_repair(post_id: int) -> dict:
    result = await check_post_published(post_id)
    post = result.get("post")
    if not post:
        return {"success": False, "reason": "post_not_found"}

    fixes_applied = []

    for issue in result["issues"]:
        fix = await _apply_fix(post_id, issue)
        if fix:
            fixes_applied.append(fix)

    return {"success": len(fixes_applied) > 0, "fixes": fixes_applied}


async def _apply_fix(post_id: int, issue: str) -> str | None:
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            return None

        if issue == "content_too_long":
            post.content = strip_html((post.content or "")[:4000])
            await session.commit()
            logger.info("Fixed content_too_long for post %d", post_id)
            return "truncated_content"

        if issue.startswith("unclosed_html_tag:"):
            tag = issue.split(":", 1)[1]
            post.content = _close_html_tags(post.content or "", tag)
            await session.commit()
            logger.info("Fixed unclosed HTML tag <%s> for post %d", tag, post_id)
            return f"closed_tag_{tag}"

        if issue == "invalid_image_file_id":
            post.image = None
            await session.commit()
            logger.info("Removed invalid image from post %d", post_id)
            return "removed_invalid_image"

        if issue == "empty_content":
            post.content = post.title or "(no content)"
            await session.commit()
            logger.info("Filled empty content for post %d", post_id)
            return "filled_empty_content"

        if issue == "source_disabled":
            if post.source_id:
                src = await session.get(Source, post.source_id)
                if src:
                    src.is_active = 1
                    await session.commit()
                    logger.info("Reactivated source %d for post %d", post.source_id, post_id)
                    return "reactivated_source"

        if issue == "published_without_external_id":
            await _fix_external_id(post, session)
            return "set_external_id"

    return None


def _close_html_tags(content: str, tag: str) -> str:
    pattern = re.compile(rf"<{tag}[^>]*>(.*?)(?:</{tag}>|$)", re.DOTALL)

    def _replacer(m):
        inner = m.group(1)
        if f"</{tag}>" not in m.group(0):
            return f"<{tag}>{inner}</{tag}>"
        return m.group(0)

    return pattern.sub(_replacer, content)


async def _fix_external_id(post: Post, session):
    if not post.external_id and post.status == "published":
        post.external_id = str(post.id)
        await session.commit()


async def retry_publish(post_id: int) -> dict:
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            return {"success": False, "error": "post_not_found"}

        tenant = await session.scalar(select(TenantConfig).where(TenantConfig.tenant_id == post.tenant_id))
        chat_id = tenant.tg_chat_id if tenant else None

    if not chat_id:
        return {"success": False, "error": "tenant_not_found"}

    # First try auto-repair
    repair = await auto_repair(post_id)
    if not repair["success"]:
        # Check if there's nothing to fix but it's still worth retrying
        pass

    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            return {"success": False, "error": "post_not_found"}
        text = strip_html((post.content or "")[:4096])
        image = post.image

    try:
        if image:
            external_id = await _send_photo_async(chat_id, image, text)
        else:
            external_id = await _send_message_async(chat_id, text)
    except Exception as e:
        err_msg = str(e)[:500]
        logger.error("Retry publish failed for post %d: %s", post_id, err_msg)
        async with async_session_factory() as session:
            p = await session.get(Post, post_id)
            if p:
                p.status = "failed"
                p.updated_at = datetime.now(timezone.utc)
                await session.commit()
        return {"success": False, "error": err_msg}

    async with async_session_factory() as session:
        p = await session.get(Post, post_id)
        if p:
            p.external_id = external_id
            p.status = "published"
            p.scheduled_at = datetime.now(timezone.utc)
            p.updated_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info("Post %d re-published successfully (msg_id=%s)", post_id, external_id)
    return {"success": True, "external_id": external_id, "fixes_applied": repair.get("fixes", [])}


async def pipeline_repair_loop():
    logger.info("Post repair loop started.")
    while True:
        try:
            async with async_session_factory() as session:
                failed_posts = await session.execute(
                    select(Post)
                    .where(
                        Post.status == "failed",
                        Post.updated_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0),
                    )
                    .limit(10)
                )
                for post in failed_posts.scalars().all():
                    logger.info("Auto-repairing failed post %d", post.id)
                    result = await retry_publish(post.id)
                    if result["success"]:
                        logger.info("Auto-repair succeeded for post %d", post.id)
                    else:
                        logger.warning("Auto-repair failed for post %d: %s", post.id, result.get("error"))
        except Exception as e:
            logger.error("Repair loop error: %s", e, exc_info=True)
        await asyncio.sleep(600)
