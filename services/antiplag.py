import difflib
import logging
import re

logger = logging.getLogger(__name__)


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a = _normalize(a)
    b = _normalize(b)
    if len(a) < 20 or len(b) < 20:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _normalize(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def ensure_unique(content: str, original: str, threshold: float = 0.5) -> tuple[str, bool]:
    sim = similarity(content, original)
    if sim >= threshold:
        logger.info("Similarity %.2f >= %.2f — adding source disclaimer", sim, threshold)
        content = content + "\n\n(с) Источник"
        return content, False
    return content, True


def add_attribution(content: str, link: str | None) -> str:
    if not link:
        return content
    if "источник" in content.lower() or link in content:
        return content
    return f"{content}\n\n<a href='{link}'>Источник</a>"
