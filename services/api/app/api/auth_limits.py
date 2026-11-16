"""Ограничение перебора паролей и памяти Argon2 на каждой реплике API."""

import time
from collections import OrderedDict

from starlette.responses import JSONResponse


class AuthLimitsMiddleware:
    def __init__(self, app):
        self.app = app
        self.attempts = OrderedDict()
        self.active = 0

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["path"]
            not in ("/api/v1/auth/login", "/login", "/api/v1/auth/register", "/register")
            or scope["method"] != "POST"
        ):
            return await self.app(scope, receive, send)
        now = time.monotonic()
        while self.attempts and next(iter(self.attempts.values()))[0] <= now:
            self.attempts.popitem(last=False)
        ip = (scope.get("client") or ("unknown", 0))[0]
        expires, attempts = self.attempts.get(ip, (now + 60, 0))
        if (
            attempts >= 10
            or self.active >= 4
            or (ip not in self.attempts and len(self.attempts) >= 4096)
        ):
            code = (
                "registration_rate_limited"
                if scope["path"].endswith("register")
                else "login_rate_limited"
            )
            if scope["path"] in ("/login", "/register"):
                from starlette.requests import Request

                from app.web.routes import error_page

                response = error_page(Request(scope), 429, code, {"Retry-After": "60"})
                return await response(scope, receive, send)
            response = JSONResponse({"detail": code}, 429, headers={"Retry-After": "60"})
            return await response(scope, receive, send)
        self.attempts[ip] = (expires, attempts + 1)
        self.active += 1
        try:
            return await self.app(scope, receive, send)
        finally:
            self.active -= 1
