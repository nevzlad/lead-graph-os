from config import settings

if settings.MODE != "internal":
    raise ImportError(
        "api.bigdata is only available in MODE=internal. Commercial deployments must not import this package."
    )

from .analyzer import BigDataAnalyzer

__all__ = ["BigDataAnalyzer"]
