"""Защита браузерных форм и request_id для диагностики."""

import uuid
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


class BrowserSecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        headers = dict(scope.get("headers", []))
        path = scope["path"]
        browser = path.startswith("/ui/") or path in ("/", "/login", "/register")
        if browser and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            origin = headers.get(b"origin", b"").decode()
            host = headers.get(b"host", b"").decode()
            if headers.get(b"sec-fetch-site") == b"cross-site" or (
                origin
                and (urlsplit(origin).netloc != host or urlsplit(origin).scheme != scope["scheme"])
            ):
                response = JSONResponse(
                    {"detail": "cross_origin_forbidden", "request_id": request_id}, 403
                )
                return await response(scope, receive, send)

        async def secured(message):
            if message["type"] == "http.response.start":
                extra = [
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                ]
                if browser:
                    extra += [
                        (b"cache-control", b"no-store"),
                        (b"x-frame-options", b"DENY"),
                        # OSM tiles require Referer; disclose only the origin cross-site.
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, secured)
