"""Ограничение ожидающих HTTP-запросов; SSE имеет отдельный лимит соединений."""

from starlette.responses import JSONResponse


class AdmissionMiddleware:
    def __init__(self, app, limit=128):
        self.app = app
        self.limit = limit
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].endswith("/stream"):
            return await self.app(scope, receive, send)
        if self.active >= self.limit:
            response = JSONResponse(
                {"detail": "overloaded"}, status_code=503, headers={"Retry-After": "3"}
            )
            return await response(scope, receive, send)
        self.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.active -= 1
