import json

import httpx
import pytest

from app.clients.gbfs import GbfsClient
from app.core.config.settings import Settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.clients.gbfs.time.sleep", lambda _: None)
    client = GbfsClient(Settings(_env_file=None, http_max_tries=3))
    client._client.close()
    yield client
    client.close()


def transport(client, handler):
    client._client = httpx.Client(transport=httpx.MockTransport(handler))


def test_retries_transient_http_and_connection_errors(client):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        if len(calls) == 2:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"data": {}}, headers={"ETag": "new"})

    transport(client, respond)
    result = client.fetch_json("https://example.test/feed", "old")
    assert len(calls) == 3
    assert all(request.headers["If-None-Match"] == "old" for request in calls)
    assert (result.status, result.etag, result.error) == (200, "new", None)


def test_network_failure_does_not_keep_previous_http_status(client):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        raise httpx.ReadTimeout("timeout", request=request)

    transport(client, respond)
    result = client.fetch_json("https://example.test/feed")
    assert len(calls) == 3
    assert result.status is None
    assert "ReadTimeout" in result.error


@pytest.mark.parametrize("body", [[], None, 42, "text"])
def test_rejects_non_object_json(client, body):
    transport(client, lambda _: httpx.Response(200, content=json.dumps(body)))
    result = client.fetch_json("https://example.test/feed")
    assert result.error and result.body is None


def test_not_modified_and_permanent_error(client):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(304 if len(calls) == 1 else 404)

    transport(client, respond)
    result = client.fetch_json("https://example.test/feed", "old")
    assert (result.status, result.etag, result.error) == (304, "old", None)
    assert client.fetch_json("https://example.test/missing").error == "HTTP 404"
    assert len(calls) == 2
