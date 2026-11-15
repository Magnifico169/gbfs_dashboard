"""События из двух достоверных соседних наблюдений, без выдуманных аренд."""

from datetime import datetime, timedelta

from app.models import StationCurrent
from app.schemas.gbfs import StationStatusRow


def station_events(
    previous: StationCurrent, row: StationStatusRow, now: datetime, stale_after_s: int
) -> list[dict]:
    threshold = now - timedelta(seconds=stale_after_s)
    if (
        previous.reported_at is None
        or row.reported_at is None
        or min(previous.checked_at, previous.reported_at, row.reported_at) < threshold
    ):
        return []
    events = []
    delta = row.bikes_available - previous.bikes_available
    if delta:
        events.append(
            {
                "event_type": "bikes_added" if delta > 0 else "bikes_removed",
                "details": {"count": abs(delta), "lower_bound": True},
            }
        )
    for field, off, on in (
        ("is_installed", "uninstalled", "installed"),
        ("is_renting", "renting_stopped", "renting_resumed"),
        ("is_returning", "returning_stopped", "returning_resumed"),
    ):
        if getattr(previous, field) != getattr(row, field):
            events.append({"event_type": on if getattr(row, field) else off, "details": None})
    for field, empty, available in (
        ("bikes_available", "empty", "not_empty"),
        ("docks_available", "full", "not_full"),
    ):
        old, new = getattr(previous, field), getattr(row, field)
        if old is not None and new is not None and (old == 0) != (new == 0):
            events.append({"event_type": empty if new == 0 else available, "details": None})
    return [
        {**event, "system_id": row.system_id, "station_id": row.station_id, "occurred_at": now}
        for event in events
    ]
