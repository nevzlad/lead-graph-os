from .billing import router as billing_router
from .setup import router as setup_router
from .template import router as template_router

__all__ = ["billing_router", "setup_router", "template_router"]
