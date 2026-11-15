"""Состояние станций: текущее, факты, карантин, партиции."""

from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.models import StationCurrent, StationQuarantine, StationStatus
from app.schemas.gbfs import StationStatusRow


class StationStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_partitions(self, days_ahead: int) -> None:
        self.session.execute(select(func.core.ensure_station_status_partitions(days_ahead)))

    def touch_current(
        self, system_id: str, previous_check: datetime | None, checked_at: datetime
    ) -> None:
        if previous_check is None:
            return
        self.session.execute(
            update(StationCurrent)
            .where(
                StationCurrent.system_id == system_id,
                StationCurrent.checked_at == previous_check,
            )
            .values(checked_at=checked_at)
        )

    def quarantine(self, rows: list[tuple[StationStatusRow, str]], fetched_at: datetime) -> None:
        self.session.add_all(
            StationQuarantine(
                system_id=row.system_id,
                station_id=row.station_id or "",
                fetched_at=fetched_at,
                reason=reason,
                payload=row.model_dump(mode="json"),
            )
            for row, reason in rows
        )

    def write_status(
        self, rows: list[StationStatusRow], system_id: str, fetched_at: datetime
    ) -> int:
        """Пишет в факты только станции, чьё состояние изменилось (reported_at не сравниваем:
        фид может обновлять метку без изменения счётчиков). Возвращает число записанных строк.
        """
        if not rows:
            return 0
        state_columns = StationCurrent.state_columns()
        known = {
            row.station_id: tuple(row[1:])
            for row in self.session.execute(
                select(
                    StationCurrent.station_id,
                    *(getattr(StationCurrent, c) for c in state_columns),
                ).where(StationCurrent.system_id == system_id)
            )
        }
        facts, new_current, changed_current, touched_current = [], [], [], []
        for row in rows:
            data = row.model_dump()
            state = tuple(data[c] for c in state_columns)
            stamped = {**data, "fetched_at": fetched_at, "checked_at": fetched_at}
            if row.station_id not in known:
                facts.append({**data, "fetched_at": fetched_at})
                new_current.append(stamped)
            elif known[row.station_id] != state:
                facts.append({**data, "fetched_at": fetched_at})
                changed_current.append(stamped)
            else:
                touched_current.append(
                    {
                        "system_id": system_id,
                        "station_id": row.station_id,
                        "reported_at": row.reported_at,
                        "checked_at": fetched_at,
                    }
                )
        if facts:
            self.session.execute(insert(StationStatus), facts)
        if new_current:
            self.session.execute(insert(StationCurrent), new_current)
        if changed_current:
            self.session.execute(update(StationCurrent), changed_current)
        if touched_current:
            self.session.execute(update(StationCurrent), touched_current)
        return len(facts)
