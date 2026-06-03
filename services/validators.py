import logging
import re
from collections import Counter

from config import settings

logger = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<(/)?([a-zA-Z][a-zA-Z0-9]*)(\s[^>]*)?>")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
MARKDOWN_LIST_RE = re.compile(r"^(\s*[-*+]\s|\s*\d+\.\s)", re.MULTILINE)

LANGDETECT_AVAILABLE = False
try:
    from langdetect import DetectorFactory, lang_detect_exception
    from langdetect import detect as langdetect_detect

    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    pass


class ContentValidator:
    def validate(self, original: str, rewritten: str, target_lang: str) -> dict:
        errors = []
        similarity = 0.0
        length = len(rewritten.strip())

        lang_ok, lang_err = self._check_language(rewritten, target_lang)
        if not lang_ok:
            errors.append(lang_err)

        len_ok, len_err = self._check_length(rewritten)
        if not len_ok:
            errors.append(len_err)

        markup_ok, markup_err = self._check_markup(rewritten)
        if not markup_ok:
            errors.append(markup_err)

        if original and rewritten:
            similarity = self._ngram_overlap(original, rewritten)
            if similarity > settings.SIMILARITY_THRESHOLD:
                errors.append(f"too_similar:{similarity:.3f}")

        return {
            "is_valid": len(errors) == 0,
            "errors": errors,
            "similarity": round(similarity, 4),
            "length": length,
        }

    def _check_language(self, text: str, target_lang: str) -> tuple[bool, str | None]:
        sample = (text or "").strip()[:500]
        if len(sample) < 100:
            return True, None
        use_langdetect = LANGDETECT_AVAILABLE and len(sample) >= 100
        if not use_langdetect:
            try:
                from services.language import detect_language

                detected = detect_language(sample)
                if detected != target_lang and target_lang != detected:
                    return False, f"wrong_language:{detected}"
                return True, None
            except Exception:
                return True, None
        try:
            detected = langdetect_detect(sample)
            allowed = {target_lang}
            if target_lang == "ru":
                allowed.add("uk")
            elif target_lang == "en":
                allowed.update({"de", "fr", "es"})
            if detected not in allowed:
                return False, f"wrong_language:{detected}"
            return True, None
        except lang_detect_exception:
            return True, None
        except Exception:
            return True, None

    def _check_length(self, text: str) -> tuple[bool, str | None]:
        stripped = (text or "").strip()
        length = len(stripped)
        if length < 500:
            return False, f"too_short:{length}"
        if length > 4000:
            return False, f"too_long:{length}"
        return True, None

    def _check_markup(self, text: str) -> tuple[bool, str | None]:
        if not text:
            return True, None
        tags = HTML_TAG_RE.findall(text)
        open_tags = Counter()
        close_tags = Counter()
        for is_close, tag_name, _ in tags:
            if tag_name in ("br", "img", "input", "hr", "meta", "link", "source"):
                continue
            if is_close:
                close_tags[tag_name] += 1
            else:
                open_tags[tag_name] += 1
        for tag, count in open_tags.items():
            if count != close_tags.get(tag, 0):
                return False, f"unclosed_tag:{tag}"
        heading_count = len(MARKDOWN_HEADING_RE.findall(text))
        len(MARKDOWN_LIST_RE.findall(text))
        if heading_count > 10:
            return False, f"too_many_headings:{heading_count}"
        return True, None

    def _ngram_overlap(self, original: str, rewritten: str) -> float:
        orig_ng = self._extract_ngrams(original)
        rewr_ng = self._extract_ngrams(rewritten)
        if not orig_ng or not rewr_ng:
            return 0.0
        intersection = orig_ng & rewr_ng
        total = orig_ng + rewr_ng
        if not total:
            return 0.0
        return sum(intersection.values()) / sum(total.values())

    def _extract_ngrams(self, text: str, n: int = 4) -> Counter:
        cleaned = re.sub(r"<[^>]+>", "", text)
        cleaned = re.sub(r"[^\w\s]", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
        if len(cleaned) < n:
            return Counter([cleaned])
        return Counter(cleaned[i : i + n] for i in range(len(cleaned) - n + 1))


_validator = ContentValidator()


def check_length(content: str, min_len: int | None = None) -> tuple[bool, str | None]:
    if min_len is None:
        min_len = settings.LLM_MIN_CONTENT_LENGTH
    stripped = (content or "").strip()
    if len(stripped) < min_len:
        return False, f"too_short:{len(stripped)}"
    return True, None


def check_language(content: str, target_lang: str | None = None) -> tuple[bool, str | None]:
    if target_lang is None:
        target_lang = settings.LLM_TARGET_LANG
    return _validator._check_language(content, target_lang)


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
    return Counter(normalized[i : i + n] for i in range(len(normalized) - n + 1))


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


def check_post_for_queue(content: str | None, target_lang: str, status: str = "raw") -> tuple[bool, str | None]:
    if not content or not content.strip():
        return False, "empty"
    stripped = content.strip()
    if len(stripped) > 4000:
        return False, f"too_long:{len(stripped)}"
    if status in ("raw", "draft"):
        return True, None
    if len(stripped) < settings.LLM_MIN_CONTENT_LENGTH:
        return False, f"too_short:{len(stripped)}"
    if status in ("rewritten", "rewritten_fallback", "scheduled"):
        ok, err = _validator._check_language(stripped, target_lang)
        if not ok:
            return False, err
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
