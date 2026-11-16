"""Ограничение ожидающих HTTP-запросов; SSE имеет отдельный лимит соединений."""

from starlette.responses import JSONResponse


class AdmissionMiddleware:
    def __init__(self, app, limit=128):
        self.app = app
        self.limit = limit
        self.active = 0

    async def __call__(self, scope, receive, send):
        original_send = send
        if scope["type"] == "http" and scope["path"].startswith("/api/"):

            async def private_send(message):
                if message["type"] == "http.response.start":
                    headers = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() != b"cache-control"
                    ]
                    message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
                await original_send(message)

            send = private_send
        if scope["type"] != "http" or scope["path"].endswith("/stream"):
            return await self.app(scope, receive, send)
        if self.active >= self.limit:
            if scope["path"].startswith("/ui/") or scope["path"] in ("/", "/login"):
                from starlette.requests import Request

                from app.web.routes import error_page

                response = error_page(Request(scope), 503, "overloaded", {"Retry-After": "3"})
                return await response(scope, receive, send)
            response = JSONResponse(
                {"detail": "overloaded"}, status_code=503, headers={"Retry-After": "3"}
            )
            return await response(scope, receive, send)
        self.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.active -= 1
