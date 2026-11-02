"""Клиент фидов GBFS: таймаут, повторы с экспоненциальным backoff, условные запросы по ETag."""

import random
import time

import httpx
from fastapi import status

from app.core.config.settings import Settings
from app.enums import TransientHttpStatus
from app.schemas.ingest import FetchResult


class GbfsClient:
    def __init__(self, settings: Settings) -> None:
        self._max_tries = settings.http_max_tries
        self._client = httpx.Client(
            timeout=settings.http_timeout_s,
            headers={"User-Agent": settings.http_user_agent},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def fetch_json(self, url: str, etag: str | None = None) -> FetchResult:
        headers = {"If-None-Match": etag} if etag else {}
        result = FetchResult(etag=etag)
        for attempt in range(1, self._max_tries + 1):
            try:
                response = self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                result.error = f"{type(exc).__name__}: {exc}"
            else:
                result.status = response.status_code
                if response.status_code == status.HTTP_304_NOT_MODIFIED:
                    result.error = None
                    return result
                if response.status_code == status.HTTP_200_OK:
                    return self._parse(result, response)
                result.error = f"HTTP {response.status_code}"
                if response.status_code not in TransientHttpStatus:
                    return result
            if attempt < self._max_tries:
                time.sleep(2**attempt + random.random())  # noqa: S311
        return result

    @staticmethod
    def _parse(result: FetchResult, response: httpx.Response) -> FetchResult:
        try:
            result.body = response.json()
        except ValueError as exc:
            result.error = f"некорректный JSON: {exc}"
            return result
        result.etag = response.headers.get("ETag")
        result.error = None
        return result
