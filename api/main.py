import logging

from fastapi import FastAPI

from api.routes import public
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lead-Graph OS API", version="1.0.0")

app.include_router(public.router)

if settings.MODE == "internal":
    try:
        from api.routes import internal

        app.include_router(internal.router)
        logger.info("Internal mode: private routes and Big Data module loaded.")
    except ImportError as e:
        logger.error(f"Failed to load internal module: {e}")
        raise RuntimeError("Internal module required in MODE=internal") from e
else:
    logger.info("Commercial mode: internal routes and Big Data module DISABLED.")


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
