from fastapi import status
from fastapi.testclient import TestClient

from app.main import create_app


def test_livez():
    with TestClient(create_app()) as client:
        response = client.get("/livez")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_readyz_with_database(pg_database):
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_readyz_without_database(monkeypatch):
    monkeypatch.setenv("PGPORT", "1")
    from app.core.config.settings import get_settings
    from app.core.database import Database

    get_settings.cache_clear()
    Database.reset_instance()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/readyz")
    finally:
        Database.reset_instance()
        get_settings.cache_clear()
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
