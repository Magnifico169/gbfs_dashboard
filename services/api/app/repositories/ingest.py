"""Настройки систем и журнал запусков приёма."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import IngestStatus
from app.models import IngestRun, SystemSetting


class IngestRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enabled_systems(self) -> list[SystemSetting]:
        return list(self.session.scalars(select(SystemSetting).where(SystemSetting.enabled)))

    def log_run(
        self,
        system_id: str,
        feed: str,
        started_at: datetime,
        status: IngestStatus,
        http_status: int | None = None,
        rows_in: int = 0,
        rows_written: int = 0,
        rows_quarantined: int = 0,
        error: str | None = None,
    ) -> None:
        self.session.add(
            IngestRun(
                system_id=system_id,
                feed=feed,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                status=status.value,
                http_status=http_status,
                rows_in=rows_in,
                rows_written=rows_written,
                rows_quarantined=rows_quarantined,
                error=error,
            )
        )
