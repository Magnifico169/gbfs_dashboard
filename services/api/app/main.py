from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health
from app.core.config.settings import get_settings
from app.core.database import get_database


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    get_database().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_title, lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
