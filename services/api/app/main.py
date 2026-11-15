from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.admission import AdmissionMiddleware
from app.api.routes import dashboard, health
from app.core.config.settings import get_settings
from app.core.database import get_database
from app.services.dashboard import ReadCapacityError, ReadService
from app.services.stream import StreamService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.reads = ReadService(get_database(), get_settings())
    app.state.stream = StreamService(app.state.reads, get_settings())
    app.state.stream.start()
    try:
        yield
    finally:
        await app.state.stream.close()
        await app.state.reads.close()
        get_database().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_title, lifespan=lifespan)
    app.add_middleware(AdmissionMiddleware)
    app.include_router(health.router)
    app.include_router(dashboard.router)

    @app.exception_handler(ReadCapacityError)
    async def capacity_error(request: Request, exc: ReadCapacityError):
        return JSONResponse({"detail": "overloaded"}, status_code=503, headers={"Retry-After": "3"})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse(
            {"detail": "database_unavailable"}, status_code=503, headers={"Retry-After": "3"}
        )

    @app.exception_handler(LookupError)
    async def not_found(request: Request, exc: LookupError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_query(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    return app


app = create_app()
