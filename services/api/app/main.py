from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.admission import AdmissionMiddleware
from app.api.auth_limits import AuthLimitsMiddleware
from app.api.routes import accounts, auth, dashboard, health
from app.core.config.settings import get_settings
from app.core.database import get_database
from app.services.dashboard import ReadCapacityError, ReadService
from app.services.stream import StreamService
from app.web import routes as web
from app.web.security import BrowserSecurityMiddleware


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
    app.add_middleware(AuthLimitsMiddleware)
    app.add_middleware(BrowserSecurityMiddleware)
    app.mount("/static", StaticFiles(directory=Path(web.__file__).parent / "static"), name="static")
    app.include_router(web.router)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(health.router)
    app.include_router(dashboard.router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if web.is_browser(request):
            return web.error_page(request, 422, "Проверьте параметры запроса и формат дат")
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if web.is_browser(request):
            return web.error_page(request, exc.status_code, exc.detail, exc.headers)
        return JSONResponse({"detail": exc.detail}, exc.status_code, headers=exc.headers)

    @app.exception_handler(ReadCapacityError)
    async def capacity_error(request: Request, exc: ReadCapacityError):
        if web.is_browser(request):
            return web.error_page(request, 503, "overloaded", {"Retry-After": "3"})
        return JSONResponse({"detail": "overloaded"}, status_code=503, headers={"Retry-After": "3"})

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        return JSONResponse({"detail": "conflict"}, status_code=409)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        if web.is_browser(request):
            return web.error_page(request, 503, "database_unavailable", {"Retry-After": "3"})
        return JSONResponse(
            {"detail": "database_unavailable"}, status_code=503, headers={"Retry-After": "3"}
        )

    @app.exception_handler(LookupError)
    async def not_found(request: Request, exc: LookupError):
        if web.is_browser(request):
            return web.error_page(request, 404, str(exc))
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_query(request: Request, exc: ValueError):
        if web.is_browser(request):
            return web.error_page(request, 422, str(exc))
        return JSONResponse({"detail": str(exc)}, status_code=422)

    return app


app = create_app()
