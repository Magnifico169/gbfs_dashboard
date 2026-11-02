"""Базовый класс моделей и общие типы колонок."""

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase

TIMESTAMPTZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass
