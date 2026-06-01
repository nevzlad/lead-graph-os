import os

from dotenv import load_dotenv

load_dotenv()

_mode = os.getenv("MODE", "commercial")
if _mode not in ("commercial", "internal"):
    raise EnvironmentError("MODE должен быть 'commercial' или 'internal'")


class Settings:
    MODE: str = _mode
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
    HF_API_KEY: str = os.getenv("HF_API_KEY", "")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "512"))
    RATE_LIMIT_PER_HOUR: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "100"))
    TG_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "")
    PUBLISHER_JITTER_MIN: int = int(os.getenv("PUBLISHER_JITTER_MIN", "60"))
    PUBLISHER_JITTER_MAX: int = int(os.getenv("PUBLISHER_JITTER_MAX", "300"))
    ONBOARDING_BOT_TOKEN: str = os.getenv("ONBOARDING_BOT_TOKEN", "") or os.getenv("TG_BOT_TOKEN", "")
    TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "7"))

    INTERNAL_API_TOKEN: str = ""
    BIGDATA_DB_URL: str = ""

    if MODE == "internal":
        INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
        BIGDATA_DB_URL = os.getenv("BIGDATA_DB_URL", "")
        if not INTERNAL_API_TOKEN:
            raise EnvironmentError("INTERNAL_API_TOKEN обязателен в MODE=internal")

    if not DATABASE_URL:
        raise EnvironmentError("Переменная окружения DATABASE_URL не установлена")
    if not HF_API_KEY:
        raise EnvironmentError("Переменная окружения HF_API_KEY не установлена")
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
