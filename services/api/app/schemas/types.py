"""Лояльный разбор значений GBFS: фиды отдают числа строками, флаги 0/1 вперемешку с
true/false, время то unix-секундами (v1/v2), то строкой RFC 3339 (v3), а локализуемые
строки — то строкой (v2), то списком {text, language} (v3). Некорректное значение
превращается в None; обязательность поля проверяет сама модель.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BeforeValidator

# Заглушки вроде 86400 («станция не отчитывалась») — всё, что раньше этой даты
EPOCH_SENTINEL = datetime(2000, 1, 1, tzinfo=UTC)


def localized_text(value: Any) -> str | None:
    """Английский вариант, иначе первый."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    texts = [
        (item.get("language"), item.get("text"))
        for item in value
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    for language, text in texts:
        if language == "en":
            return text
    return texts[0][1] if texts else None


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_str(value: Any) -> str | None:
    return None if value is None else str(value)


def to_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str) and value.lower() in {"true", "1", "false", "0"}:
        return value.lower() in {"true", "1"}
    return None


def to_utc(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    try:
        if isinstance(value, int | float):
            parsed = datetime.fromtimestamp(value, UTC)
        else:
            parsed = datetime.fromisoformat(str(value)).astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return None if parsed < EPOCH_SENTINEL else parsed


LenientInt = Annotated[int | None, BeforeValidator(to_int)]
LenientStr = Annotated[str | None, BeforeValidator(to_str)]
LenientFlag = Annotated[bool | None, BeforeValidator(to_flag)]
LenientDatetime = Annotated[datetime | None, BeforeValidator(to_utc)]
LenientFloat = Annotated[float | None, BeforeValidator(to_float)]
LocalizedText = Annotated[str | None, BeforeValidator(localized_text)]

RequiredStr = Annotated[str, BeforeValidator(to_str)]
RequiredFloat = Annotated[float, BeforeValidator(to_float)]
RequiredLocalizedText = Annotated[str, BeforeValidator(localized_text)]
