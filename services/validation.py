import logging
import re
from difflib import SequenceMatcher

from sqlalchemy import select

from models import Post
from utils.db import async_session_factory

logger = logging.getLogger(__name__)

MIN_CONTENT_LENGTH = 50
MAX_CONTENT_LENGTH = 4000
MIN_TITLE_LENGTH = 5
SIMILARITY_THRESHOLD = 0.6
REWRITE_MIN_DIFFERENCE = 0.3

BANNED_PATTERNS = [
    re.compile(r"http\S+\.(ru|com|org)\b", re.IGNORECASE),
    re.compile(r"\bкупить\b.*\bдешево\b", re.IGNORECASE),
    re.compile(r"\b(?:реклама|спонсор|промо)\b", re.IGNORECASE),
]


async def validate_post(
    tenant_id: str,
    title: str,
    content: str | None,
    original_content: str | None = None,
    skip_db_checks: bool = False,
) -> dict:
    issues = []
    warnings = []

    # Title checks
    title_issues = _check_title(title)
    issues.extend(title_issues)

    # Content checks
    if content:
        content_issues = _check_content(content)
        issues.extend([i for i in content_issues if i.get("severity") == "error"])
        warnings.extend([i for i in content_issues if i.get("severity") == "warning"])

    # Banned patterns
    if content:
        banned = _check_banned_patterns(content)
        warnings.extend(banned)

    # Duplicate check (DB)
    if not skip_db_checks and content:
        dups = await _check_duplicates(tenant_id, title, content)
        issues.extend(dups)

    # Rewrite quality check
    if content and original_content:
        rewrite_issues = _check_rewrite_quality(content, original_content)
        warnings.extend(rewrite_issues)

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }


def _check_title(title: str) -> list[dict]:
    issues = []
    if not title or not title.strip():
        issues.append({"field": "title", "severity": "error", "code": "empty", "message": "Заголовок пустой"})
    elif len(title.strip()) < MIN_TITLE_LENGTH:
        issues.append({"field": "title", "severity": "error", "code": "too_short", "message": f"Заголовок короче {MIN_TITLE_LENGTH} символов"})
    return issues


def _check_content(content: str) -> list[dict]:
    issues = []
    stripped = content.strip()

    if len(stripped) < MIN_CONTENT_LENGTH:
        issues.append({
            "field": "content",
            "severity": "error",
            "code": "too_short",
            "message": f"Контент короче {MIN_CONTENT_LENGTH} символов (сейчас {len(stripped)})",
            "current_length": len(stripped),
        })

    if len(stripped) > MAX_CONTENT_LENGTH:
        issues.append({
            "field": "content",
            "severity": "error",
            "code": "too_long",
            "message": f"Контент длиннее {MAX_CONTENT_LENGTH} символов (сейчас {len(stripped)})",
            "current_length": len(stripped),
        })

    # Check for at least some structure (paragraphs)
    paragraphs = [p.strip() for p in stripped.split("\n\n") if p.strip()]
    if len(paragraphs) == 0:
        issues.append({
            "field": "content",
            "severity": "warning",
            "code": "no_structure",
            "message": "Контент не содержит абзацев",
        })

    # Check for excessive HTML
    html_ratio = len(re.findall(r"<[^>]+>", stripped)) / max(len(stripped), 1)
    if html_ratio > 0.3:
        issues.append({
            "field": "content",
            "severity": "warning",
            "code": "excessive_html",
            "message": "Слишком много HTML-тегов",
        })

    return issues


def _check_banned_patterns(content: str) -> list[dict]:
    warnings = []
    for pattern in BANNED_PATTERNS:
        if pattern.search(content):
            warnings.append({
                "field": "content",
                "severity": "warning",
                "code": "banned_pattern",
                "message": f"Обнаружен подозрительный паттерн: {pattern.pattern[:50]}",
            })
    return warnings


async def _check_duplicates(tenant_id: str, title: str, content: str) -> list[dict]:
    issues = []
    async with async_session_factory() as session:
        existing = await session.execute(
            select(Post).where(
                Post.tenant_id == tenant_id,
                Post.status.in_(["raw", "rewritten", "rewritten_fallback", "draft", "published", "scheduled"]),
            )
        )
        for post in existing.scalars().all():
            title_sim = SequenceMatcher(None, title.lower(), (post.title or "").lower()).ratio()
            if title_sim > SIMILARITY_THRESHOLD:
                issues.append({
                    "field": "title",
                    "severity": "error",
                    "code": "duplicate_title",
                    "message": f"Похожий заголовок уже существует (схожесть {title_sim:.0%})",
                    "existing_post_id": post.id,
                    "similarity": title_sim,
                })

            if post.content and content:
                content_sim = SequenceMatcher(None, content.lower()[:500], post.content.lower()[:500]).ratio()
                if content_sim > SIMILARITY_THRESHOLD:
                    issues.append({
                        "field": "content",
                        "severity": "error",
                        "code": "duplicate_content",
                        "message": f"Похожий контент уже существует (схожесть {content_sim:.0%})",
                        "existing_post_id": post.id,
                        "similarity": content_sim,
                    })
                    break

    return issues


def _check_rewrite_quality(content: str, original: str) -> list[dict]:
    warnings = []
    if not original:
        return warnings

    sim = SequenceMatcher(None, content.lower()[:1000], original.lower()[:1000]).ratio()
    if sim > (1 - REWRITE_MIN_DIFFERENCE):
        warnings.append({
            "field": "content",
            "severity": "warning",
            "code": "insufficient_rewrite",
            "message": f"Контент слишком похож на оригинал (схожесть {sim:.0%}). Нейросеть не доработала текст.",
            "similarity": sim,
        })

    # Check if attribution was added
    if "источник" not in content.lower() and "http" not in content:
        warnings.append({
            "field": "content",
            "severity": "warning",
            "code": "missing_attribution",
            "message": "Отсутствует ссылка на источник",
        })

    return warnings


def format_validation_result(result: dict) -> str:
    parts = []
    if result["passed"]:
        parts.append("✅ Контент прошёл проверку")
    else:
        parts.append("❌ Обнаружены проблемы:")

    for iss in result["issues"]:
        icon = "❌" if iss.get("severity") == "error" else "⚠️"
        parts.append(f"{icon} {iss['message']}")

    if result["warnings"]:
        if not result["issues"]:
            parts.append("⚠️ Предупреждения:")
        for w in result["warnings"]:
            parts.append(f"  ⚠️ {w['message']}")

    return "\n".join(parts)


async def auto_fix_content(content: str, vresult: dict) -> tuple[str, list[str]]:
    fixes = []

    for iss in vresult["issues"]:
        if iss["code"] == "too_long":
            content = content[:MAX_CONTENT_LENGTH]
            fixes.append("truncated_to_max_length")
        elif iss["code"] == "excessive_html":
            from services.telegram import strip_html
            content = strip_html(content)
            fixes.append("stripped_excessive_html")

    for w in vresult["warnings"]:
        if w["code"] == "missing_attribution":
            content += "\n\n(с) Источник"
            fixes.append("added_attribution")
            break

    return content, fixes
