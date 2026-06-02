import os

from dotenv import load_dotenv

load_dotenv()

_mode = os.getenv("MODE", "commercial")
if _mode not in ("commercial", "internal"):
    raise EnvironmentError("MODE должен быть 'commercial' или 'internal'")

FREE_LLM_PROVIDERS = [
    {
        "name": "huggingface",
        "type": "hf",
        "base_url": None,
        "model": os.getenv("LLM_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
        "key_env": "HF_API_KEY",
    },
    {
        "name": "groq",
        "type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "model": os.getenv("GROQ_MODEL", "meta-llama/llama-3.1-8b-instant"),
        "key_env": "GROQ_API_KEY",
    },
    {
        "name": "openrouter",
        "type": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "model": os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free"),
        "key_env": "OPENROUTER_API_KEY",
    },
    {
        "name": "gemini",
        "type": "gemini",
        "base_url": None,
        "model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        "key_env": "GEMINI_API_KEY",
    },
    {
        "name": "deepseek",
        "type": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "key_env": "DEEPSEEK_API_KEY",
    },
    {
        "name": "cerebras",
        "type": "openai_compat",
        "base_url": "https://api.cerebras.ai/v1",
        "model": os.getenv("CEREBRAS_MODEL", "cerebras/Llama-3.3-70B"),
        "key_env": "CEREBRAS_API_KEY",
    },
    {
        "name": "cohere",
        "type": "cohere",
        "base_url": None,
        "model": os.getenv("COHERE_MODEL", "command-r"),
        "key_env": "COHERE_API_KEY",
    },
]


def _build_providers():
    providers = []
    for cfg in FREE_LLM_PROVIDERS:
        key = os.getenv(cfg["key_env"], "")
        if not key:
            continue
        entry = {
            "name": cfg["name"],
            "type": cfg["type"],
            "key": key,
            "model": cfg["model"],
        }
        if cfg["base_url"]:
            entry["base_url"] = cfg["base_url"]
        providers.append(entry)
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

    MAX_RETRIES_PER_PROVIDER: int = int(os.getenv("MAX_RETRIES_PER_PROVIDER", "2"))
    SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.45"))
    TARGET_LANGUAGE: str = os.getenv("TARGET_LANGUAGE", "ru")

    PROVIDERS: list[dict] = _build_providers()

    INTERNAL_API_TOKEN: str = ""
    BIGDATA_DB_URL: str = ""

    if MODE == "internal":
        INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
        BIGDATA_DB_URL = os.getenv("BIGDATA_DB_URL", "")
        if not INTERNAL_API_TOKEN:
            raise EnvironmentError("INTERNAL_API_TOKEN обязателен в MODE=internal")

    if not DATABASE_URL:
        raise EnvironmentError("DATABASE_URL не установлена")
    if not REDIS_URL:
        raise EnvironmentError("REDIS_URL не установлена")
    if not TG_BOT_TOKEN:
        raise EnvironmentError("TG_BOT_TOKEN не установлен")
    if not ONBOARDING_BOT_TOKEN:
        raise EnvironmentError("ONBOARDING_BOT_TOKEN не установлен")

    if "asyncpg" not in DATABASE_URL:
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
        elif DATABASE_URL.startswith("postgresql://"):
            DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


settings = Settings()
