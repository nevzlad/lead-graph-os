import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MODE", "commercial")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CEREBRAS_API_KEY", "test_cerebras_key")
os.environ.setdefault("HF_API_KEY", "test_hf_key")
os.environ.setdefault("TG_BOT_TOKEN", "test_tg_token")
os.environ.setdefault("ONBOARDING_BOT_TOKEN", "test_onboarding_token")
