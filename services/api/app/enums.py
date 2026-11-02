"""Перечисления приложения — отдельно от таблиц, схем и сервисов."""

from enum import IntEnum, StrEnum

from fastapi import status


class IngestStatus(StrEnum):
    """Итог запуска приёма по фиду (app.ingest_run.status)."""

    OK = "ok"
    NOT_MODIFIED = "not_modified"
    ERROR = "error"


class RejectReason(StrEnum):
    """Причина отбраковки снимка станции (core.station_quarantine.reason).

    Причина «пропущено обязательное поле» содержит имя поля и собирается в validation.
    """

    MISSING_STATION_ID = "missing_station_id"
    DUPLICATE_STATION = "duplicate_station"
    NEGATIVE_COUNTER = "negative_counter"
    EXCEEDS_CAPACITY = "exceeds_capacity"
    EBIKES_EXCEED_BIKES = "ebikes_exceed_bikes"
    REPORTED_IN_FUTURE = "reported_in_future"


class HealthState(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


class TransientHttpStatus(IntEnum):
    """Коды ответа фида, после которых запрос повторяется."""

    TOO_MANY_REQUESTS = status.HTTP_429_TOO_MANY_REQUESTS
    INTERNAL_SERVER_ERROR = status.HTTP_500_INTERNAL_SERVER_ERROR
    BAD_GATEWAY = status.HTTP_502_BAD_GATEWAY
    SERVICE_UNAVAILABLE = status.HTTP_503_SERVICE_UNAVAILABLE
    GATEWAY_TIMEOUT = status.HTTP_504_GATEWAY_TIMEOUT
