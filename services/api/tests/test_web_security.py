import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.web.security import BrowserSecurityMiddleware


@pytest.mark.parametrize("path", ["/login", "/register", "/ui/operator"])
def test_browser_headers_allow_map_referer_without_cross_origin_path(path):
    async def page(request):
        return PlainTextResponse("page")

    app = Starlette(routes=[Route("/{path:path}", page)])
    app.add_middleware(BrowserSecurityMiddleware)
    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200
    # OSM requires Referer; the policy sends the origin without paths or queries.
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
