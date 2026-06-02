import os

from dotenv import load_dotenv

load_dotenv()

_mode = os.getenv("MODE", "commercial")
if _mode not in ("commercial", "internal"):
    raise EnvironmentError("MODE должен быть 'commercial' или 'internal'")


def _build_providers():
    providers = []

    hf_key = os.getenv("HF_API_KEY", "")
    if hf_key:
        providers.append({
            "name": "huggingface",
            "type": "hf",
            "key": hf_key,
            "model": os.getenv("LLM_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
        })

    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        providers.append({
            "name": "groq",
            "type": "openai_compat",
            "key": groq_key,
            "model": os.getenv("GROQ_MODEL", "meta-llama/llama-3.1-8b-instant"),
            "base_url": "https://api.groq.com/openai/v1",
        })

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    if openrouter_key:
        providers.append({
            "name": "openrouter",
            "type": "openai_compat",
            "key": openrouter_key,
            "model": os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free"),
            "base_url": "https://openrouter.ai/api/v1",
        })

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        providers.append({
            "name": "gemini",
            "type": "gemini",
            "key": gemini_key,
            "model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        })

    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    if deepseek_key:
        providers.append({
            "name": "deepseek",
            "type": "openai_compat",
            "key": deepseek_key,
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "base_url": "https://api.deepseek.com",
        })

    cohere_key = os.getenv("COHERE_API_KEY", "")
    if cohere_key:
        providers.append({
            "name": "cohere",
            "type": "cohere",
            "key": cohere_key,
            "model": os.getenv("COHERE_MODEL", "command-r"),
        })

    return providers


class Settings:
    MODE: str = _mode
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

    RATE_LIMIT_PER_HOUR: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "100"))
    TG_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "")
    PUBLISHER_JITTER_MIN: int = int(os.getenv("PUBLISHER_JITTER_MIN", "60"))
    PUBLISHER_JITTER_MAX: int = int(os.getenv("PUBLISHER_JITTER_MAX", "300"))
    ONBOARDING_BOT_TOKEN: str = os.getenv("ONBOARDING_BOT_TOKEN", "") or os.getenv("TG_BOT_TOKEN", "")
    TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "7"))

    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "1024"))
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))
    LLM_CB_ERROR_THRESHOLD: int = int(os.getenv("LLM_CB_ERROR_THRESHOLD", "3"))
    LLM_CB_RECOVERY_SECONDS: int = int(os.getenv("LLM_CB_RECOVERY_SECONDS", "300"))
    LLM_REDIS_TTL: int = int(os.getenv("LLM_REDIS_TTL", "86400"))

    LLM_NGRAM_THRESHOLD: float = float(os.getenv("LLM_NGRAM_THRESHOLD", "0.45"))
    LLM_MIN_CONTENT_LENGTH: int = int(os.getenv("LLM_MIN_CONTENT_LENGTH", "100"))
    LLM_TARGET_LANG: str = os.getenv("LLM_TARGET_LANG", "ru")
    LLM_LANG_CHECK_LENGTH: int = int(os.getenv("LLM_LANG_CHECK_LENGTH", "500"))

    PROVIDERS: list[dict] = _build_providers()

    INTERNAL_API_TOKEN: str = ""
    BIGDATA_DB_URL: str = ""

    if MODE == "internal":
        INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
        BIGDATA_DB_URL = os.getenv("BIGDATA_DB_URL", "")
        if not INTERNAL_API_TOKEN:
            raise EnvironmentError("INTERNAL_API_TOKEN обязателен в MODE=internal")

    if not DATABASE_URL:
        raise EnvironmentError("Переменная окружения DATABASE_URL не установлена")
    if not TG_BOT_TOKEN:
        raise EnvironmentError("Переменная окружения TG_BOT_TOKEN не установлена")
    if not ONBOARDING_BOT_TOKEN:
        raise EnvironmentError("Переменная окружения ONBOARDING_BOT_TOKEN не установлена")

    if "asyncpg" not in DATABASE_URL:
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
        elif DATABASE_URL.startswith("postgresql://"):
            DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


settings = Settings()
