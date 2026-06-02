import logging
import re

logger = logging.getLogger(__name__)

LANG_NAMES = {
    "ru": "Русский",
    "en": "English",
    "uk": "Українська",
    "kk": "Қазақша",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "tr": "Türkçe",
    "pl": "Polski",
}

CYRILLIC_RANGES = [
    (0x0400, 0x04FF),
    (0x0500, 0x052F),
    (0x2DE0, 0x2DFF),
    (0xA640, 0xA69F),
]
LATIN_RANGES = [
    (0x0041, 0x007A),
    (0x00C0, 0x024F),
]

# Common words per language for disambiguation
LANG_SIGNATURES = {
    "ru": {"что", "это", "как", "все", "она", "они", "было", "его", "еще", "уже", "также", "может", "которые", "после"},
    "uk": {"що", "це", "як", "вона", "вони", "було", "його", "ще", "вже", "також", "може", "які", "після", "але"},
    "en": {"the", "and", "that", "have", "this", "with", "from", "they", "will", "what", "which", "their", "about"},
    "de": {"der", "die", "das", "und", "mit", "von", "nicht", "sich", "auch", "werden", "dass", "diese"},
    "fr": {"les", "des", "dans", "pour", "avec", "cette", "sont", "faire", "plus", "leur"},
}

TRANSLATION_PROMPT_TEMPLATE = (
    "Translate the following text to {target_lang}. "
    "Preserve all HTML tags exactly as they are. "
    "Return ONLY the translated text, no explanations, no JSON.\n\n{text}"
)


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "ru"

    cyrillic_count = 0
    latin_count = 0
    total_letters = 0

    for ch in text:
        code = ord(ch)
        in_cyrillic = any(lo <= code <= hi for lo, hi in CYRILLIC_RANGES)
        in_latin = any(lo <= code <= hi for lo, hi in LATIN_RANGES)

        if in_cyrillic:
            cyrillic_count += 1
            total_letters += 1
        elif in_latin:
            latin_count += 1
            total_letters += 1

    if total_letters == 0:
        return "ru"

    cyrillic_ratio = cyrillic_count / total_letters
    latin_ratio = latin_count / total_letters

    if cyrillic_ratio > 0.7:
        return _disambiguate_cyrillic(text.lower())
    elif latin_ratio > 0.7:
        return _disambiguate_latin(text.lower())
    else:
        return _disambiguate_cyrillic(text.lower()) if cyrillic_ratio >= latin_ratio else "en"


def _disambiguate_cyrillic(text: str) -> str:
    words = set(re.findall(r"[а-яіїєґӑӓӛӗӧӫӯӱӳ'']+", text, re.IGNORECASE))
    ru_score = sum(1 for w in words if w in LANG_SIGNATURES["ru"])
    uk_score = sum(1 for w in words if w in LANG_SIGNATURES["uk"])
    if uk_score > ru_score:
        return "uk"
    return "ru"


def _disambiguate_latin(text: str) -> str:
    words = set(re.findall(r"[a-zäöüßéèêëàâîôûùçœæ]+", text, re.IGNORECASE))
    scores = {}
    for lang, sig in LANG_SIGNATURES.items():
        if lang in ("ru", "uk"):
            continue
        scores[lang] = sum(1 for w in words if w in sig)
    if scores:
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best
    return "en"


def needs_translation(detected: str, target: str) -> bool:
    if detected == target:
        return False
    if detected == "ru" and target == "uk":
        return True
    if detected == "uk" and target == "ru":
        return True
    if detected == "en" and target in ("ru", "uk", "de", "fr", "es", "tr", "pl"):
        return True
    return detected != target


def build_translation_prompt(text: str, target_lang: str) -> str:
    lang_label = LANG_NAMES.get(target_lang, target_lang)
    return TRANSLATION_PROMPT_TEMPLATE.format(target_lang=lang_label, text=text)
