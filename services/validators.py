import logging
import re
from collections import Counter

from config import settings

logger = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<(/)?([a-zA-Z][a-zA-Z0-9]*)(\s[^>]*)?>")


def check_length(content: str, min_len: int | None = None) -> tuple[bool, str | None]:
    if min_len is None:
        min_len = settings.LLM_MIN_CONTENT_LENGTH
    stripped = (content or "").strip()
    if len(stripped) < min_len:
        return False, f"too_short:{len(stripped)}"
    return True, None


def check_language(content: str, target_lang: str | None = None) -> tuple[bool, str | None]:
    sample = (content or "")[:settings.LLM_LANG_CHECK_LENGTH]
    if len(sample.strip()) < 100:
        return True, None
    try:
        from services.language import detect_language
        detected = detect_language(sample)
        if target_lang is None:
            target_lang = settings.LLM_TARGET_LANG
        if detected != target_lang:
            return False, f"wrong_language:{detected}"
        return True, None
    except Exception as e:
        logger.warning("Language check skipped: %s", e)
        return True, None


def check_html_tags(content: str) -> tuple[bool, str | None]:
    if not content:
        return True, None
    tags = HTML_TAG_RE.findall(content)
    open_tags = Counter()
    close_tags = Counter()
    for is_close, tag_name, _ in tags:
        if tag_name in ("br", "img", "input", "hr", "meta", "link"):
            continue
        if is_close:
            close_tags[tag_name] += 1
        else:
            open_tags[tag_name] += 1
    for tag, count in open_tags.items():
        if count != close_tags.get(tag, 0):
            return False, f"unclosed_tag:{tag}"
    return True, None


def _ngrams(text: str, n: int = 3) -> Counter:
    normalized = re.sub(r"<[^>]+>", "", text)
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    if len(normalized) < n:
        return Counter([normalized])
    return Counter(normalized[i:i+n] for i in range(len(normalized) - n + 1))


def check_uniqueness(content: str, original: str, threshold: float | None = None) -> tuple[bool, str | None]:
    if not original or not content:
        return True, None
    if threshold is None:
        threshold = settings.LLM_NGRAM_THRESHOLD
    content_ng = _ngrams(content)
    original_ng = _ngrams(original)
    if not content_ng or not original_ng:
        return True, None
    intersection = content_ng & original_ng
    total = content_ng + original_ng
    if not total:
        return True, None
    overlap = sum(intersection.values()) / sum(total.values())
    if overlap > threshold:
        return False, f"too_similar:{overlap:.3f}"
    return True, None


def validate_all(
    content: str,
    original: str | None = None,
    target_lang: str | None = None,
    min_len: int | None = None,
) -> dict:
    issues = []

    ok, err = check_length(content, min_len)
    if not ok:
        issues.append(err)

    ok, err = check_language(content, target_lang)
    if not ok:
        issues.append(err)

    ok, err = check_html_tags(content)
    if not ok:
        issues.append(err)

    if original:
        ok, err = check_uniqueness(content, original)
        if not ok:
            issues.append(err)

    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
    }
